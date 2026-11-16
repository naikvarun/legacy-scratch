# /// script
# requires-python = ">=3.10"
# dependencies = ["faker>=30", "psycopg[binary]>=3.2"]
# ///
"""Seed the synthetic legacy clinic database.

Every value is fake: names/addresses come from Faker with a fixed seed, SSNs use the
never-issued 9xx area range, phone numbers use the fictional 555-01xx block, and emails
use the reserved example.com domain.

Writes answer_key/ground_truth.json — the true meaning of every code column and every
planted data-quality trap. Keep that file away from your agents; it is for scoring them.

    uv run seed.py                       # defaults: 15k patients, seed 42
    uv run seed.py --patients 30000 --url postgresql://legacy:legacy@localhost:5433/legacy_db
"""
from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
from faker import Faker

HERE = Path(__file__).parent
DEFAULT_URL = "postgresql://legacy:legacy@localhost:5433/legacy_db"
AS_OF = datetime(2025, 6, 30, 23, 0)
START = datetime(2012, 1, 1)

DEPTS = [
    ("Emergency Medicine", "EMER"), ("Cardiology", "CARD"), ("Orthopedics", "ORTH"),
    ("Neurology", "NEUR"), ("Oncology", "ONCO"), ("Pediatrics", "PEDS"),
    ("Internal Medicine", "INTM"), ("General Surgery", "GSUR"),
    ("Obstetrics & Gynecology", "OBGY"), ("Psychiatry", "PSYC"), ("Radiology", "RADI"),
    ("Pulmonology", "PULM"), ("Nephrology", "NEPH"), ("Gastroenterology", "GAST"),
    ("Intensive Care Unit", "ICU"), ("Neonatal ICU", "NICU"), ("Rehabilitation", "REHB"),
    ("Dermatology", "DERM"), ("Urology", "UROL"), ("Old Wing - Closed", "OLDW"),
]
SPECIALTIES = ["CARD", "ORTH", "NEUR", "ONC", "PED", "EM", "IM", "GSURG", "OBGYN",
               "PSY", "RAD", "PULM", "NEPH", "GI"]
DX_CODES = ["I10", "E11.9", "J18.9", "N39.0", "I50.9", "K35.80", "S72.001A", "O80",
            "F32.9", "M17.11", "C34.90", "J44.1", "I21.4", "A41.9", "R07.9", "K80.20"]
PAYERS_INSURED = ["MCARE", "MCAID", "BCBS", "AETNA", "UHC", "CIGNA"]
CRT_USERS = ["admin", "jsmith_old", "MIGR_2014", "sys", "frontdesk2", None]

# Ground-truth semantics for the undocumented columns.
PAT_ST_CD = {
    "A": "Admitted - currently inpatient",
    "D": "Discharged",
    "I": "Observation stay - not formally admitted",
    "S": "Transferred out to another facility",
    "X": "Expired - patient deceased",
}
ADM_TYPE = {1: "Emergency", 2: "Elective", 3: "Urgent", 4: "Newborn", 9: "Unknown"}
INV_STATUS = {0: "Draft", 1: "Issued", 2: "Paid in full", 3: "Partially paid", 9: "Void"}
METHOD_CD = {"CC": "Credit card", "CA": "Cash", "CH": "Cheque",
             "IN": "Insurance remittance", "EF": "Electronic funds transfer"}


def rand_dt(r: random.Random, start: datetime, end: datetime) -> datetime:
    span = max(int((end - start).total_seconds()), 0)
    return start + timedelta(seconds=r.randint(0, span))


def fake_ssn(r: random.Random) -> str | None:
    x = r.random()
    if x < 0.04:
        return None
    if x < 0.06:
        return "000-00-0000"
    return f"9{r.randint(0, 99):02d}-{r.randint(1, 99):02d}-{r.randint(1, 9999):04d}"


def fake_phone(r: random.Random) -> str:
    area, line = r.randint(201, 989), r.randint(0, 99)
    return r.choice([
        f"({area}) 555-01{line:02d}", f"{area}-555-01{line:02d}",
        f"{area}55501{line:02d}", f"+1 {area} 555 01{line:02d}",
    ])


def fmt_dob(r: random.Random, d: date, traps: Counter) -> str:
    x = r.random()
    if x < 0.70:
        return d.strftime("%Y-%m-%d")
    if x < 0.90:
        traps["dob_us_format"] += 1
        return d.strftime("%m/%d/%Y")
    if x < 0.98:
        traps["dob_two_digit_year"] += 1
        return d.strftime("%d-%b-%y").upper()
    traps["dob_unparseable_or_sentinel"] += 1
    return r.choice(["", "00/00/0000", "UNKNOWN", "1900-01-01"])


def gen_depts(r: random.Random):
    rows = []
    for i, (name, code) in enumerate(DEPTS, start=1):
        flag = "N" if code == "OLDW" else r.choices(["Y", "y", "1"], [90, 6, 4])[0]
        rows.append((i, name, code, r.choice([1, 2, 3, 4, 5, 6, None]), flag))
    return rows


def gen_providers(r: random.Random, fake: Faker, n: int):
    rows = []
    for pid in range(1, n + 1):
        name = r.choice([
            f"Dr. {fake.first_name()} {fake.last_name()}",
            f"{fake.last_name().upper()}, {fake.first_name()}",
            f"{fake.first_name()} {fake.last_name()} MD",
        ])
        dept = None if r.random() < 0.05 else r.randint(1, len(DEPTS))
        rows.append((pid, name, r.choice(SPECIALTIES), dept,
                     f"{r.randint(10**9, 2 * 10**9 - 1)}",
                     r.choices([1, 0], [90, 10])[0],
                     fake.date_between(date(1995, 1, 1), date(2025, 1, 1))))
    return rows


def gen_patients(r: random.Random, fake: Faker, n: int, n_prov: int, traps: Counter, truth: dict):
    notes_pool = [
        "Pt prefers Spanish-speaking staff.", "Allergy: penicillin.", "Allergy: latex",
        "Wheelchair access required", "Prior records at another facility", "DNR on file",
        "Bad debt - refer to collections", "Interpreter needed (ASL)",
    ]
    rows, true_dob = [], {}
    n_unique = int(n * 0.99)
    for pid in range(1, n_unique + 1):
        sex = r.choices(["M", "F", "U"], [49, 49, 2])[0]
        fname = (fake.first_name_male() if sex == "M"
                 else fake.first_name_female() if sex == "F" else fake.first_name())
        lname = fake.last_name()
        dob = fake.date_between(date(1930, 1, 1), date(2025, 1, 31))
        true_dob[pid] = dob
        g = r.random()
        gndr = sex if g < 0.975 else (sex.lower() if g < 0.985 else (None if g < 0.995 else ""))
        st = fake.state_abbr(include_territories=False)
        try:
            zip_txt = fake.zipcode_in_state(st)
        except Exception:
            zip_txt = fake.zipcode()
        if zip_txt.startswith("0"):
            traps["zip_lost_leading_zero"] += 1
        note = None
        if r.random() < 0.30:
            if r.random() < 0.15:
                note = r.choice([
                    f"Call back at {fake_phone(r)} re: results",
                    f"NOK: {fake.first_name()} {fake.last_name()} {fake_phone(r)}",
                ])
                traps["pii_in_free_text_notes"] += 1
            else:
                note = r.choice(notes_pool)
        mrn = f"MRN{pid:07d}" if r.random() < 0.9 else f"M-{r.randint(10000, 99999)}"
        rows.append([
            pid, mrn, fname, lname, fmt_dob(r, dob, traps), gndr, fake_ssn(r), fake_phone(r),
            f"{fname.lower()}.{lname.lower()}{r.randint(1, 999)}@example.com",
            fake.street_address(), fake.city(), st, int(zip_txt),
            None if r.random() < 0.15 else r.randint(1, n_prov),
            None,  # fax_no: dead column, always NULL
            note,
            rand_dt(r, datetime(2005, 1, 1), datetime(2025, 6, 1)),
            r.choice(CRT_USERS),
        ])

    # ~1% duplicate registrations: same person, new pat_id/MRN, dob in a different format.
    for pid in range(n_unique + 1, n + 1):
        orig = r.choice(rows[:n_unique])
        dup = list(orig)
        dup[0] = pid
        dup[1] = f"MRN{pid:07d}"
        if r.random() < 0.5:
            dup[3] = dup[3].upper()
        dup[4] = fmt_dob(r, true_dob[orig[0]], traps)
        dup[7] = fake_phone(r)
        dup[16] = rand_dt(r, datetime(2005, 1, 1), datetime(2025, 6, 1))
        true_dob[pid] = true_dob[orig[0]]
        rows.append(dup)
        truth["duplicate_patients"].append({"original_pat_id": orig[0], "duplicate_pat_id": pid})
    return [tuple(x) for x in rows], true_dob


def gen_encounters(r: random.Random, true_dob: dict, n_prov: int, traps: Counter, truth: dict):
    dept_id = {code: i for i, (_, code) in enumerate(DEPTS, start=1)}
    general_depts = [i for i, (_, c) in enumerate(DEPTS, start=1) if c not in ("NICU", "OLDW")]
    rows, meta = [], {}
    rec_id = 0
    for pid, dob in true_dob.items():
        dob_dt = datetime.combine(dob, datetime.min.time())
        earliest = max(dob_dt + timedelta(days=1), START)
        latest = AS_OF - timedelta(days=25)
        if earliest >= latest:
            continue
        k = r.choices([1, 2, 3, 4, 5, 6], [40, 30, 15, 9, 4, 2])[0]
        admits = sorted(rand_dt(r, earliest, latest) for _ in range(k))
        if dob_dt >= START and r.random() < 0.5:
            admits[0] = dob_dt + timedelta(hours=r.randint(0, 12))
            admits.sort()

        for idx, admit in enumerate(admits):
            last = idx == len(admits) - 1
            code = (r.choices(list("DAISX"), [78, 8, 5, 6, 3])[0] if last
                    else r.choices(["D", "S"], [93, 7])[0])
            if code in ("A", "I"):
                recent = AS_OF - timedelta(days=r.uniform(0, 20))
                if idx == 0 or recent > admits[idx - 1] + timedelta(days=45):
                    admit = recent
                else:
                    code = "D"
            discharge = None
            if code not in ("A", "I"):
                stay = timedelta(days=r.choices([0, 1, 2, 3, 4, 5, 7, 10, 14, 21],
                                                [8, 20, 18, 14, 10, 9, 8, 6, 4, 3])[0],
                                 hours=r.randint(1, 20))
                discharge = min(admit + stay, AS_OF)

            age_days = (admit.date() - dob).days
            if age_days < 365 and idx == 0 and admit - dob_dt < timedelta(days=1):
                adm_type, dx = 4, "Z38.00"
                dept = r.choices([dept_id["PEDS"], dept_id["NICU"]], [80, 20])[0]
            else:
                adm_type = r.choices([1, 2, 3, 9], [45, 30, 18, 5])[0]
                dx = r.choice(DX_CODES)
                dept = dept_id["OLDW"] if admit.year < 2016 and r.random() < 0.08 else r.choice(general_depts)
            if r.random() < 0.03:
                dept = None

            stored = code
            if code == "D":
                x = r.random()
                if x < 0.03:
                    stored = "Discharged"
                elif x < 0.04:
                    stored = r.choice(["d", " D", "D "])
                    traps["status_case_or_whitespace"] += 1
            elif code == "X" and r.random() < 0.25:
                stored = "Discharged"  # the earlier failed migration mapped X -> Discharged

            los = None
            if discharge:
                los = (discharge.date() - admit.date()).days
                if r.random() < 0.02:
                    los += 1
                    traps["los_inconsistent_with_dates"] += 1

            rec_id += 1
            if stored == "Discharged":
                truth["corrupted_status_rows"].append(
                    {"rec_id": rec_id, "stored": stored, "true_code": code})
            rows.append((rec_id, pid, admit, discharge, stored, adm_type,
                         None if r.random() < 0.07 else r.randint(1, n_prov),
                         dept, dx, los,
                         (discharge or admit) + timedelta(hours=r.randint(1, 72))))
            meta[rec_id] = (pid, code, admit, discharge)
    return rows, meta


def gen_billing(r: random.Random, meta: dict, n_pat: int, traps: Counter, truth: dict):
    invoices, payments = [], []
    inv_no = txn_id = 0

    def add_invoice(rec, pid, inv_dt, true_code):
        nonlocal inv_no, txn_id
        inv_no += 1
        if true_code in ("A", "I"):
            status = 0
        else:
            status = r.choices([0, 1, 2, 3, 9], [5, 12, 63, 15, 5])[0]
        tot = sum(round(r.uniform(15, 900), 2) for _ in range(r.randint(1, 12)))
        insured = r.random() < 0.82
        payer = r.choice(PAYERS_INSURED) if insured else "SELF"
        flag = (r.choices(["Y", "1", "T", "yes"], [70, 15, 10, 5])[0] if insured
                else r.choices(["N", "0", "F", "no"], [70, 15, 10, 5])[0])
        if r.random() < 0.01:
            flag = None
        inv_pid = pid
        if rec is not None and r.random() < 0.002:
            inv_pid = r.randint(1, n_pat)
            truth["invoice_patient_mismatch"].append(inv_no)
        invoices.append((inv_no, rec, inv_pid, inv_dt, tot, status, flag, payer,
                         r.choice([30, 45, 60, 90])))

        if status in (2, 3):
            target = tot if status == 2 else tot * r.uniform(0.2, 0.9)
            parts = r.randint(1, 3) if status == 2 else r.randint(1, 2)
            weights = [r.random() for _ in range(parts)]
            amounts = [round(target * w / sum(weights), 2) for w in weights[:-1]]
            amounts.append(target - sum(amounts))
            for amt in amounts:
                txn_id += 1
                method = (r.choices(["IN", "EF", "CC"], [60, 25, 15])[0] if insured
                          else r.choices(["CC", "CA", "CH", "EF"], [50, 20, 15, 15])[0])
                paid_at = datetime.combine(inv_dt, datetime.min.time()) + timedelta(
                    days=r.randint(1, 90), seconds=r.randint(0, 86399))
                stored_method = method.lower() if r.random() < 0.01 else method
                payments.append([txn_id, inv_no, amt,
                                 int(paid_at.replace(tzinfo=timezone.utc).timestamp()),
                                 stored_method, f"{method}-{r.randint(10**7, 10**8 - 1)}"])

    for rec, (pid, code, admit, discharge) in meta.items():
        n_inv = r.choices([1, 2, 0], [93, 5, 2])[0]
        for _ in range(n_inv):
            inv_dt = min((discharge or admit).date() + timedelta(days=r.randint(0, 5)), AS_OF.date())
            add_invoice(rec, pid, inv_dt, code)

    # Walk-in pharmacy invoices with no encounter (rec_id NULL).
    for _ in range(int(len(meta) * 0.01)):
        add_invoice(None, r.randint(1, n_pat), rand_dt(r, START, AS_OF).date(), "D")
        traps["invoice_without_encounter"] += 1

    # Payments pointing at purged invoices, or at nothing.
    for p in payments:
        x = r.random()
        if x < 0.005:
            p[1] = inv_no + r.randint(1, 5000)
            truth["orphan_payment_txn_ids"].append(p[0])
        elif x < 0.007:
            p[1] = None
            truth["orphan_payment_txn_ids"].append(p[0])
    return invoices, [tuple(p) for p in payments]


TABLES = [
    ("dept", ["dept_id", "dept_nm", "dept_cd", "flr", "active_flg"]),
    ('"ProviderMaster"', ['"ProvID"', '"ProvName"', '"SpecialtyCd"', '"DeptRef"',
                          '"NPI_num"', '"IsActive"', '"HireDt"']),
    ("pt_demog", ["pat_id", "mrn", "str_fname", "str_lname", "dob_txt", "gndr", "ssn_no",
                  "ph_num", "email_addr", "addr_line1", "city", "st", "zip_cd", "pcp_id",
                  "fax_no", "notes", "crt_dt", "crt_usr"]),
    ("patient_records", ["rec_id", "pat_id", "admit_dt", "discharge_dt", "pat_st_cd",
                         "adm_type", "attending_prov", "dept_id", "dx_cd", "los", "upd_ts"]),
    ("inv_hdr", ["inv_no", "rec_id", "pat_id", "inv_dt", "tot_amt", "status", "ins_flag",
                 "payer_cd", "due_days"]),
    ("pmt_txn", ["txn_id", "inv_no", "pmt_amt", "pmt_epoch", "method_cd", "ref_no"]),
]
SERIALS = [("dept", "dept_id"), ('"ProviderMaster"', "ProvID"), ("pt_demog", "pat_id"),
           ("patient_records", "rec_id"), ("inv_hdr", "inv_no"), ("pmt_txn", "txn_id")]


    def load(url: str, data: dict[str, list[tuple]]) -> dict[str, int]:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute((HERE / "schema.sql").read_text())
        cur.execute('TRUNCATE pmt_txn, inv_hdr, patient_records, pt_demog, '
                    '"ProviderMaster", dept RESTART IDENTITY CASCADE')
        for table, cols in TABLES:
            with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
                for row in data[table]:
                    copy.write_row(row)
        for table, col in SERIALS:
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                        f'(SELECT COALESCE(MAX("{col}"), 1) FROM {table}))')
        conn.commit()
        cur.execute("ANALYZE")
        counts = {}
        for table, _ in TABLES:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table.strip('"')] = cur.fetchone()[0]
        cur.execute("SELECT pat_st_cd, COUNT(*) FROM patient_records GROUP BY 1 ORDER BY 2 DESC")
        counts["_pat_st_cd_distribution"] = {repr(k): v for k, v in cur.fetchall()}
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.getenv("SOURCE_DB_URL", DEFAULT_URL))
    ap.add_argument("--patients", type=int, default=15000)
    ap.add_argument("--providers", type=int, default=400)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--truth-out", default=str(HERE / "answer_key" / "ground_truth.json"))
    args = ap.parse_args()

    r = random.Random(args.seed)
    fake = Faker("en_US")
    Faker.seed(args.seed)
    traps: Counter = Counter()
    truth = {"duplicate_patients": [], "corrupted_status_rows": [],
             "invoice_patient_mismatch": [], "orphan_payment_txn_ids": []}

    depts = gen_depts(r)
    providers = gen_providers(r, fake, args.providers)
    patients, true_dob = gen_patients(r, fake, args.patients, args.providers, traps, truth)
    encounters, meta = gen_encounters(r, true_dob, args.providers, traps, truth)
    invoices, payments = gen_billing(r, meta, args.patients, traps, truth)

    print(f"Generated {len(patients)} patients, {len(encounters)} encounters, "
          f"{len(invoices)} invoices, {len(payments)} payments. Loading…")
    counts = load(args.url, {
        "dept": depts, '"ProviderMaster"': providers, "pt_demog": patients,
        "patient_records": encounters, "inv_hdr": invoices, "pmt_txn": payments,
    })

    true_codes = Counter(code for _, code, _, _ in meta.values())
    answer_key = {
        "_warning": "ANSWER KEY. Do not feed to agents or the profiler; use it to score them.",
        "generated": {"seed": args.seed, "as_of": AS_OF.isoformat(), "patients": args.patients},
        "row_counts": {k: v for k, v in counts.items() if not k.startswith("_")},
        "code_columns": {
            "patient_records.pat_st_cd": PAT_ST_CD,
            "patient_records.adm_type": ADM_TYPE,
            "inv_hdr.status": INV_STATUS,
            "inv_hdr.ins_flag": "Boolean. Y/1/T/yes = insured; N/0/F/no = self-pay; NULL = unknown",
            "pmt_txn.method_cd": METHOD_CD,
            "dept.active_flg": "Boolean. Y/y/1 = active; N = inactive",
            "ProviderMaster.IsActive": "Boolean. 1 = active; 0 = inactive",
        },
        "column_semantics": {
            "pt_demog.dob_txt": "Date of birth as text, mixed formats; DD-MON-YY has century ambiguity",
            "pt_demog.zip_cd": "US ZIP code stored as INTEGER; leading zeros lost (must become CHAR(5))",
            "pt_demog.fax_no": "Dead column, 100% NULL",
            "pt_demog.pcp_id": "Primary care provider -> ProviderMaster.ProvID",
            "patient_records.los": "Length of stay in days, derived; ~2% disagree with dates",
            "inv_hdr.tot_amt": "Invoice total in USD, stored as FLOAT (needs NUMBER(12,2))",
            "inv_hdr.pat_id": "Denormalised copy of patient_records.pat_id; a few disagree",
            "pmt_txn.pmt_epoch": "Payment timestamp as Unix epoch seconds (UTC)",
            "pmt_txn.inv_no": "Undeclared FK to inv_hdr.inv_no; contains orphans",
        },
        "true_pat_st_cd_distribution": dict(true_codes),
        "stored_pat_st_cd_distribution": counts["_pat_st_cd_distribution"],
        "pii_columns": ["pt_demog.str_fname", "pt_demog.str_lname", "pt_demog.dob_txt",
                        "pt_demog.ssn_no", "pt_demog.ph_num", "pt_demog.email_addr",
                        "pt_demog.addr_line1", "pt_demog.city", "pt_demog.zip_cd",
                        "pt_demog.mrn", "pt_demog.notes", "patient_records.dx_cd",
                        '"ProviderMaster"."ProvName"', '"ProviderMaster"."NPI_num"'],
        "trap_counts": dict(traps) | {
            "status_literal_Discharged": len(truth["corrupted_status_rows"]),
            "status_Discharged_actually_expired": sum(
                1 for x in truth["corrupted_status_rows"] if x["true_code"] == "X"),
            "duplicate_patients": len(truth["duplicate_patients"]),
            "invoice_patient_mismatch": len(truth["invoice_patient_mismatch"]),
            "orphan_payments": len(truth["orphan_payment_txn_ids"]),
            "ssn_placeholder_or_null": "~6% of pt_demog",
            "float_money_artifacts": "inv_hdr.tot_amt, pmt_txn.pmt_amt",
        },
        **truth,
    }
    out = Path(args.truth_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(answer_key, indent=2, default=str))

    print(json.dumps(counts, indent=2))
    print(f"Answer key written to {out}")


if __name__ == "__main__":
    main()

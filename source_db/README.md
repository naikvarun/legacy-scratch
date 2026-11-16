# Legacy clinic source database (synthetic)

A deliberately messy PostgreSQL 16 database that stands in for the legacy OLTP system.
**All data is synthetic:** Faker with a fixed seed, SSNs in the never-issued 9xx range,
fictional 555-01xx phone numbers and `example.com` emails.

## Run

```bash
docker compose up -d --wait        # Postgres on localhost:5433, schema auto-applied
uv run seed.py                     # ~15 s; idempotent (truncates and reloads)
```

`SOURCE_DB_URL=postgresql://legacy:legacy@localhost:5433/legacy_db`

Options: `--patients N`, `--seed N`, `--url ...`. The default seed 42 makes every run identical.

## Tables (default size)

| Table | Rows | Notes |
|---|---|---|
| `dept` | 20 | `active_flg` Y/y/1/N |
| `"ProviderMaster"` | 400 | Quoted CamelCase identifiers, `IsActive` 1/0, nullable `DeptRef` |
| `pt_demog` | 15,000 | Patient master with PII and about 1% duplicate registrations |
| `patient_records` | ~31,800 | Encounters with the undocumented `pat_st_cd` and `adm_type` |
| `inv_hdr` | ~33,000 | `status INT`, FLOAT money, mixed boolean `ins_flag` |
| `pmt_txn` | ~46,000 | `pmt_epoch` timestamps; `inv_no` has **no declared FK**, plus orphans |

## Planted traps (your profiler and agents should find these)

- `pat_st_cd`: the codes A/D/I/S/X are undocumented. It also holds the literal `'Discharged'`
  left by the earlier failed migration, and about 11% of those rows are really `X` (deceased).
  A few values use the wrong case or have stray whitespace (`'d'`, `' D'`).
- `dob_txt`: dates stored as VARCHAR in mixed formats, `DD-MON-YY` with an ambiguous century,
  and sentinels (`1900-01-01`, `00/00/0000`, `UNKNOWN`).
- `zip_cd` stored as INTEGER, so ZIP codes lose their leading zeros.
- FLOAT money with binary artifacts (`5723.8099999999995`).
- Booleans written as Y/N, 1/0, T/F and yes/no.
- The derived `los` column disagrees with the dates in about 2% of rows.
- `inv_hdr.pat_id` sometimes disagrees with the encounter's patient.
- Orphan and NULL `pmt_txn.inv_no` values.
- `fax_no` is 100% NULL; SSNs include `000-00-0000` placeholders.
- PII hidden in the free-text `notes` column.

## Answer key

`answer_key/ground_truth.json` holds:

- the true meaning of every code column
- the counts for each trap
- the list of corrupted `rec_id`s with their true codes
- the duplicate patient pairs
- the orphan payment IDs

**Keep it out of the profiler's and agents' inputs.** Use it only to score the AI mapper
(for example, did the gate flag `pat_st_cd`, and did the reviewer catch `X` = Expired?).

Reset everything: `docker compose down -v`.

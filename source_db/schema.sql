-- Legacy clinic OLTP schema (SYNTHETIC — assessment use only).
-- Deliberately messy: no column comments, mixed naming conventions, weak typing,
-- and one relationship with no declared FK. Do not tidy this file: the mess is the test.

CREATE TABLE IF NOT EXISTS dept (
    dept_id     SERIAL PRIMARY KEY,
    dept_nm     VARCHAR(60) NOT NULL,
    dept_cd     CHAR(4),
    flr         SMALLINT,
    active_flg  CHAR(1)
);

CREATE TABLE IF NOT EXISTS "ProviderMaster" (
    "ProvID"        SERIAL PRIMARY KEY,
    "ProvName"      VARCHAR(120),
    "SpecialtyCd"   VARCHAR(6),
    "DeptRef"       INTEGER REFERENCES dept (dept_id),
    "NPI_num"       VARCHAR(10),
    "IsActive"      SMALLINT,
    "HireDt"        DATE
);

CREATE TABLE IF NOT EXISTS pt_demog (
    pat_id      SERIAL PRIMARY KEY,
    mrn         VARCHAR(12),
    str_fname   VARCHAR(60),
    str_lname   VARCHAR(60),
    dob_txt     VARCHAR(20),
    gndr        CHAR(1),
    ssn_no      VARCHAR(11),
    ph_num      VARCHAR(20),
    email_addr  VARCHAR(120),
    addr_line1  VARCHAR(120),
    city        VARCHAR(60),
    st          CHAR(2),
    zip_cd      INTEGER,
    pcp_id      INTEGER REFERENCES "ProviderMaster" ("ProvID"),
    fax_no      VARCHAR(20),
    notes       TEXT,
    crt_dt      TIMESTAMP,
    crt_usr     VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS patient_records (
    rec_id          SERIAL PRIMARY KEY,
    pat_id          INTEGER NOT NULL REFERENCES pt_demog (pat_id),
    admit_dt        TIMESTAMP NOT NULL,
    discharge_dt    TIMESTAMP,
    pat_st_cd       VARCHAR(12),
    adm_type        INTEGER,
    attending_prov  INTEGER REFERENCES "ProviderMaster" ("ProvID"),
    dept_id         INTEGER REFERENCES dept (dept_id),
    dx_cd           VARCHAR(8),
    los             INTEGER,
    upd_ts          TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inv_hdr (
    inv_no      SERIAL PRIMARY KEY,
    rec_id      INTEGER REFERENCES patient_records (rec_id),
    pat_id      INTEGER REFERENCES pt_demog (pat_id),
    inv_dt      DATE,
    tot_amt     DOUBLE PRECISION,
    status      INTEGER,
    ins_flag    VARCHAR(3),
    payer_cd    VARCHAR(10),
    due_days    SMALLINT
);

-- inv_no intentionally has no FK constraint (implicit relationship in the legacy app).
CREATE TABLE IF NOT EXISTS pmt_txn (
    txn_id      BIGSERIAL PRIMARY KEY,
    inv_no      INTEGER,
    pmt_amt     DOUBLE PRECISION,
    pmt_epoch   BIGINT,
    method_cd   CHAR(2),
    ref_no      VARCHAR(30)
);

CREATE INDEX IF NOT EXISTS ix_pr_pat   ON patient_records (pat_id);
CREATE INDEX IF NOT EXISTS ix_inv_rec  ON inv_hdr (rec_id);
CREATE INDEX IF NOT EXISTS ix_pmt_inv  ON pmt_txn (inv_no);

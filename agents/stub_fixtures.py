"""Canned data for LLM_MODE=stub. Shapes match what the real nodes will produce.

The values mirror source_db/ (the seeded legacy clinic DB) so the stub run already shows
the interesting cases: PII columns, an undocumented status code with a corrupted literal,
and a lossy text-to-date cast.
"""

PROFILE = {
    "source": "postgresql://…/legacy_db (stub profile)",
    "tables": {
        "pt_demog": {
            "row_count": 15000,
            "primary_key": ["pat_id"],
            "foreign_keys": [],
            "columns": {
                "pat_id": {"type": "INTEGER", "null_rate": 0.0, "distinct": 15000, "samples": [1, 2, 3]},
                "str_fname": {"type": "VARCHAR(60)", "null_rate": 0.0, "distinct": 2911,
                              "samples": ["Maria", "James", "Aaliyah"], "pii": True},
                "dob_txt": {"type": "VARCHAR(20)", "null_rate": 0.0, "distinct": 13120,
                            "samples": ["1988-03-14", "05/18/1941", "24-FEB-72", "00/00/0000"],
                            "pii": True, "parse_rate": {"date": 0.98}},
                "zip_cd": {"type": "INTEGER", "null_rate": 0.0, "distinct": 9800,
                           "samples": [88369, 2134, 58716], "pii": True},
                "ssn_no": {"type": "VARCHAR(11)", "null_rate": 0.04, "distinct": 14100,
                           "samples": ["937-78-4326", "000-00-0000"], "pii": True},
            },
        },
        "patient_records": {
            "row_count": 31802,
            "primary_key": ["rec_id"],
            "foreign_keys": [{"column": "pat_id", "references": "pt_demog.pat_id"}],
            "columns": {
                "rec_id": {"type": "INTEGER", "null_rate": 0.0, "distinct": 31802, "samples": [1, 2, 3]},
                "pat_id": {"type": "INTEGER", "null_rate": 0.0, "distinct": 14702, "samples": [17, 17, 902]},
                "admit_dt": {"type": "TIMESTAMP", "null_rate": 0.0, "distinct": 31790,
                             "samples": ["2019-04-02 13:11:09"]},
                "discharge_dt": {"type": "TIMESTAMP", "null_rate": 0.06, "distinct": 29880,
                                 "samples": ["2019-04-05 09:00:00", None]},
                "pat_st_cd": {"type": "VARCHAR(12)", "null_rate": 0.0, "distinct": 9,
                              "samples": ["D", "S", "A", "Discharged", "I", "X", " D", "d"],
                              "top_values": {"D": 26118, "S": 2168, "A": 1169, "Discharged": 955,
                                             "I": 752, "X": 343, " D": 105, "D ": 100, "d": 92},
                              "mixed_domain": True},
                "adm_type": {"type": "INTEGER", "null_rate": 0.0, "distinct": 5,
                             "samples": [1, 2, 3, 9, 4],
                             "top_values": {"1": 14127, "2": 9470, "3": 5671, "9": 1584, "4": 950}},
            },
        },
    },
}

# ai_mapper responses, keyed by source table.
MAPPER = {
    "pt_demog": [
        {"source_column": "pat_id", "target_column": "patient_id", "target_type": "NUMBER(38,0)",
         "inferred_meaning": "Surrogate patient key", "reasoning": "PK, dense integers",
         "confidence": 0.98, "unexplained_values": []},
        {"source_column": "str_fname", "target_column": "first_name", "target_type": "VARCHAR(60)",
         "inferred_meaning": "Patient given name", "reasoning": "Hungarian 'str' prefix; personal names",
         "confidence": 0.95, "unexplained_values": []},
        {"source_column": "dob_txt", "target_column": "date_of_birth", "target_type": "DATE",
         "inferred_meaning": "Date of birth stored as text", "reasoning": "Three date formats plus sentinels",
         "confidence": 0.82, "unexplained_values": ["00/00/0000"]},
        {"source_column": "zip_cd", "target_column": "zip_code", "target_type": "CHAR(5)",
         "inferred_meaning": "US ZIP code", "reasoning": "5-digit integers; some 4-digit (lost leading zero)",
         "confidence": 0.88, "unexplained_values": []},
        {"source_column": "ssn_no", "target_column": "ssn_sha256", "target_type": "CHAR(64)",
         "inferred_meaning": "Social Security Number", "reasoning": "NNN-NN-NNNN pattern; hash, never copy raw",
         "confidence": 0.90, "unexplained_values": ["000-00-0000"]},
    ],
    "patient_records": [
        {"source_column": "rec_id", "target_column": "encounter_id", "target_type": "NUMBER(38,0)",
         "inferred_meaning": "Encounter key", "reasoning": "PK", "confidence": 0.97, "unexplained_values": []},
        {"source_column": "pat_id", "target_column": "patient_id", "target_type": "NUMBER(38,0)",
         "inferred_meaning": "FK to patient", "reasoning": "Declared FK to pt_demog.pat_id",
         "confidence": 0.97, "unexplained_values": []},
        {"source_column": "admit_dt", "target_column": "admitted_at", "target_type": "TIMESTAMP_NTZ",
         "inferred_meaning": "Admission timestamp", "reasoning": "Never null, precedes discharge_dt",
         "confidence": 0.95, "unexplained_values": []},
        {"source_column": "discharge_dt", "target_column": "discharged_at", "target_type": "TIMESTAMP_NTZ",
         "inferred_meaning": "Discharge timestamp", "reasoning": "Null for open stays",
         "confidence": 0.93, "unexplained_values": []},
        {"source_column": "pat_st_cd", "target_column": "encounter_status", "target_type": "VARCHAR(20)",
         "inferred_meaning": "Encounter status code",
         "reasoning": "A/D likely Admitted/Discharged; I and S plausible; X and the literal 'Discharged' unclear",
         "confidence": 0.78, "unexplained_values": ["X", "Discharged"]},
        {"source_column": "adm_type", "target_column": "admission_type", "target_type": "VARCHAR(20)",
         "inferred_meaning": "Admission type code",
         "reasoning": "Integer codes 1-4 plus 9; 9 is probably 'unknown'",
         "confidence": 0.74, "unexplained_values": ["9"]},
    ],
}

# rule_generator responses, keyed by mapping_id.
RULES = {
    "pt_demog.pat_id": {"logic": "src", "null_handling": "Never null (PK).", "edge_cases": [], "confidence": 0.99},
    "pt_demog.str_fname": {"logic": "INITCAP(TRIM(src))", "null_handling": "Keep NULL.",
                           "edge_cases": ["Trim whitespace"], "confidence": 0.93},
    "pt_demog.dob_txt": {
        "logic": "COALESCE(TRY_TO_DATE(src, 'YYYY-MM-DD'), TRY_TO_DATE(src, 'MM/DD/YYYY'), "
                 "TRY_TO_DATE(src, 'DD-MON-YY'))",
        "null_handling": "Unparseable or sentinel values become NULL and are counted in reconciliation.",
        "edge_cases": ["'00/00/0000', 'UNKNOWN', '' -> NULL",
                       "DD-MON-YY century is ambiguous (e.g. 29-SEP-30)"],
        "confidence": 0.72},
    "pt_demog.zip_cd": {"logic": "LPAD(TO_VARCHAR(src), 5, '0')", "null_handling": "Keep NULL.",
                        "edge_cases": ["Restore leading zeros lost by INTEGER storage"], "confidence": 0.90},
    "pt_demog.ssn_no": {"logic": "SHA2(NULLIF(TRIM(src), '000-00-0000'), 256)",
                        "null_handling": "NULL and placeholder 000-00-0000 become NULL.",
                        "edge_cases": ["Raw SSN never lands in the target"], "confidence": 0.86},
    "patient_records.rec_id": {"logic": "src", "null_handling": "Never null (PK).", "edge_cases": [],
                               "confidence": 0.99},
    "patient_records.pat_id": {"logic": "src", "null_handling": "Never null (FK).", "edge_cases": [],
                               "confidence": 0.98},
    "patient_records.admit_dt": {"logic": "src", "null_handling": "Never null.", "edge_cases": [],
                                 "confidence": 0.97},
    "patient_records.discharge_dt": {"logic": "src", "null_handling": "NULL means the stay is still open.",
                                     "edge_cases": [], "confidence": 0.96},
    "patient_records.pat_st_cd": {
        "logic": "CASE UPPER(TRIM(src)) WHEN 'A' THEN 'ADMITTED' WHEN 'D' THEN 'DISCHARGED' "
                 "WHEN 'DISCHARGED' THEN 'DISCHARGED' WHEN 'I' THEN 'OBSERVATION' "
                 "WHEN 'S' THEN 'TRANSFERRED' ELSE NULL END",
        "null_handling": "Unknown codes map to NULL and are flagged in reconciliation.",
        "edge_cases": ["Trim and upper-case before mapping", "X has no inferable meaning -> NULL",
                       "Literal 'Discharged' (prior migration) treated as D"],
        "confidence": 0.66},
    "patient_records.adm_type": {
        "logic": "CASE src WHEN 1 THEN 'EMERGENCY' WHEN 2 THEN 'ELECTIVE' WHEN 3 THEN 'URGENT' "
                 "WHEN 4 THEN 'NEWBORN' WHEN 9 THEN 'UNKNOWN' ELSE NULL END",
        "null_handling": "Unknown codes map to NULL.",
        "edge_cases": ["9 assumed to mean unknown"], "confidence": 0.77},
}

DOC_OVERVIEW = {
    "overview": "Stub overview. In LLM mode this paragraph summarises the table's grain, "
                "contents and the review decisions a consumer should know."
}

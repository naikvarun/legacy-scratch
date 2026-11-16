"""Great Expectations checkpoints, dbt tests and the reconciliation report.

STUB: the three GE checkpoints and dbt return canned passing results; reconciliation is
computed from the profile and executor results. Real suites live in
validation/great_expectations/ and validation/dbt_models/ (next milestone).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GE_CHECKPOINTS = ("source_baseline", "post_extraction", "post_load")


def run_ge_checkpoint(name: str, tables: list[str]) -> dict[str, Any]:
    assert name in GE_CHECKPOINTS, name
    return {"checkpoint": name, "success": True, "stub": True,
            "suites": {t: {"success": True, "expectations": 0} for t in tables}}


def run_dbt_tests() -> dict[str, Any]:
    return {"success": True, "stub": True, "tests": []}


def reconcile(profile: dict[str, Any], exec_results: dict[str, Any], out_dir: Path,
              force_failure: bool = False) -> dict[str, Any]:
    tables = {}
    for table, res in exec_results["tables"].items():
        if "skipped" in res:
            continue
        source_rows = profile["tables"][table]["row_count"]
        target_rows = res["rows_loaded"] - (1 if force_failure else 0)
        tables[table] = {"target_table": res["target_table"], "source_rows": source_rows,
                         "target_rows": target_rows, "row_count_match": source_rows == target_rows}
    report = {"success": all(t["row_count_match"] for t in tables.values()), "tables": tables,
              "stub": True}
    path = out_dir / "reconciliation_report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["path"] = str(path)
    return report

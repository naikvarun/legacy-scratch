"""migration_executor — ELT into the target warehouse using only approved rules.

STUB: the SQL is generated for real and written to runs/<id>/sql/, but nothing is sent to
a warehouse; "loaded" row counts come from the profile. The approval guard, FK-ordered
load sequence and retry-with-backoff are real.
"""
from __future__ import annotations

import random
import re
import time
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class UnapprovedRuleError(RuntimeError):
    """A rule below the threshold reached the executor without a human approval."""


class TransientError(RuntimeError):
    """Retryable failure (network blip, warehouse busy, ...)."""


def assert_rules_executable(rules: list[dict[str, Any]], threshold: float) -> None:
    bad = [r["rule_id"] for r in rules
           if r["confidence"] < threshold and not (r["human_reviewed"] and r["override_note"])]
    if bad:
        raise UnapprovedRuleError(f"Unapproved low-confidence rules block execution: {', '.join(bad)}")


def load_order(fk_graph: dict[str, list[str]]) -> list[str]:
    """Parents before children, so FK targets exist before the rows that reference them."""
    tables = set(fk_graph)
    return list(TopologicalSorter({t: [p for p in deps if p in tables]
                                   for t, deps in fk_graph.items()}).static_order())


def build_insert_sql(source_table: str, rules: list[dict[str, Any]], *, raw_schema: str,
                     stage_schema: str) -> str:
    target_table = rules[0]["target_table"]
    cols, exprs = [], []
    for r in rules:
        expr = re.sub(r"\bsrc\b", f'"{r["source_column"]}"', r["logic"])
        cols.append(r["target_column"])
        exprs.append(f"    {expr} AS {r['target_column']}")
    return (f"-- generated from {len(rules)} approved rules\n"
            f"INSERT INTO {stage_schema}.{target_table} ({', '.join(cols)})\n"
            f"SELECT\n" + ",\n".join(exprs) + f"\nFROM {raw_schema}.{source_table};\n")


def with_retry(fn: Callable[[], T], *, attempts: int = 4, base_delay: float = 0.5,
               on_retry: Callable[[int, Exception], None] | None = None) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except TransientError as exc:
            if attempt == attempts:
                raise
            if on_retry:
                on_retry(attempt, exc)
            time.sleep(base_delay * 2 ** (attempt - 1) * (1 + random.random() * 0.1))
    raise AssertionError("unreachable")


def execute(run_id: str, profile: dict[str, Any], rules: list[dict[str, Any]], threshold: float,
            out_dir: Path) -> dict[str, Any]:
    assert_rules_executable(rules, threshold)
    raw_schema, stage_schema = f"RAW_{run_id}".replace("-", "_").upper(), f"STG_{run_id}".replace("-", "_").upper()
    sql_dir = out_dir / "sql"
    sql_dir.mkdir(exist_ok=True)
    results: dict[str, Any] = {"raw_schema": raw_schema, "stage_schema": stage_schema, "tables": {}}
    for table in load_order(profile["fk_graph"]):
        table_rules = [r for r in rules if r["source_table"] == table]
        if not table_rules:
            results["tables"][table] = {"skipped": "no approved rules"}
            continue
        sql = build_insert_sql(table, table_rules, raw_schema=raw_schema, stage_schema=stage_schema)
        (sql_dir / f"{table}.sql").write_text(sql, encoding="utf-8")
        rows = with_retry(lambda: profile["tables"][table]["row_count"])  # stub "load"
        results["tables"][table] = {
            "target_table": table_rules[0]["target_table"], "rules_applied": len(table_rules),
            "rows_extracted": rows, "rows_loaded": rows, "sql_file": str(sql_dir / f"{table}.sql"),
        }
    results["load_order"] = load_order(profile["fk_graph"])
    results["stub"] = True
    return results

"""doc_generator — writes the target data dictionary from approved mappings and rules.

Lineage, logic and override notes are rendered deterministically (they must be exact); the
LLM only writes the per-table overview prose.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.llm import call_agent
from agents.stub_fixtures import DOC_OVERVIEW


def generate_dictionary(run_id: str, mappings: list[dict[str, Any]], rules: list[dict[str, Any]],
                        out_dir: Path, config: RunnableConfig) -> dict[str, str]:
    by_mapping = {m["mapping_id"]: m for m in mappings}
    tables: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        m = by_mapping[r["mapping_id"]]
        tables.setdefault(r["target_table"], []).append({
            "target_column": r["target_column"], "target_type": m["target_type"],
            "definition": m["inferred_meaning"],
            "lineage": f"{r['source_table']}.{r['source_column']} -> {r['target_table']}.{r['target_column']}",
            "logic": r["logic"], "null_handling": r["null_handling"], "edge_cases": r["edge_cases"],
            "mapping_review": m["review"], "rule_confidence": r["confidence"],
            "human_reviewed": r["human_reviewed"], "override_note": r["override_note"],
            "prompt_ids": {"mapping": m["prompt_id"], "rule": r["prompt_id"]},
        })

    lines = [f"# Target data dictionary — run `{run_id}`", ""]
    doc: dict[str, Any] = {"run_id": run_id, "tables": {}}
    for table, cols in tables.items():
        out, prompt_id, _ = call_agent(
            run_id=run_id, agent="doc_generator", prompt="doc_generator", version="v1",
            variables={"target_table": table, "lineage": json.dumps(cols, indent=2, default=str)},
            stub_response=DOC_OVERVIEW, config=config,
        )
        doc["tables"][table] = {"overview": out["overview"], "overview_prompt_id": prompt_id, "columns": cols}
        lines += [f"## {table}", "", out["overview"], "",
                  "| Column | Type | Definition | Lineage | Logic | Review |",
                  "|---|---|---|---|---|---|"]
        for c in cols:
            review = c["mapping_review"]["decision"] if c["mapping_review"] else "-"
            if c["human_reviewed"]:
                review += f"; rule reviewed: {c['override_note']}"
            lines.append(f"| {c['target_column']} | {c['target_type']} | {c['definition']} | "
                         f"`{c['lineage']}` | `{c['logic']}` | {review} |")
        lines.append("")

    md, js = out_dir / "data_dictionary.md", out_dir / "data_dictionary.json"
    md.write_text("\n".join(lines), encoding="utf-8")
    js.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    return {"data_dictionary_md": str(md), "data_dictionary_json": str(js)}

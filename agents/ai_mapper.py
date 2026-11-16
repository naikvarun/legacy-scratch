"""ai_mapper — LangChain agent that proposes a target mapping for every source column.

The LLM's self-reported confidence is not trusted on its own: ``calibrate`` caps it with
deterministic signals from the profile, so PII, mixed-domain columns, unexplained codes and
lossy casts are always routed to human review regardless of what the model claims.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.llm import call_agent
from agents.stub_fixtures import MAPPER
from workflow.state import ColumnMapping

CONFIDENCE_CAPS = {
    "pii": 0.60,                 # sensitive data always gets a human decision
    "mixed_domain": 0.70,        # column holds values from more than one encoding
    "unexplained_values": 0.70,  # model admitted it could not explain some values
    "lossy_cast": 0.75,          # < 99% of values parse into the proposed type
}
STUB_TARGET_TABLES = {"pt_demog": "patient", "patient_records": "encounter"}


def calibrate(llm_confidence: float, column_profile: dict[str, Any],
              llm_item: dict[str, Any]) -> tuple[float, list[str]]:
    caps = []
    if column_profile.get("pii"):
        caps.append("pii")
    if column_profile.get("mixed_domain"):
        caps.append("mixed_domain")
    if llm_item.get("unexplained_values"):
        caps.append("unexplained_values")
    parse_rate = column_profile.get("parse_rate") or {}
    if parse_rate and min(parse_rate.values()) < 0.99:
        caps.append("lossy_cast")
    return min([llm_confidence] + [CONFIDENCE_CAPS[c] for c in caps]), caps


def map_table(run_id: str, table: str, table_profile: dict[str, Any],
              config: RunnableConfig) -> list[dict[str, Any]]:
    stub = {"target_table": STUB_TARGET_TABLES.get(table, table), "columns": MAPPER.get(table, [])}
    out, prompt_id, prompt_version = call_agent(
        run_id=run_id, agent="ai_mapper", prompt="ai_mapper", version="v1",
        variables={"table": table, "table_profile": json.dumps(table_profile, indent=2, default=str)},
        stub_response=stub, config=config,
    )
    by_col = {c["source_column"]: c for c in out["columns"]}
    mappings = []
    for col, col_profile in table_profile["columns"].items():
        item = by_col.get(col) or {
            "target_column": col, "target_type": col_profile["type"],
            "inferred_meaning": "UNMAPPED", "reasoning": "Model returned no mapping for this column",
            "confidence": 0.0, "unexplained_values": [],
        }
        confidence, caps = calibrate(item["confidence"], col_profile, item)
        mappings.append(ColumnMapping(
            mapping_id=f"{table}.{col}", source_table=table, source_column=col,
            source_type=col_profile["type"], target_table=out["target_table"],
            target_column=item["target_column"], target_type=item["target_type"],
            inferred_meaning=item["inferred_meaning"], reasoning=item["reasoning"],
            llm_confidence=item["confidence"], confidence=confidence, confidence_caps=caps,
            prompt_id=prompt_id, prompt_version=prompt_version,
        ).model_dump())
    return mappings

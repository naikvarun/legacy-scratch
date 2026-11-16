"""rule_generator — turns each approved mapping into a Section-07 transformation rule.

Rules always start with human_reviewed=False: a human approving the *mapping* is not the
same as a human approving the *SQL*, so low-confidence rules go back through the gate.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.llm import call_agent
from agents.stub_fixtures import RULES
from workflow.state import TransformationRule

_STUB_FALLBACK = {"logic": "src", "null_handling": "Keep NULL.", "edge_cases": [], "confidence": 0.5}


def generate_rule(run_id: str, mapping: dict[str, Any], column_profile: dict[str, Any],
                  config: RunnableConfig) -> dict[str, Any]:
    mapping_view = {k: mapping[k] for k in (
        "source_table", "source_column", "source_type", "target_table", "target_column",
        "target_type", "inferred_meaning", "review")}
    out, prompt_id, prompt_version = call_agent(
        run_id=run_id, agent="rule_generator", prompt="rule_generator", version="v1",
        variables={"mapping": json.dumps(mapping_view, indent=2, default=str),
                   "column_profile": json.dumps(column_profile, indent=2, default=str)},
        stub_response=RULES.get(mapping["mapping_id"], _STUB_FALLBACK), config=config,
    )
    return TransformationRule(
        source_column=mapping["source_column"], target_column=mapping["target_column"],
        logic=out["logic"], null_handling=out["null_handling"], edge_cases=out["edge_cases"],
        confidence=out["confidence"], prompt_id=prompt_id,
        rule_id=f"rule:{mapping['mapping_id']}", mapping_id=mapping["mapping_id"],
        source_table=mapping["source_table"], target_table=mapping["target_table"],
        prompt_version=prompt_version,
    ).model_dump()

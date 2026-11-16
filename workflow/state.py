"""Graph state and the domain models that flow through it.

The LangGraph state holds plain JSON-able dicts (``model_dump()`` output) so the SQLite
checkpointer can serialise it without custom types. The Pydantic models below validate
data at node boundaries.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

RunStatus = Literal["RUNNING", "FAILED", "COMPLETED"]
ReviewStage = Literal["mapping", "rule"]


class ReviewRecord(BaseModel):
    decision: Literal["auto_approved", "approved", "rejected", "overridden"]
    reviewer: str
    note: str | None = None
    at: str


class ColumnMapping(BaseModel):
    mapping_id: str                      # "<source_table>.<source_column>"
    source_table: str
    source_column: str
    source_type: str
    target_table: str
    target_column: str
    target_type: str
    inferred_meaning: str
    reasoning: str
    llm_confidence: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)  # after deterministic calibration caps
    confidence_caps: list[str] = []
    prompt_id: str
    prompt_version: str
    review: ReviewRecord | None = None


class TransformationRule(BaseModel):
    # --- Section 07 rule schema (required fields) ---
    source_column: str
    target_column: str
    logic: str                           # SQL expression; `src` is the source value
    null_handling: str
    edge_cases: list[str]
    confidence: float = Field(ge=0, le=1)
    prompt_id: str
    human_reviewed: bool = False
    override_note: str | None = None
    # --- traceability extras ---
    rule_id: str
    mapping_id: str
    source_table: str
    target_table: str
    prompt_version: str
    reviewed_by: str | None = None


class Decision(BaseModel):
    id: str
    decision: Literal["approve", "reject", "override"]
    note: str | None = None
    changes: dict[str, Any] = {}
    stage: ReviewStage | None = None     # only used to filter a combined decisions file


class ReviewResponse(BaseModel):
    reviewer: str
    decisions: list[Decision]


class MigrationState(TypedDict, total=False):
    run_id: str
    config: dict[str, Any]               # threshold, tables, target, fail_at, ...
    status: RunStatus
    profile: dict[str, Any]
    mappings: list[dict[str, Any]]       # ColumnMapping dumps
    rules: list[dict[str, Any]]          # TransformationRule dumps
    rejected: list[dict[str, Any]]
    exec_results: dict[str, Any]
    validation: dict[str, Any]
    artifacts: dict[str, str]
    errors: list[str]

"""human_review_gate — the confidence gate. Every mapping and rule passes through it.

The same node serves two stages:
  * mapping stage (before rules exist): every mapping gets a review record; those below
    the threshold need a human decision, the rest are logged as auto_approved.
  * rule stage (after rule_generator): rules below the threshold need a human decision,
    which sets human_reviewed=True and a non-null override_note.

The node pauses with ``interrupt()``. On resume LangGraph re-runs the node from the top,
so nothing with side effects happens before the interrupt; audit writes come after.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import ValidationError

from audit.logger import AuditLog
from observability.tracing import score, trace_id_for
from workflow.state import MigrationState, ReviewResponse, ReviewStage

SYSTEM_REVIEWER = "system:confidence_gate"
OVERRIDABLE = {
    "mapping": {"target_table", "target_column", "target_type", "inferred_meaning"},
    "rule": {"logic", "null_handling", "edge_cases", "target_column"},
}


def review_stage(state: MigrationState) -> ReviewStage:
    return "rule" if state.get("rules") else "mapping"


def pending_items(state: MigrationState, stage: ReviewStage) -> list[dict[str, Any]]:
    th = state["config"]["threshold"]
    if stage == "mapping":
        return [m for m in state.get("mappings", []) if m["confidence"] < th and m["review"] is None]
    return [r for r in state.get("rules", []) if r["confidence"] < th and not r["human_reviewed"]]


def item_id(item: dict[str, Any], stage: ReviewStage) -> str:
    return item["mapping_id"] if stage == "mapping" else item["rule_id"]


def review_request(state: MigrationState, stage: ReviewStage, flagged: list[dict[str, Any]],
                   errors: list[str]) -> dict[str, Any]:
    return {"run_id": state["run_id"], "stage": stage, "threshold": state["config"]["threshold"],
            "errors": errors, "allowed_changes": sorted(OVERRIDABLE[stage]),
            "items": [{"id": item_id(i, stage), **i} for i in flagged]}


def validate_response(raw: Any, flagged: list[dict[str, Any]],
                      stage: ReviewStage) -> tuple[ReviewResponse | None, list[str]]:
    try:
        resp = ReviewResponse.model_validate(raw)
    except ValidationError as exc:
        return None, [f"Malformed review response: {exc.errors()[0]['msg']}"]
    errors = []
    expected = {item_id(i, stage) for i in flagged}
    given = {d.id for d in resp.decisions}
    if missing := expected - given:
        errors.append(f"Missing decisions for: {', '.join(sorted(missing))}")
    if unknown := given - expected:
        errors.append(f"Decisions for items not under review: {', '.join(sorted(unknown))}")
    if not resp.reviewer.strip():
        errors.append("reviewer is required")
    for d in resp.decisions:
        if not (d.note and d.note.strip()):
            errors.append(f"{d.id}: a note explaining the decision is required")
        if d.decision == "override":
            if not d.changes:
                errors.append(f"{d.id}: override needs at least one change")
            if bad := set(d.changes) - OVERRIDABLE[stage]:
                errors.append(f"{d.id}: cannot override {', '.join(sorted(bad))} at {stage} stage")
    return resp, errors


def human_review_gate(state: MigrationState, config: RunnableConfig) -> dict[str, Any]:
    stage = review_stage(state)
    flagged = pending_items(state, stage)
    resp, errors = None, []
    while flagged:
        raw = interrupt(review_request(state, stage, flagged, errors))
        resp, errors = validate_response(raw, flagged, stage)
        if not errors:
            break

    run_id = state["run_id"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    decisions = {d.id: d for d in resp.decisions} if resp else {}
    audit, trace = AuditLog(), trace_id_for(run_id)
    updated, rejected = [], list(state.get("rejected", []))

    items = state["mappings"] if stage == "mapping" else state["rules"]
    for item in items:
        item = dict(item)
        iid = item_id(item, stage)
        d = decisions.get(iid)
        if d is None:
            if stage == "mapping" and item["review"] is None:
                item["review"] = {"decision": "auto_approved", "reviewer": SYSTEM_REVIEWER,
                                  "note": f"confidence {item['confidence']:.2f} >= threshold", "at": now}
                audit.append(run_id, "mapping_auto_approved", SYSTEM_REVIEWER,
                             {"id": iid, "confidence": item["confidence"]},
                             prompt_id=item["prompt_id"], trace_id=trace)
            updated.append(item)
            continue

        before = {k: item.get(k) for k in d.changes}
        item.update(d.changes)
        if stage == "mapping":
            label = {"approve": "approved", "reject": "rejected", "override": "overridden"}[d.decision]
            item["review"] = {"decision": label, "reviewer": resp.reviewer, "note": d.note, "at": now}
        else:
            item.update(human_reviewed=True, override_note=d.note, reviewed_by=resp.reviewer)
        audit.append(run_id, f"human_{stage}_decision", f"human:{resp.reviewer}",
                     {"id": iid, "decision": d.decision, "note": d.note, "changes": d.changes,
                      "before": before, "confidence": item["confidence"]},
                     prompt_id=item["prompt_id"], trace_id=trace)
        score(run_id, f"human_{stage}_decision", d.decision, data_type="CATEGORICAL",
              comment=f"{iid}: {d.note}")
        if d.decision == "reject":
            rejected.append({"stage": stage, "id": iid, "note": d.note, "reviewer": resp.reviewer})
            if stage == "rule":
                continue  # rejected rules never reach the executor
        updated.append(item)

    out: dict[str, Any] = {"mappings" if stage == "mapping" else "rules": updated, "rejected": rejected}
    approved = [m for m in updated if stage == "rule" or m["review"]["decision"] != "rejected"]
    if not approved:
        out["status"] = "FAILED"
        out["errors"] = state.get("errors", []) + [f"Nothing left to migrate after {stage} review"]
    return out

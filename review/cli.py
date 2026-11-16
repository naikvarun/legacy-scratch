"""Human review interface: interactive terminal prompts, or a decisions JSON file.

Decisions file format (one file may cover both stages; `stage` picks which applies):

    {"reviewer": "jane.doe (clinic ops)",
     "decisions": [
       {"stage": "mapping", "id": "patient_records.pat_st_cd", "decision": "approve",
        "note": "Confirmed with ward admin"},
       {"stage": "rule", "id": "rule:patient_records.pat_st_cd", "decision": "override",
        "note": "X = Expired", "changes": {"logic": "CASE ... END"}}]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

Responder = Callable[[dict[str, Any]], dict[str, Any] | None]

_SHOW = {
    "mapping": ["source_type", "target_table", "target_column", "target_type", "inferred_meaning",
                "reasoning", "llm_confidence", "confidence_caps"],
    "rule": ["target_table", "target_column", "logic", "null_handling", "edge_cases"],
}


def file_responder(path: str | Path) -> Responder:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))

    def respond(request: dict[str, Any]) -> dict[str, Any] | None:
        wanted = {i["id"] for i in request["items"]}
        decisions = [d for d in doc["decisions"]
                     if d.get("stage") in (None, request["stage"]) and d["id"] in wanted]
        return {"reviewer": doc["reviewer"], "decisions": decisions} if decisions else None
    return respond


def interactive_responder(reviewer: str | None = None) -> Responder:
    def respond(request: dict[str, Any]) -> dict[str, Any] | None:
        print(f"\n=== Human review: {request['stage']} stage, run {request['run_id']} ===")
        print(f"{len(request['items'])} item(s) below confidence threshold {request['threshold']:.2f}")
        for err in request.get("errors", []):
            print(f"  ! previous response rejected: {err}")
        who = reviewer or _ask("Reviewer name: ")
        decisions = []
        for item in request["items"]:
            print(f"\n--- {item['id']}  (confidence {item['confidence']:.2f})")
            for key in _SHOW[request["stage"]]:
                print(f"  {key:18} {item.get(key)}")
            choice = _ask("  [a]pprove / [r]eject / [o]verride: ", {"a", "r", "o"})
            decision = {"a": "approve", "r": "reject", "o": "override"}[choice]
            changes: dict[str, Any] = {}
            if decision == "override":
                print(f"  Fields you can change: {', '.join(request['allowed_changes'])}")
                while True:
                    field = input("  field to change (blank when done): ").strip()
                    if not field:
                        break
                    if field not in request["allowed_changes"]:
                        print("  not overridable at this stage")
                        continue
                    value = input(f"  new {field}: ").strip()
                    changes[field] = [v.strip() for v in value.split(";")] if field == "edge_cases" else value
            note = _ask("  note (required, why?): ")
            decisions.append({"id": item["id"], "decision": decision, "note": note, "changes": changes})
        return {"reviewer": who, "decisions": decisions}
    return respond


def _ask(prompt: str, allowed: set[str] | None = None) -> str:
    while True:
        value = input(prompt).strip()
        if value and (allowed is None or value.lower() in allowed):
            return value.lower() if allowed else value

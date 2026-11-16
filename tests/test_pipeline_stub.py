"""End-to-end tests of the stubbed pipeline: gating, resume, audit integrity."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.ai_mapper import calibrate
from audit.logger import AuditLog
from executor.migration_executor import UnapprovedRuleError, assert_rules_executable
from workflow import langgraph_orchestrator as orch
from workflow.config import ROOT

SAMPLE = str(ROOT / "review" / "sample_decisions.json")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MIGRATION_WORKDIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODE", "stub")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    return tmp_path


def _values(run_id: str) -> dict:
    with orch.open_graph() as graph:
        return graph.get_state(orch._thread(run_id)).values


def _events(run_id: str) -> list[str]:
    return [e["event"] for e in AuditLog().entries(run_id)]


def test_full_run_with_decisions_file(isolated):
    assert orch.main(["run", "--run-id", "r1", "--decisions", SAMPLE]) == orch.EXIT_COMPLETED
    v = _values("r1")
    assert v["status"] == "COMPLETED"
    # No mapping bypasses the gate: every one carries a review record.
    assert all(m["review"] is not None for m in v["mappings"])
    # Every low-confidence rule was human-reviewed with a note.
    assert_rules_executable(v["rules"], 0.80)
    status_rule = next(r for r in v["rules"] if r["source_column"] == "pat_st_cd")
    assert "'EXPIRED'" in status_rule["logic"] and status_rule["human_reviewed"]
    assert not AuditLog().verify()
    assert (isolated / "audit" / "migration_audit_log.json").exists()
    assert (isolated / "runs" / "r1" / "data_dictionary.md").exists()


def test_pauses_at_both_gates_then_completes():
    assert orch.main(["run", "--run-id", "r2"]) == orch.EXIT_PAUSED
    assert not _values("r2").get("rules")  # paused before rule generation
    assert orch.main(["review", "--run-id", "r2", "--decisions", SAMPLE]) == orch.EXIT_COMPLETED
    events = _events("r2")
    assert events.count("review_requested") == 2  # mapping stage, then rule stage
    assert events.count("human_rule_decision") == 3


def test_invalid_decisions_are_rejected_and_gate_stays_closed(tmp_path):
    bad = json.loads(Path(SAMPLE).read_text())
    for d in bad["decisions"]:
        d["note"] = ""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text(json.dumps(bad))

    assert orch.main(["run", "--run-id", "r3", "--decisions", str(bad_file)]) == orch.EXIT_PAUSED
    assert not _values("r3").get("rules")
    assert orch.main(["review", "--run-id", "r3", "--decisions", SAMPLE]) == orch.EXIT_COMPLETED


def test_crash_then_resume_from_checkpoint():
    assert orch.main(["run", "--run-id", "r4", "--decisions", SAMPLE,
                      "--fail-at", "migration_executor"]) == orch.EXIT_FAILED
    assert "node_failed" in _events("r4")
    assert orch.main(["resume", "--run-id", "r4"]) == orch.EXIT_COMPLETED
    # Work done before the crash was not repeated.
    assert _events("r4").count("human_mapping_decision") == 6


def test_validation_failure_blocks_completion(isolated):
    assert orch.main(["run", "--run-id", "r5", "--decisions", SAMPLE,
                      "--force-validation-failure"]) == orch.EXIT_FAILED
    v = _values("r5")
    assert v["status"] == "FAILED" and not v["validation"]["passed"]
    assert not (isolated / "runs" / "r5" / "data_dictionary.md").exists()


def test_executor_guard_blocks_unapproved_rules():
    rule = {"rule_id": "rule:x", "confidence": 0.5, "human_reviewed": False, "override_note": None}
    with pytest.raises(UnapprovedRuleError):
        assert_rules_executable([rule], 0.80)
    with pytest.raises(UnapprovedRuleError):  # reviewed but no note is still unapproved
        assert_rules_executable([{**rule, "human_reviewed": True}], 0.80)
    assert_rules_executable([{**rule, "human_reviewed": True, "override_note": "ok"}], 0.80)


def test_audit_tampering_is_detected():
    log = AuditLog()
    for i in range(3):
        log.append("r6", "event", "test", {"i": i})
    lines = log.path.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["payload"]["i"] = 99
    lines[1] = json.dumps(entry)
    log.path.write_text("\n".join(lines) + "\n")
    assert any("hash mismatch" in p for p in log.verify())


def test_calibration_caps_override_model_confidence():
    assert calibrate(0.95, {"pii": True}, {}) == (0.60, ["pii"])
    conf, caps = calibrate(0.9, {"parse_rate": {"date": 0.97}}, {"unexplained_values": ["X"]})
    assert conf == 0.70 and set(caps) == {"unexplained_values", "lossy_cast"}
    assert calibrate(0.9, {}, {"unexplained_values": []}) == (0.9, [])

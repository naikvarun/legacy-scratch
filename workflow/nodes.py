"""The seven LangGraph nodes. Each is a thin adapter: read state, call the agent/module,
write artefacts, return a partial state update. ``@audited`` logs completion or failure.
"""
from __future__ import annotations

import json
import time
from functools import wraps
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphBubbleUp

from agents import ai_mapper, doc_generator, rule_generator, schema_profiler
from audit.logger import AuditLog
from executor import migration_executor
from observability.tracing import score, trace_id_for
from validation import checks
from workflow import config as cfg
from workflow.gates import human_review_gate  # noqa: F401  (re-exported for the graph)
from workflow.state import MigrationState

Node = Callable[[MigrationState, RunnableConfig], dict[str, Any]]


class SimulatedFailure(RuntimeError):
    pass


def _maybe_fail(state: MigrationState, node: str) -> None:
    """--fail-at <node>: crash once, so `resume` can demonstrate retry-from-checkpoint."""
    if state["config"].get("fail_at") != node:
        return
    marker = cfg.run_dir(state["run_id"]) / f".failed_once_{node}"
    if not marker.exists():
        marker.touch()
        raise SimulatedFailure(f"Simulated failure in {node} (run `resume` to continue)")


def audited(name: str) -> Callable[[Node], Node]:
    def deco(fn: Node) -> Node:
        @wraps(fn)
        def wrapper(state: MigrationState, config: RunnableConfig) -> dict[str, Any]:
            run_id, t0 = state["run_id"], time.monotonic()
            try:
                out = fn(state, config)
            except GraphBubbleUp:  # interrupt() — a pause, not a failure
                raise
            except Exception as exc:
                AuditLog().append(run_id, "node_failed", f"node:{name}",
                                  {"error": f"{type(exc).__name__}: {exc}"}, trace_id=trace_id_for(run_id))
                raise
            AuditLog().append(run_id, "node_completed", f"node:{name}",
                              {"duration_ms": round((time.monotonic() - t0) * 1000),
                               "updated_keys": sorted(out)}, trace_id=trace_id_for(run_id))
            return out
        return wrapper
    return deco


def _write(state: MigrationState, name: str, data: Any) -> str:
    path = cfg.run_dir(state["run_id"]) / name
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return str(path)


@audited("schema_profiler")
def schema_profiler_node(state: MigrationState, config: RunnableConfig) -> dict[str, Any]:
    _maybe_fail(state, "schema_profiler")
    profile = schema_profiler.profile_source(state["config"].get("tables"))
    baseline = checks.run_ge_checkpoint("source_baseline", list(profile["tables"]))
    if not baseline["success"]:
        return {"status": "FAILED", "errors": ["GE source baseline failed; nothing may be moved"]}
    return {"profile": profile, "validation": {"source_baseline": baseline},
            "artifacts": {"profile": _write(state, "profile.json", profile)}}


@audited("ai_mapper")
def ai_mapper_node(state: MigrationState, config: RunnableConfig) -> dict[str, Any]:
    _maybe_fail(state, "ai_mapper")
    mappings = []
    for table, table_profile in state["profile"]["tables"].items():
        mappings += ai_mapper.map_table(state["run_id"], table, table_profile, config)
    for m in mappings:
        score(state["run_id"], "mapping_confidence", m["confidence"],
              comment=f"{m['mapping_id']} caps={m['confidence_caps']}")
    return {"mappings": mappings,
            "artifacts": {**state.get("artifacts", {}), "mappings": _write(state, "mappings.json", mappings)}}


@audited("rule_generator")
def rule_generator_node(state: MigrationState, config: RunnableConfig) -> dict[str, Any]:
    _maybe_fail(state, "rule_generator")
    rules = []
    for m in state["mappings"]:
        if m["review"]["decision"] == "rejected":
            continue
        col_profile = state["profile"]["tables"][m["source_table"]]["columns"][m["source_column"]]
        rules.append(rule_generator.generate_rule(state["run_id"], m, col_profile, config))
    return {"rules": rules}


@audited("migration_executor")
def migration_executor_node(state: MigrationState, config: RunnableConfig) -> dict[str, Any]:
    _maybe_fail(state, "migration_executor")
    out_dir = cfg.run_dir(state["run_id"])
    rules_path = _write(state, "rules.json", state["rules"])  # the exact approved rule set executed
    results = migration_executor.execute(state["run_id"], state["profile"], state["rules"],
                                         state["config"]["threshold"], out_dir)
    post_extract = checks.run_ge_checkpoint("post_extraction", list(results["tables"]))
    return {"exec_results": results,
            "validation": {**state["validation"], "post_extraction": post_extract},
            "artifacts": {**state["artifacts"], "rules": rules_path}}


@audited("validator")
def validator_node(state: MigrationState, config: RunnableConfig) -> dict[str, Any]:
    _maybe_fail(state, "validator")
    tables = [t for t, r in state["exec_results"]["tables"].items() if "skipped" not in r]
    post_load = checks.run_ge_checkpoint("post_load", tables)
    dbt = checks.run_dbt_tests()
    recon = checks.reconcile(state["profile"], state["exec_results"], cfg.run_dir(state["run_id"]),
                             force_failure=state["config"].get("force_validation_failure", False))
    passed = state["validation"]["post_extraction"]["success"] and post_load["success"] \
        and dbt["success"] and recon["success"]
    out: dict[str, Any] = {
        "validation": {**state["validation"], "post_load": post_load, "dbt": dbt,
                       "reconciliation": recon, "passed": passed},
        "artifacts": {**state["artifacts"], "reconciliation": recon["path"]},
    }
    if not passed:
        out["status"] = "FAILED"
        out["errors"] = state.get("errors", []) + [
            "Validation failed; staged tables were NOT swapped into the target (see reconciliation report)"]
    return out


@audited("doc_generator")
def doc_generator_node(state: MigrationState, config: RunnableConfig) -> dict[str, Any]:
    _maybe_fail(state, "doc_generator")
    paths = doc_generator.generate_dictionary(state["run_id"], state["mappings"], state["rules"],
                                              cfg.run_dir(state["run_id"]), config)
    return {"status": "COMPLETED", "artifacts": {**state["artifacts"], **paths}}

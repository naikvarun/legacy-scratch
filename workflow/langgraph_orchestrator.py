"""LangGraph migration orchestrator and CLI.

    schema_profiler -> ai_mapper -> human_review_gate -> rule_generator -+-> migration_executor
                                        ^      (mapping stage)           |        |
                                        +---- rules below threshold -----+        v
                                                                      validator -> doc_generator

Usage:
    python -m workflow.langgraph_orchestrator run [--tables a,b] [--decisions FILE] [--interactive]
    python -m workflow.langgraph_orchestrator review --run-id ID [--decisions FILE]
    python -m workflow.langgraph_orchestrator resume --run-id ID
    python -m workflow.langgraph_orchestrator status --run-id ID
    python -m workflow.langgraph_orchestrator verify-audit

Exit codes: 0 completed, 2 paused for human review, 1 failed or crashed (resumable).
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from audit.logger import AuditLog
from observability.tracing import trace_id_for, traced_invocation
from review.cli import Responder, file_responder, interactive_responder
from workflow import config as cfg
from workflow import nodes
from workflow.gates import SYSTEM_REVIEWER, human_review_gate, pending_items
from workflow.state import MigrationState

EXIT_COMPLETED, EXIT_FAILED, EXIT_PAUSED = 0, 1, 2


# ---------------------------------------------------------------- graph
def route_after_profiler(state: MigrationState) -> str:
    return END if state.get("status") == "FAILED" else "ai_mapper"


def route_after_gate(state: MigrationState) -> str:
    if state.get("status") == "FAILED":
        return END
    return "migration_executor" if state.get("rules") else "rule_generator"


def route_after_rules(state: MigrationState) -> str:
    return "human_review_gate" if pending_items(state, "rule") else "migration_executor"


def route_after_validator(state: MigrationState) -> str:
    return END if state.get("status") == "FAILED" else "doc_generator"


def build_graph(checkpointer=None) -> CompiledStateGraph:
    g = StateGraph(MigrationState)
    g.add_node("schema_profiler", nodes.schema_profiler_node)
    g.add_node("ai_mapper", nodes.ai_mapper_node)
    g.add_node("human_review_gate", nodes.audited("human_review_gate")(human_review_gate))
    g.add_node("rule_generator", nodes.rule_generator_node)
    g.add_node("migration_executor", nodes.migration_executor_node)
    g.add_node("validator", nodes.validator_node)
    g.add_node("doc_generator", nodes.doc_generator_node)

    g.add_edge(START, "schema_profiler")
    g.add_conditional_edges("schema_profiler", route_after_profiler, ["ai_mapper", END])
    g.add_edge("ai_mapper", "human_review_gate")
    g.add_conditional_edges("human_review_gate", route_after_gate,
                            ["rule_generator", "migration_executor", END])
    g.add_conditional_edges("rule_generator", route_after_rules,
                            ["human_review_gate", "migration_executor"])
    g.add_edge("migration_executor", "validator")
    g.add_conditional_edges("validator", route_after_validator, ["doc_generator", END])
    g.add_edge("doc_generator", END)
    return g.compile(checkpointer=checkpointer)


@contextmanager
def open_graph() -> Iterator[CompiledStateGraph]:
    with SqliteSaver.from_conn_string(str(cfg.checkpoint_db_path())) as saver:
        yield build_graph(saver)


# ---------------------------------------------------------------- driving
def _thread(run_id: str, callbacks: list[Any] | None = None) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}, "callbacks": callbacks or []}


def _pending_request(graph: CompiledStateGraph, run_id: str) -> dict[str, Any] | None:
    snap = graph.get_state(_thread(run_id))
    for task in snap.tasks:
        for intr in task.interrupts:
            return intr.value
    return None


def drive(graph: CompiledStateGraph, run_id: str, payload: Any, leg: str,
          responder: Responder | None = None) -> int:
    """Invoke the graph and keep answering interrupts while the responder has answers."""
    audit = AuditLog()
    try:
        with traced_invocation(run_id, leg) as callbacks:
            thread = _thread(run_id, callbacks)
            result = graph.invoke(payload, thread)
            last_answer = None
            while "__interrupt__" in result:
                request = result["__interrupt__"][0].value
                queue = cfg.review_queue_path(run_id)
                queue.write_text(json.dumps(request, indent=2, default=str), encoding="utf-8")
                audit.append(run_id, "review_requested", SYSTEM_REVIEWER,
                             {"stage": request["stage"], "items": [i["id"] for i in request["items"]],
                              "errors": request["errors"], "queue_file": str(queue)},
                             trace_id=trace_id_for(run_id))
                answer = responder(request) if responder else None
                if answer is None or answer == last_answer:
                    for err in request["errors"]:
                        print(f"  ! {err}")
                    print(f"\nPAUSED for {request['stage']} review: {len(request['items'])} item(s) "
                          f"below {request['threshold']:.2f}.\n  queue: {queue}\n  next:  python -m "
                          f"workflow.langgraph_orchestrator review --run-id {run_id} [--decisions FILE]")
                    return EXIT_PAUSED
                last_answer = answer
                result = graph.invoke(Command(resume=answer), thread)
    except Exception as exc:
        print(f"\nRun {run_id} stopped with an error: {type(exc).__name__}: {exc}\n"
              f"  Fix the cause, then: python -m workflow.langgraph_orchestrator resume --run-id {run_id}")
        return EXIT_FAILED
    finally:
        audit.export()

    print(f"\nRun {run_id} finished: {result.get('status')}")
    for err in result.get("errors", []):
        print(f"  ! {err}")
    for name, path in result.get("artifacts", {}).items():
        print(f"  {name:22} {path}")
    return EXIT_COMPLETED if result.get("status") == "COMPLETED" else EXIT_FAILED


def _responder(args: argparse.Namespace) -> Responder | None:
    if getattr(args, "decisions", None):
        return file_responder(args.decisions)
    if getattr(args, "interactive", False):
        return interactive_responder(getattr(args, "reviewer", None))
    return None


# ---------------------------------------------------------------- commands
def cmd_run(args: argparse.Namespace) -> int:
    run_id = args.run_id or f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    run_config = {
        "threshold": args.threshold, "target": "snowflake",
        "tables": [t.strip() for t in args.tables.split(",")] if args.tables else None,
        "fail_at": args.fail_at, "force_validation_failure": args.force_validation_failure,
    }
    AuditLog().append(run_id, "run_started", "system:cli", {"config": run_config},
                      trace_id=trace_id_for(run_id))
    print(f"Run id: {run_id}")
    initial: MigrationState = {"run_id": run_id, "config": run_config, "status": "RUNNING",
                               "errors": [], "rejected": [], "artifacts": {}, "validation": {}}
    with open_graph() as graph:
        return drive(graph, run_id, initial, "run", _responder(args))


def cmd_review(args: argparse.Namespace) -> int:
    with open_graph() as graph:
        request = _pending_request(graph, args.run_id)
        if request is None:
            print(f"Run {args.run_id} has nothing waiting for review.")
            return EXIT_FAILED
        responder = _responder(args) or interactive_responder(args.reviewer)
        answer = responder(request)
        if answer is None:
            print("The decisions file has no answers for the pending items.")
            return EXIT_PAUSED
        return drive(graph, args.run_id, Command(resume=answer), "review", responder)


def cmd_resume(args: argparse.Namespace) -> int:
    with open_graph() as graph:
        if not graph.get_state(_thread(args.run_id)).next:
            print(f"Run {args.run_id} has no unfinished work.")
            return EXIT_COMPLETED
        return drive(graph, args.run_id, None, "resume", _responder(args))


def cmd_status(args: argparse.Namespace) -> int:
    with open_graph() as graph:
        snap = graph.get_state(_thread(args.run_id))
        if not snap.values:
            print(f"Unknown run {args.run_id}")
            return EXIT_FAILED
        v, request = snap.values, _pending_request(graph, args.run_id)
        print(json.dumps({
            "run_id": args.run_id,
            "status": "AWAITING_REVIEW" if request else v.get("status"),
            "next_nodes": list(snap.next),
            "pending_review": {"stage": request["stage"], "items": [i["id"] for i in request["items"]]}
            if request else None,
            "mappings": len(v.get("mappings", [])), "rules": len(v.get("rules", [])),
            "rejected": v.get("rejected", []), "errors": v.get("errors", []),
            "artifacts": v.get("artifacts", {}),
        }, indent=2))
    return EXIT_COMPLETED


def cmd_verify_audit(_: argparse.Namespace) -> int:
    log = AuditLog()
    problems = log.verify()
    print(f"{len(log.entries())} entries; " + ("chain intact" if not problems else "INTEGRITY FAILURES:"))
    for p in problems:
        print(f"  ! {p}")
    return EXIT_FAILED if problems else EXIT_COMPLETED


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="migrate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="start a migration run")
    run.add_argument("--tables", help="comma-separated subset of source tables (default: all)")
    run.add_argument("--threshold", type=float, default=cfg.default_threshold())
    run.add_argument("--run-id")
    run.add_argument("--decisions", help="answer review gates from this JSON file")
    run.add_argument("--interactive", action="store_true", help="answer review gates in the terminal")
    run.add_argument("--reviewer")
    run.add_argument("--fail-at", help="simulate one crash in this node (to demo resume)")
    run.add_argument("--force-validation-failure", action="store_true")
    run.set_defaults(fn=cmd_run)

    for name, fn, help_ in (("review", cmd_review, "answer a paused review gate"),
                            ("resume", cmd_resume, "continue a crashed run from its last checkpoint"),
                            ("status", cmd_status, "show a run's state")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--run-id", required=True)
        if name != "status":
            p.add_argument("--decisions")
            p.add_argument("--reviewer")
        p.set_defaults(fn=fn)

    sub.add_parser("verify-audit", help="check the audit log hash chain").set_defaults(fn=cmd_verify_audit)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

"""Paths and settings. Paths are resolved lazily so tests can redirect MIGRATION_WORKDIR."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def workdir() -> Path:
    return Path(os.getenv("MIGRATION_WORKDIR") or ROOT)


def _ensure(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def run_dir(run_id: str) -> Path:
    p = workdir() / "runs" / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def audit_log_path() -> Path:
    return _ensure(workdir() / "audit" / "audit_log.jsonl")


def audit_export_path() -> Path:
    return _ensure(workdir() / "audit" / "migration_audit_log.json")


def checkpoint_db_path() -> Path:
    return _ensure(workdir() / "checkpoints" / "langgraph.sqlite")


def review_queue_path(run_id: str) -> Path:
    return _ensure(workdir() / "review" / "queue" / f"{run_id}.json")


def default_threshold() -> float:
    return float(os.getenv("CONFIDENCE_THRESHOLD", "0.80"))

"""Append-only, hash-chained audit log.

Every entry stores the SHA-256 of the previous entry, so editing, deleting or reordering
any line breaks the chain and ``verify()`` reports it. The JSONL file is the source of
truth; ``export()`` writes the submission artefact ``audit/migration_audit_log.json``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflow import config

GENESIS = "0" * 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _digest(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = path or config.audit_log_path()

    def append(self, run_id: str, event: str, actor: str, payload: dict[str, Any] | None = None,
               *, prompt_id: str | None = None, trace_id: str | None = None) -> dict[str, Any]:
        with open(self.path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                last = None
                for line in f:
                    if line.strip():
                        last = line
                prev = json.loads(last) if last else None
                entry = {
                    "seq": prev["seq"] + 1 if prev else 1,
                    "ts": _now(),
                    "run_id": run_id,
                    "event": event,
                    "actor": actor,
                    "prompt_id": prompt_id,
                    "langfuse_trace_id": trace_id,
                    "payload": payload or {},
                    "prev_hash": prev["hash"] if prev else GENESIS,
                }
                entry["hash"] = _digest(entry)
                f.seek(0, 2)
                f.write(json.dumps(entry, default=str) + "\n")
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return entry

    def entries(self, run_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [e for e in out if run_id is None or e["run_id"] == run_id]

    def verify(self) -> list[str]:
        """Return a list of integrity problems; empty means the chain is intact."""
        problems, prev_hash = [], GENESIS
        for i, e in enumerate(self.entries(), start=1):
            if e.get("seq") != i:
                problems.append(f"line {i}: seq {e.get('seq')} out of order")
            if e.get("prev_hash") != prev_hash:
                problems.append(f"seq {e.get('seq')}: prev_hash does not match previous entry")
            if _digest(e) != e.get("hash"):
                problems.append(f"seq {e.get('seq')}: content hash mismatch (entry was modified)")
            prev_hash = e.get("hash")
        return problems

    def export(self, out: Path | None = None) -> Path:
        out = out or config.audit_export_path()
        entries = self.entries()
        doc = {
            "exported_at": _now(),
            "entry_count": len(entries),
            "chain_head": entries[-1]["hash"] if entries else GENESIS,
            "integrity_ok": not self.verify(),
            "entries": entries,
        }
        out.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        return out

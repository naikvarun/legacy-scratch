"""LangFuse wiring (Python SDK v4).

One LangFuse trace per migration run: the trace id is derived from the run id, so every
CLI leg (run, review, resume) lands in the same trace and session. When the LangFuse keys
are not set, everything here becomes a no-op so the pipeline still runs offline.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger(__name__)

# The problem statement names LANGFUSE_HOST; SDK v4 reads LANGFUSE_BASE_URL.
if os.getenv("LANGFUSE_HOST") and not os.getenv("LANGFUSE_BASE_URL"):
    os.environ["LANGFUSE_BASE_URL"] = os.environ["LANGFUSE_HOST"]


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def trace_id_for(run_id: str) -> str | None:
    if not enabled():
        return None
    from langfuse import Langfuse
    return Langfuse.create_trace_id(seed=run_id)


@contextmanager
def traced_invocation(run_id: str, leg: str) -> Iterator[list[Any]]:
    """Yield the LangChain callbacks to pass into graph.invoke()."""
    if not enabled():
        yield []
        return
    from langfuse import get_client, propagate_attributes
    from langfuse.langchain import CallbackHandler

    client = get_client()
    try:
        with client.start_as_current_observation(
            as_type="span", name=f"migration.{leg}",
            trace_context={"trace_id": trace_id_for(run_id)},
        ):
            with propagate_attributes(
                trace_name=f"legacy-migration {run_id}", session_id=run_id,
                tags=["legacy-migration", leg], metadata={"run_id": run_id},
            ):
                yield [CallbackHandler()]
    finally:
        client.flush()


def score(run_id: str, name: str, value: float | str, *, comment: str | None = None,
          data_type: str = "NUMERIC") -> None:
    """Attach a score (confidence, human decision) to the run's trace."""
    if not enabled():
        return
    try:
        from langfuse import get_client
        get_client().create_score(trace_id=trace_id_for(run_id), name=name, value=value,
                                  data_type=data_type, comment=comment)
    except Exception as exc:  # observability must never break the migration
        log.warning("LangFuse score failed: %s", exc)

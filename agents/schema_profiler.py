"""schema_profiler — introspects the source DB into the profile JSON every agent consumes.

STUB: returns the canned profile from stub_fixtures. The real implementation will use
SQLAlchemy reflection (tables, columns, types, PK/FKs) plus per-column stats queries
(null rate, cardinality, top values, samples, type-parse rates) and PII detection.
"""
from __future__ import annotations

import copy
from typing import Any

from agents.stub_fixtures import PROFILE


def profile_source(tables: list[str] | None = None) -> dict[str, Any]:
    profile = copy.deepcopy(PROFILE)
    if tables:
        unknown = sorted(set(tables) - set(profile["tables"]))
        if unknown:
            raise ValueError(f"Unknown source tables: {', '.join(unknown)}")
        profile["tables"] = {t: v for t, v in profile["tables"].items() if t in tables}
    profile["fk_graph"] = {
        t: sorted({fk["references"].split(".")[0] for fk in meta["foreign_keys"]})
        for t, meta in profile["tables"].items()
    }
    profile["stub"] = True
    return profile

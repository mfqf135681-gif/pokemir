"""Structured diagnostic events — pipeline decision-point telemetry.

Complements full stderr/file logging with narrow, queryable rows:
showdown gate verdicts, all-in candidates, CNN low-confidence rejects, etc.
Linux side can SELECT diagnostic_events to debug a Win-side run.

Failure to insert is **silent** at WARN level — never blocks the main
capture loop. Connection sharing piggybacks on storage.database.engine.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

_engine = None  # lazy import to avoid circular at module load


def _get_engine():
    global _engine
    if _engine is None:
        from storage.database import engine
        _engine = engine
    return _engine


def emit(
    tag: str,
    payload: dict[str, Any],
    *,
    hand_id: Optional[UUID] = None,
    level: str = "INFO",
) -> None:
    """Insert one diagnostic_events row. Never raises.

    Args:
        tag: dotted category, e.g. 'showdown.gate3_reject', 'all_in.candidate'.
        payload: jsonb body — any JSON-serialisable dict.
        hand_id: optional FK to hands(id); None for hand-less events.
        level: 'INFO' / 'WARN' / 'ERROR'.
    """
    try:
        from sqlalchemy import text
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO diagnostic_events (hand_id, tag, level, payload) "
                    "VALUES (:hand_id, :tag, :level, CAST(:payload AS jsonb))"
                ),
                {
                    "hand_id": str(hand_id) if hand_id else None,
                    "tag": tag,
                    "level": level,
                    "payload": _to_json(payload),
                },
            )
    except Exception as e:
        logger.warning(f"diag.emit failed (tag={tag}): {e!r}")


def _to_json(obj: Any) -> str:
    """JSON-encode with fallback for non-serialisable values (UUID, datetime, etc.)."""
    import json
    def default(v):
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)
    return json.dumps(obj, default=default, ensure_ascii=False)

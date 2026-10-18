"""Persistent research-session cooldown for Telegram FloodWait errors.

The read-only research connection is shared across products. A Telegram
FloodWait must therefore block all products using that session, not just
the product that hit the limit. Immutable records prevent a concurrent
shorter wait from overwriting a longer one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.runtime_store import RuntimeStateStore

TELEGRAM_RESEARCH_FLOOD_WAIT_NAMESPACE = "telegram_research_flood_wait"


def _aware(value: object) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def research_flood_wait_until(
    store: RuntimeStateStore, *, now: datetime | None = None
) -> datetime | None:
    """Return the longest outstanding wait; clear only expired events."""
    checked = now or datetime.now(UTC)
    checked = checked.replace(tzinfo=UTC) if checked.tzinfo is None else checked.astimezone(UTC)
    longest = None
    for row in store.list_namespace(TELEGRAM_RESEARCH_FLOOD_WAIT_NAMESPACE):
        until = _aware(row.get("blocked_until"))
        key = str(row.get("id") or "")
        if until is None or until <= checked:
            if key:
                store.delete(TELEGRAM_RESEARCH_FLOOD_WAIT_NAMESPACE, key)
            continue
        if longest is None or until > longest:
            longest = until
    return longest


def record_research_flood_wait(
    store: RuntimeStateStore, seconds: int, *, now: datetime | None = None
) -> datetime:
    checked = now or datetime.now(UTC)
    checked = checked.replace(tzinfo=UTC) if checked.tzinfo is None else checked.astimezone(UTC)
    wait = max(1, int(seconds))
    until = checked + timedelta(seconds=wait)
    event_id = f"{checked.isoformat()}:{uuid4().hex}"
    store.put_if_absent(
        TELEGRAM_RESEARCH_FLOOD_WAIT_NAMESPACE,
        event_id,
        {
            "id": event_id,
            "recorded_at": checked.isoformat(),
            "blocked_until": until.isoformat(),
            "seconds": wait,
            "status": "TELEGRAM_FLOOD_WAIT",
        },
    )
    return until

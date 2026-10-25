"""Compare cached a_sfera ban to FRESH native Telegram rights, without writes.

Read-only production diagnostic for a single owner-reviewed message. The normal
publisher and its historic restriction memory are NEVER bypassed or modified.
An independent no-memory preflight is used solely to expose native evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from app.femdom_first_publish_preflight import verify_discussion_root
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT
from app.telegram_community_restrictions import telegram_community_restriction_memory
from app.telegram_opportunity_preflight import (
    TelegramOpportunityPreflightService,
    telegram_opportunity_preflight_service,
)

_TARGET = "https://t.me/a_sfera/2117"
_HANDLE = "a_sfera"


def _safe_restriction_summary(value: dict | None) -> dict:
    """Avoid leaking runtime metadata, account/session details or raw receipts."""
    row = value if isinstance(value, dict) else {}
    return {
        "present": bool(row),
        "reason": str(row.get("reason") or "")[:120] or None,
        "restriction_signal": str(row.get("restriction_signal") or "")[:120] or None,
        "source_action_id": str(row.get("source_action_id") or "")[:80] or None,
        "first_observed_at": str(row.get("first_observed_at") or "")[:60] or None,
        "last_observed_at": str(row.get("last_observed_at") or "")[:60] or None,
    }


async def audit(
    *,
    project_id: UUID = _PROJECT,
    product_id: UUID = _PRODUCT,
    restriction_reader=None,
    standard_checker=None,
    native_checker=None,
    discussion_checker=None,
) -> dict:
    if project_id != _PROJECT or product_id != _PRODUCT:
        raise ValueError("Outside approved FemDom a_sfera diagnostic scope")

    reader = restriction_reader or telegram_community_restriction_memory.get
    standard = standard_checker or telegram_opportunity_preflight_service
    # This instance is for INSPECTION only; it is never passed to an executor.
    # All real sends remain blocked by the unchanged persisted restriction gate.
    native = native_checker or TelegramOpportunityPreflightService(
        restriction_checker=lambda _project, _handle: None,
    )
    stored = _safe_restriction_summary(reader(project_id, _HANDLE))
    cached = await standard.check_comment_target(project_id, _TARGET)
    fresh = await native.check_comment_target(project_id, _TARGET)
    discussion = {"status": "NOT_CHECKED"}
    if fresh.status == "READY":
        verify = discussion_checker or verify_discussion_root
        discussion = await verify(project_id, _TARGET, native)
    conflict = (
        stored["present"]
        and cached.status == "NO_WRITE_ACCESS"
        and fresh.status == "READY"
    )
    return {
        "status": "ASFERA_READONLY_RECHECK_COMPLETE",
        "as_of": datetime.now(UTC).isoformat(),
        "target": _TARGET,
        "human_observation": "COMPOSER_VISIBLE_NOT_SEND_PROOF",
        "stored_restriction": stored,
        "cached_preflight": cached.public_dict(),
        "fresh_native_preflight": fresh.public_dict(),
        "native_discussion": discussion,
        "historical_ban_conflicts_with_fresh_read": bool(conflict),
        "fresh_route_verified": (
            fresh.status == "READY"
            and discussion.get("status") == "DISCUSSION_ROOT_VERIFIED"
        ),
        "restriction_memory_cleared": False,
        "publisher_authorization_changed": False,
        "publication_authorized": False,
        "send_allowed": False,
        "telegram_joins": 0,
        "telegram_sends": 0,
        "profile_mutations": 0,
        "approval_mutations": 0,
        "llm_calls": 0,
        "paid_search_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="a_sfera live read-only rights recheck")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--product-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(audit(
            project_id=args.project_id, product_id=args.product_id,
        ))
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "ASFERA_READONLY_RECHECK_FAILED",
            "reason_type": type(exc).__name__,
            "send_allowed": False, "telegram_joins": 0, "telegram_sends": 0,
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""One-off evidence-gated cleanup of stale a_sfera restriction; NO sends.

Only the exact restriction captured on 2026-10-10 may be removed, and only
after the connected account independently verifies current write rights and
an exact fresh native discussion root for a_sfera/2117. An unexpected newer
restriction must never be cleared. Publication still requires separate policy
and owner approval.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from app.femdom_asfera_live_access_recheck import audit
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT
from app.telegram_community_restrictions import telegram_community_restriction_memory
from app.telegram_opportunity_preflight import telegram_opportunity_preflight_service

_TARGET = "https://t.me/a_sfera/2117"
_HANDLE = "a_sfera"
_EXPECTED_RESTRICTION = {
    "reason": "ACCOUNT_BANNED_IN_COMMUNITY",
    "restriction_signal": "WRITE_RESTRICTED",
    "source_action_id": None,
    "first_observed_at": "2026-10-10T21:48:28.836970+00:00",
    "last_observed_at": "2026-10-10T21:58:25.723486+00:00",
}
_EXPECTED_LINKED_CHAT_ID = 1582097097


def _matches_observed_old_record(row: object) -> bool:
    return isinstance(row, dict) and all(
        row.get(key) == value for key, value in _EXPECTED_RESTRICTION.items()
    )


async def reconcile(
    *,
    project_id: UUID = _PROJECT,
    product_id: UUID = _PRODUCT,
    memory=None,
    audit_runner=None,
    post_checker=None,
) -> dict:
    if project_id != _PROJECT or product_id != _PRODUCT:
        raise ValueError("Outside pinned FemDom a_sfera recovery scope")

    restriction_memory = memory or telegram_community_restriction_memory
    old = restriction_memory.get(project_id, _HANDLE)
    base = {
        "target": _TARGET,
        "restriction_removed": False,
        "telegram_sends": 0,
        "telegram_joins": 0,
        "profile_mutations": 0,
        "approval_mutations": 0,
        "publication_authorized": False,
        "send_allowed": False,
        "no_commercial_policy_override": True,
    }
    if old is not None and not _matches_observed_old_record(old):
        return {**base, "status": "BLOCKED_RESTRICTION_CHANGED"}

    check = audit_runner or audit
    evidence = await check(project_id=project_id, product_id=product_id)
    fresh = evidence.get("fresh_native_preflight") or {}
    root = evidence.get("native_discussion") or {}
    if not (
        evidence.get("status") == "ASFERA_READONLY_RECHECK_COMPLETE"
        and fresh.get("status") == "READY"
        and fresh.get("handle") == _HANDLE
        and fresh.get("post_id") == 2117
        and fresh.get("linked_chat_id") == _EXPECTED_LINKED_CHAT_ID
        and root.get("status") == "DISCUSSION_ROOT_VERIFIED"
        and root.get("expected_post_id") == 2117
        and root.get("actual_post_id") == 2117
        and evidence.get("fresh_route_verified") is True
    ):
        return {
            **base, "status": "BLOCKED_FRESH_NATIVE_EVIDENCE_INSUFFICIENT",
            "fresh_status": fresh.get("status"),
            "discussion_status": root.get("status"),
        }

    if old is None:
        return {**base, "status": "ALREADY_CLEARED_CURRENT_NATIVE_ROUTE_READY"}
    if restriction_memory.get(project_id, _HANDLE) != old:
        return {**base, "status": "BLOCKED_RESTRICTION_CHANGED_DURING_CHECK"}

    # This is the ONLY write: remove one exact, expired project/handle
    # restriction record. Never join, send, approve, or modify a profile.
    restriction_memory.clear(project_id, _HANDLE)
    checker = post_checker or telegram_opportunity_preflight_service
    after = await checker.check_comment_target(project_id, _TARGET)
    return {
        **base,
        "status": (
            "STALE_RESTRICTION_CLEARED_CURRENT_NATIVE_READY"
            if after.status == "READY"
            else "STALE_RESTRICTION_CLEARED_BUT_PREFLIGHT_NOT_READY"
        ),
        "restriction_removed": True,
        "post_clear_preflight": after.public_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="One-off evidence-gated a_sfera cleanup")
    parser.add_argument("--project-id", required=True, type=UUID)
    parser.add_argument("--product-id", required=True, type=UUID)
    args = parser.parse_args()
    try:
        result = asyncio.run(reconcile(
            project_id=args.project_id,
            product_id=args.product_id,
        ))
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result["status"] in {
            "STALE_RESTRICTION_CLEARED_CURRENT_NATIVE_READY",
            "ALREADY_CLEARED_CURRENT_NATIVE_ROUTE_READY",
        } else 2
    except Exception as exc:
        print(json.dumps({
            "status": "ASFERA_STALE_RESTRICTION_RECONCILE_FAILED",
            "reason_type": type(exc).__name__,
            "telegram_sends": 0, "telegram_joins": 0, "send_allowed": False,
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

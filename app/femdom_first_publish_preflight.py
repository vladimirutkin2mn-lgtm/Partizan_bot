"""Read-only acceptance gate before the FIRST real FemDom Telegram publish.

Nothing in this module approves a DistributionAction, sends, joins, changes a
profile, or creates a draft. Human review of exact text and explicit "публикуй"
is a separate, mandatory execution boundary.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from telethon.tl.functions.messages import GetDiscussionMessageRequest

from app.customer_telegram_profile_setup import (
    TELEGRAM_PROFILE_SETUP_NAMESPACE,
    _setup_key,
)
from app.femdom_research_comment_preview import (
    _ALLOWED,
    _PRODUCT,
    _PROJECT,
    _REVIEW_NAMESPACE,
    _read_candidate,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import customer_telegram_client_publish_service
from app.telegram_opportunity_preflight import (
    TelegramOpportunityPreflightService,
    telegram_opportunity_preflight_service,
)

# Only three previously hand-reviewed channel comment targets and two
# standalone group roots. No global discovery or automated join operation.
_HANDLES = frozenset((
    "a_sfera", "play_hard_community", "bizarreclubmsk",
    "findom_gospoja0", "poiskrabovirabin",
))


async def verify_discussion_root(
    project_id: UUID, target_url: str, service: TelegramOpportunityPreflightService,
) -> dict:
    """Require exact latest post and a resolvable linked discussion root.

    Unlike checking whether the UI shows "comments", this verifies the route
    against the connected PUBLISHER account. No join or send methods used.
    """
    target = service.parse_comment_target(target_url)
    if target is None:
        return {"status": "INVALID_TARGET_URL"}
    client, error = service._client_for_project(
        project_id, target.handle, target.post_id, "CHANNEL_COMMENT"
    )
    if error is not None or client is None:
        return {"status": "ACCOUNT_SESSION_UNAVAILABLE"}
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {"status": "SESSION_NOT_AUTHORIZED"}
        channel = await client.get_entity(f"@{target.handle}")
        last = await client.get_messages(channel, limit=1)
        actual_id = int(getattr(last[0], "id", 0) or 0) if last else 0
        if actual_id != target.post_id:
            return {
                "status": "TARGET_NO_LONGER_LATEST",
                "expected_post_id": target.post_id,
                "actual_post_id": actual_id,
            }
        discussion = await client(
            GetDiscussionMessageRequest(peer=channel, msg_id=target.post_id)
        )
        messages = list(getattr(discussion, "messages", []) or [])
        chats = list(getattr(discussion, "chats", []) or [])
        channel_ids = {
            int(getattr(chat, "id", 0) or 0)
            for chat in chats if int(getattr(chat, "id", 0) or 0) > 0
        }
        root_found = any(
            int(getattr(getattr(item, "peer_id", None), "channel_id", 0) or 0)
            in channel_ids
            for item in messages
        )
        return {
            "status": "DISCUSSION_ROOT_VERIFIED" if root_found
            else "DISCUSSION_ROOT_NOT_VERIFIED",
            "expected_post_id": target.post_id,
            "actual_post_id": actual_id,
        }
    except Exception as exc:
        return {"status": "DISCUSSION_ROUTE_FAILED",
                "reason": type(exc).__name__}
    finally:
        await service._disconnect_safely(client)


async def check_profile(
    store: RuntimeStateStore, project_id: UUID,
) -> dict:
    """Never include bio, username, session or private attribution token."""
    record = store.get(
        TELEGRAM_PROFILE_SETUP_NAMESPACE, _setup_key(project_id)
    )
    verified = (
        isinstance(record, dict) and record.get("status") == "VERIFIED"
    )
    try:
        profile = await customer_telegram_client_publish_service.profile_internal(
            project_id
        )
    except Exception as exc:
        return {
            "status": "PROFILE_READ_FAILED",
            "reason": type(exc).__name__,
            "avatar_present": False,
            "tracking_profile_route_verified": False,
        }
    known_route = (
        str(record.get("profile_route_url") or "")
        if isinstance(record, dict) else ""
    )
    route_verified = bool(
        verified and known_route.startswith("https://")
        and known_route in (profile.about or "")
    )
    return {
        "status": "PROFILE_ROUTE_VERIFIED" if route_verified
        else "PROFILE_ATTRIBUTION_UNVERIFIED",
        "avatar_present": profile.avatar is not None,
        "tracking_profile_route_verified": route_verified,
        "destination_fallback_configured": bool(
            record.get("fallback_url") if isinstance(record, dict) else False
        ),
    }


async def run(
    store: RuntimeStateStore, *, project_id: UUID, handle: str,
    preflight: TelegramOpportunityPreflightService | None = None,
    profile_checker=None, discussion_checker=None,
    now: datetime | None = None,
) -> dict:
    handle = handle.lstrip("@").casefold()
    if project_id != _PROJECT or handle not in _HANDLES or handle not in _ALLOWED:
        raise ValueError("Outside the bounded FemDom first-publish preflight scope")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)

    response = {
        "handle": handle, "project_id": str(project_id),
        "status": "BLOCKED", "published": False, "joined": False,
        "profile_mutated": False, "distribution_action_created": False,
        "customer_approval_recorded": False, "send_allowed": False,
        "llm_calls": 0, "paid_search_calls": 0,
    }

    try:
        candidate, project = _read_candidate(store, handle, now=current)
    except ValueError as exc:
        return {**response, "reason": str(exc)[:120]}
    mode = candidate["surface"]
    url = candidate["target_url"]
    response.update({"target_url": url, "action_target_mode": mode,
                     "target_post_id": candidate["post_id"]})

    publisher = (project.get("channel_publisher_modes") or {}).get("TELEGRAM")
    if publisher != "CLIENT_OWNED":
        return {**response, "reason": "CUSTOMER_PUBLISHER_MODE_NOT_ENABLED"}
    draft_key = f"{project_id}:{handle}:{candidate['post_id']}"
    draft = store.get(_REVIEW_NAMESPACE, draft_key)
    if not isinstance(draft, dict) or draft.get("status") != (
        "AWAITING_HUMAN_CONTEXT_AND_POLICY_REVIEW"
    ):
        return {**response, "reason": "CURRENT_REVIEW_DRAFT_MISSING"}
    if (
        draft.get("target_url") != url
        or draft.get("surface") != mode
        or int(draft.get("post_id") or 0) != candidate["post_id"]
        or draft.get("audience_status") != "DIRECT_CONFIRMED"
        or draft.get("publisher_permission_verified") is not False
        or draft.get("published") is not False
        or not str(draft.get("comment") or "").strip()
    ):
        return {**response, "reason": "CURRENT_DRAFT_TARGET_OR_POLICY_MISMATCH"}

    comment = str(draft["comment"]).strip()
    response["draft_sha256"] = hashlib.sha256(
        comment.encode("utf-8")
    ).hexdigest()
    response["draft_text"] = comment  # only the previously stored review draft
    response["prompt_version"] = draft.get("prompt_version")
    checker = preflight or telegram_opportunity_preflight_service
    access = await checker.check_target(project_id, url, mode)
    response["account_preflight"] = access.public_dict()
    if not access.execution_ready:
        return {**response, "reason": "ACCOUNT_PREFLIGHT_NOT_READY"}
    if mode == "CHANNEL_COMMENT":
        verify = discussion_checker or verify_discussion_root
        discussion = await verify(project_id, url, checker)
        response["discussion_preflight"] = discussion
        if discussion.get("status") != "DISCUSSION_ROOT_VERIFIED":
            return {**response, "reason": "DISCUSSION_ROUTE_NOT_VERIFIED"}

    profile = await (
        profile_checker(store, project_id) if profile_checker
        else check_profile(store, project_id)
    )
    response["profile_preflight"] = profile
    if not profile.get("tracking_profile_route_verified"):
        return {**response, "reason": "ATTRIBUTION_PROFILE_ROUTE_UNVERIFIED"}

    # READY_FOR_HUMAN_REVIEW is deliberately NOT an execution permission.
    # Commercial rules and exact-text approval are not inferable from APIs.
    return {
        **response, "status": "TECHNICALLY_READY_FOR_HUMAN_REVIEW",
        "reason": "COMMUNITY_RULES_AND_EXACT_TEXT_APPROVAL_REQUIRED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero-send Telegram first-publish preflight")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--handle", required=True, choices=sorted(_HANDLES))
    args = parser.parse_args()
    if args.product_id != _PRODUCT or args.project_id != _PROJECT:
        raise ValueError("First-publish target does not belong to FemDom project")
    try:
        result = asyncio.run(run(
            get_runtime_store(), project_id=args.project_id, handle=args.handle
        ))
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "BLOCKED", "reason": type(exc).__name__,
            "published": False, "send_allowed": False,
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

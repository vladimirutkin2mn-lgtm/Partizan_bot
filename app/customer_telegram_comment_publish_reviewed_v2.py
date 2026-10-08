from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from app import customer_telegram_comment_publish_reviewed as v1
from app.runtime_store import get_runtime_store
from app.telegram_comment_access import ensure_comment_access


async def _prepare_linked_discussion_access(args, config: dict) -> dict:
    if config.get("linked_discussion_join_authorized") is not True:
        return {
            "status": "JOIN_NOT_AUTHORIZED",
            "joined": False,
            "linked_chat_id": None,
        }

    client = v1._telegram_client(args.project_id)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Telegram session is not authorised")
        entity = await client.get_entity(f"@{v1.HANDLE}")
        if int(getattr(entity, "id", 0) or 0) != v1.ENTITY_ID:
            raise ValueError("Telegram target entity no longer matches the reviewed channel")
        access = await ensure_comment_access(client, entity, allow_join=True)
        return access.public_dict()
    finally:
        await client.disconnect()


async def run(args) -> dict:
    if args.confirm != v1.CONFIRMATION:
        raise ValueError(f"Exact confirmation is required: {v1.CONFIRMATION}")

    project, _ = v1._validate_target(args)
    config = v1._load_config(args.review_config)
    modes = project.get("channel_publisher_modes")
    telegram_mode = str(modes.get("TELEGRAM") or "") if isinstance(modes, dict) else ""
    if telegram_mode != "CLIENT_OWNED":
        raise ValueError("FemDom Telegram publisher mode is no longer CLIENT_OWNED")

    store = get_runtime_store()
    key = v1._marker_key(args.project_id)
    existing = store.get(v1.MARKER_NAMESPACE, key)
    if existing is not None:
        existing_status = str(existing.get("status") or "")
        # Let v1 preserve its reconciliation/idempotency behavior before any new side effect.
        if existing_status in {
            "PUBLISHED",
            "PUBLISHED_RECONCILED",
            "FAILED_UNRESOLVED",
        }:
            return await v1.run(args)
        if existing_status not in {"RECONCILED_NOT_FOUND"}:
            return {
                **existing,
                "comment_published": False,
                "retry_allowed": False,
            }

    v1.customer_telegram_client_publish_service._require_ready()
    verified = await v1._verify_target_post(args.project_id, config)
    if not verified["discussion_available"]:
        return await v1.run(args)

    access = await _prepare_linked_discussion_access(args, config)
    if access.get("status") != "READY":
        blocked = {
            "status": f"COMMENT_ACCESS_{access.get('status') or 'BLOCKED'}",
            "operation_id": v1.OPERATION_ID,
            "project_id": str(args.project_id),
            "target_url": v1.TARGET_URL,
            "target_handle": v1.HANDLE,
            "target_post_id": v1.POST_ID,
            "target_entity_id": verified["entity_id"],
            "target_replies_count_before": verified["replies_count"],
            "content_sha256": v1.CONTENT_SHA256,
            "access": access,
            "checked_at": datetime.now(UTC).isoformat(),
            "comment_published": False,
            "retry_allowed": False,
            "profile_mutated": False,
            "story_mutated": False,
            "reply_published": False,
            "message_published": False,
        }
        store.put(v1.MARKER_NAMESPACE, key, blocked)
        return blocked

    result = await v1.run(args)
    # Surface the prerequisite side effect in the receipt without changing v1's publish semantics.
    if bool(access.get("joined")) and result.get("status") in {
        "PUBLISHED",
        "ALREADY_PUBLISHED",
    }:
        result = {
            **result,
            "linked_discussion_joined": True,
            "linked_discussion_chat_id": access.get("linked_chat_id"),
        }
        store.put(v1.MARKER_NAMESPACE, key, result)
    return result


def main() -> int:
    args = v1.build_parser().parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        error = {"error_type": type(exc).__name__, "error": str(exc)[:2000]}
        print(json.dumps(error, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

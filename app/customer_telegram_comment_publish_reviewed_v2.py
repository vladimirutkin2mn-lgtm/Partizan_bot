from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from telethon.tl.functions.messages import GetDiscussionMessageRequest

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


def _peer_channel_id(message) -> int:
    peer = getattr(message, "peer_id", None)
    return int(getattr(peer, "channel_id", 0) or 0)


def _reply_ids(message) -> set[int]:
    reply = getattr(message, "reply_to", None)
    values = {
        int(getattr(reply, "reply_to_msg_id", 0) or 0),
        int(getattr(reply, "top_msg_id", 0) or 0),
    }
    values.discard(0)
    return values


async def _reconcile_linked_discussion(args, config: dict) -> dict:
    """Find the approved comment in the linked group without sending anything."""
    client = v1._telegram_client(args.project_id)
    expected_text = str(config["content_text"]).strip()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Telegram session is not authorised")
        me = await client.get_me()
        if me is None or not getattr(me, "id", None):
            raise ValueError("Telegram identity is not available")

        channel = await client.get_entity(f"@{v1.HANDLE}")
        if int(getattr(channel, "id", 0) or 0) != v1.ENTITY_ID:
            raise ValueError("Telegram target entity no longer matches the reviewed channel")

        access = await ensure_comment_access(client, channel, allow_join=False)
        if access.status != "READY" or access.linked_chat_id is None:
            return {
                "status": "LINKED_RECONCILIATION_BLOCKED",
                "reason": access.status,
                "found": False,
                "comments_checked": 0,
                "retry_allowed": access.status == "JOIN_REQUIRED",
                "linked_chat_id": access.linked_chat_id,
            }

        discussion = await client(
            GetDiscussionMessageRequest(peer=channel, msg_id=v1.POST_ID)
        )
        linked_chat_id = int(access.linked_chat_id)
        linked_entity = next(
            (
                chat
                for chat in (getattr(discussion, "chats", None) or [])
                if int(getattr(chat, "id", 0) or 0) == linked_chat_id
            ),
            None,
        )
        if linked_entity is None:
            linked_entity = await client.get_entity(linked_chat_id)

        root = next(
            (
                message
                for message in (getattr(discussion, "messages", None) or [])
                if _peer_channel_id(message) == linked_chat_id
            ),
            None,
        )
        if root is None or not getattr(root, "id", None):
            return {
                "status": "LINKED_RECONCILIATION_BLOCKED",
                "reason": "DISCUSSION_ROOT_NOT_FOUND",
                "found": False,
                "comments_checked": 0,
                "retry_allowed": False,
                "linked_chat_id": linked_chat_id,
            }

        root_id = int(root.id)
        checked = 0
        seen: set[int] = set()

        async for item in client.iter_messages(
            linked_entity,
            reply_to=root_id,
            limit=v1.RECONCILE_LIMIT,
        ):
            message_id = int(getattr(item, "id", 0) or 0)
            if message_id:
                seen.add(message_id)
            checked += 1
            text = str(getattr(item, "message", "") or "").strip()
            sender_id = int(getattr(item, "sender_id", 0) or 0)
            if sender_id == int(me.id) and text == expected_text:
                return {
                    "status": "PUBLISHED_RECONCILED",
                    "found": True,
                    "comments_checked": checked,
                    "retry_allowed": False,
                    "remote_peer_id": linked_chat_id,
                    "remote_message_id": message_id,
                    "discussion_root_id": root_id,
                    "executed_url": f"{v1.TARGET_URL}?comment={message_id}",
                }

        # Telethon can fail to materialize a freshly sent comment even when Telegram
        # returned an update. Scan recent linked-group messages as a second, read-only check.
        async for item in client.iter_messages(linked_entity, limit=v1.RECONCILE_LIMIT):
            message_id = int(getattr(item, "id", 0) or 0)
            if message_id in seen:
                continue
            checked += 1
            text = str(getattr(item, "message", "") or "").strip()
            sender_id = int(getattr(item, "sender_id", 0) or 0)
            if (
                sender_id == int(me.id)
                and text == expected_text
                and root_id in _reply_ids(item)
            ):
                return {
                    "status": "PUBLISHED_RECONCILED",
                    "found": True,
                    "comments_checked": checked,
                    "retry_allowed": False,
                    "remote_peer_id": linked_chat_id,
                    "remote_message_id": message_id,
                    "discussion_root_id": root_id,
                    "executed_url": f"{v1.TARGET_URL}?comment={message_id}",
                }

        return {
            "status": "LINKED_RECONCILED_NOT_FOUND",
            "found": False,
            "comments_checked": checked,
            "retry_allowed": True,
            "linked_chat_id": linked_chat_id,
            "discussion_root_id": root_id,
        }
    except ValueError:
        raise
    except Exception as exc:
        return {
            "status": "LINKED_RECONCILIATION_BLOCKED",
            "reason": type(exc).__name__,
            "found": False,
            "comments_checked": 0,
            "retry_allowed": False,
        }
    finally:
        await client.disconnect()


def _published_receipt(existing: dict | None, reconciliation: dict) -> dict:
    return {
        **(existing or {}),
        **reconciliation,
        "status": "PUBLISHED_RECONCILED",
        "reconciled_at": datetime.now(UTC).isoformat(),
        "comment_published": True,
        "profile_mutated": False,
        "story_mutated": False,
        "reply_published": False,
        "message_published": False,
    }


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
        if existing_status in {"PUBLISHED", "PUBLISHED_RECONCILED"}:
            return await v1.run(args)
        if existing_status in {"FAILED_UNRESOLVED", "RECONCILED_NOT_FOUND"}:
            reconciliation = await _reconcile_linked_discussion(args, config)
            if reconciliation.get("found"):
                receipt = _published_receipt(existing, reconciliation)
                store.put(v1.MARKER_NAMESPACE, key, receipt)
                return receipt
            if reconciliation.get("retry_allowed") is not True:
                blocked = {
                    **existing,
                    **reconciliation,
                    "comment_published": False,
                    "retry_allowed": False,
                }
                store.put(v1.MARKER_NAMESPACE, key, blocked)
                return blocked
            # v1 only retries from RECONCILED_NOT_FOUND. Persist that state after the
            # stronger linked-group reconciliation proves the exact comment is absent.
            retry_state = {
                **existing,
                **reconciliation,
                "status": "RECONCILED_NOT_FOUND",
                "comment_published": False,
                "retry_allowed": True,
                "reconciled_at": datetime.now(UTC).isoformat(),
            }
            store.put(v1.MARKER_NAMESPACE, key, retry_state)
            existing = retry_state
        elif existing_status not in {"RECONCILED_NOT_FOUND"}:
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

    try:
        result = await v1.run(args)
    except Exception:
        # Telegram may accept the comment while Telethon fails to map the returned
        # MessageEmpty update. Reconcile the linked group before surfacing failure.
        reconciliation = await _reconcile_linked_discussion(args, config)
        if reconciliation.get("found"):
            current = store.get(v1.MARKER_NAMESPACE, key)
            receipt = _published_receipt(current, reconciliation)
            if bool(access.get("joined")):
                receipt["linked_discussion_joined"] = True
                receipt["linked_discussion_chat_id"] = access.get("linked_chat_id")
            store.put(v1.MARKER_NAMESPACE, key, receipt)
            return receipt
        raise

    if bool(access.get("joined")) and result.get("status") in {
        "PUBLISHED",
        "ALREADY_PUBLISHED",
        "PUBLISHED_RECONCILED",
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

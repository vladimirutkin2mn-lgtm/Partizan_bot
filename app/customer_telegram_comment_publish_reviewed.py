from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import get_settings
from app.customer_telegram_profile_apply_reviewed import _validate_target
from app.distribution_types import DistributionActionType
from app.runtime_store import get_runtime_store
from app.telegram_client_publishing import TelegramPublishTarget, customer_telegram_client_publish_service

CONFIRMATION = "PUBLISH_FEMDOM_ASFERA_2100_APPROVED_V1"
MARKER_NAMESPACE = "reviewed_customer_telegram_comment_publish"
OPERATION_ID = "femdom-asfera-2100-approved-comment-2026-10-08-v1"
HANDLE = "a_sfera"
ENTITY_ID = 1642699396
POST_ID = 2100
TARGET_URL = "https://t.me/a_sfera/2100"
CONTENT_SHA256 = "7ccc37f8c9cbc40503546bb7e1c7df742fa82ded45b19a85d2fe774ea0dd96a2"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish the one exact user-approved FemDom Telegram comment only.")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--review-config", type=Path, required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def _load_config(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported reviewed Telegram comment config")
    checks = {
        "operation_id": OPERATION_ID,
        "authorization_scope": "COMMENT_ONLY",
        "authorization_phrase_received": "публикуй",
        "expected_handle": HANDLE,
        "target_url": TARGET_URL,
    }
    for key, expected in checks.items():
        if str(payload.get(key) or "").strip().lstrip("@") != expected:
            raise ValueError(f"Reviewed Telegram comment field changed: {key}")
    if payload.get("community_publish_authorized") is not True:
        raise ValueError("Reviewed Telegram comment is not authorized")
    for key in ("profile_mutation_authorized", "story_mutation_authorized", "reply_publish_authorized", "message_publish_authorized"):
        if payload.get(key) is not False:
            raise ValueError(f"Reviewed Telegram comment must keep {key}=false")
    if int(payload.get("expected_telegram_entity_id") or 0) != ENTITY_ID or int(payload.get("target_post_id") or 0) != POST_ID:
        raise ValueError("Reviewed Telegram target changed")
    text = str(payload.get("content_text") or "").strip()
    actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if actual_sha != CONTENT_SHA256 or str(payload.get("content_sha256") or "").casefold() != CONTENT_SHA256:
        raise ValueError("Reviewed Telegram comment text changed")
    if not str(payload.get("target_context_marker") or "").strip():
        raise ValueError("Reviewed Telegram target context marker is empty")
    return payload


def _marker_key(project_id: UUID) -> str:
    return f"{project_id}:{OPERATION_ID}"


async def _verify_target_post(project_id: UUID, config: dict) -> dict:
    session = customer_telegram_client_publish_service._active_session_internal(project_id)
    settings = get_settings()
    if settings.telegram_client_publish_api_id is None or settings.telegram_client_publish_api_hash is None:
        raise ValueError("Telegram client publishing credentials are not configured")
    client = TelegramClient(StringSession(session), settings.telegram_client_publish_api_id, settings.telegram_client_publish_api_hash.get_secret_value())
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Telegram session is not authorised")
        entity = await client.get_entity(f"@{HANDLE}")
        if int(getattr(entity, "id", 0) or 0) != ENTITY_ID:
            raise ValueError("Telegram target entity no longer matches the reviewed channel")
        message = await client.get_messages(entity, ids=POST_ID)
        if message is None or int(getattr(message, "id", 0) or 0) != POST_ID:
            raise ValueError("Reviewed Telegram target post is no longer available")
        marker = str(config["target_context_marker"])
        if marker.casefold() not in str(getattr(message, "message", "") or "").casefold():
            raise ValueError("Telegram target post no longer matches the reviewed context")
        return {"entity_id": ENTITY_ID, "post_id": POST_ID}
    finally:
        await client.disconnect()


async def run(args: argparse.Namespace) -> dict:
    if args.confirm != CONFIRMATION:
        raise ValueError(f"Exact confirmation is required: {CONFIRMATION}")
    project, _ = _validate_target(args)
    config = _load_config(args.review_config)
    modes = project.get("channel_publisher_modes")
    if not isinstance(modes, dict) or str(modes.get("TELEGRAM") or "") != "CLIENT_OWNED":
        raise ValueError("FemDom Telegram publisher mode is no longer CLIENT_OWNED")

    store = get_runtime_store()
    key = _marker_key(args.project_id)
    existing = store.get(MARKER_NAMESPACE, key)
    if existing is not None:
        if str(existing.get("status") or "") == "PUBLISHED":
            return {**existing, "status": "ALREADY_PUBLISHED"}
        raise ValueError("Previous reviewed Telegram comment outcome is unresolved; manual reconciliation is required")

    customer_telegram_client_publish_service._require_ready()
    verified = await _verify_target_post(args.project_id, config)
    session = customer_telegram_client_publish_service._active_session_internal(args.project_id)
    text = str(config["content_text"]).strip()
    target = TelegramPublishTarget(username=HANDLE, reply_to_message_id=POST_ID)
    fingerprint = customer_telegram_client_publish_service._fingerprint(target, text)
    customer_telegram_client_publish_service._enforce_publish_guard(args.project_id, fingerprint)
    started = {
        "status": "IN_PROGRESS", "operation_id": OPERATION_ID, "project_id": str(args.project_id),
        "target_url": TARGET_URL, "target_handle": HANDLE, "target_post_id": POST_ID,
        "target_entity_id": verified["entity_id"], "content_sha256": CONTENT_SHA256,
        "started_at": datetime.now(UTC).isoformat(), "profile_mutated": False, "story_mutated": False,
        "comment_published": False, "reply_published": False, "message_published": False,
    }
    store.put(MARKER_NAMESPACE, key, started)
    try:
        result = await customer_telegram_client_publish_service._transport.publish(
            session=session, target=target, action_type=DistributionActionType.COMMENT, text=text
        )
    except Exception as exc:
        store.put(MARKER_NAMESPACE, key, {**started, "status": "FAILED_UNRESOLVED", "error_type": type(exc).__name__, "failed_at": datetime.now(UTC).isoformat()})
        raise

    customer_telegram_client_publish_service._record_publish(args.project_id, fingerprint)
    completed = {
        **started, "status": "PUBLISHED", "comment_published": True,
        "remote_peer_id": result.peer_id, "remote_message_id": result.message_id,
        "published_at": result.published_at.isoformat(), "executed_url": str(result.executed_url),
    }
    store.put(MARKER_NAMESPACE, key, completed)
    return completed


def main() -> int:
    args = build_parser().parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error": str(exc)[:2000]}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

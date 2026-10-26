"""Single authorized join of the BizarreClubMSK linked discussion (NO SEND).

Explicit owner approval in the active conversation on 2026-10-25:
"Да. Вступай" in response to joining BizarreClubMSK's linked discussion.

This command cannot join another group, draft, send, edit profile or grant
marketing authorization. Its workflow runs once after the matching deploy.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from telethon.tl.functions.channels import JoinChannelRequest

from app.distribution_schemas import AudienceDistributionMapView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    customer_telegram_client_publish_service,
)
from app.telegram_comment_access import preflight_comment_access

HANDLE = "bizarreclubmsk"
EXPECTED_LINKED_CHAT_ID = 1506400194
OPERATION_KEY = "femdom-bizarreclub-linked-discussion-join-20261025-v1"
_RESULT_NAMESPACE = "femdom_bizarreclub_authorized_join_once"


def _validate_scope(store: RuntimeStateStore) -> int:
    project = store.get("customer_acquisition_projects", str(_PROJECT))
    if not isinstance(project, dict) or project.get("deleted_at"):
        raise ValueError("PROJECT_NOT_ACTIVE")
    if str(project.get("product_id") or "") != str(_PRODUCT):
        raise ValueError("PROJECT_PRODUCT_MISMATCH")
    publisher_mode = (project.get("channel_publisher_modes") or {}).get("TELEGRAM")
    if publisher_mode != "CLIENT_OWNED":
        raise ValueError("CUSTOMER_OWNED_PUBLISHER_REQUIRED")
    conn = store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(_PROJECT))
    if not isinstance(conn, dict) or str(conn.get("status") or "").upper() != "ACTIVE":
        raise ValueError("ACTIVE_TELEGRAM_CONNECTION_REQUIRED")

    raw = store.get("audience_distribution_map", str(_PRODUCT))
    if not isinstance(raw, dict):
        raise ValueError("AUDIENCE_MAP_UNAVAILABLE")
    mapping = AudienceDistributionMapView.model_validate(raw)
    matches = [
        item for item in mapping.opportunities
        if item.platform == DistributionPlatform.TELEGRAM
        and item.kind == OpportunityKind.CHANNEL
        and str(item.metadata.get("handle") or "").casefold().lstrip("@") == HANDLE
    ]
    if len(matches) != 1:
        raise ValueError("REVIEWED_COMMUNITY_NOT_UNIQUE")
    meta = matches[0].metadata
    if meta.get("research_audience_status") != "DIRECT_CONFIRMED":
        raise ValueError("COMMUNITY_AUDIENCE_NOT_CONFIRMED")
    if meta.get("native_research_status") != "VERIFIED":
        raise ValueError("COMMUNITY_NATIVE_ID_NOT_VERIFIED")
    expected_channel_id = int(meta.get("telegram_entity_id") or 0)
    if expected_channel_id <= 0:
        raise ValueError("CHANNEL_ENTITY_ID_MISSING")
    return expected_channel_id


async def join_once(store: RuntimeStateStore, *, client_factory=None) -> dict:
    base = {
        "operation_id": OPERATION_KEY, "channel_handle": HANDLE,
        "linked_chat_id": EXPECTED_LINKED_CHAT_ID,
        "messages_sent": 0, "profile_changes": 0,
        "publisher_approval_granted": False,
    }
    try:
        expected_channel_id = _validate_scope(store)
    except Exception as exc:
        return {**base, "status": "BLOCKED", "reason": str(exc)[:100], "joined": False}
    existing = store.get(_RESULT_NAMESPACE, OPERATION_KEY)
    if isinstance(existing, dict) and existing.get("status") in {
        "JOINED_AND_READY", "ALREADY_MEMBER", "JOINED_BUT_RESTRICTED",
    }:
        return {**existing, "idempotent_replay": True}

    try:
        client = (
            client_factory()
            if client_factory is not None
            else _connected_client()
        )
    except Exception as exc:
        return {**base, "status": "BLOCKED",
                "reason": type(exc).__name__, "joined": False}
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {**base, "status": "BLOCKED",
                    "reason": "SESSION_NOT_AUTHORIZED", "joined": False}
        channel = await client.get_entity(f"@{HANDLE}")
        if int(getattr(channel, "id", 0) or 0) != expected_channel_id:
            return {**base, "status": "BLOCKED",
                    "reason": "CHANNEL_ID_CHANGED", "joined": False}
        # First read from native Telegram. No guessed discussion group IDs.
        access = await preflight_comment_access(client, channel)
        if access.linked_chat_id != EXPECTED_LINKED_CHAT_ID:
            return {**base, "status": "BLOCKED",
                    "reason": "LINKED_CHAT_ID_CHANGED", "joined": False}
        if access.status == "READY":
            outcome = {**base, "status": "ALREADY_MEMBER", "joined": False,
                       "write_access": "READY"}
        elif access.status != "JOIN_REQUIRED":
            return {**base, "status": "BLOCKED", "reason": access.status,
                    "joined": False}
        else:
            linked_entity = access._linked_entity
            if (
                linked_entity is None
                or int(getattr(linked_entity, "id", 0) or 0)
                != EXPECTED_LINKED_CHAT_ID
            ):
                return {**base, "status": "BLOCKED",
                        "reason": "LINKED_ENTITY_ID_MISMATCH", "joined": False}
            # The only mutating Telegram API call in this entire operation.
            try:
                await client(JoinChannelRequest(linked_entity))
            except Exception as exc:
                # Never retry an uncertain join. Verify state read-only first.
                checked = await preflight_comment_access(client, channel)
                if (
                    checked.linked_chat_id == EXPECTED_LINKED_CHAT_ID
                    and checked.status == "READY"
                ):
                    outcome = {**base, "status": "JOINED_AND_READY",
                               "joined": True, "write_access": "READY"}
                else:
                    return {**base, "status": "JOIN_FAILED",
                            "reason": type(exc).__name__, "joined": False}
            else:
                checked = await preflight_comment_access(client, channel)
                if checked.linked_chat_id != EXPECTED_LINKED_CHAT_ID:
                    return {**base, "status": "JOIN_OUTCOME_UNVERIFIED",
                            "reason": "LINKED_CHAT_CHANGED_AFTER_JOIN",
                            "joined": True}
                outcome = {
                    **base, "status": (
                        "JOINED_AND_READY" if checked.status == "READY"
                        else "JOINED_BUT_RESTRICTED"
                    ),
                    "joined": True, "write_access": checked.status,
                }
        outcome["checked_at"] = datetime.now(UTC).isoformat()
        store.put(_RESULT_NAMESPACE, OPERATION_KEY, outcome)
        return outcome
    except Exception as exc:
        return {**base, "status": "BLOCKED",
                "reason": type(exc).__name__, "joined": False}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def _connected_client():
    # Reuse the securely stored customer-owned session; never print it.
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    from app.config import get_settings

    session = customer_telegram_client_publish_service._active_session_internal(_PROJECT)
    cfg = get_settings()
    api_id, api_hash = (
        cfg.telegram_client_publish_api_id,
        cfg.telegram_client_publish_api_hash,
    )
    if api_id is None or api_hash is None:
        raise ValueError("TELEGRAM_PUBLISHER_CREDENTIALS_MISSING")
    return TelegramClient(StringSession(session), api_id, api_hash.get_secret_value())


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Authorized single BizarreClub linked join")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--expected-linked-chat-id", type=int, required=True)
    args = parser.parse_args()
    if (
        args.project_id != _PROJECT
        or args.product_id != _PRODUCT
        or args.expected_linked_chat_id != EXPECTED_LINKED_CHAT_ID
    ):
        raise ValueError("AUTHORIZED_JOIN_SCOPE_MISMATCH")
    result = asyncio.run(join_once(get_runtime_store()))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {
        "JOINED_AND_READY", "ALREADY_MEMBER", "JOINED_BUT_RESTRICTED",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())

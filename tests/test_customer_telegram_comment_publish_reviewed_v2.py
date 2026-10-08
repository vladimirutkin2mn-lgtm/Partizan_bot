from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import customer_telegram_comment_publish_reviewed as v1
from app import customer_telegram_comment_publish_reviewed_v2 as v2


class FakeStore:
    def __init__(self, initial: dict | None = None) -> None:
        self.value = initial

    def get(self, namespace: str, key: str) -> dict | None:
        del namespace, key
        return self.value

    def put(self, namespace: str, key: str, value: dict) -> None:
        del namespace, key
        self.value = dict(value)


def _args():
    return SimpleNamespace(
        project_id=uuid4(),
        confirm=v1.CONFIRMATION,
        review_config=SimpleNamespace(),
    )


def _install_common(monkeypatch, store: FakeStore) -> None:
    monkeypatch.setattr(v2, "get_runtime_store", lambda: store)
    monkeypatch.setattr(
        v1,
        "_validate_target",
        lambda args: ({"channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"}}, None),
    )
    monkeypatch.setattr(
        v1,
        "_load_config",
        lambda path: {
            "content_text": "approved comment",
            "linked_discussion_join_authorized": True,
        },
    )


@pytest.mark.asyncio
async def test_existing_ambiguous_send_is_reconciled_before_retry(monkeypatch):
    store = FakeStore({"status": "FAILED_UNRESOLVED", "comment_published": False})
    _install_common(monkeypatch, store)

    async def reconcile(args, config):
        del args, config
        return {
            "status": "PUBLISHED_RECONCILED",
            "found": True,
            "remote_message_id": 37243,
            "retry_allowed": False,
        }

    async def forbidden_run(args):
        del args
        raise AssertionError("must not publish when reconciliation already found the comment")

    monkeypatch.setattr(v2, "_reconcile_linked_discussion", reconcile)
    monkeypatch.setattr(v1, "run", forbidden_run)

    result = await v2.run(_args())

    assert result["status"] == "PUBLISHED_RECONCILED"
    assert result["comment_published"] is True
    assert result["remote_message_id"] == 37243


@pytest.mark.asyncio
async def test_ambiguous_publish_error_is_reconciled_immediately(monkeypatch):
    store = FakeStore({"status": "RECONCILED_NOT_FOUND", "comment_published": False})
    _install_common(monkeypatch, store)
    monkeypatch.setattr(
        v1.customer_telegram_client_publish_service,
        "_require_ready",
        lambda: None,
    )

    async def verify(project_id, config):
        del project_id, config
        return {"discussion_available": True, "entity_id": v1.ENTITY_ID, "replies_count": 0}

    async def access(args, config):
        del args, config
        return {"status": "READY", "joined": False, "linked_chat_id": 1582097097}

    reconciliations = [
        {
            "status": "LINKED_RECONCILED_NOT_FOUND",
            "found": False,
            "retry_allowed": True,
            "comments_checked": 0,
        },
        {
            "status": "PUBLISHED_RECONCILED",
            "found": True,
            "retry_allowed": False,
            "comments_checked": 1,
            "remote_peer_id": 1582097097,
            "remote_message_id": 37243,
        },
    ]

    async def reconcile(args, config):
        del args, config
        return reconciliations.pop(0)

    async def ambiguous_run(args):
        del args
        raise RuntimeError("transport could not map Telegram update")

    monkeypatch.setattr(v1, "_verify_target_post", verify)
    monkeypatch.setattr(v2, "_prepare_linked_discussion_access", access)
    monkeypatch.setattr(v2, "_reconcile_linked_discussion", reconcile)
    monkeypatch.setattr(v1, "run", ambiguous_run)

    result = await v2.run(_args())

    assert result["status"] == "PUBLISHED_RECONCILED"
    assert result["comment_published"] is True
    assert result["remote_message_id"] == 37243
    assert reconciliations == []


@pytest.mark.asyncio
async def test_blocked_reconciliation_never_retries_publish(monkeypatch):
    store = FakeStore({"status": "FAILED_UNRESOLVED", "comment_published": False})
    _install_common(monkeypatch, store)

    async def reconcile(args, config):
        del args, config
        return {
            "status": "LINKED_RECONCILIATION_BLOCKED",
            "reason": "PRECHECK_FAILED",
            "found": False,
            "retry_allowed": False,
        }

    async def forbidden_run(args):
        del args
        raise AssertionError("must not publish when duplicate safety cannot reconcile")

    monkeypatch.setattr(v2, "_reconcile_linked_discussion", reconcile)
    monkeypatch.setattr(v1, "run", forbidden_run)

    result = await v2.run(_args())

    assert result["status"] == "LINKED_RECONCILIATION_BLOCKED"
    assert result["comment_published"] is False
    assert result["retry_allowed"] is False

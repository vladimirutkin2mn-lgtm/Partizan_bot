from types import SimpleNamespace
from uuid import UUID

import pytest

import app.tiktok_direct_post_reconciliation as reconciliation_module
from app.customer_execution_boundary import customer_execution_request_scope
from app.tiktok_direct_post_reconciliation import TikTokDirectPostReconciliationService

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
WRONG_REQUEST_ID = UUID("22222222-2222-4222-8222-222222222222")
ACTION_ID = UUID("66666666-6666-4666-8666-666666666666")


class _ExecutionLookup:
    def get_action(self, action_id):
        assert action_id == ACTION_ID
        return SimpleNamespace(
            operational_metadata={"customer_execution_request_id": str(REQUEST_ID)}
        )


class _DirectPostProbe:
    def __init__(self) -> None:
        self.calls = 0

    def get_latest(self, action_id):
        assert action_id == ACTION_ID
        self.calls += 1
        raise KeyError(action_id)


class _StatusProbe:
    def fetch_status(self, **_kwargs):
        raise AssertionError("Scope rejection must happen before provider status polling")


def _service(monkeypatch):
    direct_post = _DirectPostProbe()
    monkeypatch.setattr(
        reconciliation_module,
        "distribution_execution_service",
        _ExecutionLookup(),
    )
    service = TikTokDirectPostReconciliationService(
        client=_StatusProbe(),  # type: ignore[arg-type]
        direct_post_service=direct_post,  # type: ignore[arg-type]
    )
    return direct_post, service


def test_customer_bound_tiktok_reconciliation_rejects_missing_request_scope(monkeypatch) -> None:
    direct_post, service = _service(monkeypatch)

    with pytest.raises(ValueError, match="dedicated customer execution request flow"):
        service.reconcile(ACTION_ID, mark_executed=False)

    assert direct_post.calls == 0


def test_customer_bound_tiktok_reconciliation_rejects_wrong_request_scope(monkeypatch) -> None:
    direct_post, service = _service(monkeypatch)

    with customer_execution_request_scope(WRONG_REQUEST_ID):
        with pytest.raises(ValueError, match="dedicated customer execution request flow"):
            service.reconcile(ACTION_ID, mark_executed=False)

    assert direct_post.calls == 0


def test_customer_bound_tiktok_reconciliation_allows_matching_request_scope(monkeypatch) -> None:
    direct_post, service = _service(monkeypatch)

    with customer_execution_request_scope(REQUEST_ID):
        with pytest.raises(KeyError):
            service.reconcile(ACTION_ID, mark_executed=False)

    assert direct_post.calls == 1

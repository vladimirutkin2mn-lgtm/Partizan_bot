from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import app.paid_activation as meta_activation_module
import app.tiktok_paid_activation as tiktok_activation_module
from app.distribution_types import DistributionActionStatus, DistributionPlatform
from app.paid_activation import (
    PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
    PaidActivationAuthorizationRequest,
    PaidActivationRequest,
    PaidActivationService,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_paid_activation import (
    TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
    TikTokPaidActivationAuthorizationRequest,
    TikTokPaidActivationRequest,
    TikTokPaidActivationService,
)


class _ExecutionService:
    def __init__(self, action) -> None:
        self.action = action
        self.get_action_calls: list[UUID] = []

    def get_action(self, action_id: UUID):
        self.get_action_calls.append(action_id)
        return self.action


class _MetaProviderSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def set_status(self, **kwargs) -> None:
        self.calls.append(kwargs)
        raise AssertionError("Customer-bound paid activation must not reach Meta")


class _TikTokProviderSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def set_ad_status(self, **kwargs) -> None:
        self.calls.append(("ad", kwargs))
        raise AssertionError("Customer-bound paid activation must not reach TikTok")

    def set_adgroup_status(self, **kwargs) -> None:
        self.calls.append(("adgroup", kwargs))
        raise AssertionError("Customer-bound paid activation must not reach TikTok")

    def set_campaign_status(self, **kwargs) -> None:
        self.calls.append(("campaign", kwargs))
        raise AssertionError("Customer-bound paid activation must not reach TikTok")


def _customer_bound_action(platform: DistributionPlatform):
    return SimpleNamespace(
        id=uuid4(),
        status=DistributionActionStatus.APPROVED,
        platform=platform,
        operational_metadata={"customer_execution_request_id": str(uuid4())},
    )


def test_customer_bound_meta_cannot_authorize_or_start_spend(monkeypatch) -> None:
    action = _customer_bound_action(DistributionPlatform.INSTAGRAM)
    execution = _ExecutionService(action)
    provider = _MetaProviderSpy()
    store = MemoryRuntimeStateStore()
    monkeypatch.setattr(meta_activation_module, "distribution_execution_service", execution)
    service = PaidActivationService(store=store, meta_client=provider)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="dedicated customer execution request flow"):
        service.authorize(
            action.id,
            PaidActivationAuthorizationRequest(
                approved_budget_cap=10,
                confirm_spend=True,
            ),
        )
    with pytest.raises(ValueError, match="dedicated customer execution request flow"):
        service.activate(
            action.id,
            PaidActivationRequest(authorization_id=uuid4()),
        )

    assert execution.get_action_calls == [action.id, action.id]
    assert provider.calls == []
    assert store.list_namespace(PAID_ACTIVATION_AUTHORIZATION_NAMESPACE) == []


def test_customer_bound_tiktok_cannot_authorize_or_start_spend(monkeypatch) -> None:
    action = _customer_bound_action(DistributionPlatform.TIKTOK)
    execution = _ExecutionService(action)
    provider = _TikTokProviderSpy()
    store = MemoryRuntimeStateStore()
    monkeypatch.setattr(tiktok_activation_module, "distribution_execution_service", execution)
    service = TikTokPaidActivationService(store=store, client=provider)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="dedicated customer execution request flow"):
        service.authorize(
            action.id,
            TikTokPaidActivationAuthorizationRequest(
                approved_budget_cap=10,
                confirm_spend=True,
            ),
        )
    with pytest.raises(ValueError, match="dedicated customer execution request flow"):
        service.activate(
            action.id,
            TikTokPaidActivationRequest(authorization_id=uuid4()),
        )

    assert execution.get_action_calls == [action.id, action.id]
    assert provider.calls == []
    assert store.list_namespace(TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE) == []

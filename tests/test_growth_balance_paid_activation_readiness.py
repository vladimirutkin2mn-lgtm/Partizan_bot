from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_types import DistributionActionStatus, DistributionPlatform
from app.execution_adapters import AdapterExecutionOutcome, ExecutionAdapterReceipt
from app.growth_balance import GROWTH_BALANCE_RAIL_NAMESPACE
from app.paid_activation import (
    PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
    PaidActivationAuthorizationRequest,
    PaidActivationRequest,
    PaidActivationService,
)
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
PRODUCT_ID = UUID("22222222-2222-2222-2222-222222222222")
EXPERIMENT_ID = UUID("33333333-3333-3333-3333-333333333333")
ACTION_ID = UUID("44444444-4444-4444-4444-444444444444")


class AlwaysConfiguredSettlement:
    def readiness(self, project_id: UUID) -> tuple[bool, str]:
        assert project_id == PROJECT_ID
        return True, "READY"


class FakeMetaClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def set_status(self, **kwargs) -> None:
        self.calls.append((str(kwargs["object_id"]), str(kwargs["status"])))


class FakeSecretResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, name: str) -> str | None:
        self.calls += 1
        assert name == "META_TEST_TOKEN"
        return "secret"


class FakeConnectionService:
    def __init__(self, ad_account_id: str = "act_123") -> None:
        self.ad_account_id = ad_account_id
        self.calls = 0

    def require_active_meta(self, product_id: UUID):
        self.calls += 1
        assert product_id == PRODUCT_ID
        return SimpleNamespace(
            ad_account_id=self.ad_account_id,
            access_token_env="META_TEST_TOKEN",
        )


def _store(*, card_status: str = "active", paused_reason: str | None = None) -> MemoryRuntimeStateStore:
    store = MemoryRuntimeStateStore()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(PROJECT_ID),
        {
            "id": str(PROJECT_ID),
            "product_id": str(PRODUCT_ID),
        },
    )
    store.put(
        GROWTH_BALANCE_RAIL_NAMESPACE,
        str(PROJECT_ID),
        {
            "project_id": str(PROJECT_ID),
            "provider": "stripe_issuing",
            "card_id": "ic_project",
            "card_status": card_status,
            "currency": "usd",
            "acquisition_limit_cents": 50_000,
            "binding_status": "BOUND",
            "bound_provider": "meta",
            "bound_provider_account_id": "act_123",
            "paused_reason": paused_reason,
        },
    )
    return store


def _wire_action(monkeypatch, *, budget_cap: float = 25.0) -> ExecutionAdapterReceipt:
    import app.paid_activation as paid_activation_module

    action = SimpleNamespace(
        id=ACTION_ID,
        status=DistributionActionStatus.APPROVED,
        platform=DistributionPlatform.INSTAGRAM,
        experiment_id=EXPERIMENT_ID,
    )
    experiment = SimpleNamespace(product_id=PRODUCT_ID)
    monkeypatch.setattr(
        paid_activation_module.distribution_execution_service,
        "get_action",
        lambda action_id: action,
    )
    monkeypatch.setattr(
        paid_activation_module.distribution_execution_service,
        "get_experiment",
        lambda experiment_id: experiment,
    )
    receipt = ExecutionAdapterReceipt(
        action_id=ACTION_ID,
        adapter_name="meta-ads-create-paused",
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        message="staged",
        external_reference="meta:ad:ad_123",
        metadata={
            "provider_ids": {
                "campaign_id": "cmp_123",
                "ad_set_id": "set_123",
                "ad_id": "ad_123",
            }
        },
        created_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        paid_activation_module.distribution_execution_adapter_service,
        "get_receipt",
        lambda action_id: receipt,
    )
    return receipt


def _service(
    store: MemoryRuntimeStateStore,
    monkeypatch,
    *,
    connection: FakeConnectionService | None = None,
) -> tuple[PaidActivationService, FakeMetaClient, FakeSecretResolver, FakeConnectionService]:
    _wire_action(monkeypatch)
    fake_meta = FakeMetaClient()
    secrets = FakeSecretResolver()
    connections = connection or FakeConnectionService()
    spec_service = SimpleNamespace(get=lambda action_id: SimpleNamespace(budget_cap=25.0))
    service = PaidActivationService(
        store=store,
        meta_client=fake_meta,
        secret_resolver=secrets,
        connection_service=connections,
        spec_service=spec_service,
        settlement_readiness=AlwaysConfiguredSettlement(),
    )
    return service, fake_meta, secrets, connections


def test_paused_customer_rail_cannot_create_activation_authorization(monkeypatch) -> None:
    store = _store(card_status="inactive", paused_reason="GROWTH_BALANCE_REVERSAL")
    service, fake_meta, secrets, connections = _service(store, monkeypatch)

    with pytest.raises(ValueError, match="STRIPE_ISSUING_CARD_PAUSED"):
        service.authorize(
            ACTION_ID,
            PaidActivationAuthorizationRequest(
                approved_budget_cap=25.0,
                confirm_spend=True,
            ),
        )

    assert store.list_namespace(PAID_ACTIVATION_AUTHORIZATION_NAMESPACE) == []
    assert connections.calls == 0
    assert secrets.calls == 0
    assert fake_meta.calls == []


def test_activation_rechecks_rail_after_authorization_and_before_provider_mutation(monkeypatch) -> None:
    store = _store()
    service, fake_meta, secrets, connections = _service(store, monkeypatch)
    authorization = service.authorize(
        ACTION_ID,
        PaidActivationAuthorizationRequest(
            approved_budget_cap=25.0,
            confirm_spend=True,
        ),
    )

    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    rail["card_status"] = "inactive"
    rail["paused_reason"] = "ISSUING_FALLBACK"
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID), rail)

    with pytest.raises(ValueError, match="STRIPE_ISSUING_CARD_PAUSED"):
        service.activate(
            ACTION_ID,
            PaidActivationRequest(authorization_id=authorization.id),
        )

    stored = service.get_authorization(authorization.id)
    assert stored.attempted_at is None
    assert stored.consumed_at is None
    assert connections.calls == 0
    assert secrets.calls == 0
    assert fake_meta.calls == []


def test_activation_requires_exact_meta_account_bound_to_growth_balance_rail(monkeypatch) -> None:
    store = _store()
    connection = FakeConnectionService(ad_account_id="act_different")
    service, fake_meta, secrets, connections = _service(
        store,
        monkeypatch,
        connection=connection,
    )
    authorization = service.authorize(
        ACTION_ID,
        PaidActivationAuthorizationRequest(
            approved_budget_cap=25.0,
            confirm_spend=True,
        ),
    )

    with pytest.raises(ValueError, match="different Meta ad account"):
        service.activate(
            ACTION_ID,
            PaidActivationRequest(authorization_id=authorization.id),
        )

    stored = service.get_authorization(authorization.id)
    assert stored.attempted_at is None
    assert connections.calls == 1
    assert secrets.calls == 0
    assert fake_meta.calls == []

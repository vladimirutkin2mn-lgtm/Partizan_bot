from uuid import UUID

from app.config import Settings
from app.growth_balance import GROWTH_BALANCE_RAIL_NAMESPACE
from app.growth_balance_funding_policy import CheckoutFirstGrowthBalanceSettlementService
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("22222222-2222-2222-2222-222222222222")


class FakeIssuingRail(CheckoutFirstGrowthBalanceSettlementService):
    def __init__(self, store: MemoryRuntimeStateStore) -> None:
        super().__init__(
            store,
            settings=Settings(
                _env_file=None,
                growth_balance_settlement_provider="stripe_issuing",
                stripe_secret_key="sk_test_partizan",
                stripe_issuing_cardholder_id="ich_partizan",
                stripe_issuing_currency="usd",
            ),
        )
        self.created: list[dict] = []
        self.modified: list[tuple[str, dict]] = []

    def _create_card(self, **kwargs):
        self.created.append(kwargs)
        return {
            "id": "ic_partizan_project",
            "last4": "4242",
            "status": kwargs["status"],
        }

    def _modify_card(self, card_id: str, **kwargs):
        self.modified.append((card_id, kwargs))
        return {
            "id": card_id,
            "last4": "4242",
            "status": kwargs.get("status", "inactive"),
        }


def test_paused_issuing_rail_stays_inactive_when_capacity_syncs() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeIssuingRail(store)

    service.provision_or_update(PROJECT_ID, 90_909)
    service.confirm_meta_binding(PROJECT_ID, "act_123")
    service.pause(PROJECT_ID, "MANDATE_PAUSED")
    updated = service.provision_or_update(PROJECT_ID, 120_000)

    assert service.created[0]["idempotency_key"] == f"partizan-issuing-card-{PROJECT_ID}"
    assert service.modified[-1][1]["status"] == "inactive"
    assert service.modified[-1][1]["spending_controls"]["spending_limits"] == [
        {"amount": 120_000, "interval": "all_time"}
    ]
    assert all("idempotency_key" not in kwargs for _, kwargs in service.modified)
    assert updated["card_status"] == "inactive"
    assert updated["paused_reason"] == "MANDATE_PAUSED"
    assert updated["acquisition_limit_cents"] == 120_000


def test_repeated_pause_after_reactivation_performs_fresh_provider_mutation() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeIssuingRail(store)

    service.provision_or_update(PROJECT_ID, 90_909)
    service.confirm_meta_binding(PROJECT_ID, "act_123")
    service.pause(PROJECT_ID, "MANDATE_PAUSED")
    service.activate(PROJECT_ID)
    service.pause(PROJECT_ID, "MANDATE_PAUSED")

    statuses = [kwargs["status"] for _, kwargs in service.modified]
    assert statuses == ["active", "inactive", "active", "inactive"]
    assert all("idempotency_key" not in kwargs for _, kwargs in service.modified)

    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "inactive"
    assert rail["paused_reason"] == "MANDATE_PAUSED"


def test_binding_does_not_override_existing_safety_pause() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeIssuingRail(store)

    service.provision_or_update(PROJECT_ID, 90_909)
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    rail["paused_reason"] = "OPERATOR_PAUSED"
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID), rail)

    bound = service.confirm_meta_binding(PROJECT_ID, "act_123")

    assert service.modified[-1][1]["status"] == "inactive"
    assert bound["binding_status"] == "BOUND"
    assert bound["card_status"] == "inactive"
    assert bound["paused_reason"] == "OPERATOR_PAUSED"

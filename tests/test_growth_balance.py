from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.customer_autopilot import customer_autopilot_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerAutopilotConfigureRequest, CustomerPreviewRequest
from app.growth_balance import GROWTH_BALANCE_TOPUP_NAMESPACE, GrowthBalanceService, growth_balance_service
from app.main import app
from app.runtime_store import MemoryRuntimeStateStore

client = TestClient(app)
PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")


class ReadySettlement:
    def readiness(self, project_id: UUID) -> tuple[bool, str]:
        del project_id
        return True, "READY"


@pytest.fixture(autouse=True)
def reset_customer_state() -> None:
    customer_funnel_service.reset()
    growth_balance_service.reset()


def test_all_in_growth_balance_charges_fee_only_on_actual_acquisition_spend() -> None:
    store = MemoryRuntimeStateStore()
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "cs_paid_1000",
        {
            "session_id": "cs_paid_1000",
            "project_id": str(PROJECT_ID),
            "amount_cents": 100_000,
            "currency": "usd",
            "state": "PAID",
        },
    )
    service = GrowthBalanceService(store, settlement_service=ReadySettlement())

    summary = service.summary(PROJECT_ID, 600.0)

    assert summary.funded_usd == 1000.0
    assert summary.acquisition_spend_usd == 600.0
    assert summary.management_fee_pct == 10
    assert summary.management_fee_usd == 60.0
    assert summary.used_usd == 660.0
    assert summary.available_usd == 340.0
    assert summary.acquisition_capacity_usd == 909.09
    assert summary.remaining_acquisition_capacity_usd == 309.09
    assert summary.settlement_ready is True


def test_all_in_capacity_uses_fee_on_media_spend_not_ten_percent_of_deposit() -> None:
    assert GrowthBalanceService._max_acquisition_cents(100_000, 10) == 90_909
    assert GrowthBalanceService._fee_cents(90_909, 10) == 9_091


def test_paid_checkout_recovers_from_pre_stripe_liquidity_reservation() -> None:
    store = MemoryRuntimeStateStore()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(PROJECT_ID),
        {"id": str(PROJECT_ID), "autopilot_subscription_status": "ACTIVE"},
    )
    reservation_key = f"reservation:{PROJECT_ID}:7"
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        reservation_key,
        {
            "reservation_key": reservation_key,
            "project_id": str(PROJECT_ID),
            "checkout_generation": 7,
            "amount_cents": 100_000,
            "currency": "usd",
            "state": "RESERVED",
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    service = GrowthBalanceService(store, settlement_service=ReadySettlement())

    credited = service.credit_paid_checkout(
        PROJECT_ID,
        session_id="cs_paid_after_db_gap",
        amount_cents=100_000,
        currency="usd",
        stripe_customer_id="cus_recovered",
    )

    assert credited is True
    recovered = service.pending("cs_paid_after_db_gap")
    assert recovered is not None
    assert recovered["state"] == "PAID"
    assert recovered["checkout_generation"] == 7
    assert recovered["recovered_from_reservation"] is True
    reservation = store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, reservation_key)
    assert reservation is not None
    assert reservation["state"] == "CHECKOUT_CREATED"
    assert reservation["session_id"] == "cs_paid_after_db_gap"
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(PROJECT_ID))
    assert project is not None
    assert project["stripe_customer_id"] == "cus_recovered"
    assert service.summary(PROJECT_ID, 0.0).funded_usd == 1000.0


def test_legacy_delegated_marketing_budget_payload_is_rejected() -> None:
    with pytest.raises(ValidationError, match="marketing_budget_usd"):
        CustomerAutopilotConfigureRequest.model_validate(
            {
                "marketing_budget_usd": 1000,
                "target_max_cac": 30,
                "confirm_autonomous_spend": True,
            }
        )


def test_growth_balance_checkout_fails_closed_until_partizan_funded_rail_exists() -> None:
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    customer_autopilot_service.sync_subscription(
        preview.project_id,
        subscription_id="sub_active",
        stripe_status="active",
        stripe_customer_id="cus_test",
    )

    response = client.post(
        f"/v1/customer-projects/{preview.project_id}/growth-balance/checkout",
        headers={"X-Partizan-Customer-Token": preview.customer_token},
        json={"amount_usd": 1000},
    )

    assert response.status_code == 409
    assert "payment rail" in response.json()["detail"]


def test_unfunded_autopilot_overview_exposes_no_legacy_budget_fields() -> None:
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    customer_autopilot_service.sync_subscription(
        preview.project_id,
        subscription_id="sub_active",
        stripe_status="active",
    )

    response = client.get(
        f"/v1/customer-projects/{preview.project_id}/autopilot",
        headers={"X-Partizan-Customer-Token": preview.customer_token},
    )
    payload = response.json()

    assert response.status_code == 200
    assert "growth_balance" in payload
    assert "marketing_budget_usd" not in payload
    assert "remaining_budget_usd" not in payload
    assert "estimated_managed_fee_usd" not in payload
    assert payload["growth_balance"]["settlement_ready"] is False

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import (
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GrowthBalanceService,
    growth_balance_service,
)
from app.main import app
from app.runtime_store import MemoryRuntimeStateStore, get_runtime_store

client = TestClient(app)
PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")


class ReadySettlement:
    def readiness(self, project_id: UUID) -> tuple[bool, str]:
        del project_id
        return True, "READY"

    def provision_or_update(self, project_id: UUID, acquisition_capacity_cents: int) -> dict:
        return {
            "project_id": str(project_id),
            "acquisition_limit_cents": acquisition_capacity_cents,
        }


@pytest.fixture(autouse=True)
def reset_customer_projects() -> None:
    customer_funnel_service.reset()
    growth_balance_service.reset()


def _preview():
    return customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=500,
        )
    )


def test_preview_exposes_plan_price_and_execution_fee_but_no_monthly_subscription_price() -> None:
    preview = _preview()
    payload = preview.model_dump(mode="json")

    assert payload["launch_price_usd"] == 49
    assert payload["managed_spend_fee_pct"] == 10
    assert "autopilot_price_usd" not in payload


def test_retired_autopilot_subscription_checkout_routes_are_gone() -> None:
    preview = _preview()
    headers = {"X-Partizan-Customer-Token": preview.customer_token}

    assert client.post(
        f"/v1/customer-projects/{preview.project_id}/autopilot/checkout",
        headers=headers,
    ).status_code == 404
    assert client.post(
        f"/v1/customer-projects/{preview.project_id}/autopilot/verify",
        headers=headers,
        json={"session_id": "cs_retired"},
    ).status_code == 404


def test_guardrails_can_be_saved_before_research_or_growth_balance() -> None:
    preview = _preview()
    headers = {"X-Partizan-Customer-Token": preview.customer_token}

    response = client.put(
        f"/v1/customer-projects/{preview.project_id}/autopilot",
        headers=headers,
        json={"target_max_cac": 30, "confirm_autonomous_spend": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["autopilot_status"] == "RESEARCHING"
    assert payload["growth_balance"]["funded_usd"] == 0
    assert not any("guardrails are not saved" in item for item in payload["blockers"])
    project = get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    assert project["autopilot_target_max_cac"] == 30
    assert project["autopilot_spend_confirmed"] is True


def test_paid_growth_balance_unlocks_research_without_buying_49_plan() -> None:
    store = MemoryRuntimeStateStore()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(PROJECT_ID),
        {
            "id": str(PROJECT_ID),
            "status": "PREVIEW",
            "launch_unlocked": False,
            "stripe_customer_id": None,
        },
    )
    reservation_key = f"reservation:{PROJECT_ID}:1"
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        reservation_key,
        {
            "reservation_key": reservation_key,
            "project_id": str(PROJECT_ID),
            "checkout_generation": 1,
            "amount_cents": 100_000,
            "currency": "usd",
            "state": "CHECKOUT_CREATED",
            "session_id": "cs_growth_execution",
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "cs_growth_execution",
        {
            "session_id": "cs_growth_execution",
            "project_id": str(PROJECT_ID),
            "checkout_generation": 1,
            "amount_cents": 100_000,
            "currency": "usd",
            "state": "PENDING",
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    service = GrowthBalanceService(store, settlement_service=ReadySettlement())

    assert service.credit_paid_checkout(
        PROJECT_ID,
        session_id="cs_growth_execution",
        amount_cents=100_000,
        currency="usd",
        stripe_customer_id="cus_growth",
    ) is True

    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(PROJECT_ID))
    assert project is not None
    assert project["launch_unlocked"] is True
    assert project["launch_entitlement_source"] == "GROWTH_BALANCE"
    assert project["status"] == "UNLOCKED"
    assert project["stripe_customer_id"] == "cus_growth"
    assert service.summary(PROJECT_ID, 0.0).funded_usd == 1000.0


def test_start_page_is_channel_first_growth_balance_execution_flow() -> None:
    page = client.get("/start")
    css = client.get("/start/assets/start.v2.css")
    javascript = client.get("/start/assets/start.v2.js")
    channel_javascript = client.get("/start/assets/start.channels.v1.js")

    assert page.status_code == 200
    assert '/start/assets/start.v2.css' in page.text
    assert '/start/assets/start.v2.js' in page.text
    assert '/start/assets/start.channels.v1.js' in page.text
    assert 'id="autopilot-direct-button"' in page.text
    assert 'id="growth-balance-form"' in page.text
    assert 'id="view-strategy-button"' in page.text
    assert "$149" not in page.text
    assert "No monthly subscription" in page.text
    assert 'id="autopilot-budget"' not in page.text
    assert "Channels Partizan can use" in page.text
    for channel in ("Instagram &amp; Facebook", "TikTok", "Reddit", "Telegram"):
        assert channel in page.text
    assert page.text.index("1 · Connect access") < page.text.index("2 · Guardrails")
    assert page.text.index("2 · Guardrails") < page.text.index("3 · Fund growth")
    assert css.status_code == 200
    assert ".autopilot-first-launch" in css.text
    assert ".channel-grid" in css.text
    assert ".growth-balance-form" in css.text
    assert javascript.status_code == 200
    assert "View strategy & audience" in javascript.text
    assert "/autopilot/checkout" not in javascript.text
    assert "/growth-balance/checkout" in javascript.text
    assert "marketing_budget_usd" not in javascript.text
    assert channel_javascript.status_code == 200
    assert "Growth Balance is being enabled" in channel_javascript.text
    assert "meta-connect-button" in channel_javascript.text
    assert "autopilot-config-button" in channel_javascript.text

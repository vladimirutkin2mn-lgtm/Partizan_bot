from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.customer_autopilot import customer_autopilot_service
from app.customer_funnel import CustomerPaymentRequiredError, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_customer_projects() -> None:
    customer_funnel_service.reset()
    growth_balance_service.reset()


def _preview(*, website: bool = True):
    return customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com" if website else None,
            market="United States",
            goal="Get paying customers",
            budget_usd=500,
        )
    )


def test_autopilot_checkout_no_longer_requires_paid_acquisition_plan() -> None:
    preview = _preview()

    generation, stripe_customer_id = customer_autopilot_service.prepare_checkout(
        preview.project_id,
        preview.customer_token,
    )
    project = customer_funnel_service.get_project(preview.project_id, preview.customer_token)

    assert generation == 1
    assert stripe_customer_id is None
    assert project.launch_unlocked is False
    assert project.research_state == "NOT_STARTED"
    assert project.product_id is None


def test_autopilot_checkout_still_fails_closed_without_destination() -> None:
    preview = _preview(website=False)

    with pytest.raises(CustomerPaymentRequiredError, match="website or landing page"):
        customer_autopilot_service.prepare_checkout(preview.project_id, preview.customer_token)


def test_active_autopilot_bundles_research_entitlement_before_research_runs() -> None:
    preview = _preview()
    customer_autopilot_service.prepare_checkout(preview.project_id, preview.customer_token)
    customer_autopilot_service.mark_checkout_pending(
        preview.project_id,
        preview.customer_token,
        "cs_autopilot_direct",
    )

    status = customer_autopilot_service.sync_subscription(
        UUID(str(preview.project_id)),
        subscription_id="sub_autopilot_direct",
        stripe_status="active",
        stripe_customer_id="cus_direct",
        checkout_session_id="cs_autopilot_direct",
    )
    project = customer_funnel_service.get_project(preview.project_id, preview.customer_token)
    overview = customer_autopilot_service.overview(preview.project_id, preview.customer_token)

    assert status == "ACTIVE"
    assert project.launch_unlocked is True
    assert project.status == "UNLOCKED"
    assert project.research_state == "NOT_STARTED"
    assert overview.product_id is None
    assert overview.subscription_status == "ACTIVE"
    assert overview.autopilot_status == "RESEARCHING"
    assert overview.setup_complete is False
    assert overview.growth_balance.funded_usd == 0
    assert overview.growth_balance.settlement_ready is False
    assert any("mapping" in blocker.lower() for blocker in overview.blockers)


def test_start_page_is_clean_v2_autopilot_first_flow() -> None:
    page = client.get("/start")
    css = client.get("/start/assets/start.v2.css")
    javascript = client.get("/start/assets/start.v2.js")

    assert page.status_code == 200
    assert '/start/assets/start.v2.css' in page.text
    assert '/start/assets/start.v2.js' in page.text
    assert 'id="autopilot-direct-button"' in page.text
    assert 'id="growth-balance-form"' in page.text
    assert 'id="view-strategy-button"' in page.text
    assert 'id="autopilot-budget"' not in page.text
    assert css.status_code == 200
    assert ".autopilot-first-launch" in css.text
    assert ".growth-balance-form" in css.text
    assert javascript.status_code == 200
    assert "View strategy & audience" in javascript.text
    assert "/autopilot/checkout" in javascript.text
    assert "/growth-balance/checkout" in javascript.text
    assert "marketing_budget_usd" not in javascript.text

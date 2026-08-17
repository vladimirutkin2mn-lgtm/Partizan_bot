from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.customer_funnel import CustomerFunnelService, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.main import app
from app.runtime_store import MemoryRuntimeStateStore

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_customer_projects() -> None:
    customer_funnel_service.reset()


def _preview_payload() -> dict:
    return {
        "brief": "AI bookkeeping assistant for freelancers with a monthly subscription.",
        "market": "United States",
        "goal": "Get paying customers",
        "budget_usd": 1000,
    }


def test_free_preview_is_deterministic_and_requires_no_llm_or_search() -> None:
    service = CustomerFunnelService(MemoryRuntimeStateStore())
    payload = CustomerPreviewRequest.model_validate(_preview_payload())

    first = service.create_preview(payload)
    second = service.create_preview(payload)

    assert first.opportunity_scope_estimate == second.opportunity_scope_estimate
    assert first.fastest_signal == second.fastest_signal
    assert first.directions
    assert all("••" in item.label for item in first.masked_opportunities)
    assert first.launch_price_usd == 49


def test_customer_preview_and_project_access_are_public_but_token_gated() -> None:
    preview = client.post("/v1/customer-projects/preview", json=_preview_payload())

    assert preview.status_code == 201
    data = preview.json()
    project_id = data["project_id"]
    token = data["customer_token"]
    assert data["launch_price_usd"] == 49
    assert data["opportunity_scope_estimate"] >= 1

    missing = client.get(f"/v1/customer-projects/{project_id}")
    wrong = client.get(
        f"/v1/customer-projects/{project_id}",
        headers={"X-Partizan-Customer-Token": "wrong"},
    )
    allowed = client.get(
        f"/v1/customer-projects/{project_id}",
        headers={"X-Partizan-Customer-Token": token},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["launch_unlocked"] is False


def test_deep_research_is_blocked_before_payment_without_calling_product_intake(monkeypatch) -> None:
    preview = client.post("/v1/customer-projects/preview", json=_preview_payload()).json()

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("free project must not reach paid LLM research")

    monkeypatch.setattr("app.customer_funnel.product_intake_service.create_draft", fail_if_called)
    response = client.post(
        f"/v1/customer-projects/{preview['project_id']}/deep-research",
        headers={"X-Partizan-Customer-Token": preview["customer_token"]},
    )

    assert response.status_code == 402
    assert "Unlock" in response.json()["detail"]


def test_checkout_fails_closed_when_stripe_is_not_configured() -> None:
    preview = client.post("/v1/customer-projects/preview", json=_preview_payload()).json()

    response = client.post(
        f"/v1/customer-projects/{preview['project_id']}/checkout",
        headers={"X-Partizan-Customer-Token": preview["customer_token"]},
    )

    assert response.status_code == 503
    assert "Stripe launch checkout is not configured" in response.json()["detail"]


def test_signed_checkout_webhook_unlocks_only_matching_pending_session(monkeypatch) -> None:
    preview = client.post("/v1/customer-projects/preview", json=_preview_payload()).json()
    project_id = UUID(preview["project_id"])
    customer_funnel_service.mark_checkout_pending(project_id, preview["customer_token"], "cs_test_123")

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "payment_status": "paid",
                "customer": "cus_123",
                "metadata": {
                    "partizan_project_id": str(project_id),
                    "partizan_entitlement": "launch_plan",
                },
            }
        },
    }
    monkeypatch.setattr("app.customer_routes.construct_stripe_event", lambda **kwargs: event)

    response = client.post(
        "/v1/billing/stripe/webhook",
        content=b"signed-payload",
        headers={"Stripe-Signature": "t=1,v1=test"},
    )
    project = client.get(
        f"/v1/customer-projects/{project_id}",
        headers={"X-Partizan-Customer-Token": preview["customer_token"]},
    )

    assert response.status_code == 200
    assert project.status_code == 200
    assert project.json()["launch_unlocked"] is True
    assert project.json()["status"] == "UNLOCKED"


def test_signed_webhook_cannot_unlock_project_without_matching_pending_checkout(monkeypatch) -> None:
    preview = client.post("/v1/customer-projects/preview", json=_preview_payload()).json()
    project_id = UUID(preview["project_id"])
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_unbound",
                "payment_status": "paid",
                "customer": "cus_unbound",
                "metadata": {
                    "partizan_project_id": str(project_id),
                    "partizan_entitlement": "launch_plan",
                },
            }
        },
    }
    monkeypatch.setattr("app.customer_routes.construct_stripe_event", lambda **kwargs: event)

    response = client.post(
        "/v1/billing/stripe/webhook",
        content=b"signed-payload",
        headers={"Stripe-Signature": "t=1,v1=test"},
    )
    project = client.get(
        f"/v1/customer-projects/{project_id}",
        headers={"X-Partizan-Customer-Token": preview["customer_token"]},
    )

    assert response.status_code == 200
    assert project.json()["launch_unlocked"] is False
    assert project.json()["status"] == "PREVIEW"


def test_customer_start_page_and_assets_are_served() -> None:
    page = client.get("/start")
    css = client.get("/start/assets/start.v1.css")
    javascript = client.get("/start/assets/start.v1.js")

    assert page.status_code == 200
    assert "Unlock Acquisition Plan — $49" in page.text
    assert 'id="preview-form"' in page.text
    assert 'id="checkout-button"' in page.text
    assert css.status_code == 200
    assert "--lime" in css.text
    assert javascript.status_code == 200
    assert "/v1/customer-projects/preview" in javascript.text
    assert "/recover-access" in javascript.text
    assert "/deep-research" in javascript.text
    assert "X-Partizan-Customer-Token" in javascript.text
    assert "localStorage" in javascript.text
    assert "sessionStorage" not in javascript.text


def test_landing_primary_ctas_route_to_start_funnel_via_script() -> None:
    javascript = client.get("/site/assets/landing.v1.js")

    assert javascript.status_code == 200
    assert 'a.button-primary[href="/app"]' in javascript.text
    assert "/start?budget=" in javascript.text

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.growth_balance_jit_funding import (
    GrowthBalanceJitFundingService,
    NextMoveFundingPlan,
)
from app.main import app


@pytest.fixture(autouse=True)
def reset_customer_state() -> None:
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()


def _opportunity(cost: float) -> dict[str, object]:
    return {
        "title": "Sponsor a niche newsletter placement",
        "recommended_action": "Run the smallest placement and measure paid signups.",
        "estimated_cost_max_usd": cost,
    }


def _plan(
    *,
    cost: float,
    funded: float,
    spent: float = 0,
    remaining_capacity: float = 0,
) -> NextMoveFundingPlan:
    return GrowthBalanceJitFundingService().plan(
        opportunity=_opportunity(cost),
        project_budget_usd=30,
        funded_usd=funded,
        acquisition_spend_usd=spent,
        remaining_acquisition_capacity_usd=remaining_capacity,
        management_fee_pct=10,
    )


def test_jit_funding_includes_fee_without_front_loading_extra_budget() -> None:
    plan = _plan(cost=20, funded=0)

    assert plan.required_acquisition_usd == 20
    assert plan.topup_amount_usd == 22
    assert plan.funding_required is True


def test_jit_funding_only_requests_the_remaining_shortfall() -> None:
    plan = _plan(cost=20, funded=11, remaining_capacity=10)

    assert plan.remaining_acquisition_capacity_usd == 10
    assert plan.topup_amount_usd == 11


def test_jit_funding_accounts_for_spend_already_consumed() -> None:
    plan = _plan(cost=20, funded=11, spent=10)

    assert plan.topup_amount_usd == 22


def test_jit_funding_creates_no_topup_when_capacity_is_already_funded() -> None:
    plan = _plan(cost=20, funded=22, remaining_capacity=20)

    assert plan.topup_amount_usd == 0
    assert plan.funding_required is False


def test_jit_funding_never_funds_above_customer_test_budget() -> None:
    with pytest.raises(ValueError, match="exceeds the customer test budget"):
        GrowthBalanceJitFundingService().plan(
            opportunity=_opportunity(31),
            project_budget_usd=30,
            funded_usd=0,
            acquisition_spend_usd=0,
            remaining_acquisition_capacity_usd=0,
            management_fee_pct=10,
        )


def test_zero_cost_move_never_requests_acquisition_funding() -> None:
    plan = _plan(cost=0, funded=0)

    assert plan.required_acquisition_usd == 0
    assert plan.topup_amount_usd == 0
    assert plan.funding_required is False


def _registered_workspace() -> tuple[TestClient, object]:
    client = TestClient(app)
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=30,
        )
    )
    response = client.post(
        "/customer/account/register",
        json={
            "email": "jit-founder@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def test_jit_checkout_uses_server_derived_amount_without_client_amount(monkeypatch) -> None:
    client, preview = _registered_workspace()
    plan = NextMoveFundingPlan(
        opportunity_title="Niche newsletter",
        recommended_action="Run one placement.",
        required_acquisition_usd=20,
        remaining_acquisition_capacity_usd=10,
        topup_amount_usd=11,
        management_fee_pct=10,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.growth_balance_jit_routes._funding_plan",
        lambda _project_id, _customer_token: plan,
    )

    def fake_prepare(project_id, customer_token, amount_usd):
        captured["project_id"] = project_id
        captured["customer_token"] = customer_token
        captured["amount_usd"] = amount_usd
        return 7, None, 1100

    monkeypatch.setattr(
        "app.growth_balance_jit_routes.growth_balance_service.prepare_checkout",
        fake_prepare,
    )
    monkeypatch.setattr(
        "app.growth_balance_jit_routes.create_growth_balance_checkout",
        lambda **_kwargs: SimpleNamespace(
            session_id="cs_jit_123",
            url="https://checkout.stripe.test/jit",
        ),
    )
    monkeypatch.setattr(
        "app.growth_balance_jit_routes.growth_balance_service.mark_checkout_pending",
        lambda *_args, **_kwargs: None,
    )

    response = client.post(
        f"/customer/workspace/{preview.project_id}/growth-balance/next-move/checkout"
    )

    assert response.status_code == 200
    assert captured["amount_usd"] == 11
    assert response.json()["topup_amount_usd"] == 11
    assert response.json()["checkout_url"] == "https://checkout.stripe.test/jit"


def test_jit_checkout_skips_stripe_when_move_is_already_funded(monkeypatch) -> None:
    client, preview = _registered_workspace()
    plan = NextMoveFundingPlan(
        opportunity_title="Niche newsletter",
        recommended_action="Run one placement.",
        required_acquisition_usd=20,
        remaining_acquisition_capacity_usd=20,
        topup_amount_usd=0,
        management_fee_pct=10,
    )
    monkeypatch.setattr(
        "app.growth_balance_jit_routes._funding_plan",
        lambda _project_id, _customer_token: plan,
    )

    def should_not_prepare(*_args, **_kwargs):
        raise AssertionError("already-funded move must not create a checkout")

    monkeypatch.setattr(
        "app.growth_balance_jit_routes.growth_balance_service.prepare_checkout",
        should_not_prepare,
    )

    response = client.post(
        f"/customer/workspace/{preview.project_id}/growth-balance/next-move/checkout"
    )

    assert response.status_code == 200
    assert response.json()["funding_required"] is False
    assert response.json()["checkout_url"] is None

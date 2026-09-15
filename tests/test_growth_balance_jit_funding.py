from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

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
from app.growth_balance_jit_routes import _funding_plan
from app.main import app


@pytest.fixture(autouse=True)
def reset_customer_state() -> None:
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()


def _plan(
    *,
    cost: float,
    funded: float,
    spent: float = 0,
    remaining_capacity: float = 0,
) -> NextMoveFundingPlan:
    return GrowthBalanceJitFundingService().plan(
        required_acquisition_usd=cost,
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
            required_acquisition_usd=31,
            project_budget_usd=30,
            funded_usd=0,
            acquisition_spend_usd=0,
            remaining_acquisition_capacity_usd=0,
            management_fee_pct=10,
        )


def test_zero_cost_research_move_never_enters_paid_funding() -> None:
    with pytest.raises(ValueError, match="required_acquisition_usd must be positive"):
        _plan(cost=0, funded=0)


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


def _experiment():
    return SimpleNamespace(
        experiment_id=uuid4(),
        action_id=uuid4(),
        platform="INSTAGRAM",
    )


def test_non_waiting_experiment_cannot_trigger_jit_funding(monkeypatch) -> None:
    project_id = uuid4()
    customer_token = "customer-token"
    product_id = uuid4()
    experiment_id = uuid4()
    monkeypatch.setattr(
        "app.growth_balance_jit_routes.customer_funnel_service.get_project",
        lambda _project_id, _customer_token: SimpleNamespace(
            product_id=product_id,
            budget_usd=30,
        ),
    )
    monkeypatch.setattr(
        "app.growth_balance_jit_routes.autonomy_overview_service.get",
        lambda _product_id: SimpleNamespace(waiting_approval=[]),
    )

    with pytest.raises(ValueError, match="not waiting for execution"):
        _funding_plan(project_id, customer_token, experiment_id)


def test_jit_checkout_uses_server_derived_amount_without_client_amount(monkeypatch) -> None:
    client, preview = _registered_workspace()
    experiment = _experiment()
    plan = NextMoveFundingPlan(
        required_acquisition_usd=20,
        remaining_acquisition_capacity_usd=10,
        topup_amount_usd=11,
        management_fee_pct=10,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.growth_balance_jit_routes._funding_plan",
        lambda _project_id, _customer_token, _experiment_id: (plan, experiment),
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
        f"/customer/workspace/{preview.project_id}/growth-balance/experiments/"
        f"{experiment.experiment_id}/funding/checkout"
    )

    assert response.status_code == 200
    assert captured["amount_usd"] == 11
    assert response.json()["experiment_id"] == str(experiment.experiment_id)
    assert response.json()["action_id"] == str(experiment.action_id)
    assert response.json()["topup_amount_usd"] == 11
    assert response.json()["checkout_url"] == "https://checkout.stripe.test/jit"


def test_jit_checkout_skips_stripe_when_move_is_already_funded(monkeypatch) -> None:
    client, preview = _registered_workspace()
    experiment = _experiment()
    plan = NextMoveFundingPlan(
        required_acquisition_usd=20,
        remaining_acquisition_capacity_usd=20,
        topup_amount_usd=0,
        management_fee_pct=10,
    )
    monkeypatch.setattr(
        "app.growth_balance_jit_routes._funding_plan",
        lambda _project_id, _customer_token, _experiment_id: (plan, experiment),
    )

    def should_not_prepare(*_args, **_kwargs):
        raise AssertionError("already-funded move must not create a checkout")

    monkeypatch.setattr(
        "app.growth_balance_jit_routes.growth_balance_service.prepare_checkout",
        should_not_prepare,
    )

    response = client.post(
        f"/customer/workspace/{preview.project_id}/growth-balance/experiments/"
        f"{experiment.experiment_id}/funding/checkout"
    )

    assert response.status_code == 200
    assert response.json()["funding_required"] is False
    assert response.json()["checkout_url"] is None

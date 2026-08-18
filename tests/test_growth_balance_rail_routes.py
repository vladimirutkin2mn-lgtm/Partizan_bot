from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.operator_auth import OPERATOR_KEY_HEADER

client = TestClient(app)


class FakeRailService:
    def __init__(self) -> None:
        self.authorizations: list[dict] = []
        self.transactions: list[dict] = []
        self.bindings: list[tuple[object, str]] = []

    def authorize_request(self, authorization: dict) -> bool:
        self.authorizations.append(authorization)
        return True

    def record_issuing_transaction(self, transaction: dict) -> bool:
        self.transactions.append(transaction)
        return True

    def confirm_meta_binding(self, project_id, ad_account_id: str) -> dict:
        self.bindings.append((project_id, ad_account_id))
        return {"binding_status": "BOUND"}

    def rail_view(self, project_id) -> dict:
        return {
            "project_id": str(project_id),
            "provider": "stripe_issuing",
            "settlement_ready": bool(self.bindings),
            "settlement_status": "READY" if self.bindings else "NOT_BOUND",
            "binding_status": "BOUND" if self.bindings else "UNBOUND",
        }


@pytest.fixture(autouse=True)
def cleanup_overrides() -> None:
    app.dependency_overrides.pop(get_settings, None)
    yield
    app.dependency_overrides.pop(get_settings, None)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="production",
        operator_api_key="operator-secret",
        stripe_issuing_authorization_webhook_secret="whsec_auth_test",
        stripe_issuing_events_webhook_secret="whsec_events_test",
        stripe_issuing_webhook_api_version="2025-03-31.basil",
    )


def test_signed_issuing_authorization_is_public_and_returns_direct_decision(monkeypatch) -> None:
    import app.growth_balance_rail_routes as routes

    fake = FakeRailService()
    monkeypatch.setattr(routes, "growth_balance_service", fake)
    monkeypatch.setattr(
        routes.stripe.Webhook,
        "construct_event",
        lambda payload, signature, secret: {
            "type": "issuing_authorization.request",
            "data": {"object": {"id": "iauth_test", "pending_request": {"amount": 2500}}},
        },
    )
    app.dependency_overrides[get_settings] = _settings

    response = client.post(
        "/v1/billing/stripe/issuing-authorizations",
        content=b"{}",
        headers={"Stripe-Signature": "signed"},
    )

    assert response.status_code == 200
    assert response.json() == {"approved": True}
    assert response.headers["Stripe-Version"] == "2025-03-31.basil"
    assert fake.authorizations[0]["id"] == "iauth_test"


def test_signed_issuing_transaction_event_is_recorded(monkeypatch) -> None:
    import app.growth_balance_rail_routes as routes

    fake = FakeRailService()
    monkeypatch.setattr(routes, "growth_balance_service", fake)
    monkeypatch.setattr(
        routes.stripe.Webhook,
        "construct_event",
        lambda payload, signature, secret: {
            "type": "issuing_transaction.created",
            "data": {"object": {"id": "ipi_test", "amount": -5000, "currency": "usd"}},
        },
    )
    app.dependency_overrides[get_settings] = _settings

    response = client.post(
        "/v1/billing/stripe/issuing-events",
        content=b"{}",
        headers={"Stripe-Signature": "signed"},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    assert fake.transactions[0]["id"] == "ipi_test"


def test_meta_billing_binding_is_operator_only_and_requires_double_confirmation(monkeypatch) -> None:
    import app.growth_balance_rail_routes as routes

    fake = FakeRailService()
    monkeypatch.setattr(routes, "growth_balance_service", fake)
    app.dependency_overrides[get_settings] = _settings
    project_id = uuid4()
    path = f"/v1/customer-projects/{project_id}/growth-balance/rail/meta-binding"
    payload = {
        "ad_account_id": "act_123",
        "confirm_partizan_card_primary": True,
        "confirm_customer_payment_method_not_used": True,
    }

    blocked = client.post(path, json=payload)
    assert blocked.status_code == 401

    incomplete = client.post(
        path,
        json={**payload, "confirm_customer_payment_method_not_used": False},
        headers={OPERATOR_KEY_HEADER: "operator-secret"},
    )
    assert incomplete.status_code == 409

    confirmed = client.post(
        path,
        json=payload,
        headers={OPERATOR_KEY_HEADER: "operator-secret"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["binding_status"] == "BOUND"
    assert fake.bindings == [(project_id, "act_123")]

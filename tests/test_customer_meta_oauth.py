from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet

from app.config import Settings
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_meta_oauth import (
    CUSTOMER_META_OAUTH_STATE_NAMESPACE,
    CustomerMetaOAuthService,
    HttpxMetaOAuthClient,
)
from app.customer_schemas import CustomerPreviewRequest
from app.runtime_store import get_runtime_store


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _settings(**overrides) -> Settings:
    values = {
        "partizan_public_base_url": "https://partizan.example.com",
        "meta_oauth_app_id": "1234567890",
        "meta_oauth_app_secret": "meta-secret-not-real",
        "meta_oauth_api_version": "v25.0",
        "provider_secret_encryption_key": Fernet.generate_key().decode("ascii"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_meta_resource_requests_keep_user_token_in_bearer_header(monkeypatch) -> None:
    calls: list[tuple[str, dict, dict | None]] = []

    def fake_get(url, *, params, headers=None, timeout):
        calls.append((url, params, headers))
        if url.endswith("/me/adaccounts"):
            return _Response({"data": [{"id": "act_123", "account_id": "123"}]})
        return _Response({"promote_pages": {"data": [{"id": "page_1", "name": "Page"}]}})

    monkeypatch.setattr("app.customer_meta_oauth.httpx.get", fake_get)
    client = HttpxMetaOAuthClient(_settings())
    token = "EAAB-not-a-real-user-token"

    accounts = client.ad_accounts(token)
    pages = client.promote_pages(token, "123")

    assert accounts[0]["account_id"] == "123"
    assert pages[0]["id"] == "page_1"
    assert len(calls) == 2
    for url, params, headers in calls:
        assert token not in url
        assert "access_token" not in params
        assert headers == {"Authorization": f"Bearer {token}"}


def test_meta_oauth_state_is_one_time_random_and_stored_only_as_digest() -> None:
    store = get_runtime_store()
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for freelancers with a monthly subscription.",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    project["research_state"] = "READY"
    project["product_id"] = str(uuid4())
    project["autopilot_subscription_status"] = "ACTIVE"
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id), project)

    service = CustomerMetaOAuthService(store=store, settings=_settings())
    authorization_url = service.begin(preview.project_id, preview.customer_token)
    query = parse_qs(urlsplit(authorization_url).query)
    state = query["state"][0]

    assert len(state) >= 32
    assert query["scope"] == ["ads_management,ads_read"]
    persisted = store.list_namespace(CUSTOMER_META_OAUTH_STATE_NAMESPACE)
    assert len(persisted) >= 1
    assert all(state not in str(item) for item in persisted)

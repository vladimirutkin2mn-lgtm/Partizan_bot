from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.customer_autopilot import STAGED_META_CONNECTION_KEY
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_meta_oauth import (
    CUSTOMER_META_OAUTH_STATE_NAMESPACE,
    CUSTOMER_META_PENDING_NAMESPACE,
    CustomerMetaOAuthError,
    CustomerMetaOAuthService,
    HttpxMetaOAuthClient,
)
from app.customer_schemas import CustomerMetaConnectionRequest, CustomerPreviewRequest
from app.main import app
from app.provider_secret_store import ProviderSecretStore
from app.runtime_store import get_runtime_store

client = TestClient(app)


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


def _preview():
    return customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )


def test_meta_resource_requests_keep_user_token_in_bearer_header(monkeypatch) -> None:
    calls: list[tuple[str, dict, dict | None]] = []

    def fake_get(url, *, params, headers=None, timeout):
        calls.append((url, params, headers))
        if url.endswith("/me/adaccounts"):
            return _Response({"data": [{"id": "act_123", "account_id": "123"}]})
        return _Response({"promote_pages": {"data": [{"id": "page_1", "name": "Page"}]}})

    monkeypatch.setattr("app.customer_meta_oauth.httpx.get", fake_get)
    oauth_client = HttpxMetaOAuthClient(_settings())
    token = "EAAB-not-a-real-user-token"

    accounts = oauth_client.ad_accounts(token)
    pages = oauth_client.promote_pages(token, "123")

    assert accounts[0]["account_id"] == "123"
    assert pages[0]["id"] == "page_1"
    assert len(calls) == 2
    for url, params, headers in calls:
        assert token not in url
        assert "access_token" not in params
        assert headers == {"Authorization": f"Bearer {token}"}


def test_meta_oauth_can_begin_before_research_or_funding() -> None:
    store = get_runtime_store()
    preview = _preview()

    service = CustomerMetaOAuthService(store=store, settings=_settings())
    authorization_url = service.begin(preview.project_id, preview.customer_token)
    query = parse_qs(urlsplit(authorization_url).query)
    state = query["state"][0]

    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    assert project["research_state"] == "NOT_STARTED"
    assert project["product_id"] is None
    assert project["launch_unlocked"] is False
    assert len(state) >= 32
    assert query["scope"] == ["ads_management,ads_read"]
    persisted = store.list_namespace(CUSTOMER_META_OAUTH_STATE_NAMESPACE)
    assert len(persisted) >= 1
    assert all(state not in str(item) for item in persisted)
    assert service.pending_context(state) == (preview.project_id, "/start")


def test_meta_oauth_state_preserves_workspace_return_without_open_redirect() -> None:
    preview = _preview()
    service = CustomerMetaOAuthService(store=get_runtime_store(), settings=_settings())

    authorization_url = service.begin(
        preview.project_id,
        preview.customer_token,
        return_path="/workspace",
    )
    state = parse_qs(urlsplit(authorization_url).query)["state"][0]

    assert service.pending_context(state) == (preview.project_id, "/workspace")
    try:
        service.begin(
            preview.project_id,
            preview.customer_token,
            return_path="https://evil.example/steal",
        )
    except CustomerMetaOAuthError as exc:
        assert "return path" in str(exc)
    else:
        raise AssertionError("Meta OAuth must reject arbitrary return URLs")


def test_meta_oauth_callback_returns_autonomous_customer_to_workspace(monkeypatch) -> None:
    preview = _preview()
    monkeypatch.setattr(
        "app.customer_routes.customer_meta_oauth_service.complete_with_return",
        lambda **kwargs: (preview.project_id, "/workspace"),
    )

    response = client.get(
        "/v1/customer-meta/oauth/callback?state=workspace-state&code=meta-code",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/workspace?meta=connected&project={preview.project_id}"
    )


def test_meta_oauth_error_returns_to_workspace_when_state_has_workspace_context(
    monkeypatch,
) -> None:
    preview = _preview()
    monkeypatch.setattr(
        "app.customer_routes.customer_meta_oauth_service.pending_context",
        lambda state: (preview.project_id, "/workspace"),
    )

    response = client.get(
        "/v1/customer-meta/oauth/callback?state=workspace-state&error=access_denied",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/workspace?meta=error&project={preview.project_id}"


def test_meta_account_is_staged_until_research_creates_product() -> None:
    store = get_runtime_store()
    preview = _preview()
    settings = _settings()
    secret_store = ProviderSecretStore(store=store, settings=settings)
    secret_reference = secret_store.create_reference()
    secret_store.put(secret_reference, "EAAB-staged-token-not-real")
    store.put(
        CUSTOMER_META_PENDING_NAMESPACE,
        str(preview.project_id),
        {
            "project_id": str(preview.project_id),
            "secret_reference": secret_reference,
            "ad_accounts": [
                {
                    "id": "act_123",
                    "account_id": "123",
                    "name": "Partizan test account",
                    "currency": "USD",
                }
            ],
            "pages_by_ad_account": {
                "123": [{"id": "page_1", "name": "Partizan test page"}]
            },
        },
    )
    service = CustomerMetaOAuthService(
        store=store,
        settings=settings,
        secret_store=secret_store,
    )

    service.connect(
        preview.project_id,
        preview.customer_token,
        CustomerMetaConnectionRequest(
            ad_account_id="123",
            page_id="page_1",
            country_codes=["us"],
        ),
    )

    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    staged = project[STAGED_META_CONNECTION_KEY]
    assert staged["ad_account_id"] == "123"
    assert staged["page_id"] == "page_1"
    assert staged["country_codes"] == ["US"]
    assert staged["access_token_env"] == secret_reference
    assert service.options(preview.project_id, preview.customer_token).connected_to_meta is True
    assert store.get(CUSTOMER_META_PENDING_NAMESPACE, str(preview.project_id)) is None

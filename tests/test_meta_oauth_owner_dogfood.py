from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.customer_funnel import customer_funnel_service
from app.customer_meta_oauth import (
    CUSTOMER_META_OAUTH_STATE_NAMESPACE,
    CustomerMetaOAuthError,
    CustomerMetaOAuthService,
)


class _Store:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], dict] = {}

    def put(self, namespace: str, key: str, value: dict) -> None:
        self.data[(namespace, key)] = dict(value)

    def get(self, namespace: str, key: str) -> dict | None:
        value = self.data.get((namespace, key))
        return dict(value) if value is not None else None


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        partizan_public_base_url="https://partizan.example.com",
        meta_oauth_app_id="1234567890",
        meta_oauth_app_secret="meta-secret-not-real",
        meta_oauth_api_version="v26.0",
        meta_oauth_public_ready=False,
        provider_secret_encryption_key=Fernet.generate_key().decode("ascii"),
    )


def test_allowlisted_project_can_start_owner_dogfood_oauth(monkeypatch) -> None:
    project_id = uuid4()
    monkeypatch.setenv(
        "META_OAUTH_DOGFOOD_PROJECT_IDS",
        f"not-a-uuid, {project_id}",
    )
    monkeypatch.setattr(
        customer_funnel_service,
        "get_project_payload",
        lambda _project_id, _customer_token: {},
    )
    store = _Store()
    service = CustomerMetaOAuthService(store=store, settings=_settings())

    authorization_url = service.begin(
        project_id,
        "customer-token",
        return_path="/workspace",
    )

    query = parse_qs(urlsplit(authorization_url).query)
    assert query["scope"] == ["ads_management,ads_read"]
    assert query["redirect_uri"] == [
        "https://partizan.example.com/v1/customer-meta/oauth/callback"
    ]
    states = [
        value
        for (namespace, _), value in store.data.items()
        if namespace == CUSTOMER_META_OAUTH_STATE_NAMESPACE
    ]
    assert len(states) == 1
    assert states[0]["project_id"] == str(project_id)
    assert states[0]["return_path"] == "/workspace"


def test_non_allowlisted_project_stays_blocked_when_public_oauth_is_off(monkeypatch) -> None:
    allowed_project_id = uuid4()
    blocked_project_id = uuid4()
    monkeypatch.setenv("META_OAUTH_DOGFOOD_PROJECT_IDS", str(allowed_project_id))
    monkeypatch.setattr(
        customer_funnel_service,
        "get_project_payload",
        lambda _project_id, _customer_token: {},
    )
    store = _Store()
    service = CustomerMetaOAuthService(store=store, settings=_settings())

    with pytest.raises(CustomerMetaOAuthError, match="temporarily unavailable"):
        service.begin(blocked_project_id, "customer-token")

    assert not any(
        namespace == CUSTOMER_META_OAUTH_STATE_NAMESPACE
        for namespace, _ in store.data
    )

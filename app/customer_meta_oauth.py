from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID

import httpx

from app.config import Settings, get_settings
from app.customer_autopilot import customer_autopilot_service
from app.customer_funnel import customer_funnel_service
from app.customer_schemas import (
    CustomerMetaAdAccountOption,
    CustomerMetaConnectionRequest,
    CustomerMetaOptionsView,
    CustomerMetaPageOption,
)
from app.paid_provider_connections import (
    PaidProviderConnectionCreateRequest,
    paid_provider_connection_service,
)
from app.provider_secret_store import ProviderSecretStore, provider_secret_store
from app.runtime_store import RuntimeStateStore, get_runtime_store

CUSTOMER_META_OAUTH_STATE_NAMESPACE = "customer_meta_oauth_state"
CUSTOMER_META_PENDING_NAMESPACE = "customer_meta_pending"


class CustomerMetaOAuthError(RuntimeError):
    pass


class MetaOAuthClient(Protocol):
    def exchange_code(self, *, code: str, redirect_uri: str) -> str: ...

    def extend_token(self, short_lived_token: str) -> str: ...

    def ad_accounts(self, access_token: str) -> list[dict]: ...

    def promote_pages(self, access_token: str, account_id: str) -> list[dict]: ...


class HttpxMetaOAuthClient:
    def __init__(self, settings: Settings | None = None, timeout_seconds: float = 20.0) -> None:
        self._settings = settings or get_settings()
        self._timeout = timeout_seconds

    def exchange_code(self, *, code: str, redirect_uri: str) -> str:
        payload = self._get(
            "oauth/access_token",
            params={
                "client_id": self._app_id(),
                "client_secret": self._app_secret(),
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        token = str(payload.get("access_token") or "")
        if not token:
            raise CustomerMetaOAuthError("Meta did not return an access token")
        return token

    def extend_token(self, short_lived_token: str) -> str:
        payload = self._get(
            "oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self._app_id(),
                "client_secret": self._app_secret(),
                "fb_exchange_token": short_lived_token,
            },
        )
        token = str(payload.get("access_token") or "")
        if not token:
            raise CustomerMetaOAuthError("Meta did not return a long-lived access token")
        return token

    def ad_accounts(self, access_token: str) -> list[dict]:
        payload = self._get(
            "me/adaccounts",
            params={
                "fields": "id,account_id,name,currency,account_status",
                "limit": "50",
            },
            bearer_token=access_token,
        )
        return [row for row in payload.get("data", []) if isinstance(row, dict)]

    def promote_pages(self, access_token: str, account_id: str) -> list[dict]:
        normalized = account_id.removeprefix("act_")
        payload = self._get(
            f"act_{normalized}",
            params={"fields": "promote_pages"},
            bearer_token=access_token,
        )
        pages = payload.get("promote_pages") or {}
        if isinstance(pages, dict):
            rows = pages.get("data", [])
        elif isinstance(pages, list):
            rows = pages
        else:
            rows = []
        return [row for row in rows if isinstance(row, dict)]

    def _get(
        self,
        path: str,
        *,
        params: dict[str, str],
        bearer_token: str | None = None,
    ) -> dict:
        version = self._settings.meta_oauth_api_version
        if not version:
            raise CustomerMetaOAuthError("META_OAUTH_API_VERSION is not configured")
        url = f"https://graph.facebook.com/{version}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else None
        try:
            response = httpx.get(
                url,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CustomerMetaOAuthError("Meta OAuth/API request failed") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise CustomerMetaOAuthError("Meta OAuth/API request was rejected")
        return payload

    def _app_id(self) -> str:
        value = self._settings.meta_oauth_app_id
        if not value:
            raise CustomerMetaOAuthError("META_OAUTH_APP_ID is not configured")
        return value

    def _app_secret(self) -> str:
        value = self._settings.meta_oauth_app_secret
        if value is None:
            raise CustomerMetaOAuthError("META_OAUTH_APP_SECRET is not configured")
        return value.get_secret_value()


class CustomerMetaOAuthService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
        client: MetaOAuthClient | None = None,
        secret_store: ProviderSecretStore | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()
        self._client = client or HttpxMetaOAuthClient(self._settings)
        self._secret_store = secret_store or provider_secret_store

    def begin(self, project_id: UUID, customer_token: str) -> str:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        if project.get("autopilot_subscription_status") != "ACTIVE":
            raise CustomerMetaOAuthError("Activate Autopilot before connecting Meta")
        if project.get("research_state") != "READY" or not project.get("product_id"):
            raise CustomerMetaOAuthError("Complete acquisition research before connecting Meta")
        redirect_uri = self._redirect_uri()
        app_id = self._settings.meta_oauth_app_id
        version = self._settings.meta_oauth_api_version
        if not app_id or not version or self._settings.meta_oauth_app_secret is None:
            raise CustomerMetaOAuthError("Meta OAuth is not configured")
        if self._settings.provider_secret_encryption_key is None:
            raise CustomerMetaOAuthError("Encrypted provider secret storage is not configured")

        state = secrets.token_urlsafe(32)
        state_key = self._state_key(state)
        now = datetime.now(UTC)
        self._store.put(
            CUSTOMER_META_OAUTH_STATE_NAMESPACE,
            state_key,
            {
                "project_id": str(project_id),
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=15)).isoformat(),
                "used": False,
            },
        )
        query = urlencode(
            {
                "client_id": app_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "response_type": "code",
                "scope": "ads_management,ads_read",
            }
        )
        return f"https://www.facebook.com/{version}/dialog/oauth?{query}"

    def complete(self, *, state: str, code: str) -> UUID:
        state_key = self._state_key(state)
        record = self._store.get(CUSTOMER_META_OAUTH_STATE_NAMESPACE, state_key)
        if record is None or record.get("used"):
            raise CustomerMetaOAuthError("Meta OAuth state is invalid or already used")
        expires_at = datetime.fromisoformat(str(record["expires_at"]))
        if self._as_utc(expires_at) <= datetime.now(UTC):
            raise CustomerMetaOAuthError("Meta OAuth state has expired")
        record["used"] = True
        self._store.put(CUSTOMER_META_OAUTH_STATE_NAMESPACE, state_key, record)

        project_id = UUID(str(record["project_id"]))
        short_token = self._client.exchange_code(code=code, redirect_uri=self._redirect_uri())
        access_token = self._client.extend_token(short_token)
        secret_reference = self._secret_store.create_reference()
        self._secret_store.put(secret_reference, access_token)

        accounts: list[CustomerMetaAdAccountOption] = []
        pages_by_account: dict[str, list[CustomerMetaPageOption]] = {}
        for raw in self._client.ad_accounts(access_token)[:20]:
            account_id = str(raw.get("account_id") or raw.get("id") or "").removeprefix(
                "act_"
            )
            if not account_id:
                continue
            option = CustomerMetaAdAccountOption(
                id=str(raw.get("id") or f"act_{account_id}"),
                account_id=account_id,
                name=str(raw.get("name") or f"Ad account {account_id}"),
                currency=str(raw.get("currency")) if raw.get("currency") else None,
            )
            accounts.append(option)
            pages: list[CustomerMetaPageOption] = []
            for page in self._client.promote_pages(access_token, account_id)[:25]:
                page_id = str(page.get("id") or "")
                if page_id:
                    pages.append(
                        CustomerMetaPageOption(
                            id=page_id,
                            name=str(page.get("name") or f"Page {page_id}"),
                        )
                    )
            pages_by_account[account_id] = pages

        self._store.put(
            CUSTOMER_META_PENDING_NAMESPACE,
            str(project_id),
            {
                "project_id": str(project_id),
                "secret_reference": secret_reference,
                "ad_accounts": [item.model_dump(mode="json") for item in accounts],
                "pages_by_ad_account": {
                    key: [item.model_dump(mode="json") for item in values]
                    for key, values in pages_by_account.items()
                },
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        return project_id

    def options(self, project_id: UUID, customer_token: str) -> CustomerMetaOptionsView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        pending = self._store.get(CUSTOMER_META_PENDING_NAMESPACE, str(project_id))
        connection = self._connection_for_project(project_id)
        if pending is None:
            return CustomerMetaOptionsView(connected_to_meta=connection is not None)
        return CustomerMetaOptionsView(
            connected_to_meta=connection is not None,
            ad_accounts=[
                CustomerMetaAdAccountOption.model_validate(item)
                for item in pending.get("ad_accounts", [])
            ],
            pages_by_ad_account={
                key: [CustomerMetaPageOption.model_validate(item) for item in values]
                for key, values in pending.get("pages_by_ad_account", {}).items()
            },
        )

    def connect(
        self,
        project_id: UUID,
        customer_token: str,
        payload: CustomerMetaConnectionRequest,
    ) -> None:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        if project.get("autopilot_subscription_status") != "ACTIVE":
            raise CustomerMetaOAuthError("Autopilot subscription is not active")
        product_id_raw = project.get("product_id")
        if not product_id_raw:
            raise CustomerMetaOAuthError("Customer project has no researched product")
        pending = self._store.get(CUSTOMER_META_PENDING_NAMESPACE, str(project_id))
        if pending is None:
            raise CustomerMetaOAuthError("Connect Meta first to load available ad accounts")
        accounts = {
            str(item.get("account_id")): item
            for item in pending.get("ad_accounts", [])
            if isinstance(item, dict)
        }
        if payload.ad_account_id not in accounts:
            raise CustomerMetaOAuthError("Selected Meta ad account was not returned by OAuth")
        pages = {
            str(item.get("id")): item
            for item in pending.get("pages_by_ad_account", {}).get(payload.ad_account_id, [])
            if isinstance(item, dict)
        }
        if payload.page_id not in pages:
            raise CustomerMetaOAuthError("Selected Facebook Page is not available to this ad account")
        secret_reference = str(pending.get("secret_reference") or "")
        if not secret_reference or self._secret_store.get(secret_reference) is None:
            raise CustomerMetaOAuthError("Meta access token is no longer available")
        version = self._settings.meta_oauth_api_version
        if not version:
            raise CustomerMetaOAuthError("META_OAUTH_API_VERSION is not configured")

        product_id = UUID(str(product_id_raw))
        previous = paid_provider_connection_service.get_meta(product_id)
        paid_provider_connection_service.upsert_meta(
            product_id,
            PaidProviderConnectionCreateRequest(
                ad_account_id=payload.ad_account_id,
                page_id=payload.page_id,
                instagram_actor_id=payload.instagram_actor_id,
                access_token_env=secret_reference,
                api_version=version,
                country_codes=payload.country_codes,
                default_image_url=None,
            ),
        )
        if previous is not None and previous.access_token_env != secret_reference:
            try:
                self._secret_store.delete(previous.access_token_env)
            except Exception:
                pass
        self._store.delete(CUSTOMER_META_PENDING_NAMESPACE, str(project_id))
        customer_autopilot_service.meta_connected(project_id, customer_token)

    def _connection_for_project(self, project_id: UUID):
        project = self._store.get("customer_acquisition_projects", str(project_id))
        if project is None or not project.get("product_id"):
            return None
        return paid_provider_connection_service.get_meta(UUID(str(project["product_id"])))

    def _redirect_uri(self) -> str:
        origin = self._settings.partizan_public_base_url
        if not origin:
            raise CustomerMetaOAuthError("PARTIZAN_PUBLIC_BASE_URL is required for Meta OAuth")
        return f"{origin}/v1/customer-meta/oauth/callback"

    @staticmethod
    def _state_key(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


customer_meta_oauth_service = CustomerMetaOAuthService()

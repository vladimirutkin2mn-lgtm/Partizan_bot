from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, cast
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.config import Settings, get_settings
from app.customer_funnel import customer_funnel_service
from app.customer_meta_oauth import (
    CUSTOMER_META_OAUTH_STATE_NAMESPACE,
    CUSTOMER_META_PENDING_NAMESPACE,
    META_OAUTH_SCOPES,
    CustomerMetaOAuthError,
    HttpxMetaOAuthClient,
)
from app.provider_secret_store import ProviderSecretStore, provider_secret_store
from app.runtime_store import RuntimeStateStore, get_runtime_store

CUSTOMER_META_GUIDED_SETUP_NAMESPACE = "customer_meta_guided_setup"
META_GUIDED_SETUP_RETURN_PATH = "/workspace"
META_GUIDED_STATE_TTL_MINUTES = 15
META_GUIDED_MAX_BUSINESSES = 5

MetaGuidedStatus = Literal[
    "NOT_STARTED",
    "NO_META_BUSINESS",
    "BUSINESS_NEEDS_AD_ACCOUNT",
    "AD_ACCOUNT_NEEDS_PAGE",
    "PAGE_NEEDS_AD_ACCOUNT_ACCESS",
    "READY",
    "REAUTHORIZE_REQUIRED",
]
_META_GUIDED_STATUSES = frozenset(
    {
        "NOT_STARTED",
        "NO_META_BUSINESS",
        "BUSINESS_NEEDS_AD_ACCOUNT",
        "AD_ACCOUNT_NEEDS_PAGE",
        "PAGE_NEEDS_AD_ACCOUNT_ACCESS",
        "READY",
        "REAUTHORIZE_REQUIRED",
    }
)


class CustomerMetaGuidedConnectResponse(BaseModel):
    authorization_url: HttpUrl


class CustomerMetaGuidedSetupView(BaseModel):
    status: MetaGuidedStatus
    message: str
    business_count: int = Field(default=0, ge=0)
    ad_account_count: int = Field(default=0, ge=0)
    business_page_count: int = Field(default=0, ge=0)
    promotable_page_count: int = Field(default=0, ge=0)
    business_names: list[str] = Field(default_factory=list, max_length=5)
    business_page_names: list[str] = Field(default_factory=list, max_length=5)
    primary_url: HttpUrl | None = None
    secondary_url: HttpUrl | None = None
    can_check_again: bool = False


class GuidedMetaOAuthClient(Protocol):
    def exchange_code(self, *, code: str, redirect_uri: str) -> str: ...

    def extend_token(self, short_lived_token: str) -> str: ...

    def businesses(self, access_token: str) -> list[dict]: ...

    def business_pages(self, access_token: str, business_id: str) -> list[dict]: ...

    def managed_pages(self, access_token: str) -> list[dict]: ...

    def ad_accounts(self, access_token: str) -> list[dict]: ...

    def promote_pages(self, access_token: str, account_id: str) -> list[dict]: ...


class HttpxGuidedMetaOAuthClient(HttpxMetaOAuthClient):
    def businesses(self, access_token: str) -> list[dict]:
        payload = self._get(
            "me/businesses",
            params={"fields": "id,name", "limit": str(META_GUIDED_MAX_BUSINESSES)},
            bearer_token=access_token,
        )
        return [
            item for item in payload.get("data", []) if isinstance(item, dict)
        ][:META_GUIDED_MAX_BUSINESSES]

    def business_pages(self, access_token: str, business_id: str) -> list[dict]:
        rows: list[dict] = []
        seen: set[str] = set()
        for edge in ("owned_pages", "client_pages"):
            try:
                payload = self._get(
                    f"{business_id}/{edge}",
                    params={"fields": "id,name", "limit": "50"},
                    bearer_token=access_token,
                )
            except CustomerMetaOAuthError:
                continue
            for item in payload.get("data", []):
                if not isinstance(item, dict):
                    continue
                page_id = str(item.get("id") or "")
                if not page_id or page_id in seen:
                    continue
                seen.add(page_id)
                rows.append(item)
        return rows[:50]

    def managed_pages(self, access_token: str) -> list[dict]:
        payload = self._get(
            "me/accounts",
            params={"fields": "id,name,tasks", "limit": "50"},
            bearer_token=access_token,
        )
        return [
            item for item in payload.get("data", []) if isinstance(item, dict)
        ][:50]


class CustomerMetaGuidedSetupService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
        client: GuidedMetaOAuthClient | None = None,
        secret_store: ProviderSecretStore | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()
        self._client = client or HttpxGuidedMetaOAuthClient(self._settings)
        self._secret_store = secret_store or provider_secret_store

    def begin(self, project_id: UUID, customer_token: str) -> str:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        if not self._project_enabled(project_id):
            raise CustomerMetaOAuthError(
                "Facebook & Instagram connection is temporarily unavailable while "
                "Partizan's Meta app is being activated for customer access"
            )
        if not self._settings.meta_oauth_app_id or not self._settings.meta_oauth_api_version:
            raise CustomerMetaOAuthError("Meta OAuth is not configured")
        if self._settings.meta_oauth_app_secret is None:
            raise CustomerMetaOAuthError("Meta OAuth is not configured")
        if self._settings.provider_secret_encryption_key is None:
            raise CustomerMetaOAuthError(
                "Encrypted provider secret storage is not configured"
            )

        self._clear_guided_setup(project_id)
        state = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        self._store.put(
            CUSTOMER_META_OAUTH_STATE_NAMESPACE,
            self._state_key(state),
            {
                "project_id": str(project_id),
                "return_path": META_GUIDED_SETUP_RETURN_PATH,
                "created_at": now.isoformat(),
                "expires_at": (
                    now + timedelta(minutes=META_GUIDED_STATE_TTL_MINUTES)
                ).isoformat(),
                "used": False,
                "guided_setup": True,
            },
        )
        query = urlencode(
            {
                "client_id": self._settings.meta_oauth_app_id,
                "redirect_uri": self._redirect_uri(),
                "state": state,
                "response_type": "code",
                "scope": ",".join(META_OAUTH_SCOPES),
            }
        )
        return (
            f"https://www.facebook.com/{self._settings.meta_oauth_api_version}"
            f"/dialog/oauth?{query}"
        )

    def complete_with_return(self, *, state: str, code: str) -> tuple[UUID, str]:
        record = self._state_record(state)
        if record is None or record.get("used") or not record.get("guided_setup"):
            raise CustomerMetaOAuthError("Meta OAuth state is invalid or already used")
        expires_at = datetime.fromisoformat(str(record["expires_at"]))
        if self._as_utc(expires_at) <= datetime.now(UTC):
            raise CustomerMetaOAuthError("Meta OAuth state has expired")

        project_id = UUID(str(record["project_id"]))
        record["used"] = True
        self._store.put(
            CUSTOMER_META_OAUTH_STATE_NAMESPACE,
            self._state_key(state),
            record,
        )
        short_token = self._client.exchange_code(
            code=code,
            redirect_uri=self._redirect_uri(),
        )
        access_token = self._client.extend_token(short_token)
        secret_reference = self._secret_store.create_reference()
        self._secret_store.put(secret_reference, access_token)

        try:
            self._run_preflight(project_id, secret_reference, access_token)
        except Exception:
            self._safe_delete_secret(secret_reference)
            raise
        return project_id, META_GUIDED_SETUP_RETURN_PATH

    def view(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> CustomerMetaGuidedSetupView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        record = self._store.get(
            CUSTOMER_META_GUIDED_SETUP_NAMESPACE,
            str(project_id),
        )
        if not isinstance(record, dict):
            return CustomerMetaGuidedSetupView(
                status="NOT_STARTED",
                message="Connect Meta to check Business, ad account and Page access.",
            )
        return self._view_from_record(record)

    def check(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> CustomerMetaGuidedSetupView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        record = self._store.get(
            CUSTOMER_META_GUIDED_SETUP_NAMESPACE,
            str(project_id),
        )
        if not isinstance(record, dict):
            return CustomerMetaGuidedSetupView(
                status="NOT_STARTED",
                message="Connect Meta first so Partizan can check your setup.",
            )

        secret_reference = str(record.get("secret_reference") or "")
        access_token = (
            self._secret_store.get(secret_reference) if secret_reference else None
        )
        if not access_token:
            return self._mark_reauthorization_required(project_id, record)
        try:
            return self._run_preflight(project_id, secret_reference, access_token)
        except CustomerMetaOAuthError:
            self._safe_delete_secret(secret_reference)
            return self._mark_reauthorization_required(project_id, record)

    def is_guided_state(self, state: str) -> bool:
        record = self._state_record(state)
        return bool(isinstance(record, dict) and record.get("guided_setup"))

    def _run_preflight(
        self,
        project_id: UUID,
        secret_reference: str,
        access_token: str,
    ) -> CustomerMetaGuidedSetupView:
        try:
            businesses = self._client.businesses(access_token)
        except CustomerMetaOAuthError:
            businesses = []
        accounts = self._client.ad_accounts(access_token)[:20]
        business_names = [
            str(item.get("name") or "Meta Business").strip() or "Meta Business"
            for item in businesses[:5]
        ]

        business_pages: list[dict] = []
        seen_business_pages: set[str] = set()
        for business in businesses[:META_GUIDED_MAX_BUSINESSES]:
            business_id = str(business.get("id") or "")
            if not business_id:
                continue
            try:
                pages = self._client.business_pages(access_token, business_id)
            except CustomerMetaOAuthError:
                continue
            for page in pages:
                page_id = str(page.get("id") or "")
                if not page_id or page_id in seen_business_pages:
                    continue
                seen_business_pages.add(page_id)
                business_pages.append(page)
        business_page_names = [
            str(item.get("name") or f"Page {item.get('id') or ''}").strip()
            for item in business_pages[:5]
        ]

        try:
            managed_pages = self._client.managed_pages(access_token)
        except CustomerMetaOAuthError:
            managed_pages = []
        business_page_ids = {
            str(item.get("id") or "") for item in business_pages if item.get("id")
        }
        managed_advertisable_pages = [
            page
            for page in managed_pages
            if str(page.get("id") or "") in business_page_ids
            and "ADVERTISE"
            in {
                str(task).strip().upper()
                for task in (page.get("tasks") or [])
                if str(task).strip()
            }
        ]

        normalized_accounts: list[dict] = []
        pages_by_account: dict[str, list[dict]] = {}
        for raw in accounts:
            account_id = str(
                raw.get("account_id") or raw.get("id") or ""
            ).removeprefix("act_")
            if not account_id:
                continue
            try:
                pages = self._client.promote_pages(access_token, account_id)[:25]
            except CustomerMetaOAuthError:
                pages = []
            pages_by_account[account_id] = [
                {
                    "id": str(page.get("id") or ""),
                    "name": str(
                        page.get("name") or f"Page {page.get('id') or ''}"
                    ),
                }
                for page in pages
                if str(page.get("id") or "")
            ]
            normalized_accounts.append(
                {
                    "id": str(raw.get("id") or f"act_{account_id}"),
                    "account_id": account_id,
                    "name": str(
                        raw.get("name") or f"Ad account {account_id}"
                    ),
                    "currency": (
                        str(raw.get("currency")) if raw.get("currency") else None
                    ),
                }
            )

        if (
            normalized_accounts
            and not any(pages_by_account.values())
            and managed_advertisable_pages
        ):
            fallback_pages = [
                {
                    "id": str(page.get("id") or ""),
                    "name": str(
                        page.get("name") or f"Page {page.get('id') or ''}"
                    ),
                }
                for page in managed_advertisable_pages[:25]
                if str(page.get("id") or "")
            ]
            for account in normalized_accounts:
                pages_by_account[str(account["account_id"])] = list(fallback_pages)

        promotable_page_count = sum(len(items) for items in pages_by_account.values())
        if normalized_accounts and promotable_page_count:
            eligible_accounts = [
                item
                for item in normalized_accounts
                if pages_by_account.get(str(item["account_id"]))
            ]
            eligible_pages = {
                str(item["account_id"]): pages_by_account[str(item["account_id"])]
                for item in eligible_accounts
            }
            self._promote_to_standard_pending(
                project_id,
                secret_reference,
                eligible_accounts,
                eligible_pages,
            )
            self._store.delete(
                CUSTOMER_META_GUIDED_SETUP_NAMESPACE,
                str(project_id),
            )
            return CustomerMetaGuidedSetupView(
                status="READY",
                message=(
                    "Meta is ready. Choose the ad account and Facebook Page "
                    "Partizan should use."
                ),
                business_count=len(businesses),
                ad_account_count=len(eligible_accounts),
                business_page_count=len(business_pages),
                promotable_page_count=sum(
                    len(items) for items in eligible_pages.values()
                ),
                business_names=business_names,
                business_page_names=business_page_names,
            )

        if normalized_accounts and business_pages:
            status: MetaGuidedStatus = "PAGE_NEEDS_AD_ACCOUNT_ACCESS"
        elif normalized_accounts:
            status = "AD_ACCOUNT_NEEDS_PAGE"
        elif businesses:
            status = "BUSINESS_NEEDS_AD_ACCOUNT"
        else:
            status = "NO_META_BUSINESS"
        record = {
            "project_id": str(project_id),
            "secret_reference": secret_reference,
            "status": status,
            "business_count": len(businesses),
            "ad_account_count": len(normalized_accounts),
            "business_page_count": len(business_pages),
            "promotable_page_count": promotable_page_count,
            "business_names": business_names,
            "business_page_names": business_page_names,
            "checked_at": datetime.now(UTC).isoformat(),
        }
        self._replace_guided_setup(project_id, record)
        return self._view_from_record(record)

    def _promote_to_standard_pending(
        self,
        project_id: UUID,
        secret_reference: str,
        accounts: list[dict],
        pages_by_account: dict[str, list[dict]],
    ) -> None:
        previous = self._store.get(CUSTOMER_META_PENDING_NAMESPACE, str(project_id))
        previous_ref = (
            str(previous.get("secret_reference") or "")
            if isinstance(previous, dict)
            else ""
        )
        self._store.put(
            CUSTOMER_META_PENDING_NAMESPACE,
            str(project_id),
            {
                "project_id": str(project_id),
                "secret_reference": secret_reference,
                "ad_accounts": accounts,
                "pages_by_ad_account": pages_by_account,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        if previous_ref and previous_ref != secret_reference:
            self._safe_delete_secret(previous_ref)

    def _replace_guided_setup(self, project_id: UUID, record: dict) -> None:
        previous = self._store.get(
            CUSTOMER_META_GUIDED_SETUP_NAMESPACE,
            str(project_id),
        )
        previous_ref = (
            str(previous.get("secret_reference") or "")
            if isinstance(previous, dict)
            else ""
        )
        self._store.put(
            CUSTOMER_META_GUIDED_SETUP_NAMESPACE,
            str(project_id),
            record,
        )
        current_ref = str(record.get("secret_reference") or "")
        if previous_ref and previous_ref != current_ref:
            self._safe_delete_secret(previous_ref)

    def _mark_reauthorization_required(
        self,
        project_id: UUID,
        previous: dict,
    ) -> CustomerMetaGuidedSetupView:
        record = {
            "project_id": str(project_id),
            "status": "REAUTHORIZE_REQUIRED",
            "business_count": int(previous.get("business_count") or 0),
            "ad_account_count": int(previous.get("ad_account_count") or 0),
            "business_page_count": int(previous.get("business_page_count") or 0),
            "promotable_page_count": int(
                previous.get("promotable_page_count") or 0
            ),
            "business_names": list(previous.get("business_names") or [])[:5],
            "business_page_names": list(
                previous.get("business_page_names") or []
            )[:5],
            "checked_at": datetime.now(UTC).isoformat(),
        }
        self._store.put(
            CUSTOMER_META_GUIDED_SETUP_NAMESPACE,
            str(project_id),
            record,
        )
        return self._view_from_record(record)

    def _clear_guided_setup(self, project_id: UUID) -> None:
        previous = self._store.get(
            CUSTOMER_META_GUIDED_SETUP_NAMESPACE,
            str(project_id),
        )
        if isinstance(previous, dict):
            reference = str(previous.get("secret_reference") or "")
            if reference:
                self._safe_delete_secret(reference)
        self._store.delete(CUSTOMER_META_GUIDED_SETUP_NAMESPACE, str(project_id))

    @staticmethod
    def _view_from_record(record: dict) -> CustomerMetaGuidedSetupView:
        raw_status = str(record.get("status") or "NOT_STARTED")
        status = cast(
            MetaGuidedStatus,
            raw_status if raw_status in _META_GUIDED_STATUSES else "NOT_STARTED",
        )
        messages = {
            "NO_META_BUSINESS": (
                "Partizan could not find a Meta Business Portfolio for this Facebook "
                "profile. Create or open your Business setup, then come back and check again."
            ),
            "BUSINESS_NEEDS_AD_ACCOUNT": (
                "Meta Business was found, but this Facebook profile has no manageable "
                "ad account. If you already use Ads Manager, assign yourself to the ad "
                "account; otherwise create one."
            ),
            "AD_ACCOUNT_NEEDS_PAGE": (
                "An ad account was found, but Partizan could not find a Facebook Page "
                "inside the connected Meta Business Portfolio. Add or assign a Page, "
                "then check again."
            ),
            "PAGE_NEEDS_AD_ACCOUNT_ACCESS": (
                "A Facebook Page exists in the connected Meta Business Portfolio, but "
                "Meta does not return it as available for promotion from the ad account. "
                "Do not create another Page. Check Page and ad-account access, then try again."
            ),
            "REAUTHORIZE_REQUIRED": (
                "The saved Meta authorization can no longer be checked. Connect Meta "
                "again to refresh access."
            ),
        }
        primary_urls = {
            "NO_META_BUSINESS": "https://business.facebook.com/reg/",
            "BUSINESS_NEEDS_AD_ACCOUNT": (
                "https://business.facebook.com/settings/ad-accounts"
            ),
            "AD_ACCOUNT_NEEDS_PAGE": "https://business.facebook.com/settings/pages",
            "PAGE_NEEDS_AD_ACCOUNT_ACCESS": (
                "https://business.facebook.com/settings/pages"
            ),
            "REAUTHORIZE_REQUIRED": None,
        }
        secondary_urls = {
            "BUSINESS_NEEDS_AD_ACCOUNT": (
                "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
            ),
            "PAGE_NEEDS_AD_ACCOUNT_ACCESS": (
                "https://business.facebook.com/settings/ad-accounts"
            ),
        }
        return CustomerMetaGuidedSetupView(
            status=status,
            message=messages.get(
                status,
                "Connect Meta to check Business, ad account and Page access.",
            ),
            business_count=int(record.get("business_count") or 0),
            ad_account_count=int(record.get("ad_account_count") or 0),
            business_page_count=int(record.get("business_page_count") or 0),
            promotable_page_count=int(record.get("promotable_page_count") or 0),
            business_names=[
                str(item) for item in record.get("business_names", [])
            ][:5],
            business_page_names=[
                str(item) for item in record.get("business_page_names", [])
            ][:5],
            primary_url=primary_urls.get(status),
            secondary_url=secondary_urls.get(status),
            can_check_again=status
            in {
                "NO_META_BUSINESS",
                "BUSINESS_NEEDS_AD_ACCOUNT",
                "AD_ACCOUNT_NEEDS_PAGE",
                "PAGE_NEEDS_AD_ACCOUNT_ACCESS",
            },
        )

    def _state_record(self, state: str) -> dict | None:
        return self._store.get(
            CUSTOMER_META_OAUTH_STATE_NAMESPACE,
            self._state_key(state),
        )

    def _project_enabled(self, project_id: UUID) -> bool:
        if self._settings.meta_oauth_public_ready:
            return True
        for item in os.getenv("META_OAUTH_DOGFOOD_PROJECT_IDS", "").split(","):
            try:
                if str(UUID(item.strip())) == str(project_id):
                    return True
            except ValueError:
                continue
        return False

    def _redirect_uri(self) -> str:
        origin = self._settings.partizan_public_base_url
        if not origin:
            raise CustomerMetaOAuthError(
                "PARTIZAN_PUBLIC_BASE_URL is required for Meta OAuth"
            )
        return f"{origin}/v1/customer-meta/oauth/callback"

    @staticmethod
    def _state_key(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _safe_delete_secret(self, reference: str) -> None:
        try:
            self._secret_store.delete(reference)
        except Exception:
            pass


customer_meta_guided_setup_service = CustomerMetaGuidedSetupService()


def install_guided_meta_oauth_completion() -> None:
    from app.customer_meta_oauth import customer_meta_oauth_service

    if getattr(customer_meta_oauth_service, "_guided_setup_installed", False):
        return
    original_complete = customer_meta_oauth_service.complete_with_return

    def complete_with_guided(*, state: str, code: str) -> tuple[UUID, str]:
        if customer_meta_guided_setup_service.is_guided_state(state):
            return customer_meta_guided_setup_service.complete_with_return(
                state=state,
                code=code,
            )
        return original_complete(state=state, code=code)

    customer_meta_oauth_service.complete_with_return = complete_with_guided  # type: ignore[method-assign]
    customer_meta_oauth_service._guided_setup_installed = True  # type: ignore[attr-defined]

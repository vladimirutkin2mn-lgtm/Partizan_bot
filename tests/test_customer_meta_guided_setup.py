from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet

from app.config import Settings
from app.customer_funnel import customer_funnel_service
from app.customer_meta_guided_setup import (
    CUSTOMER_META_GUIDED_SETUP_NAMESPACE,
    CustomerMetaGuidedSetupService,
)
from app.customer_meta_oauth import CUSTOMER_META_PENDING_NAMESPACE
from app.customer_schemas import CustomerPreviewRequest
from app.provider_secret_store import ProviderSecretStore
from app.runtime_store import get_runtime_store


class _GuidedMetaStub:
    def __init__(
        self,
        *,
        businesses: list[dict] | None = None,
        accounts: list[dict] | None = None,
        business_pages: list[dict] | None = None,
        pages: list[dict] | None = None,
    ) -> None:
        self.business_rows = businesses or []
        self.account_rows = accounts or []
        self.business_page_rows = business_pages or []
        self.page_rows = pages or []

    def exchange_code(self, *, code: str, redirect_uri: str) -> str:
        return "short-token"

    def extend_token(self, short_lived_token: str) -> str:
        return "long-token"

    def businesses(self, access_token: str) -> list[dict]:
        return list(self.business_rows)

    def business_pages(self, access_token: str, business_id: str) -> list[dict]:
        return list(self.business_page_rows)

    def ad_accounts(self, access_token: str) -> list[dict]:
        return list(self.account_rows)

    def promote_pages(self, access_token: str, account_id: str) -> list[dict]:
        return list(self.page_rows)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        partizan_public_base_url="https://partizan.example.com",
        meta_oauth_app_id="1234567890",
        meta_oauth_app_secret="meta-secret-not-real",
        meta_oauth_api_version="v26.0",
        meta_oauth_public_ready=True,
        provider_secret_encryption_key=Fernet.generate_key().decode("ascii"),
    )


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


def _complete(service: CustomerMetaGuidedSetupService, project_id, customer_token):
    authorization_url = service.begin(project_id, customer_token)
    state = parse_qs(urlsplit(authorization_url).query)["state"][0]
    return service.complete_with_return(state=state, code="oauth-code")


def test_guided_meta_preflight_keeps_token_when_business_is_missing() -> None:
    store = get_runtime_store()
    preview = _preview()
    settings = _settings()
    secret_store = ProviderSecretStore(store=store, settings=settings)
    service = CustomerMetaGuidedSetupService(
        store=store,
        settings=settings,
        client=_GuidedMetaStub(),
        secret_store=secret_store,
    )

    _complete(service, preview.project_id, preview.customer_token)

    view = service.view(preview.project_id, preview.customer_token)
    assert view.status == "NO_META_BUSINESS"
    assert view.can_check_again is True
    record = store.get(CUSTOMER_META_GUIDED_SETUP_NAMESPACE, str(preview.project_id))
    assert record is not None
    assert secret_store.get(str(record["secret_reference"])) == "long-token"
    assert store.get(CUSTOMER_META_PENDING_NAMESPACE, str(preview.project_id)) is None


def test_guided_meta_preflight_distinguishes_business_without_ad_account() -> None:
    store = get_runtime_store()
    preview = _preview()
    settings = _settings()
    service = CustomerMetaGuidedSetupService(
        store=store,
        settings=settings,
        client=_GuidedMetaStub(businesses=[{"id": "biz_1", "name": "Partizan"}]),
        secret_store=ProviderSecretStore(store=store, settings=settings),
    )

    _complete(service, preview.project_id, preview.customer_token)

    view = service.view(preview.project_id, preview.customer_token)
    assert view.status == "BUSINESS_NEEDS_AD_ACCOUNT"
    assert view.business_count == 1
    assert view.business_names == ["Partizan"]
    assert "ad account" in view.message


def test_guided_meta_preflight_distinguishes_ad_account_without_page() -> None:
    store = get_runtime_store()
    preview = _preview()
    settings = _settings()
    service = CustomerMetaGuidedSetupService(
        store=store,
        settings=settings,
        client=_GuidedMetaStub(
            businesses=[{"id": "biz_1", "name": "Partizan"}],
            accounts=[{"id": "act_123", "account_id": "123", "name": "Partizan Ads"}],
        ),
        secret_store=ProviderSecretStore(store=store, settings=settings),
    )

    _complete(service, preview.project_id, preview.customer_token)

    view = service.view(preview.project_id, preview.customer_token)
    assert view.status == "AD_ACCOUNT_NEEDS_PAGE"
    assert view.ad_account_count == 1
    assert view.business_page_count == 0
    assert view.promotable_page_count == 0


def test_guided_meta_preflight_distinguishes_page_not_promotable_for_ad_account() -> None:
    store = get_runtime_store()
    preview = _preview()
    settings = _settings()
    service = CustomerMetaGuidedSetupService(
        store=store,
        settings=settings,
        client=_GuidedMetaStub(
            businesses=[{"id": "biz_1", "name": "Partizan"}],
            accounts=[{"id": "act_123", "account_id": "123", "name": "Partizan Ads"}],
            business_pages=[{"id": "page_1", "name": "Partizan"}],
        ),
        secret_store=ProviderSecretStore(store=store, settings=settings),
    )

    _complete(service, preview.project_id, preview.customer_token)

    view = service.view(preview.project_id, preview.customer_token)
    assert view.status == "PAGE_NEEDS_AD_ACCOUNT_ACCESS"
    assert view.ad_account_count == 1
    assert view.business_page_count == 1
    assert view.business_page_names == ["Partizan"]
    assert view.promotable_page_count == 0
    assert "Do not create another Page" in view.message


def test_guided_meta_check_reuses_token_and_promotes_ready_assets() -> None:
    store = get_runtime_store()
    preview = _preview()
    settings = _settings()
    meta = _GuidedMetaStub(businesses=[{"id": "biz_1", "name": "Partizan"}])
    secret_store = ProviderSecretStore(store=store, settings=settings)
    service = CustomerMetaGuidedSetupService(
        store=store,
        settings=settings,
        client=meta,
        secret_store=secret_store,
    )

    _complete(service, preview.project_id, preview.customer_token)
    assert service.view(preview.project_id, preview.customer_token).status == "BUSINESS_NEEDS_AD_ACCOUNT"

    meta.account_rows = [
        {"id": "act_123", "account_id": "123", "name": "Partizan Ads", "currency": "USD"}
    ]
    meta.page_rows = [{"id": "page_1", "name": "Partizan"}]
    checked = service.check(preview.project_id, preview.customer_token)

    assert checked.status == "READY"
    pending = store.get(CUSTOMER_META_PENDING_NAMESPACE, str(preview.project_id))
    assert pending is not None
    assert pending["ad_accounts"][0]["account_id"] == "123"
    assert pending["pages_by_ad_account"]["123"][0]["id"] == "page_1"
    assert store.get(CUSTOMER_META_GUIDED_SETUP_NAMESPACE, str(preview.project_id)) is None


def test_workspace_meta_guided_asset_contains_setup_cjm_contract() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "web"
        / "workspace.meta-oauth-errors.v1.js"
    ).read_text(encoding="utf-8")

    assert "/meta-guided/connect" in source
    assert "/meta-guided/setup" in source
    assert "/meta-guided/check" in source
    assert "Meta setup needs one more step" in source
    assert "Check again" in source
    assert "Do you already use Meta Ads Manager for this business?" in source
    assert "PAGE_NEEDS_AD_ACCOUNT_ACCESS" in source
    assert "Do not create another Facebook Page" in source

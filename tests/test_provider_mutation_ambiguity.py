from types import SimpleNamespace

import httpx
import pytest

from app.meta_marketing_api import HttpxMetaMarketingApiClient, MetaMarketingApiError
from app.tiktok_marketing_api import HttpxTikTokMarketingApiClient, TikTokMarketingApiError


def test_meta_mutation_network_failure_is_ambiguous(monkeypatch) -> None:
    def fail_post(*args, **kwargs):
        raise httpx.ReadTimeout("provider response timed out")

    monkeypatch.setattr("app.meta_marketing_api.httpx.post", fail_post)
    client = HttpxMetaMarketingApiClient()
    connection = SimpleNamespace(
        api_version="v99.0",
        ad_account_id="123",
        special_ad_categories=[],
    )

    with pytest.raises(MetaMarketingApiError) as captured:
        client.create_campaign(
            connection=connection,
            access_token="secret",
            name="ambiguous campaign",
        )

    assert captured.value.ambiguous is True


def test_meta_explicit_client_rejection_is_not_ambiguous(monkeypatch) -> None:
    class Response:
        status_code = 400

        def json(self):
            return {"error": {"message": "invalid campaign"}}

    monkeypatch.setattr("app.meta_marketing_api.httpx.post", lambda *args, **kwargs: Response())
    client = HttpxMetaMarketingApiClient()
    connection = SimpleNamespace(
        api_version="v99.0",
        ad_account_id="123",
        special_ad_categories=[],
    )

    with pytest.raises(MetaMarketingApiError) as captured:
        client.create_campaign(
            connection=connection,
            access_token="secret",
            name="rejected campaign",
        )

    assert captured.value.ambiguous is False


def test_tiktok_mutation_network_failure_is_ambiguous(monkeypatch) -> None:
    def fail_post(*args, **kwargs):
        raise httpx.ReadTimeout("provider response timed out")

    monkeypatch.setattr("app.tiktok_marketing_api.httpx.post", fail_post)
    client = HttpxTikTokMarketingApiClient()
    connection = SimpleNamespace(api_version="v1.3", advertiser_id="adv_123")

    with pytest.raises(TikTokMarketingApiError) as captured:
        client.create_campaign(
            connection=connection,
            access_token="secret",
            name="ambiguous campaign",
        )

    assert captured.value.ambiguous is True


def test_tiktok_explicit_provider_rejection_is_not_ambiguous(monkeypatch) -> None:
    class Response:
        status_code = 200

        def json(self):
            return {"code": 40002, "message": "invalid campaign", "data": {}}

    monkeypatch.setattr(
        "app.tiktok_marketing_api.httpx.post",
        lambda *args, **kwargs: Response(),
    )
    client = HttpxTikTokMarketingApiClient()
    connection = SimpleNamespace(api_version="v1.3", advertiser_id="adv_123")

    with pytest.raises(TikTokMarketingApiError) as captured:
        client.create_campaign(
            connection=connection,
            access_token="secret",
            name="rejected campaign",
        )

    assert captured.value.ambiguous is False

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from app.tiktok_paid_provider import TikTokPaidProviderConnectionView


class TikTokMarketingApiError(RuntimeError):
    pass


class TikTokMarketingApiClient(Protocol):
    def create_campaign(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        name: str,
    ) -> str: ...

    def create_ad_group(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
        name: str,
        daily_budget: float,
    ) -> str: ...

    def create_ad(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        adgroup_id: str,
        name: str,
        destination_url: str,
        ad_text: str,
    ) -> str: ...


class HttpxTikTokMarketingApiClient:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout_seconds = timeout_seconds

    def create_campaign(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        name: str,
    ) -> str:
        payload = {
            "advertiser_id": connection.advertiser_id,
            "campaign_name": name,
            "objective_type": "TRAFFIC",
            "operation_status": "DISABLE",
        }
        data = self._post(connection, access_token, "campaign/create/", payload)
        return self._identifier(data, "campaign_id")

    def create_ad_group(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
        name: str,
        daily_budget: float,
    ) -> str:
        payload: dict[str, object] = {
            "advertiser_id": connection.advertiser_id,
            "campaign_id": campaign_id,
            "adgroup_name": name,
            "billing_event": connection.billing_event,
            "budget": round(daily_budget, 2),
            "budget_mode": connection.budget_mode,
            "optimization_goal": connection.optimization_goal,
            "pacing": connection.pacing,
            "schedule_start_time": self._schedule_start_time(),
            "schedule_type": connection.schedule_type,
            "location_ids": list(connection.location_ids),
            "identity_id": connection.identity_id,
            "identity_type": connection.identity_type,
            "placements": list(connection.placements),
            "operation_status": "DISABLE",
        }
        if connection.languages:
            payload["languages"] = list(connection.languages)
        if connection.promotion_type:
            payload["promotion_type"] = connection.promotion_type
        data = self._post(connection, access_token, "adgroup/create/", payload)
        return self._identifier(data, "adgroup_id")

    def create_ad(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        adgroup_id: str,
        name: str,
        destination_url: str,
        ad_text: str,
    ) -> str:
        creative = {
            "ad_name": name,
            "ad_text": ad_text[:100],
            "call_to_action": connection.call_to_action,
            "identity_id": connection.identity_id,
            "identity_type": connection.identity_type,
            "landing_page_url": destination_url,
            "video_id": connection.video_id,
            "operation_status": "DISABLE",
        }
        payload = {
            "advertiser_id": connection.advertiser_id,
            "adgroup_id": adgroup_id,
            "creatives": [creative],
        }
        data = self._post(connection, access_token, "ad/create/", payload)
        if data.get("ad_id"):
            return str(data["ad_id"])
        ad_ids = data.get("ad_ids")
        if isinstance(ad_ids, list) and ad_ids:
            return str(ad_ids[0])
        raise TikTokMarketingApiError("TikTok ad create response did not include an ad id")

    def _post(
        self,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        path: str,
        payload: dict[str, object],
    ) -> dict:
        url = f"https://business-api.tiktok.com/open_api/{connection.api_version}/{path}"
        try:
            response = httpx.post(
                url,
                json=payload,
                headers={"Access-Token": access_token},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TikTokMarketingApiError("TikTok Marketing API request failed") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise TikTokMarketingApiError("TikTok Marketing API returned invalid JSON") from exc
        if response.status_code >= 400:
            raise TikTokMarketingApiError(
                f"TikTok Marketing API HTTP {response.status_code}: {self._message(body)}"
            )
        if not isinstance(body, dict) or body.get("code") not in (0, "0", None):
            raise TikTokMarketingApiError(
                f"TikTok Marketing API rejected the request: {self._message(body)}"
            )
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise TikTokMarketingApiError("TikTok Marketing API response did not include data")
        return data

    def _identifier(self, data: dict, key: str) -> str:
        value = data.get(key)
        if value is None or str(value).strip() == "":
            raise TikTokMarketingApiError(f"TikTok response did not include {key}")
        return str(value)

    def _message(self, body: object) -> str:
        if isinstance(body, dict):
            for key in ("message", "msg"):
                value = body.get(key)
                if value:
                    return str(value)[:800]
        return "provider error"

    def _schedule_start_time(self) -> str:
        start = datetime.now(UTC) + timedelta(minutes=10)
        return start.strftime("%Y-%m-%d %H:%M:%S")

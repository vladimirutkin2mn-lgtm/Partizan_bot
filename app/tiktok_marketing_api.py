from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from app.tiktok_paid_provider import TikTokPaidProviderConnectionView


class TikTokMarketingApiError(RuntimeError):
    pass


class TikTokCampaignState(BaseModel):
    campaign_id: str
    operation_status: str
    primary_status: str | None = None
    secondary_status: str | None = None


class TikTokCampaignInsights(BaseModel):
    campaign_id: str
    spend: float = Field(ge=0)
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    currency: str | None = None


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

    def set_campaign_status(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
        operation_status: str,
    ) -> None: ...

    def set_adgroup_status(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        adgroup_id: str,
        operation_status: str,
    ) -> None: ...

    def set_ad_status(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        ad_id: str,
        operation_status: str,
    ) -> None: ...

    def get_campaign_state(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
    ) -> TikTokCampaignState: ...

    def get_campaign_insights(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
    ) -> TikTokCampaignInsights: ...


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

    def set_campaign_status(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
        operation_status: str,
    ) -> None:
        self._post(
            connection,
            access_token,
            "campaign/status/update/",
            {
                "advertiser_id": connection.advertiser_id,
                "campaign_ids": [campaign_id],
                "operation_status": operation_status,
            },
        )

    def set_adgroup_status(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        adgroup_id: str,
        operation_status: str,
    ) -> None:
        self._post(
            connection,
            access_token,
            "adgroup/status/update/",
            {
                "advertiser_id": connection.advertiser_id,
                "adgroup_ids": [adgroup_id],
                "operation_status": operation_status,
            },
        )

    def set_ad_status(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        ad_id: str,
        operation_status: str,
    ) -> None:
        self._post(
            connection,
            access_token,
            "ad/status/update/",
            {
                "advertiser_id": connection.advertiser_id,
                "ad_ids": [ad_id],
                "operation_status": operation_status,
            },
        )

    def get_campaign_state(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
    ) -> TikTokCampaignState:
        data = self._get(
            connection,
            access_token,
            "campaign/get/",
            {
                "advertiser_id": connection.advertiser_id,
                "filtering": json.dumps({"campaign_ids": [campaign_id]}),
                "fields": json.dumps(
                    [
                        "campaign_id",
                        "operation_status",
                        "primary_status",
                        "secondary_status",
                    ]
                ),
                "page": 1,
                "page_size": 1,
            },
        )
        rows = data.get("list")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise TikTokMarketingApiError("TikTok campaign get returned no campaign row")
        row = rows[0]
        identifier = str(row.get("campaign_id") or "")
        if identifier != campaign_id:
            raise TikTokMarketingApiError("TikTok campaign get returned an unexpected campaign")
        operation_status = str(row.get("operation_status") or "")
        if not operation_status:
            raise TikTokMarketingApiError("TikTok campaign row has no operation_status")
        return TikTokCampaignState(
            campaign_id=identifier,
            operation_status=operation_status,
            primary_status=self._optional_str(row.get("primary_status")),
            secondary_status=self._optional_str(row.get("secondary_status")),
        )

    def get_campaign_insights(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
    ) -> TikTokCampaignInsights:
        if not connection.report_type or not connection.report_data_level:
            raise TikTokMarketingApiError(
                "TikTok reporting requires explicit report_type and report_data_level configuration"
            )
        data = self._get(
            connection,
            access_token,
            "report/integrated/get/",
            {
                "report_type": connection.report_type,
                "advertiser_id": connection.advertiser_id,
                "data_level": connection.report_data_level,
                "dimensions": json.dumps(["campaign_id"]),
                "metrics": json.dumps(["spend", "impressions", "clicks"]),
                "query_lifetime": "true",
                "filtering": json.dumps(
                    [
                        {
                            "field_name": "campaign_ids",
                            "filter_type": "IN",
                            "filter_value": json.dumps([campaign_id]),
                        }
                    ]
                ),
                "page": 1,
                "page_size": 1,
            },
        )
        rows = data.get("list")
        if not isinstance(rows, list) or not rows:
            return TikTokCampaignInsights(
                campaign_id=campaign_id,
                spend=0,
                impressions=0,
                clicks=0,
            )
        row = rows[0]
        if not isinstance(row, dict):
            raise TikTokMarketingApiError("TikTok reporting returned an invalid row")
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else row
        returned_id = str(dimensions.get("campaign_id") or row.get("campaign_id") or campaign_id)
        if returned_id != campaign_id:
            raise TikTokMarketingApiError("TikTok reporting returned an unexpected campaign")
        return TikTokCampaignInsights(
            campaign_id=campaign_id,
            spend=self._float_metric(metrics, "spend"),
            impressions=self._int_metric(metrics, "impressions"),
            clicks=self._int_metric(metrics, "clicks"),
            currency=self._optional_str(metrics.get("currency") or row.get("currency")),
        )

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
        return self._response_data(response)

    def _get(
        self,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        path: str,
        params: dict[str, object],
    ) -> dict:
        url = f"https://business-api.tiktok.com/open_api/{connection.api_version}/{path}"
        try:
            response = httpx.get(
                url,
                params=params,
                headers={"Access-Token": access_token},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TikTokMarketingApiError("TikTok Marketing API request failed") from exc
        return self._response_data(response)

    def _response_data(self, response: httpx.Response) -> dict:
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
        data = body.get("data")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise TikTokMarketingApiError("TikTok Marketing API response data is invalid")
        return data

    def _identifier(self, data: dict, key: str) -> str:
        value = data.get(key)
        if value is None or str(value).strip() == "":
            raise TikTokMarketingApiError(f"TikTok response did not include {key}")
        return str(value)

    def _float_metric(self, metrics: dict, key: str) -> float:
        try:
            return max(0.0, float(metrics.get(key, 0) or 0))
        except (TypeError, ValueError) as exc:
            raise TikTokMarketingApiError(f"TikTok metric {key} is not numeric") from exc

    def _int_metric(self, metrics: dict, key: str) -> int:
        try:
            return max(0, int(float(metrics.get(key, 0) or 0)))
        except (TypeError, ValueError) as exc:
            raise TikTokMarketingApiError(f"TikTok metric {key} is not numeric") from exc

    def _optional_str(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

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

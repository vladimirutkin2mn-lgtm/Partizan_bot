from __future__ import annotations

import json
from typing import Protocol

import httpx

from app.paid_provider_connections import PaidProviderConnectionView


class MetaMarketingApiError(RuntimeError):
    pass


class MetaMarketingApiClient(Protocol):
    def create_campaign(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        name: str,
    ) -> str: ...

    def create_ad_set(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
        name: str,
        daily_budget_minor_units: int,
    ) -> str: ...

    def create_ad_creative(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        name: str,
        destination_url: str,
        primary_text: str,
        headline: str,
    ) -> str: ...

    def create_ad(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        ad_set_id: str,
        creative_id: str,
        name: str,
    ) -> str: ...

    def set_status(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        object_id: str,
        status: str,
    ) -> None: ...


class HttpxMetaMarketingApiClient:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout_seconds = timeout_seconds

    def create_campaign(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        name: str,
    ) -> str:
        return self._post_id(
            connection=connection,
            access_token=access_token,
            path=f"act_{connection.ad_account_id}/campaigns",
            data={
                "name": name,
                "objective": "OUTCOME_TRAFFIC",
                "special_ad_categories": json.dumps(connection.special_ad_categories),
                "status": "PAUSED",
                "buying_type": "AUCTION",
            },
        )

    def create_ad_set(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        campaign_id: str,
        name: str,
        daily_budget_minor_units: int,
    ) -> str:
        targeting = {"geo_locations": {"countries": connection.country_codes}}
        return self._post_id(
            connection=connection,
            access_token=access_token,
            path=f"act_{connection.ad_account_id}/adsets",
            data={
                "name": name,
                "campaign_id": campaign_id,
                "status": "PAUSED",
                "billing_event": "IMPRESSIONS",
                "optimization_goal": "LINK_CLICKS",
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                "daily_budget": str(daily_budget_minor_units),
                "targeting": json.dumps(targeting),
            },
        )

    def create_ad_creative(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        name: str,
        destination_url: str,
        primary_text: str,
        headline: str,
    ) -> str:
        link_data = {
            "call_to_action": {"type": "LEARN_MORE"},
            "link": destination_url,
            "message": primary_text,
            "name": headline,
            "picture": str(connection.default_image_url),
        }
        object_story_spec: dict[str, object] = {
            "page_id": connection.page_id,
            "link_data": link_data,
        }
        if connection.instagram_actor_id:
            object_story_spec["instagram_actor_id"] = connection.instagram_actor_id
        return self._post_id(
            connection=connection,
            access_token=access_token,
            path=f"act_{connection.ad_account_id}/adcreatives",
            data={
                "name": name,
                "object_story_spec": json.dumps(object_story_spec),
            },
        )

    def create_ad(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        ad_set_id: str,
        creative_id: str,
        name: str,
    ) -> str:
        return self._post_id(
            connection=connection,
            access_token=access_token,
            path=f"act_{connection.ad_account_id}/ads",
            data={
                "name": name,
                "status": "PAUSED",
                "adset_id": ad_set_id,
                "creative": json.dumps({"creative_id": creative_id}),
            },
        )

    def set_status(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        object_id: str,
        status: str,
    ) -> None:
        if status not in {"ACTIVE", "PAUSED"}:
            raise ValueError("Meta status must be ACTIVE or PAUSED")
        self._post_json(
            connection=connection,
            access_token=access_token,
            path=object_id,
            data={"status": status},
        )

    def _post_id(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        path: str,
        data: dict[str, str],
    ) -> str:
        payload = self._post_json(
            connection=connection,
            access_token=access_token,
            path=path,
            data=data,
        )
        identifier = payload.get("id") if isinstance(payload, dict) else None
        if not identifier:
            raise MetaMarketingApiError("Meta Marketing API response did not include an object id")
        return str(identifier)

    def _post_json(
        self,
        *,
        connection: PaidProviderConnectionView,
        access_token: str,
        path: str,
        data: dict[str, str],
    ) -> dict:
        url = f"https://graph.facebook.com/{connection.api_version}/{path}"
        try:
            response = httpx.post(
                url,
                data=data,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise MetaMarketingApiError("Meta Marketing API request failed") from exc

        if response.status_code >= 400:
            message = "Meta Marketing API rejected the request"
            try:
                payload = response.json()
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, dict) and error.get("message"):
                    message = f"Meta Marketing API rejected the request: {error['message']}"
            except ValueError:
                pass
            raise MetaMarketingApiError(message[:1000])

        try:
            payload = response.json()
        except ValueError as exc:
            raise MetaMarketingApiError("Meta Marketing API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MetaMarketingApiError("Meta Marketing API returned an invalid response object")
        return payload

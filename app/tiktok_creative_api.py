from __future__ import annotations

from typing import Protocol

import httpx

from app.tiktok_paid_provider import TikTokPaidProviderConnectionView


class TikTokCreativeApiError(RuntimeError):
    pass


class TikTokCreativeApiClient(Protocol):
    def upload_video_by_url(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        video_url: str,
        file_name: str,
    ) -> str: ...


class HttpxTikTokCreativeApiClient:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds

    def upload_video_by_url(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        video_url: str,
        file_name: str,
    ) -> str:
        normalized_url = video_url.strip()
        normalized_name = file_name.strip()[:100]
        if not normalized_url.startswith(("https://", "http://")):
            raise ValueError("TikTok video upload requires an absolute http(s) video URL")
        if not normalized_name:
            raise ValueError("TikTok video upload requires a non-empty file name")

        url = (
            "https://business-api.tiktok.com/open_api/"
            f"{connection.api_version}/file/video/ad/upload/"
        )
        multipart = {
            "advertiser_id": (None, connection.advertiser_id),
            "upload_type": (None, "UPLOAD_BY_URL"),
            "video_url": (None, normalized_url),
            "file_name": (None, normalized_name),
        }
        try:
            response = httpx.post(
                url,
                files=multipart,
                headers={"Access-Token": access_token},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TikTokCreativeApiError("TikTok video upload request failed") from exc

        data = self._response_data(response)
        video_id = data.get("video_id")
        if video_id is None or not str(video_id).strip():
            raise TikTokCreativeApiError(
                "TikTok video upload succeeded without returning a video_id"
            )
        return str(video_id).strip()

    def _response_data(self, response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise TikTokCreativeApiError("TikTok video upload returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise TikTokCreativeApiError("TikTok video upload returned an invalid response")
        if response.status_code >= 400:
            raise TikTokCreativeApiError(
                f"TikTok video upload HTTP {response.status_code}: {self._message(body)}"
            )
        if body.get("code") not in (0, "0", None):
            raise TikTokCreativeApiError(
                f"TikTok video upload was rejected: {self._message(body)}"
            )
        data = body.get("data")
        if not isinstance(data, dict):
            raise TikTokCreativeApiError("TikTok video upload response has no data object")
        return data

    def _message(self, body: dict) -> str:
        for key in ("message", "msg"):
            value = body.get(key)
            if value:
                return str(value)[:800]
        return "provider error"

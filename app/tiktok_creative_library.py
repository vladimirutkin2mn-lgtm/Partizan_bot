from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import httpx

from app.tiktok_paid_provider import TikTokPaidProviderConnectionView


class TikTokCreativeLibraryError(RuntimeError):
    pass


@dataclass(frozen=True)
class TikTokUploadedVideo:
    video_id: str
    preview_url: str | None = None
    video_cover_url: str | None = None


class TikTokCreativeLibraryClient:
    def __init__(self, timeout_seconds: float = 90.0) -> None:
        self._timeout_seconds = timeout_seconds

    def upload_and_verify(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        file_name: str,
        video_bytes: bytes,
    ) -> TikTokUploadedVideo:
        if not video_bytes:
            raise ValueError("TikTok creative upload requires non-empty video bytes")
        video_id = self._upload(
            connection=connection,
            access_token=access_token,
            file_name=file_name,
            video_bytes=video_bytes,
        )
        return self._verify(
            connection=connection,
            access_token=access_token,
            video_id=video_id,
        )

    def _upload(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        file_name: str,
        video_bytes: bytes,
    ) -> str:
        url = (
            f"https://business-api.tiktok.com/open_api/{connection.api_version}/"
            "file/video/ad/upload/"
        )
        data = {
            "advertiser_id": connection.advertiser_id,
            "file_name": file_name,
            "video_signature": hashlib.md5(video_bytes).hexdigest(),  # noqa: S324
        }
        files = {"video_file": (file_name, video_bytes, "video/mp4")}
        try:
            response = httpx.post(
                url,
                data=data,
                files=files,
                headers={"Access-Token": access_token},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TikTokCreativeLibraryError("TikTok video upload request failed") from exc
        payload = self._payload(response, "upload")
        video_id = str(payload.get("video_id") or "").strip()
        if not video_id:
            raise TikTokCreativeLibraryError("TikTok video upload returned no video_id")
        return video_id

    def _verify(
        self,
        *,
        connection: TikTokPaidProviderConnectionView,
        access_token: str,
        video_id: str,
    ) -> TikTokUploadedVideo:
        url = (
            f"https://business-api.tiktok.com/open_api/{connection.api_version}/"
            "file/video/ad/info/"
        )
        params = {
            "advertiser_id": connection.advertiser_id,
            "video_ids": json.dumps([video_id]),
        }
        try:
            response = httpx.get(
                url,
                params=params,
                headers={"Access-Token": access_token},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TikTokCreativeLibraryError("TikTok video info request failed") from exc
        payload = self._payload(response, "info")
        rows = payload.get("list")
        if not isinstance(rows, list):
            rows = payload.get("videos")
        if not isinstance(rows, list):
            rows = []
        row = next(
            (
                item
                for item in rows
                if isinstance(item, dict) and str(item.get("video_id") or "") == video_id
            ),
            None,
        )
        if row is None:
            raise TikTokCreativeLibraryError(
                "TikTok did not confirm the uploaded video as an ad-usable Creative Library asset"
            )
        return TikTokUploadedVideo(
            video_id=video_id,
            preview_url=self._optional_string(row.get("preview_url")),
            video_cover_url=self._optional_string(row.get("video_cover_url")),
        )

    def _payload(self, response: httpx.Response, operation: str) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise TikTokCreativeLibraryError(
                f"TikTok video {operation} returned invalid JSON"
            ) from exc
        if response.status_code >= 400:
            raise TikTokCreativeLibraryError(
                f"TikTok video {operation} returned HTTP {response.status_code}"
            )
        if not isinstance(body, dict) or body.get("code") not in {0, "0"}:
            raise TikTokCreativeLibraryError(f"TikTok video {operation} was rejected")
        data = body.get("data")
        if not isinstance(data, dict):
            raise TikTokCreativeLibraryError(
                f"TikTok video {operation} returned no data object"
            )
        return data

    def _optional_string(self, value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

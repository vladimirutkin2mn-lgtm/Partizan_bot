import hashlib
import json
from types import SimpleNamespace

import pytest

from app.tiktok_creative_library import TikTokCreativeLibraryClient, TikTokCreativeLibraryError


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self.payload


def _connection():
    return SimpleNamespace(advertiser_id="adv_123", api_version="v1.3")


def test_tiktok_creative_library_uploads_mp4_then_verifies_video_id(monkeypatch) -> None:
    calls: list[tuple[str, str, dict]] = []
    video_bytes = b"video-bytes"

    def fake_post(url, *, data, files, headers, timeout):
        calls.append(("POST", url, {"data": data, "files": files, "headers": headers, "timeout": timeout}))
        return FakeResponse({"code": 0, "message": "OK", "data": {"video_id": "video_123"}})

    def fake_get(url, *, params, headers, timeout):
        calls.append(("GET", url, {"params": params, "headers": headers, "timeout": timeout}))
        return FakeResponse(
            {
                "code": 0,
                "message": "OK",
                "data": {
                    "list": [
                        {
                            "video_id": "video_123",
                            "preview_url": "https://example.com/preview.mp4",
                            "video_cover_url": "https://example.com/cover.jpg",
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr("app.tiktok_creative_library.httpx.post", fake_post)
    monkeypatch.setattr("app.tiktok_creative_library.httpx.get", fake_get)

    result = TikTokCreativeLibraryClient(timeout_seconds=30).upload_and_verify(
        connection=_connection(),
        access_token="top-secret",
        file_name="partizan.mp4",
        video_bytes=video_bytes,
    )

    assert result.video_id == "video_123"
    assert result.preview_url == "https://example.com/preview.mp4"
    assert result.video_cover_url == "https://example.com/cover.jpg"
    assert len(calls) == 2

    method, url, request = calls[0]
    assert method == "POST"
    assert url.endswith("/open_api/v1.3/file/video/ad/upload/")
    assert request["headers"] == {"Access-Token": "top-secret"}
    assert request["data"] == {
        "advertiser_id": "adv_123",
        "file_name": "partizan.mp4",
        "video_signature": hashlib.md5(video_bytes).hexdigest(),  # noqa: S324
    }
    assert request["files"]["video_file"] == (
        "partizan.mp4",
        video_bytes,
        "video/mp4",
    )

    method, url, request = calls[1]
    assert method == "GET"
    assert url.endswith("/open_api/v1.3/file/video/ad/info/")
    assert request["headers"] == {"Access-Token": "top-secret"}
    assert request["params"]["advertiser_id"] == "adv_123"
    assert json.loads(request["params"]["video_ids"]) == ["video_123"]


def test_tiktok_creative_library_rejects_unconfirmed_video_id(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.tiktok_creative_library.httpx.post",
        lambda *args, **kwargs: FakeResponse(
            {"code": 0, "message": "OK", "data": {"video_id": "video_123"}}
        ),
    )
    monkeypatch.setattr(
        "app.tiktok_creative_library.httpx.get",
        lambda *args, **kwargs: FakeResponse({"code": 0, "message": "OK", "data": {"list": []}}),
    )

    with pytest.raises(TikTokCreativeLibraryError, match="did not confirm"):
        TikTokCreativeLibraryClient().upload_and_verify(
            connection=_connection(),
            access_token="top-secret",
            file_name="partizan.mp4",
            video_bytes=b"video-bytes",
        )


def test_tiktok_creative_library_rejects_provider_error_without_leaking_token(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.tiktok_creative_library.httpx.post",
        lambda *args, **kwargs: FakeResponse(
            {"code": 40001, "message": "bad request", "data": {}},
            status_code=200,
        ),
    )

    with pytest.raises(TikTokCreativeLibraryError) as exc_info:
        TikTokCreativeLibraryClient().upload_and_verify(
            connection=_connection(),
            access_token="top-secret",
            file_name="partizan.mp4",
            video_bytes=b"video-bytes",
        )

    assert "top-secret" not in str(exc_info.value)

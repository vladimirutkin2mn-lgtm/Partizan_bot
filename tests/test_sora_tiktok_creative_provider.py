from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.creative_assets import CreativeBriefView, CreativeMediaType, CreativePurpose
from app.creative_blob_store import CreativeBlobStore
from app.creative_generation import (
    CreativeGenerationOutcome,
    OpenAITikTokVideoCreativeGenerator,
)
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_creative_library import TikTokCreativeLibraryError, TikTokUploadedVideo


class FakeBinaryContent:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


class FakeVideos:
    def __init__(self, data: bytes = b"sora-mp4") -> None:
        self.data = data
        self.create_calls: list[dict] = []
        self.download_calls: list[str] = []

    def create_and_poll(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        return SimpleNamespace(id="video_sora_123", status="completed")

    def download_content(self, video_id: str):
        self.download_calls.append(video_id)
        return FakeBinaryContent(self.data)


class FakeOpenAI:
    def __init__(self, data: bytes = b"sora-mp4") -> None:
        self.videos = FakeVideos(data)


class FakeConnectionService:
    def __init__(self, connection) -> None:
        self.connection = connection

    def get(self, product_id):
        assert product_id == self.connection.product_id
        return self.connection


class FakeLibraryClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def upload_and_verify(self, **kwargs) -> TikTokUploadedVideo:
        self.calls.append(dict(kwargs))
        if self.fail:
            raise TikTokCreativeLibraryError("not confirmed")
        return TikTokUploadedVideo(video_id="tt_video_456")


def _brief(product_id=None) -> CreativeBriefView:
    return CreativeBriefView(
        id=uuid4(),
        product_id=product_id or uuid4(),
        action_id=uuid4(),
        experiment_id=uuid4(),
        play_id=uuid4(),
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.PAID_AD,
        media_type=CreativeMediaType.VIDEO,
        content={
            "product_name": "Oracle",
            "message_hook": "Stop scrolling if uncertainty keeps looping in your head",
            "value_proposition": "Personalized relationship readings",
            "audience": {"market": "US"},
        },
        constraints=["Do not fabricate testimonials or results."],
        fingerprint="b" * 64,
        created_at=datetime.now(UTC),
    )


def _connection(product_id):
    return SimpleNamespace(
        product_id=product_id,
        status=SimpleNamespace(value="ACTIVE"),
        access_token_env="TIKTOK_TEST_TOKEN",
        advertiser_id="adv_123",
        api_version="v1.3",
    )


def test_sora_tiktok_generator_returns_only_verified_provider_asset(monkeypatch) -> None:
    brief = _brief()
    connection = _connection(brief.product_id)
    openai = FakeOpenAI(b"generated-mp4")
    library = FakeLibraryClient()
    blob_store = CreativeBlobStore(MemoryRuntimeStateStore())
    monkeypatch.setenv("TIKTOK_TEST_TOKEN", "secret-tiktok-token")

    result = OpenAITikTokVideoCreativeGenerator(
        api_key="openai-test-key",
        public_base_url="https://partizan.example",
        model="sora-2",
        seconds=8,
        size="720x1280",
        client=openai,
        blob_store=blob_store,
        connection_service=FakeConnectionService(connection),
        library_client=library,
    ).generate(brief)

    assert result.outcome == CreativeGenerationOutcome.READY
    assert result.provider_asset_id == "tt_video_456"
    assert result.mime_type == "video/mp4"
    assert result.width == 720
    assert result.height == 1280
    assert result.duration_seconds == 8
    assert str(result.public_url).startswith(
        "https://partizan.example/v1/public/creative-blobs/"
    )
    assert result.provenance["sora_video_id"] == "video_sora_123"
    assert result.provenance["tiktok_video_id"] == "tt_video_456"
    assert "secret-tiktok-token" not in result.model_dump_json()
    assert "openai-test-key" not in result.model_dump_json()

    assert openai.videos.create_calls == [
        {
            "model": "sora-2",
            "prompt": openai.videos.create_calls[0]["prompt"],
            "seconds": "8",
            "size": "720x1280",
            "poll_interval_ms": 2000,
        }
    ]
    assert "first second" in openai.videos.create_calls[0]["prompt"]
    assert openai.videos.download_calls == ["video_sora_123"]
    assert len(library.calls) == 1
    assert library.calls[0]["access_token"] == "secret-tiktok-token"
    assert library.calls[0]["video_bytes"] == b"generated-mp4"
    assert library.calls[0]["file_name"].endswith(".mp4")


def test_sora_tiktok_generator_fails_closed_without_tiktok_secret(monkeypatch) -> None:
    brief = _brief()
    connection = _connection(brief.product_id)
    openai = FakeOpenAI()
    monkeypatch.delenv("TIKTOK_TEST_TOKEN", raising=False)

    result = OpenAITikTokVideoCreativeGenerator(
        api_key="openai-test-key",
        public_base_url="https://partizan.example",
        client=openai,
        connection_service=FakeConnectionService(connection),
        library_client=FakeLibraryClient(),
    ).generate(brief)

    assert result.outcome == CreativeGenerationOutcome.UNAVAILABLE
    assert not openai.videos.create_calls


def test_sora_tiktok_generator_does_not_mark_unverified_upload_ready(monkeypatch) -> None:
    brief = _brief()
    connection = _connection(brief.product_id)
    openai = FakeOpenAI()
    library = FakeLibraryClient(fail=True)
    monkeypatch.setenv("TIKTOK_TEST_TOKEN", "secret-tiktok-token")

    result = OpenAITikTokVideoCreativeGenerator(
        api_key="openai-test-key",
        public_base_url="https://partizan.example",
        client=openai,
        blob_store=CreativeBlobStore(MemoryRuntimeStateStore()),
        connection_service=FakeConnectionService(connection),
        library_client=library,
    ).generate(brief)

    assert result.outcome == CreativeGenerationOutcome.FAILED
    assert result.provider_asset_id is None
    assert result.public_url is None
    assert result.provenance["blob_id"]
    assert "not provider-confirmed" in result.message

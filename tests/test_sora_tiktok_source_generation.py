from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.creative_assets import CreativeBriefView, CreativeMediaType, CreativePurpose
from app.creative_generation import (
    CreativeGenerationOutcome,
    OpenAITikTokVideoSourceGenerator,
    RoutedCreativeGenerator,
    UnavailableCreativeGenerator,
)
from app.creative_video_blob_store import CreativeVideoBlobStore, creative_video_blob_store
from app.distribution_types import DistributionPlatform
from app.main import app
from app.runtime_store import MemoryRuntimeStateStore

client = TestClient(app)


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


def _brief() -> CreativeBriefView:
    return CreativeBriefView(
        id=uuid4(),
        product_id=uuid4(),
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


def test_sora_generator_creates_stable_url_source_without_faking_tiktok_id() -> None:
    fake = FakeOpenAI(b"generated-mp4")
    store = CreativeVideoBlobStore(MemoryRuntimeStateStore())
    generator = OpenAITikTokVideoSourceGenerator(
        api_key="openai-test-key",
        public_base_url="https://partizan.example",
        model="sora-2",
        seconds=8,
        size="720x1280",
        client=fake,
        video_store=store,
    )

    result = generator.generate(_brief())

    assert result.outcome == CreativeGenerationOutcome.READY
    assert result.provider_asset_id is None
    assert result.mime_type == "video/mp4"
    assert result.width == 720
    assert result.height == 1280
    assert result.duration_seconds == 8
    assert str(result.public_url).startswith(
        "https://partizan.example/v1/public/creative-video-blobs/"
    )
    assert result.provenance["sora_video_id"] == "video_sora_123"
    assert "openai-test-key" not in result.model_dump_json()

    assert len(fake.videos.create_calls) == 1
    call = fake.videos.create_calls[0]
    assert call["model"] == "sora-2"
    assert call["seconds"] == "8"
    assert call["size"] == "720x1280"
    assert call["poll_interval_ms"] == 2000
    assert "first second" in call["prompt"]
    assert "fabricate testimonials" in call["prompt"]
    assert fake.videos.download_calls == ["video_sora_123"]

    blob_id = UUID(str(result.public_url).rstrip("/").split("/")[-1])
    view, data = store.get(blob_id)
    assert view.mime_type == "video/mp4"
    assert data == b"generated-mp4"


def test_sora_generator_fails_closed_without_key_or_public_origin() -> None:
    fake = FakeOpenAI()
    brief = _brief()

    no_key = OpenAITikTokVideoSourceGenerator(
        api_key=None,
        public_base_url="https://partizan.example",
        client=fake,
    ).generate(brief)
    assert no_key.outcome == CreativeGenerationOutcome.UNAVAILABLE

    no_origin = OpenAITikTokVideoSourceGenerator(
        api_key="openai-test-key",
        public_base_url=None,
        client=fake,
    ).generate(brief)
    assert no_origin.outcome == CreativeGenerationOutcome.UNAVAILABLE
    assert not fake.videos.create_calls


def test_routed_openai_generator_reaches_sora_after_non_applicable_provider() -> None:
    fake = FakeOpenAI()
    sora = OpenAITikTokVideoSourceGenerator(
        api_key="openai-test-key",
        public_base_url="https://partizan.example",
        client=fake,
        video_store=CreativeVideoBlobStore(MemoryRuntimeStateStore()),
    )
    routed = RoutedCreativeGenerator([UnavailableCreativeGenerator(), sora])

    result = routed.generate(_brief())

    assert result.outcome == CreativeGenerationOutcome.READY
    assert len(fake.videos.create_calls) == 1


def test_public_video_blob_route_serves_exact_mp4_bytes() -> None:
    creative_video_blob_store.reset()
    blob = creative_video_blob_store.put(b"public-video")

    response = client.get(f"/v1/public/creative-video-blobs/{blob.id}")

    assert response.status_code == 200
    assert response.content == b"public-video"
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["etag"] == f'"{blob.sha256}"'
    assert "immutable" in response.headers["cache-control"]

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.creative_assets import (
    CreativeBriefView,
    CreativeMediaType,
    CreativePurpose,
    CreativeReadinessStatus,
    CreativeReadinessView,
)
from app.creative_blob_store import CreativeBlobStore
from app.creative_generation import (
    CreativeGenerationOutcome,
    CreativeGenerationView,
    CreativeGeneratorResult,
)
from app.creative_provider_finalization import (
    CreativeProviderFinalizationOutcome,
    CreativeProviderFinalizationView,
    ProviderAwareCreativeGenerationService,
)
from app.distribution_types import DistributionPlatform
from app.gemini_video_generation import (
    ConfiguredMultimediaCreativeGenerator,
    GeminiOmniTikTokVideoGenerator,
    GeminiOmniVideoApiError,
    HttpxGeminiOmniVideoClient,
)
from app.runtime_store import MemoryRuntimeStateStore

MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isompartizan"


class FakeGeminiClient:
    def __init__(self, *, data: bytes = MP4_BYTES, fail: bool = False) -> None:
        self.data = data
        self.fail = fail
        self.calls: list[dict] = []

    def generate_mp4(self, *, model, prompt, aspect_ratio):
        self.calls.append(
            {"model": model, "prompt": prompt, "aspect_ratio": aspect_ratio}
        )
        if self.fail:
            raise GeminiOmniVideoApiError("sensitive provider failure")
        return self.data


class FakeGenerator:
    def __init__(self, result: CreativeGeneratorResult) -> None:
        self.result = result
        self.calls = 0

    def generate(self, brief):
        self.calls += 1
        return self.result


class SequencedFinalizer:
    def __init__(self, brief: CreativeBriefView) -> None:
        self.brief = brief
        self.calls = 0

    def finalize(self, action_id):
        self.calls += 1
        if self.calls == 1:
            return CreativeProviderFinalizationView(
                action_id=action_id,
                outcome=CreativeProviderFinalizationOutcome.NOOP,
                readiness=CreativeReadinessView(
                    action_id=action_id,
                    brief=self.brief,
                    status=CreativeReadinessStatus.BLOCKED,
                    reasons=["No source video yet."],
                ),
                message="No source video yet.",
            )
        provider_asset = SimpleNamespace(provider_asset_id="video_real_123")
        return CreativeProviderFinalizationView(
            action_id=action_id,
            outcome=CreativeProviderFinalizationOutcome.READY,
            readiness=CreativeReadinessView(
                action_id=action_id,
                brief=self.brief,
                status=CreativeReadinessStatus.READY,
                selected_asset=None,
                reasons=["Provider-ready TikTok video."],
            ),
            asset=provider_asset,
            message="TikTok provider video ID persisted.",
        )


class SourceGenerationService:
    def __init__(self, brief: CreativeBriefView) -> None:
        self.brief = brief
        self.calls = 0

    def ensure_ready(self, action_id):
        self.calls += 1
        return CreativeGenerationView(
            action_id=action_id,
            outcome=CreativeGenerationOutcome.FAILED,
            brief=self.brief,
            readiness=CreativeReadinessView(
                action_id=action_id,
                brief=self.brief,
                status=CreativeReadinessStatus.BLOCKED,
                reasons=["URL video exists but TikTok provider ID is not ready yet."],
            ),
            message="Source URL generated; provider finalization remains.",
        )


def _brief(
    *,
    platform: DistributionPlatform = DistributionPlatform.TIKTOK,
    media_type: CreativeMediaType = CreativeMediaType.VIDEO,
) -> CreativeBriefView:
    return CreativeBriefView(
        id=uuid4(),
        product_id=uuid4(),
        action_id=uuid4(),
        experiment_id=uuid4(),
        play_id=uuid4(),
        platform=platform,
        purpose=CreativePurpose.PAID_AD,
        media_type=media_type,
        content={
            "product_name": "Oracle",
            "value_proposition": "Personalized entertainment readings on demand.",
            "message_hook": "A reflective reading for uncertain moments.",
            "audience": {"market": "US"},
        },
        constraints=[
            "Use only confirmed product facts.",
            "Do not fabricate testimonials or social proof.",
        ],
        fingerprint="b" * 64,
        created_at=datetime.now(UTC),
    )


def test_gemini_http_client_sends_header_auth_and_decodes_inline_mp4(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {
                                "type": "video",
                                "mime_type": "video/mp4",
                                "data": base64.b64encode(MP4_BYTES).decode("ascii"),
                            }
                        ],
                    }
                ]
            }

    def fake_post(url, *, json, headers, timeout):
        captured.update(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return Response()

    monkeypatch.setattr("app.gemini_video_generation.httpx.post", fake_post)
    client = HttpxGeminiOmniVideoClient(api_key="gemini-secret", timeout_seconds=33)

    video = client.generate_mp4(
        model="gemini-omni-flash-preview",
        prompt="confirmed campaign prompt",
        aspect_ratio="9:16",
    )

    assert video == MP4_BYTES
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert "gemini-secret" not in captured["url"]
    assert captured["headers"] == {
        "x-goog-api-key": "gemini-secret",
        "Content-Type": "application/json",
    }
    assert captured["timeout"] == 33
    assert captured["json"] == {
        "model": "gemini-omni-flash-preview",
        "input": "confirmed campaign prompt",
        "response_format": {"type": "video", "aspect_ratio": "9:16"},
    }


def test_gemini_http_client_requires_inline_mp4(monkeypatch) -> None:
    class Response:
        status_code = 200

        def json(self):
            return {"steps": [{"type": "model_output", "content": []}]}

    monkeypatch.setattr(
        "app.gemini_video_generation.httpx.post",
        lambda *args, **kwargs: Response(),
    )
    client = HttpxGeminiOmniVideoClient(api_key="gemini-secret")

    with pytest.raises(GeminiOmniVideoApiError, match="no inline video"):
        client.generate_mp4(
            model="gemini-omni-flash-preview",
            prompt="prompt",
            aspect_ratio="9:16",
        )


def test_gemini_video_generator_persists_public_mp4_without_provider_id() -> None:
    store = CreativeBlobStore(MemoryRuntimeStateStore())
    gemini = FakeGeminiClient()
    generator = GeminiOmniTikTokVideoGenerator(
        client=gemini,
        blob_store=store,
        public_base_url="https://growth.example.com",
    )

    result = generator.generate(_brief())

    assert result.outcome == CreativeGenerationOutcome.READY
    assert result.provider_asset_id is None
    assert result.mime_type == "video/mp4"
    assert result.width == 720
    assert result.height == 1280
    assert str(result.public_url).startswith(
        "https://growth.example.com/v1/public/creative-blobs/"
    )
    blob_id = uuid4()
    actual_id = str(result.public_url).rsplit("/", 1)[-1]
    blob_id = type(blob_id)(actual_id)
    view, data = store.get(blob_id)
    assert data == MP4_BYTES
    assert view.mime_type == "video/mp4"
    assert result.provenance["sha256"] == view.sha256
    assert gemini.calls[0]["aspect_ratio"] == "9:16"
    assert "Oracle" in gemini.calls[0]["prompt"]
    assert "Do not fabricate testimonials" in gemini.calls[0]["prompt"]


def test_gemini_video_generator_is_tiktok_video_only_and_sanitizes_failure() -> None:
    gemini = FakeGeminiClient(fail=True)
    generator = GeminiOmniTikTokVideoGenerator(
        client=gemini,
        blob_store=CreativeBlobStore(MemoryRuntimeStateStore()),
        public_base_url="https://growth.example.com",
    )

    not_video = generator.generate(
        _brief(platform=DistributionPlatform.INSTAGRAM, media_type=CreativeMediaType.IMAGE)
    )
    assert not_video.outcome == CreativeGenerationOutcome.UNAVAILABLE
    assert gemini.calls == []

    failed = generator.generate(_brief())
    assert failed.outcome == CreativeGenerationOutcome.FAILED
    assert "sensitive provider failure" not in failed.message
    assert "secret" not in failed.model_dump_json().lower()


def test_creative_blob_store_rejects_invalid_mp4_signature_and_oversize_video() -> None:
    store = CreativeBlobStore(MemoryRuntimeStateStore())

    with pytest.raises(ValueError, match="invalid file signature"):
        store.put(data=b"not-an-mp4", mime_type="video/mp4")
    with pytest.raises(ValueError, match="12 MiB"):
        store.put(data=MP4_BYTES + b"x" * (12 * 1024 * 1024), mime_type="video/mp4")


def test_multimedia_dispatch_routes_video_and_image_to_separate_generators() -> None:
    image = FakeGenerator(
        CreativeGeneratorResult(
            outcome=CreativeGenerationOutcome.READY,
            public_url="https://cdn.example.com/image.png",
            mime_type="image/png",
            message="image",
        )
    )
    video = FakeGenerator(
        CreativeGeneratorResult(
            outcome=CreativeGenerationOutcome.READY,
            public_url="https://cdn.example.com/video.mp4",
            mime_type="video/mp4",
            message="video",
        )
    )
    dispatch = ConfiguredMultimediaCreativeGenerator(
        image_generator=image,
        video_generator=video,
    )

    video_result = dispatch.generate(_brief())
    image_result = dispatch.generate(
        _brief(platform=DistributionPlatform.INSTAGRAM, media_type=CreativeMediaType.IMAGE)
    )

    assert video_result.message == "video"
    assert image_result.message == "image"
    assert video.calls == 1
    assert image.calls == 1


def test_provider_aware_flow_runs_source_then_tiktok_finalization() -> None:
    brief = _brief()
    source = SourceGenerationService(brief)
    finalizer = SequencedFinalizer(brief)
    service = ProviderAwareCreativeGenerationService(
        generation_service=source,  # type: ignore[arg-type]
        tiktok_finalizer=finalizer,  # type: ignore[arg-type]
    )

    result = service.ensure_ready(brief.action_id)

    assert result.outcome == CreativeGenerationOutcome.READY
    assert result.asset is not None
    assert result.asset.provider_asset_id == "video_real_123"
    assert source.calls == 1
    assert finalizer.calls == 2

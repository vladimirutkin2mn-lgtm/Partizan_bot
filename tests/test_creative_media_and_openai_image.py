from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.creative_assets import (
    CreativeBriefView,
    CreativeMediaType,
    CreativePurpose,
)
from app.creative_generation import (
    CreativeGenerationOutcome,
    OpenAICreativeImageGenerator,
)
from app.creative_media import CreativeMediaStore, creative_media_store
from app.distribution_types import DistributionPlatform
from app.main import app

client = TestClient(app)

PNG_BYTES = b"\x89PNG\r\n\x1a\npartizan-test-image"


class FakeOpenAIImageClient:
    def __init__(self, content: bytes = PNG_BYTES, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.calls: list[dict] = []

    def generate_png(self, *, model, prompt, size, quality) -> bytes:
        self.calls.append(
            {
                "model": model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
            }
        )
        if self.fail:
            raise RuntimeError("provider failure with internal details")
        return self.content


class FakeMediaStore:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []
        self.media_id = uuid4()

    def put(self, content: bytes, *, mime_type: str):
        self.calls.append((content, mime_type))
        return SimpleNamespace(
            id=self.media_id,
            sha256="a" * 64,
        )


def _brief(*, media_type: CreativeMediaType = CreativeMediaType.IMAGE) -> CreativeBriefView:
    return CreativeBriefView(
        id=uuid4(),
        product_id=uuid4(),
        action_id=uuid4(),
        experiment_id=uuid4(),
        play_id=uuid4(),
        platform=(
            DistributionPlatform.INSTAGRAM
            if media_type == CreativeMediaType.IMAGE
            else DistributionPlatform.TIKTOK
        ),
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
        fingerprint="a" * 64,
        created_at=datetime.now(UTC),
    )


def test_openai_image_generator_persists_bytes_and_returns_partizan_public_url() -> None:
    openai = FakeOpenAIImageClient()
    media = FakeMediaStore()
    generator = OpenAICreativeImageGenerator(
        client=openai,
        media_store=media,  # type: ignore[arg-type]
        public_base_url="https://growth.example.com",
        model="gpt-image-2",
        size="1024x1536",
        quality="medium",
    )

    result = generator.generate(_brief())

    assert result.outcome == CreativeGenerationOutcome.READY
    assert str(result.public_url) == (
        f"https://growth.example.com/v1/media/creative/{media.media_id}"
    )
    assert result.mime_type == "image/png"
    assert result.width == 1024
    assert result.height == 1536
    assert result.provenance == {
        "generator": "openai_image",
        "model": "gpt-image-2",
        "size": "1024x1536",
        "quality": "medium",
        "media_id": str(media.media_id),
        "sha256": "a" * 64,
    }
    assert media.calls == [(PNG_BYTES, "image/png")]
    assert len(openai.calls) == 1
    assert openai.calls[0]["model"] == "gpt-image-2"
    assert "Oracle" in openai.calls[0]["prompt"]
    assert "Do not fabricate testimonials" in openai.calls[0]["prompt"]


def test_openai_image_generator_refuses_video_without_provider_call() -> None:
    openai = FakeOpenAIImageClient()
    generator = OpenAICreativeImageGenerator(
        client=openai,
        media_store=FakeMediaStore(),  # type: ignore[arg-type]
        public_base_url="https://growth.example.com",
    )

    result = generator.generate(_brief(media_type=CreativeMediaType.VIDEO))

    assert result.outcome == CreativeGenerationOutcome.UNAVAILABLE
    assert result.public_url is None
    assert openai.calls == []


def test_openai_image_generator_failure_is_sanitized() -> None:
    openai = FakeOpenAIImageClient(fail=True)
    generator = OpenAICreativeImageGenerator(
        client=openai,
        media_store=FakeMediaStore(),  # type: ignore[arg-type]
        public_base_url="https://growth.example.com",
    )

    result = generator.generate(_brief())

    assert result.outcome == CreativeGenerationOutcome.FAILED
    assert "internal details" not in result.message
    assert "api_key" not in result.model_dump_json().lower()


def test_creative_media_store_deduplicates_and_public_route_serves_immutable_bytes() -> None:
    first = creative_media_store.put(PNG_BYTES, mime_type="image/png")
    second = creative_media_store.put(PNG_BYTES, mime_type="image/png")

    assert first.id == second.id
    assert first.sha256 == second.sha256

    response = client.get(f"/v1/media/creative/{first.id}")

    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["etag"] == f'"{first.sha256}"'
    assert response.headers["x-content-type-options"] == "nosniff"


def test_creative_media_store_rejects_mime_spoofing() -> None:
    store = CreativeMediaStore()

    with pytest.raises(ValueError, match="do not match"):
        store.put(b"<script>alert(1)</script>", mime_type="image/png")


def test_creative_media_route_returns_404_for_unknown_uuid() -> None:
    response = client.get(f"/v1/media/creative/{UUID(int=0)}")

    assert response.status_code == 404

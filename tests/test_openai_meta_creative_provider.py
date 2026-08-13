import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.creative_assets import CreativeBriefView, CreativeMediaType, CreativePurpose
from app.creative_blob_store import CreativeBlobStore, creative_blob_store
from app.creative_generation import CreativeGenerationOutcome, OpenAIMetaImageCreativeGenerator
from app.distribution_types import DistributionPlatform
from app.main import app
from app.runtime_store import MemoryRuntimeStateStore

client = TestClient(app)


def _brief(
    *,
    platform: DistributionPlatform = DistributionPlatform.INSTAGRAM,
    media_type: CreativeMediaType = CreativeMediaType.IMAGE,
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
            "value_proposition": "Personalized relationship readings",
            "message_hook": "When uncertainty keeps looping in your head",
            "audience": {"market": "US"},
        },
        constraints=[
            "Use only confirmed product facts.",
            "Do not fabricate testimonials or social proof.",
        ],
        fingerprint="a" * 64,
        created_at=datetime.now(UTC),
    )


class FakeImages:
    def __init__(self, image_bytes: bytes) -> None:
        self.image_bytes = image_bytes
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(self.image_bytes).decode("ascii"))]
        )


class FakeOpenAI:
    def __init__(self, image_bytes: bytes = b"fake-png-bytes") -> None:
        self.images = FakeImages(image_bytes)


def test_openai_meta_generator_creates_restart_safe_public_image() -> None:
    store = MemoryRuntimeStateStore()
    blob_store = CreativeBlobStore(store)
    fake = FakeOpenAI(b"generated-image")
    generator = OpenAIMetaImageCreativeGenerator(
        api_key="test-key",
        public_base_url="https://partizan.example",
        model="gpt-image-2",
        quality="medium",
        client=fake,
        blob_store=blob_store,
    )

    result = generator.generate(_brief())

    assert result.outcome == CreativeGenerationOutcome.READY
    assert result.mime_type == "image/png"
    assert result.width == 1024
    assert result.height == 1536
    assert result.public_url is not None
    assert str(result.public_url).startswith(
        "https://partizan.example/v1/public/creative-blobs/"
    )
    assert result.provenance["generator"] == "openai"
    assert result.provenance["model"] == "gpt-image-2"
    assert "test-key" not in result.model_dump_json()

    assert len(fake.images.calls) == 1
    call = fake.images.calls[0]
    assert call["model"] == "gpt-image-2"
    assert call["size"] == "1024x1536"
    assert call["quality"] == "medium"
    assert call["output_format"] == "png"
    assert "Oracle" in call["prompt"]
    assert "fabricating claims" in call["prompt"]

    blob_id = UUID(str(result.public_url).rstrip("/").split("/")[-1])
    view, data = blob_store.get(blob_id)
    assert view.byte_size == len(b"generated-image")
    assert data == b"generated-image"


def test_openai_generator_fails_closed_without_required_configuration() -> None:
    fake = FakeOpenAI()
    no_key = OpenAIMetaImageCreativeGenerator(
        api_key=None,
        public_base_url="https://partizan.example",
        client=fake,
    ).generate(_brief())
    assert no_key.outcome == CreativeGenerationOutcome.UNAVAILABLE
    assert not fake.images.calls

    no_public_url = OpenAIMetaImageCreativeGenerator(
        api_key="test-key",
        public_base_url=None,
        client=fake,
    ).generate(_brief())
    assert no_public_url.outcome == CreativeGenerationOutcome.UNAVAILABLE
    assert not fake.images.calls


def test_openai_image_provider_does_not_fake_tiktok_video_readiness() -> None:
    fake = FakeOpenAI()
    result = OpenAIMetaImageCreativeGenerator(
        api_key="test-key",
        public_base_url="https://partizan.example",
        client=fake,
    ).generate(
        _brief(
            platform=DistributionPlatform.TIKTOK,
            media_type=CreativeMediaType.VIDEO,
        )
    )

    assert result.outcome == CreativeGenerationOutcome.UNAVAILABLE
    assert not fake.images.calls


def test_public_creative_blob_route_serves_exact_bytes_without_operator_auth() -> None:
    creative_blob_store.reset()
    blob = creative_blob_store.put(data=b"public-image", mime_type="image/png")

    response = client.get(f"/v1/public/creative-blobs/{blob.id}")

    assert response.status_code == 200
    assert response.content == b"public-image"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["etag"] == f'"{blob.sha256}"'
    assert "immutable" in response.headers["cache-control"]

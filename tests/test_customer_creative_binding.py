from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.creative_assets import (
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeMediaType,
    CreativePurpose,
)
from app.creative_blob_store import CreativeBlobStore
from app.customer_creative_binding import CustomerCreativeBindingService
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore

PUBLIC_BASE_URL = "https://partizan.example"
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00"


def _asset(*, blob_id, sha256: str, url: str) -> CreativeAssetView:
    now = datetime.now(UTC)
    return CreativeAssetView(
        id=uuid4(),
        product_id=uuid4(),
        action_id=uuid4(),
        brief_id=uuid4(),
        brief_fingerprint="a" * 64,
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        source=CreativeAssetSource.GENERATED,
        status=CreativeAssetStatus.READY,
        public_url=url,
        mime_type="video/mp4",
        duration_seconds=8,
        provenance={"blob_id": str(blob_id), "sha256": sha256},
        created_at=now,
        updated_at=now,
    )


def test_exact_customer_video_requires_canonical_immutable_partizan_blob() -> None:
    store = MemoryRuntimeStateStore()
    blobs = CreativeBlobStore(store)
    blob = blobs.put(data=VIDEO_BYTES, mime_type="video/mp4")
    asset = _asset(
        blob_id=blob.id,
        sha256=blob.sha256,
        url=f"{PUBLIC_BASE_URL}/v1/public/creative-blobs/{blob.id}",
    )
    service = CustomerCreativeBindingService(
        blob_store=blobs,
        public_base_url=PUBLIC_BASE_URL,
    )

    binding = service.validate_exact_video(asset)

    assert binding.blob_id == blob.id
    assert binding.sha256 == blob.sha256


def test_exact_customer_video_rejects_mutable_external_url_even_with_blob_provenance() -> None:
    store = MemoryRuntimeStateStore()
    blobs = CreativeBlobStore(store)
    blob = blobs.put(data=VIDEO_BYTES, mime_type="video/mp4")
    asset = _asset(
        blob_id=blob.id,
        sha256=blob.sha256,
        url="https://cdn.example.com/mutable-video.mp4",
    )
    service = CustomerCreativeBindingService(
        blob_store=blobs,
        public_base_url=PUBLIC_BASE_URL,
    )

    with pytest.raises(ValueError, match="canonical Partizan immutable blob URL"):
        service.validate_exact_video(asset)


def test_exact_customer_video_rejects_sha_substitution() -> None:
    store = MemoryRuntimeStateStore()
    blobs = CreativeBlobStore(store)
    blob = blobs.put(data=VIDEO_BYTES, mime_type="video/mp4")
    asset = _asset(
        blob_id=blob.id,
        sha256="f" * 64,
        url=f"{PUBLIC_BASE_URL}/v1/public/creative-blobs/{blob.id}",
    )
    service = CustomerCreativeBindingService(
        blob_store=blobs,
        public_base_url=PUBLIC_BASE_URL,
    )

    with pytest.raises(ValueError, match="SHA-256 no longer matches"):
        service.validate_exact_video(asset)

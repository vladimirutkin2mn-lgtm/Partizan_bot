from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.config import get_settings
from app.creative_assets import CreativeAssetView, CreativeMediaType
from app.creative_blob_store import CreativeBlobStore, creative_blob_store


@dataclass(frozen=True)
class CustomerCreativeBlobBinding:
    blob_id: UUID
    sha256: str


class CustomerCreativeBindingService:
    def __init__(
        self,
        *,
        blob_store: CreativeBlobStore | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self._blob_store = blob_store or creative_blob_store
        configured = (
            public_base_url
            if public_base_url is not None
            else get_settings().partizan_public_base_url
        )
        self._public_base_url = configured.rstrip("/") if configured else None

    def validate_exact_video(self, asset: CreativeAssetView) -> CustomerCreativeBlobBinding:
        if asset.media_type != CreativeMediaType.VIDEO:
            raise ValueError("Customer creative binding requires a video asset.")
        if asset.public_url is None:
            raise ValueError("Exact customer video requires a public review URL.")
        if self._public_base_url is None:
            raise ValueError(
                "PARTIZAN_PUBLIC_BASE_URL is required for exact customer video confirmation."
            )

        provenance = asset.provenance if isinstance(asset.provenance, dict) else {}
        raw_blob_id = provenance.get("blob_id")
        raw_sha256 = str(provenance.get("sha256") or "").strip().lower()
        try:
            blob_id = UUID(str(raw_blob_id))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Exact customer video must use a Partizan-managed immutable creative blob."
            ) from exc
        if len(raw_sha256) != 64 or any(char not in "0123456789abcdef" for char in raw_sha256):
            raise ValueError(
                "Exact customer video is missing its immutable SHA-256 content binding."
            )

        expected_url = f"{self._public_base_url}/v1/public/creative-blobs/{blob_id}"
        if str(asset.public_url) != expected_url:
            raise ValueError(
                "Exact customer video must use its canonical Partizan immutable blob URL."
            )

        try:
            blob, _ = self._blob_store.get(blob_id)
        except (KeyError, ValueError) as exc:
            raise ValueError(
                "Exact customer video blob is missing or failed integrity validation."
            ) from exc
        if blob.mime_type != "video/mp4" or asset.mime_type != "video/mp4":
            raise ValueError("Exact customer video must be a validated video/mp4 blob.")
        if blob.sha256 != raw_sha256:
            raise ValueError("Exact customer video SHA-256 no longer matches the immutable blob.")
        return CustomerCreativeBlobBinding(blob_id=blob_id, sha256=raw_sha256)


customer_creative_binding_service = CustomerCreativeBindingService()

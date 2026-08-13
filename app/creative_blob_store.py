from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.runtime_store import RuntimeStateStore, get_runtime_store

CREATIVE_BLOB_NAMESPACE = "creative_blob"
_MAX_BLOB_BYTES = 64 * 1024 * 1024
_ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "video/mp4"}


class CreativeBlobView(BaseModel):
    id: UUID
    mime_type: str
    byte_size: int = Field(ge=1, le=_MAX_BLOB_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime


class CreativeBlobStore:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def put(self, *, data: bytes, mime_type: str) -> CreativeBlobView:
        normalized_mime = mime_type.strip().lower()
        if normalized_mime not in _ALLOWED_MIME_TYPES:
            raise ValueError("Creative blob mime type is not supported")
        if not data:
            raise ValueError("Creative blob cannot be empty")
        if len(data) > _MAX_BLOB_BYTES:
            raise ValueError("Creative blob exceeds the 64 MiB limit")
        blob_id = uuid4()
        view = CreativeBlobView(
            id=blob_id,
            mime_type=normalized_mime,
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            created_at=datetime.now(UTC),
        )
        self._store.put(
            CREATIVE_BLOB_NAMESPACE,
            str(blob_id),
            {
                **view.model_dump(mode="json"),
                "data_b64": base64.b64encode(data).decode("ascii"),
            },
        )
        return view

    def get(self, blob_id: UUID) -> tuple[CreativeBlobView, bytes]:
        payload = self._store.get(CREATIVE_BLOB_NAMESPACE, str(blob_id))
        if payload is None:
            raise KeyError(blob_id)
        view = CreativeBlobView.model_validate(payload)
        encoded = payload.get("data_b64")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("Creative blob payload is incomplete")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Creative blob payload is invalid") from exc
        if len(data) != view.byte_size or hashlib.sha256(data).hexdigest() != view.sha256:
            raise ValueError("Creative blob integrity check failed")
        return view, data

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CREATIVE_BLOB_NAMESPACE)


creative_blob_store = CreativeBlobStore()

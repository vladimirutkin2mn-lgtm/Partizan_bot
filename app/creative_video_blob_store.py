from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.runtime_store import RuntimeStateStore, get_runtime_store

CREATIVE_VIDEO_BLOB_NAMESPACE = "creative_video_blob"
_MAX_VIDEO_BYTES = 64 * 1024 * 1024


class CreativeVideoBlobView(BaseModel):
    id: UUID
    mime_type: str = "video/mp4"
    byte_size: int = Field(ge=1, le=_MAX_VIDEO_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime


class CreativeVideoBlobStore:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def put(self, data: bytes) -> CreativeVideoBlobView:
        if not data:
            raise ValueError("Creative video blob cannot be empty")
        if len(data) > _MAX_VIDEO_BYTES:
            raise ValueError("Creative video blob exceeds the 64 MiB limit")
        blob_id = uuid4()
        view = CreativeVideoBlobView(
            id=blob_id,
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            created_at=datetime.now(UTC),
        )
        self._store.put(
            CREATIVE_VIDEO_BLOB_NAMESPACE,
            str(blob_id),
            {
                **view.model_dump(mode="json"),
                "data_b64": base64.b64encode(data).decode("ascii"),
            },
        )
        return view

    def get(self, blob_id: UUID) -> tuple[CreativeVideoBlobView, bytes]:
        payload = self._store.get(CREATIVE_VIDEO_BLOB_NAMESPACE, str(blob_id))
        if payload is None:
            raise KeyError(blob_id)
        view = CreativeVideoBlobView.model_validate(payload)
        encoded = payload.get("data_b64")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("Creative video blob payload is incomplete")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Creative video blob payload is invalid") from exc
        if len(data) != view.byte_size or hashlib.sha256(data).hexdigest() != view.sha256:
            raise ValueError("Creative video blob integrity check failed")
        return view, data

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CREATIVE_VIDEO_BLOB_NAMESPACE)


creative_video_blob_store = CreativeVideoBlobStore()

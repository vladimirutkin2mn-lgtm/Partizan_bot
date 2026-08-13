from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, String, func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, get_sync_session_factory

MAX_CREATIVE_MEDIA_BYTES = 20 * 1024 * 1024
SUPPORTED_CREATIVE_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
}


class CreativeMediaBlob(Base):
    __tablename__ = "creative_media_blobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    byte_size: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@dataclass(frozen=True, slots=True)
class CreativeMediaRecord:
    id: uuid.UUID
    sha256: str
    mime_type: str
    byte_size: int
    content: bytes
    created_at: datetime


class CreativeMediaStore:
    def put(self, content: bytes, *, mime_type: str) -> CreativeMediaRecord:
        if mime_type not in SUPPORTED_CREATIVE_IMAGE_MIME_TYPES:
            raise ValueError(f"Unsupported creative image MIME type: {mime_type}")
        if not content:
            raise ValueError("Creative media content cannot be empty")
        if len(content) > MAX_CREATIVE_MEDIA_BYTES:
            raise ValueError("Creative media content exceeds 20 MiB limit")

        digest = hashlib.sha256(content).hexdigest()
        factory = get_sync_session_factory()
        with factory() as session:
            existing = session.scalar(
                select(CreativeMediaBlob).where(CreativeMediaBlob.sha256 == digest)
            )
            if existing is not None:
                if existing.mime_type != mime_type:
                    raise ValueError("Existing creative media hash has a different MIME type")
                return self._record(existing)

            blob = CreativeMediaBlob(
                sha256=digest,
                mime_type=mime_type,
                byte_size=len(content),
                content=content,
            )
            session.add(blob)
            session.commit()
            session.refresh(blob)
            return self._record(blob)

    def get(self, media_id: uuid.UUID) -> CreativeMediaRecord:
        factory = get_sync_session_factory()
        with factory() as session:
            blob = session.get(CreativeMediaBlob, media_id)
            if blob is None:
                raise KeyError(media_id)
            return self._record(blob)

    def _record(self, blob: CreativeMediaBlob) -> CreativeMediaRecord:
        return CreativeMediaRecord(
            id=blob.id,
            sha256=blob.sha256,
            mime_type=blob.mime_type,
            byte_size=blob.byte_size,
            content=bytes(blob.content),
            created_at=blob.created_at,
        )


creative_media_store = CreativeMediaStore()

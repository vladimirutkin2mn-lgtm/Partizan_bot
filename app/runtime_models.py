from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RuntimeSnapshot(Base):
    """Transitional durable snapshot for current service-shaped runtime state.

    Normalized domain tables remain authoritative where the runtime already writes them.
    This table lets the existing service APIs become restart-safe incrementally without
    forcing one giant rewrite of every endpoint in the same change.
    """

    __tablename__ = "runtime_snapshots"

    namespace: Mapped[str] = mapped_column(String(80), primary_key=True)
    entity_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

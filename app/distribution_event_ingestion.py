from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.runtime_store import RuntimeStateStore, get_runtime_store

DISTRIBUTION_EVENT_KEY_NAMESPACE = "distribution_event_ingestion_key"
DISTRIBUTION_EVENT_KEY_HEADER = "X-Partizan-Event-Key"
DISTRIBUTION_EVENT_KEY_PREFIX = "ptz_evt_"


class DistributionEventKeyStatusView(BaseModel):
    product_id: UUID
    configured: bool
    key_hint: str | None = None
    created_at: datetime | None = None


class DistributionEventKeyCreateView(DistributionEventKeyStatusView):
    event_key: str = Field(min_length=32)


class DistributionEventKeyService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def rotate(self, product_id: UUID) -> DistributionEventKeyCreateView:
        event_key = f"{DISTRIBUTION_EVENT_KEY_PREFIX}{secrets.token_urlsafe(32)}"
        now = datetime.now(UTC)
        hint = self._hint(event_key)
        self._store.put(
            DISTRIBUTION_EVENT_KEY_NAMESPACE,
            str(product_id),
            {
                "product_id": str(product_id),
                "key_digest": self._digest(event_key),
                "key_hint": hint,
                "created_at": now.isoformat(),
            },
        )
        return DistributionEventKeyCreateView(
            product_id=product_id,
            configured=True,
            key_hint=hint,
            created_at=now,
            event_key=event_key,
        )

    def status(self, product_id: UUID) -> DistributionEventKeyStatusView:
        payload = self._store.get(DISTRIBUTION_EVENT_KEY_NAMESPACE, str(product_id))
        if payload is None:
            return DistributionEventKeyStatusView(product_id=product_id, configured=False)
        return DistributionEventKeyStatusView(
            product_id=product_id,
            configured=True,
            key_hint=str(payload["key_hint"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
        )

    def verify(self, product_id: UUID, event_key: str | None) -> bool:
        if not event_key or not event_key.startswith(DISTRIBUTION_EVENT_KEY_PREFIX):
            return False
        payload = self._store.get(DISTRIBUTION_EVENT_KEY_NAMESPACE, str(product_id))
        if payload is None:
            return False
        expected = str(payload.get("key_digest") or "")
        if not expected:
            return False
        return hmac.compare_digest(self._digest(event_key), expected)

    def revoke(self, product_id: UUID) -> DistributionEventKeyStatusView:
        self._store.delete(DISTRIBUTION_EVENT_KEY_NAMESPACE, str(product_id))
        return DistributionEventKeyStatusView(product_id=product_id, configured=False)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(DISTRIBUTION_EVENT_KEY_NAMESPACE)

    def _digest(self, event_key: str) -> str:
        return hashlib.sha256(event_key.encode("utf-8")).hexdigest()

    def _hint(self, event_key: str) -> str:
        return f"{DISTRIBUTION_EVENT_KEY_PREFIX}…{event_key[-6:]}"


distribution_event_key_service = DistributionEventKeyService()

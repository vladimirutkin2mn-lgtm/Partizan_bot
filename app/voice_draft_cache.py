"""Durable, fail-closed idempotency for paid v17 Telegram draft generation.

A request manifest is derived from the *exact* system/user prompt bytes, voice
version and product/project scope. No request text is stored in the manifest
record; only the response draft is persisted on success. A reserved, failed or
unknown request must never launch another possibly paid provider call merely
because a caller retries. Recovery requires a separately reviewed operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.voice_profiles import fingerprint, utcnow

VOICE_DRAFT_GENERATION_NAMESPACE = "voice_draft_generations"
VOICE_DRAFT_REQUEST_SCHEMA = "persisted_telegram_draft_request_v1"


@dataclass(frozen=True, slots=True)
class VoiceDraftReservation:
    key: str
    expected: dict
    cached_draft: dict | None = None


class VoiceDraftCache:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store if store is not None else get_runtime_store()

    def reserve(
        self, *, project_id: UUID, product_id: UUID,
        voice_version_id: UUID, voice_content_hash: str,
        system_prompt: str, user_context: str,
    ) -> VoiceDraftReservation:
        request_hash = fingerprint({
            "schema": VOICE_DRAFT_REQUEST_SCHEMA,
            "system": system_prompt,
            "user": user_context,
        })
        # Include all ownership/version fields in the key. A trial version
        # must not inherit the champion's cached answer, even with the same
        # context. A change of text, policy or source changes request_hash.
        key = fingerprint({
            "request_hash": request_hash,
            "project_id": str(project_id),
            "product_id": str(product_id),
            "voice_version_id": str(voice_version_id),
            "voice_content_hash": voice_content_hash,
        })
        expected = {
            "id": key, "project_id": str(project_id),
            "product_id": str(product_id),
            "voice_version_id": str(voice_version_id),
            "voice_content_hash": voice_content_hash,
            "request_hash": request_hash,
            "status": "RESERVED", "created_at": utcnow(),
        }
        if self._store.put_if_absent(
            VOICE_DRAFT_GENERATION_NAMESPACE, key, expected
        ):
            return VoiceDraftReservation(key=key, expected=expected)
        existing = self._store.get(VOICE_DRAFT_GENERATION_NAMESPACE, key)
        if existing is None:
            raise RuntimeError("VOICE_DRAFT_CACHE_STATE_UNAVAILABLE")
        for field in (
            "id", "project_id", "product_id", "voice_version_id",
            "voice_content_hash", "request_hash",
        ):
            if existing.get(field) != expected[field]:
                raise PermissionError("VOICE_DRAFT_CACHE_SCOPE_CONFLICT")
        if existing.get("status") == "SUCCEEDED":
            payload = existing.get("draft")
            if not isinstance(payload, dict):
                raise RuntimeError("VOICE_DRAFT_CACHE_CORRUPT")
            return VoiceDraftReservation(
                key=key, expected=existing, cached_draft=dict(payload)
            )
        # RESERVED can mean another worker has the request in flight;
        # UNKNOWN may mean the provider charged us before a timeout.
        raise RuntimeError("VOICE_DRAFT_ALREADY_RESERVED_OR_UNRECONCILED")

    def complete(self, reservation: VoiceDraftReservation, draft: dict) -> None:
        if reservation.cached_draft is not None:
            raise RuntimeError("VOICE_DRAFT_ALREADY_COMPLETE")
        completed = {
            **reservation.expected, "status": "SUCCEEDED",
            "completed_at": utcnow(), "draft": dict(draft),
        }
        if not self._store.compare_and_swap(
            VOICE_DRAFT_GENERATION_NAMESPACE, reservation.key,
            reservation.expected, completed,
        ):
            raise RuntimeError("VOICE_DRAFT_CACHE_COMMIT_CONFLICT")

    def stop(self, reservation: VoiceDraftReservation, *, status: str) -> None:
        if status not in {"PROVIDER_OUTCOME_UNKNOWN", "POLICY_REJECTED"}:
            raise ValueError("VOICE_DRAFT_INVALID_STOP_STATUS")
        stopped = {
            **reservation.expected, "status": status, "completed_at": utcnow()
        }
        if not self._store.compare_and_swap(
            VOICE_DRAFT_GENERATION_NAMESPACE, reservation.key,
            reservation.expected, stopped,
        ):
            raise RuntimeError("VOICE_DRAFT_CACHE_COMMIT_CONFLICT")

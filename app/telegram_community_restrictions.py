from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.runtime_store import RuntimeStateStore, get_runtime_store

TELEGRAM_COMMUNITY_RESTRICTION_NAMESPACE = "telegram_community_restriction"

_HARD_RESTRICTION_CODES = {
    "ACCOUNT_BANNED_IN_COMMUNITY",
    "WRITE_FORBIDDEN",
}
_HARD_RESTRICTION_SIGNALS = {
    "ACCOUNT_RESTRICTED",
    "WRITE_RESTRICTED",
}


class TelegramCommunityRestrictionMemory:
    """Durable, project-scoped memory of observed Telegram write restrictions.

    A confirmed account/community restriction is stronger evidence than a later read-only
    membership preflight. Once Telegram rejects a write as banned/forbidden, every post
    under the same public community handle is treated as NO_WRITE_ACCESS until an operator
    explicitly clears the stored restriction.
    """

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def get(self, project_id: UUID, community_handle: str) -> dict | None:
        return self._store.get(
            TELEGRAM_COMMUNITY_RESTRICTION_NAMESPACE,
            self._key(project_id, community_handle),
        )

    def remember(
        self,
        project_id: UUID,
        community_handle: str,
        *,
        reason: str,
        source_action_id: UUID | str | None = None,
        restriction_signal: str | None = None,
    ) -> dict:
        normalized = self._normalize_handle(community_handle)
        if not normalized:
            raise ValueError("community_handle is required")
        now = datetime.now(UTC).isoformat()
        existing = self.get(project_id, normalized) or {}
        record = {
            "project_id": str(project_id),
            "community_handle": normalized,
            "reason": str(reason or "NO_WRITE_ACCESS"),
            "restriction_signal": (
                str(restriction_signal) if restriction_signal else existing.get("restriction_signal")
            ),
            "source_action_id": (
                str(source_action_id) if source_action_id is not None else existing.get("source_action_id")
            ),
            "first_observed_at": existing.get("first_observed_at") or now,
            "last_observed_at": now,
        }
        self._store.put(
            TELEGRAM_COMMUNITY_RESTRICTION_NAMESPACE,
            self._key(project_id, normalized),
            record,
        )
        return record

    def observe_publish_receipt(self, project_id: UUID, receipt: Any) -> dict | None:
        outcome = getattr(getattr(receipt, "outcome", None), "value", None)
        if outcome is None:
            outcome = str(getattr(receipt, "outcome", "") or "")
        if str(outcome).upper() != "FAILED":
            return None

        metadata = getattr(receipt, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        error_code = str(metadata.get("error_code") or "").upper()
        restriction_signal = str(metadata.get("restriction_signal") or "").upper()
        if (
            error_code not in _HARD_RESTRICTION_CODES
            and restriction_signal not in _HARD_RESTRICTION_SIGNALS
        ):
            return None

        handle = self._normalize_handle(str(metadata.get("target_username") or ""))
        if not handle:
            return None
        return self.remember(
            project_id,
            handle,
            reason=error_code or "NO_WRITE_ACCESS",
            restriction_signal=restriction_signal or None,
            source_action_id=getattr(receipt, "action_id", None),
        )

    def clear(self, project_id: UUID, community_handle: str) -> None:
        self._store.delete(
            TELEGRAM_COMMUNITY_RESTRICTION_NAMESPACE,
            self._key(project_id, community_handle),
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TELEGRAM_COMMUNITY_RESTRICTION_NAMESPACE)

    @classmethod
    def _key(cls, project_id: UUID, community_handle: str) -> str:
        normalized = cls._normalize_handle(community_handle)
        return f"{project_id}:{normalized}"

    @staticmethod
    def _normalize_handle(value: str) -> str:
        return str(value or "").strip().lstrip("@").casefold()


telegram_community_restriction_memory = TelegramCommunityRestrictionMemory()

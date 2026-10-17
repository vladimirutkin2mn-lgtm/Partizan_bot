from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.runtime_store import RuntimeStateStore, get_runtime_store

TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE = "telegram_community_registry"

_DYNAMIC_METADATA_KEYS = {
    "access_state",
    "action_target_mode",
    "action_target_quality",
    "action_target_resolution",
    "action_target_specific",
    "action_target_url",
    "actionability",
    "about",
    "community_only_reason",
    "handle",
    "last_activity_at",
    "linked_discussion_id",
    "member_count",
    "native_research_status",
    "recent_context",
    "source_checked_at",
    "source_type",
    "standalone_post_surface",
    "surface_capabilities",
    "surface_kind",
    "telegram_entity_id",
}

_FIT_METADATA_KEYS = {
    "community_fit_pass",
    "community_fit_reason",
    "community_fit_terms",
    "registry_confirmed",
}


class TelegramCommunityRegistry:
    """Persist one logical Telegram community while keeping fit and native state separate."""

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def list_for_product(self, product_id: object) -> list[dict]:
        return [
            row
            for row in self._store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)
            if str(row.get("product_id") or "") == str(product_id)
        ]

    def hydrate_metadata(self, row: dict) -> dict:
        metadata = dict(row.get("metadata") or {})
        native_snapshot = row.get("native_snapshot")
        if isinstance(native_snapshot, dict):
            for key in _DYNAMIC_METADATA_KEYS:
                metadata.pop(key, None)
            metadata.update(native_snapshot)

        fit = row.get("audience_fit")
        if isinstance(fit, dict) and fit.get("confirmed") is True:
            metadata["registry_confirmed"] = True
            metadata["community_fit_pass"] = True
            metadata["community_fit_reason"] = str(
                fit.get("reason") or metadata.get("community_fit_reason") or "REGISTRY_CONFIRMED"
            )
            metadata["community_fit_terms"] = list(
                fit.get("terms") or metadata.get("community_fit_terms") or []
            )
        return metadata

    def upsert(
        self,
        *,
        product_id: object,
        canonical_key: str,
        title: str,
        url: str | None,
        kind: str,
        metadata: dict,
    ) -> dict:
        incoming = {
            "product_id": str(product_id),
            "canonical_key": str(canonical_key or ""),
            "title": str(title or ""),
            "url": str(url or ""),
            "kind": str(kind or ""),
            "metadata": dict(metadata or {}),
        }
        matched = self._matching_rows(product_id, incoming)
        merged = self._merge_rows(product_id, matched, incoming)
        storage_key = str(merged["id"])

        self._store.put(
            TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
            storage_key,
            merged,
        )
        for row in matched:
            old_key = str(row.get("id") or "")
            if old_key and old_key != storage_key:
                self._store.delete(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, old_key)
        return merged

    def _matching_rows(self, product_id: object, incoming: dict) -> list[dict]:
        rows = self.list_for_product(product_id)
        aliases = self._aliases(incoming)
        matched: list[dict] = []
        remaining = list(rows)
        changed = True
        while changed:
            changed = False
            next_remaining: list[dict] = []
            for row in remaining:
                row_aliases = self._aliases(row)
                if aliases & row_aliases:
                    matched.append(row)
                    aliases.update(row_aliases)
                    changed = True
                else:
                    next_remaining.append(row)
            remaining = next_remaining
        return matched

    def _merge_rows(self, product_id: object, rows: list[dict], incoming: dict) -> dict:
        now = datetime.now(UTC).isoformat()
        all_rows = [*rows, incoming]
        aliases = sorted({alias for row in all_rows for alias in self._aliases(row)})

        entity_id = self._first_entity_id(all_rows)
        handle = self._first_handle(all_rows)
        invite_hash = self._first_invite_hash(all_rows)
        identity = (
            f"entity:{entity_id}"
            if entity_id is not None
            else f"handle:{handle}"
            if handle
            else f"invite:{invite_hash}"
            if invite_hash
            else f"canonical:{str(incoming.get('canonical_key') or '').casefold()}"
        )
        storage_key = f"{product_id}:{identity}"

        audience_fit = self._merged_audience_fit(rows, incoming, now)
        native_snapshot = self._latest_native_snapshot(rows, incoming)
        compatibility_metadata = self._compatibility_metadata(
            rows,
            incoming,
            audience_fit,
            native_snapshot,
        )

        first_seen_at = min(
            [
                str(row.get("first_seen_at") or row.get("updated_at") or now)
                for row in rows
            ]
            or [now]
        )
        latest_verified_row = self._latest_verified_row(rows, incoming)
        display_source = latest_verified_row or incoming

        return {
            "id": storage_key,
            "product_id": str(product_id),
            "canonical_key": self._canonical_key(entity_id, handle, invite_hash, incoming),
            "title": str(display_source.get("title") or incoming.get("title") or ""),
            "url": str(display_source.get("url") or incoming.get("url") or ""),
            "kind": str(display_source.get("kind") or incoming.get("kind") or ""),
            "aliases": aliases,
            "audience_fit": audience_fit,
            "native_snapshot": native_snapshot,
            "metadata": compatibility_metadata,
            "first_seen_at": first_seen_at,
            "updated_at": now,
        }

    def _merged_audience_fit(self, rows: list[dict], incoming: dict, now: str) -> dict:
        historical: list[dict] = []
        for row in rows:
            fit = row.get("audience_fit")
            if isinstance(fit, dict):
                historical.append(dict(fit))
                continue
            metadata = dict(row.get("metadata") or {})
            if metadata.get("community_fit_pass") is True:
                historical.append(
                    {
                        "confirmed": True,
                        "reason": str(metadata.get("community_fit_reason") or "REGISTRY_CONFIRMED"),
                        "terms": list(metadata.get("community_fit_terms") or []),
                        "confirmed_at": str(row.get("updated_at") or now),
                    }
                )

        incoming_metadata = dict(incoming.get("metadata") or {})
        incoming_pass = incoming_metadata.get("community_fit_pass") is True
        confirmed = incoming_pass or any(item.get("confirmed") is True for item in historical)
        if incoming_pass:
            reason = str(incoming_metadata.get("community_fit_reason") or "COMMUNITY_FIT")
            terms = list(incoming_metadata.get("community_fit_terms") or [])
            confirmed_at = now
        else:
            prior = next(
                (item for item in reversed(historical) if item.get("confirmed") is True),
                {},
            )
            reason = str(prior.get("reason") or "")
            terms = list(prior.get("terms") or [])
            confirmed_at = str(prior.get("confirmed_at") or "")

        return {
            "confirmed": confirmed,
            "reason": reason,
            "terms": terms[:20],
            "confirmed_at": confirmed_at,
        }

    def _latest_native_snapshot(self, rows: list[dict], incoming: dict) -> dict:
        candidates: list[dict] = []
        for row in rows:
            snapshot = row.get("native_snapshot")
            if isinstance(snapshot, dict) and snapshot:
                candidates.append(dict(snapshot))
            else:
                metadata = dict(row.get("metadata") or {})
                if metadata:
                    candidates.append(self._native_fields(metadata))
        incoming_metadata = dict(incoming.get("metadata") or {})
        if incoming_metadata:
            candidates.append(self._native_fields(incoming_metadata))
        candidates = [item for item in candidates if item]
        if not candidates:
            return {}
        return max(candidates, key=self._native_rank)

    def _latest_verified_row(self, rows: list[dict], incoming: dict) -> dict | None:
        candidates = [*rows, incoming]
        verified = [
            row
            for row in candidates
            if str(self.hydrate_metadata(row).get("native_research_status") or "").upper()
            == "VERIFIED"
        ]
        if not verified:
            return None
        return max(
            verified,
            key=lambda row: self._timestamp_value(
                self.hydrate_metadata(row).get("source_checked_at")
                or row.get("updated_at")
            ),
        )

    def _compatibility_metadata(
        self,
        rows: list[dict],
        incoming: dict,
        audience_fit: dict,
        native_snapshot: dict,
    ) -> dict:
        metadata: dict = {}
        for row in rows:
            metadata.update(dict(row.get("metadata") or {}))
        metadata.update(dict(incoming.get("metadata") or {}))
        for key in _DYNAMIC_METADATA_KEYS | _FIT_METADATA_KEYS:
            metadata.pop(key, None)
        metadata.update(native_snapshot)
        if audience_fit.get("confirmed") is True:
            metadata.update(
                {
                    "registry_confirmed": True,
                    "community_fit_pass": True,
                    "community_fit_reason": str(
                        audience_fit.get("reason") or "REGISTRY_CONFIRMED"
                    ),
                    "community_fit_terms": list(audience_fit.get("terms") or []),
                }
            )
        return metadata

    @classmethod
    def _aliases(cls, row: dict) -> set[str]:
        metadata = cls._row_metadata(row)
        aliases = {str(item).casefold() for item in list(row.get("aliases") or []) if item}
        canonical_key = str(row.get("canonical_key") or "").strip().casefold()
        if canonical_key:
            aliases.add(f"canonical:{canonical_key}")
        registry_key = str(metadata.get("community_registry_key") or "").strip().casefold()
        if registry_key:
            aliases.add(f"canonical:{registry_key}")

        entity_id = metadata.get("telegram_entity_id")
        if entity_id is not None:
            aliases.add(f"entity:{entity_id}")
        handle = str(metadata.get("handle") or "").strip().lstrip("@").casefold()
        if handle:
            aliases.add(f"handle:{handle}")
            aliases.add(f"canonical:telegram:{handle}:community")
        invite_hash = str(metadata.get("invite_token_hash") or "").strip().casefold()
        if invite_hash:
            aliases.add(f"invite:{invite_hash}")

        url_handle = cls._handle_from_url(str(row.get("url") or ""))
        if url_handle:
            aliases.add(f"handle:{url_handle}")
            aliases.add(f"canonical:telegram:{url_handle}:community")
        return aliases

    @classmethod
    def _row_metadata(cls, row: dict) -> dict:
        metadata = dict(row.get("metadata") or {})
        snapshot = row.get("native_snapshot")
        if isinstance(snapshot, dict):
            metadata.update(snapshot)
        return metadata

    @classmethod
    def _first_entity_id(cls, rows: list[dict]) -> object | None:
        for row in reversed(rows):
            value = cls._row_metadata(row).get("telegram_entity_id")
            if value is not None:
                return value
        return None

    @classmethod
    def _first_handle(cls, rows: list[dict]) -> str:
        for row in reversed(rows):
            value = str(cls._row_metadata(row).get("handle") or "").strip().lstrip("@").casefold()
            if value:
                return value
        for row in reversed(rows):
            value = cls._handle_from_url(str(row.get("url") or ""))
            if value:
                return value
        return ""

    @classmethod
    def _first_invite_hash(cls, rows: list[dict]) -> str:
        for row in reversed(rows):
            value = str(cls._row_metadata(row).get("invite_token_hash") or "").strip().casefold()
            if value:
                return value
        return ""

    @staticmethod
    def _canonical_key(
        entity_id: object | None,
        handle: str,
        invite_hash: str,
        incoming: dict,
    ) -> str:
        if entity_id is not None:
            return f"telegram:{entity_id}:community"
        if handle:
            return f"telegram:{handle}:community"
        if invite_hash:
            return f"telegram:invite:{invite_hash}:community"
        return str(incoming.get("canonical_key") or "")

    @classmethod
    def _native_fields(cls, metadata: dict) -> dict:
        return {key: metadata[key] for key in _DYNAMIC_METADATA_KEYS if key in metadata}

    @classmethod
    def _native_rank(cls, metadata: dict) -> tuple[int, float]:
        verified = (
            1
            if str(metadata.get("native_research_status") or "").upper() == "VERIFIED"
            else 0
        )
        timestamp = cls._timestamp_value(
            metadata.get("source_checked_at") or metadata.get("last_activity_at")
        )
        return verified, timestamp

    @staticmethod
    def _timestamp_value(value: object) -> float:
        if not value:
            return 0.0
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).timestamp()

    @staticmethod
    def _handle_from_url(value: str) -> str:
        if not value:
            return ""
        parts = urlsplit(value)
        if parts.netloc.casefold().removeprefix("www.") not in {"t.me", "telegram.me"}:
            return ""
        segments = [segment for segment in parts.path.split("/") if segment]
        if not segments:
            return ""
        if segments[0].startswith("+") or segments[0].casefold() == "joinchat":
            return ""
        if segments[0].casefold() == "s" and len(segments) > 1:
            return segments[1].casefold()
        return segments[0].casefold()

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.runtime_store import RuntimeStateStore, get_runtime_store

TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE = "telegram_community_registry"
TELEGRAM_COMMUNITY_ALIAS_NAMESPACE = "telegram_community_registry_alias"

_DYNAMIC_METADATA_KEYS = {
    "access_state",
    "action_target_mode",
    "action_target_quality",
    "action_target_resolution",
    "action_target_specific",
    "action_target_url",
    "actionability",
    "about",
    "comment_surface",
    "community_only_reason",
    "community_registry_key",
    "handle",
    "last_activity_at",
    "linked_discussion_id",
    "member_count",
    "native_research_status",
    "recent_context",
    "source_post_category",
    "conversation_signals",
    "reply_surface",
    "source_checked_at",
    "source_type",
    "standalone_post_surface",
    "surface_capabilities",
    "surface_kind",
    "target_age_days",
    "target_published_at",
    "telegram_entity_id",
}

_FIT_METADATA_KEYS = {
    "community_fit_pass",
    "community_fit_reason",
    "community_fit_terms",
    "registry_confirmed",
}


class TelegramCommunityRegistry:
    """Persist one logical Telegram community with fit and native state separated."""

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
                fit.get("reason")
                or metadata.get("community_fit_reason")
                or "REGISTRY_CONFIRMED"
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
        self._index_aliases(product_id, merged)
        for row in matched:
            old_key = str(row.get("id") or "")
            if old_key and old_key != storage_key:
                self._store.delete(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, old_key)
        return merged

    def _matching_rows(self, product_id: object, incoming: dict) -> list[dict]:
        """Resolve rows through stable keys and reverse aliases without a namespace scan."""
        aliases = set(self._aliases(incoming))
        matched_by_id: dict[str, dict] = {}
        checked_storage_keys: set[str] = set()
        checked_aliases: set[str] = set()

        while True:
            storage_keys = self._storage_keys_for_aliases(product_id, aliases)
            for alias in sorted(self._identity_aliases(aliases) - checked_aliases):
                checked_aliases.add(alias)
                index = self._store.get(
                    TELEGRAM_COMMUNITY_ALIAS_NAMESPACE,
                    self._alias_index_key(product_id, alias),
                )
                if isinstance(index, dict):
                    registry_id = str(index.get("registry_id") or "")
                    if registry_id:
                        storage_keys.add(registry_id)

            pending = storage_keys - checked_storage_keys
            if not pending:
                break
            checked_storage_keys.update(pending)
            before_alias_count = len(aliases)
            for storage_key in sorted(pending):
                row = self._store.get(
                    TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
                    storage_key,
                )
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("id") or storage_key)
                if row_id in matched_by_id:
                    continue
                matched_by_id[row_id] = row
                aliases.update(self._aliases(row))
            if len(aliases) == before_alias_count:
                break
        return list(matched_by_id.values())

    def _index_aliases(self, product_id: object, row: dict) -> None:
        registry_id = str(row.get("id") or "")
        if not registry_id:
            return
        for alias in sorted(self._identity_aliases(self._aliases(row))):
            self._store.put(
                TELEGRAM_COMMUNITY_ALIAS_NAMESPACE,
                self._alias_index_key(product_id, alias),
                {
                    "product_id": str(product_id),
                    "alias": alias,
                    "registry_id": registry_id,
                },
            )

    @staticmethod
    def _alias_index_key(product_id: object, alias: str) -> str:
        return f"{product_id}:{str(alias or '').casefold()}"

    @staticmethod
    def _identity_aliases(aliases: set[str]) -> set[str]:
        return {
            alias
            for alias in aliases
            if str(alias).startswith(("entity:", "handle:", "invite:"))
        }

    @staticmethod
    def _storage_keys_for_aliases(product_id: object, aliases: set[str]) -> set[str]:
        keys: set[str] = set()
        prefix = f"{product_id}:"
        for alias in aliases:
            value = str(alias or "").casefold()
            if value.startswith(("entity:", "handle:", "invite:")):
                keys.add(f"{prefix}{value}")
                continue
            if not value.startswith("canonical:telegram:"):
                continue
            canonical = value.removeprefix("canonical:telegram:")
            if canonical.startswith("invite:") and canonical.endswith(":community"):
                token = canonical.removeprefix("invite:").removesuffix(":community")
                if token:
                    keys.add(f"{prefix}invite:{token}")
                continue
            if not canonical.endswith(":community"):
                continue
            identity = canonical.removesuffix(":community")
            if not identity:
                continue
            if identity.lstrip("-").isdigit():
                keys.add(f"{prefix}entity:{identity}")
            else:
                keys.add(f"{prefix}handle:{identity}")
        return keys

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
            "canonical_key": self._canonical_key(
                entity_id,
                handle,
                invite_hash,
                incoming,
            ),
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
                        "reason": str(
                            metadata.get("community_fit_reason")
                            or "REGISTRY_CONFIRMED"
                        ),
                        "terms": list(metadata.get("community_fit_terms") or []),
                        "confirmed_at": str(row.get("updated_at") or now),
                    }
                )

        prior = next(
            (item for item in reversed(historical) if item.get("confirmed") is True),
            {},
        )
        incoming_metadata = dict(incoming.get("metadata") or {})
        incoming_pass = incoming_metadata.get("community_fit_pass") is True
        incoming_reason = str(incoming_metadata.get("community_fit_reason") or "")
        confirmed = incoming_pass or bool(prior)

        if incoming_pass and incoming_reason != "REGISTRY_CONFIRMED":
            reason = incoming_reason or "COMMUNITY_FIT"
            terms = list(incoming_metadata.get("community_fit_terms") or [])
            confirmed_at = now
        elif prior:
            reason = str(prior.get("reason") or "")
            terms = list(prior.get("terms") or [])
            confirmed_at = str(prior.get("confirmed_at") or "")
        elif incoming_pass:
            reason = incoming_reason or "REGISTRY_CONFIRMED"
            terms = list(incoming_metadata.get("community_fit_terms") or [])
            confirmed_at = now
        else:
            reason = ""
            terms = []
            confirmed_at = ""

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
            if str(
                self.hydrate_metadata(row).get("native_research_status") or ""
            ).upper()
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
        aliases = {
            str(item).casefold()
            for item in list(row.get("aliases") or [])
            if item
        }
        canonical_key = str(row.get("canonical_key") or "").strip().casefold()
        if canonical_key:
            aliases.add(f"canonical:{canonical_key}")
        registry_key = str(
            metadata.get("community_registry_key") or ""
        ).strip().casefold()
        if registry_key:
            aliases.add(f"canonical:{registry_key}")

        entity_id = metadata.get("telegram_entity_id")
        if entity_id is not None:
            aliases.add(f"entity:{entity_id}")
        handle = str(metadata.get("handle") or "").strip().lstrip("@").casefold()
        if handle:
            aliases.add(f"handle:{handle}")
            aliases.add(f"canonical:telegram:{handle}:community")
        invite_hash = str(
            metadata.get("invite_token_hash") or ""
        ).strip().casefold()
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
            value = str(
                cls._row_metadata(row).get("handle") or ""
            ).strip().lstrip("@").casefold()
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
            value = str(
                cls._row_metadata(row).get("invite_token_hash") or ""
            ).strip().casefold()
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
        return {
            key: metadata[key]
            for key in _DYNAMIC_METADATA_KEYS
            if key in metadata
        }

    @classmethod
    def _native_rank(cls, metadata: dict) -> tuple[int, float]:
        verified = (
            1
            if str(metadata.get("native_research_status") or "").upper()
            == "VERIFIED"
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
        host = parts.netloc.casefold().removeprefix("www.")
        if host not in {"t.me", "telegram.me"}:
            return ""
        segments = [segment for segment in parts.path.split("/") if segment]
        if not segments:
            return ""
        if segments[0].startswith("+") or segments[0].casefold() == "joinchat":
            return ""
        if segments[0].casefold() == "s" and len(segments) > 1:
            return segments[1].casefold()
        return segments[0].casefold()

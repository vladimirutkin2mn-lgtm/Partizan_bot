from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit
from uuid import UUID

from app.customer_live_opportunities import (
    CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
    CUSTOMER_PROJECT_NAMESPACE,
)
from app.discovery_relevance_learning import (
    DiscoveryRelevanceLearningService,
    discovery_relevance_learning_service,
)
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.platform_discovery import (
    PlatformCandidate,
    PlatformDiscoveryRequest,
    TelegramDiscoveryAdapter,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.search import SearchHit
from app.telegram_community_registry import (
    TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE as _TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
)
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_discovery_strategy import ExpandedTelegramDiscoveryAdapter
from app.telegram_participation_target import select_telegram_participation_target

COMMUNITY_FIRST_TELEGRAM_QUERY_BUDGET = 3
TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE = _TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE

_SEMANTIC_UTILITY_TERMS = {
    "site",
    "telegram",
    "channel",
    "group",
    "chat",
    "community",
    "discussion",
    "comments",
    "questions",
    "public",
    "replies",
    "education",
    "advice",
    "online",
    "any",
    "https",
    "канал",
    "группа",
    "чат",
    "сообщество",
    "обсуждение",
    "комментарии",
    "вопросы",
    "советы",
}


class CommunityFirstTelegramDiscoveryAdapter(ExpandedTelegramDiscoveryAdapter):
    """Treat a Telegram community as the opportunity and posts as evidence/targets."""

    def __init__(
        self,
        research_connector=None,
        *,
        use_default_research_connector: bool = True,
        adaptive_hints: list[str] | tuple[str, ...] | None = None,
        relevance_learning_service: DiscoveryRelevanceLearningService | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        super().__init__(
            research_connector=research_connector,
            use_default_research_connector=use_default_research_connector,
            adaptive_hints=adaptive_hints,
            relevance_learning_service=(
                relevance_learning_service or discovery_relevance_learning_service
            ),
        )
        self._store = store or get_runtime_store()
        self._registry = TelegramCommunityRegistry(self._store)
        self._product_id: UUID | None = None

    def build_requests(self, product, icp) -> list[PlatformDiscoveryRequest]:
        self._product_id = getattr(product, "id", None)
        market, language = self._market_language(product)
        discussion_terms, learning_terms, community_terms = self._participation_terms(language)
        stable_anchor = self._stable_search_anchor(product, icp)
        if not stable_anchor:
            return []
        alternatives = self._compact_alternatives(getattr(icp, "alternatives", []))
        hints = self._relevance_learning.filter_hints(
            getattr(product, "id", None), self._adaptive_hints
        )
        third_anchor = stable_anchor
        third_intent = discussion_terms
        if hints:
            third_anchor = self._bounded_anchor(
                stable_anchor, self._concise_fragment(hints[0], 72)
            )
        elif alternatives:
            third_anchor = self._bounded_anchor(stable_anchor, alternatives)

        requests = [
            self._request(
                OpportunityKind.GROUP,
                stable_anchor,
                community_terms,
                "Telegram group community audience",
                market,
                language,
            ),
            self._request(
                OpportunityKind.CHANNEL,
                stable_anchor,
                learning_terms,
                "Telegram channel community audience",
                market,
                language,
            ),
            self._request(
                OpportunityKind.CHANNEL,
                third_anchor,
                third_intent,
                "Telegram adjacent community",
                market,
                language,
            ),
        ]
        result: list[PlatformDiscoveryRequest] = []
        seen: set[str] = set()
        for request in requests:
            key = " ".join(request.discovery_query.query.casefold().split())
            if key in seen:
                continue
            seen.add(key)
            result.append(request)
        return result[:COMMUNITY_FIRST_TELEGRAM_QUERY_BUDGET]

    async def refresh_registered(self, product, icps) -> list[PlatformCandidate]:
        """Rotate saved peers through native inspection; never run contacts.Search."""
        self._product_id = getattr(product, "id", None)
        if self._product_id is None or not icps:
            return []
        rows = sorted(
            self._registry.list_for_product(self._product_id),
            key=lambda item: str(item.get("id") or item.get("canonical_key") or ""),
        )
        if not rows:
            return []
        requests = self.build_requests(product, icps[0])
        if not requests:
            return []
        request = requests[0]

        # Only public handles can be inspected directly. Unverified invite
        # links remain in the registry and cannot become READY by refresh.
        by_handle: dict[str, dict] = {}
        for row in rows:
            stored = self._registry.hydrate_metadata(row)
            if str(stored.get("access_state") or "").upper() == "JOIN_REQUIRED":
                continue
            handle = str(stored.get("handle") or "").strip().lstrip("@").casefold()
            if not handle:
                continue
            by_handle.setdefault(handle, row)
        handles = sorted(by_handle)
        cursor_namespace = "telegram_registered_native_refresh_cursor"
        cursor_key = str(self._product_id)
        cursor = self._store.get(cursor_namespace, cursor_key) or {}
        offset = int(cursor.get("next_index") or 0) % len(handles) if handles else 0
        batch_size = 3  # Matches the conservative known-peer native request budget.
        batch = (handles[offset:] + handles[:offset])[:batch_size]
        results = {}
        inspection_error = None
        connector = self._research_connector
        inspector = getattr(connector, "inspect_known", None)
        if batch and callable(inspector):
            try:
                inspected = await inspector(batch)
                results = {str(item.handle).casefold(): item for item in inspected}
            except Exception as exc:
                inspection_error = type(exc).__name__
        elif batch:
            inspection_error = "INSPECTION_UNAVAILABLE"

        if batch and inspection_error is None:
            statuses = {
                handle: str(getattr(results.get(handle), "status", "RETRYABLE_FAILED"))
                for handle in batch
            }
            self._store.put(
                cursor_namespace,
                cursor_key,
                {
                    "next_index": (offset + len(batch)) % len(handles),
                    "last_batch": batch,
                    "last_batch_statuses": statuses,
                    "last_attempt_at": datetime.now(UTC).isoformat(),
                },
            )

        candidates: list[PlatformCandidate] = []
        for row in rows:
            metadata = self._registry.hydrate_metadata(row)
            handle = str(metadata.get("handle") or "").strip().lstrip("@").casefold()
            fit = row.get("audience_fit") or {}
            has_fit = (
                fit.get("confirmed") is True
                or metadata.get("community_fit_pass") is True
            )
            metadata["registry_confirmed"] = bool(has_fit)
            metadata["community_fit_pass"] = bool(has_fit)
            metadata.setdefault(
                "community_fit_reason",
                "REGISTRY_CONFIRMED" if has_fit else "NOT_YET_QUALIFIED",
            )
            status = "DEFERRED"
            if handle in batch:
                status = (
                    "INSPECTION_UNAVAILABLE"
                    if inspection_error is not None
                    else str(getattr(results.get(handle), "status", "RETRYABLE_FAILED"))
                )
            metadata["registry_refresh_status"] = status
            metadata["registry_refresh_attempted"] = handle in batch
            if status == "INSPECTION_UNAVAILABLE":
                metadata["registry_refresh_error_type"] = inspection_error

            raw_kind = str(row.get("kind") or "CHANNEL").upper()
            kind = (
                OpportunityKind.GROUP
                if raw_kind == OpportunityKind.GROUP.value
                else OpportunityKind.CHANNEL
            )
            hits = []
            canonical_key = str(row.get("canonical_key") or "")
            title = str(row.get("title") or metadata.get("handle") or "Telegram community")
            url = str(row.get("url") or "") or None
            inspected = results.get(handle) if status == "VERIFIED" else None
            snapshot = getattr(inspected, "snapshot", None)
            if snapshot is not None:
                metadata.update(self._native_metadata(request, snapshot))
                metadata["registry_confirmed"] = bool(has_fit)
                metadata["community_fit_pass"] = bool(has_fit)
                metadata["registry_refresh_status"] = "VERIFIED"
                metadata["registry_refresh_attempted"] = True
                canonical_key = f"telegram:{snapshot.entity_id}:community"
                kind = (
                    OpportunityKind.GROUP
                    if snapshot.kind == TelegramSurfaceKind.GROUP
                    else OpportunityKind.CHANNEL
                )
                title = snapshot.title
                url = snapshot.url
                hits.append(self._native_hit(request, snapshot))

            candidate = PlatformCandidate(
                platform=DistributionPlatform.TELEGRAM,
                kind=kind,
                canonical_key=canonical_key,
                title=title,
                url=url,
                metadata=metadata,
                hits=hits,
            )
            if snapshot is not None and has_fit:
                self._remember_community(candidate)
            candidates.append(candidate)
        return candidates

    def candidates(
        self, request: PlatformDiscoveryRequest, hits: list[SearchHit]
    ) -> list[PlatformCandidate]:
        by_community: dict[str, PlatformCandidate] = {}
        for hit in hits:
            invite = self._invite_candidate(request, hit)
            if invite is not None:
                existing = by_community.get(invite.canonical_key)
                if existing is None:
                    by_community[invite.canonical_key] = invite
                else:
                    existing.hits.append(hit)
                continue
            normalized = self._normalize(hit.url, request.kind)
            if normalized is None:
                continue
            _, canonical_url, handle = normalized
            key = f"telegram:{handle.casefold()}:community"
            candidate = by_community.get(key)
            if candidate is None:
                candidate = PlatformCandidate(
                    platform=DistributionPlatform.TELEGRAM,
                    kind=request.kind,
                    canonical_key=key,
                    title=handle,
                    url=canonical_url,
                    metadata={
                        "handle": handle,
                        "surface_kind": "UNKNOWN",
                        "source_type": "UNKNOWN",
                        "discovery_query": request.discovery_query.query,
                        "native_research_status": "NOT_CHECKED",
                        "opportunity_scope": "COMMUNITY",
                        "actionability": "RESEARCH_ONLY",
                    },
                    hits=[],
                )
                by_community[key] = candidate
            candidate.hits.append(hit)
        return list(by_community.values())

    async def enrich_candidates(
        self, request: PlatformDiscoveryRequest, candidates: list[PlatformCandidate]
    ) -> list[PlatformCandidate]:
        invites = [
            item for item in candidates if item.metadata.get("access_state") == "JOIN_REQUIRED"
        ]
        public = [
            item for item in candidates if item.metadata.get("access_state") != "JOIN_REQUIRED"
        ]
        registry_by_handle = {
            str(item.metadata.get("handle") or "").casefold(): dict(item.metadata)
            for item in public
            if item.metadata.get("registry_confirmed") is True
            and item.metadata.get("handle")
        }
        enriched = await TelegramDiscoveryAdapter.enrich_candidates(self, request, public)
        result: list[PlatformCandidate] = []
        for candidate in [*enriched, *invites]:
            metadata = dict(candidate.metadata or {})
            prior_registry = registry_by_handle.get(
                str(metadata.get("handle") or "").casefold()
            )
            if prior_registry is not None:
                metadata["registry_confirmed"] = True
                metadata.setdefault(
                    "community_fit_terms",
                    list(prior_registry.get("community_fit_terms") or []),
                )
            if self._looks_like_bot(candidate):
                continue
            if metadata.get("native_research_status") == "VERIFIED":
                metadata["source_type"] = str(
                    metadata.get("surface_kind") or "UNKNOWN"
                ).upper()
                entity_id = metadata.get("telegram_entity_id")
                if entity_id is not None:
                    candidate.canonical_key = f"telegram:{entity_id}:community"
            elif metadata.get("access_state") == "JOIN_REQUIRED":
                metadata["source_type"] = "INVITE_COMMUNITY"
            else:
                metadata["source_type"] = "UNKNOWN"

            if metadata.get("registry_confirmed") is True:
                passed = True
                reason = "REGISTRY_CONFIRMED"
                terms = list(metadata.get("community_fit_terms") or [])
            else:
                passed, reason, terms = self._community_fit(request, candidate)
            metadata["community_fit_pass"] = passed
            metadata["community_fit_reason"] = reason
            metadata["community_fit_terms"] = terms[:12]
            metadata["opportunity_scope"] = "COMMUNITY"
            candidate.metadata = metadata
            if passed:
                self._remember_community(candidate)
            result.append(candidate)
        return result

    def _native_metadata(self, request, snapshot) -> dict:
        metadata = TelegramDiscoveryAdapter._native_metadata(self, request, snapshot)
        metadata.update(
            {
                "opportunity_scope": "COMMUNITY",
                "source_type": snapshot.kind.value,
                "community_registry_key": f"telegram:{snapshot.entity_id}:community",
            }
        )
        target = select_telegram_participation_target(
            snapshot,
            blocked_urls=self._known_bad_targets(),
        )
        metadata.update(target.metadata())
        return metadata

    def _community_fit(
        self, request: PlatformDiscoveryRequest, candidate: PlatformCandidate
    ) -> tuple[bool, str, list[str]]:
        metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
        query_terms = self._semantic_terms(request.topic or request.discovery_query.query)
        native_text = " ".join(
            [
                str(candidate.title or ""),
                str(metadata.get("about") or ""),
                *[
                    str(item.get("text") or "")
                    for item in list(metadata.get("recent_context") or [])
                    if isinstance(item, dict)
                ],
            ]
        )
        native_overlap = sorted(query_terms & self._semantic_terms(native_text))
        if native_overlap and metadata.get("native_research_status") == "VERIFIED":
            return True, "NATIVE_COMMUNITY_OVERLAP", native_overlap
        source_text = " ".join(
            f"{hit.title} {hit.snippet}"
            for hit in candidate.hits
            if str(hit.metadata.get("evidence_type") or "") != "telegram_native"
        )
        source_overlap = sorted(query_terms & self._semantic_terms(source_text))
        if source_overlap:
            reason = (
                "INVITE_SOURCE_OVERLAP"
                if metadata.get("access_state") == "JOIN_REQUIRED"
                else "SOURCE_BOUND_COMMUNITY_OVERLAP"
            )
            return True, reason, source_overlap
        if metadata.get("native_research_status") == "VERIFIED":
            return False, "VERIFIED_BUT_NO_COMMUNITY_OVERLAP", []
        return False, "UNVERIFIED_COMMUNITY", []

    @staticmethod
    def _looks_like_bot(candidate: PlatformCandidate) -> bool:
        metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
        handle = str(metadata.get("handle") or "").casefold()
        if handle.endswith("bot"):
            return True
        return any(
            str(hit.title or "").casefold().startswith("telegram: launch @")
            and "bot" in str(hit.title or "").casefold()
            for hit in candidate.hits
        )

    def _invite_candidate(
        self, request: PlatformDiscoveryRequest, hit: SearchHit
    ) -> PlatformCandidate | None:
        parts = urlsplit(str(hit.url or "").strip())
        if parts.netloc.casefold().removeprefix("www.") not in {"t.me", "telegram.me"}:
            return None
        segments = [unquote(item) for item in parts.path.split("/") if item]
        if not segments:
            return None
        if segments[0].startswith("+"):
            token = segments[0][1:]
        elif segments[0].casefold() == "joinchat" and len(segments) > 1:
            token = segments[1]
        else:
            return None
        token = token.strip()
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()[:20]
        url = f"https://t.me/+{token}"
        return PlatformCandidate(
            platform=DistributionPlatform.TELEGRAM,
            kind=request.kind,
            canonical_key=f"telegram:invite:{digest}:community",
            title=str(hit.title or "Telegram invite community")[:300],
            url=url,
            metadata={
                "invite_token_hash": digest,
                "invite_url": url,
                "access_state": "JOIN_REQUIRED",
                "source_type": "INVITE_COMMUNITY",
                "native_research_status": "INVITE_ONLY",
                "opportunity_scope": "COMMUNITY",
                "actionability": "JOIN_REQUIRED",
                "action_target_url": None,
                "action_target_specific": False,
                "action_target_resolution": "JOIN_REQUIRED",
                "discovery_query": request.discovery_query.query,
            },
            hits=[hit],
        )

    def _remember_community(self, candidate: PlatformCandidate) -> None:
        if self._product_id is None:
            return
        self._registry.upsert(
            product_id=self._product_id,
            canonical_key=candidate.canonical_key,
            title=candidate.title,
            url=candidate.url,
            kind=candidate.kind.value,
            metadata=dict(candidate.metadata or {}),
        )

    def _known_bad_targets(self) -> set[str]:
        if self._product_id is None:
            return set()
        project_ids = {
            str(row.get("id"))
            for row in self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE)
            if not row.get("deleted_at")
            and str(row.get("product_id") or "") == str(self._product_id)
        }
        return {
            self._normalize_url(str(row.get("url") or ""))
            for row in self._store.list_namespace(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE)
            if str(row.get("project_id") or "") in project_ids
            and str(row.get("publishability") or "").upper() == "NO_DISCUSSION"
        }

    @staticmethod
    def _semantic_terms(value: str) -> set[str]:
        return {
            word
            for word in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", str(value or "").casefold())
            if len(word) > 3 and not word.isdigit() and word not in _SEMANTIC_UTILITY_TERMS
        }

    @staticmethod
    def _normalize_url(value: str) -> str:
        return str(value or "").strip().rstrip("/").split("?", 1)[0]

    @staticmethod
    def _sort_date(value: object) -> datetime:
        if not isinstance(value, datetime):
            return datetime.min.replace(tzinfo=UTC)
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

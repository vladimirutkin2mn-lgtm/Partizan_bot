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
from app.telegram_discovery_strategy import ExpandedTelegramDiscoveryAdapter
from app.telegram_research import SurfaceAvailability, TelegramSurfaceKind

COMMUNITY_FIRST_TELEGRAM_QUERY_BUDGET = 3

_SEMANTIC_UTILITY_TERMS = {
    "site", "telegram", "channel", "group", "chat", "community", "discussion",
    "comments", "questions", "public", "replies", "education", "advice", "online",
    "any", "https", "канал", "группа", "чат", "сообщество", "обсуждение",
    "комментарии", "вопросы", "советы",
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
        enriched = await TelegramDiscoveryAdapter.enrich_candidates(self, request, public)
        result: list[PlatformCandidate] = []
        for candidate in [*enriched, *invites]:
            metadata = dict(candidate.metadata or {})
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

            passed, reason, terms = self._community_fit(request, candidate)
            metadata["community_fit_pass"] = passed
            metadata["community_fit_reason"] = reason
            metadata["community_fit_terms"] = terms[:12]
            metadata["opportunity_scope"] = "COMMUNITY"
            candidate.metadata = metadata
            result.append(candidate)
        return result

    def _native_metadata(self, request, snapshot) -> dict:
        metadata = TelegramDiscoveryAdapter._native_metadata(self, request, snapshot)
        metadata.update(
            {
                "opportunity_scope": "COMMUNITY",
                "source_type": snapshot.kind.value,
                "community_registry_key": f"telegram:{snapshot.entity_id}:community",
                "action_target_url": None,
                "action_target_specific": False,
                "action_target_resolution": "COMMUNITY_ONLY",
                "actionability": "RESEARCH_ONLY",
            }
        )
        blocked = self._known_bad_targets()
        context = sorted(
            list(getattr(snapshot, "recent_context", ()) or ()),
            key=lambda item: self._sort_date(getattr(item, "published_at", None)),
            reverse=True,
        )
        selected = next(
            (
                item
                for item in context
                if str(getattr(item, "url", "") or "").strip()
                and self._normalize_url(str(getattr(item, "url", ""))) not in blocked
            ),
            None,
        )
        if snapshot.kind == TelegramSurfaceKind.CHANNEL:
            if snapshot.comment_surface != SurfaceAvailability.AVAILABLE:
                metadata["community_only_reason"] = "NO_COMMENT_SURFACE"
                return metadata
            mode = "CHANNEL_COMMENT"
        elif snapshot.kind == TelegramSurfaceKind.GROUP:
            if (
                snapshot.reply_surface != SurfaceAvailability.AVAILABLE
                and snapshot.standalone_post_surface != SurfaceAvailability.AVAILABLE
            ):
                metadata["community_only_reason"] = "NO_GROUP_WRITE_SURFACE"
                return metadata
            mode = "GROUP_REPLY_OR_POST"
        else:
            metadata["community_only_reason"] = "UNSUPPORTED_TELEGRAM_ENTITY"
            return metadata
        if selected is None:
            metadata["community_only_reason"] = "NO_UNBLOCKED_TARGET"
            return metadata

        target_url = str(getattr(selected, "url", "") or "").strip()
        published_at = self._sort_date(getattr(selected, "published_at", None))
        metadata.update(
            {
                "action_target_url": target_url,
                "action_target_specific": True,
                "action_target_resolution": "LATEST_UNBLOCKED_COMMUNITY_TARGET",
                "action_target_quality": "LATEST_COMMUNITY_SURFACE",
                "action_target_mode": mode,
                "actionability": "NEEDS_PREFLIGHT",
                "target_published_at": (
                    None if published_at.year == 1 else published_at.isoformat()
                ),
                "target_age_days": (
                    None
                    if published_at.year == 1
                    else round(
                        max(0.0, (datetime.now(UTC) - published_at).total_seconds()) / 86400,
                        2,
                    )
                ),
            }
        )
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

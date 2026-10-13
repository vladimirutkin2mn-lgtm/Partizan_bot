from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta

from app.audience_intelligence import AudienceIntelligenceEngine
from app.discovery_intelligence import (
    annotate_base_intelligence,
    exploration_slot_count,
    is_exploration_candidate,
    portfolio_score,
    propagate_surface_corroboration,
)
from app.discovery_relevance_learning import (
    DiscoveryRelevanceLearningService,
    discovery_relevance_learning_service,
)
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.platform_discovery import (
    InstagramDiscoveryAdapter,
    PlatformCandidate,
    PlatformDiscoveryAdapter,
    PlatformDiscoveryRequest,
    RedditDiscoveryAdapter,
    TelegramDiscoveryAdapter,
    TikTokDiscoveryAdapter,
)
from app.schemas import ICPView, ProductProfileView
from app.search import DiscoveryQuery, SearchProvider, SourceClass

# Discovery may explore several participation angles, but every query must retain enough
# product/audience context to avoid drifting into generic Telegram communities.
TELEGRAM_DISCOVERY_QUERY_BUDGET = 6
TARGET_READY_TELEGRAM_OPPORTUNITIES = 5
MAX_ADAPTIVE_DISCOVERY_ROUNDS = 3
TELEGRAM_ACTION_TARGET_MAX_AGE = timedelta(days=7)

_RUSSIAN_LANGUAGE_MARKERS = ("ru", "russian", "рус", "русский", "русская")
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


class ExpandedTelegramDiscoveryAdapter(TelegramDiscoveryAdapter):
    """Audience-first Telegram discovery without changing publication permissions.

    The adapter produces research candidates only. Native write access is checked later by
    publisher preflight. Broader exploration is allowed, including adjacent communities, but
    every search keeps a stable product + audience anchor. This prevents generic lenses such
    as "education" or "community" from becoming unbounded searches unrelated to the product.
    """

    def __init__(
        self,
        research_connector=None,
        *,
        use_default_research_connector: bool = True,
        adaptive_hints: list[str] | tuple[str, ...] | None = None,
        relevance_learning_service: DiscoveryRelevanceLearningService | None = None,
    ) -> None:
        super().__init__(
            research_connector=research_connector,
            use_default_research_connector=use_default_research_connector,
        )
        self._adaptive_hints = tuple(
            hint
            for hint in (self._clean(item) for item in (adaptive_hints or []))
            if hint
        )
        self._relevance_learning = (
            relevance_learning_service or discovery_relevance_learning_service
        )

    def build_requests(
        self,
        product: ProductProfileView,
        icp: ICPView,
    ) -> list[PlatformDiscoveryRequest]:
        market, language = self._market_language(product)
        discussion_terms, learning_terms, community_terms = self._participation_terms(language)

        title = self._clean(getattr(icp, "title", ""))
        pain = self._clean(getattr(icp, "pain", ""))
        trigger = self._clean(getattr(icp, "trigger", ""))
        alternatives = self._clean(" ".join(getattr(icp, "alternatives", [])[:3]))
        product_theme = self._clean(
            " ".join(
                part
                for part in (
                    getattr(product, "name", ""),
                    getattr(product, "problem_or_desire", "") or "",
                    getattr(product, "value_proposition", "") or "",
                    getattr(product, "description", "") or "",
                    " ".join(getattr(product, "known_audience", [])[:3]),
                )
                if part
            )
        )
        audience_theme = self._clean(
            " ".join(part for part in (title, pain, trigger) if part)
        )
        stable_anchor = (
            self._clean(f"{product_theme} {audience_theme}")
            or audience_theme
            or product_theme
        )
        if not stable_anchor:
            return []

        requests: list[PlatformDiscoveryRequest] = [
            self._request(
                OpportunityKind.CHANNEL,
                stable_anchor,
                discussion_terms,
                "Telegram channel discussion comments",
                market,
                language,
            ),
            self._request(
                OpportunityKind.GROUP,
                stable_anchor,
                community_terms,
                "Telegram group chat community",
                market,
                language,
            ),
        ]

        adaptive_hints = self._relevance_learning.filter_hints(
            getattr(product, "id", None),
            self._adaptive_hints,
        )
        expansions: list[tuple[OpportunityKind, str, str]] = []
        for index, hint in enumerate(adaptive_hints[:4]):
            if index % 2 == 0:
                kind = OpportunityKind.CHANNEL
                intent = discussion_terms
                surface_hint = "Telegram discussion comments questions"
            else:
                kind = OpportunityKind.GROUP
                intent = community_terms
                surface_hint = "Telegram public community replies"
            anchored_hint = self._clean(f"{stable_anchor} {hint}")
            expansions.append(
                (
                    kind,
                    anchored_hint,
                    self._query(
                        anchored_hint,
                        intent,
                        surface_hint,
                        market,
                        language,
                    ),
                )
            )

        default_expansions = [
            (
                OpportunityKind.CHANNEL,
                stable_anchor,
                self._query(
                    stable_anchor,
                    discussion_terms,
                    "Telegram discussion comments",
                    market,
                    language,
                ),
            ),
            (
                OpportunityKind.CHANNEL,
                stable_anchor,
                self._query(
                    stable_anchor,
                    learning_terms,
                    "Telegram education advice",
                    market,
                    language,
                ),
            ),
            (
                OpportunityKind.GROUP,
                stable_anchor,
                self._query(
                    stable_anchor,
                    community_terms,
                    "Telegram community chat",
                    market,
                    language,
                ),
            ),
            (
                OpportunityKind.CHANNEL,
                self._clean(f"{stable_anchor} {alternatives}"),
                self._query(
                    self._clean(f"{stable_anchor} {alternatives}"),
                    discussion_terms,
                    "Telegram adjacent community",
                    market,
                    language,
                ),
            ),
        ]
        expansions.extend(default_expansions)

        for kind, topic, query in expansions:
            if not topic or not query:
                continue
            requests.append(
                PlatformDiscoveryRequest(
                    platform=DistributionPlatform.TELEGRAM,
                    kind=kind,
                    topic=self._topic(topic, ""),
                    discovery_query=DiscoveryQuery(SourceClass.COMMUNITY, query),
                )
            )

        deduped: list[PlatformDiscoveryRequest] = []
        seen: set[str] = set()
        for request in requests:
            key = " ".join(request.discovery_query.query.lower().split())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(request)
            if len(deduped) >= TELEGRAM_DISCOVERY_QUERY_BUDGET:
                break
        return deduped

    async def enrich_candidates(
        self,
        request: PlatformDiscoveryRequest,
        candidates: list[PlatformCandidate],
    ) -> list[PlatformCandidate]:
        enriched = await super().enrich_candidates(request, candidates)
        for candidate in enriched:
            metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
            entity_id = metadata.get("telegram_entity_id")
            scope = str(metadata.get("opportunity_scope") or "").upper()
            if entity_id is None or scope not in {"COMMUNITY", "POST"}:
                continue
            parent_key = f"telegram:{entity_id}"
            metadata["surface_parent_key"] = parent_key
            if scope == "COMMUNITY":
                candidate.canonical_key = f"{parent_key}:community"
            else:
                target_url = str(metadata.get("action_target_url") or "").strip()
                if not target_url:
                    continue
                candidate.canonical_key = (
                    f"{parent_key}:post:{self._target_discriminator(target_url)}"
                )
            candidate.metadata = metadata
        return enriched

    def _native_metadata(self, request, snapshot) -> dict:
        """Keep community discovery separate from action-target resolution.

        Native Telegram research can prove that a community is relevant even when none of
        the last few messages repeats the full search query. In that case we may select the
        freshest concrete post only when the community title/about itself overlaps meaningful
        product/audience terms and the surface can actually support a later interaction.
        Publisher preflight remains mandatory before any write action.
        """
        metadata = super()._native_metadata(request, snapshot)
        if metadata.get("action_target_specific") is True:
            metadata["action_target_resolution"] = "DIRECT_MESSAGE_MATCH"
            metadata["opportunity_scope"] = "POST"
            metadata["actionability"] = "NEEDS_PREFLIGHT"
            return metadata

        query_terms = self._semantic_terms(request.topic or request.discovery_query.query)
        community_terms = self._semantic_terms(
            f"{getattr(snapshot, 'title', '')} {getattr(snapshot, 'about', '')}"
        )
        overlap = sorted(query_terms & community_terms)
        if not overlap:
            return self._community_only_metadata(metadata, reason="NO_COMMUNITY_OVERLAP")

        surface_kind = str(
            getattr(getattr(snapshot, "kind", None), "value", getattr(snapshot, "kind", ""))
            or ""
        ).upper()
        has_interaction_surface = (
            surface_kind == "GROUP"
            or getattr(snapshot, "linked_discussion_id", None) is not None
        )
        if not has_interaction_surface:
            return self._community_only_metadata(
                metadata,
                overlap=overlap,
                reason="NO_INTERACTION_SURFACE",
            )

        now = datetime.now(UTC)
        eligible = []
        for item in getattr(snapshot, "recent_context", ()) or ():
            published_at = getattr(item, "published_at", None)
            url = str(getattr(item, "url", "") or "").strip()
            if published_at is None or not url:
                continue
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
            age = now - published_at.astimezone(UTC)
            if timedelta(0) <= age <= TELEGRAM_ACTION_TARGET_MAX_AGE:
                eligible.append((published_at, url))
        if not eligible:
            return self._community_only_metadata(
                metadata,
                overlap=overlap,
                reason="NO_FRESH_ACTION_TARGET",
            )

        _, target_url = max(eligible, key=lambda item: item[0])
        metadata["action_target_url"] = target_url
        metadata["action_target_specific"] = True
        metadata["action_target_resolution"] = "COMMUNITY_RELEVANCE_FRESH_POST"
        metadata["community_match_terms"] = overlap[:12]
        metadata["opportunity_scope"] = "POST"
        metadata["actionability"] = "NEEDS_PREFLIGHT"
        return metadata

    @staticmethod
    def _community_only_metadata(
        metadata: dict,
        *,
        overlap: list[str] | None = None,
        reason: str,
    ) -> dict:
        metadata["action_target_url"] = None
        metadata["action_target_specific"] = False
        metadata["action_target_resolution"] = "COMMUNITY_ONLY"
        metadata["opportunity_scope"] = "COMMUNITY"
        metadata["actionability"] = "RESEARCH_ONLY"
        metadata["community_only_reason"] = reason
        if overlap:
            metadata["community_match_terms"] = overlap[:12]
        return metadata

    @staticmethod
    def _target_discriminator(target_url: str) -> str:
        tail = str(target_url).rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            return tail
        return hashlib.sha256(str(target_url).encode("utf-8")).hexdigest()[:16]

    def _request(
        self,
        kind: OpportunityKind,
        anchor: str,
        intent: str,
        surface_hint: str,
        market: str,
        language: str,
    ) -> PlatformDiscoveryRequest:
        return PlatformDiscoveryRequest(
            platform=DistributionPlatform.TELEGRAM,
            kind=kind,
            topic=self._topic(anchor, intent),
            discovery_query=DiscoveryQuery(
                SourceClass.COMMUNITY,
                self._query(anchor, intent, surface_hint, market, language),
            ),
        )

    @staticmethod
    def _clean(value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @classmethod
    def _topic(cls, anchor: str, intent: str) -> str:
        return cls._clean(f"{anchor} {intent}")[:160]

    @classmethod
    def _query(
        cls,
        anchor: str,
        intent: str,
        surface_hint: str,
        market: str,
        language: str,
    ) -> str:
        anchor = cls._clean(anchor)
        if not anchor:
            return ""
        return cls._clean(
            f"site:t.me {anchor} {intent} {surface_hint} {market} {language}"
        )[:500]

    @staticmethod
    def _semantic_terms(value: str) -> set[str]:
        return {
            word
            for word in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", str(value or "").casefold())
            if len(word) > 3 and not word.isdigit() and word not in _SEMANTIC_UTILITY_TERMS
        }

    @staticmethod
    def _participation_terms(language: str) -> tuple[str, str, str]:
        lowered = str(language or "").lower()
        russian = any(marker in lowered for marker in _RUSSIAN_LANGUAGE_MARKERS)
        if russian:
            return (
                "обсуждение комментарии вопросы",
                "новички обучение правила границы советы",
                "сообщество встречи знакомства опыт",
            )
        return (
            "discussion comments questions",
            "newcomers education rules boundaries advice",
            "community meetups introductions experience",
        )


def expanded_platform_adapters(
    *,
    telegram_hints: list[str] | tuple[str, ...] | None = None,
    telegram_only: bool = False,
    relevance_learning_service: DiscoveryRelevanceLearningService | None = None,
) -> list[PlatformDiscoveryAdapter]:
    telegram = ExpandedTelegramDiscoveryAdapter(
        adaptive_hints=telegram_hints,
        relevance_learning_service=relevance_learning_service,
    )
    if telegram_only:
        return [telegram]
    return [
        telegram,
        InstagramDiscoveryAdapter(),
        RedditDiscoveryAdapter(),
        TikTokDiscoveryAdapter(),
    ]


class ExpandedAudienceIntelligenceEngine(AudienceIntelligenceEngine):
    """Audience engine with broader search plus reusable quality learning enabled."""

    def __init__(
        self,
        provider: SearchProvider,
        max_concurrency: int = 4,
        *,
        telegram_hints: list[str] | tuple[str, ...] | None = None,
        telegram_only: bool = False,
        relevance_learning_service: DiscoveryRelevanceLearningService | None = None,
    ) -> None:
        self._relevance_learning = (
            relevance_learning_service or discovery_relevance_learning_service
        )
        super().__init__(
            provider,
            max_concurrency=max_concurrency,
            adapters=expanded_platform_adapters(
                telegram_hints=telegram_hints,
                telegram_only=telegram_only,
                relevance_learning_service=self._relevance_learning,
            ),
        )
        self._last_relevance_rejections: list[dict] = []

    @property
    def last_relevance_rejections(self) -> list[dict]:
        return [dict(item) for item in self._last_relevance_rejections]

    async def discover(
        self,
        product: ProductProfileView,
        icps: list[ICPView],
        per_query_limit: int = 5,
        max_opportunities: int = 80,
    ):
        # Expanded Telegram discovery has more queries than the other adapters. Collect a
        # bounded superset first so one platform cannot consume the global top-N before
        # cross-platform coverage is considered.
        collection_limit = max(
            max_opportunities,
            max_opportunities * max(1, len(self._adapters)),
        )
        opportunities = await super().discover(
            product,
            icps,
            per_query_limit=per_query_limit,
            max_opportunities=collection_limit,
        )
        scored = annotate_base_intelligence(opportunities)
        accepted, rejected = self._relevance_learning.filter_and_learn(
            product=product,
            icps=icps,
            opportunities=scored,
        )
        self._last_relevance_rejections = rejected
        propagated = propagate_surface_corroboration(accepted)
        return self._coverage_first_limit(propagated, max_opportunities)

    @staticmethod
    def _coverage_first_limit(opportunities: list, limit: int) -> list:
        """Bound the portfolio with coverage plus a small adjacent-exploration reserve."""
        if limit <= 0:
            return []
        if len(opportunities) <= limit:
            return sorted(
                opportunities,
                key=lambda item: (-portfolio_score(item), item.canonical_key),
            )

        ranked = sorted(
            opportunities,
            key=lambda item: (-portfolio_score(item), item.canonical_key),
        )
        selected: list = []
        selected_keys: set[tuple[str, str, str]] = set()
        coverage_cells: set[tuple[str, str]] = set()

        # First keep the strongest valid candidate for every ICP/platform combination.
        for item in ranked:
            platform = str(getattr(item.platform, "value", item.platform))
            cell = (str(item.icp_id), platform)
            if cell in coverage_cells:
                continue
            coverage_cells.add(cell)
            key = (str(item.icp_id), platform, str(item.canonical_key))
            selected.append(item)
            selected_keys.add(key)
            if len(selected) >= limit:
                return selected

        # Reserve roughly 15% of the remaining portfolio for audience-led adjacent contexts.
        # Exploration never bypasses the semantic relevance guard above and never changes
        # publication permissions; it only prevents non-obvious audience surfaces from being
        # squeezed out by many obvious topical results.
        target_exploration = exploration_slot_count(limit)
        selected_exploration = sum(is_exploration_candidate(item) for item in selected)
        needed_exploration = max(0, target_exploration - selected_exploration)
        if needed_exploration:
            for item in ranked:
                platform = str(getattr(item.platform, "value", item.platform))
                key = (str(item.icp_id), platform, str(item.canonical_key))
                if key in selected_keys or not is_exploration_candidate(item):
                    continue
                selected.append(item)
                selected_keys.add(key)
                needed_exploration -= 1
                if len(selected) >= limit or needed_exploration <= 0:
                    break

        # Fill the remaining budget using the explicit intelligence ranking score.
        for item in ranked:
            platform = str(getattr(item.platform, "value", item.platform))
            key = (str(item.icp_id), platform, str(item.canonical_key))
            if key in selected_keys:
                continue
            selected.append(item)
            selected_keys.add(key)
            if len(selected) >= limit:
                break

        selected.sort(
            key=lambda item: (-portfolio_score(item), item.canonical_key),
        )
        return selected
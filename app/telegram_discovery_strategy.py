from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from app.audience_intelligence import AudienceIntelligenceEngine
from app.discovery_relevance_learning import (
    DiscoveryRelevanceLearningService,
    discovery_relevance_learning_service,
)
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.platform_discovery import (
    InstagramDiscoveryAdapter,
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
        stable_anchor = self._clean(f"{product_theme} {audience_theme}") or audience_theme or product_theme
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
            expansions.append((kind, anchored_hint, self._query(anchored_hint, intent, surface_hint, market, language)))

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

    def _native_metadata(self, request, snapshot) -> dict:
        """Keep community discovery separate from action-target resolution.

        Native Telegram research can prove that a community is relevant even when none of
        the last few messages repeats the full search query. In that case we may select the
        freshest concrete post only when the community title/about itself overlaps meaningful
        product/audience terms. This is still research: publisher preflight remains mandatory.
        """
        metadata = super()._native_metadata(request, snapshot)
        if metadata.get("action_target_specific") is True:
            metadata["action_target_resolution"] = "DIRECT_MESSAGE_MATCH"
            return metadata

        query_terms = self._semantic_terms(request.topic or request.discovery_query.query)
        community_terms = self._semantic_terms(
            f"{getattr(snapshot, 'title', '')} {getattr(snapshot, 'about', '')}"
        )
        overlap = sorted(query_terms & community_terms)
        if not overlap:
            metadata["action_target_resolution"] = "COMMUNITY_ONLY"
            return metadata

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
            metadata["action_target_resolution"] = "COMMUNITY_ONLY"
            return metadata

        _, target_url = max(eligible, key=lambda item: item[0])
        metadata["action_target_url"] = target_url
        metadata["action_target_specific"] = True
        metadata["action_target_resolution"] = "COMMUNITY_RELEVANCE_FRESH_POST"
        metadata["community_match_terms"] = overlap[:12]
        return metadata

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
        opportunities = await super().discover(
            product,
            icps,
            per_query_limit=per_query_limit,
            max_opportunities=max_opportunities,
        )
        accepted, rejected = self._relevance_learning.filter_and_learn(
            product=product,
            icps=icps,
            opportunities=opportunities,
        )
        self._last_relevance_rejections = rejected
        return accepted

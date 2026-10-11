from __future__ import annotations

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

_RUSSIAN_LANGUAGE_MARKERS = ("ru", "russian", "рус", "русский", "русская")


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

        # In adaptive rounds learned hypotheses vary the exploration lens but never replace
        # the stable product/audience anchor. This is the core guard against query drift.
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

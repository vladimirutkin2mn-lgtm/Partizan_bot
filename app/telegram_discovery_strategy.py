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
TELEGRAM_QUERY_ANCHOR_MAX_CHARS = 180

_RUSSIAN_LANGUAGE_MARKERS = ("ru", "russian", "рус", "русский", "русская")
_QUERY_BOILERPLATE_MARKERS = (
    "context to use",
    "product / offer name",
    "what the product does",
    "known audience / community",
    "product url",
    "obvious terms",
)
_PRODUCT_NAME_PREFIX = re.compile(
    r"^(?:promoted\s+asset|product|offer|продукт|оффер)\s*:\s*",
    re.IGNORECASE,
)
_CONVERSATION_REQUEST_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:can|could|would)\s+(?:anyone|someone|you)\b",
        r"\b(?:anyone|anybody|someone)\s+(?:know|recommend|suggest|help|tried|used|have|has)\b",
        r"\b(?:i|we)\s+(?:need|want|am looking for|are looking for|could use|struggle|struggling|can't|cannot)\b",
        r"\b(?:need|looking for|seeking)\s+(?:help|advice|recommendations?|suggestions?|feedback)\b",
        r"\b(?:any|some)\s+(?:advice|recommendations?|suggestions?|feedback|thoughts)\b",
        r"\b(?:подскажите|посоветуйте|помогите|кто[- ]?нибудь|кто знает|есть ли|что думаете|как думаете)\b",
        r"\b(?:я|мы)\s+(?:ищу|ищем|не могу|не можем|хочу|хотим)\b",
        r"\bмне\s+(?:нужен|нужна|нужно|нужны)\b",
        r"\b(?:нужен|нужна|нужно|нужны)\s+(?:совет|помощь|рекомендац)",
    )
)
_PROMOTIONAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:dm|dms|direct message|message me|contact me|subscribe|join|buy|order|book|register|discount|sale|promo|shop|store|price|pricing|apply|sign up)\b",
        r"\bspecial\s+offer\b",
        r"\b(?:подписывай|подпишись|вступай|присоединяй|покупай|купить|заказать|заказ|скидка|акция|регистрация|записаться|цена|стоимость)\w*\b",
        r"\b(?:пишите\s+в\s+лс|в\s+личку)\b",
    )
)
_OUTBOUND_LINK_PATTERN = re.compile(r"(?:https?://|www\.|\bt\.me/)", re.IGNORECASE)
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

        alternatives = self._compact_alternatives(getattr(icp, "alternatives", []))
        stable_anchor = self._stable_search_anchor(product, icp)
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
            anchored_hint = self._bounded_anchor(stable_anchor, self._concise_fragment(hint, 72))
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

        adjacent_anchor = self._bounded_anchor(stable_anchor, alternatives)
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
                adjacent_anchor,
                self._query(
                    adjacent_anchor,
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
        """Separate relevant communities from concrete conversation opportunities.

        A concrete Telegram target is actionable only when it is recent, inspectable,
        semantically relevant and contains a real conversation cue such as a question or
        request for help/advice. Promotional calls-to-action and broadcast-only content remain
        research evidence, not opportunities. Publisher preflight remains mandatory before
        any write action.
        """
        metadata = super()._native_metadata(request, snapshot)
        now = datetime.now(UTC)
        query_terms = self._semantic_terms(request.topic or request.discovery_query.query)

        if metadata.get("action_target_specific") is True:
            target_url = str(metadata.get("action_target_url") or "").strip()
            direct_context = self._context_for_target(snapshot, target_url)
            direct_quality = self._target_context_quality(direct_context, now)
            direct_reason = direct_quality
            if direct_quality == "ELIGIBLE":
                ready, direct_reason, signals, match_terms = self._conversation_readiness(
                    direct_context,
                    query_terms,
                )
                if ready:
                    return self._promote_action_target(
                        metadata,
                        direct_context,
                        resolution="DIRECT_MESSAGE_MATCH",
                        conversation_signals=signals,
                        target_match_terms=match_terms,
                    )

            recovered = self._select_conversation_target(
                snapshot,
                now,
                query_terms,
                exclude_url=target_url,
            )
            if recovered is not None:
                item, signals, match_terms = recovered
                return self._promote_action_target(
                    metadata,
                    item,
                    resolution="RECOVERED_FRESH_CONVERSATION",
                    conversation_signals=signals,
                    target_match_terms=match_terms,
                )

            return self._community_only_metadata(
                metadata,
                reason=f"DIRECT_TARGET_{direct_reason}",
            )

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

        selected = self._select_conversation_target(snapshot, now, query_terms)
        if selected is None:
            return self._community_only_metadata(
                metadata,
                overlap=overlap,
                reason="NO_FRESH_CONVERSATIONAL_ACTION_TARGET",
            )

        item, signals, match_terms = selected
        return self._promote_action_target(
            metadata,
            item,
            resolution="COMMUNITY_RELEVANCE_FRESH_CONVERSATION",
            conversation_signals=signals,
            target_match_terms=match_terms,
            overlap=overlap,
        )

    @classmethod
    def _context_for_target(cls, snapshot, target_url: str):
        normalized = str(target_url or "").strip().rstrip("/")
        if not normalized:
            return None
        for item in getattr(snapshot, "recent_context", ()) or ():
            item_url = str(getattr(item, "url", "") or "").strip().rstrip("/")
            if item_url == normalized:
                return item
        return None

    @classmethod
    def _select_conversation_target(
        cls,
        snapshot,
        now: datetime,
        query_terms: set[str],
        *,
        exclude_url: str | None = None,
    ):
        excluded = str(exclude_url or "").strip().rstrip("/")
        eligible = []
        for item in getattr(snapshot, "recent_context", ()) or ():
            item_url = str(getattr(item, "url", "") or "").strip().rstrip("/")
            if excluded and item_url == excluded:
                continue
            if cls._target_context_quality(item, now) != "ELIGIBLE":
                continue
            ready, _, signals, match_terms = cls._conversation_readiness(item, query_terms)
            if not ready:
                continue
            published_at = cls._aware_datetime(getattr(item, "published_at", None))
            if published_at is not None and item_url:
                eligible.append((published_at, item, signals, match_terms))
        if not eligible:
            return None
        _, item, signals, match_terms = max(eligible, key=lambda candidate: candidate[0])
        return item, signals, match_terms

    @classmethod
    def _conversation_readiness(
        cls,
        item,
        query_terms: set[str],
    ) -> tuple[bool, str, list[str], list[str]]:
        text = cls._clean(str(getattr(item, "text", "") or ""))
        if not text:
            return False, "EMPTY_TEXT", [], []

        matched = cls._semantic_terms(
            " ".join(str(term or "") for term in getattr(item, "matched_terms", ()) or ())
        )
        match_terms = sorted(
            (matched & query_terms) | (cls._semantic_terms(text) & query_terms)
        )
        if not match_terms:
            return False, "NO_TARGET_SEMANTIC_MATCH", [], []

        signals: list[str] = []
        if any(marker in text for marker in ("?", "؟", "¿")):
            signals.append("QUESTION")
        if any(pattern.search(text) for pattern in _CONVERSATION_REQUEST_PATTERNS):
            signals.append("REQUEST")
        if not signals:
            return False, "NO_CONVERSATION_CUE", [], match_terms

        promo_signals: list[str] = []
        if _OUTBOUND_LINK_PATTERN.search(text):
            promo_signals.append("OUTBOUND_LINK")
        if any(pattern.search(text) for pattern in _PROMOTIONAL_PATTERNS):
            promo_signals.append("PROMOTIONAL_CTA")

        # A question alone does not make an advertisement a useful conversation target.
        # Two independent conversational signals can still survive one incidental link.
        if len(promo_signals) >= 2 or (
            promo_signals and signals == ["QUESTION"]
        ):
            return False, "PROMOTIONAL", signals, match_terms

        return True, "READY", signals, match_terms

    @classmethod
    def _target_context_quality(cls, item, now: datetime) -> str:
        if item is None:
            return "CONTEXT_MISSING"
        if not cls._clean(str(getattr(item, "text", "") or "")):
            return "EMPTY_TEXT"
        published_at = cls._aware_datetime(getattr(item, "published_at", None))
        if published_at is None:
            return "TIMESTAMP_MISSING"
        age = now - published_at
        if age < timedelta(0) or age > TELEGRAM_ACTION_TARGET_MAX_AGE:
            return "STALE"
        if not str(getattr(item, "url", "") or "").strip():
            return "URL_MISSING"
        return "ELIGIBLE"

    @staticmethod
    def _aware_datetime(value) -> datetime | None:
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _promote_action_target(
        cls,
        metadata: dict,
        item,
        *,
        resolution: str,
        conversation_signals: list[str],
        target_match_terms: list[str],
        overlap: list[str] | None = None,
    ) -> dict:
        target_url = str(getattr(item, "url", "") or "").strip()
        metadata["action_target_url"] = target_url
        metadata["action_target_specific"] = True
        metadata["action_target_resolution"] = resolution
        metadata["action_target_quality"] = "FRESH_CONVERSATION"
        metadata["conversation_signals"] = conversation_signals
        metadata["target_match_terms"] = target_match_terms[:12]
        metadata["opportunity_scope"] = "POST"
        metadata["actionability"] = "NEEDS_PREFLIGHT"
        metadata.pop("community_only_reason", None)
        if overlap:
            metadata["community_match_terms"] = overlap[:12]
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
        metadata.pop("action_target_quality", None)
        metadata.pop("conversation_signals", None)
        metadata.pop("target_match_terms", None)
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
    def _concise_fragment(cls, value: object, max_chars: int = 96) -> str:
        cleaned = cls._clean(str(value or ""))
        if not cleaned:
            return ""
        lowered = cleaned.casefold()
        if any(marker in lowered for marker in _QUERY_BOILERPLATE_MARKERS):
            return ""
        if len(cleaned) > max_chars:
            return ""
        return cleaned

    @classmethod
    def _product_name_anchor(cls, value: object) -> str:
        cleaned = cls._clean(str(value or ""))
        cleaned = _PRODUCT_NAME_PREFIX.sub("", cleaned).strip()
        return cls._concise_fragment(cleaned, 80)

    @classmethod
    def _audience_fragments(cls, value: object) -> list[str]:
        if isinstance(value, str):
            values = [value]
        else:
            try:
                values = list(value or [])
            except TypeError:
                values = [value]
        return [
            fragment
            for fragment in (cls._concise_fragment(item, 72) for item in values[:2])
            if fragment
        ]

    @classmethod
    def _bounded_anchor(cls, *parts: object) -> str:
        anchor = cls._clean(" ".join(str(part or "") for part in parts if part))
        if len(anchor) <= TELEGRAM_QUERY_ANCHOR_MAX_CHARS:
            return anchor
        words = anchor.split()
        bounded: list[str] = []
        for word in words:
            candidate = " ".join((*bounded, word))
            if len(candidate) > TELEGRAM_QUERY_ANCHOR_MAX_CHARS:
                break
            bounded.append(word)
        return " ".join(bounded)

    @classmethod
    def _stable_search_anchor(cls, product: ProductProfileView, icp: ICPView) -> str:
        product_name = cls._product_name_anchor(getattr(product, "name", ""))
        problem = cls._concise_fragment(getattr(product, "problem_or_desire", ""), 96)
        known_audience = cls._audience_fragments(getattr(product, "known_audience", []))
        icp_title = cls._concise_fragment(getattr(icp, "title", ""), 80)

        # Product identity is intentionally first so query truncation cannot remove the most
        # discriminative niche term (for example, "FemDom" from "Promoted asset: FemDom").
        # Long generated pain/trigger/description fields are intentionally excluded: the
        # search trace showed that they can contain prompt/context boilerplate rather than
        # audience language and drown the product signal in web search.
        return cls._bounded_anchor(product_name, problem, *known_audience, icp_title)

    @classmethod
    def _compact_alternatives(cls, value: object) -> str:
        if isinstance(value, str):
            values = [value]
        else:
            try:
                values = list(value or [])
            except TypeError:
                values = [value]
        fragments = [
            fragment
            for fragment in (cls._concise_fragment(item, 48) for item in values[:3])
            if fragment
        ]
        return cls._bounded_anchor(*fragments)

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

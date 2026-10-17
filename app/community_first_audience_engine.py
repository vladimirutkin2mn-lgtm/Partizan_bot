from __future__ import annotations

from app.audience_intelligence import AudienceIntelligenceEngine
from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.discovery_intelligence import annotate_base_intelligence, propagate_surface_corroboration
from app.discovery_relevance_learning import (
    DiscoveryRelevanceLearningService,
    discovery_relevance_learning_service,
)
from app.distribution_types import DistributionPlatform
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.search import SearchProvider
from app.telegram_discovery_strategy import ExpandedAudienceIntelligenceEngine

GROWTH_MANDATE_NAMESPACE = "growth_mandate"


class CommunityFirstAudienceIntelligenceEngine(ExpandedAudienceIntelligenceEngine):
    """Respect enabled platforms and keep one Telegram opportunity per community."""

    def __init__(
        self,
        provider: SearchProvider,
        max_concurrency: int = 4,
        *,
        telegram_hints: list[str] | tuple[str, ...] | None = None,
        telegram_only: bool = False,
        relevance_learning_service: DiscoveryRelevanceLearningService | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._telegram_only = telegram_only
        relevance_service = relevance_learning_service or discovery_relevance_learning_service
        super().__init__(
            provider,
            max_concurrency=max_concurrency,
            telegram_hints=telegram_hints,
            telegram_only=False,
            relevance_learning_service=relevance_service,
        )
        self._relevance_learning = relevance_service
        self._all_adapters = [
            CommunityFirstTelegramDiscoveryAdapter(
                adaptive_hints=telegram_hints,
                relevance_learning_service=relevance_service,
                store=self._store,
            )
            if adapter.platform == DistributionPlatform.TELEGRAM
            else adapter
            for adapter in self._adapters
        ]
        self._adapters = list(self._all_adapters)

    async def discover(
        self,
        product,
        icps,
        per_query_limit: int = 5,
        max_opportunities: int = 80,
    ):
        allowed = self._allowed_platforms(getattr(product, "id", None))
        self._adapters = [
            adapter
            for adapter in self._all_adapters
            if adapter.platform in allowed
            and (not self._telegram_only or adapter.platform == DistributionPlatform.TELEGRAM)
        ]
        if not self._adapters:
            self._last_attempts = []
            self._last_failures = []
            self._last_relevance_rejections = []
            return []

        collection_limit = max(
            max_opportunities, max_opportunities * max(1, len(self._adapters))
        )
        raw = await AudienceIntelligenceEngine.discover(
            self,
            product,
            icps,
            per_query_limit=per_query_limit,
            max_opportunities=collection_limit,
        )
        scored = annotate_base_intelligence(raw)
        accepted, rejected = self._relevance_learning.filter_and_learn(
            product=product, icps=icps, opportunities=scored
        )
        accepted_keys = {
            (str(item.icp_id), item.platform, str(item.canonical_key)) for item in accepted
        }
        rescued = []
        for item in scored:
            metadata = item.metadata if isinstance(item.metadata, dict) else {}
            key = (str(item.icp_id), item.platform, str(item.canonical_key))
            if (
                item.platform != DistributionPlatform.TELEGRAM
                or metadata.get("community_fit_pass") is not True
                or key in accepted_keys
            ):
                continue
            rejection = next(
                (
                    row
                    for row in rejected
                    if str(row.get("canonical_key") or "") == str(item.canonical_key)
                    and str(row.get("icp_id") or "") in {"", str(item.icp_id)}
                ),
                None,
            )
            reason = str((rejection or {}).get("reason") or "")
            if reason and reason != "INSUFFICIENT_SEMANTIC_EVIDENCE":
                continue
            metadata = dict(metadata)
            metadata["relevance_guard"] = {
                "accepted": True,
                "reason": "COMMUNITY_FIRST_FIT",
                "rescued_from": reason or "UNRECORDED_REJECTION",
            }
            item.metadata = metadata
            rescued.append(item)
            accepted_keys.add(key)

        self._last_relevance_rejections = rejected
        propagated = propagate_surface_corroboration([*accepted, *rescued])
        collapsed = collapse_telegram_communities(propagated)
        return self._coverage_first_limit(collapsed, max_opportunities)

    def _allowed_platforms(self, product_id: object) -> set[DistributionPlatform]:
        if self._telegram_only:
            return {DistributionPlatform.TELEGRAM}
        if product_id is None:
            return {adapter.platform for adapter in self._all_adapters}
        payload = self._store.get(GROWTH_MANDATE_NAMESPACE, str(product_id))
        if not isinstance(payload, dict):
            return {adapter.platform for adapter in self._all_adapters}
        if str(payload.get("status") or "ACTIVE").upper() != "ACTIVE":
            return set()
        result: set[DistributionPlatform] = set()
        for raw in list(payload.get("allowed_platforms") or []):
            try:
                result.add(DistributionPlatform(str(getattr(raw, "value", raw)).upper()))
            except ValueError:
                continue
        return result or {adapter.platform for adapter in self._all_adapters}


def collapse_telegram_communities(opportunities: list) -> list:
    merged: dict[tuple[str, str], object] = {}
    order: list[tuple[str, str]] = []
    for item in opportunities:
        if item.platform != DistributionPlatform.TELEGRAM:
            key = (str(item.platform), f"{item.icp_id}:{item.canonical_key}")
        else:
            metadata = item.metadata if isinstance(item.metadata, dict) else {}
            entity_id = metadata.get("telegram_entity_id")
            handle = str(metadata.get("handle") or "").casefold()
            invite_hash = str(metadata.get("invite_token_hash") or "")
            identity = (
                f"entity:{entity_id}"
                if entity_id is not None
                else f"handle:{handle}"
                if handle
                else f"invite:{invite_hash}"
                if invite_hash
                else str(item.canonical_key)
            )
            key = (DistributionPlatform.TELEGRAM.value, identity)
        current = merged.get(key)
        if current is None:
            metadata = dict(item.metadata) if isinstance(item.metadata, dict) else {}
            metadata["matched_icp_ids"] = [str(item.icp_id)]
            item.metadata = metadata
            merged[key] = item
            order.append(key)
            continue
        preferred = (
            item
            if (item.relevance_score or 0) > (current.relevance_score or 0)
            else current
        )
        other = current if preferred is item else item
        metadata = dict(preferred.metadata) if isinstance(preferred.metadata, dict) else {}
        ids = list(metadata.get("matched_icp_ids") or [str(preferred.icp_id)])
        matched_icp_values = [
            str(other.icp_id),
            *list(getattr(other, "metadata", {}).get("matched_icp_ids") or []),
        ]
        for value in matched_icp_values:
            if value not in ids:
                ids.append(value)
        metadata["matched_icp_ids"] = ids
        evidence = list(preferred.evidence)
        seen = {_canonical_url(row.get("url")) for row in evidence if isinstance(row, dict)}
        for row in other.evidence:
            url = _canonical_url(row.get("url")) if isinstance(row, dict) else ""
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            evidence.append(row)
        preferred.metadata = metadata
        preferred.evidence = evidence
        merged[key] = preferred
    return [merged[key] for key in order]


def _canonical_url(value: object) -> str:
    return str(value or "").strip().split("?", 1)[0].rstrip("/")

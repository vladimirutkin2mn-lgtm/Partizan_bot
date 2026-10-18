from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.audience_intelligence import AudienceIntelligenceEngine
from app.community_first_telegram_adapter import (
    TELEGRAM_NATIVE_QUERY_CACHE_TTL,
    TELEGRAM_NATIVE_REPLENISH_NAMESPACE,
    CommunityFirstTelegramDiscoveryAdapter,
)
from app.config import get_settings
from app.discovery_intelligence import annotate_base_intelligence, propagate_surface_corroboration
from app.discovery_relevance_learning import (
    DiscoveryRelevanceLearningService,
    discovery_relevance_learning_service,
)
from app.distribution_types import DistributionPlatform
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.search import OpenAIWebSearchProvider, SearchProvider
from app.telegram_discovery_stage_queue import TelegramDiscoveryStageQueue
from app.telegram_discovery_strategy import ExpandedAudienceIntelligenceEngine
from app.telegram_native_flood_wait import research_flood_wait_until
from app.telegram_paid_discovery_budget import (
    TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE as TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE,
)
from app.telegram_paid_discovery_budget import _PaidTelegramDiscoveryBudgetProvider
from app.telegram_tgstat_catalog import (
    TGSTAT_CATALOG_RUN_NAMESPACE,
    TGStatCatalogExpansion,
)

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
        catalog_expansion: TGStatCatalogExpansion | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._telegram_only = telegram_only
        self._catalog_expansion = catalog_expansion or TGStatCatalogExpansion(
            self._store,
            enabled=bool(getattr(get_settings(), "telegram_catalog_discovery_enabled", False)),
        )
        self.last_catalog_status = "NOT_ATTEMPTED"
        self.last_paid_fallback_gate = "NOT_APPLICABLE"
        self.last_telegram_stage_queue: dict | None = None
        # Adaptive hints change the native query plan, not the permission to bypass
        # saved communities and issue new paid web searches.
        self._prefer_registered_telegram = True
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
        eligible = [
            adapter
            for adapter in self._all_adapters
            if adapter.platform in allowed
            and (not self._telegram_only or adapter.platform == DistributionPlatform.TELEGRAM)
        ]

        registry_raw = []
        telegram_adapter = next(
            (
                adapter
                for adapter in eligible
                if isinstance(adapter, CommunityFirstTelegramDiscoveryAdapter)
            ),
            None,
        )
        self.last_catalog_status = "NOT_APPLICABLE"
        self.last_paid_fallback_gate = "NOT_APPLICABLE"
        self.last_telegram_stage_queue = None
        queue = (
            TelegramDiscoveryStageQueue(self._store, product.id)
            if telegram_adapter is not None and getattr(product, "id", None) is not None
            else None
        )
        if queue is not None:
            queue.start()
        catalog_result = {"status": "NOT_APPLICABLE", "staged_count": 0}
        if self._prefer_registered_telegram and telegram_adapter is not None:
            registered = await telegram_adapter.refresh_registered(product, icps)
            if queue is not None:
                self._record_registry_stage(queue, registered)
            confirmed_count = sum(
                candidate.metadata.get("community_fit_pass") is True
                and candidate.metadata.get("native_research_status") == "VERIFIED"
                and candidate.metadata.get("access_state") != "JOIN_REQUIRED"
                for candidate in registered
            )
            # Zero-community products take the same read-only, free native
            # route as sparse registries, before any potentially paid search.
            native_before = self._store.get(
                TELEGRAM_NATIVE_REPLENISH_NAMESPACE, str(product.id)
            ) or {}
            new_communities = await telegram_adapter.replenish_native(
                product, icps, confirmed_count=confirmed_count
            )
            if queue is not None:
                self._record_native_stage(
                    queue, telegram_adapter, confirmed_count, new_communities, native_before
                )
            # A catalog candidate is NOT usable until the next native peer
            # inspection verifies actual Telegram identity and audience fit.
            # The catalog is only consulted when the native search has not
            # already found a qualifying community.
            if confirmed_count < 3 and not any(
                item.metadata.get("community_fit_pass") is True
                for item in new_communities
            ):
                catalog_result = await self._catalog_expansion.replenish(
                    product, icps, confirmed_count=confirmed_count
                )
            else:
                catalog_result = {"status": "NATIVE_COVERAGE", "staged_count": 0}
            self.last_catalog_status = str(catalog_result["status"])
            if queue is not None:
                self._record_catalog_stage(queue, catalog_result)
            registry_raw = [
                self._candidate_to_seed(icp, candidate)
                for candidate in [*registered, *new_communities]
                for icp in icps
            ]
            if registered or new_communities or catalog_result["staged_count"]:
                eligible = [
                    adapter
                    for adapter in eligible
                    if adapter.platform != DistributionPlatform.TELEGRAM
                ]
            elif isinstance(self._provider, OpenAIWebSearchProvider):
                # A native transport failure, FloodWait or skipped/cooldown
                # attempt must never be interpreted as an empty result set.
                # Paid fallback is only a last resort for an actually completed
                # recent native query that returned no qualifying communities.
                native_completed_empty = self._native_no_fit_confirmed(
                    getattr(product, "id", None)
                )
                settings = get_settings()
                if not native_completed_empty:
                    self.last_paid_fallback_gate = "NATIVE_SEARCH_NOT_COMPLETED"
                elif catalog_result["status"] not in {"DISABLED", "NO_CATEGORY"}:
                    self.last_paid_fallback_gate = "CATALOG_NOT_EXHAUSTED"
                elif not settings.telegram_paid_discovery_enabled:
                    self.last_paid_fallback_gate = "PAID_SEARCH_DISABLED"
                else:
                    self.last_paid_fallback_gate = "NATIVE_NO_FIT_CONFIRMED"
                if self.last_paid_fallback_gate != "NATIVE_NO_FIT_CONFIRMED":
                    eligible = [
                        adapter
                        for adapter in eligible
                        if adapter.platform != DistributionPlatform.TELEGRAM
                    ]

        self._adapters = eligible
        web_provider = self._provider
        paid_provider = None
        if isinstance(web_provider, OpenAIWebSearchProvider):
            settings = get_settings()
            paid_provider = _PaidTelegramDiscoveryBudgetProvider(
                web_provider,
                self._store,
                getattr(product, "id", ""),
                monthly_budget_usd=getattr(
                    settings, "telegram_paid_discovery_monthly_budget_usd", None
                ),
                query_reservation_usd=getattr(
                    settings, "telegram_paid_discovery_query_reservation_usd", None
                ),
            )
            self._provider = paid_provider
        try:
            if self._adapters:
                collection_limit = max(
                    max_opportunities, max_opportunities * max(1, len(self._adapters))
                )
                web_raw = await AudienceIntelligenceEngine.discover(
                    self,
                    product,
                    icps,
                    per_query_limit=per_query_limit,
                    max_opportunities=collection_limit,
                )
            else:
                self._last_attempts = []
                self._last_failures = []
                web_raw = []
        finally:
            self._provider = web_provider
            if queue is not None:
                self._record_paid_stage(queue, paid_provider)
                self.last_telegram_stage_queue = queue.snapshot()

        raw = [*registry_raw, *web_raw]
        if not raw:
            self._last_relevance_rejections = []
            return []
        scored = annotate_base_intelligence(raw)
        accepted, rejected = self._relevance_learning.filter(
            product=product,
            opportunities=scored,
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

        final_accepted = [*accepted, *rescued]
        final_rejections = self._relevance_learning.learn_final_rejections(
            product=product,
            icps=icps,
            accepted=final_accepted,
            rejections=rejected,
        )
        self._last_relevance_rejections = final_rejections
        propagated = propagate_surface_corroboration(final_accepted)
        collapsed = collapse_telegram_communities(propagated)
        return self._coverage_first_limit(collapsed, max_opportunities)

    @staticmethod
    def _record_registry_stage(queue: TelegramDiscoveryStageQueue, registered: list) -> None:
        attempted = [
            item for item in registered
            if item.metadata.get("registry_refresh_attempted") is True
        ]
        statuses = {
            str(item.metadata.get("registry_refresh_status") or "")
            for item in registered
        }
        if not registered:
            status, reason = "SKIPPED_NO_CANDIDATES", "EMPTY_REGISTRY"
        elif "DEFERRED_FLOOD_WAIT" in statuses:
            status, reason = "DEFERRED_RATE_LIMIT", "TELEGRAM_FLOOD_WAIT"
        elif any(s in statuses for s in ("RETRYABLE_FAILED", "INSPECTION_UNAVAILABLE")):
            status, reason = "FAILED_RETRYABLE", "NATIVE_PEER_INSPECTION_FAILED"
        elif attempted:
            status, reason = "COMPLETED", "PEER_INSPECTION_COMPLETED"
        else:
            status, reason = "SKIPPED_NO_CANDIDATES", "NO_ELIGIBLE_REFRESH_BATCH"
        next_attempt = next(
            (
                str(item.metadata["native_resume_after"])
                for item in registered
                if item.metadata.get("native_resume_after")
            ),
            None,
        )
        queue.mark(
            "registry", status, reason=reason,
            candidate_count=len(registered), next_attempt_at=next_attempt,
            source="REGISTRY_NATIVE_REFRESH", gate="PUBLIC_HANDLES_ONLY",
        )

    def _record_native_stage(
        self,
        queue: TelegramDiscoveryStageQueue,
        adapter: CommunityFirstTelegramDiscoveryAdapter,
        confirmed_count: int,
        candidates: list,
        before: dict,
    ) -> None:
        product_id = queue.product_id
        after = self._store.get(TELEGRAM_NATIVE_REPLENISH_NAMESPACE, product_id) or {}
        blocked = research_flood_wait_until(self._store)
        next_attempt = str(after.get("next_attempt_at") or "") or None
        if blocked:
            status, reason = "DEFERRED_RATE_LIMIT", "TELEGRAM_FLOOD_WAIT"
            next_attempt = blocked.isoformat()
        elif confirmed_count >= 3:
            status, reason = "SKIPPED_SUFFICIENT", "VERIFIED_REGISTRY_COVERAGE"
        elif before.get("last_attempt_at") != after.get("last_attempt_at") and after:
            last_status = str(after.get("last_status") or "")
            if last_status == "RETRYABLE_FAILED":
                status, reason = "FAILED_RETRYABLE", str(after.get("last_error_type") or "")
            elif candidates:
                status, reason = "COMPLETED", "NATIVE_CANDIDATES_FOUND"
            else:
                status, reason = "NO_FIT", last_status or "NO_FIT_MATCH"
        elif after.get("last_attempt_at"):
            status, reason = "DEFERRED_COOLDOWN", "NATIVE_QUERY_ALREADY_ATTEMPTED"
            if not next_attempt:
                try:
                    at = datetime.fromisoformat(str(after["last_attempt_at"]))
                    next_attempt = (
                        at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
                    ) + timedelta(hours=24)
                    next_attempt = next_attempt.isoformat()
                except ValueError:
                    next_attempt = None
        elif not callable(getattr(adapter._research_connector, "discover", None)):
            status, reason = "SKIPPED_DISABLED", "NATIVE_RESEARCH_UNAVAILABLE"
        else:
            status, reason = "SKIPPED_NO_CANDIDATES", "NATIVE_QUERY_NOT_PLANNED"
        queue.mark(
            "native", status, reason=reason,
            candidate_count=len(candidates), next_attempt_at=next_attempt,
            source="TELETHON_NATIVE", query=str(after.get("last_query") or ""),
            gate="COMMUNITY_FIT_AND_NATIVE_VERIFICATION",
            error_type=(
                str(after.get("last_error_type"))
                if status == "FAILED_RETRYABLE" and after.get("last_error_type")
                else None
            ),
        )

    def _record_catalog_stage(
        self, queue: TelegramDiscoveryStageQueue, result: dict
    ) -> None:
        source_status = str(result.get("status") or "NOT_APPLICABLE")
        status = {
            "STAGED_UNVERIFIED": "STAGED_UNVERIFIED",
            "NOT_DUE": "DEFERRED_COOLDOWN",
            "ALREADY_CLAIMED": "DEFERRED_COOLDOWN",
            "SOURCE_THROTTLED": "DEFERRED_RATE_LIMIT",
            "RETRYABLE_FAILED": "FAILED_RETRYABLE",
            "FORMAT_UNKNOWN": "FAILED_RETRYABLE",
            "SOURCE_UNAVAILABLE": "FAILED_RETRYABLE",
            "SOURCE_HTTP_ERROR": "FAILED_RETRYABLE",
            "NO_VALID_COMMUNITIES": "FAILED_RETRYABLE",
            "DISABLED": "SKIPPED_DISABLED",
            "NATIVE_COVERAGE": "SKIPPED_SUFFICIENT",
            "SUFFICIENT_REGISTRY": "SKIPPED_SUFFICIENT",
            "NO_CATEGORY": "SKIPPED_NO_CANDIDATES",
        }.get(source_status, "NOT_APPLICABLE")
        source_state = self._store.get(TGSTAT_CATALOG_RUN_NAMESPACE, queue.product_id) or {}
        queue.mark(
            "catalog", status, reason=source_status,
            candidate_count=int(result.get("staged_count") or 0),
            source="TGSTAT_PUBLIC",
            query=(
                f"{source_state.get('category') or ''}:{source_state.get('surface') or ''}"
                if source_state.get("category") else None
            ),
            gate="CATALOG_UNVERIFIED_REQUIRES_NATIVE",
            next_attempt_at=(
                str(source_state["next_attempt_at"])
                if status in {"DEFERRED_COOLDOWN", "DEFERRED_RATE_LIMIT", "FAILED_RETRYABLE"}
                and source_state.get("next_attempt_at")
                else None
            ),
            error_type=(
                str(source_state.get("error_type"))
                if status == "FAILED_RETRYABLE" and source_state.get("error_type")
                else None
            ),
        )

    def _record_paid_stage(
        self, queue: TelegramDiscoveryStageQueue, paid_provider
    ) -> None:
        result = (
            str(paid_provider.last_paid_status)
            if paid_provider is not None
            else "NOT_APPLICABLE"
        )
        if result == "NOT_ATTEMPTED":
            result = self.last_paid_fallback_gate
        status = {
            "COMPLETED_BILLING_UNKNOWN": "COMPLETED",
            "PROVIDER_FAILED_CHARGE_UNKNOWN": "FAILED_RETRYABLE",
            "DAILY_QUERY_LIMIT": "DEFERRED_COOLDOWN",
            "MONTHLY_BUDGET_EXHAUSTED": "BUDGET_EXHAUSTED",
            "MONTHLY_BUDGET_INSUFFICIENT": "BUDGET_EXHAUSTED",
            "COST_ENVELOPE_NOT_CONFIGURED": "BLOCKED",
            "PAID_SEARCH_DISABLED": "SKIPPED_DISABLED",
            "NATIVE_SEARCH_NOT_COMPLETED": "BLOCKED",
            "CATALOG_NOT_EXHAUSTED": "BLOCKED",
        }.get(result, "NOT_APPLICABLE")
        paid_day = self._store.get(
            TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE,
            f"{queue.product_id}:{datetime.now(UTC).date().isoformat()}",
        ) or {}
        queue.mark(
            "paid", status, reason=result, source="OPENAI_WEB",
            query=str(paid_day.get("query") or ""),
            gate="OPERATOR_OPT_IN_AND_MONTHLY_ENVELOPE",
        )

    def _native_no_fit_confirmed(self, product_id: object) -> bool:
        if product_id is None:
            return False
        state = self._store.get(TELEGRAM_NATIVE_REPLENISH_NAMESPACE, str(product_id)) or {}
        if state.get("last_status") != "NO_FIT_MATCH":
            return False
        try:
            completed = datetime.fromisoformat(str(state.get("last_attempt_at") or ""))
        except ValueError:
            return False
        completed = (
            completed.replace(tzinfo=UTC)
            if completed.tzinfo is None
            else completed.astimezone(UTC)
        )
        age = datetime.now(UTC) - completed
        return timedelta(0) <= age <= TELEGRAM_NATIVE_QUERY_CACHE_TTL

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
        configured = payload.get("allowed_platforms")
        if configured is None:
            return {adapter.platform for adapter in self._all_adapters}
        result: set[DistributionPlatform] = set()
        for raw in list(configured or []):
            try:
                result.add(DistributionPlatform(str(getattr(raw, "value", raw)).upper()))
            except ValueError:
                continue
        return result


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

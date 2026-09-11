from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.audience_intelligence import AudienceIntelligenceEngine
from app.audience_intelligence_service import (
    AUDIENCE_MAP_NAMESPACE,
    AUDIENCE_OPPORTUNITY_NAMESPACE,
    audience_intelligence_service,
)
from app.community_distribution_acceptance import community_distribution_acceptance_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_schemas import (
    AudienceDistributionMapView,
    DistributionOpportunitySeed,
    DistributionOpportunityView,
)
from app.distribution_types import DistributionPlatform
from app.icp_service import icp_service
from app.models import ProductProfileStatus
from app.opportunity_enrichment import OpportunityEnrichmentService
from app.platform_discovery import RedditDiscoveryAdapter, TelegramDiscoveryAdapter
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.search import get_search_provider

TARGET_ISSUES = {250, 252}
MAX_REDDIT_ENRICHMENTS = 3
ALLOWED_CONFIRMED_PREVIEW_RESEARCH_STATES = {
    "FOUND",
    "NEEDS_MORE_RESEARCH",
    "UNAVAILABLE",
}


@dataclass(frozen=True, slots=True)
class EligibleResearchContext:
    product: object
    icp_result: object
    existing_map: AudienceDistributionMapView | None
    source: str


def _phase_state() -> dict[int, tuple[bool, bool]]:
    report = community_distribution_acceptance_service.report(None)
    return {
        phase.issue_number: (phase.current_ready, phase.production_verified)
        for phase in report.phases
        if phase.issue_number in TARGET_ISSUES
    }


def _context_for_product(
    product_id: UUID,
    *,
    existing_map: AudienceDistributionMapView | None,
    source: str,
) -> EligibleResearchContext | None:
    try:
        product = product_intake_service.get_product(product_id)
        icp_result = icp_service.get(product_id)
    except (KeyError, ValueError):
        return None
    if product.status != ProductProfileStatus.CONFIRMED or not icp_result.icps:
        return None
    return EligibleResearchContext(
        product=product,
        icp_result=icp_result,
        existing_map=existing_map,
        source=source,
    )


def _select_existing_distribution_context() -> EligibleResearchContext | None:
    store = get_runtime_store()
    candidates: list[tuple[int, str, EligibleResearchContext]] = []
    for payload in store.list_namespace(AUDIENCE_MAP_NAMESPACE):
        try:
            existing_map = AudienceDistributionMapView.model_validate(payload)
        except ValueError:
            continue
        context = _context_for_product(
            existing_map.product_id,
            existing_map=existing_map,
            source="EXISTING_DISTRIBUTION_MAP",
        )
        if context is None:
            continue
        existing_platforms = {item.platform for item in existing_map.opportunities}
        target_overlap = sum(
            platform in existing_platforms
            for platform in (DistributionPlatform.TELEGRAM, DistributionPlatform.REDDIT)
        )
        candidates.append((target_overlap, str(existing_map.product_id), context))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _select_confirmed_customer_context() -> EligibleResearchContext | None:
    store = get_runtime_store()
    candidates: list[tuple[int, str, EligibleResearchContext]] = []
    state_priority = {"FOUND": 0, "NEEDS_MORE_RESEARCH": 1, "UNAVAILABLE": 2}
    for project in store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
        if project.get("deleted_at") or project.get("understanding_confirmed") is not True:
            continue
        product_id_raw = project.get("product_id")
        preview = project.get("preview")
        if not product_id_raw or not isinstance(preview, dict):
            continue
        research_state = str(preview.get("free_research_status") or "").upper()
        if research_state not in ALLOWED_CONFIRMED_PREVIEW_RESEARCH_STATES:
            continue
        try:
            product_id = UUID(str(product_id_raw))
        except (TypeError, ValueError):
            continue
        context = _context_for_product(
            product_id,
            existing_map=None,
            source="CONFIRMED_CUSTOMER_PREVIEW_RESEARCH",
        )
        if context is None:
            continue
        candidates.append((state_priority[research_state], str(product_id), context))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _select_research_context() -> EligibleResearchContext | None:
    return _select_existing_distribution_context() or _select_confirmed_customer_context()


def _opportunity_key(
    item: DistributionOpportunityView | DistributionOpportunitySeed,
) -> tuple[str, str, str]:
    return (str(item.icp_id), item.platform.value, item.canonical_key)


def _persist_discovery(
    context: EligibleResearchContext,
    seeds: list[DistributionOpportunitySeed],
) -> list[DistributionOpportunityView]:
    store = get_runtime_store()
    existing_map = context.existing_map
    opportunities = list(existing_map.opportunities) if existing_map is not None else []
    index_by_key = {_opportunity_key(item): index for index, item in enumerate(opportunities)}
    refreshed: list[DistributionOpportunityView] = []

    for seed in seeds:
        key = _opportunity_key(seed)
        existing_index = index_by_key.get(key)
        if existing_index is None:
            view = DistributionOpportunityView(id=uuid4(), **seed.model_dump())
            index_by_key[key] = len(opportunities)
            opportunities.append(view)
        else:
            existing = opportunities[existing_index]
            view = DistributionOpportunityView(
                id=existing.id,
                legacy_channel_id=existing.legacy_channel_id,
                **seed.model_dump(),
            )
            opportunities[existing_index] = view
        store.put(
            AUDIENCE_OPPORTUNITY_NAMESPACE,
            str(view.id),
            view.model_dump(mode="json"),
        )
        refreshed.append(view)

    if not opportunities:
        return refreshed

    updated_map = AudienceDistributionMapView(
        product_id=context.product.id,
        top_icp_count=(
            max(1, existing_map.top_icp_count) if existing_map is not None else 1
        ),
        opportunity_count=len(opportunities),
        opportunities=opportunities,
    )
    store.put(
        AUDIENCE_MAP_NAMESPACE,
        str(updated_map.product_id),
        updated_map.model_dump(mode="json"),
    )
    audience_intelligence_service.reset()
    audience_intelligence_service.get(updated_map.product_id)
    return refreshed


async def _run() -> dict:
    phase_state = _phase_state()
    missing_state = sorted(TARGET_ISSUES - set(phase_state))
    if missing_state:
        return {
            "status": "INVALID_ACCEPTANCE_STATE",
            "missing_issue_numbers": missing_state,
        }

    requested = {
        issue_number
        for issue_number, (current_ready, production_verified) in phase_state.items()
        if current_ready and not production_verified
    }
    blocked = sorted(
        issue_number
        for issue_number, (current_ready, production_verified) in phase_state.items()
        if not current_ready and not production_verified
    )
    if not requested:
        return {
            "status": "NO_RESEARCH_NEEDED",
            "blocked_issue_numbers": blocked,
            "requested_issue_numbers": [],
        }

    context = _select_research_context()
    if context is None:
        return {
            "status": "NO_ELIGIBLE_CONFIRMED_RESEARCH_CONTEXT",
            "blocked_issue_numbers": blocked,
            "requested_issue_numbers": sorted(requested),
        }

    adapters = []
    if 250 in requested:
        adapters.append(TelegramDiscoveryAdapter())
    if 252 in requested:
        adapters.append(RedditDiscoveryAdapter())

    engine = AudienceIntelligenceEngine(
        get_search_provider(),
        max_concurrency=2,
        adapters=adapters,
    )
    seeds = await engine.discover(
        product=context.product,
        icps=context.icp_result.icps[:1],
        per_query_limit=5,
        max_opportunities=20,
    )
    refreshed = _persist_discovery(context, seeds) if seeds else []

    telegram_verified = sum(
        item.platform == DistributionPlatform.TELEGRAM
        and str(item.metadata.get("native_research_status", "")).upper() == "VERIFIED"
        and bool(item.metadata.get("telegram_entity_id"))
        and bool(item.metadata.get("source_checked_at"))
        for item in refreshed
    )

    reddit_enriched = 0
    reddit_verified_policies = 0
    if 252 in requested:
        reddit_candidates = sorted(
            (item for item in refreshed if item.platform == DistributionPlatform.REDDIT),
            key=lambda item: (-(item.relevance_score or 0), item.canonical_key),
        )[:MAX_REDDIT_ENRICHMENTS]
        enrichment_service = OpportunityEnrichmentService()
        for candidate in reddit_candidates:
            result = await enrichment_service.enrich(context.product, candidate, limit=6)
            reddit_enriched += 1
            policy = result.opportunity.metadata.get("community_policy_research", {})
            if str(policy.get("research_status", "")).upper() == "VERIFIED":
                reddit_verified_policies += 1

    failures = sorted(
        {
            (failure.platform.value, failure.error_type)
            for failure in engine.last_failures
        }
    )
    return {
        "status": "COMPLETED",
        "context_source": context.source,
        "requested_issue_numbers": sorted(requested),
        "blocked_issue_numbers": blocked,
        "discovered_opportunity_count": len(refreshed),
        "telegram_verified_opportunity_count": telegram_verified,
        "reddit_enriched_opportunity_count": reddit_enriched,
        "reddit_verified_policy_count": reddit_verified_policies,
        "provider_failure_types": [
            {"platform": platform, "error_type": error_type}
            for platform, error_type in failures
        ],
    }


def main() -> int:
    try:
        result = asyncio.run(_run())
    except Exception as exc:  # fail closed without echoing customer/provider payloads
        print(
            json.dumps(
                {"status": "FAILED", "error_type": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from urllib.parse import unquote, urlsplit
from uuid import UUID

from app.audience_intelligence import AudienceIntelligenceEngine
from app.community_first_audience_engine import collapse_telegram_communities
from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.discovery_execution_context import DiscoveryExecutionContext, DiscoveryExecutionMode
from app.discovery_relevance_learning import discovery_relevance_learning_service
from app.icp_service import icp_service
from app.openai_usage import (
    openai_usage_context,
    openai_usage_step_context,
    summarize_openai_usage,
)
from app.product_intake import product_intake_service
from app.search import DiscoveryQuery, SearchHit, SearchProvider, get_search_provider


def _as_value(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _selected_metadata(metadata: object) -> dict:
    if not isinstance(metadata, dict):
        return {}
    keys = (
        "handle",
        "source_type",
        "access_state",
        "community_fit_pass",
        "community_fit_reason",
        "community_fit_terms",
        "community_registry_key",
        "action_target_url",
        "action_target_specific",
        "action_target_resolution",
        "action_target_quality",
        "conversation_signals",
        "target_match_terms",
        "community_match_terms",
        "action_target_mode",
        "target_published_at",
        "target_age_days",
        "opportunity_scope",
        "actionability",
        "community_only_reason",
        "telegram_entity_id",
        "linked_discussion_id",
        "native_research_status",
        "source_checked_at",
        "last_activity_at",
        "surface_capabilities",
        "recent_context",
        "research_signals",
        "relevance_guard",
        "matched_icp_ids",
    )
    return {key: metadata.get(key) for key in keys if key in metadata}


def _evidence_rows(opportunity: object) -> list[dict]:
    rows: list[dict] = []
    for item in list(getattr(opportunity, "evidence", []) or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "query": str(item.get("query") or ""),
                "title": str(item.get("title") or "")[:500],
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("snippet") or "")[:1200],
                "source_class": str(item.get("source_class") or ""),
                "signal_tags": list(item.get("signal_tags") or []),
                "source_metadata": dict(item.get("source_metadata") or {}),
            }
        )
    return rows


def _failure_rows(failures: object) -> list[dict]:
    rows: list[dict] = []
    for failure in list(failures or []):
        rows.append(
            {
                "platform": _as_value(getattr(failure, "platform", "")),
                "query": str(getattr(failure, "query", "") or "")[:500],
                "error_type": str(getattr(failure, "error_type", "") or "")[:120],
                "message": str(getattr(failure, "message", "") or "")[:480],
            }
        )
    return rows


def _telegram_normalization_diagnostic(url: str) -> dict:
    parts = urlsplit(str(url or "").strip())
    host = parts.netloc.lower().removeprefix("www.")
    segments = [unquote(segment) for segment in parts.path.split("/") if segment]
    if host not in {"t.me", "telegram.me"}:
        return {
            "normalized": False,
            "normalization_reason": "NON_TELEGRAM_HOST",
            "host": host,
        }
    if not segments:
        return {
            "normalized": False,
            "normalization_reason": "MISSING_TELEGRAM_SLUG",
            "host": host,
        }
    if segments[0] == "s" and len(segments) >= 2:
        segments = segments[1:]
    slug = segments[0]
    if slug.startswith("+"):
        return {
            "normalized": True,
            "normalization_reason": "TELEGRAM_INVITE_ACCEPTED",
            "host": host,
            "access_state": "JOIN_REQUIRED",
        }
    if slug.lower() == "joinchat":
        return {
            "normalized": True,
            "normalization_reason": "TELEGRAM_INVITE_ACCEPTED",
            "host": host,
            "access_state": "JOIN_REQUIRED",
        }
    return {
        "normalized": True,
        "normalization_reason": "TELEGRAM_SLUG_ACCEPTED",
        "host": host,
        "handle": slug,
        "canonical_url": f"https://t.me/{slug}",
    }


class _TraceCommunityFirstTelegramDiscoveryAdapter(CommunityFirstTelegramDiscoveryAdapter):
    """Use production discovery logic while honoring trace side-effect policy."""

    def __init__(self, *args, execution_context: DiscoveryExecutionContext, **kwargs) -> None:
        self._execution_context = execution_context
        super().__init__(*args, **kwargs)

    def _remember_community(self, candidate) -> None:
        if not self._execution_context.policy.allow_state_writes:
            return
        super()._remember_community(candidate)


class _TracingSearchProvider(SearchProvider):
    def __init__(
        self,
        delegate: SearchProvider,
        execution_context: DiscoveryExecutionContext,
    ) -> None:
        self._delegate = delegate
        self._execution_context = execution_context
        self._calls: list[dict] = []

    async def search(
        self,
        discovery_query: DiscoveryQuery,
        limit: int = 5,
    ) -> list[SearchHit]:
        if not self._execution_context.policy.allow_network:
            raise RuntimeError(
                f"Network search is disabled in {self._execution_context.mode.value} mode"
            )
        query_id = self._execution_context.next_query_id()
        step_id = self._execution_context.next_step_id("search")
        with openai_usage_step_context(step_id=step_id, query_id=query_id):
            hits = await self._delegate.search(discovery_query, limit=limit)
        self._calls.append(
            {
                "query_id": query_id,
                "step_id": step_id,
                "query": discovery_query.query,
                "source_class": discovery_query.source_class.value,
                "hits": list(hits),
            }
        )
        return hits

    def raw_hit_rows(self) -> list[dict]:
        rows: list[dict] = []
        for call in sorted(self._calls, key=lambda item: item["query_id"]):
            for index, hit in enumerate(call["hits"], start=1):
                rows.append(
                    {
                        "query_id": call["query_id"],
                        "step_id": call["step_id"],
                        "query": call["query"],
                        "source_class": call["source_class"],
                        "rank": index,
                        "title": str(hit.title or "")[:500],
                        "url": str(hit.url or ""),
                        "snippet": str(hit.snippet or "")[:1200],
                        "evidence_binding": str(
                            getattr(hit, "metadata", {}).get("evidence_binding") or ""
                        ),
                        **_telegram_normalization_diagnostic(hit.url),
                    }
                )
        return rows

    def call_metadata(self, query: str) -> dict:
        for call in self._calls:
            if call["query"] == query:
                return {
                    "query_id": call["query_id"],
                    "step_id": call["step_id"],
                }
        return {}


def _filters() -> dict:
    return {
        "community_first": {
            "opportunity_identity": "TELEGRAM_COMMUNITY",
            "post_urls_are_evidence_not_separate_opportunities": True,
            "invite_links_are_retained": True,
            "source_type_bot_is_excluded": True,
            "latest_unblocked_target_selected": True,
            "post_age_is_hard_gate": False,
        },
        "semantic_gate": {
            "signal_family_ratio_threshold": 0.15,
            "minimum_matched_terms": 2,
            "direct_product_anchor_minimum_matched_terms": 1,
            "pass_paths": {
                "DIVERSIFIED_MATCH": (
                    "at least 2 signal families and at least 2 matched terms"
                ),
                "DIRECT_PROBLEM_MATCH": (
                    "pain or trigger >= 0.25, at least 1 intent hit, "
                    "and at least 2 matched terms"
                ),
                "CORROBORATED_FIT": (
                    "fit >= 0.35, at least 2 independent evidence items, "
                    "intent or alternative signal, and at least 2 matched terms"
                ),
                "CONFIDENCE_SUPPORTED": (
                    "MEDIUM/HIGH confidence, at least 2 signal families, "
                    "and at least 2 matched terms"
                ),
                "DIRECT_PRODUCT_ANCHOR": (
                    "at least 1 direct product-anchor term, MEDIUM/HIGH confidence, "
                    "and either at least 2 signal families or strong pain/trigger plus intent"
                ),
            },
            "reject_paths": [
                "NEGATIVE_COMMUNITY_MEMORY",
                "NEGATIVE_THEME_MEMORY",
                "INSUFFICIENT_SEMANTIC_EVIDENCE",
            ],
            "legacy_gate_still_recorded": True,
            "community_fit_can_rescue": "INSUFFICIENT_SEMANTIC_EVIDENCE only",
        },
        "negative_learning": {
            "exact_community_min_observations": 2,
            "negative_theme_min_observations": 2,
            "ttl_days": 30,
            "trace_writes_learning": False,
        },
        "note": (
            "This trace uses community-first Telegram discovery and records the legacy "
            "semantic decision for comparison. It never calls filter_and_learn."
        ),
    }


def _trace_decision(opportunity: object, snapshot: dict, product: object) -> dict:
    product_anchor_terms = (
        discovery_relevance_learning_service._product_anchor_terms(product)  # noqa: SLF001
    )
    return discovery_relevance_learning_service._decision(  # noqa: SLF001
        opportunity,
        snapshot,
        product_anchor_terms=product_anchor_terms,
    )


async def build_trace(product_id: UUID, *, top_icp_count: int = 3) -> dict:
    execution_context = DiscoveryExecutionContext.create(
        DiscoveryExecutionMode.DEBUG_TRACE,
        product_id,
    )
    product = product_intake_service.get_product(product_id)
    icp_result = icp_service.get(product_id)
    icps = list(icp_result.icps[:top_icp_count])
    if not icps:
        raise ValueError("ICP generation must contain at least one segment")

    provider = get_search_provider()
    tracing_provider = _TracingSearchProvider(provider, execution_context)
    adapter = _TraceCommunityFirstTelegramDiscoveryAdapter(
        relevance_learning_service=discovery_relevance_learning_service,
        execution_context=execution_context,
    )
    engine = AudienceIntelligenceEngine(tracing_provider, adapters=[adapter])
    usage_rows: list[dict] = []

    with openai_usage_context(
        "discovery_trace",
        product_id,
        run_id=execution_context.run_id,
        collector=usage_rows,
    ):
        raw = await engine.discover(product=product, icps=icps)
    raw = collapse_telegram_communities(raw)
    snapshot = discovery_relevance_learning_service.snapshot(product_id)

    opportunities: list[dict] = []
    verdict_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for opportunity in raw:
        decision = _trace_decision(opportunity, snapshot, product)
        metadata = getattr(opportunity, "metadata", {})
        legacy_accepted = bool(decision.get("accepted"))
        legacy_reason = str(decision.get("reason") or "UNKNOWN")
        community_fit = bool(
            isinstance(metadata, dict) and metadata.get("community_fit_pass") is True
        )
        rescued = community_fit and not legacy_accepted and legacy_reason in {
            "INSUFFICIENT_SEMANTIC_EVIDENCE",
            "UNKNOWN",
        }
        verdict = "PASS" if legacy_accepted or rescued else "REJECT"
        reason = "COMMUNITY_FIRST_FIT" if rescued else legacy_reason
        verdict_counts[verdict] += 1
        reason_counts[reason] += 1

        signals = metadata.get("research_signals") if isinstance(metadata, dict) else None
        evidence = _evidence_rows(opportunity)
        queries = sorted({row["query"] for row in evidence if row["query"]})
        opportunities.append(
            {
                "community_id": str(getattr(opportunity, "canonical_key", "") or ""),
                "verdict": verdict,
                "gate_reason": reason,
                "legacy_gate_reason": legacy_reason,
                "community_first_rescue": rescued,
                "signal_families": list(decision.get("signal_families") or []),
                "direct_product_anchor_count": int(
                    decision.get("direct_product_anchor_count") or 0
                ),
                "title": str(getattr(opportunity, "title", "") or ""),
                "url": str(getattr(opportunity, "url", "") or ""),
                "canonical_key": str(getattr(opportunity, "canonical_key", "") or ""),
                "kind": _as_value(getattr(opportunity, "kind", "")),
                "relevance_score": getattr(opportunity, "relevance_score", None),
                "queries": queries,
                "research_signals": signals if isinstance(signals, dict) else {},
                "metadata": _selected_metadata(metadata),
                "evidence": evidence,
            }
        )

    opportunities.sort(
        key=lambda row: (
            row["verdict"] != "PASS",
            -(float(row["relevance_score"] or 0.0)),
            row["title"],
        )
    )

    usage_by_query = {
        str(row.get("query_id")): row
        for row in usage_rows
        if row.get("query_id") is not None
    }
    attempts = []
    for attempt in engine.last_attempts:
        identifiers = tracing_provider.call_metadata(attempt.query)
        query_id = str(identifiers.get("query_id") or "")
        attempts.append(
            {
                **identifiers,
                "query": attempt.query,
                "search_hits": attempt.search_hit_count,
                "normalized_candidates": attempt.normalized_candidate_count,
                "enriched_candidates": attempt.enriched_candidate_count,
                "search_error_type": attempt.search_error_type,
                "enrichment_error_type": attempt.enrichment_error_type,
                "provider_usage": usage_by_query.get(query_id),
            }
        )
    failures = _failure_rows(engine.last_failures)
    failure_counts = Counter(row["error_type"] for row in failures if row["error_type"])
    raw_hits = tracing_provider.raw_hit_rows()
    normalization_counts = Counter(str(row["normalization_reason"]) for row in raw_hits)

    return {
        "status": "TRACE_COMPLETE",
        "read_only": True,
        "execution_context": execution_context.as_dict(),
        "product": {
            "id": str(product.id),
            "name": product.name,
            "market": getattr(product, "market", None),
            "language": getattr(product, "language", None),
            "problem_or_desire": getattr(product, "problem_or_desire", None),
            "known_audience": list(getattr(product, "known_audience", []) or []),
        },
        "icps": [
            {
                "id": str(icp.id),
                "rank": icp.rank,
                "title": icp.title,
                "description": icp.description,
                "pain": icp.pain,
                "trigger": icp.trigger,
                "alternatives": list(icp.alternatives),
            }
            for icp in icps
        ],
        "search": {
            "provider": type(provider).__name__,
            "platform": "TELEGRAM",
            "query_attempt_count": len(attempts),
            "attempts": attempts,
            "failures": failures,
            "raw_hits": raw_hits,
            "normalization_reasons": dict(sorted(normalization_counts.items())),
        },
        "usage": {
            "summary": summarize_openai_usage(usage_rows),
            "calls": usage_rows,
        },
        "current_negative_memory": {
            "blocked_keys": sorted(snapshot["blocked_keys"]),
            "negative_terms": sorted(snapshot["negative_terms"]),
        },
        "filters": _filters(),
        "summary": {
            "raw_opportunity_count": len(opportunities),
            "unique_telegram_communities": len(opportunities),
            "verdicts": dict(sorted(verdict_counts.items())),
            "reasons": dict(sorted(reason_counts.items())),
            "search_failures": dict(sorted(failure_counts.items())),
        },
        "opportunities": opportunities,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Trace community-first Telegram discovery and semantic decisions without mutations."
        )
    )
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--top-icp-count", type=int, default=3)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = asyncio.run(
            build_trace(args.product_id, top_icp_count=max(1, min(args.top_icp_count, 5)))
        )
    except (KeyError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

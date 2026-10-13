from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from uuid import UUID

from app.audience_intelligence import AudienceIntelligenceEngine
from app.discovery_relevance_learning import discovery_relevance_learning_service
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.search import get_search_provider
from app.telegram_discovery_strategy import ExpandedTelegramDiscoveryAdapter


def _as_value(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _selected_metadata(metadata: object) -> dict:
    if not isinstance(metadata, dict):
        return {}
    keys = (
        "handle",
        "action_target_url",
        "action_target_specific",
        "telegram_entity_id",
        "linked_discussion_id",
        "source_checked_at",
        "research_signals",
        "relevance_guard",
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
            }
        )
    return rows


def _filters() -> dict:
    return {
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
        },
        "negative_learning": {
            "exact_community_min_observations": 2,
            "negative_theme_min_observations": 2,
            "ttl_days": 30,
        },
        "note": (
            "This trace evaluates the current gate but never calls filter_and_learn, "
            "so it does not add negative-learning observations."
        ),
    }


def _trace_decision(opportunity: object, snapshot: dict, product: object) -> dict:
    """Evaluate exactly the same semantic inputs as production without learning writes."""
    product_anchor_terms = discovery_relevance_learning_service._product_anchor_terms(  # noqa: SLF001
        product
    )
    return discovery_relevance_learning_service._decision(  # noqa: SLF001
        opportunity,
        snapshot,
        product_anchor_terms=product_anchor_terms,
    )


async def build_trace(product_id: UUID, *, top_icp_count: int = 3) -> dict:
    product = product_intake_service.get_product(product_id)
    icp_result = icp_service.get(product_id)
    icps = list(icp_result.icps[:top_icp_count])
    if not icps:
        raise ValueError("ICP generation must contain at least one segment")

    provider = get_search_provider()
    adapter = ExpandedTelegramDiscoveryAdapter(
        relevance_learning_service=discovery_relevance_learning_service,
    )
    engine = AudienceIntelligenceEngine(provider, adapters=[adapter])

    # Deliberately use the base AudienceIntelligenceEngine. It performs the same current
    # Telegram query construction, search and enrichment, but does not call the semantic
    # gate's filter_and_learn method and therefore cannot add rejection memory.
    raw = await engine.discover(product=product, icps=icps)
    snapshot = discovery_relevance_learning_service.snapshot(product_id)

    opportunities: list[dict] = []
    verdict_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for opportunity in raw:
        decision = _trace_decision(opportunity, snapshot, product)
        verdict = "PASS" if decision.get("accepted") else "REJECT"
        reason = str(decision.get("reason") or "UNKNOWN")
        verdict_counts[verdict] += 1
        reason_counts[reason] += 1

        metadata = getattr(opportunity, "metadata", {})
        signals = metadata.get("research_signals") if isinstance(metadata, dict) else None
        evidence = _evidence_rows(opportunity)
        queries = sorted({row["query"] for row in evidence if row["query"]})
        opportunities.append(
            {
                "verdict": verdict,
                "gate_reason": reason,
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

    attempts = [
        {
            "query": attempt.query,
            "search_hits": attempt.search_hit_count,
            "normalized_candidates": attempt.normalized_candidate_count,
            "enriched_candidates": attempt.enriched_candidate_count,
            "search_error_type": attempt.search_error_type,
            "enrichment_error_type": attempt.enrichment_error_type,
        }
        for attempt in engine.last_attempts
    ]

    return {
        "status": "TRACE_COMPLETE",
        "read_only": True,
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
        },
        "current_negative_memory": {
            "blocked_keys": sorted(snapshot["blocked_keys"]),
            "negative_terms": sorted(snapshot["negative_terms"]),
        },
        "filters": _filters(),
        "summary": {
            "raw_opportunity_count": len(opportunities),
            "verdicts": dict(sorted(verdict_counts.items())),
            "reasons": dict(sorted(reason_counts.items())),
        },
        "opportunities": opportunities,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trace current Telegram discovery and semantic-gate decisions without mutations."
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

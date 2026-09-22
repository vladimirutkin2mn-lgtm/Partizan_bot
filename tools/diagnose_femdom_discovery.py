from __future__ import annotations

import asyncio
import json
import traceback
from collections import Counter
from uuid import UUID

from app.audience_intelligence import AudienceIntelligenceEngine
from app.audience_intelligence_service import (
    AUDIENCE_MAP_NAMESPACE,
    audience_intelligence_service,
)
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_types import DistributionPlatform
from app.telegram_client_publishing import customer_telegram_client_publish_service
from app.growth_balance import GROWTH_BALANCE_TOPUP_NAMESPACE
from app.icp_service import icp_service
from app.platform_discovery import TelegramDiscoveryAdapter
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.search import get_search_provider

TARGET_PRODUCT_NAME = "FemDom"


def _safe_existing_map(store, product_id: UUID) -> dict:
    payload = store.get(AUDIENCE_MAP_NAMESPACE, str(product_id))
    if not isinstance(payload, dict):
        return {
            "present": False,
            "opportunity_count": 0,
            "diagnostics": {},
        }
    diagnostics = payload.get("diagnostics")
    return {
        "present": True,
        "opportunity_count": int(payload.get("opportunity_count") or 0),
        "diagnostics": diagnostics if isinstance(diagnostics, dict) else {},
    }


def _find_target_project() -> tuple[dict, object, str] | tuple[None, None, None]:
    store = get_runtime_store()
    active: list[tuple[dict, object]] = []
    exact_name: list[tuple[dict, object]] = []
    textual_match: list[tuple[dict, object]] = []
    dogfood_fingerprint: list[tuple[dict, object]] = []

    funded_cents_by_project: dict[str, int] = {}
    for topup in store.list_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE):
        if not isinstance(topup, dict) or topup.get("state") != "PAID":
            continue
        project_id = str(topup.get("project_id") or "")
        if not project_id:
            continue
        funded_cents_by_project[project_id] = (
            funded_cents_by_project.get(project_id, 0)
            + int(topup.get("amount_cents") or 0)
        )

    for project in store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
        if not isinstance(project, dict) or project.get("deleted_at"):
            continue
        product_id_raw = project.get("product_id")
        if not product_id_raw:
            continue
        try:
            product_id = UUID(str(product_id_raw))
            product = product_intake_service.get_product(product_id)
        except (KeyError, TypeError, ValueError):
            continue

        active.append((project, product))
        if str(product.name or "").strip().casefold() == TARGET_PRODUCT_NAME.casefold():
            exact_name.append((project, product))

        searchable_parts = [
            str(project.get("brief") or ""),
            str(project.get("product_link") or ""),
            str(project.get("website_url") or ""),
            str(getattr(product, "name", "") or ""),
            str(getattr(product, "description", "") or ""),
            str(getattr(product, "problem_or_desire", "") or ""),
            str(getattr(product, "value_proposition", "") or ""),
        ]
        searchable_text = " ".join(searchable_parts).casefold()
        if "femdom" in searchable_text or "fem dom" in searchable_text:
            textual_match.append((project, product))

        preferences = project.get("channel_preferences")
        telegram_mode = (
            str(preferences.get("TELEGRAM") or "").upper()
            if isinstance(preferences, dict)
            else ""
        )
        funded_cents = funded_cents_by_project.get(str(project.get("id") or ""), 0)
        if (
            telegram_mode == "AUTO"
            and funded_cents == 1000
            and bool(project.get("launch_unlocked"))
            and str(project.get("research_state") or "") == "READY"
        ):
            dogfood_fingerprint.append((project, product))

    if len(exact_name) == 1:
        project, product = exact_name[0]
        return project, product, "EXACT_PRODUCT_NAME"

    if not exact_name and len(textual_match) == 1:
        project, product = textual_match[0]
        return project, product, "UNIQUE_FEMDOM_TEXT_MATCH"

    # The production FemDom dogfood project is known to have Telegram in AUTO,
    # exactly $10 of paid Growth Balance funding, launch unlocked, and completed
    # research. Use that operational fingerprint only when it resolves to one project.
    if not exact_name and len(dogfood_fingerprint) == 1:
        project, product = dogfood_fingerprint[0]
        return project, product, "UNIQUE_TELEGRAM_AUTO_FUNDED_10_USD"

    print(
        json.dumps(
            {
                "status": "TARGET_NOT_UNIQUE",
                "target_product_name": TARGET_PRODUCT_NAME,
                "exact_name_match_count": len(exact_name),
                "femdom_text_match_count": len(textual_match),
                "telegram_auto_funded_10_usd_match_count": len(dogfood_fingerprint),
                "active_product_project_count": len(active),
            },
            sort_keys=True,
        )
    )
    return None, None, None


async def _run() -> int:
    store = get_runtime_store()
    project, product, target_selector = _find_target_project()
    if project is None or product is None or target_selector is None:
        return 2

    try:
        icp_result = icp_service.get(product.id)
    except KeyError:
        print(
            json.dumps(
                {
                    "status": "MISSING_ICP",
                    "target_product_name": TARGET_PRODUCT_NAME,
                    "project_id": str(project.get("id")),
                    "product_id": str(product.id),
                },
                sort_keys=True,
            )
        )
        return 3

    top_icps = list(icp_result.icps[:1])
    if not top_icps:
        print(
            json.dumps(
                {
                    "status": "EMPTY_ICP",
                    "target_product_name": TARGET_PRODUCT_NAME,
                    "project_id": str(project.get("id")),
                    "product_id": str(product.id),
                },
                sort_keys=True,
            )
        )
        return 4

    existing_map = _safe_existing_map(store, product.id)

    intake_state = product_intake_service.get_state(product.id)
    effective_preferences = customer_channel_service._preferences(project)
    effective_publisher_modes = customer_channel_service._publisher_modes(project)
    autonomous_platforms = customer_channel_service.autonomous_platforms(project)
    project_uuid = UUID(str(project.get("id")))
    paid_funding_cents = sum(
        int(item.get("amount_cents") or 0)
        for item in store.list_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE)
        if isinstance(item, dict)
        and str(item.get("project_id") or "") == str(project.get("id"))
        and item.get("state") == "PAID"
    )
    preview = project.get("preview")
    preview = preview if isinstance(preview, dict) else {}
    questions = [
        {
            "field_name": str(getattr(item, "field_name", "") or ""),
            "question": str(getattr(item, "question", "") or "")[:500],
            "priority": getattr(item, "priority", None),
        }
        for item in getattr(intake_state, "questions", []) or []
    ]
    print(
        json.dumps(
            {
                "status": "STATE_DIAGNOSTIC_COMPLETED",
                "read_only": True,
                "target_product_name": TARGET_PRODUCT_NAME,
                "target_selector": target_selector,
                "resolved_product_name": str(product.name or ""),
                "project_id": str(project.get("id")),
                "product_id": str(product.id),
                "project_status": str(project.get("status") or ""),
                "project_research_state": str(project.get("research_state") or ""),
                "understanding_confirmed": bool(project.get("understanding_confirmed")),
                "launch_unlocked": bool(project.get("launch_unlocked")),
                "product_status": str(getattr(product.status, "value", product.status)),
                "product_reference_link_count": len(product.reference_links or []),
                "research_question_count": len(questions),
                "research_questions": questions,
                "preview_free_research_status": str(
                    preview.get("free_research_status") or ""
                ),
                "channel_preferences": (
                    project.get("channel_preferences")
                    if isinstance(project.get("channel_preferences"), dict)
                    else {}
                ),
                "channel_publisher_modes": (
                    project.get("channel_publisher_modes")
                    if isinstance(project.get("channel_publisher_modes"), dict)
                    else {}
                ),
                "autopilot_spend_confirmed": bool(
                    project.get("autopilot_spend_confirmed")
                ),
                "autopilot_target_max_cac": project.get("autopilot_target_max_cac"),
                "autopilot_pause_reason": project.get("autopilot_pause_reason"),
                "paid_growth_balance_usd": round(paid_funding_cents / 100, 2),
                "effective_channel_preferences": {
                    platform.value: mode
                    for platform, mode in effective_preferences.items()
                },
                "effective_telegram_publisher_mode": (
                    effective_publisher_modes[DistributionPlatform.TELEGRAM].value
                ),
                "autonomous_platforms": [
                    platform.value for platform in autonomous_platforms
                ],
                "telegram_client_connected": (
                    customer_telegram_client_publish_service.is_connected(project_uuid)
                ),
                "telegram_publish_readiness_blocker": (
                    customer_telegram_client_publish_service.readiness_blocker()
                ),
                "existing_map": existing_map,
            },
            sort_keys=True,
        )
    )
    return 0

    # Post-PR-397 production rerun.
    # Intentionally execute the same Telegram discovery stages one-by-one.
    # This remains read-only but makes stage failures explicit instead of
    # collapsing them into one top-level exception.
    provider = get_search_provider()
    adapter = TelegramDiscoveryAdapter()
    engine = AudienceIntelligenceEngine(
        provider,
        max_concurrency=1,
        adapters=[adapter],
    )
    icp = top_icps[0]
    requests = adapter.build_requests(product, icp)[:1]
    attempts: list[dict] = []
    opportunity_map: dict = {}
    stage_errors: list[dict] = []

    for request_index, request in enumerate(requests, start=1):
        try:
            hits = await provider.search(request.discovery_query, limit=5)
        except Exception as exc:
            attempts.append(
                {
                    "request_index": request_index,
                    "kind": request.kind.value,
                    "search_hits": 0,
                    "normalized_candidates": 0,
                    "enriched_candidates": 0,
                }
            )
            stage_errors.append(
                {
                    "request_index": request_index,
                    "stage": "search",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }
            )
            continue

        try:
            candidates = adapter.candidates(request, hits)
        except Exception as exc:
            attempts.append(
                {
                    "request_index": request_index,
                    "kind": request.kind.value,
                    "search_hits": len(hits),
                    "normalized_candidates": 0,
                    "enriched_candidates": 0,
                }
            )
            stage_errors.append(
                {
                    "request_index": request_index,
                    "stage": "normalize",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }
            )
            continue

        normalized_count = len(candidates)
        try:
            enriched = await adapter.enrich_candidates(request, candidates)
        except Exception as exc:
            enriched = candidates
            stage_errors.append(
                {
                    "request_index": request_index,
                    "stage": "native_enrichment",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }
            )

        attempts.append(
            {
                "request_index": request_index,
                "kind": request.kind.value,
                "search_hits": len(hits),
                "normalized_candidates": normalized_count,
                "enriched_candidates": len(enriched),
            }
        )

        for candidate_index, candidate in enumerate(enriched, start=1):
            try:
                seed = engine._candidate_to_seed(icp, candidate)
            except Exception as exc:
                stage_errors.append(
                    {
                        "request_index": request_index,
                        "candidate_index": candidate_index,
                        "stage": "score_and_seed",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:240],
                    }
                )
                continue
            try:
                engine._merge(opportunity_map, seed)
            except Exception as exc:
                stage_errors.append(
                    {
                        "request_index": request_index,
                        "candidate_index": candidate_index,
                        "stage": "merge",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:240],
                    }
                )

    seeds = sorted(
        opportunity_map.values(),
        key=lambda item: (-(item.relevance_score or 0), item.canonical_key),
    )[:10]
    by_platform = Counter(item.platform.value for item in seeds)
    sample_targets = [
        {
            "platform": item.platform.value,
            "title": item.title,
            "url": str(item.url),
            "relevance_score": item.relevance_score,
            "native_research_status": str(
                item.metadata.get("native_research_status") or ""
            ),
            "telegram_entity_id_present": bool(
                item.metadata.get("telegram_entity_id")
            ),
            "action_target_url": (
                str(item.metadata.get("action_target_url"))
                if item.metadata.get("action_target_url")
                else None
            ),
            "action_target_specific": bool(
                item.metadata.get("action_target_specific")
            ),
            "surface_capabilities": item.metadata.get("surface_capabilities") or {},
        }
        for item in seeds[:8]
    ]

    result = {
        "status": "COMPLETED",
        "read_only": True,
        "target_product_name": TARGET_PRODUCT_NAME,
        "target_selector": target_selector,
        "resolved_product_name": str(product.name or ""),
        "project_id": str(project.get("id")),
        "product_id": str(product.id),
        "project_status": str(project.get("status") or ""),
        "project_research_state": str(project.get("research_state") or ""),
        "launch_unlocked": bool(project.get("launch_unlocked")),
        "understanding_confirmed": bool(project.get("understanding_confirmed")),
        "icp_count_used": len(top_icps),
        "existing_map": existing_map,
        "diagnostic_rerun": {
            "query_count": len(requests),
            "attempts": attempts,
            "opportunity_count": len(seeds),
            "opportunities_by_platform": dict(sorted(by_platform.items())),
            "stage_errors": stage_errors,
            "sample_targets": sample_targets,
        },
    }
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:
        frames = [
            {
                "file": frame.filename.rsplit("/", 1)[-1],
                "function": frame.name,
                "line": frame.lineno,
            }
            for frame in traceback.extract_tb(exc.__traceback__)[-8:]
        ]
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "read_only": True,
                    "target_product_name": TARGET_PRODUCT_NAME,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:240],
                    "traceback": frames,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

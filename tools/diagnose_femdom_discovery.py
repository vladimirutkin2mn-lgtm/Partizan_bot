from __future__ import annotations

import asyncio
import json
from collections import Counter
from uuid import UUID

from app.audience_intelligence import AudienceIntelligenceEngine
from app.audience_intelligence_service import (
    AUDIENCE_MAP_NAMESPACE,
    audience_intelligence_service,
)
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.icp_service import icp_service
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


def _find_target_project() -> tuple[dict, object] | tuple[None, None]:
    store = get_runtime_store()
    matches: list[tuple[dict, object]] = []
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
        if str(product.name or "").strip().casefold() == TARGET_PRODUCT_NAME.casefold():
            matches.append((project, product))

    if len(matches) == 1:
        return matches[0]

    print(
        json.dumps(
            {
                "status": "TARGET_NOT_UNIQUE",
                "target_product_name": TARGET_PRODUCT_NAME,
                "match_count": len(matches),
                "project_ids": sorted(str(item[0].get("id")) for item in matches),
            },
            sort_keys=True,
        )
    )
    return None, None


async def _run() -> int:
    store = get_runtime_store()
    project, product = _find_target_project()
    if project is None or product is None:
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

    top_icps = list(icp_result.icps[:3])
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

    # Intentionally instantiate the engine directly instead of calling
    # audience_intelligence_service.discover(). This performs the same provider,
    # query-building, normalization and native-enrichment path, but does NOT
    # persist a new audience map/opportunities and cannot itself create a play
    # or publish anything.
    engine = AudienceIntelligenceEngine(get_search_provider())
    seeds = await engine.discover(product=product, icps=top_icps)

    diagnostics = audience_intelligence_service._diagnostics(engine)
    by_platform = Counter(item.platform.value for item in seeds)
    failures = sorted(
        {
            (
                failure.platform.value,
                failure.error_type,
                str(failure.message).split(":", 1)[0],
            )
            for failure in engine.last_failures
        }
    )
    sample_targets = [
        {
            "platform": item.platform.value,
            "title": item.title,
            "url": item.url,
            "relevance_score": item.relevance_score,
        }
        for item in seeds[:8]
    ]

    result = {
        "status": "COMPLETED",
        "read_only": True,
        "target_product_name": TARGET_PRODUCT_NAME,
        "project_id": str(project.get("id")),
        "product_id": str(product.id),
        "project_status": str(project.get("status") or ""),
        "project_research_state": str(project.get("research_state") or ""),
        "launch_unlocked": bool(project.get("launch_unlocked")),
        "understanding_confirmed": bool(project.get("understanding_confirmed")),
        "icp_count_used": len(top_icps),
        "existing_map": existing_map,
        "diagnostic_rerun": {
            "opportunity_count": len(seeds),
            "opportunities_by_platform": dict(sorted(by_platform.items())),
            "diagnostics": diagnostics,
            "provider_failures": [
                {
                    "platform": platform,
                    "error_type": error_type,
                    "stage": stage,
                }
                for platform, error_type, stage in failures
            ],
            "sample_targets": sample_targets,
        },
    }
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "read_only": True,
                    "target_product_name": TARGET_PRODUCT_NAME,
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

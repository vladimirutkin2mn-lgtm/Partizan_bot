"""One-shot model comparison on nine SAVED FemDom Telegram audience snapshots.

No Telegram reads, no discovery, no publisher writes or profile changes.
Output is research-only judgments; no audience gets silently approved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.telegram_audience_semantic_fit import TelegramAudienceSemanticFit
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle

_PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
_HANDLES = frozenset((
    "znakomstvachannel", "poiskfanfikov", "play_hard_community",
    "findom_gospoja0", "a_sfera", "fem_tut", "moscowellebdsm1",
    "poiskrabovirabin", "bizarre_afisha",
))


async def review_stored_audiences(
    *, store, product, icp, judge=None, v2_calibration: bool = False,
    cache_only: bool = False,
) -> dict:
    registry = TelegramCommunityRegistry(store)
    rows = registry.list_for_product(_PRODUCT)
    observed = {
        _public_handle(registry.hydrate_metadata(row).get("handle") or row.get("url")): row
        for row in rows
    }
    if set(observed) != _HANDLES:
        raise ValueError("Saved FemDom inventory does not match the approved nine peers")
    service = TelegramAudienceSemanticFit(
        store=store, enabled=True, judge=judge, daily_max=9,
        femdom_v2_calibration_once=v2_calibration,
    )
    results = []
    for handle in sorted(_HANDLES):
        row = observed[handle]
        metadata = registry.hydrate_metadata(row)
        verdict = await service.evaluate(
            product=product, icp=icp, handle=handle,
            title=str(row.get("title") or ""), metadata=metadata,
            cache_only=cache_only,
        )
        results.append({
            "handle": handle,
            "baseline_fit_confirmed": (row.get("audience_fit") or {}).get("confirmed")
            is True,
            "model_status": verdict.get("status"),
            "verdict": verdict.get("verdict"),
            "confidence": verdict.get("confidence"),
            "rationale": verdict.get("rationale"),
            "evidence": verdict.get("evidence"),
            "cache_hit": verdict.get("cache_hit"),
            "input_tokens": verdict.get("input_tokens", 0),
            "output_tokens": verdict.get("output_tokens", 0),
            "promoted_to_publisher": False,
        })
    return {
        "status": "SAVED_AUDIENCE_SEMANTIC_PILOT_COMPLETE",
        "mode": "NO_TELEGRAM_READS_NO_PUBLISHING",
        "model": service.model,
        "v2_calibration": v2_calibration,
        "cache_only": cache_only,
        "cached_judgments": sum(x["model_status"] == "CACHED" for x in results),
        "missing_cache_count": sum(x["model_status"] == "CACHE_MISS" for x in results),
        "total_peers": len(results),
        "total_new_judgments": sum(x["model_status"] == "EVALUATED" for x in results),
        "direct_proposals": sum(x["verdict"] == "DIRECT" for x in results),
        "adjacent_proposals": sum(x["verdict"] == "ADJACENT" for x in results),
        "input_tokens": sum(x["input_tokens"] or 0 for x in results),
        "output_tokens": sum(x["output_tokens"] or 0 for x in results),
        "publication_actions": 0,
        "telegram_api_calls": 0,
        "results": results,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--product-id", type=UUID, required=True)
    p.add_argument("--v2-calibration-once", action="store_true")
    p.add_argument("--cache-only", action="store_true")
    args = p.parse_args()
    if args.product_id != _PRODUCT:
        raise ValueError("Pilot is limited to the saved FemDom product")
    result = asyncio.run(review_stored_audiences(
        store=get_runtime_store(),
        product=product_intake_service.get_product(_PRODUCT),
        icp=icp_service.get(_PRODUCT).icps[0],
        v2_calibration=args.v2_calibration_once,
        cache_only=args.cache_only,
    ))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

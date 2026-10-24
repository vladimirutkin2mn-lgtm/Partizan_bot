"""Read-only probe: distinguish cached semantic verdict from durable direct approval."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.telegram_audience_semantic_fit import (
    AUDIENCE_FIT_CACHE_NAMESPACE,
    _fingerprint,
)
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle

PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
product = product_intake_service.get_product(PRODUCT)
icp = icp_service.get(PRODUCT).icps[0]
store = get_runtime_store()
registry = TelegramCommunityRegistry(store)
rows = []
for record in registry.list_for_product(PRODUCT):
    meta = registry.hydrate_metadata(record)
    handle = _public_handle(meta.get("handle") or record.get("url"))
    if not handle:
        continue
    entry = store.get(AUDIENCE_FIT_CACHE_NAMESPACE, f"{PRODUCT}:{handle}") or {}
    expected = _fingerprint(
        product, icp, title=str(record.get("title") or ""),
        about=str(meta.get("about") or ""),
    )
    checked = entry.get("checked_at")
    try:
        parsed = datetime.fromisoformat(str(checked).replace("Z", "+00:00"))
        age_ok = parsed.tzinfo is not None and timedelta(0) <= (
            datetime.now(UTC) - parsed.astimezone(UTC)
        ) <= timedelta(days=60)
    except (ValueError, TypeError):
        age_ok = False
    rows.append({
        "handle": handle,
        "registry_confirmed": (record.get("audience_fit") or {}).get("confirmed") is True,
        "cached_verdict": entry.get("verdict"),
        "cached_direct_fit": entry.get("direct_fit"),
        "cached_confidence": entry.get("confidence"),
        "cache_fingerprint_current": bool(entry) and expected == entry.get("fingerprint"),
        "cache_recent": age_ok,
        "native_verified": str(meta.get("native_research_status") or "").upper() == "VERIFIED",
    })

print(json.dumps({
    "status": "CACHE_ONLY_DIRECT_FIT_INSPECTION",
    "rows": sorted(rows, key=lambda item: item["handle"]),
    "new_llm_calls": 0,
    "telegram_calls": 0,
    "state_writes": 0,
}, ensure_ascii=False, sort_keys=True))

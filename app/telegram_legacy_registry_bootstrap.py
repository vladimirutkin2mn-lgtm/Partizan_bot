"""Safely reuse historical Telegram research without buying the same audience twice.

Legacy AudienceDistributionMaps predate the canonical TelegramCommunityRegistry.
This bootstrap stores only public handles as RESEARCH_ONLY leads. It never
inherits old relevance scores as confirmed native fitness or publication rights.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.runtime_store import RuntimeStateStore
from app.telegram_community_registry import TelegramCommunityRegistry

_LEGACY_MAP_NAMESPACE = "audience_distribution_map"
_PUBLIC_HANDLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_MAX_STAGED_PER_PRODUCT = 50


def _public_handle(value: object) -> str | None:
    raw = str(value or "").strip().lstrip("@")
    if raw.startswith(("https://", "http://")):
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return None
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").casefold() not in {
                "t.me", "telegram.me", "www.t.me", "www.telegram.me",
            }
            or parsed.username
            or parsed.password
        ):
            return None
        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments and segments[0].casefold() == "s":
            segments = segments[1:]
        raw = segments[0] if segments else ""
    return raw.casefold() if _PUBLIC_HANDLE.fullmatch(raw) else None


def stage_legacy_telegram_communities(
    store: RuntimeStateStore, product_id: object
) -> dict:
    """Idempotent, strictly non-publishing bootstrap from historical research.

    Existing registry entries (and their native/fit metadata) are never
    overwritten. A legacy lead can become audience-confirmed only after
    subsequent native verification by the existing refresh adapter.
    """
    registry = TelegramCommunityRegistry(store)
    present = {
        handle
        for row in registry.list_for_product(product_id)
        if (
            handle := _public_handle(
                registry.hydrate_metadata(row).get("handle") or row.get("url")
            )
        )
    }
    original = store.get(_LEGACY_MAP_NAMESPACE, str(product_id)) or {}
    opportunities = original.get("opportunities") or []
    found = set()
    summary = {
        "historical_records": 0,
        "staged_research_only": 0,
        "already_registered": 0,
        "skipped_nonpublic": 0,
        "skipped_not_telegram": 0,
        "skipped_limit": 0,
    }
    for opportunity in opportunities:
        if not isinstance(opportunity, dict):
            continue
        if str(opportunity.get("platform") or "").upper() != "TELEGRAM":
            summary["skipped_not_telegram"] += 1
            continue
        summary["historical_records"] += 1
        metadata = (
            opportunity.get("metadata")
            if isinstance(opportunity.get("metadata"), dict)
            else {}
        )
        if str(metadata.get("access_state") or "").upper() == "JOIN_REQUIRED":
            summary["skipped_nonpublic"] += 1
            continue
        handle = _public_handle(metadata.get("handle") or opportunity.get("url"))
        if not handle or handle.endswith("bot"):
            summary["skipped_nonpublic"] += 1
            continue
        if handle in found:
            continue
        found.add(handle)
        if handle in present:
            summary["already_registered"] += 1
            continue
        if summary["staged_research_only"] >= _MAX_STAGED_PER_PRODUCT:
            summary["skipped_limit"] += 1
            continue
        raw_kind = str(opportunity.get("kind") or "").upper()
        kind = raw_kind if raw_kind in {"GROUP", "CHANNEL"} else "CHANNEL"
        registry.upsert(
            product_id=product_id,
            canonical_key=f"telegram:{handle}:community",
            title=str(opportunity.get("title") or handle)[:300],
            url=f"https://t.me/{handle}",
            kind=kind,
            metadata={
                "handle": handle,
                "access_state": "PUBLIC",
                "legacy_audience_map_lead": True,
                "source_type": "LEGACY_AUDIENCE_MAP",
                "native_research_status": "UNVERIFIED",
                "registry_confirmed": False,
                "community_fit_pass": False,
                "actionability": "RESEARCH_ONLY",
                "action_target_url": None,
                "action_target_specific": False,
                "community_only_reason": "LEGACY_AUDIENCE_AWAITING_NATIVE_FIT",
                "opportunity_scope": "COMMUNITY",
            },
        )
        present.add(handle)
        summary["staged_research_only"] += 1
    return summary

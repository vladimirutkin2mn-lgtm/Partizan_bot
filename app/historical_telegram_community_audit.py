"""Read-only historical Telegram community audit (registry + legacy audience map).

Designed for a single, bounded production dogfood trace. No paid discovery,
Telegram contacts.Search, join, write, publishing or runtime state writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from app.audience_intelligence_service import AUDIENCE_MAP_NAMESPACE
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_participation_target import select_telegram_participation_target
from app.telegram_research import (
    TelegramFloodWaitError,
    get_telegram_research_connector,
)

_PUBLIC_HANDLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_POST_FRESHNESS = timedelta(days=7)
_MAX_INSPECT = 12
_MAX_POSTS_PER_GROUP = 8
_STOPWORDS = {
    "this", "that", "from", "with", "they", "their", "about", "people",
    "person", "social", "online", "product", "looking", "interested",
    "users", "find", "want", "dating", "community", "group", "telegram",
    "the", "for", "app", "new", "and", "who", "have", "into",
    "для", "или", "этом", "есть", "где", "кто", "через", "здесь",
    "люди", "группы", "найти", "сайт", "сервис", "общение",
}


def _utc(value: object) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _handle(raw: object) -> str | None:
    value = str(raw or "").strip().lstrip("@")
    if value.startswith(("https://", "http://")):
        try:
            parts = urlsplit(value)
        except ValueError:
            return None
        if parts.scheme != "https" or (parts.hostname or "").casefold() not in {
            "t.me", "telegram.me", "www.t.me", "www.telegram.me",
        } or parts.username or parts.password:
            return None
        pieces = [part for part in parts.path.split("/") if part]
        if pieces and pieces[0].casefold() == "s":
            pieces = pieces[1:]
        value = pieces[0] if pieces else ""
    if not _PUBLIC_HANDLE.fullmatch(value):
        return None
    return value.casefold()


def historical_communities(store: RuntimeStateStore, product_id: UUID) -> tuple[list[dict], dict]:
    """Read both historical stores; never replace or seed the canonical registry."""
    rows: dict[str, dict] = {}
    counters = Counter()
    for entry in TelegramCommunityRegistry(store).list_for_product(product_id):
        meta = TelegramCommunityRegistry(store).hydrate_metadata(entry)
        raw = meta.get("handle") or entry.get("url")
        handle = _handle(raw)
        counters["registry_rows"] += 1
        if handle is None:
            counters["registry_nonpublic_or_missing"] += 1
            continue
        fit = entry.get("audience_fit") or {}
        row = rows.setdefault(
            handle,
            {"handle": handle, "sources": [], "confirmed_fit": False,
             "historical_score": 0.0, "last_stored_check": None},
        )
        if "canonical_registry" not in row["sources"]:
            row["sources"].append("canonical_registry")
        row["confirmed_fit"] |= fit.get("confirmed") is True or meta.get(
            "community_fit_pass"
        ) is True
        row["last_stored_check"] = str(meta.get("source_checked_at") or "") or None

    stored_map = store.get(AUDIENCE_MAP_NAMESPACE, str(product_id)) or {}
    for entry in stored_map.get("opportunities") or []:
        if not isinstance(entry, dict) or str(entry.get("platform") or "").upper() != "TELEGRAM":
            continue
        counters["historical_audience_rows"] += 1
        meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        handle = _handle(meta.get("handle") or entry.get("url"))
        if handle is None:
            counters["historical_nonpublic_or_missing"] += 1
            continue
        row = rows.setdefault(
            handle,
            {"handle": handle, "sources": [], "confirmed_fit": False,
             "historical_score": 0.0, "last_stored_check": None},
        )
        if "historical_audience_map" not in row["sources"]:
            row["sources"].append("historical_audience_map")
        row["confirmed_fit"] |= meta.get("community_fit_pass") is True
        try:
            score = float(entry.get("relevance_score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        row["historical_score"] = max(row["historical_score"], score)
        if not row["last_stored_check"]:
            row["last_stored_check"] = str(meta.get("source_checked_at") or "") or None

    ordered = sorted(
        rows.values(),
        key=lambda x: (
            -int(x["confirmed_fit"]),
            -int("historical_audience_map" in x["sources"]),
            -float(x["historical_score"]),
            x["handle"],
        ),
    )
    return ordered, {
        **dict(counters),
        "unique_public_communities": len(ordered),
        "present_in_both": sum(len(x["sources"]) == 2 for x in ordered),
        "registry_only": sum(x["sources"] == ["canonical_registry"] for x in ordered),
        "historical_only": sum(x["sources"] == ["historical_audience_map"] for x in ordered),
        "confirmed_fit_count": sum(x["confirmed_fit"] for x in ordered),
    }


def _tokens(text: str) -> set[str]:
    return {
        item
        for item in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{4,}", str(text or "").casefold())
        if item not in _STOPWORDS
    }


def _anchors(product: object) -> set[str]:
    texts = [
        str(getattr(product, "name", "") or ""),
        str(getattr(product, "problem_or_desire", "") or ""),
        str(getattr(product, "value_proposition", "") or ""),
        *[str(v) for v in getattr(product, "known_audience", []) or []],
    ]
    return _tokens(" ".join(texts))


def _check_context(snapshot, *, anchors: set[str], now: datetime) -> dict:
    """Compare the actual latest selected post against fresh, product-related posts.

    Keyword overlap is a screening signal, NOT semantic acceptance or verified
    Telegram write access. Never falsely claim messages are publishable.
    """
    fresh = []
    for message in snapshot.recent_context[:_MAX_POSTS_PER_GROUP]:
        published = _utc(message.published_at)
        if published is None or not timedelta(0) <= now - published <= _POST_FRESHNESS:
            continue
        overlap = sorted(anchors & _tokens(message.text))[:12]
        fresh.append(
            {
                "url": str(message.url or ""),
                "published_at": published.isoformat(),
                "matched_product_terms": overlap,
                "keyword_match": bool(overlap),
                "excerpt": " ".join(str(message.text or "").split())[:180],
            }
        )
    selected = select_telegram_participation_target(snapshot)
    target = next((item for item in fresh if item["url"] == selected.target_url), None)
    return {
        "status": "INSPECTED",
        "title": str(snapshot.title or "")[:140],
        "kind": str(snapshot.kind.value),
        "post_samples_read": len(snapshot.recent_context),
        "fresh_post_count": len(fresh),
        "fresh_keyword_match_count": sum(p["keyword_match"] for p in fresh),
        "fresh_posts": fresh,
        "selected_target_url": selected.target_url,
        "selected_target_mode": selected.mode.value if selected.mode else None,
        "selected_target_is_fresh": target is not None,
        "selected_target_keyword_match": bool(target and target["keyword_match"]),
        "selected_target_actionability": selected.actionability,
        "selected_target_reason": selected.reason,
        "live_write_access_verified": False,
        "publication_authorized": False,
    }


async def audit(
    product_id: UUID,
    *,
    store: RuntimeStateStore,
    connector: object | None,
    product: object,
    max_groups: int = 12,
    now: datetime | None = None,
) -> dict:
    """Bounded known-public-peer inspection, no contacts.Search or writes."""
    if not 0 <= max_groups <= _MAX_INSPECT:
        raise ValueError("Live inspection limit must be 0..12")
    checked = now or datetime.now(UTC)
    checked = checked.replace(tzinfo=UTC) if checked.tzinfo is None else checked.astimezone(UTC)
    communities, inventory = historical_communities(store, product_id)
    selected = communities[:max_groups]
    anchor_terms = _anchors(product)
    rows = [
        {
            **item, "inspection": {
                "status": "NOT_CHECKED", "reason": "READ_ONLY_CONNECTOR_UNAVAILABLE",
            }
        }
        for item in selected
    ]
    blocked = []
    for entry in store.list_namespace("telegram_research_flood_wait"):
        wait = _utc(entry.get("blocked_until"))
        if wait is not None and wait > checked:
            blocked.append(wait)
    blocked_until = max(blocked) if blocked else None

    if max_groups == 0:
        mode = "INVENTORY_ONLY"
    elif blocked_until is not None:
        mode = "DEFERRED_FLOOD_WAIT"
    elif connector is None or not callable(getattr(connector, "inspect_known", None)):
        mode = "RESEARCH_CONNECTOR_UNAVAILABLE"
    elif not anchor_terms:
        mode = "MISSING_PRODUCT_ANCHORS"
    else:
        mode = "READ_ONLY_LIVE_INSPECTION"
        batch_size = max(1, min(3, int(getattr(connector, "_known_handle_limit", 3))))
        for index in range(0, len(rows), batch_size):
            batch = rows[index : index + batch_size]
            try:
                found = await connector.inspect_known([item["handle"] for item in batch])
            except TelegramFloodWaitError as exc:
                mode = "HALTED_FLOOD_WAIT"
                for remaining in rows[index:]:
                    remaining["inspection"] = {
                        "status": "NOT_CHECKED",
                        "reason": "TELEGRAM_FLOOD_WAIT",
                    }
                # Never retry during Telegram's wait and never touch the
                # production flood-wait ledger in this read-only probe.
                blocked_until = checked + timedelta(seconds=exc.seconds)
                break
            except Exception as exc:
                for remaining in batch:
                    remaining["inspection"] = {
                        "status": "RETRYABLE_FAILED", "reason": type(exc).__name__,
                    }
                continue
            by_handle = {str(v.handle).casefold(): v for v in found}
            for item in batch:
                observed = by_handle.get(item["handle"])
                if observed is None or observed.snapshot is None:
                    item["inspection"] = {
                        "status": "NOT_VERIFIED",
                        "reason": str(getattr(observed, "status", "NO_RESPONSE")),
                    }
                    continue
                item["inspection"] = _check_context(
                    observed.snapshot, anchors=anchor_terms, now=checked
                )
    return {
        "status": "HISTORICAL_COMMUNITY_AUDIT_COMPLETE",
        "read_only": True,
        "product_id": str(product_id),
        "checked_at": checked.isoformat(),
        "mode": mode,
        "blocking_flood_wait_until": blocked_until.isoformat() if blocked_until else None,
        "inventory": inventory,
        "summary": {
            "selected_public_communities": len(rows),
            "actually_inspected": sum(
                x["inspection"]["status"] == "INSPECTED" for x in rows
            ),
            "fresh_posts_observed": sum(
                x["inspection"].get("fresh_post_count", 0) for x in rows
            ),
            "fresh_keyword_matches": sum(
                x["inspection"].get("fresh_keyword_match_count", 0) for x in rows
            ),
            "selected_latest_posts_keyword_matching": sum(
                x["inspection"].get("selected_target_keyword_match") is True for x in rows
            ),
            "publishable_without_separate_checks": 0,
        },
        "product_keywords_for_diagnostic_only": sorted(anchor_terms)[:30],
        "communities": rows,
        "effects": {
            "new_paid_search_calls": 0,
            "new_contact_search_calls": 0,
            "write_actions": 0,
            "publications": 0,
            "state_writes": 0,
        },
        "limitations": [
            "Inspects a bounded sample from the complete saved inventory, not every historical community.",
            "Keyword overlap does not prove semantic relevance or publication access.",
            "Only the most recent native posts returned by the research transport are sampled.",
            "Unverified historical metadata is never treated as a fresh inspection.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit saved FemDom public communities read-only")
    parser.add_argument("--product-id", required=True, type=UUID)
    parser.add_argument("--max-groups", type=int, default=12)
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    try:
        from app.product_intake import product_intake_service

        store = get_runtime_store()
        product = product_intake_service.get_product(args.product_id)
        connector = (
            None if args.inventory_only else get_telegram_research_connector()
        )
        result = asyncio.run(
            audit(
                args.product_id, store=store, connector=connector,
                product=product, max_groups=0 if args.inventory_only else args.max_groups,
            )
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (KeyError, TypeError, ValueError) as exc:
        print(json.dumps({
            "status": "HISTORICAL_COMMUNITY_AUDIT_FAILED",
            "error_type": type(exc).__name__,
        }))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

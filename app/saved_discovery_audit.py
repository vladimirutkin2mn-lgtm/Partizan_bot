"""Offline replay of saved operational opportunities; never a send approval.

Use the same CustomerLiveOpportunityService freshness logic as customer
Activity. Historical relevance scores are *not* a publication preflight.
No store writes, Telegram calls, model requests, network or profile mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from app.customer_live_opportunities import CustomerLiveOpportunityService
from app.runtime_store import MemoryRuntimeStateStore

MAX_INPUT_BYTES = 1024 * 1024
MAX_ROWS = 200
_ALLOWED_HOSTS = {"t.me", "telegram.me"}


def _datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _telegram_target(value: object) -> tuple[str | None, str | None]:
    """Normalize a Telegram public post URL without resolving access."""
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None, None
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in _ALLOWED_HOSTS:
        return None, None
    if parsed.username or parsed.password or parsed.port:
        return None, None
    path = [piece for piece in parsed.path.split("/") if piece]
    if len(path) != 2 or not path[1].isdigit():
        return None, None
    handle = path[0]
    if not (5 <= len(handle) <= 32) or not all(
        ch.isascii() and (ch.isalnum() or ch == "_") for ch in handle
    ):
        return None, None
    post_id = int(path[1])
    if post_id <= 0:
        return None, None
    canonical = f"https://t.me/{handle.casefold()}/{post_id}"
    return canonical, f"telegram:{handle.casefold()}"


def audit_saved_opportunities(rows: list[dict], *, as_of: datetime) -> dict[str, Any]:
    """Classify recorded leads, not claim to verify current Telegram content."""
    if len(rows) > MAX_ROWS or not all(isinstance(item, dict) for item in rows):
        raise ValueError("Expected at most 200 saved opportunity objects")
    if as_of.tzinfo is None:
        raise ValueError("Audit clock must contain a timezone")
    checked = as_of.astimezone(UTC)
    viewer = CustomerLiveOpportunityService(store=MemoryRuntimeStateStore())
    results = []
    seen_posts = set()
    communities = set()
    for index, item in enumerate(rows):
        url, community = _telegram_target(item.get("url"))
        reasons = []
        if url is None:
            reasons.append("INVALID_OR_NONPUBLIC_TARGET")
        elif url in seen_posts:
            reasons.append("DUPLICATE_POST")
        else:
            seen_posts.add(url)
            communities.add(community)
        if str(item.get("platform") or "").upper() != "TELEGRAM":
            reasons.append("NOT_TELEGRAM")
        discovered = _datetime(item.get("discovered_at"))
        expiry = _datetime(item.get("expires_at"))
        if discovered is None:
            reasons.append("DISCOVERY_TIME_UNVERIFIED")
        if expiry is None:
            reasons.append("EXPIRY_UNVERIFIED")
        elif expiry <= checked:
            reasons.append("EXPIRED")
        if str(item.get("status") or "").upper() != "ACTIVE":
            reasons.append("INACTIVE_RECORD")

        # Real application classification; this audit never uses discovery
        # timestamp as proof that the underlying post was recently published.
        freshness = "UNKNOWN"
        if discovered is not None and url is not None:
            value = viewer._view(
                {
                    **item,
                    "url": url,
                    "opportunity_id": hashlib.sha256(url.encode("utf-8")).hexdigest()[:20],
                    "discovered_at": discovered.isoformat(),
                    "expires_at": expiry.isoformat() if expiry is not None else None,
                },
                now=checked,
            )
            freshness = value.freshness

        if str(item.get("publishability") or "").upper() != "READY":
            reasons.append("PUBLISHER_PREFLIGHT_NOT_READY")

        # Even READY in an old file does not verify publisher rights *now*.
        # An online, account-specific, live preflight and approval is required.
        if not reasons:
            reasons.append("REQUIRES_LIVE_REVALIDATION")

        results.append(
            {
                "index": index,
                "title": str(item.get("title") or "")[:200],
                "url": url,
                "community_key": community,
                "relevance_score": item.get("relevance_score"),
                "freshness": freshness,
                "expires_at": expiry.isoformat() if expiry else None,
                "stored_publishability": str(item.get("publishability") or "UNKNOWN"),
                "rejection_reasons": reasons,
                "currently_publishable": False,
                "usable_as_historical_search_evidence": url is not None,
            }
        )

    reasons = Counter(reason for item in results for reason in item["rejection_reasons"])
    return {
        "status": "OFFLINE_AUDIT_COMPLETE",
        "read_only": True,
        "as_of": checked.isoformat(),
        "summary": {
            "saved_post_count": len(results),
            "distinct_public_post_count": len(seen_posts),
            "distinct_community_count": len(communities),
            "expired_post_count": sum(
                "EXPIRED" in item["rejection_reasons"] for item in results
            ),
            "publishable_without_live_checks": 0,
            "reason_counts": dict(sorted(reasons.items())),
        },
        "opportunities": results,
        "effects": {
            "network_calls": 0,
            "telegram_calls": 0,
            "paid_calls": 0,
            "state_writes": 0,
            "publications": 0,
        },
        "limitations": [
            "Saved posts prove only that these targets were previously collected.",
            "No current Telegram content, membership, write permission, or linked discussion was checked.",
            "The persisted expires_at is an operator-supplied research deadline, not a verified post timestamp.",
            "Saved relevance does not prove conversion or publication eligibility.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only saved opportunity freshness audit")
    parser.add_argument("file", type=Path)
    parser.add_argument("--as-of", help="ISO UTC datetime; default current UTC")
    args = parser.parse_args()
    try:
        if args.file.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("Saved opportunities file exceeds the one-megabyte limit")
        payload = json.loads(args.file.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Expected a list of saved opportunities")
        clock = _datetime(args.as_of) if args.as_of else datetime.now(UTC)
        if clock is None:
            raise ValueError("Invalid or timezone-naive --as-of")
        result = audit_saved_opportunities(payload, as_of=clock)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "AUDIT_FAILED", "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

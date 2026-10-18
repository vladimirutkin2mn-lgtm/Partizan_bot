"""Offline import of public Telegram community catalog exports.

Consumes exported community metadata only. Never executes third-party code,
opens Telegram sessions, collects user rosters, or authorizes publication.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry

CATALOG_SOURCES = (
    "telegram-groups-parser",
    "telegram-chat-scraper",
    "tgscan-export",
    "tgstat-public",
)
MAX_CATALOG_ROWS = 200
_PUBLIC_HANDLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def _public_handle(value: object) -> str:
    value = str(value or "").strip()
    if value.startswith("@"):
        value = value[1:]
    if value.startswith(("https://", "http://")):
        parts = urlsplit(value)
        if parts.netloc.casefold().removeprefix("www.") not in {
            "t.me",
            "telegram.me",
        }:
            return ""
        segments = [part for part in parts.path.split("/") if part]
        if not segments:
            return ""
        value = segments[1] if segments[0].lower() == "s" and len(segments) > 1 else segments[0]
    if not _PUBLIC_HANDLE.fullmatch(value) or value.casefold().endswith("bot"):
        return ""
    return value.casefold()


def parse_public_catalog(payload: object, *, source: str) -> list[dict]:
    """Normalize supported export shapes; never import user/member information."""
    if source not in CATALOG_SOURCES:
        raise ValueError(f"Unknown catalog source: {source}")
    if isinstance(payload, dict):
        # The first two sources export groups.json as a list and
        # verified_chats.json as {"groups": [...]} respectively.
        rows = payload.get("groups")
        if rows is None:
            rows = payload.get("results")
        if rows is None:
            rows = payload.get("chats")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("Catalog payload must contain a community list")
    if len(rows) > MAX_CATALOG_ROWS:
        raise ValueError("Too many catalog rows; split into batches of 200 or fewer")

    communities: dict[str, dict] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        handle = _public_handle(
            raw.get("username")
            or raw.get("handle")
            or raw.get("url")
            or raw.get("link")
        )
        if not handle:
            continue
        kind_hint = str(raw.get("type") or "").casefold()
        if source == "telegram-groups-parser" and kind_hint not in {
            "group", "supergroup", "megagroup", "channel",
        }:
            continue
        if source == "telegram-chat-scraper" and raw.get("is_group") is not True:
            continue
        if source in {"tgscan-export", "tgstat-public"} and kind_hint not in {
            "group", "supergroup", "megagroup", "channel",
        }:
            continue
        if raw.get("is_group") is True or kind_hint in {
            "group",
            "supergroup",
            "megagroup",
        }:
            kind = "GROUP"
        else:
            kind = "CHANNEL"
        title = " ".join(str(raw.get("title") or handle).split())[:160]
        description = " ".join(
            str(raw.get("description") or raw.get("about") or "").split()
        )[:600]
        if handle not in communities:
            communities[handle] = {
                "handle": handle,
                "url": f"https://t.me/{handle}",
                "title": title,
                "description": description,
                "kind": kind,
                "source_url": (
                    str(raw.get("source_url") or "")[:320]
                    if source == "tgstat-public"
                    and str(raw.get("source_url") or "").startswith("https://tgstat.com/")
                    else ""
                ),
            }
    return list(communities.values())


def import_public_catalog(
    *,
    store: RuntimeStateStore,
    product_id: UUID,
    source: str,
    communities: list[dict],
    commit: bool = False,
) -> dict:
    """Stage unverified community identities in the canonical registry.

    Only the Telegram native inspector may later assert VERIFIED and fit.
    Duplicate handles are merged by the existing registry alias index.
    """
    if source not in CATALOG_SOURCES:
        raise ValueError(f"Unknown catalog source: {source}")
    if len(communities) > MAX_CATALOG_ROWS:
        raise ValueError("Catalog import batch exceeds the row cap")
    if not commit:
        return {
            "mode": "DRY_RUN",
            "source": source,
            "product_id": str(product_id),
            "candidate_count": len(communities),
            "registry_mutations": 0,
        }

    registry = TelegramCommunityRegistry(store)
    imported = 0
    timestamp = datetime.now(UTC).isoformat()
    for item in communities:
        handle = _public_handle(item.get("handle"))
        if not handle:
            continue
        metadata = {
            "handle": handle,
            "source_type": "CATALOG_UNVERIFIED",
            "catalog_source": source,
            "catalog_observed_at": timestamp,
            "catalog_description": str(item.get("description") or "")[:600],
            "catalog_evidence_url": str(item.get("source_url") or "")[:320],
            "native_research_status": "NOT_CHECKED",
            "community_fit_pass": False,
            "actionability": "RESEARCH_ONLY",
            "access_state": "CATALOG_UNVERIFIED",
        }
        registry.upsert(
            product_id=product_id,
            canonical_key=f"telegram:{handle}:community",
            title=str(item.get("title") or handle)[:160],
            url=f"https://t.me/{handle}",
            kind=str(item.get("kind") or "CHANNEL"),
            metadata=metadata,
        )
        imported += 1
    return {
        "mode": "COMMITTED",
        "source": source,
        "product_id": str(product_id),
        "candidate_count": len(communities),
        "registry_mutations": imported,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage public Telegram catalog communities")
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--source", choices=CATALOG_SOURCES, required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument(
        "--commit", action="store_true", help="Persist unverified communities to registry"
    )
    args = parser.parse_args()
    if args.file.stat().st_size > 1_000_000:
        parser.error("Catalog file is too large (1 MB maximum)")
    payload = json.loads(args.file.read_text(encoding="utf-8"))
    rows = parse_public_catalog(payload, source=args.source)
    # Dry-run must not depend on production storage or secrets.
    store = get_runtime_store() if args.commit else None
    if store is None:
        from app.runtime_store import MemoryRuntimeStateStore

        store = MemoryRuntimeStateStore()
    result = import_public_catalog(
        store=store,
        product_id=args.product_id,
        source=args.source,
        communities=rows,
        commit=args.commit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

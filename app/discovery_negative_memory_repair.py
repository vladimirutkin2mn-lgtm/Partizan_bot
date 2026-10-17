from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from app.community_first_telegram_adapter import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE
from app.discovery_relevance_learning import DISCOVERY_NEGATIVE_LEARNING_NAMESPACE
from app.runtime_store import RuntimeStateStore, get_runtime_store


def _memory_key(canonical_key: str) -> str:
    normalized = str(canonical_key or "").strip().casefold()
    return f"telegram|{normalized}" if normalized else ""


def _registry_alias_keys(row: dict) -> set[str]:
    metadata = dict(row.get("metadata") or {})
    canonical_keys = {
        str(row.get("canonical_key") or "").strip(),
        str(metadata.get("community_registry_key") or "").strip(),
    }
    entity_id = metadata.get("telegram_entity_id")
    if entity_id is not None:
        canonical_keys.add(f"telegram:{entity_id}:community")
    handle = str(metadata.get("handle") or "").strip().lstrip("@").casefold()
    if handle:
        canonical_keys.add(f"telegram:{handle}:community")
    url = str(row.get("url") or "").strip()
    if url:
        parts = urlsplit(url)
        path = [segment for segment in parts.path.split("/") if segment]
        if path and not path[0].startswith("+") and path[0].casefold() != "joinchat":
            url_handle = path[1] if path[0].casefold() == "s" and len(path) > 1 else path[0]
            if url_handle:
                canonical_keys.add(f"telegram:{url_handle.casefold()}:community")
    return {_memory_key(value) for value in canonical_keys if value}


def _safe_registry_row(row: dict, product_id: UUID | str) -> bool:
    if str(row.get("product_id") or "") != str(product_id):
        return False
    metadata = dict(row.get("metadata") or {})
    if metadata.get("community_fit_pass") is not True:
        return False
    if str(metadata.get("native_research_status") or "").upper() != "VERIFIED":
        return False
    source_type = str(metadata.get("source_type") or metadata.get("surface_kind") or "").upper()
    return source_type in {"CHANNEL", "GROUP"}


def plan_discovery_negative_memory_repair(
    product_id: UUID | str,
    *,
    store: RuntimeStateStore | None = None,
) -> dict:
    runtime_store = store or get_runtime_store()
    registry_rows = [
        row
        for row in runtime_store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)
        if str(row.get("product_id") or "") == str(product_id)
    ]
    safe_rows = [row for row in registry_rows if _safe_registry_row(row, product_id)]
    skipped_rows = [row for row in registry_rows if not _safe_registry_row(row, product_id)]

    payload = runtime_store.get(DISCOVERY_NEGATIVE_LEARNING_NAMESPACE, str(product_id)) or {}
    blocked = dict(payload.get("blocked_keys") or {})
    safe_aliases: set[str] = set()
    aliases_by_registry_key: dict[str, set[str]] = {}
    for row in safe_rows:
        aliases = _registry_alias_keys(row)
        safe_aliases.update(aliases)
        aliases_by_registry_key[str(row.get("canonical_key") or "")] = aliases

    removable = sorted(key for key in blocked if key.casefold() in safe_aliases)
    matched_registry: list[dict] = []
    for canonical_key, aliases in aliases_by_registry_key.items():
        matched = sorted(key for key in removable if key.casefold() in aliases)
        if matched:
            matched_registry.append(
                {
                    "canonical_key": canonical_key,
                    "removed_memory_keys": matched,
                }
            )

    return {
        "status": "REPAIR_PLANNED",
        "product_id": str(product_id),
        "registry_count": len(registry_rows),
        "verified_registry_count": len(safe_rows),
        "skipped_registry_count": len(skipped_rows),
        "blocked_key_count_before": len(blocked),
        "removable_blocked_keys": removable,
        "matched_registry": matched_registry,
        "negative_terms_untouched": True,
        "access_restrictions_untouched": True,
        "notes": [
            (
                "Only exact Telegram negative-memory keys matching natively VERIFIED, "
                "audience-fit registry communities are eligible."
            ),
            (
                "Invite-only, unverified and non-fit registry rows are not promoted or "
                "repaired by this operation."
            ),
            (
                "Theme-level negative terms are left unchanged because historical rows do "
                "not contain enough provenance to remove their counts safely."
            ),
            (
                "Telegram permissions, NO_DISCUSSION and other access restrictions live "
                "outside this namespace and are not modified."
            ),
        ],
    }


def commit_discovery_negative_memory_repair(
    product_id: UUID | str,
    *,
    store: RuntimeStateStore | None = None,
) -> dict:
    runtime_store = store or get_runtime_store()
    plan = plan_discovery_negative_memory_repair(product_id, store=runtime_store)
    removable = set(plan["removable_blocked_keys"])
    payload = runtime_store.get(DISCOVERY_NEGATIVE_LEARNING_NAMESPACE, str(product_id))
    if not isinstance(payload, dict) or not removable:
        return {
            **plan,
            "status": "REPAIR_NOOP",
            "blocked_key_count_after": plan["blocked_key_count_before"],
            "committed": False,
        }

    blocked = dict(payload.get("blocked_keys") or {})
    for key in removable:
        blocked.pop(key, None)
    updated = dict(payload)
    updated["blocked_keys"] = blocked
    updated["repaired_at"] = datetime.now(UTC).isoformat()
    updated["repair_reason"] = "FINAL_ACCEPT_PRECEDED_NEGATIVE_LEARNING_FIX"
    updated["repair_removed_blocked_keys"] = sorted(removable)
    runtime_store.put(
        DISCOVERY_NEGATIVE_LEARNING_NAMESPACE,
        str(product_id),
        updated,
    )
    return {
        **plan,
        "status": "REPAIR_COMMITTED",
        "blocked_key_count_after": len(blocked),
        "committed": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or commit exact-key repair for poisoned Telegram discovery negative memory."
    )
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Apply the repair. Without this flag the command is read-only.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = (
        commit_discovery_negative_memory_repair(args.product_id)
        if args.commit
        else plan_discovery_negative_memory_repair(args.product_id)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

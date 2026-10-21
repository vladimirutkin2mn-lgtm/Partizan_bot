"""Read already-saved native Telegram community snapshots; never contact Telegram."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from uuid import UUID

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle

_FEMDOM_PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
_FEMDOM_PROJECT = UUID("124cdd70-49b0-48b3-aee0-a037dda4bcd0")
_PROJECT_NAMESPACE = "customer_acquisition_projects"


def _latest_message(metadata: dict) -> dict | None:
    messages = [
        item for item in metadata.get("recent_context") or []
        if isinstance(item, dict)
        and isinstance(item.get("message_id"), int)
        and item.get("message_id") > 0
    ]
    if not messages:
        return None
    latest = max(messages, key=lambda row: row["message_id"])
    return {
        "id": latest["message_id"],
        "url": str(latest.get("url") or "") or None,
        "published_at": str(latest.get("published_at") or "") or None,
        "excerpt": str(latest.get("text") or "")[:300],
    }


def snapshot_review(
    store: RuntimeStateStore, *, product_id: UUID, project_id: UUID
) -> dict:
    """Inspect the canonical registry without mutating state or fetching new posts."""
    if product_id != _FEMDOM_PRODUCT or project_id != _FEMDOM_PROJECT:
        raise ValueError("Only the saved FemDom product and project are in scope")
    project = store.get(_PROJECT_NAMESPACE, str(project_id))
    if not isinstance(project, dict) or project.get("deleted_at"):
        raise ValueError("FemDom project is unavailable")
    if str(project.get("product_id") or "") != str(product_id):
        raise ValueError("Product/project mismatch")

    registry = TelegramCommunityRegistry(store)
    rows = registry.list_for_product(product_id)
    communities = []
    for row in rows:
        metadata = registry.hydrate_metadata(row)
        handle = _public_handle(metadata.get("handle") or row.get("url"))
        if not handle:
            continue
        fit = row.get("audience_fit") or {}
        caps = metadata.get("surface_capabilities")
        caps = caps if isinstance(caps, dict) else {}
        item = {
            "handle": handle,
            "title": str(row.get("title") or "")[:200],
            "description_excerpt": str(metadata.get("about") or "")[:300],
            "last_native_checked_at": metadata.get("source_checked_at"),
            "native_verification": metadata.get("native_research_status"),
            "audience_confirmed": fit.get("confirmed") is True,
            "fit_reason": fit.get("reason")
            or metadata.get("community_fit_reason"),
            "fit_terms": list(fit.get("terms") or []),
            "reply_surface": caps.get("reply"),
            "comment_surface": caps.get("comment"),
            "publisher_permission_verified": False,
            "research_only_latest_post": _latest_message(metadata),
            "action_target_url": metadata.get("action_target_url"),
            "action_target_specific": metadata.get("action_target_specific") is True,
            "actionability": metadata.get("actionability"),
        }
        communities.append(item)
    communities.sort(key=lambda x: x["handle"])
    return {
        "status": "OFFLINE_REGISTRY_SNAPSHOT_COMPLETE",
        "generated_at": datetime.now(UTC).isoformat(),
        "product_id": str(product_id),
        "project_id": str(project_id),
        "source": "EXISTING_TELEGRAM_COMMUNITY_REGISTRY_ONLY",
        "read_only": True,
        "external_telegram_read_calls": 0,
        "paid_search_calls": 0,
        "telegram_send_calls": 0,
        "registry_count": len(rows),
        "audience_confirmed_count": sum(c["audience_confirmed"] for c in communities),
        "recent_context_available_count": sum(
            c["research_only_latest_post"] is not None for c in communities
        ),
        "exact_action_target_count": sum(
            c["action_target_specific"] and bool(c["action_target_url"])
            for c in communities
        ),
        "communities": communities,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Review stored FemDom Telegram snapshots")
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--project-id", type=UUID, required=True)
    args = parser.parse_args()
    result = snapshot_review(
        get_runtime_store(), product_id=args.product_id, project_id=args.project_id
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

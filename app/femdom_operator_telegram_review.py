"""Apply explicit human Telegram audience feedback, without publishing authorization.

User observations prove in-context relevance and visible surfaces, not that
Partizan's specific connected account is a member or permitted to send.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle

PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
EVIDENCE = (
    Path(__file__).resolve().parent.parent
    / "ops/femdom/telegram_operator_review_2026-10-25.json"
)


def reconcile_operator_review(
    store: RuntimeStateStore, *, product_id: UUID,
    commit: bool = False, payload: dict | None = None,
) -> dict:
    if product_id != PRODUCT:
        raise ValueError("Restricted to the manually reviewed FemDom product")
    record = payload if payload is not None else json.loads(
        EVIDENCE.read_text(encoding="utf-8")
    )
    if str(record.get("product_id")) != str(product_id):
        raise ValueError("Manual review product mismatch")
    entries = list(record.get("entries") or [])
    if not (1 <= len(entries) <= 20):
        raise ValueError("Manual review entry count must be in 1..20")

    registry = TelegramCommunityRegistry(store)
    current = registry.list_for_product(product_id)
    by_handle = {}
    for row in current:
        handle = _public_handle(
            registry.hydrate_metadata(row).get("handle") or row.get("url")
        )
        if handle and handle not in by_handle:
            by_handle[handle] = row
    results = []
    visited: set[str] = set()
    confirmed_before = sum(
        (row.get("audience_fit") or {}).get("confirmed") is True
        for row in current
    )
    for item in entries:
        handle = str(item.get("handle") or "").strip().casefold()
        if handle in visited:
            raise ValueError("Repeated manually reviewed community handle")
        visited.add(handle)
        if not handle or _public_handle(f"https://t.me/{handle}") != handle:
            raise ValueError("Invalid operator review public handle")
        if item.get("audience") != "DIRECT":
            raise ValueError("Operator sync is only for explicit DIRECT judgments")
        surface = item.get("surface")
        if surface not in {
            "GROUP_STANDALONE", "NO_WRITE_AT_THIS_POST",
            "USER_OBSERVED_WRITE_SURFACE",
        }:
            raise ValueError("Unknown user-observed surface type")

        # No new community is manufactured from a manual assertion.
        row = by_handle.get(handle)
        if row is None:
            results.append({"handle": handle, "status": "MISSING_REGISTRY"})
            continue
        metadata = registry.hydrate_metadata(row)
        reviewed = {
            "source": "USER_MANUAL_VISUAL_REVIEW",
            "reviewed_on": str(record["reviewed_on"]),
            "reference_post": str(item.get("post") or ""),
            "observed_surface": surface,
            "account_join_needed": str(item.get("join_needed") or "UNKNOWN"),
            "related_post": str(item.get("related_post") or ""),
            "notes": str(item.get("note") or "")[:500],
            "account_write_permission_verified": False,
            "publication_approved": False,
        }
        old_review = metadata.get("operator_review")
        existing_confirmation = (row.get("audience_fit") or {}).get(
            "confirmed"
        ) is True
        if old_review == reviewed and existing_confirmation:
            results.append({"handle": handle, "status": "ALREADY_APPLIED"})
            continue
        if commit:
            # A manually confirmed audience can be used in future read-only
            # discovery. This does not verify the actionability of its posts.
            refreshed = {
                **metadata,
                "operator_review": reviewed,
                "community_fit_pass": True,
                "community_fit_reason": "USER_MANUALLY_CONFIRMED",
                "community_fit_terms": list(
                    metadata.get("community_fit_terms") or []
                ),
                "publisher_permission_verified": False,
            }
            registry.upsert(
                product_id=product_id,
                canonical_key=str(row.get("canonical_key") or ""),
                title=str(row.get("title") or handle),
                url=str(row.get("url") or f"https://t.me/{handle}"),
                kind=str(row.get("kind") or "CHANNEL"),
                metadata=refreshed,
            )
        results.append({
            "handle": handle,
            "status": "APPLIED" if commit else "WOULD_APPLY",
            "audience_was_confirmed": existing_confirmation,
            "surface": surface,
            "account_permission_verified": False,
        })

    after = registry.list_for_product(product_id)
    return {
        "status": "OPERATOR_FEEDBACK_RECONCILIATION_COMPLETE",
        "mode": "COMMIT" if commit else "PREVIEW_ONLY",
        "registry_before": len(current),
        "registry_after": len(after),
        "confirmed_before": confirmed_before,
        "confirmed_after": sum(
            (row.get("audience_fit") or {}).get("confirmed") is True
            for row in after
        ),
        "reviewed": len(entries),
        "added_confirmations": sum(
            row["status"] == "APPLIED"
            and row.get("audience_was_confirmed") is False
            for row in results
        ),
        "rows": results,
        "telegram_search_calls": 0,
        "telegram_join_calls": 0,
        "telegram_send_calls": 0,
        "publisher_permission_granted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-id", required=True, type=UUID)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        reconcile_operator_review(
            get_runtime_store(), product_id=args.product_id, commit=args.commit
        ), ensure_ascii=False, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

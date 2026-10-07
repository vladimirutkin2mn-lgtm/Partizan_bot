from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from app.customer_telegram_preview import (
    PREVIEW_SCHEMA_VERSION,
    TELEGRAM_PREVIEW_NAMESPACE,
    _preview_key,
    _render_preview,
    _validate_existing_preview,
)
from app.customer_telegram_rollout import _load_exact_target
from app.telegram_client_publishing import customer_telegram_client_publish_service

TELEGRAM_REVIEW_BUNDLE_NAMESPACE = "customer_telegram_review_bundle"
REVIEW_BUNDLE_SCHEMA_VERSION = 1
_MAX_PROFILE_ABOUT_LENGTH = 70


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the exact customer-owned Telegram profile/comment review bundle "
            "without mutating the profile or publishing any Telegram content."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--expected-telegram-entity-id", type=int, required=True)
    parser.add_argument("--expected-handle", required=True)
    return parser


def _bundle_key(project_id: UUID, entity_id: int) -> str:
    return f"{project_id}:{entity_id}:v{REVIEW_BUNDLE_SCHEMA_VERSION}"


def _is_partizan_url(value: str) -> bool:
    try:
        hostname = (urlsplit(value).hostname or "").casefold()
    except ValueError:
        return False
    return hostname == "partizanlabs.com" or hostname.endswith(".partizanlabs.com")


def _native_destination(reference_links: list[str]) -> str:
    links = [str(item).strip() for item in reference_links if str(item).strip()]
    if not links:
        raise ValueError("Product has no customer-facing destination")

    for link in links:
        try:
            hostname = (urlsplit(link).hostname or "").casefold()
        except ValueError:
            continue
        if hostname in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            return link

    for link in links:
        if not _is_partizan_url(link):
            return link
    raise ValueError(
        "Product reference links contain only Partizan-owned routes; "
        "a real product destination is required for review"
    )


def _profile_about(product_name: str, destination: str) -> str:
    proposed = f"{product_name.strip()} ↓\n{destination.strip()}".strip()
    if len(proposed) > _MAX_PROFILE_ABOUT_LENGTH:
        raise ValueError("Native Telegram profile CTA exceeds the conservative bio limit")
    return proposed


def _recommended_variant(variants: list[dict]) -> dict:
    if not variants:
        raise ValueError("Telegram review has no prepared comment variants")

    sendable = [item for item in variants if bool(item.get("send_eligible"))]
    pool = sendable or variants
    preferred = [
        item
        for item in pool
        if str(item.get("conversion_mechanism") or "") == "PROFILE_CLICK"
        and str(item.get("variant_name") or "") == "expertise_signal"
    ]
    if preferred:
        return preferred[0]

    profile_click = [
        item
        for item in pool
        if str(item.get("conversion_mechanism") or "") == "PROFILE_CLICK"
    ]
    if profile_click:
        return profile_click[0]
    return pool[0]


def _review_hash(payload: dict) -> str:
    material = {
        "project_id": payload["project_id"],
        "product_id": payload["product_id"],
        "target_url": payload["target_url"],
        "native_destination": payload["native_destination"],
        "proposed_profile": payload["proposed_profile"],
        "recommended_comment": payload["recommended_comment"],
        "story_plan": payload["story_plan"],
    }
    return hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


async def run(args: argparse.Namespace) -> dict:
    store, _, product, _, _, target_url = _load_exact_target(args)
    preview_key = _preview_key(args.project_id, args.expected_telegram_entity_id)
    preview = store.get(TELEGRAM_PREVIEW_NAMESPACE, preview_key)
    if preview is None:
        raise ValueError("Telegram human preview must exist before building the review bundle")
    if int(preview.get("schema_version") or 0) != PREVIEW_SCHEMA_VERSION:
        raise ValueError("Telegram human preview is stale")
    if str(preview.get("status") or "") != "AWAITING_USER_APPROVAL":
        raise ValueError("Telegram human preview is not awaiting user approval")
    _validate_existing_preview(preview)

    rendered = _render_preview(preview)
    recommended = _recommended_variant(list(rendered.get("variants") or []))
    destination = _native_destination(list(product.reference_links or []))
    current_profile = await customer_telegram_client_publish_service.profile_internal(
        args.project_id
    )
    proposed_about = _profile_about(args.expected_product_name, destination)

    payload = {
        "schema_version": REVIEW_BUNDLE_SCHEMA_VERSION,
        "status": "AWAITING_USER_REVIEW",
        "project_id": str(args.project_id),
        "product_id": str(product.id),
        "product_name": args.expected_product_name,
        "target_url": target_url,
        "target_handle": args.expected_handle,
        "native_destination": destination,
        "current_profile": {
            "display_name": current_profile.display_name,
            "about": current_profile.about,
            "avatar_present": current_profile.avatar is not None,
        },
        "proposed_profile": {
            "display_name": current_profile.display_name,
            "about": proposed_about,
            "cta": destination,
            "avatar": {
                "change_recommended": True,
                "status": "AWAITING_CREATIVE_REVIEW",
                "reason": (
                    "Avatar is part of the profile conversion treatment, but no avatar "
                    "may be changed before the user sees and approves the exact creative."
                ),
            },
        },
        "recommended_comment": {
            "conversion_mechanism": recommended.get("conversion_mechanism"),
            "variant_name": recommended.get("variant_name"),
            "objective": recommended.get("objective"),
            "expected_user_next_step": recommended.get("expected_user_next_step"),
            "action_id": recommended.get("action_id"),
            "action_status": recommended.get("action_status"),
            "content_text": recommended.get("content_text"),
            "content_sha256": recommended.get("content_sha256"),
            "send_eligible_under_existing_preview": bool(recommended.get("send_eligible")),
        },
        "alternatives": [
            {
                "conversion_mechanism": item.get("conversion_mechanism"),
                "variant_name": item.get("variant_name"),
                "content_text": item.get("content_text"),
                "content_sha256": item.get("content_sha256"),
                "send_eligible_under_existing_preview": bool(item.get("send_eligible")),
            }
            for item in list(rendered.get("variants") or [])
            if item.get("action_id") != recommended.get("action_id")
        ],
        "story_plan": {
            "first_experiment": "DEFER",
            "reason": (
                "Do not mix story exposure into the first comment/profile experiment. "
                "Prepare it as a separate reviewed arm after the profile/avatar treatment "
                "has been approved."
            ),
        },
        "safety": {
            "profile_mutated": False,
            "comment_published": False,
            "story_published": False,
            "publish_requires_user_phrase": "публикуй",
        },
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    payload["review_fingerprint"] = _review_hash(payload)
    store.put(
        TELEGRAM_REVIEW_BUNDLE_NAMESPACE,
        _bundle_key(args.project_id, args.expected_telegram_entity_id),
        payload,
    )
    return payload


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"error_type": type(exc).__name__, "error": str(exc)[:2000]},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

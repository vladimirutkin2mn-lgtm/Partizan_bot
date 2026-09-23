from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from app.config import get_settings
from app.conversion_path import conversion_path_validator
from app.conversion_scenarios import ConversionMechanism
from app.customer_telegram_preview import (
    PREVIEW_SCHEMA_VERSION,
    TELEGRAM_PREVIEW_NAMESPACE,
    _preview_key,
    _render_preview,
    _validate_existing_preview,
)
from app.customer_telegram_rollout import (
    _ensure_customer_identity,
    _load_exact_target,
)
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    customer_telegram_client_publish_service,
)

TELEGRAM_PROFILE_SETUP_NAMESPACE = "customer_telegram_profile_conversion_setup"
_MAX_PROFILE_ABOUT_LENGTH = 70


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install and verify the stable FemDom conversion CTA in the connected Telegram "
            "profile. This command never publishes a Telegram message."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--expected-telegram-entity-id", type=int, required=True)
    parser.add_argument("--expected-handle", required=True)
    return parser


def _setup_key(project_id: UUID) -> str:
    return f"{project_id}:stable-profile-v1"


def _build_about(
    current_about: str,
    *,
    product_name: str,
    profile_route_url: str,
) -> str:
    current = current_about.strip()
    if profile_route_url in current:
        return current

    cta = f"{product_name.strip()} → {profile_route_url}".strip()
    if len(cta) > _MAX_PROFILE_ABOUT_LENGTH:
        raise ValueError("Stable Telegram profile CTA exceeds the conservative bio limit")
    if not current:
        return cta

    candidate = f"{current}\n{cta}"
    if len(candidate) > _MAX_PROFILE_ABOUT_LENGTH:
        raise ValueError(
            "Existing Telegram bio is too long to append the stable conversion CTA safely"
        )
    return candidate


def _profile_route_url(slot) -> str:
    token = str(slot.metadata.get("profile_route_token") or "").strip()
    public_base = get_settings().partizan_public_base_url
    if public_base is None or not token:
        raise ValueError("Stable public profile conversion route is not available")
    return f"{str(public_base).rstrip('/')}/p/{token}"


def _refresh_preview_paths(
    *,
    record: dict,
    identity,
    slot,
    profile_route_url: str,
) -> None:
    for item in record.get("variants", []):
        action = distribution_execution_service.get_action(UUID(str(item["action_id"])))
        mechanism = ConversionMechanism(str(item["conversion_mechanism"]))
        assessment = conversion_path_validator.assess(
            mechanism=mechanism,
            action=action,
            identity=identity,
            slot=slot,
            profile_route_url=profile_route_url,
        )
        distribution_execution_service.attach_conversion_path(
            action.id,
            assessment.as_dict(),
        )


async def run(args: argparse.Namespace) -> dict:
    store, _, product, _, _, _ = _load_exact_target(args)
    preview_key = _preview_key(args.project_id, args.expected_telegram_entity_id)
    preview = store.get(TELEGRAM_PREVIEW_NAMESPACE, preview_key)
    if preview is None:
        raise ValueError("Telegram human preview must exist before profile conversion setup")
    if int(preview.get("schema_version") or 0) != PREVIEW_SCHEMA_VERSION:
        raise ValueError("Telegram human preview is stale")
    if str(preview.get("status") or "") != "AWAITING_USER_APPROVAL":
        raise ValueError("Telegram human preview is not awaiting user approval")
    _validate_existing_preview(preview)

    connection = store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(args.project_id))
    if connection is None or str(connection.get("status") or "").upper() != "ACTIVE":
        raise ValueError("An active customer-owned Telegram connection is required")

    identity = _ensure_customer_identity(args.project_id, product, connection)
    slot = distribution_control_plane_service.find_active_slot(identity.id, product.id)
    slot = distribution_control_plane_service.ensure_profile_route(slot.id)
    profile_route_url = _profile_route_url(slot)
    if profile_route_url != str(preview.get("profile_route_url") or ""):
        raise ValueError("Preview profile route does not match the active CampaignSlot")

    if not product.reference_links:
        raise ValueError("Product has no fallback destination for the stable profile route")
    fallback_url = str(product.reference_links[0])
    slot = distribution_control_plane_service.set_profile_route_fallback(
        slot.id,
        fallback_url,
    )

    setup_key = _setup_key(args.project_id)
    existing = store.get(TELEGRAM_PROFILE_SETUP_NAMESPACE, setup_key)
    profile = await customer_telegram_client_publish_service.profile_internal(args.project_id)

    if existing is not None and str(existing.get("status") or "") == "VERIFIED":
        if profile_route_url not in profile.about:
            distribution_control_plane_service.set_identity_profile_conversion(
                identity.id,
                profile_url=profile_route_url,
                verified=False,
            )
            raise ValueError(
                "Telegram profile CTA drifted after verification; refusing automatic rewrite"
            )
        identity = distribution_control_plane_service.set_identity_profile_conversion(
            identity.id,
            profile_url=profile_route_url,
            verified=True,
        )
        _refresh_preview_paths(
            record=preview,
            identity=identity,
            slot=slot,
            profile_route_url=profile_route_url,
        )
        return {
            "status": "already_verified",
            "profile_route_url": profile_route_url,
            "profile_about": profile.about,
            "preview": _render_preview(preview),
            "published": False,
        }

    previous_about = profile.about
    desired_about = _build_about(
        previous_about,
        product_name=args.expected_product_name,
        profile_route_url=profile_route_url,
    )
    attempt = {
        "status": "ATTEMPTING",
        "project_id": str(args.project_id),
        "product_id": str(product.id),
        "identity_id": str(identity.id),
        "slot_id": str(slot.id),
        "profile_route_url": profile_route_url,
        "fallback_url": fallback_url,
        "previous_about": previous_about,
        "desired_about": desired_about,
        "started_at": datetime.now(UTC).isoformat(),
    }
    store.put(TELEGRAM_PROFILE_SETUP_NAMESPACE, setup_key, attempt)

    mutated = desired_about != previous_about.strip()
    try:
        if mutated:
            profile = await customer_telegram_client_publish_service.update_profile_about_internal(
                args.project_id,
                desired_about,
            )
        if profile_route_url not in profile.about:
            raise ValueError("Telegram did not confirm the stable profile conversion CTA")

        identity = distribution_control_plane_service.set_identity_profile_conversion(
            identity.id,
            profile_url=profile_route_url,
            verified=True,
        )
        _refresh_preview_paths(
            record=preview,
            identity=identity,
            slot=slot,
            profile_route_url=profile_route_url,
        )
        rendered = _render_preview(preview)
        if not rendered["variants"] or not all(
            bool(item.get("send_eligible")) for item in rendered["variants"]
        ):
            raise ValueError("Profile setup succeeded but one or more preview paths remain blocked")

        verified = {
            **attempt,
            "status": "VERIFIED",
            "verified_about": profile.about,
            "verified_at": datetime.now(UTC).isoformat(),
        }
        store.put(TELEGRAM_PROFILE_SETUP_NAMESPACE, setup_key, verified)
        return {
            "status": "verified",
            "profile_route_url": profile_route_url,
            "previous_about": previous_about,
            "profile_about": profile.about,
            "fallback_url": fallback_url,
            "preview": rendered,
            "published": False,
        }
    except Exception as exc:
        distribution_control_plane_service.set_identity_profile_conversion(
            identity.id,
            profile_url=profile_route_url,
            verified=False,
        )
        rollback_status = "NOT_NEEDED"
        if mutated:
            try:
                await customer_telegram_client_publish_service.update_profile_about_internal(
                    args.project_id,
                    previous_about,
                )
                rollback_status = "RESTORED"
            except Exception:
                rollback_status = "FAILED"
        failed = {
            **attempt,
            "status": "FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "rollback_status": rollback_status,
            "failed_at": datetime.now(UTC).isoformat(),
        }
        store.put(TELEGRAM_PROFILE_SETUP_NAMESPACE, setup_key, failed)
        raise


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

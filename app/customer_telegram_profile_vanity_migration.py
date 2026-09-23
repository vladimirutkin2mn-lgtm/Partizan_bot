from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from app.config import get_settings
from app.customer_telegram_preview import (
    PREVIEW_SCHEMA_VERSION,
    TELEGRAM_PREVIEW_NAMESPACE,
    _preview_key,
    _render_preview,
    _validate_existing_preview,
)
from app.customer_telegram_profile_setup import _refresh_preview_paths
from app.customer_telegram_rollout import _ensure_customer_identity, _load_exact_target
from app.distribution_control_plane_service import distribution_control_plane_service
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    customer_telegram_client_publish_service,
)

TELEGRAM_PROFILE_VANITY_MIGRATION_NAMESPACE = "customer_telegram_profile_vanity_migration"
VANITY_ALIAS = "femdom"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate the verified FemDom Telegram profile CTA from the technical /p token "
            "to the short /femdom URL. This command never publishes a Telegram message."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--expected-telegram-entity-id", type=int, required=True)
    parser.add_argument("--expected-handle", required=True)
    return parser


def _migration_key(project_id: UUID) -> str:
    return f"{project_id}:femdom-vanity-v1"


def _vanity_url() -> str:
    public_base = get_settings().partizan_public_base_url
    if public_base is None:
        raise ValueError("PARTIZAN_PUBLIC_BASE_URL is required")
    return f"{str(public_base).rstrip('/')}/{VANITY_ALIAS}"


def _desired_about(product_name: str, vanity_url: str) -> str:
    return f"{product_name.strip()} ↓\n{vanity_url}"


async def _wait_for_exact_about(
    project_id: UUID,
    expected_about: str,
    *,
    attempts: int = 4,
    delay_seconds: float = 1.0,
):
    observed = None
    for index in range(attempts):
        observed = await customer_telegram_client_publish_service.profile_internal(project_id)
        if observed.about.strip() == expected_about:
            return observed
        if index + 1 < attempts:
            await asyncio.sleep(delay_seconds)
    actual = observed.about if observed is not None else ""
    raise ValueError(
        "Telegram did not confirm the exact short FemDom profile CTA; "
        f"observed_about={actual!r}"
    )


async def run(args: argparse.Namespace) -> dict:
    store, _, product, _, _, _ = _load_exact_target(args)
    connection = store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(args.project_id))
    if connection is None or str(connection.get("status") or "").upper() != "ACTIVE":
        raise ValueError("An active customer-owned Telegram connection is required")

    identity = _ensure_customer_identity(args.project_id, product, connection)
    slot = distribution_control_plane_service.find_active_slot(identity.id, product.id)
    profile_config = identity.profile_config if isinstance(identity.profile_config, dict) else {}
    previous_route = str(profile_config.get("conversion_profile_url") or "").strip()
    if profile_config.get("conversion_profile_verified") is not True or not previous_route:
        raise ValueError("Existing Telegram profile conversion route must already be verified")

    preview_key = _preview_key(args.project_id, args.expected_telegram_entity_id)
    preview = store.get(TELEGRAM_PREVIEW_NAMESPACE, preview_key)
    if preview is None:
        raise ValueError("Telegram human preview must exist before vanity migration")
    if int(preview.get("schema_version") or 0) != PREVIEW_SCHEMA_VERSION:
        raise ValueError("Telegram human preview is stale")
    _validate_existing_preview(preview)

    vanity_url = _vanity_url()
    expected_previous_about = f"{args.expected_product_name.strip()} → {previous_route}"
    desired_about = _desired_about(args.expected_product_name, vanity_url)
    profile = await customer_telegram_client_publish_service.profile_internal(args.project_id)

    if profile.about.strip() == desired_about:
        slot = distribution_control_plane_service.set_profile_route_alias(slot.id, VANITY_ALIAS)
        identity = distribution_control_plane_service.set_identity_profile_conversion(
            identity.id,
            profile_url=vanity_url,
            verified=True,
        )
        _refresh_preview_paths(
            record=preview,
            identity=identity,
            slot=slot,
            profile_route_url=vanity_url,
        )
        preview["profile_route_url"] = vanity_url
        store.put(TELEGRAM_PREVIEW_NAMESPACE, preview_key, preview)
        return {
            "status": "already_migrated",
            "profile_about": profile.about,
            "profile_route_url": vanity_url,
            "preview": _render_preview(preview),
            "published": False,
        }

    if profile.about.strip() != expected_previous_about:
        raise ValueError(
            "Telegram bio changed since the approved preview; refusing to overwrite it"
        )

    migration_key = _migration_key(args.project_id)
    attempt = {
        "status": "ATTEMPTING",
        "project_id": str(args.project_id),
        "product_id": str(product.id),
        "identity_id": str(identity.id),
        "slot_id": str(slot.id),
        "previous_route_url": previous_route,
        "vanity_route_url": vanity_url,
        "previous_about": profile.about,
        "desired_about": desired_about,
        "started_at": datetime.now(UTC).isoformat(),
    }
    store.put(TELEGRAM_PROFILE_VANITY_MIGRATION_NAMESPACE, migration_key, attempt)

    mutated = False
    try:
        slot = distribution_control_plane_service.set_profile_route_alias(slot.id, VANITY_ALIAS)
        await customer_telegram_client_publish_service.update_profile_about_internal(
            args.project_id,
            desired_about,
        )
        mutated = True
        updated = await _wait_for_exact_about(
            args.project_id,
            desired_about,
        )

        identity = distribution_control_plane_service.set_identity_profile_conversion(
            identity.id,
            profile_url=vanity_url,
            verified=True,
        )
        _refresh_preview_paths(
            record=preview,
            identity=identity,
            slot=slot,
            profile_route_url=vanity_url,
        )
        preview["profile_route_url"] = vanity_url
        store.put(TELEGRAM_PREVIEW_NAMESPACE, preview_key, preview)
        rendered = _render_preview(preview)
        if not rendered["variants"] or not all(
            bool(item.get("send_eligible")) for item in rendered["variants"]
        ):
            raise ValueError("Vanity migration succeeded but a preview path is not READY")

        verified = {
            **attempt,
            "status": "VERIFIED",
            "verified_about": updated.about,
            "verified_at": datetime.now(UTC).isoformat(),
        }
        store.put(TELEGRAM_PROFILE_VANITY_MIGRATION_NAMESPACE, migration_key, verified)
        return {
            "status": "verified",
            "profile_about": updated.about,
            "profile_route_url": vanity_url,
            "previous_route_url": previous_route,
            "preview": rendered,
            "published": False,
        }
    except Exception as exc:
        rollback_status = "NOT_NEEDED"
        try:
            identity = distribution_control_plane_service.set_identity_profile_conversion(
                identity.id,
                profile_url=previous_route,
                verified=True,
            )
            _refresh_preview_paths(
                record=preview,
                identity=identity,
                slot=slot,
                profile_route_url=previous_route,
            )
            preview["profile_route_url"] = previous_route
            store.put(TELEGRAM_PREVIEW_NAMESPACE, preview_key, preview)
        except Exception:
            pass
        if mutated:
            try:
                await customer_telegram_client_publish_service.update_profile_about_internal(
                    args.project_id,
                    expected_previous_about,
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
        store.put(TELEGRAM_PROFILE_VANITY_MIGRATION_NAMESPACE, migration_key, failed)
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

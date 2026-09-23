from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from app.config import get_settings
from app.customer_telegram_rollout import _load_exact_target
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_types import DistributionIdentityStatus, DistributionPlatform
from app.telegram_profile_inspection import (
    customer_telegram_profile_inspection_service,
)

_SAFE_BIO_LIMIT = 70


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read the connected Telegram profile and prepare an exact conversion bio "
            "for human review. This command never mutates Telegram."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--expected-telegram-entity-id", type=int, required=True)
    parser.add_argument("--expected-handle", required=True)
    return parser


def _identity(project_id: UUID):
    matches = []
    for identity in distribution_control_plane_service.list_identities(
        DistributionPlatform.TELEGRAM
    ):
        config = identity.profile_config if isinstance(identity.profile_config, dict) else {}
        if (
            identity.status == DistributionIdentityStatus.ACTIVE
            and str(config.get("customer_project_id") or "") == str(project_id)
            and str(config.get("publisher_mode") or "").upper() == "CLIENT_OWNED"
        ):
            matches.append(identity)
    if len(matches) != 1:
        raise ValueError("Expected one active customer-owned Telegram identity")
    return matches[0]


def _proposed_about(product_name: str, profile_route_url: str) -> str:
    proposal = f"{product_name} → {profile_route_url}"
    if len(proposal) > _SAFE_BIO_LIMIT:
        raise ValueError("Stable conversion CTA does not fit the conservative Telegram bio limit")
    return proposal


async def run(args: argparse.Namespace) -> dict:
    _, _, product, _, _, _ = _load_exact_target(args)
    identity = _identity(args.project_id)
    slot = distribution_control_plane_service.find_active_slot(identity.id, product.id)
    slot = distribution_control_plane_service.ensure_profile_route(slot.id)

    token = str(slot.metadata.get("profile_route_token") or "").strip()
    public_base = get_settings().partizan_public_base_url
    if public_base is None or not token:
        raise ValueError("Stable public profile conversion route is not available")
    profile_route_url = f"{str(public_base).rstrip('/')}/p/{token}"

    snapshot = await customer_telegram_profile_inspection_service.inspect_internal(
        args.project_id
    )
    proposal = _proposed_about(str(product.name or "FemDom").strip(), profile_route_url)
    current_about = snapshot.about.strip()
    route_present = profile_route_url in current_about

    return {
        "mode": "READ_ONLY_PROFILE_PREVIEW",
        "project_id": str(args.project_id),
        "telegram_user_id": snapshot.user_id,
        "username": snapshot.username,
        "display_name": " ".join(
            item for item in (snapshot.first_name, snapshot.last_name) if item
        ).strip(),
        "current_about": current_about,
        "profile_route_url": profile_route_url,
        "route_present": route_present,
        "proposed_about": proposal,
        "proposed_about_length": len(proposal),
        "bio_limit_used_for_validation": _SAFE_BIO_LIMIT,
        "telegram_mutated": False,
        "publication_performed": False,
    }


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

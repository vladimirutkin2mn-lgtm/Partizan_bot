from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.telegram_client_publishing import customer_telegram_client_publish_service
from app.telegram_profile_conversion import (
    TelegramProfileMutationStatus,
    telegram_profile_conversion_service,
)

REVIEWED_PROFILE_APPLY_NAMESPACE = "reviewed_customer_telegram_profile_apply"
CONFIRMATION = "APPLY_REVIEWED_FEMDOM_PROFILE_V2"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply one exact, user-reviewed customer-owned Telegram profile treatment. "
            "This command cannot publish comments, replies, messages, or stories."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--profile-config", type=Path, required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def _normal_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _load_config(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Reviewed profile config must be a JSON object")
    required = {
        "operation_id",
        "authorization_scope",
        "display_name",
        "about",
        "native_destination",
        "avatar_filename",
        "avatar_sha256",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise ValueError(f"Reviewed profile config is missing fields: {missing}")
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported reviewed profile config schema")
    if str(payload["authorization_scope"]) != "PROFILE_ONLY":
        raise ValueError("Reviewed profile config is not profile-only")
    if bool(payload.get("story_publish_authorized")):
        raise ValueError("Reviewed profile apply cannot authorize a story")
    if bool(payload.get("community_publish_authorized")):
        raise ValueError("Reviewed profile apply cannot authorize community publication")
    if str(payload["native_destination"]).strip() not in str(payload["about"]):
        raise ValueError("Reviewed bio must contain the exact native destination")
    if len(str(payload["about"]).strip()) > 70:
        raise ValueError("Reviewed Telegram bio exceeds the conservative limit")

    single_file = str(payload.get("avatar_b64_file") or "").strip()
    chunks = payload.get("avatar_b64_chunks")
    if chunks is None:
        chunks = []
    if not isinstance(chunks, list) or any(not str(item).strip() for item in chunks):
        raise ValueError("Reviewed avatar chunk list must contain non-empty paths")
    if bool(single_file) == bool(chunks):
        raise ValueError("Reviewed profile must define exactly one avatar payload source")
    return payload


def _repository_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    repository_root = config_path.resolve().parents[2]
    return repository_root / path


def _avatar_bytes(config: dict, *, config_path: Path) -> bytes:
    chunk_paths = [str(item).strip() for item in config.get("avatar_b64_chunks") or []]
    if chunk_paths:
        encoded = "".join(
            "".join(
                _repository_path(config_path, item)
                .read_text(encoding="ascii")
                .split()
            )
            for item in chunk_paths
        )
    else:
        avatar_path = _repository_path(config_path, str(config["avatar_b64_file"]))
        encoded = "".join(avatar_path.read_text(encoding="ascii").split())

    content = base64.b64decode(encoded, validate=True)
    actual_sha = hashlib.sha256(content).hexdigest()
    expected_sha = str(config["avatar_sha256"]).strip().casefold()
    if actual_sha != expected_sha:
        raise ValueError("Reviewed Telegram avatar hash does not match the approved asset")
    if not content:
        raise ValueError("Reviewed Telegram avatar is empty")
    return content


def _marker_key(project_id: UUID, operation_id: str) -> str:
    return f"{project_id}:{operation_id.strip()}"


def _validate_target(args: argparse.Namespace) -> tuple[dict, object]:
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(args.project_id))
    if project is None or project.get("deleted_at"):
        raise ValueError("Exact customer project is not available")
    if str(project.get("product_id") or "") != str(args.expected_product_id):
        raise ValueError("Customer project product id does not match the reviewed profile")
    product = product_intake_service.get_product(args.expected_product_id)
    expected_name = args.expected_product_name.strip().casefold()
    if not expected_name or expected_name not in str(product.name or "").casefold():
        raise ValueError("Product name does not match the reviewed profile product")
    return project, product


def _profile_matches(profile, config: dict) -> bool:
    return bool(
        profile.display_name == str(config["display_name"]).strip()
        and _normal_text(profile.about) == _normal_text(str(config["about"]).strip())
        and profile.avatar is not None
    )


async def run(args: argparse.Namespace) -> dict:
    if args.confirm != CONFIRMATION:
        raise ValueError(f"Exact confirmation is required: {CONFIRMATION}")
    _validate_target(args)
    config = _load_config(args.profile_config)
    avatar = _avatar_bytes(config, config_path=args.profile_config)
    operation_id = str(config["operation_id"]).strip()
    if not operation_id:
        raise ValueError("Reviewed profile operation id is required")

    store = get_runtime_store()
    marker_key = _marker_key(args.project_id, operation_id)
    existing = store.get(REVIEWED_PROFILE_APPLY_NAMESPACE, marker_key)
    if existing is not None:
        live = await customer_telegram_client_publish_service.profile_internal(args.project_id)
        if not _profile_matches(live, config):
            raise ValueError(
                "Reviewed profile operation was already applied but live controlled fields differ; "
                "refusing to overwrite newer customer edits"
            )
        if live.username != existing.get("username_after"):
            raise ValueError("Telegram username changed after reviewed profile apply")
        return {
            **existing,
            "status": "ALREADY_APPLIED",
            "live_verified": True,
            "story_published": False,
            "community_content_published": False,
        }

    before = await customer_telegram_client_publish_service.profile_internal(args.project_id)
    mutation = await telegram_profile_conversion_service.apply_internal(
        args.project_id,
        about=str(config["about"]).strip(),
        display_name=str(config["display_name"]).strip(),
        avatar_content=avatar,
        avatar_filename=str(config["avatar_filename"]).strip(),
    )
    if mutation.status != TelegramProfileMutationStatus.APPLIED:
        raise ValueError("Reviewed Telegram profile mutation did not reach APPLIED")

    after = await customer_telegram_client_publish_service.profile_internal(args.project_id)
    if not _profile_matches(after, config):
        raise ValueError("Live Telegram profile does not match the reviewed treatment after apply")
    if after.username != before.username:
        try:
            await telegram_profile_conversion_service.rollback_internal(
                args.project_id,
                mutation.mutation_id,
            )
        finally:
            raise ValueError("Telegram username changed unexpectedly; reviewed profile was rolled back")

    marker = {
        "status": "APPLIED",
        "operation_id": operation_id,
        "proposal_id": str(config.get("proposal_id") or ""),
        "project_id": str(args.project_id),
        "product_id": str(args.expected_product_id),
        "mutation_id": str(mutation.mutation_id),
        "before_snapshot_id": str(mutation.before_snapshot_id),
        "after_snapshot_id": str(mutation.after_snapshot_id),
        "display_name": after.display_name,
        "about": after.about,
        "username_before": before.username,
        "username_after": after.username,
        "avatar_present_before": before.avatar is not None,
        "avatar_present_after": after.avatar is not None,
        "avatar_sha256": str(config["avatar_sha256"]),
        "native_destination": str(config["native_destination"]),
        "live_verified": True,
        "rollback_snapshot_preserved": True,
        "story_published": False,
        "community_content_published": False,
        "community_publish_authorization_phrase": str(
            config.get("community_publish_authorization_phrase") or ""
        ),
    }
    if not store.put_if_absent(REVIEWED_PROFILE_APPLY_NAMESPACE, marker_key, marker):
        existing = store.get(REVIEWED_PROFILE_APPLY_NAMESPACE, marker_key)
        if existing is not None:
            return {**existing, "status": "ALREADY_APPLIED", "live_verified": True}
        raise RuntimeError("Reviewed Telegram profile marker could not be persisted")
    return marker


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

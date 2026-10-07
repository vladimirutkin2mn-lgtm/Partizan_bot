from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import functions as telegram_functions

from app.config import get_settings
from app.customer_telegram_profile_apply_reviewed import (
    _avatar_bytes,
    _load_config,
    _validate_target,
)
from app.runtime_store import get_runtime_store
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    customer_telegram_client_publish_service,
)

CONFIRMATION = "APPLY_FEMDOM_PROFILE_HQ_AND_STORY_V1"
PROFILE_MARKER_NAMESPACE = "reviewed_customer_telegram_profile_hq"
STORY_MARKER_NAMESPACE = "reviewed_customer_telegram_story_publish"
PROFILE_OPERATION_ID = "femdom-profile-hq-username-removed-2026-10-08-v1"
STORY_OPERATION_ID = "femdom-story-2026-10-08-v1"
EXPECTED_DISPLAY_NAME = "Nika"
AVATAR_SIZE = (1024, 1024)
STORY_SIZE = (1080, 1920)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the exact reviewed FemDom profile quality correction, remove the public "
            "Telegram username, and publish the separately approved story. This command has "
            "no community comment/reply/message publishing path."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--profile-config", type=Path, required=True)
    parser.add_argument("--review-config", type=Path, required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def _normal_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _jpeg_bytes(image: Image.Image, *, quality: int = 95) -> bytes:
    output = BytesIO()
    image.save(
        output,
        format="JPEG",
        quality=quality,
        subsampling=0,
        optimize=True,
        progressive=True,
    )
    return output.getvalue()


def prepare_avatar_for_telegram(content: bytes) -> bytes:
    with Image.open(BytesIO(content)) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        avatar = ImageOps.fit(
            source,
            AVATAR_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        avatar = avatar.filter(
            ImageFilter.UnsharpMask(radius=1.15, percent=125, threshold=3)
        )
        return _jpeg_bytes(avatar, quality=96)


def prepare_story_media(content: bytes) -> bytes:
    with Image.open(BytesIO(content)) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        background = ImageOps.fit(
            source,
            STORY_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        ).filter(ImageFilter.GaussianBlur(radius=42))
        background = ImageEnhance.Brightness(background).enhance(0.46)

        portrait = ImageOps.fit(
            source,
            (960, 960),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        ).filter(ImageFilter.UnsharpMask(radius=1.0, percent=115, threshold=3))

        canvas = background.copy()
        canvas.paste(portrait, (60, 390))
        return _jpeg_bytes(canvas, quality=94)


def _load_story_config(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Reviewed Telegram proposal must be a JSON object")
    story = payload.get("story")
    if not isinstance(story, dict):
        raise ValueError("Reviewed Telegram proposal has no story")
    if str(story.get("first_experiment") or "").strip().upper() != "INCLUDE":
        raise ValueError("Reviewed Telegram proposal does not include the story")
    copy = str(story.get("copy") or "").strip()
    if not copy:
        raise ValueError("Reviewed Telegram story copy is empty")
    if str(payload.get("display_name") or "").strip() != EXPECTED_DISPLAY_NAME:
        raise ValueError("Reviewed Telegram display name changed")
    return {"copy": copy, "visual_direction": str(story.get("visual_direction") or "")}


def _marker_key(project_id: UUID, operation_id: str) -> str:
    return f"{project_id}:{operation_id}"


async def _remove_public_username(project_id: UUID) -> None:
    session = customer_telegram_client_publish_service._active_session_internal(project_id)
    settings = get_settings()
    api_id = settings.telegram_client_publish_api_id
    api_hash = settings.telegram_client_publish_api_hash
    if api_id is None or api_hash is None:
        raise ValueError("Telegram client publishing credentials are not configured")

    client = TelegramClient(
        StringSession(session),
        api_id,
        api_hash.get_secret_value(),
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Telegram session is not authorised")
        me = await client.get_me()
        if me is None:
            raise ValueError("Telegram identity is not available")
        if getattr(me, "username", None):
            await client(telegram_functions.account.UpdateUsernameRequest(username=""))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            f"Unable to remove the reviewed Telegram username: {type(exc).__name__}"
        ) from None
    finally:
        await client.disconnect()

    store = get_runtime_store()
    connection = store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id))
    if connection is not None:
        updated = dict(connection)
        updated["username"] = None
        updated["last_verified_at"] = datetime.now(UTC).isoformat()
        store.put(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id), updated)


def _assert_live_profile_matches_review(profile, profile_config: dict, *, username_removed: bool) -> None:
    if profile.display_name != EXPECTED_DISPLAY_NAME:
        raise ValueError("Live Telegram display name no longer matches the reviewed profile")
    if _normal_text(profile.about) != _normal_text(str(profile_config["about"])):
        raise ValueError("Live Telegram bio no longer matches the reviewed profile")
    if profile.avatar is None:
        raise ValueError("Live Telegram profile has no avatar")
    if username_removed and profile.username is not None:
        raise ValueError("Live Telegram public username was not removed")


async def run(args: argparse.Namespace) -> dict:
    if args.confirm != CONFIRMATION:
        raise ValueError(f"Exact confirmation is required: {CONFIRMATION}")

    _validate_target(args)
    profile_config = _load_config(args.profile_config)
    story_config = _load_story_config(args.review_config)
    source_avatar = _avatar_bytes(profile_config, config_path=args.profile_config)
    hq_avatar = prepare_avatar_for_telegram(source_avatar)
    story_media = prepare_story_media(source_avatar)

    store = get_runtime_store()
    profile_key = _marker_key(args.project_id, PROFILE_OPERATION_ID)
    story_key = _marker_key(args.project_id, STORY_OPERATION_ID)

    live = await customer_telegram_client_publish_service.profile_internal(args.project_id)
    _assert_live_profile_matches_review(live, profile_config, username_removed=False)

    profile_marker = store.get(PROFILE_MARKER_NAMESPACE, profile_key)
    if profile_marker is None:
        username_before = live.username
        await customer_telegram_client_publish_service.update_profile_avatar_internal(
            args.project_id,
            hq_avatar,
            filename="femdom-avatar-reviewed-hq-v1.jpg",
        )
        await _remove_public_username(args.project_id)
        live = await customer_telegram_client_publish_service.profile_internal(args.project_id)
        _assert_live_profile_matches_review(live, profile_config, username_removed=True)
        profile_marker = {
            "status": "APPLIED",
            "operation_id": PROFILE_OPERATION_ID,
            "project_id": str(args.project_id),
            "username_before": username_before,
            "username_after": None,
            "avatar_width": AVATAR_SIZE[0],
            "avatar_height": AVATAR_SIZE[1],
            "avatar_source_sha256": str(profile_config["avatar_sha256"]),
            "applied_at": datetime.now(UTC).isoformat(),
            "community_content_published": False,
        }
        store.put(PROFILE_MARKER_NAMESPACE, profile_key, profile_marker)
    else:
        _assert_live_profile_matches_review(live, profile_config, username_removed=True)

    story_marker = store.get(STORY_MARKER_NAMESPACE, story_key)
    if story_marker is None:
        story = await customer_telegram_client_publish_service.publish_story_internal(
            args.project_id,
            content=story_media,
            filename="femdom-story-reviewed-v1.jpg",
            caption=story_config["copy"],
            period_seconds=86400,
            noforwards=False,
        )
        story_marker = {
            "status": "PUBLISHED",
            "operation_id": STORY_OPERATION_ID,
            "project_id": str(args.project_id),
            "story_id": story.story_id,
            "caption": story_config["copy"],
            "published_at": story.published_at.isoformat(),
            "period_seconds": 86400,
            "story_width": STORY_SIZE[0],
            "story_height": STORY_SIZE[1],
            "community_content_published": False,
        }
        store.put(STORY_MARKER_NAMESPACE, story_key, story_marker)

    return {
        "status": "DONE",
        "profile": profile_marker,
        "story": story_marker,
        "comment_published": False,
        "reply_published": False,
        "message_published": False,
    }


def main() -> int:
    args = build_parser().parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

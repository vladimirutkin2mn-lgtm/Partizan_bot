from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID

from PIL import Image

from app.customer_telegram_profile_apply_reviewed import _validate_target
from app.runtime_store import get_runtime_store
from app.telegram_client_publishing import customer_telegram_client_publish_service

CONFIRMATION = "REPLACE_FEMDOM_STORY_WITH_APPROVED_CREATIVE_V1"
MARKER_NAMESPACE = "reviewed_customer_telegram_story_replacement"
OPERATION_ID = "femdom-story-approved-creative-replacement-2026-10-08-v1"
OLD_STORY_ID = 1
EXPECTED_SIZE = (940, 1672)
EXPECTED_SHA256 = "4de4ccb6628a309808b1ac69f8cb771f134512fcaa3e60c737f87dd08d93eb37"
BASE64_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)
ASSET_PARTS = (
    "ops/femdom/story_approved_q85_v1.webp.b64.part01",
    "ops/femdom/story_approved_q85_v1.webp.b64.part02",
    "ops/femdom/story_approved_q85_v1.webp.b64.part03",
    "ops/femdom/story_approved_q85_v1.webp.b64.part04",
    "ops/femdom/story_approved_q85_v1.webp.b64.part05",
    "ops/femdom/story_approved_q85_v1.webp.b64.part06",
    "ops/femdom/story_approved_q85_v1.webp.b64.part07",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replace only the previously published reviewed FemDom Telegram story with the "
            "user-approved creative. This command cannot mutate the profile and has no "
            "comment, reply or message publishing path."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _asset_bytes() -> bytes:
    root = _repository_root()
    raw_encoded = "".join(
        (root / path).read_text(encoding="ascii")
        for path in ASSET_PARTS
    )
    encoded = "".join(character for character in raw_encoded if character in BASE64_ALPHABET)
    if not encoded:
        raise ValueError("Approved story asset is empty")
    content = base64.b64decode(encoded, validate=True)
    if not content.startswith(b"RIFF") or content[8:12] != b"WEBP":
        raise ValueError("Approved story asset is not a valid WebP payload")
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != EXPECTED_SHA256:
        raise ValueError("Approved story asset hash does not match the reviewed creative")
    with Image.open(BytesIO(content)) as image:
        image.load()
        if image.size != EXPECTED_SIZE:
            raise ValueError(
                f"Approved story creative size changed: {image.size} != {EXPECTED_SIZE}"
            )
    return content


def _telegram_photo_bytes(content: bytes) -> bytes:
    """Encode the reviewed pixels as a Telegram photo without cropping or resizing.

    Telegram stories sent as InputMediaUploadedPhoto reject WebP payloads. The reviewed
    creative remains the source of truth; this conversion only changes the transport
    container to JPEG and keeps the exact canvas, composition and embedded copy.
    """
    with Image.open(BytesIO(content)) as source:
        source.load()
        if source.size != EXPECTED_SIZE:
            raise ValueError(
                f"Approved story creative size changed: {source.size} != {EXPECTED_SIZE}"
            )
        image = source.convert("RGB")
        output = BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=98,
            subsampling=0,
            optimize=True,
            progressive=True,
        )
    result = output.getvalue()
    if not result.startswith(b"\xff\xd8\xff"):
        raise ValueError("Telegram story transport payload is not a JPEG")
    with Image.open(BytesIO(result)) as verify:
        verify.load()
        if verify.size != EXPECTED_SIZE:
            raise ValueError("Telegram story transport changed the creative dimensions")
    return result


def _marker_key(project_id: UUID) -> str:
    return f"{project_id}:{OPERATION_ID}"


async def run(args: argparse.Namespace) -> dict:
    if args.confirm != CONFIRMATION:
        raise ValueError(f"Exact confirmation is required: {CONFIRMATION}")

    _validate_target(args)
    content = _asset_bytes()
    asset_sha256 = hashlib.sha256(content).hexdigest()
    telegram_content = _telegram_photo_bytes(content)
    telegram_sha256 = hashlib.sha256(telegram_content).hexdigest()
    store = get_runtime_store()
    key = _marker_key(args.project_id)
    marker = store.get(MARKER_NAMESPACE, key)

    if marker is not None and str(marker.get("status") or "") == "REPLACED":
        return {
            **marker,
            "status": "ALREADY_REPLACED",
            "profile_mutated": False,
            "comment_published": False,
            "reply_published": False,
            "message_published": False,
        }

    if marker is None:
        await customer_telegram_client_publish_service.delete_story_internal(
            args.project_id,
            OLD_STORY_ID,
        )
        marker = {
            "status": "OLD_STORY_DELETED",
            "operation_id": OPERATION_ID,
            "project_id": str(args.project_id),
            "old_story_id": OLD_STORY_ID,
            "asset_sha256": asset_sha256,
            "asset_width": EXPECTED_SIZE[0],
            "asset_height": EXPECTED_SIZE[1],
            "old_story_deleted_at": datetime.now(UTC).isoformat(),
            "profile_mutated": False,
            "community_content_published": False,
        }
        store.put(MARKER_NAMESPACE, key, marker)
    elif str(marker.get("asset_sha256") or "") != asset_sha256:
        raise ValueError("Approved story asset changed after the replacement operation started")

    story = await customer_telegram_client_publish_service.publish_story_internal(
        args.project_id,
        content=telegram_content,
        filename="femdom-story-approved-v1.jpg",
        caption="",
        period_seconds=86400,
        noforwards=False,
    )
    completed = {
        **marker,
        "status": "REPLACED",
        "new_story_id": story.story_id,
        "published_at": story.published_at.isoformat(),
        "period_seconds": 86400,
        "caption": "",
        "telegram_upload_format": "JPEG",
        "telegram_upload_sha256": telegram_sha256,
        "profile_mutated": False,
        "community_content_published": False,
        "comment_published": False,
        "reply_published": False,
        "message_published": False,
    }
    store.put(MARKER_NAMESPACE, key, completed)
    return completed


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
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

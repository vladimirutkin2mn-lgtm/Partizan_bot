"""Optional, read-only evidence from the selected public photo and one explicit link."""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from io import BytesIO

from PIL import Image
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession

from app.config import get_settings
from app.runtime_store import get_runtime_store
from app.telegram_native_flood_wait import record_research_flood_wait, research_flood_wait_until
from app.telegram_public_post_links import canonical_public_post, referenced_public_posts

_log = logging.getLogger(__name__)
_MAX_THUMB_BYTES = 240_000


@dataclass(frozen=True, slots=True)
class SelectedPhoto:
    jpeg: bytes | None = None
    linked_url: str | None = None
    linked_text: str = ""

    def prompt_context(self) -> dict:
        return {
            "kind": "photo",
            "image_attached": self.jpeg is not None,
            # The draft cache hashes this JSON. Different photo bytes are different inputs.
            "image_sha256": hashlib.sha256(self.jpeg).hexdigest() if self.jpeg else None,
            "linked_background": (
                {"url": self.linked_url, "text": self.linked_text} if self.linked_text else None
            ),
        }


async def _thumbnail(client, message) -> bytes | None:
    sizes = [
        size
        for size in message.photo.sizes
        if 0 < getattr(size, "w", 0) <= 900
        and 0 < getattr(size, "h", 0) <= 900
        and 0 < getattr(size, "size", 0) <= _MAX_THUMB_BYTES
    ]
    if not sizes:
        return None
    thumb = max(sizes, key=lambda size: size.w * size.h)
    raw = await client.download_media(message, file=bytes, thumb=thumb)
    if not raw or len(raw) > _MAX_THUMB_BYTES:
        return None
    with Image.open(BytesIO(raw)) as img:
        if img.format != "JPEG" or max(img.size) > 900:
            return None
        img.verify()
    return raw


async def _read_photo(client, url: str, caption: str) -> SelectedPhoto:
    handle, number = url.split("/")[-2:]
    peer = await client.get_entity("@" + handle)
    if str(getattr(peer, "username", "") or "").casefold() != handle:
        return SelectedPhoto()
    message = await client.get_messages(peer, ids=int(number))
    if (
        getattr(message, "id", None) != int(number)
        or not getattr(message, "photo", None)
        or getattr(message, "noforwards", False)
        or getattr(peer, "noforwards", False)
    ):
        return SelectedPhoto()
    # An edited post must not mix today's pixels with an unrelated saved caption.
    native_caption = str(getattr(message, "message", "") or "").strip()
    if native_caption and native_caption != caption.strip():
        return SelectedPhoto()
    jpeg = await _thumbnail(client, message)
    prefix = f"https://t.me/{handle}/"
    for linked in referenced_public_posts(message, own_handle=""):
        if not linked.startswith(prefix) or linked == url:
            continue
        linked_id = int(linked.rsplit("/", 1)[-1])
        background = await client.get_messages(peer, ids=linked_id)
        if getattr(background, "id", None) == linked_id and not getattr(background, "noforwards", False):
            body = str(getattr(background, "message", "") or "").strip()
            return SelectedPhoto(jpeg, linked, body[:750])
        break
    return SelectedPhoto(jpeg)


async def read_selected_photo(url: str, caption: str) -> SelectedPhoto:
    canonical = canonical_public_post(url)
    settings = get_settings()
    if (
        not canonical
        or not settings.telegram_research_public_ready
        or settings.telegram_research_provider != "telethon"
        or not settings.telegram_research_api_id
        or not settings.telegram_research_api_hash
        or not settings.telegram_research_session
    ):
        return SelectedPhoto()
    store = get_runtime_store()
    if research_flood_wait_until(store) is not None:
        return SelectedPhoto()
    try:
        client = TelegramClient(
            StringSession(settings.telegram_research_session.get_secret_value()),
            settings.telegram_research_api_id,
            settings.telegram_research_api_hash.get_secret_value(),
            connection_retries=0,
            request_retries=0,
            flood_sleep_threshold=0,
        )
        try:
            async with asyncio.timeout(12):
                await client.connect()
                if await client.is_user_authorized():
                    return await _read_photo(client, canonical, caption)
        finally:
            await client.disconnect()
    except FloodWaitError as exc:
        record_research_flood_wait(store, exc.seconds)
    except (RPCError, OSError, ValueError, Image.DecompressionBombError) as exc:
        _log.info("Telegram writer photo unavailable (%s)", type(exc).__name__)
    return SelectedPhoto()

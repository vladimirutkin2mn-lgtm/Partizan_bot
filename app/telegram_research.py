from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from app.config import Settings, get_settings

ACTION_TARGET_FRESHNESS_SECONDS = 14 * 24 * 60 * 60


class TelegramResearchError(RuntimeError):
    pass


class TelegramResearchUnavailableError(TelegramResearchError):
    pass


class TelegramSurfaceKind(StrEnum):
    CHANNEL = "CHANNEL"
    GROUP = "GROUP"


class SurfaceAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TelegramRecentContext:
    message_id: int
    text: str
    published_at: datetime | None
    url: str | None
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TelegramCommunitySnapshot:
    entity_id: int
    username: str
    title: str
    kind: TelegramSurfaceKind
    url: str
    about: str = ""
    member_count: int | None = None
    source_checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime | None = None
    comment_surface: SurfaceAvailability = SurfaceAvailability.UNKNOWN
    reply_surface: SurfaceAvailability = SurfaceAvailability.UNKNOWN
    standalone_post_surface: SurfaceAvailability = SurfaceAvailability.UNKNOWN
    linked_discussion_id: int | None = None
    action_target_url: str | None = None
    recent_context: tuple[TelegramRecentContext, ...] = ()


class TelegramResearchTransport(Protocol):
    async def discover_public(
        self,
        query: str,
        known_handles: list[str],
        *,
        result_limit: int,
        recent_message_limit: int,
    ) -> list[TelegramCommunitySnapshot]: ...


class UnavailableTelegramResearchTransport:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def discover_public(
        self,
        query: str,
        known_handles: list[str],
        *,
        result_limit: int,
        recent_message_limit: int,
    ) -> list[TelegramCommunitySnapshot]:
        del query, known_handles, result_limit, recent_message_limit
        raise TelegramResearchUnavailableError(self._reason)


class TelegramResearchConnector:
    """Bounded, read-only Telegram research boundary.

    This type deliberately has no send/join/invite methods. A research session can
    discover and inspect public communities, but it cannot authorize publishing.
    """

    def __init__(
        self,
        transport: TelegramResearchTransport,
        *,
        result_limit: int = 5,
        known_handle_limit: int = 3,
        recent_message_limit: int = 4,
        query_length_limit: int = 96,
    ) -> None:
        self._transport = transport
        self._result_limit = max(1, min(result_limit, 10))
        self._known_handle_limit = max(0, min(known_handle_limit, 5))
        self._recent_message_limit = max(0, min(recent_message_limit, 8))
        self._query_length_limit = max(16, min(query_length_limit, 160))
        self._lock = asyncio.Lock()

    async def discover(
        self,
        *,
        query: str,
        known_handles: list[str] | None = None,
    ) -> list[TelegramCommunitySnapshot]:
        normalized_query = " ".join(query.split()).strip()[: self._query_length_limit]
        if not normalized_query:
            return []
        normalized_handles: list[str] = []
        seen: set[str] = set()
        for raw in known_handles or []:
            handle = raw.strip().lstrip("@").lower()
            if not handle or handle in seen:
                continue
            seen.add(handle)
            normalized_handles.append(handle)
            if len(normalized_handles) >= self._known_handle_limit:
                break
        async with self._lock:
            snapshots = await self._transport.discover_public(
                normalized_query,
                normalized_handles,
                result_limit=self._result_limit,
                recent_message_limit=self._recent_message_limit,
            )
        deduped: dict[int, TelegramCommunitySnapshot] = {}
        for snapshot in snapshots:
            existing = deduped.get(snapshot.entity_id)
            if existing is None or self._freshness_key(snapshot) > self._freshness_key(existing):
                deduped[snapshot.entity_id] = snapshot
        return sorted(
            deduped.values(),
            key=lambda item: (self._freshness_key(item), item.entity_id),
            reverse=True,
        )[: self._result_limit + self._known_handle_limit]

    def _freshness_key(self, snapshot: TelegramCommunitySnapshot) -> datetime:
        return snapshot.last_activity_at or datetime.min.replace(tzinfo=UTC)


class TelethonTelegramResearchTransport:
    """Authorised user-session transport for public Telegram research only."""

    def __init__(self, *, api_id: int, api_hash: str, session_string: str) -> None:
        if api_id <= 0 or not api_hash.strip() or not session_string.strip():
            raise ValueError("Telegram research credentials are incomplete")
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_string = session_string

    async def discover_public(
        self,
        query: str,
        known_handles: list[str],
        *,
        result_limit: int,
        recent_message_limit: int,
    ) -> list[TelegramCommunitySnapshot]:
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            from telethon.tl.functions.channels import GetFullChannelRequest
            from telethon.tl.functions.contacts import SearchRequest
            from telethon.tl.types import Channel
        except ImportError as exc:  # pragma: no cover - dependency contract
            raise TelegramResearchUnavailableError(
                "Telethon is not installed for Telegram research"
            ) from exc

        try:
            session = StringSession(self._session_string)
            client = TelegramClient(session, self._api_id, self._api_hash)
        except Exception as exc:
            raise TelegramResearchUnavailableError(
                "Telegram research session could not be initialized"
            ) from exc

        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise TelegramResearchUnavailableError(
                    "Telegram research session is not authorized"
                )

            entities: dict[int, object] = {}
            search_result = await client(SearchRequest(q=query, limit=result_limit))
            for entity in getattr(search_result, "chats", []) or []:
                if isinstance(entity, Channel) and self._public_username(entity):
                    entities[int(entity.id)] = entity

            # Known public handles come from external evidence such as web search. Resolve
            # only a very small bounded set because Telegram username resolution is rate-limited.
            for handle in known_handles:
                try:
                    entity = await client.get_entity(f"@{handle}")
                except (ValueError, TypeError):
                    continue
                if isinstance(entity, Channel) and self._public_username(entity):
                    entities[int(entity.id)] = entity

            snapshots: list[TelegramCommunitySnapshot] = []
            for entity in list(entities.values())[: result_limit + len(known_handles)]:
                try:
                    input_entity = await client.get_input_entity(entity)
                    full = await client(GetFullChannelRequest(input_entity))
                    messages = await client.get_messages(
                        input_entity,
                        limit=recent_message_limit,
                    )
                    snapshots.append(
                        self._snapshot(
                            entity,
                            full,
                            list(messages or []),
                            query=query,
                        )
                    )
                except (ValueError, TypeError):
                    continue
            return snapshots
        except TelegramResearchUnavailableError:
            raise
        except Exception as exc:
            # Provider errors are surfaced to Audience Intelligence. Generic web evidence
            # may still be retained, but native Telegram evidence is never fabricated.
            raise TelegramResearchUnavailableError(
                f"Telegram native research failed: {type(exc).__name__}"
            ) from exc
        finally:
            await client.disconnect()

    def _snapshot(
        self,
        entity: object,
        full: object,
        messages: list[object],
        *,
        query: str,
    ) -> TelegramCommunitySnapshot:
        username = self._public_username(entity)
        if not username:
            raise ValueError("Telegram community has no public username")
        is_group = bool(getattr(entity, "megagroup", False))
        is_channel = bool(getattr(entity, "broadcast", False))
        if not (is_group or is_channel):
            raise ValueError("Telegram entity is not a supported public channel/group")

        full_chat = getattr(full, "full_chat", None)
        linked_discussion_id = getattr(full_chat, "linked_chat_id", None)
        kind = TelegramSurfaceKind.GROUP if is_group else TelegramSurfaceKind.CHANNEL
        canonical_url = f"https://t.me/{username}"
        checked_at = datetime.now(UTC)
        terms = self._tokens(query)
        recent: list[TelegramRecentContext] = []
        for message in messages:
            text = str(getattr(message, "message", "") or "").strip()
            message_id = int(getattr(message, "id", 0) or 0)
            if not message_id:
                continue
            published_at = self._aware(getattr(message, "date", None))
            message_url = f"{canonical_url}/{message_id}"
            matched = tuple(sorted(terms & self._tokens(text))[:12])
            recent.append(
                TelegramRecentContext(
                    message_id=message_id,
                    text=text[:1200],
                    published_at=published_at,
                    url=message_url,
                    matched_terms=matched,
                )
            )

        relevant_context = next(
            (
                item
                for item in recent
                if item.matched_terms
                and item.published_at is not None
                and 0
                <= (checked_at - item.published_at).total_seconds()
                <= ACTION_TARGET_FRESHNESS_SECONDS
            ),
            None,
        )
        action_target_url: str | None = None
        if relevant_context is not None:
            if is_group or linked_discussion_id is not None:
                action_target_url = relevant_context.url

        last_activity_at = max(
            (item.published_at for item in recent if item.published_at is not None),
            default=None,
        )
        participants = getattr(full_chat, "participants_count", None)
        if participants is None:
            participants = getattr(entity, "participants_count", None)

        return TelegramCommunitySnapshot(
            entity_id=int(entity.id),
            username=username,
            title=str(getattr(entity, "title", username) or username)[:300],
            kind=kind,
            url=canonical_url,
            about=str(getattr(full_chat, "about", "") or "")[:2000],
            member_count=(int(participants) if participants is not None else None),
            source_checked_at=checked_at,
            last_activity_at=last_activity_at,
            comment_surface=(
                SurfaceAvailability.AVAILABLE
                if is_channel and linked_discussion_id is not None
                else SurfaceAvailability.UNAVAILABLE
                if is_channel
                else SurfaceAvailability.UNKNOWN
            ),
            reply_surface=(
                SurfaceAvailability.AVAILABLE if is_group else SurfaceAvailability.UNKNOWN
            ),
            standalone_post_surface=(
                SurfaceAvailability.AVAILABLE if is_group else SurfaceAvailability.UNAVAILABLE
            ),
            linked_discussion_id=(
                int(linked_discussion_id) if linked_discussion_id is not None else None
            ),
            action_target_url=action_target_url,
            recent_context=tuple(recent),
        )

    def _public_username(self, entity: object) -> str | None:
        username = str(getattr(entity, "username", "") or "").strip().lstrip("@")
        return username or None

    def _aware(self, value: object) -> datetime | None:
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _tokens(self, text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text.lower())
            if len(token) > 2
        }


def get_telegram_research_connector(
    settings: Settings | None = None,
) -> TelegramResearchConnector | None:
    settings = settings or get_settings()
    if not settings.telegram_research_public_ready:
        return None
    if settings.telegram_research_provider == "unavailable":
        return TelegramResearchConnector(
            UnavailableTelegramResearchTransport(
                "TELEGRAM_RESEARCH_PROVIDER is unavailable while public readiness is enabled"
            )
        )
    if settings.telegram_research_provider != "telethon":
        return TelegramResearchConnector(
            UnavailableTelegramResearchTransport(
                f"Unsupported Telegram research provider: {settings.telegram_research_provider}"
            )
        )

    api_hash = settings.telegram_research_api_hash
    session = settings.telegram_research_session
    if settings.telegram_research_api_id is None or api_hash is None or session is None:
        return TelegramResearchConnector(
            UnavailableTelegramResearchTransport(
                "Telegram research is marked ready but API/session secrets are incomplete"
            )
        )
    try:
        transport: TelegramResearchTransport = TelethonTelegramResearchTransport(
            api_id=settings.telegram_research_api_id,
            api_hash=api_hash.get_secret_value(),
            session_string=session.get_secret_value(),
        )
    except ValueError as exc:
        transport = UnavailableTelegramResearchTransport(str(exc))
    return TelegramResearchConnector(
        transport,
        result_limit=settings.telegram_research_result_limit,
        known_handle_limit=settings.telegram_research_known_handle_limit,
        recent_message_limit=settings.telegram_research_recent_message_limit,
    )

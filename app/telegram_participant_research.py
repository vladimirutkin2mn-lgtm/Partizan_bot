from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from app.config import Settings, get_settings
from app.distribution_types import DistributionPlatform

ParticipantRole = Literal["AUTHOR", "COMMENTER"]


@dataclass(frozen=True, slots=True)
class TelegramParticipantObservation:
    """Ephemeral participant observation used only inside the research process.

    actor_id is deliberately never serialized into opportunity metadata. It exists only so
    the service can count distinct/repeated public participants before discarding identity.
    """

    actor_id: int
    role: ParticipantRole
    text: str
    published_at: datetime | None
    is_bot: bool = False


class TelegramParticipantResearchTransport(Protocol):
    async def observe(
        self,
        handles: list[str],
        *,
        message_limit: int,
    ) -> dict[str, tuple[TelegramParticipantObservation, ...]]: ...


class TelegramParticipantResearchService:
    """Attach aggregate audience-participation evidence without persisting identities."""

    def __init__(
        self,
        transport: TelegramParticipantResearchTransport | None = None,
        *,
        use_default_transport: bool = True,
        max_communities: int = 8,
        message_limit: int = 8,
    ) -> None:
        self._transport = transport
        if transport is None and use_default_transport:
            self._transport = get_telegram_participant_research_transport()
        self._max_communities = max(1, min(max_communities, 12))
        self._message_limit = max(1, min(message_limit, 12))

    async def enrich(self, opportunities: list[Any], icps: list[Any]) -> list[Any]:
        transport = self._transport
        if transport is None or not opportunities:
            return list(opportunities)

        icp_terms = {
            str(getattr(icp, "id", "")): self._icp_terms(icp)
            for icp in icps
        }
        handles = self._ranked_handles(opportunities)
        if not handles:
            return list(opportunities)
        try:
            observed = await transport.observe(
                handles,
                message_limit=self._message_limit,
            )
        except Exception:
            # Participant evidence is optional research. Generic/native discovery evidence
            # remains valid if this bounded enrichment source is unavailable.
            return list(opportunities)

        result: list[Any] = []
        checked_at = datetime.now(UTC).isoformat()
        for opportunity in opportunities:
            metadata = self._metadata(opportunity)
            handle = self._handle(metadata)
            observations = tuple(observed.get(handle.casefold(), ())) if handle else ()
            if not observations:
                result.append(opportunity)
                continue
            evidence = self._aggregate(
                observations,
                icp_terms.get(str(getattr(opportunity, "icp_id", "")), set()),
                checked_at=checked_at,
            )
            metadata["participant_evidence"] = evidence
            result.append(self._copy_with_metadata(opportunity, metadata))
        return result

    def _ranked_handles(self, opportunities: list[Any]) -> list[str]:
        ranked = sorted(
            opportunities,
            key=lambda item: (
                -(float(getattr(item, "relevance_score", 0.0) or 0.0)),
                str(getattr(item, "canonical_key", "")),
            ),
        )
        result: list[str] = []
        seen: set[str] = set()
        for opportunity in ranked:
            platform = str(
                getattr(
                    getattr(opportunity, "platform", None),
                    "value",
                    getattr(opportunity, "platform", ""),
                )
            ).upper()
            if platform != DistributionPlatform.TELEGRAM.value:
                continue
            handle = self._handle(self._metadata(opportunity))
            if not handle:
                continue
            key = handle.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(handle)
            if len(result) >= self._max_communities:
                break
        return result

    @classmethod
    def _aggregate(
        cls,
        observations: tuple[TelegramParticipantObservation, ...],
        target_terms: set[str],
        *,
        checked_at: str,
    ) -> dict:
        usable = [item for item in observations if item.actor_id and not item.is_bot]
        author_ids = {item.actor_id for item in usable if item.role == "AUTHOR"}
        commenter_ids = {item.actor_id for item in usable if item.role == "COMMENTER"}
        matched = [
            item
            for item in usable
            if target_terms and bool(cls._tokens(item.text) & target_terms)
        ]
        matched_author_ids = {item.actor_id for item in matched if item.role == "AUTHOR"}
        matched_commenter_ids = {
            item.actor_id for item in matched if item.role == "COMMENTER"
        }
        activity = Counter(item.actor_id for item in usable)
        repeat_participants = sum(1 for count in activity.values() if count >= 2)
        distinct_participants = author_ids | commenter_ids
        matched_participants = matched_author_ids | matched_commenter_ids
        ratio = (
            len(matched_participants) / len(distinct_participants)
            if distinct_participants
            else 0.0
        )
        return {
            "status": "OBSERVED",
            "source": "TELEGRAM_PUBLIC_RESEARCH",
            "source_checked_at": checked_at,
            "observed_message_count": len(usable),
            "matched_message_count": len(matched),
            "distinct_participant_count": len(distinct_participants),
            "distinct_author_count": len(author_ids),
            "distinct_commenter_count": len(commenter_ids),
            "matched_participant_count": len(matched_participants),
            "matched_author_count": len(matched_author_ids),
            "matched_commenter_count": len(matched_commenter_ids),
            "repeat_participant_count": repeat_participants,
            "matched_participant_ratio": round(ratio, 3),
            "identity_persisted": False,
            "participant_ids_persisted": False,
            "usernames_persisted": False,
            "execution_permission_inferred": False,
        }

    @classmethod
    def _icp_terms(cls, icp: Any) -> set[str]:
        values = [
            getattr(icp, "title", ""),
            getattr(icp, "description", ""),
            getattr(icp, "pain", ""),
            getattr(icp, "trigger", ""),
            getattr(icp, "desired_outcome", ""),
            " ".join(getattr(icp, "alternatives", []) or []),
        ]
        terms: set[str] = set()
        for value in values:
            terms.update(cls._tokens(str(value or "")))
        return terms

    @staticmethod
    def _tokens(value: str) -> set[str]:
        stopwords = {
            "about", "advice", "channel", "community", "group", "help", "people",
            "telegram", "with", "для", "или", "люди", "советы", "сообщество", "чат",
        }
        return {
            token
            for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", value.casefold())
            if len(token) > 3 and not token.isdigit() and token not in stopwords
        }

    @staticmethod
    def _handle(metadata: dict) -> str:
        return str(metadata.get("handle") or "").strip().lstrip("@")

    @staticmethod
    def _metadata(item: Any) -> dict:
        value = getattr(item, "metadata", {})
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _copy_with_metadata(item: Any, metadata: dict) -> Any:
        if hasattr(item, "model_copy"):
            return item.model_copy(update={"metadata": metadata})
        try:
            item.metadata = metadata
        except Exception:
            pass
        return item


class TelethonTelegramParticipantResearchTransport:
    """Bounded read-only participant research over public Telegram surfaces."""

    def __init__(self, *, api_id: int, api_hash: str, session_string: str) -> None:
        if api_id <= 0 or not api_hash.strip() or not session_string.strip():
            raise ValueError("Telegram participant research credentials are incomplete")
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_string = session_string
        self._lock = asyncio.Lock()

    async def observe(
        self,
        handles: list[str],
        *,
        message_limit: int,
    ) -> dict[str, tuple[TelegramParticipantObservation, ...]]:
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            from telethon.tl.functions.channels import GetFullChannelRequest
            from telethon.tl.types import Channel
        except ImportError:
            return {}

        session = StringSession(self._session_string)
        client = TelegramClient(session, self._api_id, self._api_hash)
        async with self._lock:
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    return {}
                result: dict[str, tuple[TelegramParticipantObservation, ...]] = {}
                for raw_handle in handles:
                    handle = str(raw_handle or "").strip().lstrip("@")
                    if not handle:
                        continue
                    try:
                        entity = await client.get_entity(f"@{handle}")
                        if not isinstance(entity, Channel):
                            continue
                        observations: list[TelegramParticipantObservation] = []
                        if bool(getattr(entity, "megagroup", False)):
                            messages = await client.get_messages(entity, limit=message_limit)
                            observations.extend(
                                self._message_observations(
                                    list(messages or []),
                                    role="AUTHOR",
                                )
                            )
                        elif bool(getattr(entity, "broadcast", False)):
                            input_entity = await client.get_input_entity(entity)
                            full = await client(GetFullChannelRequest(input_entity))
                            linked_id = getattr(
                                getattr(full, "full_chat", None),
                                "linked_chat_id",
                                None,
                            )
                            linked = self._linked_discussion(
                                getattr(full, "chats", []) or [],
                                linked_id,
                            )
                            if linked is not None:
                                messages = await client.get_messages(
                                    linked,
                                    limit=message_limit,
                                )
                                observations.extend(
                                    self._message_observations(
                                        list(messages or []),
                                        role="COMMENTER",
                                    )
                                )
                        if observations:
                            result[handle.casefold()] = tuple(observations)
                    except (ValueError, TypeError):
                        continue
                return result
            except Exception:
                return {}
            finally:
                await client.disconnect()

    @staticmethod
    def _linked_discussion(chats: list[object], linked_id: object) -> object | None:
        if linked_id is None:
            return None
        try:
            normalized_id = int(linked_id)
        except (TypeError, ValueError):
            return None
        return next(
            (chat for chat in chats if int(getattr(chat, "id", 0) or 0) == normalized_id),
            None,
        )

    @classmethod
    def _message_observations(
        cls,
        messages: list[object],
        *,
        role: ParticipantRole,
    ) -> list[TelegramParticipantObservation]:
        result: list[TelegramParticipantObservation] = []
        for message in messages:
            text = str(getattr(message, "message", "") or "").strip()
            actor_id = int(getattr(message, "sender_id", 0) or 0)
            if not text or not actor_id:
                continue
            sender = getattr(message, "sender", None)
            result.append(
                TelegramParticipantObservation(
                    actor_id=actor_id,
                    role=role,
                    text=text[:1200],
                    published_at=cls._aware(getattr(message, "date", None)),
                    is_bot=bool(getattr(sender, "bot", False)),
                )
            )
        return result

    @staticmethod
    def _aware(value: object) -> datetime | None:
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def get_telegram_participant_research_transport(
    settings: Settings | None = None,
) -> TelegramParticipantResearchTransport | None:
    settings = settings or get_settings()
    if not settings.telegram_research_public_ready:
        return None
    if settings.telegram_research_provider != "telethon":
        return None
    api_hash = settings.telegram_research_api_hash
    session = settings.telegram_research_session
    if settings.telegram_research_api_id is None or api_hash is None or session is None:
        return None
    try:
        return TelethonTelegramParticipantResearchTransport(
            api_id=settings.telegram_research_api_id,
            api_hash=api_hash.get_secret_value(),
            session_string=session.get_secret_value(),
        )
    except ValueError:
        return None


telegram_participant_research_service = TelegramParticipantResearchService()

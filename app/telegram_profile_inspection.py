from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.users import GetFullUserRequest

from app.config import Settings, get_settings
from app.provider_secret_store import ProviderSecretStore, provider_secret_store
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    TelegramConnectionStatus,
)


class TelegramProfileInspectionError(RuntimeError):
    pass


class TelegramProfileSnapshot(BaseModel):
    user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    about: str = Field(default="", max_length=500)


class TelegramProfileReadTransport(Protocol):
    async def read_profile(self, *, session: str) -> TelegramProfileSnapshot: ...


class TelethonProfileReadTransport:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def read_profile(self, *, session: str) -> TelegramProfileSnapshot:
        client = self._client(session)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramProfileInspectionError("SESSION_NOT_AUTHORIZED")
            me = await client.get_me()
            if me is None or not getattr(me, "id", None):
                raise TelegramProfileInspectionError("IDENTITY_NOT_AVAILABLE")
            full = await client(GetFullUserRequest(me))
            about = str(getattr(getattr(full, "full_user", None), "about", "") or "")
            return TelegramProfileSnapshot(
                user_id=int(me.id),
                username=(
                    str(me.username)
                    if getattr(me, "username", None)
                    else None
                ),
                first_name=(
                    str(me.first_name)
                    if getattr(me, "first_name", None)
                    else None
                ),
                last_name=(
                    str(me.last_name)
                    if getattr(me, "last_name", None)
                    else None
                ),
                about=about,
            )
        except TelegramProfileInspectionError:
            raise
        except Exception as exc:
            raise TelegramProfileInspectionError(
                f"PROFILE_READ_FAILED:{type(exc).__name__}"
            ) from None
        finally:
            await client.disconnect()

    def _client(self, session: str) -> TelegramClient:
        api_id = self._settings.telegram_client_publish_api_id
        api_hash = self._settings.telegram_client_publish_api_hash
        if api_id is None or api_hash is None:
            raise TelegramProfileInspectionError("CLIENT_PUBLISH_API_NOT_CONFIGURED")
        return TelegramClient(
            StringSession(session),
            api_id,
            api_hash.get_secret_value(),
        )


class CustomerTelegramProfileInspectionService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        secret_store: ProviderSecretStore | None = None,
        transport: TelegramProfileReadTransport | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._secret_store = secret_store or provider_secret_store
        self._transport = transport or TelethonProfileReadTransport()

    async def inspect_internal(self, project_id: UUID) -> TelegramProfileSnapshot:
        connection = self._store.get(
            CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
            str(project_id),
        )
        if (
            connection is None
            or str(connection.get("status") or "")
            != TelegramConnectionStatus.ACTIVE.value
        ):
            raise TelegramProfileInspectionError(
                "An active customer-owned Telegram connection is required"
            )

        session_reference = str(connection.get("secret_reference") or "")
        session = self._secret_store.get(session_reference) if session_reference else None
        if session is None:
            raise TelegramProfileInspectionError(
                "The authorised Telegram session is no longer available"
            )

        snapshot = await self._transport.read_profile(session=session)
        expected_user_id = int(connection.get("telegram_user_id") or 0)
        if expected_user_id <= 0 or snapshot.user_id != expected_user_id:
            raise TelegramProfileInspectionError(
                "Telegram profile identity does not match the connected account"
            )
        return snapshot


customer_telegram_profile_inspection_service = (
    CustomerTelegramProfileInspectionService()
)

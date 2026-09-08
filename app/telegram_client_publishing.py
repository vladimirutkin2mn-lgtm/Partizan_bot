from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl, SecretStr, field_validator
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl import types as telegram_types

from app.channel_execution import PublisherMode
from app.config import Settings, get_settings
from app.customer_funnel import customer_funnel_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.provider_secret_store import (
    TELEGRAM_LOGIN_SECRET_PREFIX,
    TELEGRAM_SESSION_SECRET_PREFIX,
    ProviderSecretStore,
    provider_secret_store,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

CUSTOMER_TELEGRAM_LOGIN_NAMESPACE = "customer_telegram_login"
CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE = "customer_telegram_connection"
CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE = "customer_telegram_publish_receipt"
CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE = "customer_telegram_publish_guard"

_LOGIN_TTL = timedelta(minutes=10)
_DUPLICATE_WINDOW = timedelta(hours=24)
_DAILY_WINDOW = timedelta(hours=24)
_MIN_PUBLISH_INTERVAL = timedelta(seconds=60)
_MAX_PUBLISHES_PER_DAY = 10
_MAX_CONTENT_LENGTH = 4000
_PHONE_PATTERN = re.compile(r"^\+[1-9][0-9]{7,15}$")
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{5,32}$")


class CustomerTelegramClientPublishError(RuntimeError):
    pass


class TelegramClientPublishTransportError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        restriction_signal: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.restriction_signal = restriction_signal


class TelegramLoginStatus(StrEnum):
    CODE_SENT = "CODE_SENT"
    PASSWORD_REQUIRED = "PASSWORD_REQUIRED"


class TelegramConnectionStatus(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    ACTIVE = "ACTIVE"


class TelegramClientPublishOutcome(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class TelegramLoginStartRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=17)

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        normalized = value.strip().replace(" ", "").replace("-", "")
        if not _PHONE_PATTERN.fullmatch(normalized):
            raise ValueError("Use an international Telegram phone number such as +15551234567")
        return normalized


class TelegramLoginConfirmRequest(BaseModel):
    challenge_id: UUID
    code: SecretStr
    password: SecretStr | None = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: SecretStr) -> SecretStr:
        normalized = value.get_secret_value().strip()
        if not normalized or len(normalized) > 12:
            raise ValueError("Telegram login code is invalid")
        return SecretStr(normalized)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        normalized = value.get_secret_value()
        if not normalized or len(normalized) > 256:
            raise ValueError("Telegram two-factor password is invalid")
        return SecretStr(normalized)


class TelegramPublishRequest(BaseModel):
    retry: bool = False


class TelegramLoginChallengeView(BaseModel):
    challenge_id: UUID
    status: TelegramLoginStatus
    phone_hint: str
    expires_at: datetime


class TelegramConnectionView(BaseModel):
    status: TelegramConnectionStatus
    telegram_user_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    connected_at: datetime | None = None
    last_verified_at: datetime | None = None


class TelegramClientPublishReceipt(BaseModel):
    action_id: UUID
    outcome: TelegramClientPublishOutcome
    message: str
    external_reference: str | None = Field(default=None, max_length=500)
    executed_url: HttpUrl | None = None
    published_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class TelegramClientIdentity(BaseModel):
    user_id: int
    username: str | None = None
    display_name: str | None = None


class TelegramLoginStartResult(BaseModel):
    session: str
    phone_code_hash: str


class TelegramLoginCompleteResult(BaseModel):
    session: str
    password_required: bool = False
    identity: TelegramClientIdentity | None = None


class TelegramPublishResult(BaseModel):
    peer_id: int
    message_id: int
    published_at: datetime
    executed_url: HttpUrl


@dataclass(frozen=True)
class TelegramPublishTarget:
    username: str
    reply_to_message_id: int | None


class TelegramClientPublishTransport(Protocol):
    async def begin_login(self, phone_number: str) -> TelegramLoginStartResult: ...

    async def complete_login(
        self,
        *,
        session: str,
        phone_number: str,
        phone_code_hash: str,
        code: str,
        password: str | None,
    ) -> TelegramLoginCompleteResult: ...

    async def publish(
        self,
        *,
        session: str,
        target: TelegramPublishTarget,
        action_type: DistributionActionType,
        text: str,
    ) -> TelegramPublishResult: ...


class TelethonClientPublishTransport:
    """Telethon user-session transport with no join, invite, participant or DM operations."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def begin_login(self, phone_number: str) -> TelegramLoginStartResult:
        client = self._client()
        try:
            await client.connect()
            sent = await client.send_code_request(phone_number)
            return TelegramLoginStartResult(
                session=client.session.save(),
                phone_code_hash=str(sent.phone_code_hash),
            )
        except Exception as exc:
            raise self._safe_error(exc, "LOGIN_START_FAILED") from None
        finally:
            await client.disconnect()

    async def complete_login(
        self,
        *,
        session: str,
        phone_number: str,
        phone_code_hash: str,
        code: str,
        password: str | None,
    ) -> TelegramLoginCompleteResult:
        client = self._client(session)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                try:
                    await client.sign_in(
                        phone=phone_number,
                        code=code,
                        phone_code_hash=phone_code_hash,
                    )
                except SessionPasswordNeededError:
                    if password is None:
                        return TelegramLoginCompleteResult(
                            session=client.session.save(),
                            password_required=True,
                        )
                    await client.sign_in(password=password)
            me = await client.get_me()
            if me is None or not getattr(me, "id", None):
                raise TelegramClientPublishTransportError("IDENTITY_NOT_AVAILABLE")
            first_name = str(getattr(me, "first_name", "") or "").strip()
            last_name = str(getattr(me, "last_name", "") or "").strip()
            display_name = " ".join(part for part in (first_name, last_name) if part) or None
            return TelegramLoginCompleteResult(
                session=client.session.save(),
                identity=TelegramClientIdentity(
                    user_id=int(me.id),
                    username=(str(me.username) if getattr(me, "username", None) else None),
                    display_name=display_name,
                ),
            )
        except TelegramClientPublishTransportError:
            raise
        except Exception as exc:
            raise self._safe_error(exc, "LOGIN_CONFIRM_FAILED") from None
        finally:
            await client.disconnect()

    async def publish(
        self,
        *,
        session: str,
        target: TelegramPublishTarget,
        action_type: DistributionActionType,
        text: str,
    ) -> TelegramPublishResult:
        client = self._client(session)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramClientPublishTransportError("SESSION_NOT_AUTHORIZED")
            entity = await client.get_entity(f"@{target.username}")
            if not isinstance(entity, telegram_types.Channel):
                raise TelegramClientPublishTransportError("COMMUNITY_TARGET_REQUIRED")
            message = await client.send_message(
                entity,
                text,
                reply_to=target.reply_to_message_id,
            )
            published_at = message.date
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
            return TelegramPublishResult(
                peer_id=int(entity.id),
                message_id=int(message.id),
                published_at=published_at,
                executed_url=f"https://t.me/{target.username}/{message.id}",
            )
        except TelegramClientPublishTransportError:
            raise
        except Exception as exc:
            raise self._safe_error(exc, "PUBLISH_FAILED") from None
        finally:
            await client.disconnect()

    def _client(self, session: str = "") -> TelegramClient:
        api_id = self._settings.telegram_client_publish_api_id
        api_hash = self._settings.telegram_client_publish_api_hash
        if api_id is None or api_hash is None:
            raise TelegramClientPublishTransportError("CLIENT_PUBLISH_API_NOT_CONFIGURED")
        return TelegramClient(
            StringSession(session),
            api_id,
            api_hash.get_secret_value(),
        )

    def _safe_error(self, exc: Exception, fallback: str) -> TelegramClientPublishTransportError:
        name = type(exc).__name__
        mapping = {
            "ChatWriteForbiddenError": ("WRITE_FORBIDDEN", "WRITE_RESTRICTED"),
            "UserBannedInChannelError": ("ACCOUNT_BANNED_IN_COMMUNITY", "ACCOUNT_RESTRICTED"),
            "SlowModeWaitError": ("SLOW_MODE", "SLOW_MODE"),
            "FloodWaitError": ("RATE_LIMITED", None),
            "ChannelPrivateError": ("COMMUNITY_NOT_ACCESSIBLE", "COMMUNITY_RESTRICTED"),
            "PhoneCodeInvalidError": ("LOGIN_CODE_INVALID", None),
            "PhoneCodeExpiredError": ("LOGIN_CODE_EXPIRED", None),
            "PasswordHashInvalidError": ("TWO_FACTOR_PASSWORD_INVALID", None),
        }
        code, restriction = mapping.get(name, (fallback, None))
        return TelegramClientPublishTransportError(code, restriction_signal=restriction)


class CustomerTelegramClientPublishService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
        secret_store: ProviderSecretStore | None = None,
        transport: TelegramClientPublishTransport | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()
        self._secret_store = secret_store or provider_secret_store
        self._transport = transport or TelethonClientPublishTransport(self._settings)
        self._publish_lock = asyncio.Lock()

    def readiness_blocker(self) -> str | None:
        if self._settings.telegram_client_publish_provider != "telethon":
            return "Telegram client-owned publishing provider is unavailable"
        if not self._settings.telegram_client_publish_public_ready:
            return "Telegram client-owned publishing is not enabled for customers yet"
        if (
            self._settings.telegram_client_publish_api_id is None
            or self._settings.telegram_client_publish_api_hash is None
        ):
            return "Telegram client-owned publishing API credentials are not configured"
        if self._settings.provider_secret_encryption_key is None:
            return "Encrypted provider secret storage is not configured"
        return None

    async def begin_login(
        self,
        project_id: UUID,
        customer_token: str,
        payload: TelegramLoginStartRequest,
    ) -> TelegramLoginChallengeView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_ready()
        result = await self._transport.begin_login(payload.phone_number)
        now = datetime.now(UTC)
        challenge_id = uuid4()
        secret_reference = self._secret_store.create_reference(
            prefix=TELEGRAM_LOGIN_SECRET_PREFIX
        )
        self._secret_store.put(
            secret_reference,
            json.dumps(
                {
                    "phone_number": payload.phone_number,
                    "phone_code_hash": result.phone_code_hash,
                    "session": result.session,
                }
            ),
        )
        record = {
            "challenge_id": str(challenge_id),
            "project_id": str(project_id),
            "secret_reference": secret_reference,
            "phone_hint": self._mask_phone(payload.phone_number),
            "created_at": now.isoformat(),
            "expires_at": (now + _LOGIN_TTL).isoformat(),
        }
        self._store.put(CUSTOMER_TELEGRAM_LOGIN_NAMESPACE, str(challenge_id), record)
        return TelegramLoginChallengeView(
            challenge_id=challenge_id,
            status=TelegramLoginStatus.CODE_SENT,
            phone_hint=record["phone_hint"],
            expires_at=datetime.fromisoformat(record["expires_at"]),
        )

    async def complete_login(
        self,
        project_id: UUID,
        customer_token: str,
        payload: TelegramLoginConfirmRequest,
    ) -> TelegramConnectionView | TelegramLoginChallengeView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_ready()
        record = self._login_record(project_id, payload.challenge_id)
        expires_at = self._as_utc(datetime.fromisoformat(str(record["expires_at"])))
        if expires_at <= datetime.now(UTC):
            self._delete_login(record)
            raise CustomerTelegramClientPublishError("Telegram login challenge has expired")
        secret_reference = str(record["secret_reference"])
        encrypted_payload = self._secret_store.get(secret_reference)
        if encrypted_payload is None:
            raise CustomerTelegramClientPublishError("Telegram login challenge is no longer available")
        try:
            secret_payload = json.loads(encrypted_payload)
            phone_number = str(secret_payload["phone_number"])
            phone_code_hash = str(secret_payload["phone_code_hash"])
            session = str(secret_payload["session"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CustomerTelegramClientPublishError(
                "Telegram login challenge is malformed"
            ) from exc

        result = await self._transport.complete_login(
            session=session,
            phone_number=phone_number,
            phone_code_hash=phone_code_hash,
            code=payload.code.get_secret_value(),
            password=(
                payload.password.get_secret_value() if payload.password is not None else None
            ),
        )
        if result.password_required:
            secret_payload["session"] = result.session
            self._secret_store.put(secret_reference, json.dumps(secret_payload))
            return TelegramLoginChallengeView(
                challenge_id=payload.challenge_id,
                status=TelegramLoginStatus.PASSWORD_REQUIRED,
                phone_hint=str(record["phone_hint"]),
                expires_at=expires_at,
            )
        if result.identity is None:
            raise CustomerTelegramClientPublishError(
                "Telegram login completed without an account identity"
            )

        final_reference = self._secret_store.create_reference(
            prefix=TELEGRAM_SESSION_SECRET_PREFIX
        )
        self._secret_store.put(final_reference, result.session)
        previous = self._store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id))
        if previous is not None:
            previous_reference = str(previous.get("secret_reference") or "")
            if previous_reference:
                self._secret_store.delete(previous_reference)
        now = datetime.now(UTC)
        connection = {
            "project_id": str(project_id),
            "status": TelegramConnectionStatus.ACTIVE.value,
            "secret_reference": final_reference,
            "telegram_user_id": result.identity.user_id,
            "username": result.identity.username,
            "display_name": result.identity.display_name,
            "connected_at": now.isoformat(),
            "last_verified_at": now.isoformat(),
        }
        self._store.put(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id), connection)
        self._delete_login(record)
        return self.connection(project_id, customer_token)

    def connection(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> TelegramConnectionView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        record = self._store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id))
        if record is None:
            return TelegramConnectionView(status=TelegramConnectionStatus.DISCONNECTED)
        return self._connection_view(record)

    def is_connected(self, project_id: UUID) -> bool:
        record = self._store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id))
        return bool(record and record.get("status") == TelegramConnectionStatus.ACTIVE.value)

    def disconnect(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> TelegramConnectionView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        record = self._store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id))
        if record is not None:
            reference = str(record.get("secret_reference") or "")
            if reference:
                self._secret_store.delete(reference)
            self._store.delete(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id))
        return TelegramConnectionView(status=TelegramConnectionStatus.DISCONNECTED)

    async def publish(
        self,
        project_id: UUID,
        customer_token: str,
        action_id: UUID,
        payload: TelegramPublishRequest,
    ) -> TelegramClientPublishReceipt:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_ready()
        existing = self.get_receipt(action_id)
        if existing is not None and not payload.retry:
            return existing
        if existing is not None and existing.outcome == TelegramClientPublishOutcome.EXECUTED:
            return existing
        if existing is not None and existing.outcome == TelegramClientPublishOutcome.IN_PROGRESS:
            raise CustomerTelegramClientPublishError(
                "The previous Telegram publish outcome is unknown; reconcile before retrying"
            )

        action = distribution_execution_service.get_action(action_id)
        if action.platform != DistributionPlatform.TELEGRAM:
            raise CustomerTelegramClientPublishError("Action is not a Telegram action")
        if action.action_type not in {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
            DistributionActionType.STANDALONE_POST,
        }:
            raise CustomerTelegramClientPublishError(
                "Telegram client publishing does not support this action"
            )
        if action.status != DistributionActionStatus.APPROVED:
            raise CustomerTelegramClientPublishError(
                "Telegram action must be explicitly APPROVED before publishing"
            )
        if action.experiment_id is None:
            raise CustomerTelegramClientPublishError("Telegram action has no DistributionExperiment")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        if str(project.get("product_id") or "") != str(experiment.product_id):
            raise CustomerTelegramClientPublishError(
                "Telegram action does not belong to this customer project"
            )

        raw_modes = project.get("channel_publisher_modes")
        selected_mode = (
            str(raw_modes.get(DistributionPlatform.TELEGRAM.value) or PublisherMode.MANUAL.value)
            if isinstance(raw_modes, dict)
            else PublisherMode.MANUAL.value
        )
        if selected_mode != PublisherMode.CLIENT_OWNED.value:
            raise CustomerTelegramClientPublishError(
                "Select CLIENT_OWNED as the Telegram publisher mode before publishing"
            )

        connection = self._store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id))
        if connection is None or connection.get("status") != TelegramConnectionStatus.ACTIVE.value:
            raise CustomerTelegramClientPublishError("Connect an authorised Telegram account first")
        session_reference = str(connection.get("secret_reference") or "")
        session = self._secret_store.get(session_reference) if session_reference else None
        if session is None:
            raise CustomerTelegramClientPublishError(
                "The authorised Telegram session is no longer available"
            )
        text = str(action.content_text or "").strip()
        if not text:
            raise CustomerTelegramClientPublishError("Telegram action has no approved content")
        if len(text) > _MAX_CONTENT_LENGTH:
            raise CustomerTelegramClientPublishError(
                f"Telegram content exceeds the {_MAX_CONTENT_LENGTH}-character safety limit"
            )
        target = self._parse_target(action.action_type, str(action.target_url or ""))
        fingerprint = self._fingerprint(target, text)

        async with self._publish_lock:
            self._enforce_publish_guard(project_id, fingerprint)
            in_progress = TelegramClientPublishReceipt(
                action_id=action.id,
                outcome=TelegramClientPublishOutcome.IN_PROGRESS,
                message="Telegram client-owned publish attempt started.",
                metadata={
                    "action_type": action.action_type.value,
                    "target_username": target.username,
                },
                created_at=datetime.now(UTC),
            )
            self._persist_receipt(in_progress)
            try:
                result = await self._transport.publish(
                    session=session,
                    target=target,
                    action_type=action.action_type,
                    text=text,
                )
            except TelegramClientPublishTransportError as exc:
                failed = TelegramClientPublishReceipt(
                    action_id=action.id,
                    outcome=TelegramClientPublishOutcome.FAILED,
                    message="Telegram rejected or failed the client-owned publish attempt.",
                    metadata={
                        "action_type": action.action_type.value,
                        "target_username": target.username,
                        "error_code": exc.code,
                        "restriction_signal": exc.restriction_signal,
                    },
                    created_at=datetime.now(UTC),
                )
                self._persist_receipt(failed)
                return failed
            except Exception as exc:
                failed = TelegramClientPublishReceipt(
                    action_id=action.id,
                    outcome=TelegramClientPublishOutcome.FAILED,
                    message="Telegram client-owned publish failed without a confirmed remote result.",
                    metadata={
                        "action_type": action.action_type.value,
                        "target_username": target.username,
                        "provider_error_type": type(exc).__name__,
                    },
                    created_at=datetime.now(UTC),
                )
                self._persist_receipt(failed)
                return failed

            receipt = TelegramClientPublishReceipt(
                action_id=action.id,
                outcome=TelegramClientPublishOutcome.EXECUTED,
                message="Telegram confirmed the client-owned publish.",
                external_reference=f"telegram-client:{result.peer_id}:{result.message_id}",
                executed_url=result.executed_url,
                published_at=result.published_at,
                metadata={
                    "action_type": action.action_type.value,
                    "target_username": target.username,
                    "remote_peer_id": result.peer_id,
                    "remote_message_id": result.message_id,
                    "reply_to_message_id": target.reply_to_message_id,
                    "telegram_user_id": connection.get("telegram_user_id"),
                    "restriction_signal": None,
                },
                created_at=datetime.now(UTC),
            )
            self._persist_receipt(receipt)
            self._record_publish(project_id, fingerprint)
            distribution_execution_service.mark_executed(
                action.id,
                DistributionActionExecutionRequest(
                    external_reference=receipt.external_reference,
                    executed_url=receipt.executed_url,
                    notes="Confirmed by Telegram client-owned publishing transport.",
                ),
            )
            return receipt

    def get_receipt(self, action_id: UUID) -> TelegramClientPublishReceipt | None:
        payload = self._store.get(CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return TelegramClientPublishReceipt.model_validate(payload)

    def reset(self) -> None:
        if self._store.ephemeral:
            for namespace in (
                CUSTOMER_TELEGRAM_LOGIN_NAMESPACE,
                CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
                CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE,
                CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE,
            ):
                self._store.clear_namespace(namespace)

    def _require_ready(self) -> None:
        blocker = self.readiness_blocker()
        if blocker is not None:
            raise CustomerTelegramClientPublishError(blocker)

    def _login_record(self, project_id: UUID, challenge_id: UUID) -> dict:
        record = self._store.get(CUSTOMER_TELEGRAM_LOGIN_NAMESPACE, str(challenge_id))
        if record is None or str(record.get("project_id")) != str(project_id):
            raise CustomerTelegramClientPublishError("Telegram login challenge is invalid")
        return record

    def _delete_login(self, record: dict) -> None:
        reference = str(record.get("secret_reference") or "")
        if reference:
            self._secret_store.delete(reference)
        challenge_id = str(record.get("challenge_id") or "")
        if challenge_id:
            self._store.delete(CUSTOMER_TELEGRAM_LOGIN_NAMESPACE, challenge_id)

    def _connection_view(self, record: dict) -> TelegramConnectionView:
        return TelegramConnectionView(
            status=TelegramConnectionStatus(str(record["status"])),
            telegram_user_id=int(record["telegram_user_id"]),
            username=(str(record["username"]) if record.get("username") else None),
            display_name=(str(record["display_name"]) if record.get("display_name") else None),
            connected_at=datetime.fromisoformat(str(record["connected_at"])),
            last_verified_at=datetime.fromisoformat(str(record["last_verified_at"])),
        )

    def _parse_target(
        self,
        action_type: DistributionActionType,
        raw_url: str,
    ) -> TelegramPublishTarget:
        parts = urlsplit(raw_url)
        if parts.scheme != "https" or parts.netloc.lower() not in {"t.me", "www.t.me"}:
            raise CustomerTelegramClientPublishError(
                "Telegram client publishing accepts only public https://t.me targets"
            )
        path = [part for part in parts.path.split("/") if part]
        if not path or path[0].startswith("+") or path[0].lower() == "joinchat":
            raise CustomerTelegramClientPublishError("Private Telegram invite targets are not supported")
        username = path[0]
        if not _USERNAME_PATTERN.fullmatch(username):
            raise CustomerTelegramClientPublishError("Telegram public target username is invalid")
        if action_type == DistributionActionType.STANDALONE_POST:
            if len(path) != 1:
                raise CustomerTelegramClientPublishError(
                    "Telegram standalone posts require a community URL, not a message URL"
                )
            return TelegramPublishTarget(username=username, reply_to_message_id=None)
        if len(path) != 2 or not path[1].isdigit() or int(path[1]) <= 0:
            raise CustomerTelegramClientPublishError(
                "Telegram comments/replies require a concrete public message URL"
            )
        return TelegramPublishTarget(username=username, reply_to_message_id=int(path[1]))

    def _fingerprint(self, target: TelegramPublishTarget, text: str) -> str:
        normalized = " ".join(text.casefold().split())
        material = f"{target.username.casefold()}:{target.reply_to_message_id}:{normalized}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _enforce_publish_guard(self, project_id: UUID, fingerprint: str) -> None:
        now = datetime.now(UTC)
        record = self._store.get(CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE, str(project_id)) or {}
        history = [
            item
            for item in record.get("history", [])
            if self._history_time(item) >= now - _DAILY_WINDOW
        ]
        if any(
            item.get("fingerprint") == fingerprint
            and self._history_time(item) >= now - _DUPLICATE_WINDOW
            for item in history
        ):
            raise CustomerTelegramClientPublishError(
                "Duplicate Telegram content to the same target is blocked for 24 hours"
            )
        if history and max(self._history_time(item) for item in history) > now - _MIN_PUBLISH_INTERVAL:
            raise CustomerTelegramClientPublishError(
                "Telegram client-owned publishing is limited to one confirmed publish per minute"
            )
        if len(history) >= _MAX_PUBLISHES_PER_DAY:
            raise CustomerTelegramClientPublishError(
                f"Telegram client-owned publishing is limited to {_MAX_PUBLISHES_PER_DAY} publishes per day"
            )

    def _record_publish(self, project_id: UUID, fingerprint: str) -> None:
        now = datetime.now(UTC)
        record = self._store.get(CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE, str(project_id)) or {}
        history = [
            item
            for item in record.get("history", [])
            if self._history_time(item) >= now - _DAILY_WINDOW
        ]
        history.append({"fingerprint": fingerprint, "published_at": now.isoformat()})
        self._store.put(
            CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE,
            str(project_id),
            {"project_id": str(project_id), "history": history[-_MAX_PUBLISHES_PER_DAY:]},
        )

    def _history_time(self, item: dict) -> datetime:
        try:
            return self._as_utc(datetime.fromisoformat(str(item["published_at"])))
        except (KeyError, TypeError, ValueError):
            return datetime.min.replace(tzinfo=UTC)

    def _persist_receipt(self, receipt: TelegramClientPublishReceipt) -> None:
        self._store.put(
            CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE,
            str(receipt.action_id),
            receipt.model_dump(mode="json"),
        )

    def _mask_phone(self, phone_number: str) -> str:
        return f"{phone_number[:2]}{'*' * max(4, len(phone_number) - 6)}{phone_number[-4:]}"

    def _as_utc(self, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


customer_telegram_client_publish_service = CustomerTelegramClientPublishService()

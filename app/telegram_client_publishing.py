from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl, SecretStr, field_validator

from app.config import Settings, get_settings
from app.customer_funnel import customer_funnel_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
    PublisherMode,
)
from app.provider_secret_store import ProviderSecretStore, provider_secret_store
from app.runtime_store import RuntimeStateStore, get_runtime_store

try:
    from telethon import TelegramClient
    from telethon.errors import (
        ChannelPrivateError,
        ChatAdminRequiredError,
        ChatWriteForbiddenError,
        FloodWaitError,
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        SessionPasswordNeededError,
        SlowModeWaitError,
        UserBannedInChannelError,
    )
    from telethon.sessions import StringSession
    from telethon.tl.types import Channel
except ImportError:  # pragma: no cover - readiness blocks the unavailable provider.
    TelegramClient = None
    StringSession = None
    Channel = None
    ChannelPrivateError = ChatAdminRequiredError = ChatWriteForbiddenError = Exception
    FloodWaitError = PhoneCodeExpiredError = PhoneCodeInvalidError = Exception
    SessionPasswordNeededError = SlowModeWaitError = UserBannedInChannelError = Exception


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
    expected_target_url: str | None = None
    expected_content_text: str | None = None


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
    identity: TelegramClientIdentity | None = None
    password_required: bool = False


class TelegramClientTarget(BaseModel):
    username: str
    reply_to_message_id: int | None = None


class TelegramPublishResult(BaseModel):
    peer_id: int
    message_id: int
    published_at: datetime
    executed_url: HttpUrl


class TelegramClientTransport:
    async def begin_login(self, phone_number: str) -> TelegramLoginStartResult:
        raise NotImplementedError

    async def complete_login(
        self,
        *,
        session: str,
        phone_number: str,
        phone_code_hash: str,
        code: str,
        password: str | None,
    ) -> TelegramLoginCompleteResult:
        raise NotImplementedError

    async def publish(
        self,
        *,
        session: str,
        target: TelegramClientTarget,
        action_type: DistributionActionType,
        text: str,
    ) -> TelegramPublishResult:
        raise NotImplementedError


class TelethonTelegramClientTransport(TelegramClientTransport):
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _client(self, session: str | None = None):
        if TelegramClient is None or StringSession is None:
            raise TelegramClientPublishTransportError("TELETHON_NOT_INSTALLED")
        api_hash = self._settings.telegram_client_publish_api_hash
        api_id = self._settings.telegram_client_publish_api_id
        if api_hash is None or api_id is None:
            raise TelegramClientPublishTransportError("TELEGRAM_APP_CREDENTIALS_MISSING")
        return TelegramClient(
            StringSession(session or ""),
            api_id,
            api_hash.get_secret_value(),
        )

    async def begin_login(self, phone_number: str) -> TelegramLoginStartResult:
        client = self._client()
        try:
            await client.connect()
            sent = await client.send_code_request(phone_number)
            return TelegramLoginStartResult(
                session=client.session.save(),
                phone_code_hash=sent.phone_code_hash,
            )
        except FloodWaitError as exc:
            raise TelegramClientPublishTransportError("FLOOD_WAIT") from exc
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
            return TelegramLoginCompleteResult(
                session=client.session.save(),
                identity=TelegramClientIdentity(
                    user_id=int(me.id),
                    username=getattr(me, "username", None),
                    display_name=" ".join(
                        item
                        for item in [
                            getattr(me, "first_name", None),
                            getattr(me, "last_name", None),
                        ]
                        if item
                    )
                    or None,
                ),
            )
        except PhoneCodeInvalidError as exc:
            raise TelegramClientPublishTransportError("PHONE_CODE_INVALID") from exc
        except PhoneCodeExpiredError as exc:
            raise TelegramClientPublishTransportError("PHONE_CODE_EXPIRED") from exc
        except FloodWaitError as exc:
            raise TelegramClientPublishTransportError("FLOOD_WAIT") from exc
        finally:
            await client.disconnect()

    async def publish(
        self,
        *,
        session: str,
        target: TelegramClientTarget,
        action_type: DistributionActionType,
        text: str,
    ) -> TelegramPublishResult:
        client = self._client(session)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramClientPublishTransportError("SESSION_UNAUTHORISED")
            entity = await client.get_entity(target.username)
            if Channel is None or not isinstance(entity, Channel):
                raise TelegramClientPublishTransportError("TARGET_NOT_CHANNEL")
            message = await client.send_message(
                entity,
                text,
                reply_to=target.reply_to_message_id,
            )
            peer_id = int(getattr(entity, "id", 0))
            message_id = int(getattr(message, "id", 0))
            if not peer_id or not message_id:
                raise TelegramClientPublishTransportError("MISSING_REMOTE_RECEIPT")
            return TelegramPublishResult(
                peer_id=peer_id,
                message_id=message_id,
                published_at=datetime.now(UTC),
                executed_url=f"https://t.me/{target.username}/{message_id}",
            )
        except TelegramClientPublishTransportError:
            raise
        except (ChatWriteForbiddenError, UserBannedInChannelError) as exc:
            raise TelegramClientPublishTransportError(
                "WRITE_FORBIDDEN",
                restriction_signal="WRITE_RESTRICTED",
            ) from exc
        except ChatAdminRequiredError as exc:
            raise TelegramClientPublishTransportError(
                "ADMIN_REQUIRED",
                restriction_signal="ADMIN_REQUIRED",
            ) from exc
        except ChannelPrivateError as exc:
            raise TelegramClientPublishTransportError(
                "CHANNEL_PRIVATE",
                restriction_signal="CHANNEL_PRIVATE",
            ) from exc
        except SlowModeWaitError as exc:
            raise TelegramClientPublishTransportError(
                "SLOW_MODE",
                restriction_signal="SLOW_MODE",
            ) from exc
        except FloodWaitError as exc:
            raise TelegramClientPublishTransportError(
                "FLOOD_WAIT",
                restriction_signal="RATE_LIMITED",
            ) from exc
        except Exception as exc:
            raise TelegramClientPublishTransportError(type(exc).__name__.upper()) from exc
        finally:
            await client.disconnect()


class CustomerTelegramClientPublishService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
        secret_store: ProviderSecretStore | None = None,
        transport: TelegramClientTransport | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()
        self._secret_store = secret_store or provider_secret_store
        self._transport = transport or TelethonTelegramClientTransport(self._settings)
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
            return "Telegram client publishing app credentials are not configured"
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
        secret_reference = self._secret_store.create_reference(prefix="telegram-login")
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
        challenge_id = uuid4()
        now = datetime.now(UTC)
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
        if action.experiment_id is None:
            raise CustomerTelegramClientPublishError("Telegram action has no DistributionExperiment")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        if str(project.get("product_id") or "") != str(experiment.product_id):
            raise CustomerTelegramClientPublishError(
                "Telegram action does not belong to this customer project"
            )
        if (
            payload.expected_target_url is not None
            and payload.expected_target_url != str(action.target_url or "")
        ) or (
            payload.expected_content_text is not None
            and payload.expected_content_text != str(action.content_text or "")
        ):
            raise CustomerTelegramClientPublishError(
                "Reviewed Telegram action changed; refresh and review it again"
            )

        existing = self.get_receipt(action_id)
        if existing is not None and not payload.retry:
            return existing
        if existing is not None and existing.outcome == TelegramClientPublishOutcome.EXECUTED:
            return existing
        if existing is not None and existing.outcome == TelegramClientPublishOutcome.IN_PROGRESS:
            raise CustomerTelegramClientPublishError(
                "The previous Telegram publish outcome is unknown; reconcile before retrying"
            )
        if action.status != DistributionActionStatus.APPROVED:
            raise CustomerTelegramClientPublishError(
                "Telegram action must be explicitly APPROVED before publishing"
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
        if record is None or str(record.get("project_id") or "") != str(project_id):
            raise CustomerTelegramClientPublishError("Telegram login challenge is invalid")
        return record

    def _delete_login(self, record: dict) -> None:
        challenge_id = str(record.get("challenge_id") or "")
        secret_reference = str(record.get("secret_reference") or "")
        if challenge_id:
            self._store.delete(CUSTOMER_TELEGRAM_LOGIN_NAMESPACE, challenge_id)
        if secret_reference:
            self._secret_store.delete(secret_reference)

    def _connection_view(self, record: dict) -> TelegramConnectionView:
        return TelegramConnectionView(
            status=TelegramConnectionStatus(str(record.get("status") or "DISCONNECTED")),
            telegram_user_id=(
                int(record["telegram_user_id"])
                if record.get("telegram_user_id") is not None
                else None
            ),
            username=(str(record["username"]) if record.get("username") else None),
            display_name=(
                str(record["display_name"])
                if record.get("display_name")
                else None
            ),
            connected_at=(
                datetime.fromisoformat(str(record["connected_at"]))
                if record.get("connected_at")
                else None
            ),
            last_verified_at=(
                datetime.fromisoformat(str(record["last_verified_at"]))
                if record.get("last_verified_at")
                else None
            ),
        )

    def _parse_target(
        self,
        action_type: DistributionActionType,
        target_url: str,
    ) -> TelegramClientTarget:
        parsed = urlparse(target_url)
        if parsed.scheme != "https" or parsed.netloc.lower() not in {"t.me", "telegram.me"}:
            raise CustomerTelegramClientPublishError(
                "Telegram client publishing requires a canonical public t.me target"
            )
        path = [part for part in parsed.path.split("/") if part]
        if not path or not _USERNAME_PATTERN.fullmatch(path[0]):
            raise CustomerTelegramClientPublishError("Telegram target username is invalid")
        reply_to_message_id: int | None = None
        if action_type in {DistributionActionType.COMMENT, DistributionActionType.REPLY}:
            if len(path) < 2 or not path[1].isdigit():
                raise CustomerTelegramClientPublishError(
                    "Telegram reply/comment requires an exact message URL"
                )
            reply_to_message_id = int(path[1])
        return TelegramClientTarget(
            username=path[0],
            reply_to_message_id=reply_to_message_id,
        )

    @staticmethod
    def _fingerprint(target: TelegramClientTarget, text: str) -> str:
        payload = f"{target.username}\n{target.reply_to_message_id or ''}\n{text}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _enforce_publish_guard(self, project_id: UUID, fingerprint: str) -> None:
        now = datetime.now(UTC)
        records = []
        for record in self._store.list_namespace(CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE):
            if str(record.get("project_id") or "") != str(project_id):
                continue
            try:
                created_at = self._as_utc(datetime.fromisoformat(str(record["created_at"])))
            except (KeyError, ValueError):
                continue
            records.append((record, created_at))
        if any(
            str(record.get("fingerprint") or "") == fingerprint
            and created_at >= now - _DUPLICATE_WINDOW
            for record, created_at in records
        ):
            raise CustomerTelegramClientPublishError(
                "Duplicate Telegram content to the same target is blocked for 24 hours"
            )
        recent = [created_at for _, created_at in records if created_at >= now - _DAILY_WINDOW]
        if len(recent) >= _MAX_PUBLISHES_PER_DAY:
            raise CustomerTelegramClientPublishError("Telegram daily publish limit reached")
        if recent and max(recent) > now - _MIN_PUBLISH_INTERVAL:
            raise CustomerTelegramClientPublishError(
                "Telegram client publishing allows at most one confirmed publish per minute"
            )

    def _record_publish(self, project_id: UUID, fingerprint: str) -> None:
        now = datetime.now(UTC)
        key = str(uuid4())
        self._store.put(
            CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE,
            key,
            {
                "project_id": str(project_id),
                "fingerprint": fingerprint,
                "created_at": now.isoformat(),
            },
        )

    @staticmethod
    def _mask_phone(value: str) -> str:
        return f"{value[:3]}••••{value[-2:]}"

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


customer_telegram_client_publish_service = CustomerTelegramClientPublishService()

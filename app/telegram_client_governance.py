from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import types as telegram_types

from app.channel_execution import PublisherMode
from app.config import Settings, get_settings
from app.customer_funnel import customer_funnel_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionPlatform
from app.provider_secret_store import ProviderSecretStore, provider_secret_store
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE,
    CustomerTelegramClientPublishError,
    TelegramClientPublishOutcome,
    TelegramClientPublishReceipt,
    TelegramConnectionStatus,
    TelegramPublishRequest,
    customer_telegram_client_publish_service,
)

CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE = "customer_telegram_publish_observation"
CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE = "customer_telegram_automation_authorization"

_OBSERVATION_HISTORY_LIMIT = 50
_AUTOMATION_DAILY_WINDOW = timedelta(hours=24)
_MAX_AUTOMATION_PUBLISHES_PER_DAY = 10


class TelegramRemoteMessageState(StrEnum):
    PRESENT = "PRESENT"
    REMOVED = "REMOVED"
    INACCESSIBLE = "INACCESSIBLE"
    UNKNOWN = "UNKNOWN"


class TelegramAutomationStatus(StrEnum):
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"
    PAUSED = "PAUSED"
    REVOKED = "REVOKED"


class TelegramRemoteObservationResult(BaseModel):
    state: TelegramRemoteMessageState
    provider_code: str | None = Field(default=None, max_length=100)
    restriction_signal: str | None = Field(default=None, max_length=100)


class TelegramPublishObservationEvent(BaseModel):
    state: TelegramRemoteMessageState
    checked_at: datetime
    provider_code: str | None = Field(default=None, max_length=100)
    restriction_signal: str | None = Field(default=None, max_length=100)


class TelegramPublishObservationView(BaseModel):
    action_id: UUID
    remote_message_id: int
    target_username: str
    latest: TelegramPublishObservationEvent
    history: list[TelegramPublishObservationEvent] = Field(default_factory=list)


class TelegramAutomationAuthorizationRequest(BaseModel):
    confirm_client_owned_execution: bool
    max_publishes_per_day: int = Field(default=1, ge=1, le=_MAX_AUTOMATION_PUBLISHES_PER_DAY)


class TelegramAutomationView(BaseModel):
    project_id: UUID
    status: TelegramAutomationStatus
    max_publishes_per_day: int = 1
    authorized_at: datetime | None = None
    paused_at: datetime | None = None
    revoked_at: datetime | None = None
    readiness_ok: bool
    blockers: list[str] = Field(default_factory=list)


class TelegramClientObservationTransport(Protocol):
    async def observe(
        self,
        *,
        session: str,
        target_username: str,
        message_id: int,
    ) -> TelegramRemoteObservationResult: ...


class TelethonClientObservationTransport:
    """Read-only observer for messages previously published by a customer session."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def observe(
        self,
        *,
        session: str,
        target_username: str,
        message_id: int,
    ) -> TelegramRemoteObservationResult:
        client = self._client(session)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return TelegramRemoteObservationResult(
                    state=TelegramRemoteMessageState.INACCESSIBLE,
                    provider_code="SESSION_NOT_AUTHORIZED",
                    restriction_signal="ACCOUNT_SESSION_REVOKED",
                )
            entity = await client.get_entity(f"@{target_username}")
            if not isinstance(entity, telegram_types.Channel):
                return TelegramRemoteObservationResult(
                    state=TelegramRemoteMessageState.INACCESSIBLE,
                    provider_code="COMMUNITY_TARGET_REQUIRED",
                    restriction_signal="COMMUNITY_NOT_ACCESSIBLE",
                )
            message = await client.get_messages(entity, ids=message_id)
            if message is None or isinstance(message, telegram_types.MessageEmpty):
                return TelegramRemoteObservationResult(
                    state=TelegramRemoteMessageState.REMOVED,
                    provider_code="MESSAGE_NOT_FOUND",
                    restriction_signal="MESSAGE_REMOVED_OR_UNAVAILABLE",
                )
            return TelegramRemoteObservationResult(state=TelegramRemoteMessageState.PRESENT)
        except Exception as exc:
            return self._safe_result(exc)
        finally:
            await client.disconnect()

    def _client(self, session: str) -> TelegramClient:
        api_id = self._settings.telegram_client_publish_api_id
        api_hash = self._settings.telegram_client_publish_api_hash
        if api_id is None or api_hash is None:
            raise CustomerTelegramClientPublishError(
                "Telegram client-owned observation API credentials are not configured"
            )
        return TelegramClient(
            StringSession(session),
            api_id,
            api_hash.get_secret_value(),
        )

    def _safe_result(self, exc: Exception) -> TelegramRemoteObservationResult:
        mapping = {
            "ChannelPrivateError": (
                TelegramRemoteMessageState.INACCESSIBLE,
                "COMMUNITY_NOT_ACCESSIBLE",
                "COMMUNITY_RESTRICTED",
            ),
            "UserBannedInChannelError": (
                TelegramRemoteMessageState.INACCESSIBLE,
                "ACCOUNT_BANNED_IN_COMMUNITY",
                "ACCOUNT_RESTRICTED",
            ),
            "FloodWaitError": (
                TelegramRemoteMessageState.UNKNOWN,
                "RATE_LIMITED",
                None,
            ),
        }
        state, provider_code, restriction = mapping.get(
            type(exc).__name__,
            (TelegramRemoteMessageState.UNKNOWN, "OBSERVE_FAILED", None),
        )
        return TelegramRemoteObservationResult(
            state=state,
            provider_code=provider_code,
            restriction_signal=restriction,
        )


class CustomerTelegramGovernanceService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
        secret_store: ProviderSecretStore | None = None,
        observation_transport: TelegramClientObservationTransport | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()
        self._secret_store = secret_store or provider_secret_store
        self._observation_transport = observation_transport or TelethonClientObservationTransport(
            self._settings
        )

    def observation_blocker(self) -> str | None:
        if self._settings.telegram_client_publish_provider != "telethon":
            return "Telegram client-owned observation provider is unavailable"
        if (
            self._settings.telegram_client_publish_api_id is None
            or self._settings.telegram_client_publish_api_hash is None
        ):
            return "Telegram client-owned observation API credentials are not configured"
        if self._settings.provider_secret_encryption_key is None:
            return "Encrypted provider secret storage is not configured"
        return None

    def automation_status(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> TelegramAutomationView:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        record = self._store.get(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(project_id))
        blockers = self._automation_blockers(project_id, project)
        if record is None:
            return TelegramAutomationView(
                project_id=project_id,
                status=TelegramAutomationStatus.DISABLED,
                readiness_ok=not blockers,
                blockers=blockers,
            )
        return TelegramAutomationView(
            project_id=project_id,
            status=TelegramAutomationStatus(str(record["status"])),
            max_publishes_per_day=int(record.get("max_publishes_per_day") or 1),
            authorized_at=self._optional_datetime(record.get("authorized_at")),
            paused_at=self._optional_datetime(record.get("paused_at")),
            revoked_at=self._optional_datetime(record.get("revoked_at")),
            readiness_ok=not blockers,
            blockers=blockers,
        )

    def authorize_automation(
        self,
        project_id: UUID,
        customer_token: str,
        payload: TelegramAutomationAuthorizationRequest,
    ) -> TelegramAutomationView:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        if not payload.confirm_client_owned_execution:
            raise CustomerTelegramClientPublishError(
                "Explicit confirmation is required before enabling Telegram automation"
            )
        blockers = self._automation_blockers(project_id, project)
        if blockers:
            raise CustomerTelegramClientPublishError("; ".join(blockers))
        now = datetime.now(UTC)
        record = {
            "project_id": str(project_id),
            "status": TelegramAutomationStatus.ENABLED.value,
            "max_publishes_per_day": payload.max_publishes_per_day,
            "authorized_at": now.isoformat(),
            "paused_at": None,
            "revoked_at": None,
        }
        self._store.put(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(project_id), record)
        return self.automation_status(project_id, customer_token)

    def pause_automation(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> TelegramAutomationView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        record = self._store.get(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(project_id))
        if record is None:
            return self.automation_status(project_id, customer_token)
        record["status"] = TelegramAutomationStatus.PAUSED.value
        record["paused_at"] = datetime.now(UTC).isoformat()
        self._store.put(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(project_id), record)
        return self.automation_status(project_id, customer_token)

    def revoke_automation(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> TelegramAutomationView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        record = self._store.get(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(project_id)) or {
            "project_id": str(project_id),
            "max_publishes_per_day": 1,
            "authorized_at": None,
            "paused_at": None,
        }
        record["status"] = TelegramAutomationStatus.REVOKED.value
        record["revoked_at"] = datetime.now(UTC).isoformat()
        self._store.put(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(project_id), record)
        return self.automation_status(project_id, customer_token)

    async def automated_publish(
        self,
        project_id: UUID,
        customer_token: str,
        action_id: UUID,
        payload: TelegramPublishRequest,
    ) -> TelegramClientPublishReceipt:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        authorization = self.automation_status(project_id, customer_token)
        if authorization.status != TelegramAutomationStatus.ENABLED:
            raise CustomerTelegramClientPublishError(
                "Telegram automation is not explicitly enabled for this project"
            )
        blockers = self._automation_blockers(project_id, project)
        if blockers:
            raise CustomerTelegramClientPublishError("; ".join(blockers))
        self._enforce_automation_daily_limit(project_id, authorization.max_publishes_per_day)
        return await customer_telegram_client_publish_service.publish(
            project_id,
            customer_token,
            action_id,
            payload,
        )

    async def observe_publish(
        self,
        project_id: UUID,
        customer_token: str,
        action_id: UUID,
    ) -> TelegramPublishObservationView:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_action_ownership(project, action_id)
        blocker = self.observation_blocker()
        if blocker is not None:
            raise CustomerTelegramClientPublishError(blocker)
        receipt = customer_telegram_client_publish_service.get_receipt(action_id)
        if receipt is None or receipt.outcome != TelegramClientPublishOutcome.EXECUTED:
            raise CustomerTelegramClientPublishError(
                "Only a confirmed Telegram client-owned publish can be observed"
            )
        target_username = str(receipt.metadata.get("target_username") or "")
        remote_message_id = int(receipt.metadata.get("remote_message_id") or 0)
        if not target_username or remote_message_id <= 0:
            raise CustomerTelegramClientPublishError(
                "Telegram publish receipt is missing its remote observation identifiers"
            )
        session = self._customer_session(project_id)
        result = await self._observation_transport.observe(
            session=session,
            target_username=target_username,
            message_id=remote_message_id,
        )
        event = TelegramPublishObservationEvent(
            state=result.state,
            checked_at=datetime.now(UTC),
            provider_code=result.provider_code,
            restriction_signal=result.restriction_signal,
        )
        record = self._store.get(CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE, str(action_id)) or {
            "action_id": str(action_id),
            "remote_message_id": remote_message_id,
            "target_username": target_username,
            "history": [],
        }
        history = list(record.get("history") or [])
        history.append(event.model_dump(mode="json"))
        record.update(
            {
                "remote_message_id": remote_message_id,
                "target_username": target_username,
                "latest": event.model_dump(mode="json"),
                "history": history[-_OBSERVATION_HISTORY_LIMIT:],
            }
        )
        self._store.put(CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE, str(action_id), record)
        return TelegramPublishObservationView.model_validate(record)

    def get_observation(
        self,
        project_id: UUID,
        customer_token: str,
        action_id: UUID,
    ) -> TelegramPublishObservationView:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_action_ownership(project, action_id)
        record = self._store.get(CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE, str(action_id))
        if record is None:
            raise CustomerTelegramClientPublishError(
                "No Telegram post-publish observation has been recorded for this action"
            )
        return TelegramPublishObservationView.model_validate(record)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE)
            self._store.clear_namespace(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE)

    def _automation_blockers(self, project_id: UUID, project: dict) -> list[str]:
        blockers: list[str] = []
        readiness = customer_telegram_client_publish_service.readiness_blocker()
        if readiness is not None:
            blockers.append(readiness)
        raw_modes = project.get("channel_publisher_modes")
        selected_mode = (
            str(raw_modes.get(DistributionPlatform.TELEGRAM.value) or PublisherMode.MANUAL.value)
            if isinstance(raw_modes, dict)
            else PublisherMode.MANUAL.value
        )
        if selected_mode != PublisherMode.CLIENT_OWNED.value:
            blockers.append("Select CLIENT_OWNED as the Telegram publisher mode")
        if not customer_telegram_client_publish_service.is_connected(project_id):
            blockers.append("Connect an authorised Telegram account")
        return blockers

    def _require_action_ownership(self, project: dict, action_id: UUID) -> None:
        action = distribution_execution_service.get_action(action_id)
        if action.platform != DistributionPlatform.TELEGRAM:
            raise CustomerTelegramClientPublishError("Action is not a Telegram action")
        if action.experiment_id is None:
            raise CustomerTelegramClientPublishError("Telegram action has no DistributionExperiment")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        if str(project.get("product_id") or "") != str(experiment.product_id):
            raise CustomerTelegramClientPublishError(
                "Telegram action does not belong to this customer project"
            )

    def _customer_session(self, project_id: UUID) -> str:
        connection = self._store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id))
        if connection is None or connection.get("status") != TelegramConnectionStatus.ACTIVE.value:
            raise CustomerTelegramClientPublishError("Connect an authorised Telegram account first")
        reference = str(connection.get("secret_reference") or "")
        session = self._secret_store.get(reference) if reference else None
        if session is None:
            raise CustomerTelegramClientPublishError(
                "The authorised Telegram session is no longer available"
            )
        return session

    def _enforce_automation_daily_limit(self, project_id: UUID, maximum: int) -> None:
        record = self._store.get(CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE, str(project_id)) or {}
        now = datetime.now(UTC)
        recent = 0
        for item in record.get("history", []):
            try:
                published_at = datetime.fromisoformat(str(item["published_at"]))
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=UTC)
            except (KeyError, TypeError, ValueError):
                continue
            if published_at >= now - _AUTOMATION_DAILY_WINDOW:
                recent += 1
        if recent >= maximum:
            raise CustomerTelegramClientPublishError(
                "Telegram automation is limited to "
                f"{maximum} confirmed publishes per 24 hours for this project"
            )

    def _optional_datetime(self, value: object) -> datetime | None:
        if value in {None, ""}:
            return None
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


customer_telegram_governance_service = CustomerTelegramGovernanceService()

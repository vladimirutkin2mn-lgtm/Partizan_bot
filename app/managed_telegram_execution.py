from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.managed_distribution import ManagedDistributionError, managed_distribution_service
from app.managed_distribution_schemas import (
    ManagedAssignmentStatus,
    ManagedFulfillmentRequest,
    ManagedPublisherHealth,
    ManagedPublisherOwnership,
)
from app.provider_secret_store import (
    MANAGED_TELEGRAM_SESSION_SECRET_PREFIX,
    ProviderSecretStore,
    provider_secret_store,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import (
    TelegramClientPublishTransportError,
    TelegramPublishTarget,
    TelethonClientPublishTransport,
)

MANAGED_TELEGRAM_CONNECTION_NAMESPACE = "managed_telegram_connection"
MANAGED_TELEGRAM_EXECUTION_RECEIPT_NAMESPACE = "managed_telegram_execution_receipt"

_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{5,32}$")
_MAX_CONTENT_LENGTH = 4000


class ManagedTelegramExecutionError(RuntimeError):
    pass


class ManagedTelegramConnectionStatus(StrEnum):
    ACTIVE = "ACTIVE"


class ManagedTelegramExecutionStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    PUBLISHED_UNRECONCILED = "PUBLISHED_UNRECONCILED"


class ManagedTelegramSessionInstallRequest(BaseModel):
    session: SecretStr
    expected_username: str = Field(min_length=5, max_length=32)
    confirm_management_authorization: bool = False

    @field_validator("expected_username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip().lstrip("@")
        if not _USERNAME_PATTERN.fullmatch(normalized):
            raise ValueError("Expected Telegram username is invalid")
        return normalized


class ManagedTelegramConnectionView(BaseModel):
    managed_publisher_id: UUID
    distribution_identity_id: UUID
    status: ManagedTelegramConnectionStatus
    username: str
    display_name: str | None = None
    connected_at: datetime
    last_verified_at: datetime


class ManagedTelegramActionPreviewRequest(BaseModel):
    action_id: UUID


class ManagedTelegramActionPreview(BaseModel):
    assignment_id: UUID
    action_id: UUID
    experiment_id: UUID
    managed_publisher_id: UUID
    telegram_username: str
    persona: str | None = None
    profile_strategy_key: str | None = None
    message_strategy: str | None = None
    experiment_arm: str | None = None
    action_type: DistributionActionType
    target_url: str
    content_text: str
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    requires_publish_confirmation: bool = True


class ManagedTelegramExecuteRequest(BaseModel):
    action_id: UUID
    confirm_execute: bool = False
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ManagedTelegramReconcileRequest(BaseModel):
    confirm_reconcile: bool = False


class ManagedTelegramExecutionReceipt(BaseModel):
    assignment_id: UUID
    action_id: UUID
    managed_publisher_id: UUID
    status: ManagedTelegramExecutionStatus
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    external_reference: str | None = None
    executed_url: str | None = None
    remote_message_id: int | None = None
    published_at: datetime | None = None
    error_code: str | None = None
    restriction_signal: str | None = None
    created_at: datetime
    updated_at: datetime


class ManagedTelegramExecutionService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        secret_store: ProviderSecretStore | None = None,
        transport=None,
        managed_service=None,
        execution_service=None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._secret_store = secret_store or provider_secret_store
        self._transport = transport or TelethonClientPublishTransport()
        self._managed = managed_service or managed_distribution_service
        self._execution = execution_service or distribution_execution_service

    async def install_session(
        self,
        publisher_id: UUID,
        payload: ManagedTelegramSessionInstallRequest,
    ) -> ManagedTelegramConnectionView:
        if not payload.confirm_management_authorization:
            raise ManagedTelegramExecutionError(
                "Managed Telegram account authorization must be explicitly confirmed"
            )
        publisher = self._publisher_for_connection(publisher_id)
        session = payload.session.get_secret_value()
        if not session:
            raise ManagedTelegramExecutionError("Managed Telegram session is required")
        try:
            profile = await self._transport.profile(session=session)
        except TelegramClientPublishTransportError as exc:
            raise ManagedTelegramExecutionError(exc.code) from exc
        actual_username = str(profile.username or "").strip().lstrip("@")
        if not actual_username:
            raise ManagedTelegramExecutionError(
                "Managed Telegram session must belong to an account with a public username"
            )
        if actual_username.casefold() != payload.expected_username.casefold():
            raise ManagedTelegramExecutionError(
                "Managed Telegram session username does not match the expected account"
            )

        previous = self._store.get(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(publisher_id),
        )
        reference = self._secret_store.create_reference(
            prefix=MANAGED_TELEGRAM_SESSION_SECRET_PREFIX
        )
        self._secret_store.put(reference, session)
        if previous is not None:
            old_reference = str(previous.get("secret_reference") or "")
            if old_reference:
                self._secret_store.delete(old_reference)

        now = datetime.now(UTC)
        record = {
            "managed_publisher_id": str(publisher.id),
            "distribution_identity_id": str(publisher.distribution_identity_id),
            "status": ManagedTelegramConnectionStatus.ACTIVE.value,
            "secret_reference": reference,
            "username": actual_username,
            "display_name": profile.display_name,
            "connected_at": now.isoformat(),
            "last_verified_at": now.isoformat(),
        }
        self._store.put(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(publisher.id),
            record,
        )
        return self._connection_view(record)

    async def verify_session(self, publisher_id: UUID) -> ManagedTelegramConnectionView:
        self._publisher_for_connection(publisher_id)
        record, session = self._active_connection(publisher_id)
        try:
            profile = await self._transport.profile(session=session)
        except TelegramClientPublishTransportError as exc:
            raise ManagedTelegramExecutionError(exc.code) from exc
        username = str(profile.username or "").strip().lstrip("@")
        if not username or username.casefold() != str(record["username"]).casefold():
            raise ManagedTelegramExecutionError(
                "Managed Telegram session identity changed; reconnect the publisher"
            )
        updated = {
            **record,
            "display_name": profile.display_name,
            "last_verified_at": datetime.now(UTC).isoformat(),
        }
        self._store.put(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(publisher_id),
            updated,
        )
        return self._connection_view(updated)

    def connection(self, publisher_id: UUID) -> ManagedTelegramConnectionView:
        self._publisher_for_connection(publisher_id)
        record = self._store.get(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(publisher_id),
        )
        if record is None:
            raise ManagedTelegramExecutionError(
                "Managed Telegram publisher has no connected session"
            )
        return self._connection_view(record)

    def disconnect(self, publisher_id: UUID) -> None:
        self._publisher_for_connection(publisher_id)
        record = self._store.get(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(publisher_id),
        )
        if record is None:
            return
        reference = str(record.get("secret_reference") or "")
        if reference:
            self._secret_store.delete(reference)
        self._store.delete(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(publisher_id),
        )

    def preview(
        self,
        assignment_id: UUID,
        payload: ManagedTelegramActionPreviewRequest,
    ) -> ManagedTelegramActionPreview:
        assignment, action, experiment = self._validated_scope(
            assignment_id,
            payload.action_id,
            require_approved=True,
        )
        connection, _ = self._active_connection(assignment.managed_publisher_id)
        text = str(action.content_text or "").strip()
        if not text:
            raise ManagedTelegramExecutionError("Telegram action has no approved content")
        if len(text) > _MAX_CONTENT_LENGTH:
            raise ManagedTelegramExecutionError(
                f"Telegram content exceeds the {_MAX_CONTENT_LENGTH}-character safety limit"
            )
        target_url = str(action.target_url or "").strip()
        self._parse_target(action.action_type, target_url)
        fingerprint = self._fingerprint(
            assignment=assignment,
            action=action,
            telegram_username=str(connection["username"]),
        )
        return ManagedTelegramActionPreview(
            assignment_id=assignment.id,
            action_id=action.id,
            experiment_id=experiment.id,
            managed_publisher_id=assignment.managed_publisher_id,
            telegram_username=str(connection["username"]),
            persona=(assignment.persona.value if assignment.persona is not None else None),
            profile_strategy_key=assignment.profile_strategy_key,
            message_strategy=assignment.message_strategy,
            experiment_arm=assignment.experiment_arm,
            action_type=action.action_type,
            target_url=target_url,
            content_text=text,
            fingerprint=fingerprint,
        )

    async def execute(
        self,
        assignment_id: UUID,
        payload: ManagedTelegramExecuteRequest,
    ) -> ManagedTelegramExecutionReceipt:
        if not payload.confirm_execute:
            raise ManagedTelegramExecutionError(
                "Explicit managed Telegram publish confirmation is required"
            )
        existing = self.receipt(assignment_id)
        if existing is not None:
            if existing.status in {
                ManagedTelegramExecutionStatus.EXECUTED,
                ManagedTelegramExecutionStatus.PUBLISHED_UNRECONCILED,
                ManagedTelegramExecutionStatus.IN_PROGRESS,
            }:
                return existing
            raise ManagedTelegramExecutionError(
                "Previous managed Telegram execution failed; reconcile the remote state before retrying"
            )

        preview = self.preview(
            assignment_id,
            ManagedTelegramActionPreviewRequest(action_id=payload.action_id),
        )
        if preview.fingerprint != payload.expected_fingerprint:
            raise ManagedTelegramExecutionError(
                "Managed Telegram action changed after review; refresh the preview"
            )
        assignment, action, _ = self._validated_scope(
            assignment_id,
            payload.action_id,
            require_approved=True,
        )
        _, session = self._active_connection(assignment.managed_publisher_id)
        target = self._parse_target(action.action_type, preview.target_url)
        now = datetime.now(UTC)
        in_progress = ManagedTelegramExecutionReceipt(
            assignment_id=assignment.id,
            action_id=action.id,
            managed_publisher_id=assignment.managed_publisher_id,
            status=ManagedTelegramExecutionStatus.IN_PROGRESS,
            fingerprint=preview.fingerprint,
            created_at=now,
            updated_at=now,
        )
        self._persist_receipt(in_progress)

        try:
            result = await self._transport.publish(
                session=session,
                target=target,
                action_type=action.action_type,
                text=preview.content_text,
            )
        except TelegramClientPublishTransportError as exc:
            failed = in_progress.model_copy(
                update={
                    "status": ManagedTelegramExecutionStatus.FAILED,
                    "error_code": exc.code,
                    "restriction_signal": exc.restriction_signal,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._persist_receipt(failed)
            if exc.restriction_signal:
                self._managed.set_health(
                    assignment.managed_publisher_id,
                    ManagedPublisherHealth.RESTRICTED,
                    exc.restriction_signal,
                )
            raise ManagedTelegramExecutionError(exc.code) from exc

        external_reference = f"telegram:{result.peer_id}:{result.message_id}"
        published = in_progress.model_copy(
            update={
                "status": ManagedTelegramExecutionStatus.PUBLISHED_UNRECONCILED,
                "external_reference": external_reference,
                "executed_url": str(result.executed_url),
                "remote_message_id": result.message_id,
                "published_at": result.published_at,
                "updated_at": datetime.now(UTC),
            }
        )
        self._persist_receipt(published)
        try:
            self._fulfill_from_receipt(assignment.id, published)
        except (ManagedDistributionError, KeyError, ValueError) as exc:
            raise ManagedTelegramExecutionError(
                "Telegram published successfully but local fulfillment needs reconciliation"
            ) from exc

        executed = published.model_copy(
            update={
                "status": ManagedTelegramExecutionStatus.EXECUTED,
                "updated_at": datetime.now(UTC),
            }
        )
        self._persist_receipt(executed)
        return executed

    def reconcile(
        self,
        assignment_id: UUID,
        payload: ManagedTelegramReconcileRequest,
    ) -> ManagedTelegramExecutionReceipt:
        if not payload.confirm_reconcile:
            raise ManagedTelegramExecutionError(
                "Explicit managed Telegram reconciliation confirmation is required"
            )
        receipt = self.receipt(assignment_id)
        if receipt is None:
            raise ManagedTelegramExecutionError("Managed Telegram execution receipt not found")
        if receipt.status == ManagedTelegramExecutionStatus.EXECUTED:
            return receipt
        if receipt.status != ManagedTelegramExecutionStatus.PUBLISHED_UNRECONCILED:
            raise ManagedTelegramExecutionError(
                "Only a remotely published execution can be reconciled without republishing"
            )
        self._fulfill_from_receipt(assignment_id, receipt)
        executed = receipt.model_copy(
            update={
                "status": ManagedTelegramExecutionStatus.EXECUTED,
                "updated_at": datetime.now(UTC),
            }
        )
        self._persist_receipt(executed)
        return executed

    def receipt(self, assignment_id: UUID) -> ManagedTelegramExecutionReceipt | None:
        payload = self._store.get(
            MANAGED_TELEGRAM_EXECUTION_RECEIPT_NAMESPACE,
            str(assignment_id),
        )
        if payload is None:
            return None
        return ManagedTelegramExecutionReceipt.model_validate(payload)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(MANAGED_TELEGRAM_CONNECTION_NAMESPACE)
            self._store.clear_namespace(MANAGED_TELEGRAM_EXECUTION_RECEIPT_NAMESPACE)

    def _publisher_for_connection(self, publisher_id: UUID):
        try:
            publisher = self._managed.get_publisher(publisher_id)
        except KeyError as exc:
            raise ManagedTelegramExecutionError("Managed publisher not found") from exc
        if publisher.platform != DistributionPlatform.TELEGRAM:
            raise ManagedTelegramExecutionError("Managed publisher is not a Telegram publisher")
        if publisher.ownership != ManagedPublisherOwnership.PARTIZAN_MANAGED:
            raise ManagedTelegramExecutionError(
                "Only Partizan-managed Telegram inventory can install a direct session"
            )
        if publisher.health == ManagedPublisherHealth.RETIRED:
            raise ManagedTelegramExecutionError("Managed Telegram publisher is retired")
        return publisher

    def _active_connection(self, publisher_id: UUID) -> tuple[dict, str]:
        record = self._store.get(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(publisher_id),
        )
        if record is None or record.get("status") != ManagedTelegramConnectionStatus.ACTIVE.value:
            raise ManagedTelegramExecutionError(
                "Managed Telegram publisher has no active session"
            )
        reference = str(record.get("secret_reference") or "")
        session = self._secret_store.get(reference) if reference else None
        if not session:
            raise ManagedTelegramExecutionError(
                "Managed Telegram session is no longer available"
            )
        return record, session

    def _validated_scope(
        self,
        assignment_id: UUID,
        action_id: UUID,
        *,
        require_approved: bool,
    ):
        try:
            assignment = self._managed.get_assignment(assignment_id)
        except KeyError as exc:
            raise ManagedTelegramExecutionError("Managed assignment not found") from exc
        if assignment.status != ManagedAssignmentStatus.RESERVED:
            raise ManagedTelegramExecutionError(
                "Managed Telegram execution requires a RESERVED assignment"
            )
        if assignment.platform != DistributionPlatform.TELEGRAM:
            raise ManagedTelegramExecutionError("Managed assignment is not Telegram")
        try:
            action = self._execution.get_action(action_id)
        except KeyError as exc:
            raise ManagedTelegramExecutionError("DistributionAction not found") from exc
        if action.platform != DistributionPlatform.TELEGRAM:
            raise ManagedTelegramExecutionError("DistributionAction is not Telegram")
        if action.action_type != assignment.action_type:
            raise ManagedTelegramExecutionError(
                "DistributionAction type does not match managed assignment"
            )
        if action.distribution_identity_id != assignment.distribution_identity_id:
            raise ManagedTelegramExecutionError(
                "DistributionAction is not bound to the reserved managed publisher"
            )
        if (
            assignment.opportunity_id is not None
            and action.opportunity_id != assignment.opportunity_id
        ):
            raise ManagedTelegramExecutionError(
                "DistributionAction opportunity does not match managed assignment"
            )
        if require_approved and action.status != DistributionActionStatus.APPROVED:
            raise ManagedTelegramExecutionError(
                "Managed Telegram action must be APPROVED before preview or publish"
            )
        if action.experiment_id is None:
            raise ManagedTelegramExecutionError("DistributionAction has no experiment")
        try:
            experiment = self._execution.get_experiment(action.experiment_id)
        except KeyError as exc:
            raise ManagedTelegramExecutionError("DistributionExperiment not found") from exc
        if experiment.product_id != assignment.product_id:
            raise ManagedTelegramExecutionError(
                "DistributionAction does not belong to the assignment product"
            )
        return assignment, action, experiment

    def _parse_target(
        self,
        action_type: DistributionActionType,
        raw_url: str,
    ) -> TelegramPublishTarget:
        parts = urlsplit(raw_url)
        if parts.scheme != "https" or parts.netloc.lower() not in {"t.me", "www.t.me"}:
            raise ManagedTelegramExecutionError(
                "Managed Telegram publishing accepts only public https://t.me targets"
            )
        path = [part for part in parts.path.split("/") if part]
        if not path or path[0].startswith("+") or path[0].lower() == "joinchat":
            raise ManagedTelegramExecutionError(
                "Private Telegram invite targets are not supported"
            )
        username = path[0]
        if not _USERNAME_PATTERN.fullmatch(username):
            raise ManagedTelegramExecutionError("Telegram public target username is invalid")
        if action_type == DistributionActionType.STANDALONE_POST:
            if len(path) != 1:
                raise ManagedTelegramExecutionError(
                    "Telegram standalone posts require a community URL"
                )
            return TelegramPublishTarget(username=username, reply_to_message_id=None)
        if action_type not in {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
        }:
            raise ManagedTelegramExecutionError(
                "Managed Telegram executor supports comments, replies and standalone posts only"
            )
        if len(path) != 2 or not path[1].isdigit() or int(path[1]) <= 0:
            raise ManagedTelegramExecutionError(
                "Telegram comments/replies require a concrete public message URL"
            )
        return TelegramPublishTarget(username=username, reply_to_message_id=int(path[1]))

    def _fingerprint(self, *, assignment, action, telegram_username: str) -> str:
        material = {
            "assignment_id": str(assignment.id),
            "managed_publisher_id": str(assignment.managed_publisher_id),
            "distribution_identity_id": str(assignment.distribution_identity_id),
            "experiment_arm": assignment.experiment_arm,
            "action_id": str(action.id),
            "experiment_id": str(action.experiment_id),
            "action_type": action.action_type.value,
            "target_url": str(action.target_url or ""),
            "content_text": str(action.content_text or ""),
            "telegram_username": telegram_username.casefold(),
        }
        return hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    def _fulfill_from_receipt(
        self,
        assignment_id: UUID,
        receipt: ManagedTelegramExecutionReceipt,
    ) -> None:
        if not receipt.external_reference or not receipt.executed_url:
            raise ManagedTelegramExecutionError(
                "Managed Telegram receipt has no confirmed remote publication"
            )
        self._managed.fulfill(
            assignment_id,
            ManagedFulfillmentRequest(
                action_id=receipt.action_id,
                external_reference=receipt.external_reference,
                executed_url=receipt.executed_url,
                notes="Confirmed by Partizan-managed Telegram execution transport.",
            ),
        )

    def _connection_view(self, record: dict) -> ManagedTelegramConnectionView:
        return ManagedTelegramConnectionView(
            managed_publisher_id=UUID(str(record["managed_publisher_id"])),
            distribution_identity_id=UUID(str(record["distribution_identity_id"])),
            status=ManagedTelegramConnectionStatus(str(record["status"])),
            username=str(record["username"]),
            display_name=(str(record["display_name"]) if record.get("display_name") else None),
            connected_at=datetime.fromisoformat(str(record["connected_at"])),
            last_verified_at=datetime.fromisoformat(str(record["last_verified_at"])),
        )

    def _persist_receipt(self, receipt: ManagedTelegramExecutionReceipt) -> None:
        self._store.put(
            MANAGED_TELEGRAM_EXECUTION_RECEIPT_NAMESPACE,
            str(receipt.assignment_id),
            receipt.model_dump(mode="json"),
        )


managed_telegram_execution_service = ManagedTelegramExecutionService()

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import (
    DistributionActionExecutionRequest,
    DistributionExecutionPlanView,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_schemas import DistributionActionView
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

EXECUTION_ADAPTER_RECEIPT_NAMESPACE = "distribution_execution_adapter_receipt"
_SECRET_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,120}$")
_TELEGRAM_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{5,32}$")


class AdapterExecutionOutcome(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    EXECUTED = "EXECUTED"
    ASSISTED = "ASSISTED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class DistributionAdapterExecuteRequest(BaseModel):
    retry: bool = False


class ExecutionAdapterReceipt(BaseModel):
    action_id: UUID
    adapter_name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=120)
    outcome: AdapterExecutionOutcome
    message: str = Field(min_length=1, max_length=2000)
    requires_operator_confirmation: bool = False
    external_reference: str | None = Field(default=None, max_length=500)
    executed_url: HttpUrl | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class DistributionAdapterExecutionView(BaseModel):
    receipt: ExecutionAdapterReceipt
    plan: DistributionExecutionPlanView


class ExecutionAdapter(Protocol):
    name: str
    provider: str

    def supports(self, action: DistributionActionView) -> bool: ...

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt: ...


class SecretResolver(Protocol):
    def resolve(self, environment_variable: str) -> str: ...


class EnvironmentSecretResolver:
    def resolve(self, environment_variable: str) -> str:
        if not _SECRET_ENV_PATTERN.fullmatch(environment_variable):
            raise ValueError("Telegram bot token env reference is invalid")
        value = os.getenv(environment_variable)
        if not value:
            raise ValueError(
                f"Telegram bot token secret is not configured in {environment_variable}"
            )
        return value


class TelegramBotClient(Protocol):
    def send_message(self, *, token: str, chat_id: str, text: str) -> dict: ...


class UrlLibTelegramBotClient:
    def send_message(self, *, token: str, chat_id: str, text: str) -> dict:
        encoded_token = quote(token, safe=":-_")
        endpoint = f"https://api.telegram.org/bot{encoded_token}/sendMessage"
        body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
        request = Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            raise RuntimeError("Telegram Bot API request failed") from None
        if not payload.get("ok") or not isinstance(payload.get("result"), dict):
            raise RuntimeError("Telegram Bot API rejected sendMessage")
        return payload["result"]


class TelegramBotExecutionAdapter:
    name = "telegram-bot-send-message"
    provider = "telegram-bot-api"

    def __init__(
        self,
        client: TelegramBotClient | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self._client = client or UrlLibTelegramBotClient()
        self._secret_resolver = secret_resolver or EnvironmentSecretResolver()

    def supports(self, action: DistributionActionView) -> bool:
        if (
            action.platform != DistributionPlatform.TELEGRAM
            or action.action_type != DistributionActionType.STANDALONE_POST
            or action.distribution_identity_id is None
        ):
            return False
        try:
            identity = distribution_control_plane_service.get_identity(
                action.distribution_identity_id
            )
        except KeyError:
            return False
        return (
            str(identity.profile_config.get("execution_provider", "")).strip().lower()
            == "telegram_bot"
        )

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        if action.distribution_identity_id is None:
            return self._unavailable(action, "Telegram execution requires a Distribution Identity")

        identity = distribution_control_plane_service.get_identity(
            action.distribution_identity_id
        )
        config = identity.profile_config
        token_env = str(config.get("bot_token_env", "")).strip()
        if not token_env:
            return self._unavailable(
                action,
                "Telegram identity has no bot_token_env secret reference",
            )

        try:
            chat_id = self._chat_id_from_target(action)
        except ValueError as exc:
            return self._unavailable(action, str(exc))

        allowed_targets = {
            self._normalize_chat_id(str(value))
            for value in config.get("allowed_execution_targets", [])
            if str(value).strip()
        }
        if not allowed_targets or chat_id not in allowed_targets:
            return self._unavailable(
                action,
                "Telegram target is not explicitly allowlisted for this Distribution Identity",
            )

        if not str(action.content_text or "").strip():
            return self._unavailable(action, "Telegram standalone post has no approved content")

        try:
            token = self._secret_resolver.resolve(token_env)
        except ValueError as exc:
            return self._unavailable(action, str(exc))

        try:
            result = self._client.send_message(
                token=token,
                chat_id=chat_id,
                text=str(action.content_text),
            )
        except Exception:
            return ExecutionAdapterReceipt(
                action_id=action.id,
                adapter_name=self.name,
                provider=self.provider,
                outcome=AdapterExecutionOutcome.FAILED,
                message="Telegram Bot API execution failed; action remains approved for retry.",
                metadata={
                    "platform": action.platform.value,
                    "chat_id": chat_id,
                    "secret_env": token_env,
                },
                created_at=datetime.now(UTC),
            )

        message_id = result.get("message_id")
        if not isinstance(message_id, int):
            return ExecutionAdapterReceipt(
                action_id=action.id,
                adapter_name=self.name,
                provider=self.provider,
                outcome=AdapterExecutionOutcome.FAILED,
                message="Telegram response did not contain a valid message_id.",
                metadata={
                    "platform": action.platform.value,
                    "chat_id": chat_id,
                    "secret_env": token_env,
                },
                created_at=datetime.now(UTC),
            )

        username = chat_id.removeprefix("@")
        executed_url = f"https://t.me/{username}/{message_id}"
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.EXECUTED,
            message="Telegram Bot API confirmed sendMessage execution.",
            external_reference=f"telegram:{chat_id}:{message_id}",
            executed_url=executed_url,
            metadata={
                "platform": action.platform.value,
                "chat_id": chat_id,
                "message_id": message_id,
                "secret_env": token_env,
            },
            created_at=datetime.now(UTC),
        )

    def _chat_id_from_target(self, action: DistributionActionView) -> str:
        if action.target_url is None:
            raise ValueError("Telegram standalone post requires a public target URL")
        parts = urlsplit(str(action.target_url))
        if parts.scheme != "https" or parts.netloc.lower() not in {"t.me", "www.t.me"}:
            raise ValueError("Telegram Bot adapter accepts only public https://t.me/<username> targets")
        path_parts = [part for part in parts.path.split("/") if part]
        if len(path_parts) != 1:
            raise ValueError("Telegram Bot adapter requires a chat surface, not a message/invite URL")
        username = path_parts[0]
        if username.startswith("+") or username.lower() == "joinchat":
            raise ValueError("Telegram private invite targets are not supported")
        if not _TELEGRAM_USERNAME_PATTERN.fullmatch(username):
            raise ValueError("Telegram public target username is invalid")
        return self._normalize_chat_id(username)

    def _normalize_chat_id(self, value: str) -> str:
        normalized = value.strip().removeprefix("@").lower()
        return f"@{normalized}"

    def _unavailable(
        self,
        action: DistributionActionView,
        message: str,
    ) -> ExecutionAdapterReceipt:
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.UNAVAILABLE,
            message=message,
            metadata={"platform": action.platform.value},
            created_at=datetime.now(UTC),
        )


class AssistedCommunityExecutionAdapter:
    name = "assisted-community"
    provider = "operator"

    _ACTION_TYPES = {
        DistributionActionType.COMMENT,
        DistributionActionType.REPLY,
        DistributionActionType.STANDALONE_POST,
    }

    def supports(self, action: DistributionActionView) -> bool:
        return action.action_type in self._ACTION_TYPES

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.ASSISTED,
            message=(
                "This third-party community action is prepared and approved, but the current "
                "MVP has no verified universal platform execution path for this target. Complete "
                "the action through the approved operator flow, then use mark-executed with the "
                "real external reference."
            ),
            requires_operator_confirmation=True,
            metadata={
                "platform": action.platform.value,
                "action_type": action.action_type.value,
                "target_url": str(action.target_url) if action.target_url else None,
            },
            created_at=datetime.now(UTC),
        )


class UnavailableOwnedExecutionAdapter:
    name = "owned-content-unavailable"
    provider = "not-configured"

    def supports(self, action: DistributionActionView) -> bool:
        return action.action_type == DistributionActionType.ORGANIC_VIDEO

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.UNAVAILABLE,
            message=(
                "No compliant owned-content execution provider is configured for this identity. "
                "For TikTok, Direct Post must use a connected creator flow and the required "
                "platform approval/audit before public automated posting is treated as supported."
            ),
            metadata={
                "platform": action.platform.value,
                "action_type": action.action_type.value,
            },
            created_at=datetime.now(UTC),
        )


class UnavailablePaidExecutionAdapter:
    name = "paid-campaign-unavailable"
    provider = "not-configured"

    def supports(self, action: DistributionActionView) -> bool:
        return action.action_type == DistributionActionType.PAID_CAMPAIGN

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.UNAVAILABLE,
            message=(
                "The paid campaign is prepared and approved, but no authenticated ad-platform "
                "provider is configured. Partizan will not create spend or mark the action "
                "executed until a provider confirms campaign creation."
            ),
            metadata={
                "platform": action.platform.value,
                "action_type": action.action_type.value,
            },
            created_at=datetime.now(UTC),
        )


class ConfirmedMockExecutionAdapter:
    """Deterministic adapter used only by tests/local integration harnesses."""

    name = "confirmed-mock"
    provider = "mock"

    def supports(self, action: DistributionActionView) -> bool:
        return True

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        external_reference = f"mock:{action.platform.value.lower()}:{action.id}"
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.EXECUTED,
            message="Mock provider confirmed execution.",
            external_reference=external_reference,
            executed_url=f"https://execution.example/{action.id}",
            metadata={"test_only": True},
            created_at=datetime.now(UTC),
        )


class ExecutionAdapterRegistry:
    def __init__(self, adapters: list[ExecutionAdapter] | None = None) -> None:
        self._adapters = adapters or [
            TelegramBotExecutionAdapter(),
            AssistedCommunityExecutionAdapter(),
            UnavailableOwnedExecutionAdapter(),
            UnavailablePaidExecutionAdapter(),
        ]

    def resolve(self, action: DistributionActionView) -> ExecutionAdapter:
        for adapter in self._adapters:
            if adapter.supports(action):
                return adapter
        raise ValueError(
            f"No execution adapter registered for {action.platform.value}/{action.action_type.value}"
        )


class DistributionExecutionAdapterService:
    def __init__(
        self,
        registry: ExecutionAdapterRegistry | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._registry = registry or ExecutionAdapterRegistry()
        self._store = store or get_runtime_store()

    def execute(
        self,
        action_id: UUID,
        payload: DistributionAdapterExecuteRequest,
    ) -> DistributionAdapterExecutionView:
        action = distribution_execution_service.get_action(action_id)

        existing = self.get_receipt(action_id)
        if existing is not None and not payload.retry:
            return DistributionAdapterExecutionView(
                receipt=existing,
                plan=distribution_execution_service.get_plan(action_id),
            )

        if action.status == DistributionActionStatus.EXECUTED:
            if existing is None:
                existing = self._operator_confirmed_receipt(action)
                self._persist(existing)
            return DistributionAdapterExecutionView(
                receipt=existing,
                plan=distribution_execution_service.get_plan(action_id),
            )

        if action.status != DistributionActionStatus.APPROVED:
            raise ValueError("Action must be APPROVED before an execution adapter can run")

        adapter = self._registry.resolve(action)
        in_progress = ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=adapter.name,
            provider=adapter.provider,
            outcome=AdapterExecutionOutcome.IN_PROGRESS,
            message="Execution adapter attempt started; retry requires an explicit retry=true.",
            metadata={
                "platform": action.platform.value,
                "action_type": action.action_type.value,
            },
            created_at=datetime.now(UTC),
        )
        self._persist(in_progress)

        try:
            receipt = adapter.execute(action)
        except Exception:
            receipt = ExecutionAdapterReceipt(
                action_id=action.id,
                adapter_name=adapter.name,
                provider=adapter.provider,
                outcome=AdapterExecutionOutcome.FAILED,
                message="Execution adapter failed without a confirmed external result.",
                created_at=datetime.now(UTC),
            )

        if receipt.action_id != action.id:
            raise ValueError("Execution adapter returned a receipt for a different action")
        self._persist(receipt)

        if receipt.outcome == AdapterExecutionOutcome.EXECUTED:
            plan = distribution_execution_service.mark_executed(
                action.id,
                DistributionActionExecutionRequest(
                    external_reference=receipt.external_reference,
                    executed_url=receipt.executed_url,
                    notes=(
                        f"Confirmed by execution adapter {receipt.adapter_name} "
                        f"({receipt.provider})."
                    ),
                ),
            )
        else:
            plan = distribution_execution_service.get_plan(action.id)

        return DistributionAdapterExecutionView(receipt=receipt, plan=plan)

    def get_receipt(self, action_id: UUID) -> ExecutionAdapterReceipt | None:
        payload = self._store.get(EXECUTION_ADAPTER_RECEIPT_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return ExecutionAdapterReceipt.model_validate(payload)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(EXECUTION_ADAPTER_RECEIPT_NAMESPACE)

    def _persist(self, receipt: ExecutionAdapterReceipt) -> None:
        self._store.put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(receipt.action_id),
            receipt.model_dump(mode="json"),
        )

    def _operator_confirmed_receipt(
        self,
        action: DistributionActionView,
    ) -> ExecutionAdapterReceipt:
        metadata = action.operational_metadata
        executed_url = metadata.get("executed_url")
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name="operator-confirmed",
            provider="operator",
            outcome=AdapterExecutionOutcome.EXECUTED,
            message="Action had already been confirmed as executed by the operator flow.",
            requires_operator_confirmation=False,
            external_reference=metadata.get("external_reference"),
            executed_url=executed_url,
            metadata={"reconstructed": True},
            created_at=action.executed_at or datetime.now(UTC),
        )


distribution_execution_adapter_service = DistributionExecutionAdapterService()

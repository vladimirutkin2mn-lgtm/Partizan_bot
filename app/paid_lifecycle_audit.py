from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.distribution_execution_service import DISTRIBUTION_ACTION_NAMESPACE
from app.distribution_schemas import DistributionActionView
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    AdapterExecutionOutcome,
    ExecutionAdapterReceipt,
)
from app.meta_paid_control import META_PAID_CONTROL_NAMESPACE, MetaPaidControlSnapshotView
from app.paid_activation import (
    PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
    PaidActivationAuthorizationView,
)
from app.paid_campaign import PAID_CAMPAIGN_SPEC_NAMESPACE, PaidCampaignSpec
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_paid_activation import (
    TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
    TikTokPaidActivationAuthorizationView,
)
from app.tiktok_paid_control import (
    TIKTOK_PAID_CONTROL_NAMESPACE,
    TikTokPaidControlSnapshotView,
)

PAID_AUDIT_EVENT_NAMESPACE = "paid_audit_event"
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(access[_ -]?token|api[_ -]?key|operator[_ -]?key|authorization)\b\s*[:=]\s*[^\s,;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/=]+")


class PaidLifecycleState(StrEnum):
    NOT_STAGED = "NOT_STAGED"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    STAGED = "STAGED"
    AUTHORIZED = "AUTHORIZED"
    ACTIVATION_ATTEMPTED = "ACTIVATION_ATTEMPTED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    UNKNOWN = "UNKNOWN"


class PaidLifecycleNextAction(StrEnum):
    STAGE_PROVIDER = "STAGE_PROVIDER"
    AUTHORIZE_ACTIVATION = "AUTHORIZE_ACTIVATION"
    ACTIVATE = "ACTIVATE"
    SYNC_OR_PAUSE = "SYNC_OR_PAUSE"
    RECONCILE = "RECONCILE"
    NONE = "NONE"


class PaidAuditActor(StrEnum):
    OPERATOR = "operator"
    WORKER = "worker"
    SYSTEM = "system"


class PaidAuditEventType(StrEnum):
    PROVIDER_STAGING = "PROVIDER_STAGING"
    ACTIVATION_AUTHORIZED = "ACTIVATION_AUTHORIZED"
    ACTIVATION_ATTEMPTED = "ACTIVATION_ATTEMPTED"
    ACTIVATION_SUCCEEDED = "ACTIVATION_SUCCEEDED"
    ACTIVATION_FAILED = "ACTIVATION_FAILED"
    CONTROL_SYNC = "CONTROL_SYNC"
    PROVIDER_PAUSE = "PROVIDER_PAUSE"
    RECONCILIATION_SYNC = "RECONCILIATION_SYNC"


class PaidAuditResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


class PaidLifecycleView(BaseModel):
    action_id: UUID
    experiment_id: UUID | None = None
    provider: str
    platform: DistributionPlatform
    state: PaidLifecycleState
    safe_next_action: PaidLifecycleNextAction
    action_status: str
    receipt_outcome: str | None = None
    authorization_id: UUID | None = None
    authorization_expires_at: datetime | None = None
    provider_status: str | None = None
    budget_cap: float | None = Field(default=None, gt=0)
    provider_spend: float | None = Field(default=None, ge=0)
    synced_spend: float | None = Field(default=None, ge=0)
    pause_state: str | None = None
    pause_reason: str | None = None
    requires_reconciliation: bool = False
    provider_object_ids: dict[str, str] = Field(default_factory=dict)
    last_error: str | None = None
    observed_at: datetime


class PaidAuditEventView(BaseModel):
    id: UUID
    action_id: UUID
    experiment_id: UUID | None = None
    provider: str
    event_type: PaidAuditEventType
    actor: PaidAuditActor
    result: PaidAuditResult
    occurred_at: datetime
    correlation_id: str | None = Field(default=None, max_length=200)
    previous_state: PaidLifecycleState | None = None
    new_state: PaidLifecycleState | None = None
    budget_cap: float | None = Field(default=None, gt=0)
    provider_spend: float | None = Field(default=None, ge=0)
    synced_spend: float | None = Field(default=None, ge=0)
    pause_state: str | None = None
    requires_reconciliation: bool = False
    provider_object_ids: dict[str, str] = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=1000)
    fingerprint: str = Field(min_length=1, max_length=4000)


class PaidLifecycleService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def get(self, action_id: UUID) -> PaidLifecycleView:
        action = self._action(action_id)
        receipt = self._receipt(action_id)
        provider = self._provider(action, receipt)
        spec = self._spec(action_id)
        authorization = self._latest_authorization(action_id, provider)
        control = self._control(action_id, provider)
        provider_ids = self._provider_ids(receipt)

        requires_reconciliation = bool(
            receipt is not None and receipt.metadata.get("requires_reconciliation")
        )
        provider_status: str | None = None
        provider_spend: float | None = None
        synced_spend: float | None = None
        pause_state: str | None = None
        pause_reason: str | None = None
        last_error: str | None = None

        if isinstance(control, MetaPaidControlSnapshotView):
            provider_status = f"{control.configured_status}/{control.effective_status}"
            provider_spend = control.provider_spend
            synced_spend = control.synced_spend
            pause_state = control.pause_state
            pause_reason = control.pause_reason
            last_error = control.last_error
            requires_reconciliation = (
                requires_reconciliation
                or control.requires_reconciliation
                or control.sync_state == "UNKNOWN"
                or control.pause_state == "UNKNOWN"
            )
        elif isinstance(control, TikTokPaidControlSnapshotView):
            parts = [control.operation_status]
            if control.primary_status:
                parts.append(control.primary_status)
            if control.secondary_status:
                parts.append(control.secondary_status)
            provider_status = "/".join(parts)
            provider_spend = control.provider_spend
            synced_spend = control.synced_spend
            pause_state = control.pause_state
            pause_reason = control.pause_reason
            last_error = control.last_error
            requires_reconciliation = (
                requires_reconciliation
                or control.requires_reconciliation
                or control.sync_state == "UNKNOWN"
                or control.pause_state == "UNKNOWN"
            )

        state = self._state(
            receipt=receipt,
            authorization=authorization,
            control=control,
            requires_reconciliation=requires_reconciliation,
        )
        return PaidLifecycleView(
            action_id=action.id,
            experiment_id=action.experiment_id,
            provider=provider,
            platform=action.platform,
            state=state,
            safe_next_action=self._next_action(state),
            action_status=action.status.value,
            receipt_outcome=receipt.outcome.value if receipt else None,
            authorization_id=authorization.id if authorization else None,
            authorization_expires_at=authorization.expires_at if authorization else None,
            provider_status=provider_status,
            budget_cap=spec.budget_cap if spec else None,
            provider_spend=provider_spend,
            synced_spend=synced_spend,
            pause_state=pause_state,
            pause_reason=pause_reason,
            requires_reconciliation=requires_reconciliation,
            provider_object_ids=provider_ids,
            last_error=self._sanitize_text(last_error),
            observed_at=datetime.now(UTC),
        )

    def _action(self, action_id: UUID) -> DistributionActionView:
        payload = self._store.get(DISTRIBUTION_ACTION_NAMESPACE, str(action_id))
        if payload is None:
            raise KeyError(action_id)
        action = DistributionActionView.model_validate(payload)
        if action.action_type != DistributionActionType.PAID_CAMPAIGN:
            raise ValueError("Paid lifecycle supports PAID_CAMPAIGN actions only")
        if action.platform not in {
            DistributionPlatform.INSTAGRAM,
            DistributionPlatform.TIKTOK,
        }:
            raise ValueError("Paid lifecycle currently supports Meta/Instagram and TikTok only")
        return action

    def _receipt(self, action_id: UUID) -> ExecutionAdapterReceipt | None:
        payload = self._store.get(EXECUTION_ADAPTER_RECEIPT_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return ExecutionAdapterReceipt.model_validate(payload)

    def _provider(
        self,
        action: DistributionActionView,
        receipt: ExecutionAdapterReceipt | None,
    ) -> str:
        if receipt and receipt.provider in {"meta-marketing-api", "tiktok-marketing-api"}:
            return receipt.provider
        if action.platform == DistributionPlatform.INSTAGRAM:
            return "meta-marketing-api"
        return "tiktok-marketing-api"

    def _spec(self, action_id: UUID) -> PaidCampaignSpec | None:
        payload = self._store.get(PAID_CAMPAIGN_SPEC_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return PaidCampaignSpec.model_validate(payload)

    def _latest_authorization(
        self,
        action_id: UUID,
        provider: str,
    ) -> PaidActivationAuthorizationView | TikTokPaidActivationAuthorizationView | None:
        if provider == "meta-marketing-api":
            namespace = PAID_ACTIVATION_AUTHORIZATION_NAMESPACE
            model = PaidActivationAuthorizationView
        else:
            namespace = TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE
            model = TikTokPaidActivationAuthorizationView
        candidates = []
        for payload in self._store.list_namespace(namespace):
            authorization = model.model_validate(payload)
            if authorization.action_id == action_id:
                candidates.append(authorization)
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        return candidates[0]

    def _control(
        self,
        action_id: UUID,
        provider: str,
    ) -> MetaPaidControlSnapshotView | TikTokPaidControlSnapshotView | None:
        if provider == "meta-marketing-api":
            payload = self._store.get(META_PAID_CONTROL_NAMESPACE, str(action_id))
            return MetaPaidControlSnapshotView.model_validate(payload) if payload else None
        payload = self._store.get(TIKTOK_PAID_CONTROL_NAMESPACE, str(action_id))
        return TikTokPaidControlSnapshotView.model_validate(payload) if payload else None

    def _provider_ids(self, receipt: ExecutionAdapterReceipt | None) -> dict[str, str]:
        if receipt is None:
            return {}
        raw = receipt.metadata.get("provider_ids")
        if not isinstance(raw, dict):
            raw = receipt.metadata.get("partial_provider_ids")
        if not isinstance(raw, dict):
            return {}
        safe: dict[str, str] = {}
        for key, value in raw.items():
            normalized_key = str(key)
            if not normalized_key.endswith("_id"):
                continue
            normalized_value = str(value).strip()
            if normalized_value:
                safe[normalized_key[:80]] = normalized_value[:300]
        return safe

    def _state(
        self,
        *,
        receipt: ExecutionAdapterReceipt | None,
        authorization: PaidActivationAuthorizationView
        | TikTokPaidActivationAuthorizationView
        | None,
        control: MetaPaidControlSnapshotView | TikTokPaidControlSnapshotView | None,
        requires_reconciliation: bool,
    ) -> PaidLifecycleState:
        if requires_reconciliation:
            return PaidLifecycleState.RECONCILIATION_REQUIRED
        if control is not None and control.pause_state == "CONFIRMED":
            return PaidLifecycleState.PAUSED
        if receipt is None:
            return PaidLifecycleState.NOT_STAGED
        if receipt.outcome == AdapterExecutionOutcome.FAILED:
            return PaidLifecycleState.PROVIDER_FAILED
        if receipt.outcome == AdapterExecutionOutcome.EXECUTED:
            return PaidLifecycleState.ACTIVE
        if receipt.outcome != AdapterExecutionOutcome.STAGED:
            return PaidLifecycleState.NOT_STAGED
        if authorization is None:
            return PaidLifecycleState.STAGED
        if authorization.consumed_at is not None:
            return PaidLifecycleState.ACTIVE
        if authorization.attempted_at is not None:
            return PaidLifecycleState.ACTIVATION_ATTEMPTED
        if authorization.expires_at > datetime.now(UTC):
            return PaidLifecycleState.AUTHORIZED
        return PaidLifecycleState.STAGED

    def _next_action(self, state: PaidLifecycleState) -> PaidLifecycleNextAction:
        if state in {PaidLifecycleState.NOT_STAGED, PaidLifecycleState.PROVIDER_FAILED}:
            return PaidLifecycleNextAction.STAGE_PROVIDER
        if state == PaidLifecycleState.STAGED:
            return PaidLifecycleNextAction.AUTHORIZE_ACTIVATION
        if state == PaidLifecycleState.AUTHORIZED:
            return PaidLifecycleNextAction.ACTIVATE
        if state in {
            PaidLifecycleState.ACTIVATION_ATTEMPTED,
            PaidLifecycleState.RECONCILIATION_REQUIRED,
            PaidLifecycleState.UNKNOWN,
        }:
            return PaidLifecycleNextAction.RECONCILE
        if state == PaidLifecycleState.ACTIVE:
            return PaidLifecycleNextAction.SYNC_OR_PAUSE
        return PaidLifecycleNextAction.NONE

    def _sanitize_text(self, value: str | None) -> str | None:
        if not value:
            return None
        sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1=<redacted>", value)
        sanitized = _BEARER_PATTERN.sub("Bearer <redacted>", sanitized)
        return sanitized[:1000]


class PaidAuditLedger:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        lifecycle_service: PaidLifecycleService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._lifecycle = lifecycle_service or PaidLifecycleService(self._store)

    def record(
        self,
        *,
        action_id: UUID,
        event_type: PaidAuditEventType,
        actor: PaidAuditActor,
        result: PaidAuditResult,
        before: PaidLifecycleView | None = None,
        after: PaidLifecycleView | None = None,
        correlation_id: str | UUID | None = None,
        reason: str | None = None,
        deduplicate: bool = False,
    ) -> PaidAuditEventView:
        current = after or self._lifecycle.get(action_id)
        reason = self._sanitize_text(reason or current.last_error)
        normalized_correlation = str(correlation_id)[:200] if correlation_id else None
        fingerprint = self._fingerprint(
            event_type=event_type,
            actor=actor,
            result=result,
            previous_state=before.state if before else None,
            current=current,
            correlation_id=normalized_correlation,
            reason=reason,
        )
        if deduplicate:
            existing = self._latest_matching(action_id, event_type, actor)
            if existing is not None and existing.fingerprint == fingerprint:
                return existing
        event = PaidAuditEventView(
            id=uuid4(),
            action_id=current.action_id,
            experiment_id=current.experiment_id,
            provider=current.provider,
            event_type=event_type,
            actor=actor,
            result=result,
            occurred_at=datetime.now(UTC),
            correlation_id=normalized_correlation,
            previous_state=before.state if before else None,
            new_state=current.state,
            budget_cap=current.budget_cap,
            provider_spend=current.provider_spend,
            synced_spend=current.synced_spend,
            pause_state=current.pause_state,
            requires_reconciliation=current.requires_reconciliation,
            provider_object_ids=current.provider_object_ids,
            reason=reason,
            fingerprint=fingerprint,
        )
        self._store.put(
            PAID_AUDIT_EVENT_NAMESPACE,
            str(event.id),
            event.model_dump(mode="json"),
        )
        return event

    def query(
        self,
        *,
        action_id: UUID | None = None,
        provider: str | None = None,
        event_type: PaidAuditEventType | None = None,
        actor: PaidAuditActor | None = None,
        limit: int = 50,
    ) -> list[PaidAuditEventView]:
        if limit < 1 or limit > 200:
            raise ValueError("audit limit must be between 1 and 200")
        events = [
            PaidAuditEventView.model_validate(payload)
            for payload in self._store.list_namespace(PAID_AUDIT_EVENT_NAMESPACE)
        ]
        if action_id is not None:
            events = [item for item in events if item.action_id == action_id]
        if provider is not None:
            events = [item for item in events if item.provider == provider]
        if event_type is not None:
            events = [item for item in events if item.event_type == event_type]
        if actor is not None:
            events = [item for item in events if item.actor == actor]
        events.sort(key=lambda item: (item.occurred_at, str(item.id)), reverse=True)
        return events[:limit]

    def _latest_matching(
        self,
        action_id: UUID,
        event_type: PaidAuditEventType,
        actor: PaidAuditActor,
    ) -> PaidAuditEventView | None:
        matches = self.query(
            action_id=action_id,
            event_type=event_type,
            actor=actor,
            limit=1,
        )
        return matches[0] if matches else None

    def _fingerprint(
        self,
        *,
        event_type: PaidAuditEventType,
        actor: PaidAuditActor,
        result: PaidAuditResult,
        previous_state: PaidLifecycleState | None,
        current: PaidLifecycleView,
        correlation_id: str | None,
        reason: str | None,
    ) -> str:
        payload = {
            "event_type": event_type.value,
            "actor": actor.value,
            "result": result.value,
            "previous_state": previous_state.value if previous_state else None,
            "new_state": current.state.value,
            "provider_status": current.provider_status,
            "provider_spend": current.provider_spend,
            "synced_spend": current.synced_spend,
            "pause_state": current.pause_state,
            "requires_reconciliation": current.requires_reconciliation,
            "correlation_id": correlation_id,
            "reason": reason,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))[:4000]

    def _sanitize_text(self, value: str | None) -> str | None:
        if not value:
            return None
        sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1=<redacted>", value)
        sanitized = _BEARER_PATTERN.sub("Bearer <redacted>", sanitized)
        return sanitized[:1000]


paid_lifecycle_service = PaidLifecycleService()
paid_audit_ledger = PaidAuditLedger(lifecycle_service=paid_lifecycle_service)

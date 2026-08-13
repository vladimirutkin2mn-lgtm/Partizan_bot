from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.autonomy_schemas import AutonomyDecision, AutonomyEvaluationRequest, GrowthMandateStatus
from app.autonomy_service import growth_mandate_service
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionType
from app.outreach_briefs import (
    OUTREACH_BRIEF_NAMESPACE,
    OutreachBriefStatus,
    OutreachBriefView,
    outreach_brief_service,
)
from app.outreach_policy import (
    OutreachAutonomousPreparationService,
    OutreachPolicyStatus,
    OutreachPolicyView,
    outreach_autonomous_preparation_service,
    outreach_policy_service,
)
from app.outreach_sender import (
    OUTREACH_SEND_ATTEMPT_NAMESPACE,
    OutreachSendAttemptStatus,
    OutreachSendAttemptView,
    OutreachSendAuthorizationCreateRequest,
    outreach_sender_service,
)
from app.outreach_targets import OutreachTargetView, outreach_target_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

OUTREACH_AUTOSEND_DELEGATION_NAMESPACE = "outreach_autosend_delegation"
OUTREACH_AUTOSEND_DAILY_SLOT_NAMESPACE = "outreach_autosend_daily_slot"
OUTREACH_AUTOSEND_DOMAIN_DAILY_SLOT_NAMESPACE = "outreach_autosend_domain_daily_slot"
OUTREACH_AUTOSEND_TARGET_COOLDOWN_NAMESPACE = "outreach_autosend_target_cooldown"
OUTREACH_AUTOSEND_DOMAIN_COOLDOWN_NAMESPACE = "outreach_autosend_domain_cooldown"


class OutreachAutoSendDelegationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REVOKED = "REVOKED"


class OutreachAutoSendDelegationCreateRequest(BaseModel):
    confirm_autonomous_initial_send: bool = False


class OutreachAutoSendDelegationStatusRequest(BaseModel):
    status: OutreachAutoSendDelegationStatus


class OutreachAutoSendDelegationView(BaseModel):
    id: UUID
    product_id: UUID
    version: int = Field(ge=1)
    status: OutreachAutoSendDelegationStatus
    outreach_policy_id: UUID
    outreach_policy_version: int = Field(ge=1)
    growth_mandate_id: UUID
    growth_mandate_version: int = Field(ge=1)
    sender_email: str
    sender_name: str
    reply_to: str
    max_initial_sends_per_day: int = Field(ge=1, le=5)
    max_initial_sends_per_domain_per_day: int = Field(ge=1, le=1)
    target_cooldown_days: int = Field(ge=30)
    domain_cooldown_hours: int = Field(ge=24)
    max_followups: int = Field(default=0, ge=0, le=0)
    created_at: datetime
    updated_at: datetime


class OutreachAutonomousSendOutcome(StrEnum):
    SENT = "SENT"
    REJECTED = "REJECTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    BLOCKED = "BLOCKED"


class OutreachAutonomousSendView(BaseModel):
    product_id: UUID
    delegation_id: UUID
    delegation_version: int
    outcome: OutreachAutonomousSendOutcome
    target_id: UUID | None = None
    brief_id: UUID | None = None
    play_id: UUID | None = None
    action_id: UUID | None = None
    experiment_id: UUID | None = None
    platform: str | None = None
    send_attempt_id: UUID | None = None
    reasons: list[str] = Field(default_factory=list)


class OutreachAutoSendDelegationService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._delegations: dict[UUID, OutreachAutoSendDelegationView] = {}

    def delegate(
        self,
        product_id: UUID,
        payload: OutreachAutoSendDelegationCreateRequest,
    ) -> OutreachAutoSendDelegationView:
        if not payload.confirm_autonomous_initial_send:
            raise ValueError(
                "Explicit confirmation of bounded autonomous initial outreach sending is required"
            )
        policy = outreach_policy_service.get(product_id)
        blockers = outreach_policy_service.validate_current_mandate(policy)
        if policy.status != OutreachPolicyStatus.ACTIVE:
            blockers.insert(0, f"Outreach Policy is {policy.status.value}")
        mandate = growth_mandate_service.get(product_id)
        if mandate.status != GrowthMandateStatus.ACTIVE:
            blockers.append(f"Growth Mandate is {mandate.status.value}")
        if DistributionActionType.OUTREACH_EMAIL not in mandate.allowed_actions:
            blockers.append("Growth Mandate must explicitly allow OUTREACH_EMAIL")
        if not mandate.autonomous_prepare:
            blockers.append("Growth Mandate must delegate autonomous preparation")
        if not mandate.autonomous_approve:
            blockers.append("Growth Mandate must delegate autonomous approval for auto-send")
        if policy.max_followups != 0:
            blockers.append("Autonomous outreach follow-ups must remain disabled")
        readiness = outreach_sender_service.readiness()
        if not readiness.ready:
            blockers.extend(["Outreach sender is not ready", *readiness.blockers])
        if blockers:
            raise ValueError("; ".join(dict.fromkeys(blockers)))
        assert readiness.from_email is not None
        assert readiness.from_name is not None
        assert readiness.reply_to is not None

        now = datetime.now(UTC)
        existing = self._get_optional(product_id)
        if existing is None or existing.status == OutreachAutoSendDelegationStatus.REVOKED:
            delegation_id = uuid4()
            created_at = now
            version = (existing.version + 1) if existing is not None else 1
        else:
            delegation_id = existing.id
            created_at = existing.created_at
            version = existing.version + 1
        delegation = OutreachAutoSendDelegationView(
            id=delegation_id,
            product_id=product_id,
            version=version,
            status=OutreachAutoSendDelegationStatus.ACTIVE,
            outreach_policy_id=policy.id,
            outreach_policy_version=policy.version,
            growth_mandate_id=mandate.id,
            growth_mandate_version=mandate.version,
            sender_email=readiness.from_email,
            sender_name=readiness.from_name,
            reply_to=readiness.reply_to,
            max_initial_sends_per_day=policy.max_initial_sends_per_day,
            max_initial_sends_per_domain_per_day=(
                policy.max_initial_sends_per_domain_per_day
            ),
            target_cooldown_days=policy.target_cooldown_days,
            domain_cooldown_hours=policy.domain_cooldown_hours,
            created_at=created_at,
            updated_at=now,
        )
        self._delegations[product_id] = delegation
        self._persist(delegation)
        return delegation

    def get(self, product_id: UUID) -> OutreachAutoSendDelegationView:
        delegation = self._get_optional(product_id)
        if delegation is None:
            raise KeyError(product_id)
        return delegation

    def get_optional(self, product_id: UUID) -> OutreachAutoSendDelegationView | None:
        return self._get_optional(product_id)

    def set_status(
        self,
        product_id: UUID,
        status: OutreachAutoSendDelegationStatus,
    ) -> OutreachAutoSendDelegationView:
        delegation = self.get(product_id)
        if delegation.status == OutreachAutoSendDelegationStatus.REVOKED:
            raise ValueError("A REVOKED outreach auto-send delegation cannot be reactivated")
        if delegation.status == status:
            return delegation
        updated = delegation.model_copy(
            update={
                "status": status,
                "version": delegation.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._delegations[product_id] = updated
        self._persist(updated)
        return updated

    def validate_current(self, delegation: OutreachAutoSendDelegationView) -> list[str]:
        reasons: list[str] = []
        if delegation.status != OutreachAutoSendDelegationStatus.ACTIVE:
            reasons.append(f"Outreach auto-send delegation is {delegation.status.value}")
        try:
            policy = outreach_policy_service.get(delegation.product_id)
        except KeyError:
            return [*reasons, "Outreach Policy is unavailable"]
        if policy.status != OutreachPolicyStatus.ACTIVE:
            reasons.append(f"Outreach Policy is {policy.status.value}")
        if (
            policy.id != delegation.outreach_policy_id
            or policy.version != delegation.outreach_policy_version
        ):
            reasons.append(
                "Outreach Policy changed after auto-send was delegated; delegate auto-send again"
            )
        reasons.extend(outreach_policy_service.validate_current_mandate(policy))
        try:
            mandate = growth_mandate_service.get(delegation.product_id)
        except KeyError:
            return list(dict.fromkeys([*reasons, "Growth Mandate is unavailable"]))
        if (
            mandate.id != delegation.growth_mandate_id
            or mandate.version != delegation.growth_mandate_version
        ):
            reasons.append(
                "Growth Mandate changed after auto-send was delegated; delegate auto-send again"
            )
        if not mandate.autonomous_approve:
            reasons.append("Growth Mandate no longer delegates autonomous approval")
        readiness = outreach_sender_service.readiness()
        if not readiness.ready:
            reasons.extend(["Outreach sender is not ready", *readiness.blockers])
        elif (
            readiness.from_email != delegation.sender_email
            or readiness.from_name != delegation.sender_name
            or readiness.reply_to != delegation.reply_to
        ):
            reasons.append(
                "Outreach sender identity changed after auto-send was delegated; delegate again"
            )
        return list(dict.fromkeys(reasons))

    def reset(self) -> None:
        self._delegations.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(OUTREACH_AUTOSEND_DELEGATION_NAMESPACE)

    def _get_optional(self, product_id: UUID) -> OutreachAutoSendDelegationView | None:
        cached = self._delegations.get(product_id)
        if cached is not None:
            return cached
        row = self._store.get(OUTREACH_AUTOSEND_DELEGATION_NAMESPACE, str(product_id))
        if row is None:
            return None
        delegation = OutreachAutoSendDelegationView.model_validate(row)
        self._delegations[product_id] = delegation
        return delegation

    def _persist(self, delegation: OutreachAutoSendDelegationView) -> None:
        self._store.put(
            OUTREACH_AUTOSEND_DELEGATION_NAMESPACE,
            str(delegation.product_id),
            delegation.model_dump(mode="json"),
        )


class OutreachAutonomousSendService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        delegation_service: OutreachAutoSendDelegationService | None = None,
        preparation_service: OutreachAutonomousPreparationService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._delegation_service = delegation_service or outreach_autosend_delegation_service
        self._preparation_service = preparation_service or outreach_autonomous_preparation_service

    async def run_next(self, product_id: UUID) -> OutreachAutonomousSendView | None:
        delegation = self._delegation_service.get_optional(product_id)
        if delegation is None:
            return None
        blockers = self._delegation_service.validate_current(delegation)
        if blockers:
            return None
        reconciliation = self._product_reconciliation_blocker(product_id)
        if reconciliation is not None:
            return self._view(
                delegation,
                outcome=OutreachAutonomousSendOutcome.BLOCKED,
                reasons=[reconciliation],
            )

        brief = self._pending_outreach_brief(product_id)
        if brief is None:
            prepared = await self._preparation_service.prepare_next(product_id)
            if prepared is None or not prepared.prepared or prepared.brief_id is None:
                return None
            brief = outreach_brief_service.get(prepared.brief_id)
        return await self._send_brief(delegation, brief)

    async def _send_brief(
        self,
        delegation: OutreachAutoSendDelegationView,
        brief: OutreachBriefView,
    ) -> OutreachAutonomousSendView:
        existing = outreach_sender_service.get_attempt(brief.id)
        if existing is not None:
            return self._from_attempt(delegation, brief, existing)
        target = outreach_target_service.require_executable(brief.outreach_target_id)
        policy = outreach_policy_service.get(brief.product_id)
        platform = self._platform(target)
        evaluation = growth_mandate_service.evaluate(
            brief.product_id,
            AutonomyEvaluationRequest(
                platform=platform,
                action_type=DistributionActionType.OUTREACH_EMAIL,
                proposed_budget=0,
                requires_prepare=False,
                requires_approval=True,
                requests_paid_activation=False,
            ),
        )
        if evaluation.decision != AutonomyDecision.ALLOW:
            return self._view(
                delegation,
                outcome=OutreachAutonomousSendOutcome.BLOCKED,
                brief=brief,
                target=target,
                platform=platform.value,
                reasons=evaluation.reasons,
            )

        reservation = self._reserve_send_slots(delegation, policy, target, brief)
        if reservation is None:
            return self._view(
                delegation,
                outcome=OutreachAutonomousSendOutcome.BLOCKED,
                brief=brief,
                target=target,
                platform=platform.value,
                reasons=["Outreach auto-send daily/domain/cooldown capacity is unavailable"],
            )
        try:
            authorization = outreach_sender_service.authorize(
                brief.id,
                OutreachSendAuthorizationCreateRequest(
                    recipient_email=target.business_email,
                    confirm_one_initial_message=True,
                ),
            )
            attempt = await outreach_sender_service.send(authorization.id)
        except (KeyError, RuntimeError, ValueError) as exc:
            if outreach_sender_service.get_attempt(brief.id) is None:
                self._release_reservation(reservation)
            return self._view(
                delegation,
                outcome=OutreachAutonomousSendOutcome.BLOCKED,
                brief=brief,
                target=target,
                platform=platform.value,
                reasons=[str(exc)[:1000]],
            )
        return self._from_attempt(delegation, brief, attempt, platform=platform.value)

    def _reserve_send_slots(
        self,
        delegation: OutreachAutoSendDelegationView,
        policy: OutreachPolicyView,
        target: OutreachTargetView,
        brief: OutreachBriefView,
    ) -> dict[str, str] | None:
        now = datetime.now(UTC)
        sender_hash = self._hash(delegation.sender_email.casefold())
        domain = self._domain(target.business_email)
        domain_hash = self._hash(domain)
        day = now.date().isoformat()
        payload = {
            "product_id": str(brief.product_id),
            "brief_id": str(brief.id),
            "target_id": str(target.id),
            "sender_email": delegation.sender_email,
            "domain": domain,
            "reserved_at": now.isoformat(),
        }

        target_key = f"{brief.product_id}:{target.id}"
        if not self._reserve_cooldown(
            OUTREACH_AUTOSEND_TARGET_COOLDOWN_NAMESPACE,
            target_key,
            payload,
            timedelta(days=policy.target_cooldown_days),
        ):
            return None

        domain_cooldown_key = f"{brief.product_id}:{sender_hash}:{domain_hash}"
        if not self._reserve_cooldown(
            OUTREACH_AUTOSEND_DOMAIN_COOLDOWN_NAMESPACE,
            domain_cooldown_key,
            payload,
            timedelta(hours=policy.domain_cooldown_hours),
        ):
            self._store.delete(OUTREACH_AUTOSEND_TARGET_COOLDOWN_NAMESPACE, target_key)
            return None

        daily_key = self._reserve_numbered_slot(
            OUTREACH_AUTOSEND_DAILY_SLOT_NAMESPACE,
            f"{brief.product_id}:{day}:{sender_hash}",
            delegation.max_initial_sends_per_day,
            payload,
        )
        if daily_key is None:
            self._store.delete(OUTREACH_AUTOSEND_TARGET_COOLDOWN_NAMESPACE, target_key)
            self._store.delete(
                OUTREACH_AUTOSEND_DOMAIN_COOLDOWN_NAMESPACE,
                domain_cooldown_key,
            )
            return None

        domain_daily_key = self._reserve_numbered_slot(
            OUTREACH_AUTOSEND_DOMAIN_DAILY_SLOT_NAMESPACE,
            f"{brief.product_id}:{day}:{sender_hash}:{domain_hash}",
            delegation.max_initial_sends_per_domain_per_day,
            payload,
        )
        if domain_daily_key is None:
            self._store.delete(OUTREACH_AUTOSEND_TARGET_COOLDOWN_NAMESPACE, target_key)
            self._store.delete(
                OUTREACH_AUTOSEND_DOMAIN_COOLDOWN_NAMESPACE,
                domain_cooldown_key,
            )
            self._store.delete(OUTREACH_AUTOSEND_DAILY_SLOT_NAMESPACE, daily_key)
            return None
        return {
            "target": target_key,
            "domain_cooldown": domain_cooldown_key,
            "daily": daily_key,
            "domain_daily": domain_daily_key,
        }

    def _reserve_cooldown(
        self,
        namespace: str,
        key: str,
        payload: dict,
        cooldown: timedelta,
    ) -> bool:
        existing = self._store.get(namespace, key)
        if existing is not None:
            try:
                reserved_at = datetime.fromisoformat(str(existing["reserved_at"]))
                if reserved_at.tzinfo is None:
                    reserved_at = reserved_at.replace(tzinfo=UTC)
            except (KeyError, TypeError, ValueError):
                return False
            if datetime.now(UTC) - reserved_at < cooldown:
                return False
            self._store.delete(namespace, key)
        return self._store.put_if_absent(namespace, key, payload)

    def _reserve_numbered_slot(
        self,
        namespace: str,
        prefix: str,
        limit: int,
        payload: dict,
    ) -> str | None:
        for slot in range(1, limit + 1):
            key = f"{prefix}:{slot}"
            if self._store.put_if_absent(namespace, key, payload):
                return key
        return None

    def _release_reservation(self, reservation: dict[str, str]) -> None:
        self._store.delete(
            OUTREACH_AUTOSEND_TARGET_COOLDOWN_NAMESPACE,
            reservation["target"],
        )
        self._store.delete(
            OUTREACH_AUTOSEND_DOMAIN_COOLDOWN_NAMESPACE,
            reservation["domain_cooldown"],
        )
        self._store.delete(OUTREACH_AUTOSEND_DAILY_SLOT_NAMESPACE, reservation["daily"])
        self._store.delete(
            OUTREACH_AUTOSEND_DOMAIN_DAILY_SLOT_NAMESPACE,
            reservation["domain_daily"],
        )

    def _pending_outreach_brief(self, product_id: UUID) -> OutreachBriefView | None:
        pending: list[OutreachBriefView] = []
        for row in self._store.list_namespace(OUTREACH_BRIEF_NAMESPACE):
            try:
                brief = OutreachBriefView.model_validate(row)
                experiment = distribution_execution_service.get_experiment(brief.experiment_id)
            except (KeyError, ValueError):
                continue
            if (
                brief.product_id == product_id
                and brief.status == OutreachBriefStatus.DRAFT
                and experiment.status == DistributionExperimentStatus.DRAFT
            ):
                pending.append(brief)
        pending.sort(key=lambda item: (item.created_at, str(item.id)))
        return pending[0] if pending else None

    def _product_reconciliation_blocker(self, product_id: UUID) -> str | None:
        for row in self._store.list_namespace(OUTREACH_SEND_ATTEMPT_NAMESPACE):
            try:
                attempt = OutreachSendAttemptView.model_validate(row)
                brief = outreach_brief_service.get(attempt.brief_id)
            except (KeyError, ValueError):
                continue
            if brief.product_id != product_id:
                continue
            current = outreach_sender_service.get_attempt(attempt.brief_id) or attempt
            if current.status == OutreachSendAttemptStatus.RECONCILIATION_REQUIRED:
                return (
                    "An outreach SMTP attempt requires reconciliation; autonomous outreach "
                    "sending is paused"
                )
        return None

    def _from_attempt(
        self,
        delegation: OutreachAutoSendDelegationView,
        brief: OutreachBriefView,
        attempt: OutreachSendAttemptView,
        *,
        platform: str | None = None,
    ) -> OutreachAutonomousSendView:
        target = outreach_target_service.get(brief.outreach_target_id)
        if platform is None:
            platform = self._platform(target).value
        if attempt.status == OutreachSendAttemptStatus.SENT:
            outcome = OutreachAutonomousSendOutcome.SENT
            reasons = [
                "One policy-compliant initial outreach message was accepted by SMTP",
                "No autonomous follow-up is permitted",
            ]
        elif attempt.status == OutreachSendAttemptStatus.REJECTED:
            outcome = OutreachAutonomousSendOutcome.REJECTED
            reasons = [attempt.error_detail or "SMTP rejected the outreach message"]
        else:
            outcome = OutreachAutonomousSendOutcome.RECONCILIATION_REQUIRED
            reasons = [
                attempt.error_detail
                or "SMTP outcome requires reconciliation; automatic retry is disabled"
            ]
        return self._view(
            delegation,
            outcome=outcome,
            brief=brief,
            target=target,
            platform=platform,
            attempt=attempt,
            reasons=reasons,
        )

    def _view(
        self,
        delegation: OutreachAutoSendDelegationView,
        *,
        outcome: OutreachAutonomousSendOutcome,
        reasons: list[str],
        brief: OutreachBriefView | None = None,
        target: OutreachTargetView | None = None,
        platform: str | None = None,
        attempt: OutreachSendAttemptView | None = None,
    ) -> OutreachAutonomousSendView:
        return OutreachAutonomousSendView(
            product_id=delegation.product_id,
            delegation_id=delegation.id,
            delegation_version=delegation.version,
            outcome=outcome,
            target_id=target.id if target else None,
            brief_id=brief.id if brief else None,
            play_id=brief.distribution_play_id if brief else None,
            action_id=brief.action_id if brief else None,
            experiment_id=brief.experiment_id if brief else None,
            platform=platform,
            send_attempt_id=attempt.id if attempt else None,
            reasons=list(dict.fromkeys(reasons))[:20],
        )

    def _platform(self, target: OutreachTargetView):
        opportunity = __import__(
            "app.audience_intelligence_service",
            fromlist=["audience_intelligence_service"],
        ).audience_intelligence_service.find_opportunity(target.opportunity_id)
        return opportunity.platform

    def _domain(self, email: str) -> str:
        return email.rsplit("@", 1)[1].casefold()

    def _hash(self, value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:16]

    def reset(self) -> None:
        if self._store.ephemeral:
            for namespace in (
                OUTREACH_AUTOSEND_DAILY_SLOT_NAMESPACE,
                OUTREACH_AUTOSEND_DOMAIN_DAILY_SLOT_NAMESPACE,
                OUTREACH_AUTOSEND_TARGET_COOLDOWN_NAMESPACE,
                OUTREACH_AUTOSEND_DOMAIN_COOLDOWN_NAMESPACE,
            ):
                self._store.clear_namespace(namespace)


outreach_autosend_delegation_service = OutreachAutoSendDelegationService()
outreach_autonomous_send_service = OutreachAutonomousSendService()

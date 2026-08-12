from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.autonomy_schemas import GrowthMandateView
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import (
    InMemoryDistributionGrowthManagerService,
    distribution_growth_manager_service,
)
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.meta_paid_control import MetaPaidControlService, meta_paid_control_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_paid_control import TikTokPaidControlService, tiktok_paid_control_service

AUTONOMOUS_GROWTH_CONTROL_AUDIT_NAMESPACE = "autonomous_growth_control_audit"


class AutonomousGrowthControlOutcome(StrEnum):
    CONTINUED = "CONTINUED"
    SCALE_BOUNDED = "SCALE_BOUNDED"
    FINISHED = "FINISHED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class AutonomousGrowthControlAuditView(BaseModel):
    id: UUID
    product_id: UUID
    mandate_id: UUID
    mandate_version: int = Field(ge=1)
    experiment_id: UUID
    action_id: UUID
    platform: DistributionPlatform
    growth_action: str
    outcome: AutonomousGrowthControlOutcome
    reasons: list[str] = Field(default_factory=list)
    recorded_at: datetime


class AutonomousGrowthControlSweepView(BaseModel):
    product_id: UUID
    evaluated: int = Field(ge=0)
    finished: int = Field(ge=0)
    continued: int = Field(ge=0)
    scale_bounded: int = Field(ge=0)
    blocked: int = Field(ge=0)
    failed: int = Field(ge=0)
    audits: list[AutonomousGrowthControlAuditView]


class PaidControlBoundary(Protocol):
    def sync(self, action_id: UUID): ...

    def pause(self, action_id: UUID, *, reason: str = "EMERGENCY"): ...


class AutonomousGrowthControlService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        growth_manager: InMemoryDistributionGrowthManagerService | None = None,
        meta_control: MetaPaidControlService | PaidControlBoundary | None = None,
        tiktok_control: TikTokPaidControlService | PaidControlBoundary | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._growth_manager = growth_manager or distribution_growth_manager_service
        self._meta_control = meta_control or meta_paid_control_service
        self._tiktok_control = tiktok_control or tiktok_paid_control_service

    def evaluate_running(self, mandate: GrowthMandateView) -> AutonomousGrowthControlSweepView:
        audits: list[AutonomousGrowthControlAuditView] = []
        running = [
            experiment
            for experiment in distribution_execution_service.list_experiments(mandate.product_id)
            if experiment.status == DistributionExperimentStatus.RUNNING
        ]
        for experiment in running:
            action = distribution_execution_service.get_action(experiment.action_id)
            paid_snapshot = None
            if action.action_type == DistributionActionType.PAID_CAMPAIGN:
                try:
                    paid_snapshot = self._sync_paid(action.platform, action.id)
                except (KeyError, RuntimeError, ValueError) as exc:
                    audits.append(
                        self._record(
                            mandate=mandate,
                            experiment_id=experiment.id,
                            action_id=action.id,
                            platform=action.platform,
                            growth_action="UNKNOWN",
                            outcome=AutonomousGrowthControlOutcome.FAILED,
                            reasons=[f"Paid control sync failed: {str(exc)[:900]}"],
                        )
                    )
                    continue
                if bool(getattr(paid_snapshot, "requires_reconciliation", False)):
                    audits.append(
                        self._record(
                            mandate=mandate,
                            experiment_id=experiment.id,
                            action_id=action.id,
                            platform=action.platform,
                            growth_action="UNKNOWN",
                            outcome=AutonomousGrowthControlOutcome.BLOCKED,
                            reasons=[
                                "Provider state requires reconciliation; Growth Manager cannot "
                                "mutate this experiment"
                            ],
                        )
                    )
                    continue

            try:
                decision = self._growth_manager.evaluate(experiment.id)
            except (KeyError, RuntimeError, ValueError) as exc:
                audits.append(
                    self._record(
                        mandate=mandate,
                        experiment_id=experiment.id,
                        action_id=action.id,
                        platform=action.platform,
                        growth_action="UNKNOWN",
                        outcome=AutonomousGrowthControlOutcome.FAILED,
                        reasons=[f"Growth Manager evaluation failed: {str(exc)[:900]}"],
                    )
                )
                continue

            provider_cap_finished = bool(
                paid_snapshot is not None
                and getattr(paid_snapshot, "budget_guardrail_triggered", False)
                and getattr(paid_snapshot, "pause_state", None) == "CONFIRMED"
            )
            should_finish = decision.action in {"STOP", "MODIFY"} or provider_cap_finished
            if should_finish:
                reasons = list(decision.rationale)
                if provider_cap_finished:
                    reasons.append(
                        "Provider budget cap is reached and PAUSE is confirmed; close this bounded "
                        "test so the next portfolio can reuse the learned tactic."
                    )
                if action.action_type == DistributionActionType.PAID_CAMPAIGN and not provider_cap_finished:
                    try:
                        paused = self._pause_paid(
                            action.platform,
                            action.id,
                            reason=f"GROWTH_MANAGER_{decision.action}",
                        )
                    except (KeyError, RuntimeError, ValueError) as exc:
                        audits.append(
                            self._record(
                                mandate=mandate,
                                experiment_id=experiment.id,
                                action_id=action.id,
                                platform=action.platform,
                                growth_action=decision.action,
                                outcome=AutonomousGrowthControlOutcome.FAILED,
                                reasons=[f"Provider pause failed: {str(exc)[:900]}"],
                            )
                        )
                        continue
                    if (
                        getattr(paused, "pause_state", None) != "CONFIRMED"
                        or bool(getattr(paused, "requires_reconciliation", False))
                    ):
                        audits.append(
                            self._record(
                                mandate=mandate,
                                experiment_id=experiment.id,
                                action_id=action.id,
                                platform=action.platform,
                                growth_action=decision.action,
                                outcome=AutonomousGrowthControlOutcome.BLOCKED,
                                reasons=[
                                    "Provider PAUSE is not confirmed; keep the experiment RUNNING "
                                    "locally until reconciliation"
                                ],
                            )
                        )
                        continue
                distribution_execution_service.finish_experiment(experiment.id)
                audits.append(
                    self._record(
                        mandate=mandate,
                        experiment_id=experiment.id,
                        action_id=action.id,
                        platform=action.platform,
                        growth_action=decision.action,
                        outcome=AutonomousGrowthControlOutcome.FINISHED,
                        reasons=reasons,
                    )
                )
                continue

            outcome = (
                AutonomousGrowthControlOutcome.SCALE_BOUNDED
                if decision.action == "SCALE"
                else AutonomousGrowthControlOutcome.CONTINUED
            )
            reasons = list(decision.rationale)
            if decision.action == "SCALE":
                reasons.append(
                    "No in-place budget increase is allowed. The current campaign remains inside "
                    "its existing cap; a follow-up test may be launched only after a bounded slot "
                    "becomes available."
                )
            audits.append(
                self._record(
                    mandate=mandate,
                    experiment_id=experiment.id,
                    action_id=action.id,
                    platform=action.platform,
                    growth_action=decision.action,
                    outcome=outcome,
                    reasons=reasons,
                )
            )

        return AutonomousGrowthControlSweepView(
            product_id=mandate.product_id,
            evaluated=len(audits),
            finished=sum(item.outcome == AutonomousGrowthControlOutcome.FINISHED for item in audits),
            continued=sum(item.outcome == AutonomousGrowthControlOutcome.CONTINUED for item in audits),
            scale_bounded=sum(
                item.outcome == AutonomousGrowthControlOutcome.SCALE_BOUNDED for item in audits
            ),
            blocked=sum(item.outcome == AutonomousGrowthControlOutcome.BLOCKED for item in audits),
            failed=sum(item.outcome == AutonomousGrowthControlOutcome.FAILED for item in audits),
            audits=audits,
        )

    def _sync_paid(self, platform: DistributionPlatform, action_id: UUID):
        if platform == DistributionPlatform.INSTAGRAM:
            return self._meta_control.sync(action_id)
        if platform == DistributionPlatform.TIKTOK:
            return self._tiktok_control.sync(action_id)
        raise ValueError(f"No autonomous paid control for {platform.value}")

    def _pause_paid(self, platform: DistributionPlatform, action_id: UUID, *, reason: str):
        if platform == DistributionPlatform.INSTAGRAM:
            return self._meta_control.pause(action_id, reason=reason)
        if platform == DistributionPlatform.TIKTOK:
            return self._tiktok_control.pause(action_id, reason=reason)
        raise ValueError(f"No autonomous paid control for {platform.value}")

    def _record(
        self,
        *,
        mandate: GrowthMandateView,
        experiment_id: UUID,
        action_id: UUID,
        platform: DistributionPlatform,
        growth_action: str,
        outcome: AutonomousGrowthControlOutcome,
        reasons: list[str],
    ) -> AutonomousGrowthControlAuditView:
        audit = AutonomousGrowthControlAuditView(
            id=uuid4(),
            product_id=mandate.product_id,
            mandate_id=mandate.id,
            mandate_version=mandate.version,
            experiment_id=experiment_id,
            action_id=action_id,
            platform=platform,
            growth_action=growth_action,
            outcome=outcome,
            reasons=[str(reason)[:1000] for reason in reasons],
            recorded_at=datetime.now(UTC),
        )
        self._store.put(
            AUTONOMOUS_GROWTH_CONTROL_AUDIT_NAMESPACE,
            str(audit.id),
            audit.model_dump(mode="json"),
        )
        return audit


autonomous_growth_control_service = AutonomousGrowthControlService()

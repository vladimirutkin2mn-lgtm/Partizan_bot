from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

import app.customer_autopilot as customer_autopilot_module
from app.autonomy_schemas import GrowthMandateStatus
from app.customer_autopilot import CustomerAutopilotService
from app.customer_paid_campaign_lifecycle import (
    AUTOPILOT_CUSTOMER_PAUSE_REASON,
    CustomerPaidCampaignLifecycleResult,
    CustomerPaidCampaignLifecycleService,
)
from app.distribution_types import DistributionPlatform
from app.paid_control_resume_boundary import (
    paid_control_resume_scope,
    require_paid_control_resume_scope,
)
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
PRODUCT_ID = UUID("22222222-2222-2222-2222-222222222222")
ACTION_ONE = UUID("33333333-3333-3333-3333-333333333333")
ACTION_TWO = UUID("44444444-4444-4444-4444-444444444444")


class FakeController:
    provider = "fake-provider"

    def __init__(self, snapshots: dict[UUID, SimpleNamespace]) -> None:
        self.snapshots = snapshots
        self.pause_calls: list[tuple[UUID, str]] = []
        self.resume_calls: list[tuple[UUID, str]] = []
        self.fail_resume_for: set[UUID] = set()

    def get(self, action_id: UUID):
        return self.snapshots.get(action_id)

    def pause(self, action_id: UUID, *, reason: str):
        self.pause_calls.append((action_id, reason))
        snapshot = SimpleNamespace(
            pause_state="CONFIRMED",
            pause_reason=reason,
            requires_reconciliation=False,
            budget_guardrail_triggered=False,
        )
        self.snapshots[action_id] = snapshot
        return snapshot

    def resume_customer_pause(self, action_id: UUID, *, expected_reason: str):
        require_paid_control_resume_scope(action_id)
        self.resume_calls.append((action_id, expected_reason))
        if action_id in self.fail_resume_for:
            raise RuntimeError("provider resume failed")
        snapshot = SimpleNamespace(
            pause_state="NOT_REQUESTED",
            pause_reason=None,
            requires_reconciliation=False,
            budget_guardrail_triggered=False,
        )
        self.snapshots[action_id] = snapshot
        return snapshot


class StubLifecycle(CustomerPaidCampaignLifecycleService):
    def __init__(self, controller: FakeController, action_ids: list[UUID]) -> None:
        super().__init__(
            store=MemoryRuntimeStateStore(),
            controllers={controller.provider: controller},
        )
        self._stub_candidates = [
            (SimpleNamespace(id=action_id), SimpleNamespace(provider=controller.provider))
            for action_id in action_ids
        ]

    def _candidates(self, product_id: UUID):
        assert product_id == PRODUCT_ID
        return list(self._stub_candidates)


class FakeAutopilotLifecycle:
    def __init__(
        self,
        *,
        resume_result: CustomerPaidCampaignLifecycleResult | None = None,
        pause_result: CustomerPaidCampaignLifecycleResult | None = None,
        repause_result: CustomerPaidCampaignLifecycleResult | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.resume_result = resume_result or _result()
        self.pause_result = pause_result or _result()
        self.repause_result = repause_result or _result()
        self.events = events if events is not None else []
        self.pause_calls: list[UUID] = []
        self.resume_calls: list[UUID] = []
        self.repause_calls: list[list[UUID]] = []

    def pause_product(self, product_id: UUID, *, reason: str):
        assert reason == AUTOPILOT_CUSTOMER_PAUSE_REASON
        self.events.append("provider:pause")
        self.pause_calls.append(product_id)
        return self.pause_result

    def resume_product(self, product_id: UUID, *, expected_reason: str):
        assert expected_reason == AUTOPILOT_CUSTOMER_PAUSE_REASON
        self.events.append("provider:resume")
        self.resume_calls.append(product_id)
        return self.resume_result

    def repause_actions(self, product_id: UUID, action_ids: list[UUID], *, reason: str):
        assert product_id == PRODUCT_ID
        assert reason == AUTOPILOT_CUSTOMER_PAUSE_REASON
        self.events.append("provider:repause")
        self.repause_calls.append(list(action_ids))
        return self.repause_result


class FakeBalance:
    def __init__(
        self,
        events: list[str],
        *,
        fail_activate: bool = False,
        fail_pause: bool = False,
    ) -> None:
        self.events = events
        self.fail_activate = fail_activate
        self.fail_pause = fail_pause

    def summary(self, project_id: UUID, total_spend: float):
        assert project_id == PROJECT_ID
        assert total_spend == 0
        return SimpleNamespace(
            remaining_acquisition_capacity_usd=100.0,
            settlement_ready=True,
        )

    def activate_rail(self, project_id: UUID) -> None:
        assert project_id == PROJECT_ID
        self.events.append("rail:activate")
        if self.fail_activate:
            raise RuntimeError("rail activation failed")

    def pause_rail(self, project_id: UUID, reason: str) -> None:
        assert project_id == PROJECT_ID
        assert reason == "CUSTOMER"
        self.events.append("rail:pause")
        if self.fail_pause:
            raise RuntimeError("rail pause failed")


def _snapshot(reason: str, *, state: str = "CONFIRMED") -> SimpleNamespace:
    return SimpleNamespace(
        pause_state=state,
        pause_reason=reason,
        requires_reconciliation=False,
        budget_guardrail_triggered=reason == "BUDGET_CAP",
    )


def _result(
    *,
    candidate_count: int = 0,
    customer_paused: list[UUID] | None = None,
    preserved: list[UUID] | None = None,
    resumed: list[UUID] | None = None,
    reconciliation: list[UUID] | None = None,
    rollback_unknown: list[UUID] | None = None,
) -> CustomerPaidCampaignLifecycleResult:
    return CustomerPaidCampaignLifecycleResult(
        product_id=PRODUCT_ID,
        candidate_count=candidate_count,
        provider_mutation_count=0,
        customer_paused_action_ids=customer_paused or [],
        preserved_pause_action_ids=preserved or [],
        resumed_action_ids=resumed or [],
        reconciliation_action_ids=reconciliation or [],
        rollback_unknown_action_ids=rollback_unknown or [],
    )


def _autopilot_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lifecycle: FakeAutopilotLifecycle,
    balance: FakeBalance,
    project_pause_reason: str | None,
    events: list[str],
) -> tuple[CustomerAutopilotService, dict]:
    store = MemoryRuntimeStateStore()
    project = {
        "id": str(PROJECT_ID),
        "research_state": "READY",
        "product_id": str(PRODUCT_ID),
        "autopilot_pause_reason": project_pause_reason,
    }
    service = CustomerAutopilotService(store)
    service._balance = balance

    monkeypatch.setattr(
        customer_autopilot_module.customer_funnel_service,
        "get_project_payload",
        lambda project_id, token: project,
    )
    monkeypatch.setattr(
        customer_autopilot_module.customer_channel_service,
        "autonomous_platforms",
        lambda payload: [DistributionPlatform.TIKTOK],
    )
    monkeypatch.setattr(service, "_materialize_staged_meta", lambda payload, product_id: None)
    monkeypatch.setattr(
        service,
        "_ensure_mandate_if_ready",
        lambda project_id, payload, product_id: None,
    )
    monkeypatch.setattr(service, "_require_paid_destination", lambda product_id: None)
    monkeypatch.setattr(
        customer_autopilot_module.distribution_analytics_service,
        "product_analytics",
        lambda product_id: SimpleNamespace(total_spend=0),
    )

    def set_mandate_status(product_id: UUID, status: GrowthMandateStatus):
        assert product_id == PRODUCT_ID
        events.append(f"mandate:{status.value}")
        return SimpleNamespace(status=status)

    monkeypatch.setattr(
        customer_autopilot_module.growth_mandate_service,
        "set_status",
        set_mandate_status,
    )
    monkeypatch.setattr(
        customer_autopilot_module,
        "customer_paid_campaign_lifecycle_service",
        lifecycle,
    )
    monkeypatch.setattr(
        service,
        "overview",
        lambda project_id, token: "overview",
    )
    return service, project


def test_resume_scope_is_action_bound() -> None:
    with pytest.raises(ValueError, match="explicit customer Autopilot resume scope"):
        require_paid_control_resume_scope(ACTION_ONE)

    with paid_control_resume_scope(ACTION_ONE):
        require_paid_control_resume_scope(ACTION_ONE)
        with pytest.raises(ValueError):
            require_paid_control_resume_scope(ACTION_TWO)


def test_customer_pause_preserves_existing_budget_guardrail_pause() -> None:
    controller = FakeController(
        {
            ACTION_ONE: _snapshot("BUDGET_CAP"),
            ACTION_TWO: _snapshot("", state="NOT_REQUESTED"),
        }
    )
    service = StubLifecycle(controller, [ACTION_ONE, ACTION_TWO])

    result = service.pause_product(PRODUCT_ID)

    assert result.preserved_pause_action_ids == [ACTION_ONE]
    assert result.customer_paused_action_ids == [ACTION_TWO]
    assert controller.pause_calls == [(ACTION_TWO, AUTOPILOT_CUSTOMER_PAUSE_REASON)]
    assert controller.snapshots[ACTION_ONE].pause_reason == "BUDGET_CAP"


def test_partial_provider_resume_is_rolled_back_to_customer_pause() -> None:
    controller = FakeController(
        {
            ACTION_ONE: _snapshot(AUTOPILOT_CUSTOMER_PAUSE_REASON),
            ACTION_TWO: _snapshot(AUTOPILOT_CUSTOMER_PAUSE_REASON),
        }
    )
    controller.fail_resume_for.add(ACTION_TWO)
    service = StubLifecycle(controller, [ACTION_ONE, ACTION_TWO])

    result = service.resume_product(PRODUCT_ID)

    assert result.resumed_action_ids == []
    assert result.reconciliation_action_ids == [ACTION_TWO]
    assert controller.resume_calls == [
        (ACTION_ONE, AUTOPILOT_CUSTOMER_PAUSE_REASON),
        (ACTION_TWO, AUTOPILOT_CUSTOMER_PAUSE_REASON),
    ]
    assert controller.pause_calls == [(ACTION_ONE, AUTOPILOT_CUSTOMER_PAUSE_REASON)]
    assert controller.snapshots[ACTION_ONE].pause_state == "CONFIRMED"


def test_customer_pause_attempts_provider_stop_even_if_rail_pause_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    lifecycle = FakeAutopilotLifecycle(events=events)
    balance = FakeBalance(events, fail_pause=True)
    service, project = _autopilot_service(
        monkeypatch,
        lifecycle=lifecycle,
        balance=balance,
        project_pause_reason=None,
        events=events,
    )

    with pytest.raises(ValueError, match="requires reconciliation"):
        service.set_status(PROJECT_ID, "token", "PAUSED")

    assert events == ["mandate:PAUSED", "rail:pause", "provider:pause"]
    assert lifecycle.pause_calls == [PRODUCT_ID]
    assert project["autopilot_pause_reason"] == "CUSTOMER"


def test_customer_resume_rejects_unaccounted_provider_state_before_rail_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    lifecycle = FakeAutopilotLifecycle(
        resume_result=_result(candidate_count=1),
        pause_result=_result(candidate_count=1, customer_paused=[ACTION_ONE]),
        events=events,
    )
    balance = FakeBalance(events)
    service, project = _autopilot_service(
        monkeypatch,
        lifecycle=lifecycle,
        balance=balance,
        project_pause_reason="CUSTOMER",
        events=events,
    )

    with pytest.raises(ValueError, match="retry Autopilot resume"):
        service.set_status(PROJECT_ID, "token", "ACTIVE")

    assert events == ["provider:resume", "provider:pause"]
    assert "rail:activate" not in events
    assert "mandate:ACTIVE" not in events
    assert project["autopilot_pause_reason"] == "CUSTOMER"


def test_customer_resume_repauses_provider_if_rail_activation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    lifecycle = FakeAutopilotLifecycle(
        resume_result=_result(candidate_count=1, resumed=[ACTION_ONE]),
        repause_result=_result(candidate_count=1, customer_paused=[ACTION_ONE]),
        events=events,
    )
    balance = FakeBalance(events, fail_activate=True)
    service, project = _autopilot_service(
        monkeypatch,
        lifecycle=lifecycle,
        balance=balance,
        project_pause_reason="CUSTOMER",
        events=events,
    )

    with pytest.raises(ValueError, match="resume failed safely"):
        service.set_status(PROJECT_ID, "token", "ACTIVE")

    assert events == [
        "provider:resume",
        "rail:activate",
        "mandate:PAUSED",
        "rail:pause",
        "provider:repause",
    ]
    assert lifecycle.repause_calls == [[ACTION_ONE]]
    assert project["autopilot_pause_reason"] == "CUSTOMER"


def test_customer_resume_activates_rail_only_after_provider_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    lifecycle = FakeAutopilotLifecycle(
        resume_result=_result(candidate_count=1, resumed=[ACTION_ONE]),
        events=events,
    )
    balance = FakeBalance(events)
    service, project = _autopilot_service(
        monkeypatch,
        lifecycle=lifecycle,
        balance=balance,
        project_pause_reason="CUSTOMER",
        events=events,
    )

    response = service.set_status(PROJECT_ID, "token", "ACTIVE")

    assert response == "overview"
    assert events == ["provider:resume", "rail:activate", "mandate:ACTIVE"]
    assert project["autopilot_pause_reason"] is None


def test_paid_provider_resume_has_single_customer_autopilot_entrypoint() -> None:
    app_root = Path(__file__).resolve().parent.parent / "app"
    callers = []
    needle = "customer_paid_campaign_lifecycle_service.resume_product("
    for path in sorted(app_root.glob("*.py")):
        if needle in path.read_text(encoding="utf-8"):
            callers.append(path.name)

    assert callers == ["customer_autopilot.py"]

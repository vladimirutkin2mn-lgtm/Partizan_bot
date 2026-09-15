from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

import app.customer_autopilot as customer_autopilot_module
from app.autonomy_schemas import GrowthMandateStatus
from app.customer_autopilot import CustomerAutopilotService
from app.customer_paid_campaign_lifecycle import CustomerPaidCampaignLifecycleResult
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
PRODUCT_ID = UUID("22222222-2222-2222-2222-222222222222")
ACTION_ID = UUID("33333333-3333-3333-3333-333333333333")


class FakeBalance:
    def __init__(
        self,
        *,
        funded: float = 100.0,
        remaining: float = 100.0,
        settlement_ready: bool = True,
        events: list[str] | None = None,
    ) -> None:
        self.funded = funded
        self.remaining = remaining
        self.settlement_ready = settlement_ready
        self.events = events if events is not None else []
        self.pause_calls: list[str] = []

    def summary(self, project_id: UUID, total_spend: float):
        assert project_id == PROJECT_ID
        return SimpleNamespace(
            funded_usd=self.funded,
            remaining_acquisition_capacity_usd=self.remaining,
            settlement_ready=self.settlement_ready,
            acquisition_capacity_usd=100.0,
        )

    def pause_rail(self, project_id: UUID, reason: str) -> None:
        assert project_id == PROJECT_ID
        self.pause_calls.append(reason)
        self.events.append(f"rail:pause:{reason}")

    def activate_rail(self, project_id: UUID) -> None:
        assert project_id == PROJECT_ID
        self.events.append("rail:activate")


class FakeLifecycle:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.pause_calls: list[str] = []
        self.resume_calls: list[str] = []

    @staticmethod
    def _result(*, resumed: bool = False) -> CustomerPaidCampaignLifecycleResult:
        return CustomerPaidCampaignLifecycleResult(
            product_id=PRODUCT_ID,
            candidate_count=1,
            provider_mutation_count=1,
            customer_paused_action_ids=[] if resumed else [ACTION_ID],
            resumed_action_ids=[ACTION_ID] if resumed else [],
        )

    def pause_product(self, product_id: UUID, *, reason: str):
        assert product_id == PRODUCT_ID
        self.pause_calls.append(reason)
        self.events.append(f"provider:pause:{reason}")
        return self._result()

    def resume_product(self, product_id: UUID, *, expected_reason: str):
        assert product_id == PRODUCT_ID
        self.resume_calls.append(expected_reason)
        self.events.append(f"provider:resume:{expected_reason}")
        return self._result(resumed=True)

    def repause_actions(self, product_id: UUID, action_ids: list[UUID], *, reason: str):
        assert product_id == PRODUCT_ID
        assert action_ids == [ACTION_ID]
        self.events.append(f"provider:repause:{reason}")
        return self._result()


def _project(reason: str | None = None) -> dict:
    return {
        "id": str(PROJECT_ID),
        "research_state": "READY",
        "product_id": str(PRODUCT_ID),
        "autopilot_spend_confirmed": True,
        "autopilot_target_max_cac": 25.0,
        "autopilot_pause_reason": reason,
    }


def _install_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    service: CustomerAutopilotService,
    project: dict,
    lifecycle: FakeLifecycle,
    platforms: list[DistributionPlatform],
    events: list[str],
) -> None:
    monkeypatch.setattr(
        customer_autopilot_module.customer_channel_service,
        "autonomous_platforms",
        lambda payload: list(platforms),
    )
    monkeypatch.setattr(
        customer_autopilot_module.product_intake_service,
        "get_product",
        lambda product_id: SimpleNamespace(reference_links=["https://example.com"]),
    )
    monkeypatch.setattr(
        customer_autopilot_module.distribution_analytics_service,
        "product_analytics",
        lambda product_id: SimpleNamespace(total_spend=0.0),
    )
    monkeypatch.setattr(
        customer_autopilot_module.growth_mandate_service,
        "get",
        lambda product_id: SimpleNamespace(status=GrowthMandateStatus.ACTIVE),
    )

    def set_status(product_id: UUID, status: GrowthMandateStatus):
        events.append(f"mandate:{status.value}")
        return SimpleNamespace(status=status)

    monkeypatch.setattr(
        customer_autopilot_module.growth_mandate_service,
        "set_status",
        set_status,
    )
    monkeypatch.setattr(
        customer_autopilot_module,
        "customer_paid_campaign_lifecycle_service",
        lifecycle,
    )
    service._store.put(
        "customer_projects",
        str(PROJECT_ID),
        project,
    )


def test_channel_loss_pauses_provider_with_exact_automatic_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    project = _project()
    service = CustomerAutopilotService(MemoryRuntimeStateStore())
    balance = FakeBalance(events=events)
    lifecycle = FakeLifecycle(events)
    service._balance = balance
    _install_common(
        monkeypatch,
        service=service,
        project=project,
        lifecycle=lifecycle,
        platforms=[],
        events=events,
    )

    service._ensure_mandate_if_ready(PROJECT_ID, project, PRODUCT_ID)

    assert project["autopilot_pause_reason"] == "CHANNELS"
    assert lifecycle.pause_calls == ["AUTOPILOT_CHANNELS_PAUSE"]
    assert balance.pause_calls == ["CHANNELS"]
    assert events == [
        "mandate:PAUSED",
        "rail:pause:CHANNELS",
        "provider:pause:AUTOPILOT_CHANNELS_PAUSE",
    ]


def test_funding_exhaustion_pauses_existing_running_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    project = _project()
    service = CustomerAutopilotService(MemoryRuntimeStateStore())
    balance = FakeBalance(funded=100.0, remaining=0.0, events=events)
    lifecycle = FakeLifecycle(events)
    service._balance = balance
    _install_common(
        monkeypatch,
        service=service,
        project=project,
        lifecycle=lifecycle,
        platforms=[DistributionPlatform.TIKTOK],
        events=events,
    )

    service._ensure_mandate_if_ready(PROJECT_ID, project, PRODUCT_ID)

    assert project["autopilot_pause_reason"] == "FUNDING"
    assert lifecycle.pause_calls == ["AUTOPILOT_FUNDING_PAUSE"]
    assert balance.pause_calls == ["FUNDING"]


def test_automatic_pause_does_not_resume_when_conditions_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    project = _project("CHANNELS")
    store = MemoryRuntimeStateStore()
    service = CustomerAutopilotService(store)
    balance = FakeBalance(events=events)
    lifecycle = FakeLifecycle(events)
    service._balance = balance
    _install_common(
        monkeypatch,
        service=service,
        project=project,
        lifecycle=lifecycle,
        platforms=[DistributionPlatform.TIKTOK],
        events=events,
    )
    monkeypatch.setattr(
        customer_autopilot_module.growth_mandate_service,
        "get",
        lambda product_id: SimpleNamespace(status=GrowthMandateStatus.PAUSED),
    )

    result = service._ensure_mandate_if_ready(PROJECT_ID, project, PRODUCT_ID)

    assert result.status == GrowthMandateStatus.PAUSED
    assert project["autopilot_pause_reason"] == "CHANNELS"
    assert lifecycle.resume_calls == []
    assert "rail:activate" not in events
    assert "mandate:ACTIVE" not in events


def test_explicit_resume_uses_exact_automatic_provider_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    project = _project("CHANNELS")
    service = CustomerAutopilotService(MemoryRuntimeStateStore())
    balance = FakeBalance(events=events)
    lifecycle = FakeLifecycle(events)
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
    monkeypatch.setattr(service, "_ensure_mandate_if_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_require_paid_destination", lambda product_id: None)
    monkeypatch.setattr(
        customer_autopilot_module.distribution_analytics_service,
        "product_analytics",
        lambda product_id: SimpleNamespace(total_spend=0.0),
    )

    def set_status(product_id: UUID, status: GrowthMandateStatus):
        events.append(f"mandate:{status.value}")
        return SimpleNamespace(status=status)

    monkeypatch.setattr(
        customer_autopilot_module.growth_mandate_service,
        "set_status",
        set_status,
    )
    monkeypatch.setattr(
        customer_autopilot_module,
        "customer_paid_campaign_lifecycle_service",
        lifecycle,
    )
    monkeypatch.setattr(service, "overview", lambda project_id, token: "overview")

    result = service.set_status(PROJECT_ID, "token", "ACTIVE")

    assert result == "overview"
    assert lifecycle.resume_calls == ["AUTOPILOT_CHANNELS_PAUSE"]
    assert events == [
        "provider:resume:AUTOPILOT_CHANNELS_PAUSE",
        "rail:activate",
        "mandate:ACTIVE",
    ]
    assert project["autopilot_pause_reason"] is None

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomous_growth_control import (
    AUTONOMOUS_GROWTH_CONTROL_AUDIT_NAMESPACE,
    AutonomousGrowthControlService,
)
from app.autonomy_service import growth_mandate_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


class FakeGrowthManager:
    def __init__(self, action: str, rationale: list[str] | None = None) -> None:
        self.action = action
        self.rationale = rationale or [f"Fake {action} rationale"]
        self.calls: list[UUID] = []

    def evaluate(self, experiment_id: UUID):
        self.calls.append(experiment_id)
        return SimpleNamespace(action=self.action, rationale=self.rationale)


class FakePaidControl:
    def __init__(
        self,
        *,
        requires_reconciliation: bool = False,
        budget_guardrail_triggered: bool = False,
        sync_pause_state: str = "NOT_REQUESTED",
        pause_state: str = "CONFIRMED",
        pause_requires_reconciliation: bool = False,
    ) -> None:
        self.requires_reconciliation = requires_reconciliation
        self.budget_guardrail_triggered = budget_guardrail_triggered
        self.sync_pause_state = sync_pause_state
        self.pause_state = pause_state
        self.pause_requires_reconciliation = pause_requires_reconciliation
        self.sync_calls: list[UUID] = []
        self.pause_calls: list[tuple[UUID, str]] = []

    def sync(self, action_id: UUID):
        self.sync_calls.append(action_id)
        return SimpleNamespace(
            requires_reconciliation=self.requires_reconciliation,
            budget_guardrail_triggered=self.budget_guardrail_triggered,
            pause_state=self.sync_pause_state,
        )

    def pause(self, action_id: UUID, *, reason: str = "EMERGENCY"):
        self.pause_calls.append((action_id, reason))
        return SimpleNamespace(
            pause_state=self.pause_state,
            requires_reconciliation=self.pause_requires_reconciliation,
        )


class NeverPaidControl:
    def sync(self, action_id: UUID):
        raise AssertionError("Unexpected provider control call")

    def pause(self, action_id: UUID, *, reason: str = "EMERGENCY"):
        raise AssertionError("Unexpected provider pause call")


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()
    distribution_growth_manager_service.reset()
    growth_mandate_service.reset()
    store = get_runtime_store()
    if store.ephemeral:
        store.clear_namespace(AUTONOMOUS_GROWTH_CONTROL_AUDIT_NAMESPACE)


def _create_product() -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 200\n"
                "Max CAC: 12\n"
                "Goal: Acquire paid users"
            )
        },
    )
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution-plays/generate").status_code == 200
    return product_id


def _running_instagram_paid(product_id: str) -> tuple[UUID, UUID]:
    plays = client.get(f"/v1/products/{product_id}/distribution-plays")
    assert plays.status_code == 200
    play = next(
        item for item in plays.json()["plays"] if item["tactic_id"] == "instagram_ads"
    )
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = UUID(prepared.json()["action"]["id"])
    experiment_id = UUID(prepared.json()["experiment"]["id"])
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    distribution_execution_service.mark_executed(
        action_id,
        DistributionActionExecutionRequest(
            external_reference="fake-paid-control",
            notes="Test RUNNING experiment",
        ),
    )
    return action_id, experiment_id


def _mandate(product_id: str):
    response = client.put(
        f"/v1/products/{product_id}/growth-mandate",
        json={
            "total_budget_cap": 200,
            "target_max_cac": 12,
            "max_autonomous_spend_per_experiment": 50,
            "max_autonomous_spend_per_day": 100,
            "max_concurrent_running_experiments": 2,
            "allowed_platforms": ["INSTAGRAM"],
            "allowed_actions": ["PAID_CAMPAIGN"],
            "autonomous_prepare": True,
            "autonomous_approve": True,
            "autonomous_paid_activation": True,
            "approval_threshold": 50,
        },
    )
    assert response.status_code == 200
    return growth_mandate_service.get(UUID(product_id))


def _service(growth_action: str, paid_control: FakePaidControl) -> AutonomousGrowthControlService:
    return AutonomousGrowthControlService(
        store=get_runtime_store(),
        growth_manager=FakeGrowthManager(growth_action),
        meta_control=paid_control,
        tiktok_control=NeverPaidControl(),
    )


def test_paid_stop_requires_confirmed_provider_pause_before_finish() -> None:
    product_id = _create_product()
    action_id, experiment_id = _running_instagram_paid(product_id)
    mandate = _mandate(product_id)
    paid = FakePaidControl()

    result = _service("STOP", paid).evaluate_running(mandate)

    assert result.evaluated == 1
    assert result.finished == 1
    assert paid.sync_calls == [action_id]
    assert paid.pause_calls == [(action_id, "GROWTH_MANAGER_STOP")]
    assert distribution_execution_service.get_experiment(experiment_id).status == "FINISHED"
    audit = result.audits[0]
    assert audit.growth_action == "STOP"
    assert audit.outcome == "FINISHED"
    assert audit.mandate_id == mandate.id
    assert audit.mandate_version == mandate.version


def test_unconfirmed_paid_pause_keeps_experiment_running() -> None:
    product_id = _create_product()
    action_id, experiment_id = _running_instagram_paid(product_id)
    mandate = _mandate(product_id)
    paid = FakePaidControl(
        pause_state="UNKNOWN",
        pause_requires_reconciliation=True,
    )

    result = _service("STOP", paid).evaluate_running(mandate)

    assert result.finished == 0
    assert result.blocked == 1
    assert paid.pause_calls == [(action_id, "GROWTH_MANAGER_STOP")]
    assert distribution_execution_service.get_experiment(experiment_id).status == "RUNNING"
    assert "not confirmed" in result.audits[0].reasons[0]


def test_reconciliation_blocks_growth_manager_mutation() -> None:
    product_id = _create_product()
    _, experiment_id = _running_instagram_paid(product_id)
    mandate = _mandate(product_id)
    paid = FakePaidControl(requires_reconciliation=True)
    growth = FakeGrowthManager("STOP")
    service = AutonomousGrowthControlService(
        store=get_runtime_store(),
        growth_manager=growth,
        meta_control=paid,
        tiktok_control=NeverPaidControl(),
    )

    result = service.evaluate_running(mandate)

    assert result.blocked == 1
    assert growth.calls == []
    assert paid.pause_calls == []
    assert distribution_execution_service.get_experiment(experiment_id).status == "RUNNING"
    assert "reconciliation" in result.audits[0].reasons[0]


def test_confirmed_provider_budget_cap_finishes_bounded_test_without_second_pause() -> None:
    product_id = _create_product()
    action_id, experiment_id = _running_instagram_paid(product_id)
    mandate = _mandate(product_id)
    paid = FakePaidControl(
        budget_guardrail_triggered=True,
        sync_pause_state="CONFIRMED",
    )

    result = _service("SCALE", paid).evaluate_running(mandate)

    assert result.finished == 1
    assert paid.sync_calls == [action_id]
    assert paid.pause_calls == []
    assert distribution_execution_service.get_experiment(experiment_id).status == "FINISHED"
    assert result.audits[0].growth_action == "SCALE"
    assert any("bounded test" in reason for reason in result.audits[0].reasons)


def test_continue_keeps_running_and_does_not_pause() -> None:
    product_id = _create_product()
    action_id, experiment_id = _running_instagram_paid(product_id)
    mandate = _mandate(product_id)
    paid = FakePaidControl()

    result = _service("CONTINUE", paid).evaluate_running(mandate)

    assert result.continued == 1
    assert result.finished == 0
    assert paid.sync_calls == [action_id]
    assert paid.pause_calls == []
    assert distribution_execution_service.get_experiment(experiment_id).status == "RUNNING"


def test_scale_is_bounded_and_never_increases_existing_budget() -> None:
    product_id = _create_product()
    action_id, experiment_id = _running_instagram_paid(product_id)
    mandate = _mandate(product_id)
    paid = FakePaidControl()

    result = _service("SCALE", paid).evaluate_running(mandate)

    assert result.scale_bounded == 1
    assert result.finished == 0
    assert paid.sync_calls == [action_id]
    assert paid.pause_calls == []
    assert distribution_execution_service.get_experiment(experiment_id).status == "RUNNING"
    assert any("No in-place budget increase" in reason for reason in result.audits[0].reasons)


def test_control_audit_is_append_only_in_runtime_store() -> None:
    product_id = _create_product()
    _running_instagram_paid(product_id)
    mandate = _mandate(product_id)
    paid = FakePaidControl()
    service = _service("CONTINUE", paid)

    first = service.evaluate_running(mandate)
    second = service.evaluate_running(mandate)

    rows = get_runtime_store().list_namespace(AUTONOMOUS_GROWTH_CONTROL_AUDIT_NAMESPACE)
    assert len(rows) == 2
    assert first.audits[0].id != second.audits[0].id

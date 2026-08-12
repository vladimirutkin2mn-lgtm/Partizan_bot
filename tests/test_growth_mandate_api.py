from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.autonomy_service import GrowthMandateService, growth_mandate_service
from app.distribution_analytics_service import (
    DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
    DISTRIBUTION_SPEND_NAMESPACE,
    distribution_analytics_service,
)
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    DISTRIBUTION_EXPERIMENT_NAMESPACE,
    distribution_execution_service,
)
from app.execution_adapters import EXECUTION_ADAPTER_RECEIPT_NAMESPACE
from app.main import app
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()
    growth_mandate_service.reset()
    store = get_runtime_store()
    if store.ephemeral:
        store.clear_namespace(DISTRIBUTION_ACTION_NAMESPACE)
        store.clear_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE)
        store.clear_namespace(DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE)
        store.clear_namespace(DISTRIBUTION_SPEND_NAMESPACE)
        store.clear_namespace(EXECUTION_ADAPTER_RECEIPT_NAMESPACE)


def _create_product(name: str = "Oracle") -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                f"Product: {name}\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 1000\n"
                "Max CAC: 12\n"
                "Goal: Acquire paid users"
            )
        },
    )
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    return product_id


def _mandate_payload(**overrides) -> dict:
    payload = {
        "total_budget_cap": 1000,
        "target_max_cac": 12,
        "max_autonomous_spend_per_experiment": 50,
        "max_autonomous_spend_per_day": 100,
        "max_concurrent_running_experiments": 3,
        "allowed_platforms": ["TELEGRAM", "INSTAGRAM", "TIKTOK"],
        "allowed_actions": [
            "STANDALONE_POST",
            "ORGANIC_VIDEO",
            "PAID_CAMPAIGN",
        ],
        "autonomous_prepare": True,
        "autonomous_approve": True,
        "autonomous_paid_activation": False,
        "approval_threshold": 50,
    }
    payload.update(overrides)
    return payload


def _create_mandate(product_id: str, **overrides) -> dict:
    response = client.put(
        f"/v1/products/{product_id}/growth-mandate",
        json=_mandate_payload(**overrides),
    )
    assert response.status_code == 200
    return response.json()


def _evaluate(product_id: str, **overrides) -> dict:
    payload = {
        "platform": "INSTAGRAM",
        "action_type": "PAID_CAMPAIGN",
        "proposed_budget": 25,
        "requires_prepare": True,
        "requires_approval": True,
        "requests_paid_activation": False,
    }
    payload.update(overrides)
    response = client.post(
        f"/v1/products/{product_id}/growth-mandate/evaluate",
        json=payload,
    )
    assert response.status_code == 200
    return response.json()


def _put_experiment(product_id: str, *, status: str = "FINISHED") -> UUID:
    experiment_id = uuid4()
    get_runtime_store().put(
        DISTRIBUTION_EXPERIMENT_NAMESPACE,
        str(experiment_id),
        {
            "id": str(experiment_id),
            "product_id": product_id,
            "distribution_play_id": str(uuid4()),
            "opportunity_id": str(uuid4()),
            "action_id": str(uuid4()),
            "status": status,
            "attribution_level": "PAID",
            "tracking_url": "https://example.com/?ptz=1",
            "referral_token": experiment_id.hex[:16],
        },
    )
    return experiment_id


def test_growth_mandate_is_persisted_and_versioned() -> None:
    product_id = _create_product()
    first = _create_mandate(product_id)

    assert first["status"] == "ACTIVE"
    assert first["version"] == 1
    assert first["max_autonomous_spend_per_experiment"] == 50

    second = _create_mandate(product_id, max_autonomous_spend_per_experiment=40)
    assert second["id"] == first["id"]
    assert second["version"] == 2
    assert second["max_autonomous_spend_per_experiment"] == 40

    product_uuid = UUID(product_id)
    reloaded = GrowthMandateService(store=get_runtime_store()).get(product_uuid)
    assert reloaded.product_id == product_uuid
    assert reloaded.version == 2
    assert reloaded.max_autonomous_spend_per_experiment == 40


def test_action_inside_mandate_is_allowed() -> None:
    product_id = _create_product()
    mandate = _create_mandate(product_id)

    result = _evaluate(product_id)

    assert result["decision"] == "ALLOW"
    assert result["mandate_id"] == mandate["id"]
    assert result["mandate_version"] == mandate["version"]
    assert result["remaining_total_budget"] == 1000
    assert result["remaining_daily_budget"] == 100


def test_spend_above_autonomous_experiment_cap_requires_approval() -> None:
    product_id = _create_product()
    _create_mandate(product_id)

    result = _evaluate(product_id, proposed_budget=75)

    assert result["decision"] == "REQUIRE_APPROVAL"
    assert any("per-experiment cap" in reason for reason in result["reasons"])


def test_paid_activation_requires_explicit_delegation() -> None:
    product_id = _create_product()
    _create_mandate(product_id)

    result = _evaluate(product_id, requests_paid_activation=True)

    assert result["decision"] == "REQUIRE_APPROVAL"
    assert any("paid activation" in reason.lower() for reason in result["reasons"])


def test_disallowed_platform_is_blocked() -> None:
    product_id = _create_product()
    _create_mandate(product_id)

    result = _evaluate(
        product_id,
        platform="REDDIT",
        action_type="STANDALONE_POST",
        proposed_budget=0,
    )

    assert result["decision"] == "BLOCK"
    assert any("REDDIT" in reason for reason in result["reasons"])


def test_daily_spend_cap_blocks_additional_autonomous_spend() -> None:
    product_id = _create_product()
    _create_mandate(
        product_id,
        total_budget_cap=1000,
        max_autonomous_spend_per_day=100,
    )
    experiment_id = _put_experiment(product_id)
    spend_id = uuid4()
    get_runtime_store().put(
        DISTRIBUTION_SPEND_NAMESPACE,
        str(spend_id),
        {
            "spend_id": str(spend_id),
            "experiment_id": str(experiment_id),
            "amount": 90,
            "occurred_at": datetime.now(UTC).isoformat(),
            "properties": {},
        },
    )

    result = _evaluate(product_id, proposed_budget=25)

    assert result["decision"] == "BLOCK"
    assert result["current_daily_spend"] == 90
    assert result["remaining_daily_budget"] == 10
    assert any("daily autonomous spend cap" in reason for reason in result["reasons"])


def test_paid_reconciliation_required_blocks_autonomy() -> None:
    product_id = _create_product()
    _create_mandate(product_id)
    experiment_id = _put_experiment(product_id, status="APPROVED")
    action_id = uuid4()
    store = get_runtime_store()
    experiment = store.get(DISTRIBUTION_EXPERIMENT_NAMESPACE, str(experiment_id))
    assert experiment is not None
    experiment["action_id"] = str(action_id)
    store.put(DISTRIBUTION_EXPERIMENT_NAMESPACE, str(experiment_id), experiment)
    store.put(
        DISTRIBUTION_ACTION_NAMESPACE,
        str(action_id),
        {
            "id": str(action_id),
            "platform": "INSTAGRAM",
            "opportunity_id": str(uuid4()),
            "experiment_id": str(experiment_id),
            "action_type": "PAID_CAMPAIGN",
            "status": "APPROVED",
            "automation_level": "APPROVAL_GATED",
            "attribution_level": "PAID",
            "content_payload": {},
            "operational_metadata": {},
        },
    )
    store.put(
        EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
        str(action_id),
        {
            "action_id": str(action_id),
            "adapter_name": "meta-ads-create-paused",
            "provider": "meta-marketing-api",
            "outcome": "STAGED",
            "message": "Provider state needs reconciliation",
            "metadata": {"requires_reconciliation": True},
            "created_at": datetime.now(UTC).isoformat(),
        },
    )

    result = _evaluate(product_id, proposed_budget=25)

    assert result["decision"] == "BLOCK"
    assert any("reconciliation" in reason.lower() for reason in result["reasons"])


def test_paused_and_revoked_mandates_fail_closed() -> None:
    product_id = _create_product()
    _create_mandate(product_id)

    paused = client.patch(
        f"/v1/products/{product_id}/growth-mandate/status",
        json={"status": "PAUSED"},
    )
    assert paused.status_code == 200
    assert paused.json()["version"] == 2
    assert _evaluate(product_id)["decision"] == "BLOCK"

    revoked = client.patch(
        f"/v1/products/{product_id}/growth-mandate/status",
        json={"status": "REVOKED"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["version"] == 3

    reactivate = client.patch(
        f"/v1/products/{product_id}/growth-mandate/status",
        json={"status": "ACTIVE"},
    )
    assert reactivate.status_code == 409
    assert "REVOKED" in reactivate.json()["detail"]

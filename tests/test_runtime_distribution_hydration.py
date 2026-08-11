from uuid import uuid4

from app.distribution_control_plane_service import (
    CAMPAIGN_SLOT_NAMESPACE,
    DISTRIBUTION_IDENTITY_NAMESPACE,
    InMemoryDistributionControlPlaneService,
)
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    DISTRIBUTION_EXPERIMENT_NAMESPACE,
    InMemoryDistributionExecutionService,
)
from app.runtime_store import MemoryRuntimeStateStore


def test_control_plane_hydrates_identity_and_active_slot() -> None:
    store = MemoryRuntimeStateStore()
    identity_id = uuid4()
    product_id = uuid4()
    slot_id = uuid4()
    store.put(
        DISTRIBUTION_IDENTITY_NAMESPACE,
        str(identity_id),
        {
            "id": str(identity_id),
            "platform": "INSTAGRAM",
            "theme": "Relationships",
            "language": "English",
            "geography_hints": ["US"],
            "public_positioning": "Relationship tools and ideas",
            "profile_url": "https://example.com/profile",
            "profile_config": {},
            "eligibility": {
                "allowed_opportunity_kinds": ["CREATOR_ACCOUNT"],
                "allowed_actions": ["COMMENT"],
            },
            "reputation_metadata": {},
            "attribution_route": "https://example.com/relationships",
            "status": "ACTIVE",
        },
    )
    store.put(
        CAMPAIGN_SLOT_NAMESPACE,
        str(slot_id),
        {
            "id": str(slot_id),
            "product_id": str(product_id),
            "distribution_identity_id": str(identity_id),
            "platform": "INSTAGRAM",
            "status": "ACTIVE",
            "starts_at": None,
            "ends_at": None,
            "attribution_route": "https://example.com/relationships",
            "metadata": {},
        },
    )

    service = InMemoryDistributionControlPlaneService(store)
    assert service.get_identity(identity_id).theme == "Relationships"
    assert service.find_active_slot(identity_id, product_id).id == slot_id

    recreated = InMemoryDistributionControlPlaneService(store)
    assert recreated.list_identities()[0].id == identity_id
    assert recreated.find_active_slot(identity_id, product_id).id == slot_id


def test_execution_plan_hydrates_from_store() -> None:
    store = MemoryRuntimeStateStore()
    action_id = uuid4()
    experiment_id = uuid4()
    product_id = uuid4()
    play_id = uuid4()
    opportunity_id = uuid4()
    store.put(
        DISTRIBUTION_ACTION_NAMESPACE,
        str(action_id),
        {
            "id": str(action_id),
            "platform": "TIKTOK",
            "opportunity_id": str(opportunity_id),
            "distribution_identity_id": None,
            "campaign_slot_id": None,
            "experiment_id": str(experiment_id),
            "action_type": "PAID_CAMPAIGN",
            "status": "APPROVED",
            "automation_level": "APPROVAL_GATED",
            "attribution_level": "PAID",
            "target_url": None,
            "content_text": None,
            "content_payload": {},
            "tracking_url": "https://example.com/oracle?ptz=1",
            "operational_metadata": {"distribution_play_id": str(play_id)},
            "scheduled_at": None,
            "executed_at": None,
        },
    )
    store.put(
        DISTRIBUTION_EXPERIMENT_NAMESPACE,
        str(experiment_id),
        {
            "id": str(experiment_id),
            "product_id": str(product_id),
            "distribution_play_id": str(play_id),
            "opportunity_id": str(opportunity_id),
            "action_id": str(action_id),
            "status": "APPROVED",
            "attribution_level": "PAID",
            "tracking_url": "https://example.com/oracle?ptz=1",
            "referral_token": "token123",
        },
    )

    service = InMemoryDistributionExecutionService(store)
    plan = service.get_plan(action_id)
    assert plan.action.id == action_id
    assert plan.experiment.id == experiment_id

    recreated = InMemoryDistributionExecutionService(store)
    assert recreated.get_experiment(experiment_id).product_id == product_id

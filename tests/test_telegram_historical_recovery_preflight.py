"""Read-only recovery prerequisite introspection."""

from uuid import uuid4

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_historical_recovery import inspect_prerequisites


def test_preflight_does_not_modify_product_state():
    store = MemoryRuntimeStateStore()
    project, product = uuid4(), uuid4()
    store.put("customer_acquisition_projects", str(project), {
        "product_id": str(product),
        "channel_preferences": {"TELEGRAM": "RESEARCH_ONLY"},
    })
    store.put("audience_distribution_map", str(product), {
        "opportunities": [{"platform": "TELEGRAM", "url": "https://t.me/a_sfera"}],
    })
    before = dict(store._payloads)
    details = inspect_prerequisites(store, product, project)
    assert details["read_only"] is True
    assert details["channel_mode"] == "RESEARCH_ONLY"
    assert details["audience_opportunity_count"] == 1
    assert details["stored_icp_present"] is False
    assert details["project_matches_product"] is True
    assert store._payloads == before

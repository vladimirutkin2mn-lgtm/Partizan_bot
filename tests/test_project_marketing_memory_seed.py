import argparse
import json
from types import SimpleNamespace
from uuid import uuid4

from app import project_marketing_memory_seed as seed_module
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.project_marketing_memory import (
    ProjectMarketingMemoryService,
    ProjectMarketingMemorySource,
)
from app.runtime_store import MemoryRuntimeStateStore


def test_reviewed_marketing_memory_seed_is_idempotent(tmp_path, monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    memory = ProjectMarketingMemoryService(store)
    project_id = uuid4()
    product_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": str(product_id),
        },
    )
    seed_file = tmp_path / "memory.json"
    seed_file.write_text(
        json.dumps(
            [
                {
                    "key": "telegram-comment-style",
                    "category": "CHANNEL_PLAYBOOK",
                    "statement": "Use a concise conversational Telegram comment style.",
                    "platform": "TELEGRAM",
                    "action_type": "COMMENT",
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(seed_module, "get_runtime_store", lambda: store)
    monkeypatch.setattr(seed_module, "project_marketing_memory_service", memory)
    monkeypatch.setattr(
        seed_module.product_intake_service,
        "get_product",
        lambda resolved_product_id: SimpleNamespace(
            id=resolved_product_id,
            name="FemDom — private Telegram community",
        ),
    )
    args = argparse.Namespace(
        project_id=project_id,
        expected_product_id=product_id,
        expected_product_name="FemDom",
        seed_id="customer-review-v1",
        seed_file=seed_file,
    )

    first = seed_module.run(args)
    second = seed_module.run(args)

    assert first["status"] == "APPLIED"
    assert first["entry_count"] == 1
    assert first["product_name"] == "FemDom — private Telegram community"
    assert first["expected_product_name"] == "FemDom"
    assert second["status"] == "ALREADY_APPLIED"
    rows = memory._entries_for_project(project_id)
    assert len(rows) == 1
    assert rows[0].source == ProjectMarketingMemorySource.CUSTOMER_CONFIRMED
    assert rows[0].confidence == 1.0


def test_product_name_guard_requires_expected_name_to_be_present() -> None:
    assert seed_module._product_name_matches(
        "FemDom — private Telegram community",
        "FemDom",
    )
    assert seed_module._product_name_matches("FEMDOM", "femdom")
    assert not seed_module._product_name_matches("Different product", "FemDom")
    assert not seed_module._product_name_matches("FemDom", "")

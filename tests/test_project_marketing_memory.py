from uuid import uuid4

import pytest

from app.project_marketing_memory import (
    PROJECT_MARKETING_MEMORY_MAX_PROMPT_CHARS,
    ProjectMarketingMemoryCategory,
    ProjectMarketingMemoryConflictError,
    ProjectMarketingMemoryRecordRequest,
    ProjectMarketingMemoryService,
    ProjectMarketingMemorySource,
    ProjectMarketingMemoryStatus,
)
from app.runtime_store import MemoryRuntimeStateStore


def _request(
    *,
    key: str,
    statement: str,
    source: ProjectMarketingMemorySource,
    category: ProjectMarketingMemoryCategory = ProjectMarketingMemoryCategory.CUSTOMER_PREFERENCE,
    platform: str | None = None,
    action_type: str | None = None,
    confidence: float = 0.7,
) -> ProjectMarketingMemoryRecordRequest:
    return ProjectMarketingMemoryRecordRequest(
        key=key,
        category=category,
        statement=statement,
        source=source,
        confidence=confidence,
        source_ref="test",
        platform=platform,
        action_type=action_type,
    )


def test_customer_confirmed_memory_supersedes_weaker_hypothesis() -> None:
    service = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    project_id = uuid4()
    product_id = uuid4()
    hypothesis = service.record_internal(
        project_id,
        product_id,
        _request(
            key="profile-name-style",
            statement="Keep the existing account name.",
            source=ProjectMarketingMemorySource.AI_HYPOTHESIS,
        ),
    )

    confirmed = service.record_internal(
        project_id,
        product_id,
        _request(
            key="profile-name-style",
            statement="Prefer a short feminine or neutral profile name.",
            source=ProjectMarketingMemorySource.CUSTOMER_CONFIRMED,
        ),
    )

    assert confirmed.status == ProjectMarketingMemoryStatus.ACTIVE
    assert confirmed.confidence == 1.0
    assert confirmed.supersedes_id == hypothesis.id
    rows = service._entries_for_project(project_id)
    old = next(item for item in rows if item.id == hypothesis.id)
    assert old.status == ProjectMarketingMemoryStatus.SUPERSEDED
    assert old.superseded_by_id == confirmed.id


def test_weaker_hypothesis_cannot_overwrite_customer_confirmed_memory() -> None:
    service = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    project_id = uuid4()
    product_id = uuid4()
    service.record_internal(
        project_id,
        product_id,
        _request(
            key="bio-cta",
            statement="Use an explicit curiosity-driven CTA in the profile bio.",
            source=ProjectMarketingMemorySource.CUSTOMER_CONFIRMED,
        ),
    )

    with pytest.raises(ProjectMarketingMemoryConflictError, match="lower-provenance"):
        service.record_internal(
            project_id,
            product_id,
            _request(
                key="bio-cta",
                statement="Use only a bare URL.",
                source=ProjectMarketingMemorySource.AI_HYPOTHESIS,
            ),
        )


def test_prompt_context_applies_global_and_channel_specific_memory_only() -> None:
    service = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    project_id = uuid4()
    product_id = uuid4()
    service.record_internal(
        project_id,
        product_id,
        _request(
            key="brand-tone",
            statement="Keep the tone intriguing and concise.",
            source=ProjectMarketingMemorySource.CUSTOMER_CONFIRMED,
            category=ProjectMarketingMemoryCategory.BRAND_TONE,
        ),
    )
    service.record_internal(
        project_id,
        product_id,
        _request(
            key="telegram-comments",
            statement="Avoid dry lecture-like comments in Telegram communities.",
            source=ProjectMarketingMemorySource.CUSTOMER_CONFIRMED,
            category=ProjectMarketingMemoryCategory.CHANNEL_PLAYBOOK,
            platform="telegram",
            action_type="comment",
        ),
    )
    service.record_internal(
        project_id,
        product_id,
        _request(
            key="reddit-comments",
            statement="Use evidence-heavy comments on Reddit.",
            source=ProjectMarketingMemorySource.EXPERIMENT_RESULT,
            category=ProjectMarketingMemoryCategory.CHANNEL_PLAYBOOK,
            platform="reddit",
            action_type="comment",
        ),
    )

    prompt = service.prompt_context_for_product(
        product_id,
        platform="telegram",
        action_type="comment",
    )

    assert prompt.project_id == project_id
    assert prompt.entry_count == 2
    assert "Keep the tone intriguing and concise." in prompt.rendered
    assert "Avoid dry lecture-like comments" in prompt.rendered
    assert "Use evidence-heavy comments on Reddit." not in prompt.rendered
    assert "CUSTOMER_CONFIRMED" in prompt.rendered


def test_prompt_context_is_bounded_and_keeps_provenance_labels() -> None:
    service = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    project_id = uuid4()
    product_id = uuid4()
    for index in range(40):
        service.record_internal(
            project_id,
            product_id,
            _request(
                key=f"learning-{index}",
                statement=(f"Learning {index}: " + "x" * 280),
                source=ProjectMarketingMemorySource.EXPERIMENT_RESULT,
                category=ProjectMarketingMemoryCategory.EXPERIMENT_LEARNING,
            ),
        )

    prompt = service.prompt_context_for_product(product_id)

    assert prompt.entry_count <= 24
    assert prompt.char_count <= PROJECT_MARKETING_MEMORY_MAX_PROMPT_CHARS
    assert "[EXPERIMENT_LEARNING][EXPERIMENT_RESULT]" in prompt.rendered


def test_product_prompt_context_fails_closed_when_product_maps_to_multiple_projects() -> None:
    service = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    product_id = uuid4()
    for project_id in (uuid4(), uuid4()):
        service.record_internal(
            project_id,
            product_id,
            _request(
                key="tone",
                statement="Use a concise tone.",
                source=ProjectMarketingMemorySource.OBSERVED,
            ),
        )

    prompt = service.prompt_context_for_product(product_id, platform="telegram")

    assert prompt.project_id is None
    assert prompt.entry_count == 0
    assert prompt.rendered == ""

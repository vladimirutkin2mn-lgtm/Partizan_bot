from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.action_drafting import (
    DistributionActionComposer,
    DistributionContentDraft,
    SelectedActionTarget,
)
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.project_marketing_memory import (
    ProjectMarketingMemoryCategory,
    ProjectMarketingMemoryRecordRequest,
    ProjectMarketingMemoryService,
    ProjectMarketingMemorySource,
)
from app.runtime_store import MemoryRuntimeStateStore


class RecordingProvider:
    def __init__(self) -> None:
        self.messages = []

    async def parse(self, messages, response_model):
        self.messages = list(messages)
        return response_model(
            context_text="Concrete target context for the marketing action.",
            content_text="A concise useful draft for this target.",
            rationale="Prepared from the supplied project and target context.",
            disclosure_included=False,
            ai_disclosure_included=False,
        )


def _memory_request(
    *,
    key: str,
    statement: str,
    platform: str | None = None,
    action_type: str | None = None,
) -> ProjectMarketingMemoryRecordRequest:
    return ProjectMarketingMemoryRecordRequest(
        key=key,
        category=ProjectMarketingMemoryCategory.CHANNEL_PLAYBOOK,
        statement=statement,
        source=ProjectMarketingMemorySource.CUSTOMER_CONFIRMED,
        confidence=1.0,
        source_ref="test",
        platform=platform,
        action_type=action_type,
    )


def _product(product_id):
    return SimpleNamespace(
        id=product_id,
        model_dump=lambda mode="json": {
            "id": str(product_id),
            "name": "Test product",
        },
    )


def _play():
    return SimpleNamespace(
        tactic_id="instagram_creator_comment",
        action_type=DistributionActionType.COMMENT,
        model_dump=lambda mode="json": {
            "tactic_id": "instagram_creator_comment",
            "action_type": "COMMENT",
        },
    )


def _opportunity():
    return SimpleNamespace(
        platform=DistributionPlatform.INSTAGRAM,
        model_dump=lambda mode="json": {
            "platform": "INSTAGRAM",
            "title": "Relevant creator post",
        },
    )


@pytest.mark.asyncio
async def test_action_drafting_injects_only_applicable_project_memory() -> None:
    memory = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    provider = RecordingProvider()
    product_id = uuid4()
    project_id = uuid4()
    memory.record_internal(
        project_id,
        product_id,
        _memory_request(
            key="global-tone",
            statement="Keep the voice concise and intriguing.",
        ),
    )
    memory.record_internal(
        project_id,
        product_id,
        _memory_request(
            key="instagram-comments",
            statement="Avoid lecture-like comments on Instagram.",
            platform="instagram",
            action_type="comment",
        ),
    )
    memory.record_internal(
        project_id,
        product_id,
        _memory_request(
            key="telegram-comments",
            statement="Use a Telegram-specific conversational pattern.",
            platform="telegram",
            action_type="comment",
        ),
    )
    composer = DistributionActionComposer(
        provider=provider,
        memory_service=memory,
    )

    result = await composer.compose(
        product=_product(product_id),
        play=_play(),
        opportunity=_opportunity(),
        target=SelectedActionTarget(
            url="https://www.instagram.com/reel/ABC123/",
            context_text="Concrete target context for the marketing action.",
            source="test",
        ),
        policy=None,
    )

    assert isinstance(result, DistributionContentDraft)
    user_prompt = provider.messages[1].content
    assert "Project marketing memory:" in user_prompt
    assert "Keep the voice concise and intriguing." in user_prompt
    assert "Avoid lecture-like comments on Instagram." in user_prompt
    assert "Use a Telegram-specific conversational pattern." not in user_prompt
    assert "[CUSTOMER_CONFIRMED]" in user_prompt


@pytest.mark.asyncio
async def test_action_drafting_fails_closed_on_ambiguous_project_memory() -> None:
    memory = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    provider = RecordingProvider()
    product_id = uuid4()
    for project_id, statement in (
        (uuid4(), "Project A preference must not leak."),
        (uuid4(), "Project B preference must not leak."),
    ):
        memory.record_internal(
            project_id,
            product_id,
            _memory_request(
                key="tone",
                statement=statement,
            ),
        )
    composer = DistributionActionComposer(
        provider=provider,
        memory_service=memory,
    )

    await composer.compose(
        product=_product(product_id),
        play=_play(),
        opportunity=_opportunity(),
        target=SelectedActionTarget(
            url="https://www.instagram.com/reel/ABC123/",
            context_text="Concrete target context for the marketing action.",
            source="test",
        ),
        policy=None,
    )

    user_prompt = provider.messages[1].content
    assert "Project marketing memory: None" in user_prompt
    assert "Project A preference must not leak." not in user_prompt
    assert "Project B preference must not leak." not in user_prompt

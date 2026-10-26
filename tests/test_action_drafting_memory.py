import json
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


@pytest.mark.asyncio
async def test_telegram_action_composer_uses_shared_native_language_one_message_prompt():
    from app.community_voice import COMMUNITY_CREATIVE_PROMPT_VERSION

    memory = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    provider = RecordingProvider()
    product_id = uuid4()
    composer = DistributionActionComposer(provider=provider, memory_service=memory)
    play = SimpleNamespace(
        tactic_id="telegram_channel_comment",
        action_type=DistributionActionType.COMMENT,
        model_dump=lambda mode="json": {
            "tactic_id": "telegram_channel_comment",
            "action_type": "COMMENT",
        },
    )
    opportunity = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        title="Community post about event ground rules",
        model_dump=lambda mode="json": {
            "platform": "TELEGRAM",
            "title": "Community post about event ground rules",
        },
    )
    await composer.compose(
        product=_product(product_id),
        play=play,
        opportunity=opportunity,
        target=SelectedActionTarget(
            url="https://t.me/community/145",
            context_text="Russian discussion about rules at a community event.",
            source="native_latest",
        ),
        policy=None,
    )
    system = provider.messages[0].content
    assert COMMUNITY_CREATIVE_PROMPT_VERSION in system
    assert "Write ONE message in the target community language" in system
    assert "Infer a distinct communication voice" in system
    assert "GROUP_REPLY" in system
    assert "CHANNEL_COMMENT" in system
    assert "Drafting never grants publication permission" in system


@pytest.mark.asyncio
async def test_telegram_composer_does_not_feed_old_community_posts_to_llm():
    provider = RecordingProvider()
    memory = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    composer = DistributionActionComposer(provider=provider, memory_service=memory)
    product_id = uuid4()
    product = SimpleNamespace(
        id=product_id,
        model_dump=lambda mode="json": {
            "name": "Example product",
            "description": "Helps organize community events",
            "input_brief": "Huge old unrelated project brief",
        },
    )
    play = SimpleNamespace(
        tactic_id="telegram_channel_comment",
        action_type=DistributionActionType.COMMENT,
    )
    opportunity = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        title="An events community",
        metadata={
            "about": "An informal local event discussion with regulars",
            "community_fit_reason": "Target audience attends small workshops",
        },
        model_dump=lambda mode="json": {
            "title": "An events community",
            "recent_context": [
                {"text": "Do not reuse older unrelated message about discounts"},
            ],
        },
    )
    await composer.compose(
        product=product,
        play=play,
        opportunity=opportunity,
        target=SelectedActionTarget(
            url="https://t.me/community/145",
            context_text="Сегодня обсуждаем расписание открытой встречи.",
            source="telegram_native.latest_message",
        ),
        policy=None,
    )
    data = json.loads(provider.messages[1].content)
    assert data["selected_native_target"]["text"] == (
        "Сегодня обсуждаем расписание открытой встречи."
    )
    assert data["community_title"] == "An events community"
    assert data["community_about"] == (
        "An informal local event discussion with regulars"
    )
    assert data["audience_fit_hint"] == (
        "Target audience attends small workshops"
    )
    assert "old unrelated message" not in provider.messages[1].content
    assert "Huge old unrelated project brief" not in provider.messages[1].content
    assert len(provider.messages) == 2

    # Long latest post: select useful sections without a second LLM request
    # and without dragging old discovery context into the prompt.
    long_post = (
        "Анонс встречи с конкурсами и мастерскими. "
        + "Общие подробности для участников. " * 60
        + "Взнос для участия 4311 рублей. "
        + "Продолжение программы и деталей. " * 55
        + "Последний день записи в пятницу, осталось мало мест."
    )
    await composer.compose(
        product=product,
        play=play,
        opportunity=opportunity,
        target=SelectedActionTarget(
            url="https://t.me/community/146",
            context_text=long_post,
            source="telegram_native.latest_message",
        ),
        policy=None,
    )
    compact_data = json.loads(provider.messages[1].content)
    selected = compact_data["selected_native_target"]
    assert len(selected["text"]) <= 1400
    assert "Взнос для участия 4311 рублей" in selected["text"]
    assert "Последний день записи" in selected["text"]
    assert selected["excerpt"]["source_chars"] == len(long_post)
    assert selected["excerpt"]["compacted"] is True
    assert selected["excerpt"]["strategy"] == "OPENING_SALIENT_DETAILS_END"
    assert "old unrelated message" not in provider.messages[1].content

@pytest.mark.asyncio
async def test_telegram_product_voice_is_inferred_from_fuller_positioning_without_second_call():
    memory = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    provider = RecordingProvider()
    composer = DistributionActionComposer(provider=provider, memory_service=memory)
    product_id = uuid4()
    product = SimpleNamespace(
        id=product_id,
        model_dump=lambda mode="json": {
            "name": "Persona-powered product",
            "description": ("This is a conversational AI with a recognizable character. " * 7),
            "value_proposition": "A personalized AI companion for adults.",
            "known_audience": ["Adults interested in conversational experiences"],
            "product_type": "AI companion",
            "input_brief": "Huge old unrelated project brief",
        },
    )
    play = SimpleNamespace(
        tactic_id="telegram_channel_comment",
        action_type=DistributionActionType.COMMENT,
    )
    opportunity = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        title="Conversation club",
        metadata={"about": "Неформальное общение с юмором"},
    )
    await composer.compose(
        product=product, play=play, opportunity=opportunity,
        target=SelectedActionTarget(
            url="https://t.me/community/200",
            context_text="Мы обсуждаем образы и настроение на завтрашней встрече.",
            source="telegram_native.latest_message",
        ),
        policy=None, voice_variant_id="playful",
    )
    assert len(provider.messages) == 2
    data = json.loads(provider.messages[1].content)
    assert data["author_voice"]["variant_id"] == "playful"
    assert data["author_voice"]["profile_version"] == "product_derived_voice_v1"
    assert data["product"]["product_type"] == "AI companion"
    assert "personalized AI companion" in data["product"]["value_proposition"]
    assert len(data["product"]["description"]) > 240
    assert "Huge old unrelated project brief" not in provider.messages[1].content
    assert "community-marketing" not in provider.messages[0].content
    assert "non-explicit and professional" not in provider.messages[0].content
    assert "AI-content disclosure" not in provider.messages[0].content
    assert "Do not claim a platform rule permits" not in provider.messages[0].content


@pytest.mark.asyncio
async def test_mandatory_policy_disclosure_is_checked_separately_from_creative_prompt():
    memory = ProjectMarketingMemoryService(MemoryRuntimeStateStore())
    provider = RecordingProvider()
    composer = DistributionActionComposer(provider=provider, memory_service=memory)
    product = _product(uuid4())
    play = SimpleNamespace(
        tactic_id="telegram_channel_comment",
        action_type=DistributionActionType.COMMENT,
    )
    opportunity = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        title="Event club",
        metadata={},
    )
    with pytest.raises(ValueError, match="commercial disclosure missing"):
        await composer.compose(
            product=product, play=play, opportunity=opportunity,
            target=SelectedActionTarget(
                url="https://t.me/community/200",
                context_text="Обсуждаем новое мероприятие в субботу.",
                source="telegram_native.latest_message",
            ),
            policy=SimpleNamespace(
                disclosure_required=True,
                ai_content_constraints=[],
                model_dump=lambda mode="json": {"disclosure_required": True},
            ),
        )
    # No automatic retries that would increase the model bill.
    assert len(provider.messages) == 2

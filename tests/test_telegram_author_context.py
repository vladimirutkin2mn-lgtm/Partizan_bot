"""Use a linked Telegram profile for voice, without account modifications."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.action_drafting import DistributionActionComposer, SelectedActionTarget
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.project_marketing_memory import ProjectMarketingMemoryService
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_author_context import ConnectedTelegramAuthorContext


def _connect(store, product_id, *, mode="CLIENT_OWNED"):
    project_id = uuid4()
    store.put("customer_acquisition_projects", str(project_id), {
        "id": str(project_id),
        "product_id": str(product_id),
        "channel_publisher_modes": {"TELEGRAM": mode},
    })
    store.put("customer_telegram_connection", str(project_id), {
        "project_id": str(project_id),
        "status": "ACTIVE",
        "username": "old_author",
        "display_name": "Prior Author",
        "telegram_user_id": 123,
        "secret_reference": "DO_NOT_SEND_SESSION_REFERENCE",
    })
    return project_id


@pytest.mark.asyncio
async def test_reads_existing_account_live_bio_without_reconnecting_or_mutating():
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    project_id = _connect(store, product_id)
    calls = []

    async def live_profile(project):
        calls.append(project)
        return SimpleNamespace(
            username="new_author", display_name="Real Public Profile",
            about="Игривая AI-персона, общение и новые знакомства.",
            avatar=SimpleNamespace(file_reference_b64="NEVER_SEND_AVATAR_SECRET"),
        )

    svc = ConnectedTelegramAuthorContext(store=store, reader=live_profile)
    result = await svc.for_product(product_id)
    assert calls == [project_id]
    assert result["username"] == "new_author"
    assert result["display_name"] == "Real Public Profile"
    assert "AI-персона" in result["profile_bio"]
    assert result["profile_read_status"] == "live_profile_read"
    assert "secret_reference" not in result
    assert "telegram_user_id" not in result
    assert "avatar" not in result
    assert "DO_NOT_SEND_SESSION_REFERENCE" not in str(result)


@pytest.mark.asyncio
async def test_missing_session_still_uses_existing_project_identity():
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    _connect(store, product_id)

    async def unavailable(_project):
        raise RuntimeError("Provider unavailable")

    result = await ConnectedTelegramAuthorContext(
        store=store, reader=unavailable,
    ).for_product(product_id)
    assert result["username"] == "old_author"
    assert result["profile_bio"] == ""
    assert result["profile_read_status"] == "connected_identity_only"


@pytest.mark.asyncio
async def test_no_guessing_when_same_product_has_two_telegram_connections():
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    _connect(store, product_id)
    _connect(store, product_id)

    async def must_not_read(_project):
        raise AssertionError("Ambiguous account should not be read")

    assert await ConnectedTelegramAuthorContext(
        store=store, reader=must_not_read,
    ).for_product(product_id) is None


@pytest.mark.asyncio
async def test_different_product_and_managed_mode_do_not_leak_identity():
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    _connect(store, uuid4())
    _connect(store, product_id, mode="PARTIZAN_MANAGED")

    async def must_not_read(_project):
        raise AssertionError("Wrong account must not be read")

    assert await ConnectedTelegramAuthorContext(
        store=store, reader=must_not_read,
    ).for_product(product_id) is None


class FakeProvider:
    def __init__(self):
        self.messages = []
        self.calls = 0

    async def parse(self, messages, response_model):
        self.calls += 1
        self.messages = messages
        return response_model(
            context_text="Обсуждение ближайшей встречи.",
            content_text="Оранжевый явно задаёт настроение.",
            rationale="Addresses the specific conversation.",
        )


@pytest.mark.asyncio
async def test_main_telegram_composer_injects_actual_connected_author_profile_once():
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    _connect(store, product_id)
    reads = []

    async def profile(project_id):
        reads.append(project_id)
        return SimpleNamespace(
            username="real_handle", display_name="Live Persona",
            about="AI-персона с характером. Продукт в bio.",
        )

    composer = DistributionActionComposer(
        provider=FakeProvider(),
        memory_service=ProjectMarketingMemoryService(store),
        author_context=ConnectedTelegramAuthorContext(store=store, reader=profile),
    )
    product = SimpleNamespace(
        id=product_id,
        model_dump=lambda mode="json": {
            "name": "Character-based AI",
            "description": "Conversational AI product",
            "known_audience": ["People curious about AI personas"],
        },
    )
    play = SimpleNamespace(
        tactic_id="telegram_channel_comment",
        action_type=DistributionActionType.COMMENT,
    )
    opportunity = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        title="Conversation club",
        metadata={"about": "Неформальный чат"},
    )
    await composer.compose(
        product=product, play=play, opportunity=opportunity,
        target=SelectedActionTarget(
            url="https://t.me/community/2117",
            context_text="Чёрно-оранжевый дресс-код на встрече.",
            source="telegram_native.latest_message",
        ),
        policy=None,
    )
    assert composer._provider.calls == 1
    assert len(reads) == 1
    content = composer._provider.messages[1].content
    data = json.loads(content)
    assert data["actual_publisher_profile"]["username"] == "real_handle"
    assert data["actual_publisher_profile"]["display_name"] == "Live Persona"
    assert data["actual_publisher_profile"]["profile_bio"].startswith("AI-персона")
    assert "DO_NOT_SEND_SESSION_REFERENCE" not in content
    assert "session" not in data["actual_publisher_profile"]

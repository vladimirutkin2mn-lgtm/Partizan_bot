import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image

from app.action_drafting import ActionTargetSelector, DistributionActionComposer
from app.config import get_settings
from app.conversion_scenarios import TELEGRAM_PROFILE_INTEREST, conversion_scenario_planner
from app.distribution_execution_service import InMemoryDistributionExecutionService
from app.distribution_play_planner import TACTIC_CATALOG
from app.distribution_types import DistributionActionStatus, DistributionPlatform
from app.distribution_types import DistributionActionType as Action
from app.llm import LLMMessage
from app.telegram_writer_media import SelectedPhoto, _read_photo
from app.telegram_writer_v18 import PROMPT_VERSION, writer_prompt


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_writer_v18_enabled", True)


def case(action, text, *, photo=False):
    group = "https://t.me/example_group"
    standalone = action == Action.STANDALONE_POST
    mode, tactic = {
        Action.COMMENT: ("CHANNEL_COMMENT", "telegram_channel_comment"),
        Action.REPLY: ("GROUP_REPLY", "telegram_group_reply"),
        Action.STANDALONE_POST: ("STANDALONE_POST", "telegram_group_post"),
    }[action]
    play = SimpleNamespace(action_type=action, tactic_id=tactic, selected_identity_id=uuid4())
    opportunity = SimpleNamespace(
        title="A local community",
        url=group,
        platform=DistributionPlatform.TELEGRAM,
        metadata={
            "about": "A group for introductions",
            "action_target_mode": mode,
            "native_research_status": "VERIFIED",
            "community_fit_pass": True,
            "action_target_specific": not standalone,
            "action_target_url": group if standalone else group + "/12",
            "surface_capabilities": {"standalone_post": "AVAILABLE"},
            "recent_context": [
                {"message_id": 10, "url": group + "/10", "text": "Older unrelated post"},
                {
                    "message_id": 12,
                    "url": group + "/12",
                    "text": text,
                    "media_kind": "MessageMediaPhoto" if photo else None,
                },
            ],
        },
    )
    return play, opportunity, ActionTargetSelector().select(play, opportunity)


class Recorder:
    def __init__(self):
        self.calls = []

    async def parse(self, messages, response_model):
        self.calls.append(messages)
        return response_model(
            content_text="I'd love to see the next entry.",
            context_text="The photo contest.",
            rationale="Direct response to the entry.",
        )


@pytest.mark.parametrize("action", [Action.COMMENT, Action.REPLY, Action.STANDALONE_POST])
async def test_canonical_one_call_with_surface_specific_context(action, enabled, monkeypatch):
    play, opportunity, target = case(action, "The most recent public contribution.")
    product = SimpleNamespace(id=uuid4(), model_dump=lambda mode: {"name": "Example product"})
    provider = Recorder()
    history = SimpleNamespace(
        recent_telegram_group_posts=lambda product_id, identity, url: ["Our last topic"]
    )
    composer = DistributionActionComposer(provider=provider, execution_service=history)
    await composer.compose(product=product, play=play, opportunity=opportunity, target=target, policy=None)
    [messages] = provider.calls
    system, user = messages
    assert PROMPT_VERSION in system.content
    assert opportunity.metadata["action_target_mode"] in system.content
    assert system.content == writer_prompt(action, persisted_voice=False)
    assert "Older unrelated post" not in user.content
    payload = json.loads(user.content)
    assert payload["conversion_brief"]["variant_name"] == TELEGRAM_PROFILE_INTEREST.variant_name
    template = next(t for t in TACTIC_CATALOG if t.tactic_id == play.tactic_id)
    assert conversion_scenario_planner.variants(
        play=play, opportunity=opportunity, policy=None, template=template
    ) == (TELEGRAM_PROFILE_INTEREST,)
    if action == Action.STANDALONE_POST:
        assert payload["selected_native_target"]["url"] == str(opportunity.url)
        assert "recent public" not in payload["selected_native_target"]["text"]
        assert payload["member_excerpt_for_register_only"] == "The most recent public contribution."
        assert payload["previous_own_openers"] == ["Our last topic"]
    else:
        assert payload["selected_native_target"]["text"] == "The most recent public contribution."
        assert "member_excerpt_for_register_only" not in payload and "previous_own_openers" not in payload


async def test_photo_supplied_in_same_request_and_unseen_photo_has_no_visual_claims(enabled, monkeypatch):
    play, opportunity, target = case(Action.COMMENT, "Entry one. Voting is open.", photo=True)
    product = SimpleNamespace(id=uuid4(), model_dump=lambda mode: {"name": "Example"})
    provider = Recorder()
    composer = DistributionActionComposer(provider=provider)
    for selected in (
        SelectedPhoto(b"jpeg", "https://t.me/example_group/11", "Contest rules"),
        SelectedPhoto(),
    ):
        read = AsyncMock(return_value=selected)
        monkeypatch.setattr("app.action_drafting.read_selected_photo", read)
        await composer.compose(
            product=product, play=play, opportunity=opportunity, target=target, policy=None
        )
        read.assert_awaited_once_with(target.url, target.context_text)
        user = provider.calls[-1][1]
        assert user.image_jpeg == selected.jpeg
        assert json.loads(user.content)["selected_media"] == selected.prompt_context()
        content = user.response_input()["content"]
        if selected.jpeg:
            assert [part["type"] for part in content] == ["input_text", "input_image"]
            assert content[1]["image_url"] == "data:image/jpeg;base64,anBlZw=="
        else:
            assert content == user.content
    assert len(provider.calls) == 2
    _, _, empty = case(Action.COMMENT, "", photo=True)
    with pytest.raises(ValueError, match="no readable caption"):
        await composer.compose(product=product, play=play, opportunity=opportunity, target=empty, policy=None)
    assert len(provider.calls) == 2


async def test_only_selected_photo_and_explicit_same_peer_background_are_read():
    peer, caption = SimpleNamespace(username="example_group"), "Entry one, rules here."
    thumb = SimpleNamespace(w=160, h=160, size=2500)
    photo = SimpleNamespace(
        id=12,
        message=caption,
        photo=SimpleNamespace(sizes=[thumb]),
        entities=[
            SimpleNamespace(url="https://t.me/another_group/17"),
            SimpleNamespace(url="https://t.me/example_group/11"),
        ],
    )
    data = BytesIO()
    Image.new("RGB", (10, 10)).save(data, format="JPEG")
    client = SimpleNamespace(
        get_entity=AsyncMock(return_value=peer),
        get_messages=AsyncMock(side_effect=[photo, SimpleNamespace(id=11, message="Rules")]),
        download_media=AsyncMock(return_value=data.getvalue()),
    )
    url = "https://t.me/example_group/12"
    result = await _read_photo(client, url, caption)
    assert result == SelectedPhoto(data.getvalue(), "https://t.me/example_group/11", "Rules")
    assert [c.kwargs for c in client.get_messages.await_args_list] == [{"ids": 12}, {"ids": 11}]
    client.get_messages = AsyncMock(return_value=photo)
    client.download_media.reset_mock()
    assert await _read_photo(client, url, "Earlier caption") == SelectedPhoto()
    client.download_media.assert_not_called()


def test_legacy_plain_text_input_and_persisted_character_contract():
    assert LLMMessage("user", "test").response_input() == {"role": "user", "content": "test"}
    prompt = writer_prompt(Action.COMMENT, persisted_voice=True)
    assert "Keep its established character" in prompt and "Infer the author's voice" not in prompt


def test_recent_openers_are_scoped_to_the_product_identity_and_group(monkeypatch):
    service = InMemoryDistributionExecutionService()
    product, identity, group = uuid4(), uuid4(), "https://t.me/example_group"
    now = datetime.now(UTC)
    actions = []
    for i in range(7):
        actions.append(
            SimpleNamespace(
                id=uuid4(),
                distribution_identity_id=uuid4() if i == 4 else identity,
                platform=DistributionPlatform.TELEGRAM,
                action_type=Action.STANDALONE_POST,
                status=DistributionActionStatus.PREPARED if i == 5 else DistributionActionStatus.EXECUTED,
                target_url=group + "_other" if i == 6 else group,
                content_text=f"Own opener {i}",
                executed_at=now + timedelta(minutes=i),
            )
        )
    experiments = [SimpleNamespace(action_id=a.id) for a in actions]
    monkeypatch.setattr(service, "list_experiments", lambda pid: experiments if pid == product else [])
    monkeypatch.setattr(service, "get_action", {a.id: a for a in actions}.__getitem__)
    assert service.recent_telegram_group_posts(product, identity, group + "/") == [
        "Own opener 3",
        "Own opener 2",
    ]
    assert service.recent_telegram_group_posts(uuid4(), identity, group) == []
    assert service.recent_telegram_group_posts(product, None, group) == []

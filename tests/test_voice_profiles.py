"""Phase 1-3: immutable voice, single-call writer; no external calls in CI."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.action_drafting import DistributionActionComposer, SelectedActionTarget
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.voice_profiles import (
    VOICE_ASSIGNMENT_NAMESPACE,
    VOICE_GENERATION_JOB_NAMESPACE,
    VoiceExample,
    VoiceProfileDraft,
    VoiceProfileService,
)


def product_data():
    return {
        "name": "Conversation Agent",
        "description": "An interactive character-focused AI companion for curious adults.",
        "value_proposition": "Conversation with a consistent AI character",
        "known_audience": ["People who enjoy playful conversation"],
        "product_type": "AI companion",
    }


def draft():
    return VoiceProfileDraft(
        character_summary="A confident, observant conversationalist whose "
        "playfulness comes from noticing small contradictions, not advertisements.",
        default_tone="Calm, quick-witted, sometimes teasing, always context-sensitive.",
        speech_traits=[
            "Prefers short unadorned sentences to mini-essays.",
            "Responds to what participants actually said rather than explaining terms.",
        ],
        adaptation_rules=[
            "Use understatement for informal chat; do not force a punchline.",
            "Be clear and grounded in serious discussion, while keeping the same stance.",
        ],
        style_instruction=(
            "Speak with quiet confidence and perceptive humour. React to the "
            "specific tension in the selected post. Keep the rhythm compact and "
            "human, occasionally teasing where the context invites it. Do not "
            "mention the product category merely to display it. Maintain an "
            "observant, slightly mischievous outlook without pretending to have "
            "personally attended events or used a service."
        ),
        derivation_summary="Product offers character-based conversation for an "
        "audience that appreciates informal wit, not scripted expertise.",
        fact_refs=["product.description", "product.known_audience"],
        examples=[
            VoiceExample(
                surface="CHANNEL_COMMENT", context="Someone discusses dress code rules",
                message="И дресс-код внезапно стал самой интересной частью вечера.",
            ),
            VoiceExample(
                surface="GROUP_REPLY", context="One group member jokes about long lines",
                message="Очередь, кажется, уже организовала собственный клуб.",
            ),
        ],
    )


class VoiceLLM:
    def __init__(self):
        self.calls = 0
        self.messages = []

    async def parse(self, messages, response_model):
        self.calls += 1
        self.messages = messages
        assert response_model is VoiceProfileDraft
        return draft()


class WriterLLM:
    def __init__(self):
        self.calls = 0
        self.messages = []

    async def parse(self, messages, response_model):
        self.calls += 1
        self.messages = messages
        return response_model(
            context_text="Current native post about the group's event",
            content_text="И дресс-код стал главной интригой вечера.",
            rationale="Specific reaction in the saved communication style.",
        )


def setup(store):
    project_id, product_id = uuid4(), uuid4()
    store.put("customer_acquisition_projects", str(project_id), {
        "id": str(project_id), "product_id": str(product_id)
    })
    return project_id, product_id


async def initial(store, project_id, product_id):
    svc = VoiceProfileService(store)
    job = svc.reserve_initial(
        project_id=project_id, product_id=product_id, product_data=product_data()
    )
    model = VoiceLLM()
    version = await svc.generate_reserved(
        job.id, provider=model, allow_llm_call=True, activate_initial=True
    )
    return svc, job, model, version


def test_voice_content_is_concrete_not_a_placeholder():
    with pytest.raises(ValidationError, match="concrete style"):
        VoiceProfileDraft.model_validate({
            **draft().model_dump(),
            "style_instruction": "Please derive the voice from the product. " * 7,
        })
    with pytest.raises(ValidationError, match="SYNTHETIC_UNVERIFIED"):
        VoiceExample(surface="CHANNEL_COMMENT", context="Hello there",
                     message="A synthetic example", origin="REAL_CUSTOMER")


def test_reservation_has_no_llm_call_and_is_idempotent():
    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    svc = VoiceProfileService(store)
    a = svc.reserve_initial(project_id=pid, product_id=prod, product_data=product_data())
    b = svc.reserve_initial(project_id=pid, product_id=prod, product_data=product_data())
    assert a.id == b.id and a.status == "RESERVED"
    assert len(store.list_namespace(VOICE_GENERATION_JOB_NAMESPACE)) == 1


@pytest.mark.asyncio
async def test_no_unapproved_provider_invocation_and_only_one_successful_claim():
    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    svc = VoiceProfileService(store)
    job = svc.reserve_initial(project_id=pid, product_id=prod, product_data=product_data())
    provider = VoiceLLM()
    with pytest.raises(PermissionError, match="EXPLICIT_AUTHORIZATION"):
        await svc.generate_reserved(job.id, provider=provider)
    assert provider.calls == 0
    calls = await asyncio.gather(
        svc.generate_reserved(job.id, provider=provider, allow_llm_call=True),
        svc.generate_reserved(job.id, provider=provider, allow_llm_call=True),
        return_exceptions=True,
    )
    assert sum(not isinstance(c, Exception) for c in calls) == 1
    assert sum(isinstance(c, RuntimeError) for c in calls) == 1
    assert provider.calls == 1
    assert svc.job(job.id).status == "CREATED"


@pytest.mark.asyncio
async def test_active_version_is_durable_and_does_not_regenerate_on_each_post():
    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    svc, job, provider, version = await initial(store, pid, prod)
    first = svc.active(project_id=pid, product_id=prod, product_data=product_data())
    second = VoiceProfileService(store).active(
        project_id=pid, product_id=prod, product_data=product_data()
    )
    assert first.id == version.id == second.id
    assert provider.calls == 1
    assert first.generation_job_id == job.id
    assert first.evidence_status == "UNPROVEN_BASELINE"
    assert first.content_hash == version.content_hash
    assert len(store.list_namespace(VOICE_ASSIGNMENT_NAMESPACE)) == 1


@pytest.mark.asyncio
async def test_voice_source_changes_do_not_silently_reuse_stale_voice():
    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    svc, _, _, _ = await initial(store, pid, prod)
    changed = product_data()
    changed["description"] = "An entirely different business banking product."
    with pytest.raises(RuntimeError, match="VOICE_SOURCE_CHANGED"):
        svc.active(project_id=pid, product_id=prod, product_data=changed)


@pytest.mark.asyncio
async def test_other_project_cannot_read_or_activate_voice():
    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    svc, _, _, version = await initial(store, pid, prod)
    other_pid, other_prod = setup(store)
    with pytest.raises(PermissionError, match="VOICE_SCOPE_NOT_AUTHORIZED"):
        svc.get_version(project_id=other_pid, product_id=other_prod, version_id=version.id)
    with pytest.raises(PermissionError, match="VOICE_SCOPE_NOT_AUTHORIZED"):
        svc.reserve_initial(project_id=other_pid, product_id=prod, product_data=product_data())


@pytest.mark.asyncio
async def test_manual_assignment_requires_exact_revision():
    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    svc, _, _, version = await initial(store, pid, prod)
    updated = svc.promote(project_id=pid, product_id=prod,
                          version_id=version.id, expected_revision=1, reason="test")
    assert updated.assignment_revision == 2
    with pytest.raises(RuntimeError, match="STALE_REVISION"):
        svc.promote(project_id=pid, product_id=prod,
                    version_id=version.id, expected_revision=1, reason="stale")
    assert store.compare_and_swap(
        VOICE_ASSIGNMENT_NAMESPACE, f"{pid}:{prod}",
        {"unexpected": True}, {"override": "bad"}
    ) is False


@pytest.mark.asyncio
async def test_explicit_persisted_writer_uses_version_and_one_llm_call(monkeypatch):
    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    svc, _, generated, version = await initial(store, pid, prod)
    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=True)
    )
    writer = WriterLLM()
    composer = DistributionActionComposer(provider=writer, voice_profiles=svc)
    product = SimpleNamespace(id=prod, model_dump=lambda mode="json": product_data())
    play = SimpleNamespace(
        tactic_id="telegram_channel_comment",
        action_type=DistributionActionType.COMMENT,
    )
    opportunity = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        title="Discussion about the party",
        metadata={"about": "Дружеское сообщество"}
    )
    target = SelectedActionTarget(
        url="https://t.me/community/144", source="telegram_native.latest_message",
        context_text="Обсуждаем чёрно-оранжевый дресс-код на встречу.",
    )
    with pytest.raises(RuntimeError, match="VOICE_SCOPE_REQUIRED"):
        await composer.compose(product=product, play=play, opportunity=opportunity,
                               target=target, policy=None)
    assert writer.calls == 0
    await composer.compose(
        product=product, play=play, opportunity=opportunity,
        target=target, policy=None, project_id=pid,
    )
    assert writer.calls == 1 and generated.calls == 1
    system = writer.messages[0].content
    payload = json.loads(writer.messages[1].content)
    assert "community_message_v17_persisted_voice" in system
    assert "DO NOT infer a new" in system
    assert payload["author_voice"]["voice_profile_version_id"] == str(version.id)
    assert payload["author_voice"]["style_instruction"] == draft().style_instruction
    assert "derive_from_product_audience_and_community_in_same_generation" not in (
        writer.messages[1].content
    )
    assert "actual_publisher_profile" not in payload


@pytest.mark.asyncio
async def test_writer_rejects_other_tenant_version_even_if_explicitly_supplied(monkeypatch):
    store = MemoryRuntimeStateStore()
    first_pid, first_product = setup(store)
    svc, _, _, version = await initial(store, first_pid, first_product)
    second_pid, second_product = setup(store)
    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=True)
    )
    writer = WriterLLM()
    composer = DistributionActionComposer(provider=writer, voice_profiles=svc)
    with pytest.raises(PermissionError, match="VOICE_SCOPE_NOT_AUTHORIZED"):
        await composer.compose(
            product=SimpleNamespace(id=second_product,
                                    model_dump=lambda mode="json": product_data()),
            play=SimpleNamespace(tactic_id="telegram_channel_comment",
                                 action_type=DistributionActionType.COMMENT),
            opportunity=SimpleNamespace(platform=DistributionPlatform.TELEGRAM,
                                        title="Party", metadata={}),
            target=SelectedActionTarget(url="https://t.me/group/29",
                                        context_text="Long message about event costume rules.",
                                        source="saved_latest"),
            policy=None, project_id=second_pid, voice_version_id=version.id,
        )
    assert writer.calls == 0

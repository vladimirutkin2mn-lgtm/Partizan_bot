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


@pytest.mark.asyncio
async def test_real_auto_prepare_routes_selected_voice_to_internal_action_provenance(
    monkeypatch,
):
    from app.action_drafting import DistributionActionDraftingService
    from app.distribution_play_schemas import DistributionPlayStatus
    from app.project_marketing_memory import ProjectMarketingMemoryService

    store = MemoryRuntimeStateStore()
    project_id, product_id = setup(store)
    profiles, _, generated, version = await initial(store, project_id, product_id)
    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=True),
    )
    writer = WriterLLM()
    composer = DistributionActionComposer(
        provider=writer,
        voice_profiles=profiles,
        memory_service=ProjectMarketingMemoryService(store),
    )
    selected = SelectedActionTarget(
        url="https://t.me/community/410", source="saved_latest",
        context_text="Обсуждают дресс-код на ближайшей тематической встрече.",
    )
    drafting = DistributionActionDraftingService(
        selector=SimpleNamespace(select=lambda *args: selected), composer=composer
    )
    monkeypatch.setattr(
        drafting, "_require_unprocessed_telegram_target",
        lambda *args: None,
    )
    opportunity = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM, title="Community",
        metadata={"about": "Разговоры об интересных событиях"},
    )
    monkeypatch.setattr(
        "app.action_drafting.audience_intelligence_service.find_opportunity",
        lambda *args: opportunity,
    )
    captured = {}

    def capture_prepare(product, play, request, **kwargs):
        captured["request"] = request
        captured["kwargs"] = kwargs
        return SimpleNamespace(action=SimpleNamespace(id=uuid4()))

    monkeypatch.setattr(
        "app.action_drafting.distribution_execution_service.prepare",
        capture_prepare,
    )
    product = SimpleNamespace(id=product_id, model_dump=lambda mode="json": product_data())
    play = SimpleNamespace(
        tactic_id="telegram_channel_comment",
        action_type=DistributionActionType.COMMENT,
        platform=DistributionPlatform.TELEGRAM,
        status=DistributionPlayStatus.READY,
        opportunity_id=uuid4(),
        community_policy_required=False,
    )
    await drafting.auto_prepare(
        product=product, play=play,
        project_id=project_id, voice_version_id=version.id,
    )
    assert generated.calls == 1  # no new voice LLM calls per message
    assert writer.calls == 1
    assert captured["kwargs"]["voice_profile_version_id"] == version.id
    assert captured["kwargs"]["voice_profile_content_hash"] == version.content_hash
    assert "voice_profile_version_id" not in captured["request"].model_dump()


def _voice_test_target(text="Обсуждают дресс-код на ближайшей тематической встрече."):
    return (
        SimpleNamespace(
            tactic_id="telegram_channel_comment",
            action_type=DistributionActionType.COMMENT,
        ),
        SimpleNamespace(
            platform=DistributionPlatform.TELEGRAM, title="Community",
            metadata={"about": "Разговоры об интересных событиях"},
        ),
        SelectedActionTarget(
            url="https://t.me/community/410", source="saved_latest",
            context_text=text,
        ),
    )


@pytest.mark.asyncio
async def test_v17_cached_draft_never_rebills_identical_request_but_reacts_to_new_post(
    monkeypatch,
):
    from app.voice_draft_cache import VOICE_DRAFT_GENERATION_NAMESPACE

    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    profiles, _, _, version = await initial(store, pid, prod)
    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=True),
    )
    writer = WriterLLM()
    composer = DistributionActionComposer(provider=writer, voice_profiles=profiles)
    play, opportunity, target = _voice_test_target()
    product = SimpleNamespace(id=prod, model_dump=lambda mode="json": product_data())
    kwargs = dict(product=product, play=play, opportunity=opportunity,
                  policy=None, project_id=pid, voice_version_id=version.id)
    first = await composer.compose(**kwargs, target=target)
    second = await composer.compose(**kwargs, target=target)
    assert first == second
    assert first.voice_profile_version_id == version.id
    assert writer.calls == 1
    assert len(store.list_namespace(VOICE_DRAFT_GENERATION_NAMESPACE)) == 1
    assert store.list_namespace(VOICE_DRAFT_GENERATION_NAMESPACE)[0]["status"] == "SUCCEEDED"

    changed = _voice_test_target(
        "Свежий пост: организаторы изменили тематический дресс-код на зелёный."
    )[2]
    await composer.compose(**kwargs, target=changed)
    assert writer.calls == 2
    assert len(store.list_namespace(VOICE_DRAFT_GENERATION_NAMESPACE)) == 2


@pytest.mark.asyncio
async def test_v17_unknown_provider_outcome_stops_retries_and_double_spend(monkeypatch):
    from app.voice_draft_cache import VOICE_DRAFT_GENERATION_NAMESPACE

    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    profiles, _, _, version = await initial(store, pid, prod)
    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=True),
    )

    class TimeoutWriter(WriterLLM):
        async def parse(self, messages, response_model):
            self.calls += 1
            raise TimeoutError("Provider response lost; may have billed")

    writer = TimeoutWriter()
    composer = DistributionActionComposer(provider=writer, voice_profiles=profiles)
    play, opportunity, target = _voice_test_target()
    kwargs = dict(
        product=SimpleNamespace(id=prod, model_dump=lambda mode="json": product_data()),
        play=play, opportunity=opportunity, target=target, policy=None,
        project_id=pid, voice_version_id=version.id,
    )
    with pytest.raises(TimeoutError):
        await composer.compose(**kwargs)
    assert store.list_namespace(VOICE_DRAFT_GENERATION_NAMESPACE)[0]["status"] == (
        "PROVIDER_OUTCOME_UNKNOWN"
    )
    with pytest.raises(RuntimeError, match="VOICE_DRAFT_ALREADY_RESERVED_OR_UNRECONCILED"):
        await composer.compose(**kwargs)
    assert writer.calls == 1


@pytest.mark.asyncio
async def test_v17_validation_failure_is_persisted_and_not_regenerated(monkeypatch):
    from app.voice_draft_cache import VOICE_DRAFT_GENERATION_NAMESPACE

    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    profiles, _, _, version = await initial(store, pid, prod)
    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=True),
    )
    writer = WriterLLM()
    composer = DistributionActionComposer(provider=writer, voice_profiles=profiles)
    play, opportunity, target = _voice_test_target()
    policy = SimpleNamespace(
        disclosure_required=True, ai_content_constraints=[],
        model_dump=lambda mode="json": {"disclosure_required": True},
    )
    kwargs = dict(
        product=SimpleNamespace(id=prod, model_dump=lambda mode="json": product_data()),
        play=play, opportunity=opportunity, target=target,
        policy=policy, project_id=pid, voice_version_id=version.id,
    )
    with pytest.raises(ValueError, match="commercial disclosure"):
        await composer.compose(**kwargs)
    assert store.list_namespace(VOICE_DRAFT_GENERATION_NAMESPACE)[0]["status"] == (
        "POLICY_REJECTED"
    )
    with pytest.raises(RuntimeError, match="VOICE_DRAFT_ALREADY_RESERVED_OR_UNRECONCILED"):
        await composer.compose(**kwargs)
    assert writer.calls == 1


@pytest.mark.asyncio
async def test_v17_never_falls_back_to_mock_draft_without_llm(monkeypatch):
    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    profiles, _, _, version = await initial(store, pid, prod)
    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=True),
    )
    play, opportunity, target = _voice_test_target()
    composer = DistributionActionComposer(provider=None, voice_profiles=profiles)
    with pytest.raises(RuntimeError, match="VOICE_PROVIDER_REQUIRED"):
        await composer.compose(
            product=SimpleNamespace(id=prod),
            play=play, opportunity=opportunity, target=target,
            policy=None, project_id=pid, voice_version_id=version.id,
        )


@pytest.mark.parametrize("promotional", [
    "Попробуйте перейти по https://example.org и всё станет ясно.",
    "Переходи ко мне в профиль, там больше интересного.",
    "Ссылка в профиле.",
    "Check my profile if you want the rest.",
    "This is Conversation Agent, you should try it.",
])
def test_v17_organic_profile_click_guard_rejects_direct_promotion(promotional):
    from app.persisted_voice_writer import validate_profile_click_draft

    with pytest.raises(ValueError, match="VOICE_PROFILE_CLICK_"):
        validate_profile_click_draft(
            promotional, product_name="Conversation Agent",
        )
    validate_profile_click_draft(
        "Профиль мероприятия смешной: в описании одно, а на афише совсем другое.",
        product_name="Conversation Agent",
    )


@pytest.mark.asyncio
async def test_v17_profile_click_promotion_is_rejected_after_one_paid_draft(monkeypatch):
    from app.voice_draft_cache import VOICE_DRAFT_GENERATION_NAMESPACE

    store = MemoryRuntimeStateStore()
    pid, prod = setup(store)
    profiles, _, _, version = await initial(store, pid, prod)
    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=True),
    )

    class PromotionalWriter(WriterLLM):
        async def parse(self, messages, response_model):
            self.calls += 1
            return response_model(
                context_text="An upcoming community party and dress code",
                content_text="Отличная тема! Ссылка в профиле, заходите.",
                rationale="Promotes an account rather than reacting to the post.",
            )

    writer = PromotionalWriter()
    composer = DistributionActionComposer(provider=writer, voice_profiles=profiles)
    play, opportunity, target = _voice_test_target()
    kwargs = dict(
        product=SimpleNamespace(id=prod, model_dump=lambda mode="json": product_data()),
        play=play, opportunity=opportunity, target=target,
        policy=None, project_id=pid, voice_version_id=version.id,
    )
    with pytest.raises(ValueError, match="VOICE_PROFILE_CLICK_CTA_NOT_ALLOWED"):
        await composer.compose(**kwargs)
    assert store.list_namespace(VOICE_DRAFT_GENERATION_NAMESPACE)[0]["status"] == (
        "POLICY_REJECTED"
    )
    with pytest.raises(RuntimeError, match="VOICE_DRAFT_ALREADY_RESERVED_OR_UNRECONCILED"):
        await composer.compose(**kwargs)
    assert writer.calls == 1


@pytest.mark.asyncio
async def test_v17_review_and_real_preparation_share_exact_request_and_cached_draft(
    monkeypatch,
):
    """A non-publishing review is the identical generation used at prepare."""
    from app.action_drafting import DistributionActionDraftingService
    from app.distribution_play_schemas import DistributionPlayStatus
    from app.project_marketing_memory import ProjectMarketingMemoryService
    from app.voice_draft_cache import VOICE_DRAFT_GENERATION_NAMESPACE

    store = MemoryRuntimeStateStore()
    project_id, product_id = setup(store)
    profiles, _, generated, version = await initial(store, project_id, product_id)
    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=True),
    )
    writer = WriterLLM()
    composer = DistributionActionComposer(
        provider=writer, voice_profiles=profiles,
        memory_service=ProjectMarketingMemoryService(store),
    )
    selected = SelectedActionTarget(
        url="https://t.me/community/410", source="telegram_native.latest_message",
        context_text="Обсуждают дресс-код на ближайшей тематической встрече.",
    )
    drafting = DistributionActionDraftingService(
        selector=SimpleNamespace(select=lambda *args: selected),
        composer=composer,
    )
    monkeypatch.setattr(
        drafting, "_require_unprocessed_telegram_target",
        lambda *args: None,
    )
    opportunity = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM, title="Community",
        metadata={"about": "Разговоры об интересных событиях"},
    )
    monkeypatch.setattr(
        "app.action_drafting.audience_intelligence_service.find_opportunity",
        lambda *args: opportunity,
    )
    prepared = []
    def capture_prepare(product, play, request, **kwargs):
        prepared.append((request, kwargs))
        return SimpleNamespace(action=SimpleNamespace(id=uuid4()))

    monkeypatch.setattr(
        "app.action_drafting.distribution_execution_service.prepare", capture_prepare
    )
    product = SimpleNamespace(id=product_id, model_dump=lambda mode="json": product_data())
    play = SimpleNamespace(
        tactic_id="telegram_channel_comment",
        action_type=DistributionActionType.COMMENT,
        platform=DistributionPlatform.TELEGRAM,
        status=DistributionPlayStatus.READY,
        opportunity_id=uuid4(),
        community_policy_required=False,
    )
    preview = await drafting.preview_persisted_voice(
        product=product, play=play, project_id=project_id,
        voice_version_id=version.id,
    )
    assert preview["status"] == "REVIEW_ONLY_NOT_SENDABLE"
    assert preview["action_sendable"] is False
    assert preview["action_created"] is False
    assert preview["published"] is False
    assert preview["target_url"] == selected.url
    assert preview["voice_profile_version_id"] == str(version.id)
    assert writer.calls == 1 and generated.calls == 1
    assert not prepared
    assert len(store.list_namespace(VOICE_DRAFT_GENERATION_NAMESPACE)) == 1
    assert store.list_namespace(VOICE_DRAFT_GENERATION_NAMESPACE)[0]["status"] == "SUCCEEDED"

    await drafting.auto_prepare(
        product=product, play=play, project_id=project_id,
        voice_version_id=version.id,
    )
    assert writer.calls == 1  # Exact same manifest: no second paid call.
    assert len(prepared) == 1
    request, kwargs = prepared[0]
    assert request.content_text == preview["content_text"]
    assert kwargs["voice_profile_version_id"] == version.id
    assert kwargs["voice_profile_content_hash"] == version.content_hash


@pytest.mark.asyncio
async def test_v17_review_rejects_disabled_feature_without_provider_call(monkeypatch):
    from app.action_drafting import DistributionActionDraftingService
    from app.distribution_play_schemas import DistributionPlayStatus

    monkeypatch.setattr(
        "app.action_drafting.get_settings",
        lambda: SimpleNamespace(persisted_voice_writer_enabled=False),
    )
    writer = WriterLLM()
    drafting = DistributionActionDraftingService(
        composer=DistributionActionComposer(provider=writer),
    )
    play = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        action_type=DistributionActionType.COMMENT,
        status=DistributionPlayStatus.READY,
    )
    with pytest.raises(RuntimeError, match="PERSISTED_VOICE_WRITER_NOT_ENABLED"):
        await drafting.preview_persisted_voice(
            product=SimpleNamespace(id=uuid4()), play=play, project_id=uuid4(),
        )
    assert writer.calls == 0

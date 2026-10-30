"""No-cost challenger reservation must never mutate an active author voice."""
from uuid import uuid4

import pytest

from app.runtime_store import MemoryRuntimeStateStore
from app.voice_challenger_bootstrap import VoiceChallengerBootstrap
from app.voice_profiles import (
    VOICE_ASSIGNMENT_NAMESPACE,
    VOICE_GENERATION_JOB_NAMESPACE,
    VOICE_VERSION_NAMESPACE,
    VoiceAssignment,
    VoiceExample,
    VoiceOrigin,
    VoiceProfileDraft,
    VoiceProfileVersion,
    fingerprint,
    source_snapshot,
)


class DurableStore(MemoryRuntimeStateStore):
    ephemeral = False


def existing_voice(*, store=None):
    store = store if store is not None else DurableStore()
    pid, prod = uuid4(), uuid4()
    source = {
        "id": str(prod), "status": "CONFIRMED", "name": "Conversational Agent",
        "description": "Interactive AI fictional conversational character for adults.",
        "known_audience": ["Adults who enjoy vivid, character-driven talk"],
    }
    store.put("customer_acquisition_projects", str(pid), {
        "id": str(pid), "product_id": str(prod), "understanding_confirmed": True,
    })
    store.put("product_intake", str(prod), {"product": source})
    profile = VoiceProfileDraft(
        character_summary="Observant character with a light touch and specific curiosity.",
        default_tone="Direct, composed, occasionally playful and not academic.",
        speech_traits=["Short and varied sentences", "Talks about observed specifics"],
        adaptation_rules=["Never force jokes", "Use a serious register when relevant"],
        style_instruction=(
            "Notice the most distinctive small detail in the discussion. "
            "Respond to what the other person wrote, not to a generic "
            "product-category theme. Speak with quiet, confident curiosity "
            "without invented experience, profile calls to action or links."
        ),
        derivation_summary="Fits conversational, character-driven adult interaction.",
        fact_refs=["description"],
        examples=[
            VoiceExample(surface="CHANNEL_COMMENT", context="Event with costumes",
                         message="Похоже, дресс-код тут уже отдельная программа."),
            VoiceExample(surface="GROUP_REPLY", context="Talk about atmosphere",
                         message="Вот и оказалось, что атмосфера важнее афиши."),
        ],
    )
    vid = uuid4()
    version = VoiceProfileVersion(
        id=vid, project_id=pid, product_id=prod, tenant_scope_id=str(pid),
        version=1, origin=VoiceOrigin.INITIAL, generation_job_id="test-initial",
        source_fingerprint=fingerprint(source_snapshot(source)),
        source_snapshot=source_snapshot(source),
        generator_prompt_version="voice_generator_prompt_v2_explicit_schema",
        generation_config_id="test", content_hash=fingerprint(profile.model_dump(mode="json")),
        created_at="2026-10-31T00:00:00+00:00", profile=profile,
    )
    store.put(VOICE_VERSION_NAMESPACE, str(vid), version.model_dump(mode="json"))
    assignment = VoiceAssignment(
        project_id=pid, product_id=prod, tenant_scope_id=str(pid),
        active_version_id=vid, assignment_revision=1,
        updated_at="2026-10-31T00:00:00+00:00", reason="test",
    )
    store.put(VOICE_ASSIGNMENT_NAMESPACE, f"{pid}:{prod}", assignment.model_dump(mode="json"))
    return store, pid, prod, vid


def test_challenger_preflight_is_free_and_read_only_then_reservation_idempotent():
    store, pid, prod, initial_id = existing_voice()
    service = VoiceChallengerBootstrap(store)
    old_assignment = store.get(VOICE_ASSIGNMENT_NAMESPACE, f"{pid}:{prod}")
    preflight = service.plan(project_id=pid, product_id=prod)
    assert preflight["status"] == "CHALLENGER_RESERVATION_NEEDED"
    assert preflight["parent_voice_version_id"] == str(initial_id)
    assert preflight["provider_calls"] == 0
    assert store.list_namespace(VOICE_GENERATION_JOB_NAMESPACE) == []
    first = service.reserve(project_id=pid, product_id=prod)
    second = service.reserve(project_id=pid, product_id=prod)
    assert first == second
    assert first["status"] == "RESERVED"
    assert first["job_id"]
    assert len(store.list_namespace(VOICE_GENERATION_JOB_NAMESPACE)) == 1
    assert store.get(VOICE_ASSIGNMENT_NAMESPACE, f"{pid}:{prod}") == old_assignment


def test_challenger_reservation_requires_confirmed_project_and_durable_store():
    store, pid, prod, _ = existing_voice(store=MemoryRuntimeStateStore())
    with pytest.raises(RuntimeError, match="DURABLE_DATABASE_REQUIRED"):
        VoiceChallengerBootstrap(store).reserve(project_id=pid, product_id=prod)
    durable, pid, prod, _ = existing_voice()
    project = durable.get("customer_acquisition_projects", str(pid))
    project["understanding_confirmed"] = False
    durable.put("customer_acquisition_projects", str(pid), project)
    with pytest.raises(PermissionError, match="EXPLICITLY_UNCONFIRMED"):
        VoiceChallengerBootstrap(durable).plan(project_id=pid, product_id=prod)
    assert durable.list_namespace(VOICE_GENERATION_JOB_NAMESPACE) == []


def test_challenger_reservation_refuses_changed_product_without_call():
    store, pid, prod, _ = existing_voice()
    changed = store.get("product_intake", str(prod))
    changed["product"]["description"] = "A changed product with a different audience."
    store.put("product_intake", str(prod), changed)
    with pytest.raises(RuntimeError, match="VOICE_SOURCE_CHANGED"):
        VoiceChallengerBootstrap(store).reserve(project_id=pid, product_id=prod)
    assert store.list_namespace(VOICE_GENERATION_JOB_NAMESPACE) == []

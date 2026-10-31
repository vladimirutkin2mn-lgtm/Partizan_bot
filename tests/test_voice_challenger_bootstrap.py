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
        "value_proposition": "A playful and distinctive fictional conversation partner",
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


def test_campaign_label_without_audience_or_distinct_value_blocks_paid_candidate():
    store, pid, prod, _ = existing_voice()
    record = store.get("product_intake", str(prod))
    source = record["product"]
    source["name"] = "Promoted asset: example"
    source["description"] = (
        "Promoted asset: Example. Asset type: Telegram channel or group. "
        "Market: any. Test budget: $50. Goal: Get paying customers."
    )
    source["value_proposition"] = source["description"]
    source["known_audience"] = []
    source["use_cases"] = []
    source["product_type"] = ""
    source["problem_or_desire"] = ""
    store.put("product_intake", str(prod), record)

    # Update the fixture's original version to this changed source: an old
    # source check is not enough; we must also catch semantic thinness.
    version_row = store.list_namespace(VOICE_VERSION_NAMESPACE)[0]
    from app.voice_profiles import VoiceProfileService

    svc = VoiceProfileService(store)
    raw = store.get(VOICE_VERSION_NAMESPACE, version_row["id"])
    raw["source_snapshot"] = source_snapshot(source)
    raw["source_fingerprint"] = fingerprint(source_snapshot(source))
    store.put(VOICE_VERSION_NAMESPACE, version_row["id"], raw)

    bootstrap = VoiceChallengerBootstrap(store)
    report = bootstrap.plan(project_id=pid, product_id=prod)
    assert report["status"] == "PRODUCT_UNDERSTANDING_INSUFFICIENT"
    assert "STRUCTURED_AUDIENCE_MISSING" in report["missing_dimensions"]
    assert "PRODUCT_VALUE_NOT_DISTINGUISHED_FROM_CAMPAIGN_BRIEF" in (
        report["missing_dimensions"]
    )
    assert report["provider_calls"] == 0
    assert bootstrap.reserve(project_id=pid, product_id=prod) == report
    assert store.list_namespace(VOICE_GENERATION_JOB_NAMESPACE) == []
    with pytest.raises(RuntimeError, match="PRODUCT_UNDERSTANDING_REQUIRED"):
        svc.reserve_challenger(
            project_id=pid, product_id=prod, product_data=source
        )


def test_campaign_type_label_does_not_make_unproven_value_ready():
    from app.voice_profiles import voice_source_readiness_issues

    snapshot = {
        "name": "Promoted asset",
        "description": (
            "Promoted asset: A Telegram group. Asset type: Telegram community. "
            "Test budget: 50. Business goal: Get more customers."
        ),
        "value_proposition": (
            "Promoted asset: A Telegram group. Asset type: Telegram community. "
            "Test budget: 50. Business goal: Get more customers."
        ),
        "product_type": "Telegram channel",
        "known_audience": ["People interested in the category"],
    }
    issues = voice_source_readiness_issues(snapshot)
    assert "PRODUCT_VALUE_NOT_DISTINGUISHED_FROM_CAMPAIGN_BRIEF" in issues


def reviewed_rescan_existing_voice():
    """Simulate a completed #590 founder confirmation, without live calls."""
    from app.voice_profiles import VoiceProfileService

    store, pid, prod, old_version = existing_voice()
    old_product = dict(store.get("product_intake", str(prod))["product"])
    new_product = {
        **old_product,
        "description": "An adult character-led discussion community with original interactive prompts.",
        "value_proposition": "Members can discuss fictional scenarios and share creative responses.",
        "known_audience": ["Adults interested in character-led interactive discussions"],
    }
    record = store.get("product_intake", str(prod))
    record["product"] = new_product
    store.put("product_intake", str(prod), record)
    scan_id = str(uuid4())
    project = store.get("customer_acquisition_projects", str(pid))
    project.update({
        "voice_refresh_status": "REVIEW_REQUIRED",
        "product_revision_scan_id": scan_id,
    })
    store.put("customer_acquisition_projects", str(pid), project)
    store.put("customer_product_revision_history", f"{pid}:{scan_id}", {
        "project_id": str(pid),
        "product_id": str(prod),
        "scan_id": scan_id,
        "old_product": old_product,
        "new_product": new_product,
    })
    return store, pid, prod, old_version, scan_id


@pytest.mark.asyncio
async def test_confirmed_rescan_can_generate_inactive_challenger_but_not_publish_old_voice():
    from app.voice_profiles import VoiceProfileService

    store, pid, prod, old_version, scan_id = reviewed_rescan_existing_voice()
    svc = VoiceProfileService(store)
    original_assignment = store.get(VOICE_ASSIGNMENT_NAMESPACE, f"{pid}:{prod}")
    new_product = store.get("product_intake", str(prod))["product"]

    # Old v17 stays immutable, but cannot be reused against revised facts.
    with pytest.raises(RuntimeError, match="VOICE_SOURCE_CHANGED"):
        svc.active(project_id=pid, product_id=prod, product_data=new_product)

    bootstrap = VoiceChallengerBootstrap(store)
    report = bootstrap.plan(project_id=pid, product_id=prod)
    assert report["status"] == "CHALLENGER_RESERVATION_NEEDED"
    assert report["parent_voice_version_id"] == str(old_version)
    reserved = bootstrap.reserve(project_id=pid, product_id=prod)
    assert reserved["status"] == "RESERVED"
    assert bootstrap.reserve(project_id=pid, product_id=prod) == reserved

    class FakeProvider:
        calls = 0

        async def parse(self, messages, response_model):
            self.calls += 1
            assert "source_snapshot" in messages[1].content
            old = store.get(VOICE_VERSION_NAMESPACE, str(old_version))
            return VoiceProfileDraft.model_validate(old["profile"])

    provider = FakeProvider()
    candidate = await svc.generate_reserved(
        reserved["job_id"], provider=provider, allow_llm_call=True,
    )
    assert provider.calls == 1
    assert candidate.parent_version_id == old_version
    assert candidate.source_fingerprint == fingerprint(source_snapshot(new_product))
    assert candidate.evidence_status == "CHALLENGER_UNREVIEWED"
    assert store.get(VOICE_ASSIGNMENT_NAMESPACE, f"{pid}:{prod}") == original_assignment
    assert store.get("customer_acquisition_projects", str(pid))["voice_refresh_status"] == "REVIEW_REQUIRED"
    with pytest.raises(RuntimeError, match="VOICE_SOURCE_CHANGED"):
        svc.active(project_id=pid, product_id=prod, product_data=new_product)


def test_review_required_flag_alone_does_not_authorize_stale_source():
    store, pid, prod, _, scan_id = reviewed_rescan_existing_voice()
    project = store.get("customer_acquisition_projects", str(pid))
    before = store.get("customer_product_revision_history", f"{pid}:{scan_id}")
    before["new_product"]["value_proposition"] = "Tampered revision that does not match live facts."
    store.put("customer_product_revision_history", f"{pid}:{scan_id}", before)
    with pytest.raises(RuntimeError, match="VOICE_SOURCE_CHANGED"):
        VoiceChallengerBootstrap(store).reserve(project_id=pid, product_id=prod)
    assert store.list_namespace(VOICE_GENERATION_JOB_NAMESPACE) == []


@pytest.mark.asyncio
async def test_rescan_challenger_aborts_before_provider_if_revision_changes_after_reservation():
    from app.voice_profiles import VoiceProfileService

    store, pid, prod, _, _ = reviewed_rescan_existing_voice()
    bootstrap = VoiceChallengerBootstrap(store)
    reserved = bootstrap.reserve(project_id=pid, product_id=prod)
    live = store.get("product_intake", str(prod))
    live["product"]["description"] = "The product source has changed outside the confirmed rescan."
    store.put("product_intake", str(prod), live)

    class NoCallProvider:
        calls = 0

        async def parse(self, messages, response_model):
            self.calls += 1
            raise AssertionError("Provider must never run on stale source")

    provider = NoCallProvider()
    with pytest.raises(RuntimeError, match="VOICE_CHALLENGER_PRODUCT_SOURCE_CHANGED"):
        await VoiceProfileService(store).generate_reserved(
            reserved["job_id"], provider=provider, allow_llm_call=True,
        )
    assert provider.calls == 0

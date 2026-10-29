"""Existing-product initial voice bootstrap: no-pay default and exact-job paid gate."""
import argparse
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.runtime_store import MemoryRuntimeStateStore
from app.voice_bootstrap import APPROVAL, VoiceInitialBootstrap, run
from app.voice_initial_worker import VOICE_BUDGET_SLOT_NAMESPACE
from app.voice_profiles import VoiceProfileService


class DurableFakeStore(MemoryRuntimeStateStore):
    """In-memory for tests, but emulates a durable backend capability."""
    ephemeral = False


class VoiceLLM:
    def __init__(self):
        self.calls = 0

    async def parse(self, messages, response_model):
        self.calls += 1
        return response_model(
            character_summary=(
                "An observant and gently witty character with specific reactions "
                "rather than generic advice."
            ),
            default_tone="Conversational, perceptive, calm, and lightly playful.",
            speech_traits=[
                "Responds to a concrete detail rather than summarizing the post.",
                "Varies rhythm without forcing jokes or questions.",
            ],
            adaptation_rules=[
                "Be matter-of-fact in a serious discussion.",
                "Let mild wit show in informal conversations.",
            ],
            style_instruction=(
                "Write with quiet confidence and perceptive, low-key humour. "
                "Notice the specific tension or choice in the selected post, "
                "and say one grounded thing about it. Avoid invented biography "
                "and product marketing. Be conversational rather than formal."
            ),
            derivation_summary=(
                "The product is an interactive AI character for conversational engagement."
            ),
            fact_refs=["product.description"],
            examples=[
                {
                    "surface": "CHANNEL_COMMENT",
                    "context": "An event explains a new dress code",
                    "message": "Похоже, дресс-код уже стал половиной программы.",
                },
                {
                    "surface": "GROUP_REPLY",
                    "context": "Someone wonders about timing",
                    "message": "Вот где расписание становится интереснее афиши.",
                },
            ],
        )


def fixture_data(*, confirmed=True, durable=True):
    store = DurableFakeStore() if durable else MemoryRuntimeStateStore()
    project_id, product_id = uuid4(), uuid4()
    product = {
        "id": str(product_id), "status": "CONFIRMED",
        "name": "Example Character",
        "description": "An interactive AI character for conversation with adults.",
        "value_proposition": "Interesting character-led conversations",
        "known_audience": ["People who enjoy playful dialogue"],
    }
    store.put("customer_acquisition_projects", str(project_id), {
        "id": str(project_id), "product_id": str(product_id),
        "understanding_confirmed": confirmed,
    })
    store.put("product_intake", str(product_id), {"product": product})
    return store, project_id, product_id, product


def args(project_id, product_id, *, reserve_only=False, generate_paid=False,
         expected_job_id="", ack="", monthly_budget_usd="0.50",
         reservation_usd="0.25", max_output_tokens=1600):
    return argparse.Namespace(
        project_id=project_id, product_id=product_id,
        reserve_only=reserve_only, generate_paid=generate_paid,
        expected_job_id=expected_job_id, ack=ack,
        monthly_budget_usd=monthly_budget_usd, reservation_usd=reservation_usd,
        max_output_tokens=max_output_tokens,
    )


def settings():
    return Settings(
        _env_file=None, runtime_storage="database",
        llm_provider="openai", openai_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_preflight_has_no_store_mutation_or_provider_call():
    store, pid, product_id, _ = fixture_data()
    before = len(store.list_namespace("voice_profile_generation_jobs"))
    result = await run(args(pid, product_id), store=store, settings=settings())
    assert result["status"] == "RESERVATION_NEEDED"
    assert result["provider_calls"] == 0
    assert len(store.list_namespace("voice_profile_generation_jobs")) == before
    assert store.list_namespace(VOICE_BUDGET_SLOT_NAMESPACE) == []


@pytest.mark.asyncio
async def test_existing_confirmed_product_can_be_reserved_then_generated_exactly_once():
    store, pid, product_id, product = fixture_data()
    cli_settings = settings()
    reserve_args = args(pid, product_id, reserve_only=True)
    first = await run(reserve_args, store=store, settings=cli_settings)
    second = await run(reserve_args, store=store, settings=cli_settings)
    assert first == second
    assert first["status"] == "RESERVED"
    assert len(store.list_namespace("voice_profile_generation_jobs")) == 1
    provider = VoiceLLM()
    paid = args(
        pid, product_id, generate_paid=True, expected_job_id=first["job_id"],
        ack=APPROVAL,
    )
    completed = await run(paid, store=store, settings=cli_settings, provider=provider)
    assert completed["status"] == "VOICE_INITIAL_CREATED"
    assert completed["provider_calls"] == 1
    assert provider.calls == 1
    assert len(store.list_namespace(VOICE_BUDGET_SLOT_NAMESPACE)) == 1
    active = VoiceProfileService(store).active(
        project_id=pid, product_id=product_id, product_data=product,
    )
    assert str(active.id) == completed["voice_profile_version_id"]
    assert VoiceInitialBootstrap(store).plan(
        project_id=pid, product_id=product_id
    )["status"] == "ACTIVE"
    with pytest.raises(RuntimeError, match="EXPECTED_RESERVED_JOB"):
        await run(paid, store=store, settings=cli_settings, provider=provider)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_mismatched_or_missing_job_never_charges_or_processes_other_projects():
    store, pid, product_id, _ = fixture_data()
    other_pid, other_product_id = uuid4(), uuid4()
    original = store.get("product_intake", str(product_id))["product"]
    store.put("customer_acquisition_projects", str(other_pid), {
        "id": str(other_pid), "product_id": str(other_product_id),
        "understanding_confirmed": True,
    })
    store.put("product_intake", str(other_product_id), {
        "product": {**original, "id": str(other_product_id)},
    })
    primary = VoiceInitialBootstrap(store).reserve(project_id=pid, product_id=product_id)
    other = VoiceInitialBootstrap(store).reserve(
        project_id=other_pid, product_id=other_product_id
    )
    provider = VoiceLLM()
    with pytest.raises(RuntimeError, match="EXPECTED_RESERVED_JOB"):
        await run(args(
            pid, product_id, generate_paid=True, ack=APPROVAL,
            expected_job_id=other["job_id"],
        ), store=store, settings=settings(), provider=provider)
    assert provider.calls == 0
    completed = await run(args(
        pid, product_id, generate_paid=True, ack=APPROVAL,
        expected_job_id=primary["job_id"],
    ), store=store, settings=settings(), provider=provider)
    assert completed["status"] == "VOICE_INITIAL_CREATED"
    assert provider.calls == 1
    assert VoiceInitialBootstrap(store).plan(
        project_id=other_pid, product_id=other_product_id
    )["status"] == "RESERVED"


@pytest.mark.asyncio
async def test_paid_cli_requires_durable_store_explicit_ack_and_budget():
    store, pid, product_id, _ = fixture_data(durable=False)
    provider = VoiceLLM()
    with pytest.raises(RuntimeError, match="DURABLE_DATABASE"):
        await run(args(pid, product_id, reserve_only=True),
                  store=store, settings=settings())
    with pytest.raises(RuntimeError, match="DURABLE_DATABASE"):
        await run(args(pid, product_id, generate_paid=True, ack=APPROVAL),
                  store=store, settings=settings(), provider=provider)
    durable, pid, product_id, _ = fixture_data()
    job = VoiceInitialBootstrap(durable).reserve(project_id=pid, product_id=product_id)
    for candidate in (
        args(pid, product_id, generate_paid=True, expected_job_id=job["job_id"]),
        args(pid, product_id, generate_paid=True, expected_job_id=job["job_id"],
             ack=APPROVAL, reservation_usd="1.0"),
        args(pid, product_id, generate_paid=True, expected_job_id=job["job_id"],
             ack=APPROVAL, max_output_tokens=5000),
    ):
        with pytest.raises((PermissionError, ValueError)):
            await run(candidate, store=durable, settings=settings(), provider=provider)
    assert provider.calls == 0
    assert durable.list_namespace(VOICE_BUDGET_SLOT_NAMESPACE) == []


@pytest.mark.asyncio
async def test_bootstrap_refuses_unconfirmed_or_changed_product():
    store, pid, product_id, _ = fixture_data(confirmed=False)
    bootstrap = VoiceInitialBootstrap(store)
    with pytest.raises(PermissionError, match="NOT_CONFIRMED"):
        bootstrap.plan(project_id=pid, product_id=product_id)
    store, pid, product_id, _ = fixture_data()
    job = VoiceInitialBootstrap(store).reserve(project_id=pid, product_id=product_id)
    row = store.get("product_intake", str(product_id))
    row["product"]["description"] = "A totally different service no longer aligned with voice."
    store.put("product_intake", str(product_id), row)
    with pytest.raises(RuntimeError, match="EXPECTED_RESERVED_JOB"):
        await run(args(
            pid, product_id, generate_paid=True,
            expected_job_id=job["job_id"], ack=APPROVAL,
        ), store=store, settings=settings(), provider=VoiceLLM())


@pytest.mark.asyncio
async def test_inspect_active_returns_real_saved_character_without_new_model_call():
    store, pid, product_id, _ = fixture_data()
    job = VoiceInitialBootstrap(store).reserve(project_id=pid, product_id=product_id)
    provider = VoiceLLM()
    await run(args(
        pid, product_id, generate_paid=True, ack=APPROVAL,
        expected_job_id=job["job_id"],
    ), store=store, settings=settings(), provider=provider)
    snapshot = VoiceInitialBootstrap(store).inspect(project_id=pid, product_id=product_id)
    assert snapshot["status"] == "ACTIVE_VOICE_INSPECTED"
    assert "observant" in snapshot["character"]["style_instruction"].lower()
    assert snapshot["character"]["examples"][0]["origin"] == "SYNTHETIC_UNVERIFIED"
    assert snapshot["provider_calls"] == 0
    assert snapshot["publication_actions"] == 0
    assert provider.calls == 1

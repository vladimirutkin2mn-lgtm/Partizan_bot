"""Offline tests for finite initial voice spending and non-retry semantics."""
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.runtime_store import MemoryRuntimeStateStore
from app.voice_initial_worker import (
    VOICE_BUDGET_SLOT_NAMESPACE,
    VoiceInitialGenerationWorker,
)
from app.voice_profiles import VoiceProfileService


def source():
    return {
        "name": "Example AI Companion",
        "description": "A conversational artificial intelligence product for people "
                       "who enjoy playful, context-aware messages.",
        "known_audience": ["People who like curious dialogue"],
        "value_proposition": "Consistent conversational experiences",
    }


def setup():
    store = MemoryRuntimeStateStore()
    project_id, product_id = uuid4(), uuid4()
    store.put("customer_acquisition_projects", str(project_id), {
        "id": str(project_id), "product_id": str(product_id),
        "understanding_confirmed": True,
    })
    store.put("product_intake", str(product_id), {
        "product": source(),
    })
    job = VoiceProfileService(store).reserve_initial(
        project_id=project_id, product_id=product_id, product_data=source()
    )
    return store, project_id, product_id, job


def settings(**overrides):
    values = {
        "voice_generation_worker_enabled": False,
        "voice_generation_monthly_budget_usd": Decimal("0.50"),
        "voice_generation_call_reservation_usd": Decimal("0.25"),
        "voice_generation_max_output_tokens": 1600,
        "llm_provider": "openai", "openai_api_key": "fake-test-key",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class StubProvider:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    async def parse(self, messages, response_model):
        self.calls += 1
        if self.fail:
            raise TimeoutError("May have billed externally")
        return response_model(
            character_summary="An observant, warm voice with a hint of wit "
                              "that invites curiosity without a commercial pitch.",
            default_tone="Calm, conversational and confident without sounding formal.",
            speech_traits=[
                "Responds to the details that make the topic unusual.",
                "Uses short, straightforward sentences and occasional understatement.",
            ],
            adaptation_rules=[
                "In a serious thread be concise and respectful of the situation.",
                "In informal chat allow light, understated playfulness.",
            ],
            style_instruction=(
                "Speak like an observant, confident conversation partner. "
                "Respond to the specific selected topic in accessible natural "
                "language. Keep the pacing concise and vary sentence rhythm. "
                "Use occasional understated wit when the community invites it. "
                "Avoid artificial slogans or promotional claims and never "
                "pretend you have personally attended an event."
            ),
            derivation_summary="Product positioning stresses conversational "
                               "intelligence and the audience enjoys natural dialogue.",
            fact_refs=["product.description"],
            examples=[
                {
                    "surface": "CHANNEL_COMMENT", "context": "A post about a dress code",
                    "message": "Кажется, цвет здесь уже задаёт половину настроения.",
                    "origin": "SYNTHETIC_UNVERIFIED",
                },
                {
                    "surface": "GROUP_REPLY", "context": "A member jokes about a late arrival",
                    "message": "Вечер даже не начался, а интрига уже есть.",
                    "origin": "SYNTHETIC_UNVERIFIED",
                },
            ],
        )


@pytest.mark.asyncio
async def test_disabled_worker_never_calls_provider_or_consumes_slot():
    store, _, _, job = setup()
    provider = StubProvider()
    result = await VoiceInitialGenerationWorker(
        store=store, settings=settings(), provider=provider
    ).run_once()
    assert result["status"] == "DISABLED"
    assert provider.calls == 0
    assert not store.list_namespace(VOICE_BUDGET_SLOT_NAMESPACE)
    assert VoiceProfileService(store).job(job.id).status == "RESERVED"


@pytest.mark.asyncio
async def test_missing_budget_or_max_tokens_blocks_before_provider():
    store, _, _, _ = setup()
    provider = StubProvider()
    for limits in (
        {"voice_generation_monthly_budget_usd": None},
        {"voice_generation_call_reservation_usd": None},
        {"voice_generation_max_output_tokens": None},
        {"voice_generation_max_output_tokens": 5001},
    ):
        result = await VoiceInitialGenerationWorker(
            store=store, settings=settings(
                voice_generation_worker_enabled=True, **limits
            ), provider=provider
        ).run_once()
        assert result["status"] == "BUDGET_OR_OUTPUT_CAP_NOT_CONFIGURED"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_one_authorized_call_creates_and_activates_voice_without_repeat():
    store, project_id, product_id, job = setup()
    provider = StubProvider()
    worker = VoiceInitialGenerationWorker(
        store=store, settings=settings(voice_generation_worker_enabled=True),
        provider=provider, now=lambda: datetime(2026, 10, 29, tzinfo=UTC),
    )
    first = await worker.run_once()
    second = await worker.run_once()
    assert first["status"] == "VOICE_INITIAL_CREATED"
    assert first["provider_calls"] == 1
    assert second["provider_calls"] == 0
    assert provider.calls == 1
    assert len(store.list_namespace(VOICE_BUDGET_SLOT_NAMESPACE)) == 1
    assert VoiceProfileService(store).job(job.id).status == "CREATED"
    assert str(VoiceProfileService(store).active(
        project_id=project_id, product_id=product_id, product_data=source()
    ).id) == first["voice_profile_version_id"]


@pytest.mark.asyncio
async def test_unknown_provider_outcome_cannot_be_auto_retried():
    store, _, _, job = setup()
    provider = StubProvider(fail=True)
    worker = VoiceInitialGenerationWorker(
        store=store, settings=settings(voice_generation_worker_enabled=True),
        provider=provider,
    )
    first = await worker.run_once()
    second = await worker.run_once()
    assert first["status"] == "VOICE_PROVIDER_OUTCOME_UNKNOWN"
    assert second["status"] == "NO_ELIGIBLE_RESERVED_VOICE_JOBS"
    assert provider.calls == 1
    assert VoiceProfileService(store).job(job.id).status == "PROVIDER_OUTCOME_UNKNOWN"
    assert len(store.list_namespace(VOICE_BUDGET_SLOT_NAMESPACE)) == 1


@pytest.mark.asyncio
async def test_stale_product_snapshot_is_not_charged():
    store, _, product_id, _ = setup()
    row = store.get("product_intake", str(product_id))
    row["product"]["description"] = "A fundamentally different product."
    store.put("product_intake", str(product_id), row)
    provider = StubProvider()
    result = await VoiceInitialGenerationWorker(
        store=store, settings=settings(voice_generation_worker_enabled=True),
        provider=provider,
    ).run_once()
    assert result["status"] == "NO_ELIGIBLE_RESERVED_VOICE_JOBS"
    assert provider.calls == 0
    assert not store.list_namespace(VOICE_BUDGET_SLOT_NAMESPACE)


@pytest.mark.asyncio
async def test_budget_slots_exhausted_while_job_is_reserved_for_next_month():
    store, project_id, product_id, job = setup()
    store.put(VOICE_BUDGET_SLOT_NAMESPACE, f"{product_id}:2026-10:000", {
        "slot_id": "used", "status": "RESERVED_ESTIMATE"
    })
    provider = StubProvider()
    result = await VoiceInitialGenerationWorker(
        store=store, settings=settings(
            voice_generation_worker_enabled=True,
            voice_generation_monthly_budget_usd=Decimal("0.25"),
        ), provider=provider, now=lambda: datetime(2026, 10, 29, tzinfo=UTC),
    ).run_once()
    assert result["status"] == "MONTHLY_VOICE_BUDGET_EXHAUSTED"
    assert provider.calls == 0
    assert VoiceProfileService(store).job(job.id).status == "RESERVED"


@pytest.mark.asyncio
async def test_targeted_paid_voice_worker_disables_provider_auto_retries(monkeypatch):
    store, _, _, job = setup()
    provider = StubProvider()
    kwargs_seen = {}

    def fake_factory(**kwargs):
        kwargs_seen.update(kwargs)
        return provider

    monkeypatch.setattr("app.voice_initial_worker.get_llm_provider", fake_factory)
    worker = VoiceInitialGenerationWorker(
        store=store, settings=settings(voice_generation_worker_enabled=True),
    )
    result = await worker.run_once(expected_job_id=job.id)
    assert result["status"] == "VOICE_INITIAL_CREATED"
    assert provider.calls == 1
    assert kwargs_seen == {"max_output_tokens": 1600, "max_retries": 0}

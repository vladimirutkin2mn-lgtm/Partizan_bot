"""Read-only v17 tri-arm benchmark: no SDK calls, Telegram I/O or real writes."""
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.action_drafting import DistributionContentDraft
from app.runtime_store import MemoryRuntimeStateStore
from app.voice_profiles import VoiceExample, VoiceProfileDraft, VoiceProfileService
from tools import compare_saved_telegram_three_voices as benchmark


class VoiceProvider:
    calls = 0

    async def parse(self, messages, response_model):
        self.calls += 1
        return VoiceProfileDraft(
            character_summary="A gently playful and perceptive character who avoids sales jargon.",
            default_tone="Quiet confidence, natural curiosity, grounded and never preachy.",
            speech_traits=["Short sentences", "Specific reactions instead of generic praise"],
            adaptation_rules=["No forced jokes", "Respect serious group discussions"],
            style_instruction=(
                "Speak with calm confidence and specific, observant humour. "
                "Notice the small disagreement or interesting implication in the post. "
                "Use natural language and do not invent personal attendance, identity, "
                "customer history or product usage. Be concise and avoid sales pitch."
            ),
            derivation_summary="The product facilitates thoughtful and playful conversations.",
            fact_refs=["product.description"],
            examples=[
                VoiceExample(
                    surface="CHANNEL_COMMENT",
                    context="Community announces an event",
                    message="И формат встречи сам по себе оказался самой интригующей частью.",
                ),
                VoiceExample(
                    surface="GROUP_REPLY",
                    context="Participants discuss their expectations",
                    message="Иногда ожидания куда интереснее самого объявления.",
                ),
            ],
        )


class DraftProvider:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    async def parse(self, messages, response_model):
        assert response_model is DistributionContentDraft
        assert len(messages) == 2
        self.calls += 1
        if self.fail:
            raise TimeoutError("Ambiguous potentially paid response")
        return DistributionContentDraft(
            content_text="В этом объявлении интереснее всего формат самого разговора.",
            context_text="This is only an offline frozen post.",
            rationale="A reaction grounded in the specific post, without promotion.",
        )


async def prepared(monkeypatch, *, surface="CHANNEL_COMMENT"):
    store = MemoryRuntimeStateStore()
    product_id = UUID(benchmark._PRODUCT)
    project_id = UUID(benchmark._PROJECT)
    product_data = {
        "name": "Conversation Agent",
        "description": "An interactive AI character with consistent personality for adults.",
        "value_proposition": "A character for conversation and reflection",
        "known_audience": ["People who enjoy conversational AI"],
        "product_type": "AI companion",
    }
    product = SimpleNamespace(
        id=product_id,
        model_dump=lambda mode="json": product_data,
    )
    store.put("customer_acquisition_projects", str(project_id), {
        "id": str(project_id), "product_id": str(product_id),
    })
    profiles = VoiceProfileService(store)
    job = profiles.reserve_initial(
        project_id=project_id, product_id=product_id,
        product_data=product_data,
    )
    creator = VoiceProvider()
    version = await profiles.generate_reserved(
        job.id, provider=creator, allow_llm_call=True, activate_initial=True,
    )
    assert creator.calls == 1
    case = {
        "handle": "a_sfera", "post_id": 2117,
        "target_url": (
            "https://t.me/a_sfera"
            if surface == "STANDALONE_POST" else "https://t.me/a_sfera/2117"
        ),
        "surface": surface,
        "target_text": (
            "В этой группе обсуждают формат встреч и атмосферу сообщества."
        ),
        "community_title": "Атмосфера",
        "community_about": "Неформальное сообщество по интересам.",
    }
    monkeypatch.setattr(benchmark, "_cases", lambda store: ([case], []))
    return store, product, version


@pytest.mark.asyncio
async def test_offline_manifest_has_exact_composer_v17_and_no_remote_or_persistent_writes(
    monkeypatch,
):
    store, product, voice = await prepared(monkeypatch)
    before = {
        namespace: store.list_namespace(namespace)
        for namespace in (
            "customer_acquisition_projects",
            "voice_profile_versions",
            "voice_profile_assignments",
            "voice_draft_generations",
            "distribution_action",
        )
    }
    manifest = await benchmark.build_manifest(store=store, product=product)
    assert manifest["status"] == "PREFLIGHT_READY_NO_MODEL_CALLS"
    assert manifest["v17_is_exact_composer_request"] is True
    assert manifest["voice_profile_version_id"] == str(voice.id)
    assert manifest["max_calls"] == 3
    arms = manifest["cases"][0]["arms"]
    assert set(arms) == set(benchmark.ARMS)
    assert len({arms[arm]["request_hash"] for arm in benchmark.ARMS}) == 3
    assert all(len(arms[arm]["messages"]) == 2 for arm in benchmark.ARMS)
    assert "community_message_v17_persisted_voice" in arms["v17"]["messages"][0].content
    assert str(voice.id) in arms["v17"]["messages"][1].content
    assert "actual_publisher_profile" not in arms["v17"]["messages"][1].content
    for namespace, items in before.items():
        assert store.list_namespace(namespace) == items
    # Creating a manifest is no-send and no-paid, regardless of writer flag.


@pytest.mark.asyncio
async def test_offline_manifest_supports_standalone_without_reply_fabrication(
    monkeypatch,
):
    store, product, _ = await prepared(monkeypatch, surface="STANDALONE_POST")
    manifest = await benchmark.build_manifest(store=store, product=product)
    payload = manifest["cases"][0]["arms"]["v17"]["messages"][1].content
    assert '"action_type":"STANDALONE_POST"' in payload
    assert '"url":"https://t.me/a_sfera"' in payload


@pytest.mark.asyncio
async def test_offline_manifest_rejects_missing_active_voice_before_paid_calls(monkeypatch):
    store, product, _ = await prepared(monkeypatch)
    store.clear_namespace("voice_profile_assignments")
    with pytest.raises(RuntimeError, match="VOICE_INITIALIZING"):
        await benchmark.build_manifest(store=store, product=product)


@pytest.mark.asyncio
async def test_bounded_paid_runner_requires_explicit_approval_and_finite_budget(monkeypatch):
    store, product, _ = await prepared(monkeypatch)
    manifest = await benchmark.build_manifest(store=store, product=product)
    provider = DraftProvider()
    with pytest.raises(PermissionError, match="EXPLICIT_PAID_APPROVAL_REQUIRED"):
        await benchmark.run_comparison(manifest=manifest, provider=provider)
    with pytest.raises(ValueError, match="BUDGET_BELOW_FULL_ENVELOPE"):
        await benchmark.run_comparison(
            manifest=manifest, provider=provider, authorized=True,
            estimated_budget_usd="0.01", estimated_reservation_usd="0.1",
        )
    assert provider.calls == 0
    result = await benchmark.run_comparison(
        manifest=manifest, provider=provider, authorized=True,
        estimated_budget_usd="0.3", estimated_reservation_usd="0.1",
    )
    assert provider.calls == 3
    assert result["status"] == "AWAITING_BLIND_HUMAN_REVIEW"
    assert result["publication_actions"] == 0
    assert result["telegram_api_calls"] == 0
    assert result["provider_invoice_amount_usd"] is None
    assert len(result["results"][0]["arms"]) == 3
    assert result["promotion_approved"] is False


@pytest.mark.asyncio
async def test_bounded_runner_stops_on_unknown_provider_outcome_without_retry(monkeypatch):
    store, product, _ = await prepared(monkeypatch)
    manifest = await benchmark.build_manifest(store=store, product=product)
    provider = DraftProvider(fail=True)
    result = await benchmark.run_comparison(
        manifest=manifest, provider=provider, authorized=True,
        estimated_budget_usd="0.3", estimated_reservation_usd="0.1",
    )
    assert result["status"] == "INCOMPLETE_PROVIDER_OUTCOME"
    assert result["error_type"] == "TimeoutError"
    assert provider.calls == result["provider_calls_attempted"] == 1
    assert len(result["results"][0]["arms"]) == 0


def test_bounded_cost_preflight_rejects_zero_nan_and_infinite():
    for raw in ("0", "-1", "NaN", "Infinity", "bad"):
        with pytest.raises(ValueError, match="POSITIVE_BUDGET_REQUIRED"):
            benchmark._positive(raw)

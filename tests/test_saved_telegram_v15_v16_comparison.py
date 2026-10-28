"""Offline v15.1 versus v16.2 research never touches Telegram or publishing."""
from types import SimpleNamespace

from app.runtime_store import MemoryRuntimeStateStore
from tools import compare_saved_telegram_v15_v16_once as experiment


def _prepare_cases(store, *, count=6):
    store.put("customer_acquisition_projects", experiment._PROJECT, {
        "id": experiment._PROJECT, "product_id": experiment._PRODUCT,
    })
    handles = (
        ("a_sfera", "CHANNEL"),
        ("play_hard_community", "GROUP"),
        ("bizarreclubmsk", "CHANNEL"),
        ("findom_gospoja0", "GROUP"),
        ("fem_tut", "CHANNEL"),
        ("poiskrabovirabin", "GROUP"),
    )
    for i, (handle, kind) in enumerate(handles[:count]):
        mid = 1000 + i
        store.put("telegram_community_registry", handle, {
            "product_id": experiment._PRODUCT,
            "id": handle,
            "url": f"https://t.me/{handle}",
            "title": f"Community {i}",
            "kind": kind,
            "metadata": {
                "handle": handle, "native_research_status": "VERIFIED",
                "about": "Разговоры о мероприятиях и интересных образах.",
                "recent_context": [{
                    "message_id": mid,
                    "url": f"https://t.me/{handle}/{mid}",
                    "text": f"Обсуждаем разные образы и идеи для встречи {i} в эту субботу.",
                }],
            },
        })


def test_snapshot_pair_selection_uses_only_saved_posts():
    store = MemoryRuntimeStateStore()
    _prepare_cases(store)
    cases, skipped = experiment._cases(store)
    assert len(cases) == 6
    assert not skipped
    assert set(row["surface"] for row in cases) == {
        "CHANNEL_COMMENT", "GROUP_REPLY"
    }
    assert len({row["handle"] for row in cases}) == 6
    assert all(row["target_text"].startswith("Обсуждаем разные образы") for row in cases)


def test_no_telegram_send_and_bounded_twelve_fake_model_calls(monkeypatch):
    store = MemoryRuntimeStateStore()
    _prepare_cases(store)
    sizes_before = {
        name: len(store.list_namespace(name))
        for name in ("customer_acquisition_projects", "telegram_community_registry")
    }

    class FakeOpenAI:
        def __init__(self):
            self.calls = 0
            self.system_prompts = []

            class Responses:
                def __init__(self, parent):
                    self.parent = parent

                def parse(self, *, model, input, text_format, max_output_tokens):
                    self.parent.calls += 1
                    self.parent.system_prompts.append(input[0]["content"])
                    assert model == "gpt-6-luna"
                    assert max_output_tokens <= 1100
                    assert "Telegram" not in str(type(self.parent))
                    return SimpleNamespace(
                        output_parsed=text_format(
                            topicality="RELEVANT",
                            language="ru",
                            comment="Чёрно-оранжевые образы точно будут заметны.",
                            reason="Reflects the actual costume topic in the saved post.",
                        ),
                        usage=SimpleNamespace(input_tokens=500, output_tokens=75),
                    )

            self.responses = Responses(self)

    fake = FakeOpenAI()
    monkeypatch.setattr("app.runtime_store.get_runtime_store", lambda: store)
    monkeypatch.setattr(
        experiment.product_intake_service,
        "get_product",
        lambda product_id: SimpleNamespace(model_dump=lambda mode="json": {
            "name": "Conversational AI",
            "description": "A conversational companion product with a distinct persona",
            "known_audience": ["People interested in AI personas"],
        }),
    )
    monkeypatch.setattr(
        experiment, "get_settings",
        lambda: SimpleNamespace(openai_api_key="fake-research-only-key"),
    )
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: fake)

    result = experiment.run()
    assert result["status"] == "REVIEW_ONLY"
    assert result["paired_results"] == 6
    assert result["llm_calls"] == 12 == fake.calls
    assert result["selected_cases"] == 6
    assert all(result[key] == 0 for key in (
        "telegram_api_calls", "publication_actions",
        "profile_reads", "runtime_writes", "global_search_calls",
    ))
    assert result["conversion_evidence"] == "NONE_OFFLINE"
    assert result["promotion_approved"] is False
    assert len(set(fake.system_prompts)) == 2
    assert result["baseline_version"] == "community_message_v15_1_profile_curiosity_trial"
    assert result["challenger_version"] == "community_message_v16_2_product_led_voice"
    for name, size in sizes_before.items():
        assert len(store.list_namespace(name)) == size


def test_missing_saved_cases_produces_no_model_calls(monkeypatch):
    store = MemoryRuntimeStateStore()
    _prepare_cases(store, count=0)
    monkeypatch.setattr("app.runtime_store.get_runtime_store", lambda: store)
    monkeypatch.setattr(
        experiment.product_intake_service, "get_product",
        lambda *_: (_ for _ in ()).throw(AssertionError("product access forbidden")),
    )
    result = experiment.run()
    assert result["status"] == "NO_ELIGIBLE_SAVED_POSTS"
    assert result["llm_calls"] == 0
    assert result["publication_actions"] == 0

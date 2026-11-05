"""Bounded, read-only bridge for 20 writer calls and 10 blinded quality assessments."""
import asyncio
import base64
import hashlib
import json
import sys
from datetime import UTC, datetime
from uuid import UUID

from app.action_drafting import DistributionContentDraft as LegacyDraft
from app.config import get_settings
from app.llm import LLMMessage, get_llm_provider
from app.openai_usage import openai_task_options, openai_usage_context
from app.product_intake import product_intake_service
from app.project_marketing_memory import ProjectMarketingMemoryService
from app.runtime_store import MemoryRuntimeStateStore, get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle
from app.telegram_native_flood_wait import TELEGRAM_RESEARCH_FLOOD_WAIT_NAMESPACE
from pydantic import BaseModel, Field

PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
PROJECT = UUID("124cdd70-49b0-48b3-aee0-a037dda4bcd0")
HANDLES = ("a_sfera", "play_hard_community", "bizarreclubmsk", "findom_gospoja0", "poiskrabovirabin")
IDS = (2123, 2121, 418, 413, 8661, 8660, 536536, 536534, 2751285, 2751284)


class MessageCreativePlan(BaseModel):
    intent: str = Field(min_length=3, max_length=200)
    attention_technique: str = Field(min_length=3, max_length=200)


class DistributionContentDraft(LegacyDraft):
    creative_plan: MessageCreativePlan | None = None


class CandidateRating(BaseModel):
    label: str
    naturalness: int = Field(ge=0, le=5)
    profile_interest: int = Field(ge=0, le=5)
    on_topic: int = Field(ge=0, le=5)
    factual_fit: bool
    explicit_promotion: bool
    ready_without_editing: bool
    reason: str = Field(min_length=3, max_length=320)


class QualityAssessment(BaseModel):
    ratings: list[CandidateRating] = Field(min_length=4, max_length=4)
    best_labels: list[str] = Field(max_length=4)
    confidence: str


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def task_options(task):
    with openai_usage_context(task, PRODUCT):
        return openai_task_options(get_settings().llm_model)


def context(store):
    project = store.get("customer_acquisition_projects", str(PROJECT))
    assert str(project["product_id"]) == str(PRODUCT)
    product = product_intake_service.get_product(PRODUCT).model_dump(mode="json")
    fields = ("name", "description", "value_proposition", "usp", "product_type", "problem_or_desire",
              "known_audience", "use_cases", "language")
    registry = TelegramCommunityRegistry(store)
    peers = {}
    for row in registry.list_for_product(PRODUCT):
        meta = registry.hydrate_metadata(row)
        handle = _public_handle(meta.get("handle") or row.get("url"))
        if handle in HANDLES:
            peers[handle] = {"title": str(row.get("title") or handle),
                             "about": str(meta.get("about") or meta.get("description") or "")[:1000],
                             "reason": str((row.get("audience_fit") or {}).get("reason") or "")[:140]}
    memory = ProjectMarketingMemoryService(store)
    return {
        "product": {k: product.get(k) for k in fields}, "peers": peers,
        "memory": {a: (memory.prompt_context_for_product(PRODUCT, platform="TELEGRAM", action_type=a).rendered or "")[:700]
                   for a in ("COMMENT", "STANDALONE_POST")},
        "writer_options": task_options("action_auto_prepare"),
        "judge_options": task_options("telegram_quality_evaluation"),
        "current_schema_sha256": digest(DistributionContentDraft.model_json_schema()),
    }


async def main():
    media = sys.modules["app.telegram_writer_media"]
    assert get_settings().llm_provider == "openai"
    assert "creative_plan" not in LegacyDraft.model_fields
    writer = get_llm_provider(max_output_tokens=1400, max_retries=0)
    judge = get_llm_provider(max_output_tokens=2200, max_retries=0)
    assert writer and judge
    store = get_runtime_store()
    actual = context(store)
    assert actual["writer_options"]["model"] != actual["judge_options"]["model"]
    waiting = any(str(row.get("blocked_until") or "") > datetime.now(UTC).isoformat()
                  for row in store.list_namespace(TELEGRAM_RESEARCH_FLOOD_WAIT_NAMESPACE))
    scratch = MemoryRuntimeStateStore()
    media.get_runtime_store = lambda: scratch
    emit(actual)
    attempts = {"draft": 0, "judge": 0}
    seen = set()
    photo_checked = False
    for line in sys.stdin:
        req = json.loads(line)
        kind = req["kind"]
        if kind == "photo":
            assert not photo_checked and req["url"] == "https://t.me/bizarreclubmsk/8661"
            photo_checked = True
            photo = media.SelectedPhoto() if waiting else await media.read_selected_photo(req["url"], req["caption"])
            emit({"jpeg": base64.b64encode(photo.jpeg).decode("ascii") if photo.jpeg else None,
                  "linked_url": photo.linked_url, "linked_text": photo.linked_text})
            continue
        number, arm = req["number"], req["arm"]
        assert kind in attempts and 1 <= number <= 10 and (kind, number, arm) not in seen
        assert sum(attempts.values()) < 30
        system, user = req["messages"]
        assert system["role"] == "system" and user["role"] == "user"
        assert len(json.dumps(user)) < 400_000
        if kind == "draft":
            assert attempts[kind] < 20 and arm in {"v18_2", "v18_4"}
            assert system["content"].startswith("Prompt version: telegram_")
            user_parts = user["content"]
            payload = json.loads(user_parts if isinstance(user_parts, str) else user_parts[0]["text"])
            expected = "https://t.me/" + HANDLES[(number - 1) // 2]
            if number <= 6:
                expected += "/" + str(IDS[number - 1])
            assert payload["selected_native_target"]["url"] == expected
            if arm == "v18_4":
                assert req["schema_sha256"] == actual["current_schema_sha256"]
            model = DistributionContentDraft if arm == "v18_4" else LegacyDraft
            provider, task = writer, "action_auto_prepare"
        else:
            assert attempts["draft"] == 20 and attempts[kind] < 10 and arm == "blind"
            assert system["content"].startswith("You evaluate short Russian community messages.")
            model, provider, task = QualityAssessment, judge, "telegram_quality_evaluation"
        seen.add((kind, number, arm))
        attempts[kind] += 1
        try:
            with openai_usage_context(task, PRODUCT):
                options = openai_task_options(get_settings().llm_model)
                result = await provider.parse(
                    messages=[LLMMessage(**system), LLMMessage(**user)], response_model=model,
                )
            emit({"result": result.model_dump(mode="json"), "options": options, "attempts": dict(attempts)})
        except Exception as exc:
            emit({"error_type": type(exc).__name__, "attempts": dict(attempts)})
            return


if __name__ == "__main__":
    asyncio.run(main())

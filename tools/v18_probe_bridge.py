"""Single-use provider bridge. No Telegram publishing, deployment or durable writes."""
import asyncio
import json
import sys
from uuid import UUID

from app.action_drafting import DistributionContentDraft
from app.config import get_settings
from app.llm import LLMMessage, get_llm_provider
from app.openai_usage import openai_task_options, openai_usage_context
from app.product_intake import product_intake_service
from app.project_marketing_memory import ProjectMarketingMemoryService
from app.runtime_store import get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle

PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
PROJECT = UUID("124cdd70-49b0-48b3-aee0-a037dda4bcd0")
HANDLES = ("findom_gospoja0", "poiskrabovirabin")
IDS = (536536, 536534, 2751285, 2751284)


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


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
    return {"product": {k: product.get(k) for k in fields}, "peers": peers,
            "memory": {a: (memory.prompt_context_for_product(PRODUCT, platform="TELEGRAM", action_type=a).rendered or "")[:700]
                       for a in ("COMMENT", "STANDALONE_POST")}}


async def main():

    assert get_settings().llm_provider == "openai"
    provider = get_llm_provider(max_output_tokens=1200, max_retries=0)
    assert provider is not None
    store = get_runtime_store()
    emit(context(store))
    attempted = 0
    for line in sys.stdin:
        req = json.loads(line)
        assert req["kind"] == "draft" and attempted < len(IDS) and req["source_id"] == IDS[attempted]
        system, user = req["messages"]
        assert system["role"] == "system" and system["content"].startswith("Prompt version: telegram_standalone_v18_2\n")
        assert user["role"] == "user" and len(json.dumps(user)) < 400_000
        user_parts = user["content"]
        payload = json.loads(user_parts if isinstance(user_parts, str) else user_parts[0]["text"])
        target = "https://t.me/" + HANDLES[attempted // 2]
        assert payload["selected_native_target"]["url"] == target
        attempted += 1
        try:
            with openai_usage_context("action_auto_prepare", PRODUCT):
                model = openai_task_options(get_settings().llm_model)["model"]
                draft = await provider.parse(messages=[LLMMessage(**system), LLMMessage(**user)],
                                             response_model=DistributionContentDraft)
            emit({"draft": draft.model_dump(mode="json"), "model": model, "attempted": attempted})
        except Exception as exc:
            emit({"error_type": type(exc).__name__, "attempted": attempted})
            return


if __name__ == "__main__":
    asyncio.run(main())

"""One-off read-only REAL composer benchmark. No Telegram calls or runtime writes.

Input: two short-lived GitHub Actions artifacts from Telethon read-only capture.
Calls actual provider one time per benchmark case, without publishing. Never
persists a DistributionAction, draft cache, workflow execution, or Telegram action.
"""
import asyncio
import base64
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from app.action_drafting import DistributionActionComposer, SelectedActionTarget
from app.config import get_settings
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.llm import get_llm_provider
from app.product_intake import product_intake_service
from app.project_marketing_memory import ProjectMarketingMemoryService
from app.runtime_store import MemoryRuntimeStateStore, get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle
from app.voice_draft_cache import VoiceDraftCache
from app.voice_profiles import VoiceProfileService

PRODUCT_ID = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
PROJECT_ID = UUID("124cdd70-49b0-48b3-aee0-a037dda4bcd0")
EXPECTED = [
    ("a_sfera", 2123), ("a_sfera", 2121),
    ("play_hard_community", 418), ("play_hard_community", 413),
    ("bizarreclubmsk", 8661), ("bizarreclubmsk", 8660),
    ("findom_gospoja0", 536536), ("findom_gospoja0", 536534),
    ("poiskrabovirabin", 2751285), ("poiskrabovirabin", 2751284),
]
MAX_CALLS = 10

def load_cases():
    a = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
    b = json.loads(base64.b64decode(sys.argv[2]).decode("utf-8"))
    if a.get("status") != "READ_ONLY_COMPLETE" or b.get("status") != "READ_ONLY_COMPLETE":
        raise RuntimeError("TELETHON_INPUT_INCOMPLETE")
    peers = {c["handle"]: c for c in a["communities"]}
    peers["play_hard_community"] = b["communities"][0]
    if set(peers) != set(x[0] for x in EXPECTED):
        raise RuntimeError("UNEXPECTED_PEERS")
    results = []
    for handle, message_id in EXPECTED:
        record = peers[handle]
        posts = [x for x in record["posts"] if x["id"] == message_id]
        if len(posts) != 1:
            raise RuntimeError("EXPECTED_POST_NOT_FOUND")
        results.append((record, posts[0]))
    return results

def registry_context(store):
    reg = TelegramCommunityRegistry(store)
    result = {}
    for row in reg.list_for_product(PRODUCT_ID):
        meta = reg.hydrate_metadata(row)
        handle = _public_handle(meta.get("handle") or row.get("url"))
        if handle:
            result[handle] = (row, meta)
    return result

async def run():
    settings = get_settings()
    provider = get_llm_provider(max_output_tokens=1200, max_retries=0)
    if provider is None or settings.llm_provider != "openai":
        raise RuntimeError("REAL_LLM_PROVIDER_UNAVAILABLE_NO_MOCK_ALLOWED")
    inputs = load_cases()
    store = get_runtime_store()
    product = product_intake_service.get_product(PRODUCT_ID)
    if product.id != PRODUCT_ID:
        raise RuntimeError("PRODUCT_MISMATCH")
    project = store.get("customer_acquisition_projects", str(PROJECT_ID))
    if not isinstance(project, dict) or str(project.get("product_id")) != str(PRODUCT_ID):
        raise RuntimeError("PROJECT_MISMATCH")
    known = registry_context(store)
    runs = []
    for idx, (record, post) in enumerate(inputs, start=1):
        handle = record["handle"]
        source = known.get(handle)
        row, meta = source if source else ({}, {})
        is_group = record["kind"] == "GROUP"
        if is_group:
            group_about = str(meta.get("about") or meta.get("description") or "").strip()
            # Intentionally keep BOTH group cases as independent samples of
            # group-root drafting, never reply to latest member's message.
            if len(group_about) < 12:
                runs.append({"index":idx,"handle":handle,"post_url":post["url"],
                             "status":"INSUFFICIENT_GROUP_DESCRIPTION",
                             "source_message_sent_to_llm":False})
                continue
            context = group_about
            target_url = f"https://t.me/{handle}"
            action = DistributionActionType.STANDALONE_POST
            tactic = "telegram_group_post"
            mode = "STANDALONE_POST"
        else:
            context = post["text"]
            target_url = post["url"]
            action = DistributionActionType.COMMENT
            tactic = "telegram_channel_comment"
            mode = "CHANNEL_COMMENT"
        if len(context.strip()) < 12:
            runs.append({"index":idx,"handle":handle,"post_url":post["url"],
                         "status":"NO_READABLE_CONTEXT"})
            continue
        play = SimpleNamespace(tactic_id=tactic, action_type=action)
        opportunity = SimpleNamespace(
            platform=DistributionPlatform.TELEGRAM,
            title=str(row.get("title") or handle),
            metadata={
                "about": str(meta.get("about") or ""),
                "community_fit_reason": str((row.get("audience_fit") or {}).get("reason") or "USER_CONFIRMED"),
                "action_target_mode": mode,
            },
        )
        target = SelectedActionTarget(
            url=target_url, context_text=context, source="frozen_telethon_readonly"
        )
        # Fresh in-memory draft cache; only references to production state are reads.
        composer = DistributionActionComposer(
            provider=provider,
            memory_service=ProjectMarketingMemoryService(store),
            voice_profiles=VoiceProfileService(store),
            voice_cache=VoiceDraftCache(MemoryRuntimeStateStore()),
        )
        result = {
            "index":idx, "handle":handle, "post_url":post["url"],
            "surface":mode, "post_id":post["id"],
            "post_date_utc":post["date_utc"],
            "source_message_sent_to_llm": not is_group,
            "source_chars":len(context),
            "source_hash":hashlib.sha256(context.encode("utf-8")).hexdigest()[:20],
            "media_present":post.get("media_present", False),
            "media_bytes_sent_to_llm":False,
        }
        try:
            draft = await composer.compose(
                product=product, play=play, opportunity=opportunity,
                target=target, policy=None, project_id=PROJECT_ID
            )
            result.update({"status":"GENERATED",
                           "comment":draft.content_text,
                           "rationale":draft.rationale,
                           "context_text":draft.context_text[:360],
                           "content_chars":len(draft.content_text)})
        except Exception as exc:
            result.update({"status":"GENERATION_ERROR",
                           "error_type":type(exc).__name__,
                           "error":str(exc)[:200]})
        runs.append(result)
        # No retries, exactly one LLM request per eligible case.
    return {
        "status":"BENCHMARK_COMPLETE",
        "producer":"actual DistributionActionComposer + OpenAI provider",
        "model":settings.llm_model,
        "llm_provider":settings.llm_provider,
        "persisted_voice_writer_enabled":settings.persisted_voice_writer_enabled,
        "benchmark_size":len(EXPECTED),
        "cases":runs,
        "effects":{"telegram_reads":0,"telegram_writes":0,"new_discovery":0,
                   "persistent_drafts":0,"published":0},
    }

print(json.dumps(asyncio.run(run()),ensure_ascii=False,sort_keys=False))

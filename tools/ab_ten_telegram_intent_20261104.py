"""One-off, controlled, *real-provider* Partizan A/B test on 10 frozen Telegram situations.

Arms: current v16.2 composer prompt vs v16.2 plus a compact experimental
intent/voice/situation instruction. Identical product, target, model, LLM schema.
Groups have identical recent-message samples in BOTH arms (not native production
context). No Telegram calls; no actions/drafts/storage mutations; max 20
real-model calls; no retry. Scores and prior winning comments never fed to LLM.
"""
import asyncio
import base64
import hashlib
import json
import sys
from types import SimpleNamespace
from uuid import UUID

from app.action_drafting import DistributionActionComposer, SelectedActionTarget
from app.config import get_settings
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.llm import LLMMessage, get_llm_provider
from app.product_intake import product_intake_service
from app.project_marketing_memory import ProjectMarketingMemoryService
from app.runtime_store import MemoryRuntimeStateStore, get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle
from app.voice_draft_cache import VoiceDraftCache
from app.voice_profiles import VoiceProfileService

PRODUCT_ID = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
PROJECT_ID = UUID("124cdd70-49b0-48b3-aee0-a037dda4bcd0")
EXPECTED = (
    ("a_sfera", 2123), ("a_sfera", 2121),
    ("play_hard_community", 418), ("play_hard_community", 413),
    ("bizarreclubmsk", 8661), ("bizarreclubmsk", 8660),
    ("findom_gospoja0", 536536), ("findom_gospoja0", 536534),
    ("poiskrabovirabin", 2751285), ("poiskrabovirabin", 2751284),
)
MAX_PROVIDER_CALLS = 20

# Experimental instruction appended at the end of the ACTUAL v16.2 system
# prompt by a test-only provider wrapper. No production prompt is changed.
CHALLENGER = """EXPERIMENTAL WRITING PLAN (research only; no publishing):
First, understand the WHOLE social situation rather than selecting a random
small detail or turning the announcement into a lesson. Distinguish an event
invite, event calendar, thank-you/community welcome, intimate meetup format,
photo-contest announcement, one entry from a photo-contest voting stream,
and standalone conversation in a themed group.
Before writing (silently), select (i) the natural reason to participate
here and (ii) what makes this author interesting enough that readers might
be curious about her perspective. Pick an attention technique ONLY if useful:
a light teasing question, playful anticipation, a confident preference,
a simple warm compliment, a concise reaction. No compulsory cleverness.
The author voice for this adult, female-dominance-oriented audience can
be confident, warm, lightly flirtatious, inviting, playful, mischievous.
It is NOT a safety instructor, organizer, neutral reviewer, moderator, or
corporate marketer. Do not automatically write about 'no', boundaries,
rules, respect, authority or trust unless they are the actual conversation.
A group STANDALONE_POST is an organic standalone contribution to the local
social genre; recent member messages are style/audience evidence only, NOT
messages to answer. It can open a playful introduction or real exchange,
rather than lecturing the participants or evaluating their profiles.
Use specific naturally fitting language, but no invented identity,
attendance, lived experiences or claims about what an unseen photo depicts.
Where an image exists but is unavailable, be honest about what text
establishes and choose a reaction that works without seeing it.
A real person often stops after the first engaging thought. One compact
sentence, or two only if both earn their place. Avoid mini-essays,
forced observations, quoted slogans and abstract aphorisms.
Choose relevant, grounded originality over generic platitudes.
Use the exact community language. Produce ONE actual comment, no options,
no meta commentary, and no links or advertising."""
PROMPT_VERSION = "experimental_intent_situation_voice_20261104_v1"

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def frozen_posts():
    src_a = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
    src_b = json.loads(base64.b64decode(sys.argv[2]).decode("utf-8"))
    if src_a.get("status") != "READ_ONLY_COMPLETE" or src_b.get("status") != "READ_ONLY_COMPLETE":
        raise RuntimeError("FROZEN_TELETHON_SOURCES_INVALID")
    by_handle = {x["handle"]:x for x in src_a["communities"]}
    by_handle["play_hard_community"] = src_b["communities"][0]
    assert len(by_handle)==5 and set(by_handle)==set(x[0] for x in EXPECTED)
    frozen = []
    for handle, mid in EXPECTED:
        c = by_handle[handle]
        found = [p for p in c["posts"] if p["id"]==mid]
        if len(found)!=1 or not found[0].get("url","").endswith("/"+str(mid)):
            raise RuntimeError("FROZEN_TARGET_MISMATCH")
        frozen.append((c,found[0]))
    return by_handle,frozen

def registry_context(store):
    reg = TelegramCommunityRegistry(store)
    result = {}
    for row in reg.list_for_product(PRODUCT_ID):
        metadata = reg.hydrate_metadata(row)
        handle = _public_handle(metadata.get("handle") or row.get("url"))
        if handle:
            result[handle]=(row,metadata)
    return result

def context_for(record,post,meta,all_peers):
    if record["kind"] != "GROUP":
        return post["text"], post["url"], "CHANNEL_COMMENT", DistributionActionType.COMMENT, "telegram_channel_comment"
    about = str(meta.get("about") or meta.get("description") or "").strip()
    if len(about)<12:
        raise ValueError("INSUFFICIENT_GROUP_DESCRIPTION")
    # Both A and B see the SAME two real member-message excerpts so that
    # differences are attributable to the system prompt, not richer context.
    examples=[]
    for p in all_peers[record["handle"]]["posts"][:2]:
        text=str(p.get("text") or "").strip().replace("\n"," ")
        if text:
            examples.append(text[:340])
    context = ("Community background: "+about[:600]
               +"\nTwo recent member-message examples (STYLE/AUDIENCE ONLY; "
               +"your post MUST be standalone, NOT replies to them):\n"
               +"\n".join("- "+e for e in examples))
    return context, "https://t.me/"+record["handle"], "STANDALONE_POST", DistributionActionType.STANDALONE_POST, "telegram_group_post"

class ExperimentalProvider:
    def __init__(self, underlying, arm, budget):
        self.underlying=underlying
        self.arm=arm
        self.budget=budget
        self.user_hash=None
        self.base_system_hash=None
        self.real_call_made=False
    async def parse(self,messages,response_model):
        if len(messages)!=2 or messages[0].role!="system" or messages[1].role!="user":
            raise RuntimeError("EXPECTED_COMPOSER_MESSAGES")
        self.user_hash=sha(messages[1].content)
        self.base_system_hash=sha(messages[0].content)
        changed=list(messages)
        if self.arm=="challenger":
            changed[0]=LLMMessage(role="system",content=messages[0].content+"\n\n"+CHALLENGER)
        if self.budget["attempted"] >= MAX_PROVIDER_CALLS:
            raise RuntimeError("BUDGET_20_REAL_REQUESTS_EXCEEDED")
        self.budget["attempted"]+=1
        self.real_call_made=True
        return await self.underlying.parse(messages=changed,response_model=response_model)

async def main():
    settings = get_settings()
    if settings.llm_provider!="openai" or settings.persisted_voice_writer_enabled:
        raise RuntimeError("BENCHMARK_REQUIRES_CURRENT_V16_2_OPENAI_COMPOSER")
    underlying=get_llm_provider(max_output_tokens=900,max_retries=0)
    if underlying is None: raise RuntimeError("NO_REAL_PROVIDER")
    by_handle,frozen=frozen_posts()
    store=get_runtime_store()
    product=product_intake_service.get_product(PRODUCT_ID)
    if product.id!=PRODUCT_ID: raise RuntimeError("PRODUCT_SCOPE_MISMATCH")
    project=store.get("customer_acquisition_projects",str(PROJECT_ID))
    if not isinstance(project,dict) or str(project.get("product_id"))!=str(PRODUCT_ID):
        raise RuntimeError("PROJECT_SCOPE_MISMATCH")
    known=registry_context(store)
    budget={"attempted":0}
    cases=[]
    for index,(rec,post) in enumerate(frozen,1):
        handle=rec["handle"]
        row,meta=known.get(handle, ({},{}))
        context,target_url,surface,action,tactic=context_for(rec,post,meta,by_handle)
        if len(context)<12: raise RuntimeError("EMPTY_CONTEXT")
        target=SelectedActionTarget(url=target_url,context_text=context,source="frozen_telethon_benchmark")
        opportunity=SimpleNamespace(
            platform=DistributionPlatform.TELEGRAM,
            title=str(row.get("title") or handle),
            metadata={
                "about":str(meta.get("about") or ""),
                "community_fit_reason":str((row.get("audience_fit") or {}).get("reason") or "USER_CONFIRMED"),
                "action_target_mode":surface,
            })
        play=SimpleNamespace(tactic_id=tactic,action_type=action)
        arm_order=("control","challenger") if index%2 else ("challenger","control")
        arm_results={}
        for arm in arm_order:
            wrapped=ExperimentalProvider(underlying,arm,budget)
            composer=DistributionActionComposer(
                provider=wrapped,
                memory_service=ProjectMarketingMemoryService(store),
                voice_profiles=VoiceProfileService(store),
                voice_cache=VoiceDraftCache(MemoryRuntimeStateStore()),
            )
            result={"status":"PREFLIGHT_FAILED","comment":None}
            try:
                draft=await composer.compose(
                    product=product,play=play,opportunity=opportunity,
                    target=target,policy=None,project_id=PROJECT_ID
                )
                result={"status":"GENERATED","comment":draft.content_text,
                        "rationale":draft.rationale}
            except Exception as exc:
                result={"status":"ERROR","error_type":type(exc).__name__,
                        "error":str(exc)[:160]}
            result["real_provider_invoked"]=wrapped.real_call_made
            result["user_context_sha256"]=wrapped.user_hash
            result["base_system_sha256"]=wrapped.base_system_hash
            arm_results[arm]=result
        if arm_results["control"]["user_context_sha256"] != arm_results["challenger"]["user_context_sha256"]:
            raise RuntimeError("CONTEXT_DRIFT_BETWEEN_ARMS")
        if arm_results["control"]["base_system_sha256"] != arm_results["challenger"]["base_system_sha256"]:
            raise RuntimeError("COMPOSER_BASE_PROMPT_DRIFT")
        # Fixed non-identical A/B mappings; answer key stays in private artifact
        invert=(int(sha(str(post["id"]))[:2],16)%2)==1
        mapping={"A":("challenger" if invert else "control"),
                 "B":("control" if invert else "challenger")}
        blind={label:arm_results[arm]["comment"]
               if arm_results[arm]["status"]=="GENERATED" else "[GENERATION FAILED]"
               for label,arm in mapping.items()}
        cases.append({
            "number":index,"handle":handle,"post_url":post["url"],
            "post_id":post["id"],"surface":surface,
            "source_text":post["text"][:2000],
            "source_text_hash":sha(post["text"]),
            "source_media_present":bool(post.get("media_present")),
            "source_image_bytes_sent":False,
            "shared_context_size":len(context),
            "shared_context_sha256":sha(context),
            "group_context_enriched_for_both_arms": rec["kind"]=="GROUP",
            "blind":blind,"answer_key":mapping,
            "results":arm_results,
        })
    return {
        "status":"COMPLETED",
        "model":settings.llm_model,
        "provider":settings.llm_provider,
        "base_prompt":"community_message_v16_2_product_led_voice",
        "challenger_prompt":PROMPT_VERSION,
        "experiment":"One real model generation per arm x 10 frozen cases; no retries",
        "estimated_max_calls":MAX_PROVIDER_CALLS,
        "actual_provider_calls_attempted":budget["attempted"],
        "all_pairs_same_user_context":True,
        "disclaimers":[
            "No comments published.",
            "Two group member-post excerpts are added to BOTH arms in GROUP cases; not an exact production group-context replay.",
            "Media bytes not supplied: Bizarre contest photographs cannot be interpreted.",
            "Only one generation per arm; user must blind-rate before selecting a winner.",
        ],
        "cases":cases,
        "effects":{"telegram_calls":0,"telegram_writes":0,
                   "new_searches":0,"saved_drafts":0,"published":0},
    }

print(json.dumps(asyncio.run(main()),ensure_ascii=False,sort_keys=False))

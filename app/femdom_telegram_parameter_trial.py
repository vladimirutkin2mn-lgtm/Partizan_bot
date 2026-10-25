"""One-response Chat Completions sampling experiment for exact a_sfera/2117.

The normal publisher, its prompt and cached review records are NOT modified.
This test never joins, sends, approves, changes profiles or stores drafts.
If GPT-6 does not accept the requested sampling parameters, report the API
error category and stop: NEVER silently drop unsupported parameters or retry.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from app.community_message_prompt import COMMUNITY_MESSAGE_PROMPT_VERSION
from app.femdom_research_comment_preview import (
    _PRODUCT,
    _PROJECT,
    _REVIEW_NAMESPACE,
    _ReplyDraft,
    prepare_review,
)
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

_MODEL = "gpt-6-luna"
_TARGET = "https://t.me/a_sfera/2117"
_PROFILE = {
    "endpoint": "chat.completions.parse",
    "model": _MODEL,
    "temperature": 0.9,
    "presence_penalty": 0.7,
    "frequency_penalty": 0.4,
    "reasoning_effort": "none",
    "max_completion_tokens": 1000,
}
_EXPERIMENT_ID = "femdom_asfera_2117_chat_sampling_20261025_v1"


class _ReadOnlyNoCache:
    """Never return old drafts, nor save experimental drafts to production."""

    def __init__(self, store):
        self.store = store
        self.writes_attempted = 0

    def get(self, namespace, key):
        if namespace == _REVIEW_NAMESPACE:
            return None
        return self.store.get(namespace, key)

    def put(self, namespace, key, value):
        if namespace != _REVIEW_NAMESPACE:
            raise RuntimeError("Unexpected mutation namespace")
        self.writes_attempted += 1
        # Intentionally discard. Production review/cache remains unchanged.


async def run(*, store, product, api_client, project_id=_PROJECT,
              product_id=_PRODUCT, now=None) -> dict:
    if project_id != _PROJECT or product_id != _PRODUCT:
        raise ValueError("Sampling trial project/product scope mismatch")

    # Import of this module itself does not open a Telegram session.
    scratch = _ReadOnlyNoCache(store)
    api_calls = 0
    request_sha = None
    usage = {"input_tokens": 0, "output_tokens": 0}
    async def judge(system, payload):
        nonlocal api_calls, request_sha
        selected = json.loads(payload).get("selected_target") or {}
        if selected.get("url") != _TARGET or selected.get("surface") != "CHANNEL_COMMENT":
            raise ValueError("Unexpected target; zero model calls")
        if COMMUNITY_MESSAGE_PROMPT_VERSION != "community_message_v15_1_profile_curiosity_trial":
            raise ValueError("Wrong prompt version; zero model calls")
        request_sha = hashlib.sha256(
            (system + "\n" + payload).encode("utf-8")
        ).hexdigest()
        api_calls += 1
        response = await asyncio.to_thread(
            api_client.chat.completions.parse,
            model=_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": payload},
            ],
            response_format=_ReplyDraft,
            temperature=_PROFILE["temperature"],
            presence_penalty=_PROFILE["presence_penalty"],
            frequency_penalty=_PROFILE["frequency_penalty"],
            reasoning_effort=_PROFILE["reasoning_effort"],
            max_completion_tokens=_PROFILE["max_completion_tokens"],
        )
        usage_obj = getattr(response, "usage", None)
        usage["input_tokens"] = int(
            getattr(usage_obj, "prompt_tokens", 0) or 0
        )
        usage["output_tokens"] = int(
            getattr(usage_obj, "completion_tokens", 0) or 0
        )
        message = response.choices[0].message if response.choices else None
        if message is None or message.parsed is None:
            raise ValueError("CHAT_COMPLETIONS_STRUCTURED_OUTPUT_MISSING")
        return message.parsed

    review = await prepare_review(
        scratch, product=product, handle="a_sfera", judge=judge, now=now
    )
    if (
        api_calls != 1
        or review.get("target_url") != _TARGET
        or review.get("status") != "AWAITING_HUMAN_CONTEXT_AND_POLICY_REVIEW"
        or review.get("action_sendable") is not False
        or review.get("published") is not False
        or review.get("cache_hit") is not False
    ):
        raise ValueError("SAMPLING_EXPERIMENT_CONTRACT_FAILED")
    result = {
        "status": "ONE_CHAT_COMPLETIONS_SAMPLING_DRAFT_READY",
        "experiment_id": _EXPERIMENT_ID,
        "checked_at": datetime.now(UTC).isoformat(),
        "target": _TARGET,
        "prompt_version": COMMUNITY_MESSAGE_PROMPT_VERSION,
        "request_sha256": request_sha,
        "sampling_profile": dict(_PROFILE),
        "sampling_fingerprint": hashlib.sha256(
            json.dumps(_PROFILE, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "comment": review["comment"],
        "why_relevant": review.get("why_relevant"),
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "api_calls": api_calls,
        "review_cache_bypassed": True,
        "review_cache_writes": 0,
        "transient_drafts_discarded": scratch.writes_attempted,
        "telegram_sends": 0,
        "telegram_joins": 0,
        "profile_mutations": 0,
        "publish_approvals": 0,
        "publisher_modified": False,
        "published": False,
        "send_allowed": False,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="One-call parameter trial; no Telegram")
    parser.add_argument("--product-id", required=True, type=UUID)
    parser.add_argument("--project-id", required=True, type=UUID)
    args = parser.parse_args()
    if args.product_id != _PRODUCT or args.project_id != _PROJECT:
        raise ValueError("Out-of-scope sampling experiment")
    from openai import OpenAI

    from app.config import get_settings
    key = get_settings().openai_api_key
    if not key:
        raise RuntimeError("API_NOT_CONFIGURED")

    try:
        output = asyncio.run(run(
            store=get_runtime_store(),
            product=product_intake_service.get_product(_PRODUCT),
            api_client=OpenAI(api_key=key, timeout=25.0, max_retries=0),
        ))
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        # Explicit failure, no fallbacks to defaults/other models; never log
        # request payloads, SDK exception details or credentials.
        print(json.dumps({
            "status": "SAMPLING_EXPERIMENT_FAILED_NO_RETRY",
            "reason_type": type(exc).__name__,
            "experiment_id": _EXPERIMENT_ID,
            "sampling_profile": _PROFILE,
            "publisher_modified": False,
            "telegram_sends": 0,
        }, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

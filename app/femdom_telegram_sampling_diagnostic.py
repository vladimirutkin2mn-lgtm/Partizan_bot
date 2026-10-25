"""Bounded live retry + one-at-a-time GPT-6 Chat Completions diagnostic.

Same FemDom post, same prompt and structured schema in every call. At most SIX
model calls: full profile once; if that fails, unparameterized baseline once;
only if baseline succeeds, each of four options individually once. 5xx on the
baseline stops immediately. Does not modify production actions, caches, prompt,
Telegram sessions or accounts. No SDK retries.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from app.femdom_research_comment_preview import _PRODUCT, _PROJECT
from app.femdom_telegram_parameter_trial import _PROFILE, run
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

_DIAGNOSTIC = "femdom_asfera_2117_sampling_isolation_20261026"
_VARIANTS = [
    ("temperature_only", {"temperature": 0.9}),
    ("presence_penalty_only", {"presence_penalty": 0.7}),
    ("frequency_penalty_only", {"frequency_penalty": 0.4}),
    ("reasoning_none_only", {"reasoning_effort": "none"}),
]


def _error_type(exc: Exception) -> dict:
    status = getattr(exc, "status_code", None)
    code = status if isinstance(status, int) else None
    return {
        "error_type": type(exc).__name__,
        "http_status": code,
    }


async def diagnose(*, store, product, client, project_id=_PROJECT,
                   product_id=_PRODUCT, now=None) -> dict:
    if project_id != _PROJECT or product_id != _PRODUCT:
        raise ValueError("Sampling diagnosis outside pinned project/product")
    results = []
    attempts = 0

    async def check(name: str, override: dict | None):
        nonlocal attempts
        attempts += 1
        try:
            result = await run(
                store=store, product=product, api_client=client,
                project_id=project_id, product_id=product_id, now=now,
                sampling_override=override,
            )
            outcome = {
                "name": name, "status": "SUCCEEDED",
                "sampling": {
                    k: v for k, v in result["sampling_profile"].items()
                    if k in {"temperature", "presence_penalty",
                             "frequency_penalty", "reasoning_effort"}
                },
                "request_sha256": result["request_sha256"],
                "comment": result["comment"],
                "tokens_in": result["input_tokens"],
                "tokens_out": result["output_tokens"],
            }
        except Exception as exc:
            outcome = {
                "name": name, "status": "FAILED",
                "sampling": dict(_PROFILE) if override is None else dict(override),
                **_error_type(exc),
            }
        results.append(outcome)
        return outcome

    # The former live full-profile attempt returned InternalServerError.
    # Recheck ONCE with the exact same profile before attributing blame.
    full = await check("full_requested_profile_recheck", None)
    if full["status"] == "SUCCEEDED":
        status = "FULL_PROFILE_SUPPORTED_ONE_DRAFT"
    elif full.get("http_status") in (401, 403, 408, 429) or (
        full.get("error_type") in {"APITimeoutError", "APIConnectionError"}
    ):
        status = "STOPPED_AUTH_RATE_LIMIT_OR_NETWORK_FAILURE"
    else:
        base = await check("chat_structured_without_sampling", {})
        if base["status"] == "FAILED":
            status = "BASELINE_CHAT_FAILED_PARAMETERS_NOT_ISOLATED"
        else:
            for name, variant in _VARIANTS:
                await check(name, variant)
            status = "INDIVIDUAL_PARAMETERS_TESTED"
    if attempts != len(results) or attempts > 6:
        raise ValueError("Diagnostic call budget or accounting violated")
    return {
        "status": status,
        "experiment_id": _DIAGNOSTIC,
        "checked_at": datetime.now(UTC).isoformat(),
        "target": "https://t.me/a_sfera/2117",
        "prompt_version": "community_message_v15_1_profile_curiosity_trial",
        "model": _PROFILE["model"],
        "endpoint": _PROFILE["endpoint"],
        "attempt_count": attempts,
        "maximum_attempts": 6,
        "sdk_retries": 0,
        "results": results,
        "production_publisher_unchanged": True,
        "telegram_sends": 0,
        "telegram_joins": 0,
        "profile_edits": 0,
        "review_cache_writes": 0,
        "publication_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded Chat API diagnostic, no sending")
    parser.add_argument("--project-id", required=True, type=UUID)
    parser.add_argument("--product-id", required=True, type=UUID)
    args = parser.parse_args()
    if args.project_id != _PROJECT or args.product_id != _PRODUCT:
        raise ValueError("Out-of-scope Chat diagnostic")
    from openai import OpenAI

    from app.config import get_settings

    key = get_settings().openai_api_key
    if not key:
        print(json.dumps({
            "status": "API_NOT_CONFIGURED",
            "telegram_sends": 0,
        }))
        return 2
    # Prechecks remain live, but each candidate reads the SAME pinned post.
    result = asyncio.run(diagnose(
        store=get_runtime_store(),
        product=product_intake_service.get_product(_PRODUCT),
        client=OpenAI(api_key=key, timeout=25.0, max_retries=0),
    ))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

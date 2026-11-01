"""One-call Product Intake diagnostic; never writes product, project or Telegram state.

Default mode is offline. Explicit --live executes exactly one paid structured
Product Intake LLM request and FAILS CLOSED on provider/model errors rather than
silently presenting the deterministic fallback as an LLM success.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.llm import LLMProvider, get_llm_provider
from app.openai_usage import openai_task_options, openai_usage_context
from app.product_agent import ProductAnalysis, ProductIntakeAgent

# Reconstructed sanitized historical-style input; no real private invite token.
FEMDOM_BRIEF = (
    "Promoted asset: FemDom\n"
    "Asset type: Telegram channel or group\n\n"
    "Это телеграмм канал для тех, кому нравится femdom\n\n"
    "Market: any\n\n"
    "Test budget: $50\n\n"
    "Business goal: Get paying customers\n\n"
    "Product link: [private Telegram invitation withheld from this probe]"
)
MAX_OUTPUT_TOKENS = 4000


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def probe(
    *, provider: LLMProvider | None,
    live: bool,
    brief: str = FEMDOM_BRIEF,
) -> dict[str, Any]:
    """No runtime storage access. Exposes the exact schema/model request path."""
    agent = ProductIntakeAgent(provider)
    messages = agent._build_messages(brief, [], [])
    fallback = agent._fallback_analysis(brief, [])
    base: dict[str, Any] = {
        "diagnostic": "ISOLATED_PRODUCT_INTAKE_LLM",
        "input_is_reconstructed": True,
        "input_note": "Historical-style FemDom brief, private URL token withheld.",
        "input_brief": brief,
        "request_hash": _hash(json.dumps(
            [{"role": m.role, "content": m.content} for m in messages],
            ensure_ascii=False, sort_keys=True,
        )),
        "request_messages": [
            {"role": m.role, "content": m.content} for m in messages
        ],
        "structured_schema": "ProductAnalysis",
        "fallback_comparison": fallback.model_dump(mode="json"),
        "runtime_writes": 0,
        "telegram_calls": 0,
        "website_calls": 0,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if not live:
        return {**base, "status": "DRY_RUN_NO_LLM", "llm_calls_attempted": 0}

    # Never permit fallback in live mode. A failed model request is a failed
    # diagnostic, unlike the ordinary ProductIntakeAgent.analyze() path.
    if provider is None:
        return {
            **base, "status": "LLM_NOT_CONFIGURED",
            "llm_calls_attempted": 0,
        }
    usage: list[dict] = []
    try:
        with openai_usage_context("product_intake", collector=usage):
            raw: ProductAnalysis = await provider.parse(
                messages=messages, response_model=ProductAnalysis,
            )
        normalized = agent._normalize(raw, brief)
        with_questions = normalized.model_copy(update={
            "clarifications": agent._select_clarifications(
                normalized, set(), brief,
            )
        })
        interesting_fields = (
            "name", "description", "product_type", "problem_or_desire",
            "value_proposition", "usp", "use_cases", "known_audience",
            "customer_hypotheses", "assumptions", "missing_information",
        )
        missing = [
            key for key in interesting_fields
            if not getattr(raw, key)
        ]
        return {
            **base,
            "status": "LLM_PARSED",
            "llm_calls_attempted": 1,
            "llm_success": True,
            "raw_llm_analysis": raw.model_dump(mode="json"),
            "normalized_analysis": with_questions.model_dump(mode="json"),
            "changed_fields_after_normalization": [
                name for name in ProductAnalysis.model_fields
                if getattr(raw, name) != getattr(with_questions, name)
            ],
            "raw_missing_or_empty_fields": missing,
            "warning": (
                "LLM succeeded but the brief contains too little real "
                "product evidence; empty fields can be correct, not an error."
            ),
            "usage": usage,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic must report provider failure
        return {
            **base,
            "status": "LLM_FAILED_NO_FALLBACK",
            "llm_calls_attempted": 1,
            "llm_success": False,
            "error_type": type(exc).__name__,
            # Avoid printing exception text: some SDK errors echo request
            # bodies, headers or confidential source context.
            "usage": usage,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    settings_model: dict[str, Any] = {}
    provider = None
    if args.live:
        # Must use production model/provider settings; no mock substitution.
        try:
            from app.config import get_settings
            from app.llm import OpenAIResponsesProvider

            settings = get_settings()
            if settings.llm_provider != "openai":
                raise ValueError("LIVE_PROVIDER_IS_NOT_OPENAI")
            provider = get_llm_provider(
                max_output_tokens=MAX_OUTPUT_TOKENS, max_retries=0,
            )
            if not isinstance(provider, OpenAIResponsesProvider):
                raise ValueError("LIVE_PROVIDER_NOT_OPENAI_RESPONSES")
            with openai_usage_context("product_intake"):
                opts = openai_task_options(settings.llm_model)
            settings_model = {
                "provider": settings.llm_provider,
                "model": opts["model"],
                "reasoning": opts.get("reasoning"),
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "retries": 0,
            }
        except Exception as exc:  # noqa: BLE001 - fail closed
            report = {
                "diagnostic": "ISOLATED_PRODUCT_INTAKE_LLM",
                "status": "LLM_NOT_CONFIGURED",
                "error_type": type(exc).__name__,
                "llm_calls_attempted": 0,
                "runtime_writes": 0,
                "telegram_calls": 0,
            }
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 2
    result = asyncio.run(probe(provider=provider, live=args.live))
    result["model_configuration"] = settings_model
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"DRY_RUN_NO_LLM", "LLM_PARSED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

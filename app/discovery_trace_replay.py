from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.discovery_execution_context import DiscoveryExecutionContext, DiscoveryExecutionMode
from app.distribution_types import DistributionPlatform
from app.manual_discovery_trace import _telegram_normalization_diagnostic, _trace_decision


def _product_from_trace(payload: dict) -> SimpleNamespace:
    row = dict(payload.get("product") or {})
    return SimpleNamespace(
        id=row.get("id"),
        name=str(row.get("name") or ""),
        problem_or_desire=row.get("problem_or_desire"),
        value_proposition=row.get("value_proposition"),
        description=row.get("description"),
        known_audience=list(row.get("known_audience") or []),
        market=row.get("market"),
        language=row.get("language"),
    )


def _snapshot_from_trace(payload: dict) -> dict[str, set[str]]:
    row = dict(payload.get("current_negative_memory") or {})
    return {
        "blocked_keys": {str(value).casefold() for value in row.get("blocked_keys") or []},
        "negative_terms": {str(value).casefold() for value in row.get("negative_terms") or []},
    }


def _opportunity_from_trace(row: dict) -> SimpleNamespace:
    metadata = dict(row.get("metadata") or {})
    signals = row.get("research_signals")
    if isinstance(signals, dict):
        metadata["research_signals"] = dict(signals)
    return SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        canonical_key=str(row.get("canonical_key") or row.get("community_id") or ""),
        title=str(row.get("title") or ""),
        metadata=metadata,
    )


def _replay_verdict(row: dict, *, product: object, snapshot: dict[str, set[str]]) -> dict:
    opportunity = _opportunity_from_trace(row)
    decision = _trace_decision(opportunity, snapshot, product)
    legacy_accepted = bool(decision.get("accepted"))
    legacy_reason = str(decision.get("reason") or "UNKNOWN")
    metadata = opportunity.metadata
    community_fit = metadata.get("community_fit_pass") is True
    rescued = community_fit and not legacy_accepted and legacy_reason in {
        "INSUFFICIENT_SEMANTIC_EVIDENCE",
        "UNKNOWN",
    }
    verdict = "PASS" if legacy_accepted or rescued else "REJECT"
    reason = "COMMUNITY_FIRST_FIT" if rescued else legacy_reason
    return {
        "community_id": opportunity.canonical_key,
        "title": opportunity.title,
        "previous_verdict": str(row.get("verdict") or "UNKNOWN"),
        "verdict": verdict,
        "gate_reason": reason,
        "legacy_gate_reason": legacy_reason,
        "community_first_rescue": rescued,
        "signal_families": list(decision.get("signal_families") or []),
        "direct_product_anchor_count": int(decision.get("direct_product_anchor_count") or 0),
    }


def _replay_normalization(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for raw in list((payload.get("search") or {}).get("raw_hits") or []):
        if not isinstance(raw, dict):
            continue
        current = _telegram_normalization_diagnostic(str(raw.get("url") or ""))
        previous = {
            key: raw.get(key)
            for key in (
                "normalized",
                "normalization_reason",
                "host",
                "handle",
                "canonical_url",
                "access_state",
            )
            if key in raw
        }
        rows.append(
            {
                "query_id": raw.get("query_id"),
                "query": str(raw.get("query") or ""),
                "rank": raw.get("rank"),
                "url": str(raw.get("url") or ""),
                "previous": previous,
                "current": current,
                "changed": previous != current,
            }
        )
    return rows


def replay_trace(payload: dict[str, Any]) -> dict:
    context = DiscoveryExecutionContext.create(
        DiscoveryExecutionMode.REPLAY,
        (payload.get("product") or {}).get("id"),
    )
    if context.policy.allow_network or context.policy.allow_state_writes:
        raise RuntimeError("REPLAY must not allow network or working-state writes")

    product = _product_from_trace(payload)
    snapshot = _snapshot_from_trace(payload)
    replayed = [
        _replay_verdict(row, product=product, snapshot=snapshot)
        for row in list(payload.get("opportunities") or [])
        if isinstance(row, dict)
    ]
    normalization = _replay_normalization(payload)

    verdicts = Counter(row["verdict"] for row in replayed)
    verdict_changes = [
        row
        for row in replayed
        if row["previous_verdict"] not in {"", "UNKNOWN"}
        and row["previous_verdict"] != row["verdict"]
    ]
    normalization_changes = [row for row in normalization if row["changed"]]
    raw_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")

    return {
        "status": "REPLAY_COMPLETE",
        "execution_context": context.as_dict(),
        "input_fingerprint": hashlib.sha256(raw_bytes).hexdigest(),
        "effects": {
            "new_web_search_calls": 0,
            "new_llm_calls": 0,
            "new_native_telegram_calls": 0,
            "working_state_writes": 0,
        },
        "summary": {
            "opportunity_count": len(replayed),
            "verdicts": dict(sorted(verdicts.items())),
            "verdict_change_count": len(verdict_changes),
            "raw_hit_count": len(normalization),
            "normalization_change_count": len(normalization_changes),
        },
        "verdict_changes": verdict_changes,
        "normalization_changes": normalization_changes,
        "opportunities": replayed,
        "normalization": normalization,
        "limitations": [
            "Replay re-evaluates deterministic normalization and saved final relevance inputs.",
            "It cannot reconstruct native Telegram fields that were not stored in the source trace.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay a saved discovery trace without network access or state mutations."
    )
    parser.add_argument("trace_file", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = json.loads(args.trace_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Trace root must be a JSON object")
        result = replay_trace(payload)
    except (OSError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

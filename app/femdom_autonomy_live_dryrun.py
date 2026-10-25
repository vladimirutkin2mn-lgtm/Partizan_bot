"""FemDom production dry-run: native latest-post evidence and per-peer publish preflight.

This is an AUDIT, not an execution engine. Never joins groups, sends messages,
changes profiles, creates actions or records publication approvals.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.distribution_schemas import AudienceDistributionMapView
from app.distribution_types import DistributionPlatform
from app.femdom_first_publish_preflight import _HANDLES, run as review_preflight
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_governance import CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE
from app.telegram_opportunity_preflight import telegram_opportunity_preflight_service

_PROJECT_NAMESPACE = "customer_acquisition_projects"
_MAP_NAMESPACE = "audience_distribution_map"


def _fresh_native(metadata: dict, now: datetime) -> bool:
    raw = metadata.get("research_last_native_checked_at") or metadata.get("source_checked_at")
    try:
        checked = datetime.fromisoformat(str(raw))
        if checked.tzinfo is None:
            return False
        age = now - checked.astimezone(UTC)
        return timedelta(0) <= age <= timedelta(hours=24)
    except (ValueError, TypeError):
        return False


def _candidate_status(project: dict, automation: dict, metadata: dict,
                      *, account_status: str | None, now: datetime) -> dict:
    rules = metadata.get("community_commercial_rules")
    rules = rules if isinstance(rules, dict) else {}
    direct_semantic = metadata.get("semantic_audience_fit")
    direct_semantic = direct_semantic if isinstance(direct_semantic, dict) else {}
    direct = (
        metadata.get("research_audience_status") == "DIRECT_CONFIRMED"
        or (
            direct_semantic.get("verdict") == "DIRECT"
            and isinstance(direct_semantic.get("confidence"), (float, int))
            and direct_semantic["confidence"] >= 0.8
            and bool(direct_semantic.get("evidence"))
        )
    )
    project_auto = (
        (project.get("channel_preferences") or {}).get("TELEGRAM") == "AUTO"
        and (project.get("channel_publisher_modes") or {}).get("TELEGRAM") == "CLIENT_OWNED"
    )
    grant = (
        automation.get("status") == "ENABLED"
        and automation.get("allow_community_joins") is True
    )
    rules_verified = (
        rules.get("status") == "VERIFIED_ALLOWED"
        and rules.get("allows_promotion") is True
        and rules.get("allows_participation") is True
        and str(rules.get("source_url") or "").startswith("https://")
    )
    native = (
        metadata.get("native_research_status") == "VERIFIED"
        and _fresh_native(metadata, now)
    )
    reasons = []
    if not project_auto:
        reasons.append("CHANNEL_AUTOMATION_NOT_ENABLED")
    if not grant:
        reasons.append("AUTONOMOUS_JOIN_SCOPE_NOT_GRANTED")
    if not direct or metadata.get("community_fit_pass") is not True:
        reasons.append("AUDIENCE_NOT_CONFIRMED_FOR_AUTONOMOUS_EXECUTION")
    if not native:
        reasons.append("NATIVE_EVIDENCE_STALE_OR_UNVERIFIED")
    if not rules_verified:
        reasons.append("COMMUNITY_PROMOTION_RULES_NOT_VERIFIED")
    if account_status != "JOIN_REQUIRED":
        reasons.append("JOIN_NOT_REQUIRED_OR_DIFFERENT_ACCOUNT_BLOCKER")
    return {
        "would_autonomous_join_be_eligible": not reasons,
        "join_gate_blockers": reasons,
        "permission_state": account_status or "UNKNOWN",
        "native_fresh": native,
        "rules_verification": str(rules.get("status") or "MISSING"),
        "audience_direct": direct and metadata.get("community_fit_pass") is True,
        "auto_channel_enabled": project_auto,
        "community_join_scope_enabled": grant,
    }


async def run(store: RuntimeStateStore, *, project_id: UUID,
              review_runner=review_preflight,
              account_checker=None,
              now: datetime | None = None) -> dict:
    if project_id != _PROJECT:
        raise ValueError("Audit is explicitly bound to FemDom project")
    current = now or datetime.now(UTC)
    project = store.get(_PROJECT_NAMESPACE, str(_PROJECT))
    if not isinstance(project, dict) or str(project.get("product_id") or "") != str(_PRODUCT):
        raise ValueError("FemDom project is unavailable or mismatched")
    automation = store.get(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(_PROJECT)) or {}
    if not isinstance(automation, dict):
        automation = {}
    raw = store.get(_MAP_NAMESPACE, str(_PRODUCT))
    if not isinstance(raw, dict):
        raise ValueError("FemDom audience map is unavailable")
    audience = AudienceDistributionMapView.model_validate(raw)
    rows = []
    for handle in sorted(_HANDLES):
        matches = [
            opp for opp in audience.opportunities
            if opp.platform == DistributionPlatform.TELEGRAM
            and str(opp.metadata.get("handle") or "").casefold().lstrip("@") == handle
        ]
        if len(matches) != 1:
            rows.append({
                "handle": handle, "status": "MAP_MATCH_NOT_UNIQUE",
                "count": len(matches), "published": False, "joined": False,
            })
            continue
        opp = matches[0]
        review = await review_runner(store, project_id=project_id, handle=handle, now=current)
        if review.get("published") is not False or review.get("joined") is not False:
            raise RuntimeError("Preflight unexpectedly reported a mutation")
        account = review.get("account_preflight") or {}
        account_status = account.get("status")
        if not account_status:
            # A stale/missing draft is NOT evidence that Telegram cannot write.
            # Diagnose exact saved target independently, without any send/join.
            saved_url = str(opp.metadata.get("action_target_url") or "")
            saved_mode = str(opp.metadata.get("action_target_mode") or "")
            if saved_url and saved_mode in {
                "CHANNEL_COMMENT", "GROUP_REPLY", "STANDALONE_POST"
            }:
                checker = account_checker or telegram_opportunity_preflight_service.check_target
                observed = await checker(project_id, saved_url, saved_mode)
                account = observed.public_dict()
                account_status = account.get("status")
        gates = _candidate_status(
            project, automation, opp.metadata, account_status=account_status, now=current
        )
        rows.append({
            "handle": handle, "status": review.get("status"),
            "reason": review.get("reason"),
            "target_url": review.get("target_url"),
            "action_target_mode": review.get("action_target_mode"),
            "draft_text": review.get("draft_text"),
            "draft_sha256": review.get("draft_sha256"),
            "draft_prompt": review.get("prompt_version"),
            "profile_status": (review.get("profile_preflight") or {}).get("status"),
            "account_status": account_status or "NOT_CHECKED",
            "account_reason": account.get("reason"),
            "album_anchor": (
                (review.get("album_discussion_diagnostic") or {}).get(
                    "discussion_anchor_post_id"
                )
            ),
            "native_latest_target": opp.metadata.get("research_latest_post_url"),
            "native_last_checked": opp.metadata.get("research_last_native_checked_at"),
            "promotion_rules_source": (
                (opp.metadata.get("community_commercial_rules") or {}).get("source_url")
            ),
            **gates,
            "published": False, "joined": False,
        })
    return {
        "status": "READ_ONLY_FEMDOM_AUTONOMOUS_TELEGRAM_AUDIT_COMPLETE",
        "project_id": str(_PROJECT), "product_id": str(_PRODUCT),
        "checked_at": current.isoformat(),
        "automation_status": str(automation.get("status") or "NOT_AUTHORIZED"),
        "automation_join_scope": automation.get("allow_community_joins") is True,
        "telegram_publishes": 0, "telegram_joins": 0,
        "profile_mutations": 0, "distribution_actions_created": 0,
        "approval_mutations": 0, "llm_calls": 0, "paid_search_calls": 0,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only bounded FemDom Telegram audit")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--product-id", type=UUID, required=True)
    args = parser.parse_args()
    if args.project_id != _PROJECT or args.product_id != _PRODUCT:
        raise ValueError("FemDom project/product mismatch")
    result = asyncio.run(run(get_runtime_store(), project_id=args.project_id))
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

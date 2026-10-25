"""Read-only real-production audit of FemDom Telegram autonomous launch gates.

Bounded to five historically reviewed public communities. No discovery, API
generation, membership calls, profile edits, DistributionAction creation or
publishing. Existing research drafts are READ, never regenerated.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.distribution_schemas import AudienceDistributionMapView
from app.femdom_first_publish_preflight import check_profile
from app.femdom_first_publish_preflight import run as existing_readonly_preflight
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_governance import CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE
from app.telegram_opportunity_preflight import telegram_opportunity_preflight_service

HANDLES = (
    "a_sfera", "play_hard_community", "bizarreclubmsk",
    "findom_gospoja0", "poiskrabovirabin",
)
_PROJECT_NAMESPACE = "customer_acquisition_projects"
_MAP_NAMESPACE = "audience_distribution_map"


def _recent(value: object, now: datetime) -> bool:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
        return timedelta(0) <= now - stamp <= timedelta(hours=24)
    except (TypeError, ValueError, OverflowError):
        return False


def evaluate_autonomous_join_gates(
    *, mode: str, target: str, metadata: dict, project: dict,
    authorization: dict, preflight_status: str, now: datetime,
) -> dict:
    """Pure simulation of runtime gates: never implies JOIN or SEND."""
    rules = metadata.get("community_commercial_rules") or {}
    if not isinstance(rules, dict):
        rules = {}
    semantic = metadata.get("semantic_audience_fit") or {}
    if not isinstance(semantic, dict):
        semantic = {}
    try:
        semantic_confidence = float(semantic.get("confidence") or 0)
    except (TypeError, ValueError):
        semantic_confidence = 0.0
    has_audience = (
        metadata.get("research_audience_status") == "DIRECT_CONFIRMED"
        or (
            semantic.get("verdict") == "DIRECT"
            and semantic_confidence >= 0.8
            and bool(semantic.get("evidence"))
        )
    )
    policy_ok = (
        rules.get("status") == "VERIFIED_ALLOWED"
        and rules.get("allows_participation") is True
        and rules.get("allows_promotion") is True
        and str(rules.get("source_url") or "").startswith("https://")
    )
    enabled = authorization.get("status") == "ENABLED"
    opted_in = authorization.get("allow_community_joins") is True
    action_mode = str(metadata.get("action_target_mode") or "")
    target_verified = (
        action_mode == mode
        and str(metadata.get("action_target_url") or "").rstrip("/") == target.rstrip("/")
        and bool(target)
    )
    gates = {
        "telegram_selected_auto": (
            str((project.get("channel_preferences") or {}).get("TELEGRAM") or "") == "AUTO"
        ),
        "client_owned_account_mode": (
            (project.get("channel_publisher_modes") or {}).get("TELEGRAM") == "CLIENT_OWNED"
        ),
        "automation_enabled": enabled,
        "autonomous_joins_opted_in": opted_in,
        "audience_direct_confirmed": (
            metadata.get("community_fit_pass") is True and has_audience
        ),
        "native_identity_verified": (
            metadata.get("native_research_status") == "VERIFIED"
            and int(metadata.get("telegram_entity_id") or 0) > 0
        ),
        "native_evidence_recent_24h": _recent(
            metadata.get("research_last_native_checked_at")
            or metadata.get("source_checked_at"), now,
        ),
        "exact_target_matches": target_verified,
        "community_promotion_rules_verified": policy_ok,
        "publisher_reports_join_required": preflight_status == "JOIN_REQUIRED",
    }
    return {
        "gates": gates,
        "unmet_gates": [name for name, passed in gates.items() if not passed],
        "would_qualify_for_auto_join_now": all(gates.values()),
        "community_rules_source": rules.get("source_url") if policy_ok else None,
        "community_rule_status": rules.get("status", "NOT_RECORDED"),
    }


async def audit(
    store: RuntimeStateStore, *,
    project_id: UUID = _PROJECT, product_id: UUID = _PRODUCT,
    preflight=None, first_preflight=None, profile_reader=None,
    now: datetime | None = None,
) -> dict:
    if project_id != _PROJECT or product_id != _PRODUCT:
        raise ValueError("Outside bounded FemDom review project/product")
    now = now or datetime.now(UTC)
    project = store.get(_PROJECT_NAMESPACE, str(project_id))
    raw_map = store.get(_MAP_NAMESPACE, str(product_id))
    if (
        not isinstance(project, dict)
        or str(project.get("product_id")) != str(product_id)
        or not isinstance(raw_map, dict)
    ):
        raise ValueError("Expected FemDom project and audience map not found")
    mapping = AudienceDistributionMapView.model_validate(raw_map)
    auth = store.get(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(project_id)) or {}
    auth = auth if isinstance(auth, dict) else {}
    reader = profile_reader or check_profile
    profile = await reader(store, project_id)
    checker = preflight or telegram_opportunity_preflight_service
    first = first_preflight or existing_readonly_preflight
    rows = []
    for handle in HANDLES:
        matching = [
            opp for opp in mapping.opportunities
            if str(opp.metadata.get("handle") or "").strip().lstrip("@").casefold()
            == handle
        ]
        row = {
            "handle": handle, "saved_opportunity_matches": len(matching),
            "send_allowed": False, "published": False, "joined": False,
            "profile_mutated": False, "approval_recorded": False,
            "llm_calls": 0, "paid_discovery_calls": 0,
        }
        if len(matching) != 1:
            row["status"] = "SAVED_OPPORTUNITY_MISSING_OR_AMBIGUOUS"
            rows.append(row)
            continue
        meta = matching[0].metadata
        mode = str(meta.get("action_target_mode") or "").upper()
        url = str(meta.get("action_target_url") or "")
        row.update({"mode": mode, "target_url": url})
        try:
            historical = await first(
                store, project_id=project_id, handle=handle,
                preflight=checker,
                profile_checker=lambda *_: _profile_result(profile),
                now=now,
            )
            row["draft_review_status"] = historical.get("status")
            row["draft_exists"] = bool(historical.get("draft_sha256"))
            row["draft_reason"] = historical.get("reason")
            row["album_discussion_status"] = (
                historical.get("album_discussion_diagnostic") or {}
            ).get("status")
            row["album_discussion_anchor"] = (
                historical.get("album_discussion_diagnostic") or {}
            ).get("discussion_anchor_post_id")
            access = (historical.get("account_preflight") or {})
        except Exception as exc:
            access = {}
            row["review_error_type"] = type(exc).__name__
        if not access and mode in {"CHANNEL_COMMENT", "GROUP_REPLY", "STANDALONE_POST"} and url:
            try:
                result = await checker.check_target(project_id, url, mode)
                access = result.public_dict()
            except Exception as exc:
                access = {"status": "PRECHECK_FAILED", "reason": type(exc).__name__}
        status = str(access.get("status") or "NOT_CHECKED")
        row["account_preflight_status"] = status
        row["account_preflight_reason"] = access.get("reason")
        row["linked_chat_id"] = access.get("linked_chat_id")
        row["membership"] = evaluate_autonomous_join_gates(
            mode=mode, target=url, metadata=meta, project=project,
            authorization=auth, preflight_status=status, now=now,
        )
        rows.append(row)
    return {
        "status": "AUTONOMOUS_TELEGRAM_READONLY_AUDIT_COMPLETE",
        "as_of": now.isoformat(), "project_id": str(project_id),
        "product_id": str(product_id),
        "automation_enabled": auth.get("status") == "ENABLED",
        "automation_community_joins_allowed": auth.get("allow_community_joins") is True,
        "automation_daily_publish_cap": auth.get("max_publishes_per_day"),
        "profile_tracking_verified": profile.get("tracking_profile_route_verified") is True,
        "profile_status": profile.get("status"),
        "communities": rows,
        "any_auto_join_eligible": any(
            row.get("membership", {}).get("would_qualify_for_auto_join_now", False)
            for row in rows
        ),
        "messages_sent": 0, "community_joins_performed": 0,
        "new_approvals": 0, "profile_changes": 0,
        "llm_calls": 0, "paid_discovery_calls": 0,
    }


async def _profile_result(profile: dict) -> dict:
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description="FemDom autonomous no-send readiness")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--product-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        data = asyncio.run(audit(
            get_runtime_store(), project_id=args.project_id, product_id=args.product_id
        ))
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "AUDIT_FAILED", "reason_type": type(exc).__name__,
            "messages_sent": 0, "community_joins_performed": 0,
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Proof of no-send, scoped Telegram autonomous readiness gate diagnostics."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_autonomous_telegram_readonly_audit import (
    HANDLES,
    audit,
    evaluate_autonomous_join_gates,
)
from app.femdom_research_comment_preview import _PROJECT, _PRODUCT
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_governance import CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE


def _example(*, status="JOIN_REQUIRED", allow=True, rules=True, recent=True):
    now = datetime.now(UTC)
    meta = {
        "native_research_status": "VERIFIED",
        "telegram_entity_id": 321,
        "research_audience_status": "DIRECT_CONFIRMED",
        "community_fit_pass": True,
        "action_target_mode": "CHANNEL_COMMENT",
        "action_target_url": "https://t.me/bizarreclubmsk/8629",
        "source_checked_at": (
            now - (timedelta(hours=1) if recent else timedelta(days=3))
        ).isoformat(),
        "community_commercial_rules": {
            "status": "VERIFIED_ALLOWED" if rules else "UNVERIFIED",
            "allows_participation": rules,
            "allows_promotion": rules,
            "source_url": "https://t.me/bizarreclubmsk",
        },
    }
    project = {
        "channel_preferences": {"TELEGRAM": "AUTO"},
        "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
    }
    auth = {"status": "ENABLED", "allow_community_joins": allow}
    return evaluate_autonomous_join_gates(
        mode="CHANNEL_COMMENT", target=meta["action_target_url"],
        metadata=meta, project=project, authorization=auth,
        preflight_status=status, now=now,
    )


def test_only_complete_existing_authorization_and_source_evidence_qualify():
    ready = _example()
    assert ready["would_qualify_for_auto_join_now"] is True
    assert not ready["unmet_gates"]
    assert _example(status="READY")["would_qualify_for_auto_join_now"] is False
    assert "autonomous_joins_opted_in" in _example(allow=False)["unmet_gates"]
    assert "community_promotion_rules_verified" in _example(rules=False)["unmet_gates"]
    assert "native_evidence_recent_24h" in _example(recent=False)["unmet_gates"]


def test_unauthorized_stale_wrong_target_cannot_be_upgraded_by_diagnostic():
    now = datetime.now(UTC)
    row = evaluate_autonomous_join_gates(
        mode="STANDALONE_POST", target="https://t.me/anotherchat",
        metadata={
            "native_research_status": "VERIFIED",
            "telegram_entity_id": 456,
            "community_fit_pass": True,
            "research_audience_status": "DIRECT_CONFIRMED",
            "action_target_mode": "STANDALONE_POST",
            "action_target_url": "https://t.me/group",
            "source_checked_at": now.isoformat(),
        },
        project={"channel_preferences": {"TELEGRAM": "AUTO"},
                 "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"}},
        authorization={"status": "PAUSED", "allow_community_joins": True},
        preflight_status="JOIN_REQUIRED", now=now,
    )
    assert row["would_qualify_for_auto_join_now"] is False
    assert "exact_target_matches" in row["unmet_gates"]
    assert "automation_enabled" in row["unmet_gates"]
    assert "community_promotion_rules_verified" in row["unmet_gates"]


@pytest.mark.asyncio
async def test_audit_reads_real_shape_without_ever_joining_or_sending():
    store = MemoryRuntimeStateStore()
    store.put("customer_acquisition_projects", str(_PROJECT), {
        "id": str(_PROJECT), "product_id": str(_PRODUCT),
        "channel_preferences": {"TELEGRAM": "AUTO"},
        "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
    })
    store.put(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(_PROJECT), {
        "status": "ENABLED", "allow_community_joins": False,
    })
    opportunity = DistributionOpportunityView(
        id=uuid4(), icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM, kind=OpportunityKind.CHANNEL,
        canonical_key="telegram:321:community",
        title="Bizarre", url="https://t.me/bizarreclubmsk",
        metadata={
            "handle": "bizarreclubmsk",
            "action_target_mode": "CHANNEL_COMMENT",
            "action_target_url": "https://t.me/bizarreclubmsk/8629",
            "native_research_status": "VERIFIED",
            "community_fit_pass": True,
            "research_audience_status": "DIRECT_CONFIRMED",
            "telegram_entity_id": 321,
            "source_checked_at": datetime.now(UTC).isoformat(),
        },
    )
    store.put("audience_distribution_map", str(_PRODUCT),
              AudienceDistributionMapView(
                  product_id=_PRODUCT, top_icp_count=1, opportunity_count=1,
                  opportunities=[opportunity],
              ).model_dump(mode="json"))
    touched = []

    async def first(_store, *, project_id, handle, **kwargs):
        touched.append(("read_first", handle))
        return {
            "status": "BLOCKED", "reason": "ACCOUNT_PREFLIGHT_NOT_READY",
            "account_preflight": {"status": "JOIN_REQUIRED"},
            "draft_sha256": "known-saved-draft",
        }

    async def profile(_store, project_id):
        touched.append(("read_profile", project_id))
        return {"status": "PROFILE_ATTRIBUTION_UNVERIFIED",
                "tracking_profile_route_verified": False}

    class ForbiddenMutations:
        async def check_target(self, *_args):
            raise AssertionError("Would not need a duplicate provider request")

        async def join(self, *_args):
            raise AssertionError("Read-only audit must never join")

        async def send_message(self, *_args):
            raise AssertionError("Read-only audit must never publish")

    result = await audit(store, preflight=ForbiddenMutations(),
                         first_preflight=first, profile_reader=profile)
    assert result["status"] == "AUTONOMOUS_TELEGRAM_READONLY_AUDIT_COMPLETE"
    assert result["messages_sent"] == 0
    assert result["community_joins_performed"] == 0
    assert result["profile_changes"] == 0
    assert result["any_auto_join_eligible"] is False
    assert len(result["communities"]) == len(HANDLES)
    bizarre = next(row for row in result["communities"]
                   if row["handle"] == "bizarreclubmsk")
    assert bizarre["account_preflight_status"] == "JOIN_REQUIRED"
    assert bizarre["draft_exists"] is True
    assert "autonomous_joins_opted_in" in bizarre["membership"]["unmet_gates"]
    assert not any(step[0] not in {"read_first", "read_profile"} for step in touched)

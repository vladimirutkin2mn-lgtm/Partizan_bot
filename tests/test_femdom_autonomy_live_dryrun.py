"""No-write FemDom live audit checks the entire join gate without executing it."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

import app.femdom_autonomy_live_dryrun as audit
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_governance import CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightResult


def _store(*, enabled=False):
    now = datetime.now(UTC)
    store = MemoryRuntimeStateStore()
    store.put("customer_acquisition_projects", str(_PROJECT), {
        "id": str(_PROJECT), "product_id": str(_PRODUCT),
        "channel_preferences": {"TELEGRAM": "AUTO"},
        "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
    })
    store.put(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(_PROJECT), {
        "status": "ENABLED" if enabled else "NOT_AUTHORIZED",
        "allow_community_joins": enabled,
    })
    opp = DistributionOpportunityView(
        id=uuid4(), icp_id=uuid4(), title="Bizarre club",
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key="telegram:999:community", url="https://t.me/bizarreclubmsk",
        metadata={
            "handle": "bizarreclubmsk", "community_fit_pass": True,
            "research_audience_status": "DIRECT_CONFIRMED",
            "native_research_status": "VERIFIED",
            "research_last_native_checked_at": now.isoformat(),
            "research_latest_post_url": "https://t.me/bizarreclubmsk/8629",
            "action_target_url": "https://t.me/bizarreclubmsk/8629",
            "action_target_mode": "CHANNEL_COMMENT",
            "community_commercial_rules": {
                "status": "VERIFIED_ALLOWED", "allows_participation": True,
                "allows_promotion": True,
                "source_url": "https://t.me/bizarreclubmsk",
            },
        },
    )
    store.put("audience_distribution_map", str(_PRODUCT),
              AudienceDistributionMapView(
                  product_id=_PRODUCT, opportunity_count=1, top_icp_count=1,
                  opportunities=[opp],
              ).model_dump(mode="json"))
    return store, now


@pytest.mark.asyncio
async def test_dry_run_reports_join_candidate_only_when_grant_and_rules_verified(monkeypatch):
    store, now = _store(enabled=True)
    monkeypatch.setattr(audit, "_HANDLES", frozenset({"bizarreclubmsk"}))
    async def read_review(_store, **kwargs):
        return {
            "status": "BLOCKED", "reason": "CURRENT_REVIEW_DRAFT_MISSING",
            "joined": False, "published": False,
        }
    calls = []
    async def native_check(_project, url, mode):
        calls.append((url, mode))
        return TelegramOpportunityPreflightResult(
            status="JOIN_REQUIRED", action_target_mode=mode
        )
    report = await audit.run(
        store, project_id=_PROJECT, review_runner=read_review,
        account_checker=native_check, now=now,
    )
    assert report["telegram_joins"] == 0
    assert report["telegram_publishes"] == 0
    assert report["llm_calls"] == 0
    assert report["approval_mutations"] == 0
    assert report["rows"][0]["account_status"] == "JOIN_REQUIRED"
    assert report["rows"][0]["would_autonomous_join_be_eligible"] is True
    assert calls == [("https://t.me/bizarreclubmsk/8629", "CHANNEL_COMMENT")]
    assert store.list_namespace("telegram_autonomous_join_attempts_v1") == []


@pytest.mark.asyncio
async def test_dry_run_does_not_assume_old_authorization_implies_joins(monkeypatch):
    store, now = _store(enabled=False)
    monkeypatch.setattr(audit, "_HANDLES", frozenset({"bizarreclubmsk"}))
    async def read_review(_store, **kwargs):
        return {
            "status": "BLOCKED", "reason": "CURRENT_REVIEW_DRAFT_MISSING",
            "joined": False, "published": False,
            "account_preflight": {"status": "JOIN_REQUIRED"},
        }
    report = await audit.run(
        store, project_id=_PROJECT, review_runner=read_review, now=now,
    )
    assert report["rows"][0]["would_autonomous_join_be_eligible"] is False
    assert "AUTONOMOUS_JOIN_SCOPE_NOT_GRANTED" in (
        report["rows"][0]["join_gate_blockers"]
    )


def test_unknown_commercial_rules_are_not_consent_to_promote():
    store, now = _store(enabled=True)
    candidate = store.get("audience_distribution_map", str(_PRODUCT))["opportunities"][0]
    metadata = candidate["metadata"]
    metadata["community_commercial_rules"]["status"] = "UNVERIFIED"
    result = audit._candidate_status(
        {"channel_preferences": {"TELEGRAM": "AUTO"},
         "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"}},
        {"status": "ENABLED", "allow_community_joins": True},
        metadata, account_status="JOIN_REQUIRED", now=now,
    )
    assert not result["would_autonomous_join_be_eligible"]
    assert "COMMUNITY_PROMOTION_RULES_NOT_VERIFIED" in result["join_gate_blockers"]

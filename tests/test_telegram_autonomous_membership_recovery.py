"""Automatic Telegram membership is part of authorized execution, not discovery."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from telethon.tl.functions.channels import JoinChannelRequest

import app.telegram_autonomous_membership_recovery as module
from app.distribution_schemas import AudienceDistributionMapView, DistributionActionView, DistributionOpportunityView
from app.distribution_types import (
    AttributionLevel, AutomationLevel, DistributionActionStatus,
    DistributionActionType, DistributionPlatform, OpportunityKind,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_autonomous_membership_recovery import (
    AUTONOMOUS_TELEGRAM_JOIN_ATTEMPTS_NS,
    TelegramAutonomousJoinBlocked,
    TelegramAutonomousMembershipRecovery,
)
from app.telegram_comment_access import TelegramCommentAccess
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightResult


class FakeClient:
    def __init__(self, *, mode, channel_id=456, linked_id=789):
        self.mode = mode
        self.channel_id = channel_id
        self.linked_id = linked_id
        self.joined = False
        self.joins = 0
        self.disconnected = False
        self.entity = SimpleNamespace(
            id=channel_id, megagroup=mode != "CHANNEL_COMMENT",
            default_banned_rights=None,
        )
        self.linked = SimpleNamespace(id=linked_id, default_banned_rights=None)

    async def connect(self):
        pass

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return True

    async def get_entity(self, handle):
        assert handle == "@community"
        return self.entity

    async def __call__(self, request):
        assert isinstance(request, JoinChannelRequest)
        assert request.channel is (self.linked if self.mode == "CHANNEL_COMMENT"
                                   else self.entity)
        self.joins += 1
        self.joined = True
        return SimpleNamespace()


class FakePreflight:
    def __init__(self, client, *, locked=False):
        self.client = client
        self.locked = locked
        self.calls = []

    async def check_target(self, _project, target, mode):
        self.calls.append((target, mode))
        status = "NO_WRITE_ACCESS" if self.locked else (
            "READY" if self.client.joined else "JOIN_REQUIRED"
        )
        return TelegramOpportunityPreflightResult(
            status=status, handle="community",
            linked_chat_id=(self.client.linked_id if mode == "CHANNEL_COMMENT" else None),
            action_target_mode=mode,
        )

    def _client_for_project(self, _project, _handle, _id, _mode):
        return self.client, None

    @staticmethod
    async def _disconnect_safely(client):
        await client.disconnect()

    async def _group_write_status(self, client, _entity):
        return ("READY" if client.joined else "JOIN_REQUIRED"), None


def fixture(*, mode="CHANNEL_COMMENT", policy=True, native=True):
    project_id, product_id, opportunity_id = uuid4(), uuid4(), uuid4()
    kind = OpportunityKind.CHANNEL if mode == "CHANNEL_COMMENT" else OpportunityKind.GROUP
    action_type = {
        "CHANNEL_COMMENT": DistributionActionType.COMMENT,
        "GROUP_REPLY": DistributionActionType.REPLY,
        "STANDALONE_POST": DistributionActionType.STANDALONE_POST,
    }[mode]
    url = ("https://t.me/community/406" if mode == "CHANNEL_COMMENT" else
           "https://t.me/community/24" if mode == "GROUP_REPLY" else
           "https://t.me/community")
    rules = {
        "status": "VERIFIED_ALLOWED", "allows_participation": True,
        "allows_promotion": True,
        "source_url": "https://t.me/community/10",
    } if policy else None
    meta = {
        "handle": "community", "telegram_entity_id": 456,
        "native_research_status": "VERIFIED" if native else "UNVERIFIED",
        "research_audience_status": "DIRECT_CONFIRMED",
        "community_fit_pass": True,
        "action_target_mode": mode,
        "action_target_url": url,
        "community_commercial_rules": rules,
        "research_last_native_checked_at": datetime.now(UTC).isoformat(),
    }
    opp = DistributionOpportunityView(
        id=opportunity_id, icp_id=uuid4(), title="Community",
        platform=DistributionPlatform.TELEGRAM, kind=kind,
        canonical_key="telegram:456:community", url="https://t.me/community",
        metadata=meta,
    )
    store = MemoryRuntimeStateStore()
    store.put("audience_distribution_map", str(product_id), AudienceDistributionMapView(
        product_id=product_id, top_icp_count=1, opportunity_count=1,
        opportunities=[opp],
    ).model_dump(mode="json"))
    action = DistributionActionView(
        id=uuid4(), platform=DistributionPlatform.TELEGRAM,
        opportunity_id=opportunity_id,
        action_type=action_type,
        status=DistributionActionStatus.APPROVED,
        automation_level=AutomationLevel.FULL,
        attribution_level=AttributionLevel.ACTION,
        target_url=url, content_text="A relevant comment",
    )
    client = FakeClient(mode=mode)
    preflight = FakePreflight(client)
    return store, project_id, product_id, action, client, preflight


async def _fake_access(client, _entity):
    return TelegramCommentAccess(
        status="READY" if client.joined else "JOIN_REQUIRED",
        linked_chat_id=client.linked_id,
        _linked_entity=client.linked,
    )


@pytest.mark.asyncio
async def test_autonomous_comment_join_one_time_then_rechecks_provider(monkeypatch):
    monkeypatch.setattr(module, "preflight_comment_access", _fake_access)
    store, project, product, action, client, preflight = fixture()
    service = TelegramAutonomousMembershipRecovery(
        store, preflight=preflight,
        restriction_checker=lambda *_args: None,
    )
    status = await service.ensure_ready(
        project_id=project, product_id=product, action=action,
        allow_community_joins=True,
    )
    assert status == "JOINED_AND_READY"
    assert preflight.calls == [
        ("https://t.me/community/406", "CHANNEL_COMMENT"),
        ("https://t.me/community/406", "CHANNEL_COMMENT"),
    ]
    assert client.joins == 1
    assert client.disconnected
    saved = store.list_namespace(AUTONOMOUS_TELEGRAM_JOIN_ATTEMPTS_NS)
    assert len(saved) == 1 and saved[0]["status"] == "JOIN_ATTEMPTED"
    # A second action can reuse existing membership without a new join.
    assert await service.ensure_ready(
        project_id=project, product_id=product, action=action,
        allow_community_joins=True,
    ) == "READY"
    assert client.joins == 1


@pytest.mark.asyncio
async def test_autojoin_without_scope_never_connects():
    store, project, product, action, client, preflight = fixture()
    service = TelegramAutonomousMembershipRecovery(
        store, preflight=preflight, restriction_checker=lambda *_args: None,
    )
    with pytest.raises(TelegramAutonomousJoinBlocked, match="AUTONOMOUS_JOIN_NOT_AUTHORIZED"):
        await service.ensure_ready(
            project_id=project, product_id=product,
            action=action, allow_community_joins=False,
        )
    assert client.joins == 0
    assert store.list_namespace(AUTONOMOUS_TELEGRAM_JOIN_ATTEMPTS_NS) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,native", [(False, True), (True, False)])
async def test_unverified_commercial_rules_or_native_evidence_stops_join(policy, native):
    store, project, product, action, client, preflight = fixture(
        policy=policy, native=native
    )
    service = TelegramAutonomousMembershipRecovery(
        store, preflight=preflight, restriction_checker=lambda *_args: None,
    )
    with pytest.raises(TelegramAutonomousJoinBlocked):
        await service.ensure_ready(
            project_id=project, product_id=product,
            action=action, allow_community_joins=True,
        )
    assert client.joins == 0


@pytest.mark.asyncio
async def test_group_standalone_root_uses_exact_group_entity():
    store, project, product, action, client, preflight = fixture(
        mode="STANDALONE_POST",
    )
    service = TelegramAutonomousMembershipRecovery(
        store, preflight=preflight, restriction_checker=lambda *_args: None,
    )
    assert await service.ensure_ready(
        project_id=project, product_id=product,
        action=action, allow_community_joins=True,
    ) == "JOINED_AND_READY"
    assert client.joins == 1


@pytest.mark.asyncio
async def test_group_reply_uses_native_message_target_without_other_posts():
    store, project, product, action, client, preflight = fixture(mode="GROUP_REPLY")
    service = TelegramAutonomousMembershipRecovery(
        store, preflight=preflight, restriction_checker=lambda *_args: None,
    )
    assert await service.ensure_ready(
        project_id=project, product_id=product,
        action=action, allow_community_joins=True,
    ) == "JOINED_AND_READY"
    assert preflight.calls[-1][0] == "https://t.me/community/24"


@pytest.mark.asyncio
async def test_known_ban_or_restricted_target_never_joins():
    store, project, product, action, client, preflight = fixture()
    service = TelegramAutonomousMembershipRecovery(
        store, preflight=preflight,
        restriction_checker=lambda *_args: {"reason": "ACCOUNT_BANNED"},
    )
    with pytest.raises(TelegramAutonomousJoinBlocked, match="KNOWN_ACCOUNT"):
        await service.ensure_ready(
            project_id=project, product_id=product,
            action=action, allow_community_joins=True,
        )
    assert client.joins == 0


@pytest.mark.asyncio
async def test_native_linked_group_id_drift_never_joins_and_never_retries(monkeypatch):
    async def mismatched(client, _entity):
        return TelegramCommentAccess(
            status="JOIN_REQUIRED", linked_chat_id=99999,
            _linked_entity=client.linked,
        )
    monkeypatch.setattr(module, "preflight_comment_access", mismatched)
    store, project, product, action, client, preflight = fixture()
    service = TelegramAutonomousMembershipRecovery(
        store, preflight=preflight, restriction_checker=lambda *_args: None,
    )
    with pytest.raises(TelegramAutonomousJoinBlocked, match="LINKED_DISCUSSION_CHANGED"):
        await service.ensure_ready(
            project_id=project, product_id=product,
            action=action, allow_community_joins=True,
        )
    assert client.joins == 0
    with pytest.raises(TelegramAutonomousJoinBlocked, match="PREVIOUS_JOIN_ATTEMPT"):
        await service.ensure_ready(
            project_id=project, product_id=product,
            action=action, allow_community_joins=True,
        )
    assert client.joins == 0


@pytest.mark.asyncio
async def test_approved_action_required_even_with_scope():
    store, project, product, action, client, preflight = fixture()
    action = action.model_copy(update={"status": DistributionActionStatus.PREPARED})
    service = TelegramAutonomousMembershipRecovery(store, preflight=preflight)
    with pytest.raises(TelegramAutonomousJoinBlocked, match="ACTION_NOT_APPROVED"):
        await service.ensure_ready(
            project_id=project, product_id=product,
            action=action, allow_community_joins=True,
        )
    assert not preflight.calls

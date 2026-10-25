"""Membership API: opt-in exact IDs, no operator-only autojoin, no sends."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from telethon.errors import UserNotParticipantError
from telethon.tl.functions.channels import (
    GetFullChannelRequest,
    GetParticipantRequest,
    JoinChannelRequest,
)

from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_membership_control import (
    ATTEMPTS_NS,
    INTENTS_NS,
    TelegramMembershipApproveRequest,
    TelegramMembershipError,
    TelegramMembershipService,
)

PRODUCT = uuid4()
PROJECT = uuid4()
CHANNEL_ID = 234567
CHAT_ID = 1506400194
HANDLE = "bizarreclubmsk"


class FakeClient:
    def __init__(self, *, member=False, linked_id=CHAT_ID,
                 channel_id=CHANNEL_ID, fail_join=False):
        self.member = member
        self.linked_id = linked_id
        self.channel_id = channel_id
        self.fail_join = fail_join
        self.calls = []
        self.group = SimpleNamespace(
            id=linked_id, default_banned_rights=None,
        )

    async def connect(self):
        self.calls.append("connect")

    async def disconnect(self):
        self.calls.append("disconnect")

    async def is_user_authorized(self):
        return True

    async def get_entity(self, name):
        assert name == "@" + HANDLE
        return SimpleNamespace(id=self.channel_id)

    async def __call__(self, request):
        if isinstance(request, GetFullChannelRequest):
            return SimpleNamespace(
                full_chat=SimpleNamespace(linked_chat_id=self.linked_id),
                chats=[self.group],
            )
        if isinstance(request, GetParticipantRequest):
            if not self.member:
                raise UserNotParticipantError(request)
            return SimpleNamespace(participant=SimpleNamespace(banned_rights=None))
        if isinstance(request, JoinChannelRequest):
            self.calls.append("join")
            if self.fail_join:
                raise RuntimeError("Uncertain network outcome")
            self.member = True
            return SimpleNamespace()
        raise AssertionError(type(request).__name__)


def _setup(*, duplicates=False, connected=True, channel=CHANNEL_ID):
    store = MemoryRuntimeStateStore()
    store.put("customer_acquisition_projects", str(PROJECT), {
        "id": str(PROJECT), "product_id": str(PRODUCT),
        "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
    })
    if connected:
        store.put(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(PROJECT), {
            "status": "ACTIVE",
        })
    opp = DistributionOpportunityView(
        id=uuid4(), icp_id=uuid4(), platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key=f"telegram:{CHANNEL_ID}:community",
        title="Bizarre", url=f"https://t.me/{HANDLE}",
        metadata={
            "handle": HANDLE, "telegram_entity_id": channel,
            "research_audience_status": "DIRECT_CONFIRMED",
            "native_research_status": "VERIFIED",
            "publisher_permission_verified": False,
        },
    )
    opportunities = [opp]
    if duplicates:
        opportunities.append(opp.model_copy(update={"id": uuid4()}))
    store.put("audience_distribution_map", str(PRODUCT), AudienceDistributionMapView(
        product_id=PRODUCT, opportunity_count=len(opportunities), top_icp_count=1,
        opportunities=opportunities,
    ).model_dump(mode="json"))
    TelegramCommunityRegistry(store).upsert(
        product_id=PRODUCT, canonical_key=f"telegram:{CHANNEL_ID}:community",
        title="Bizarre", url=f"https://t.me/{HANDLE}",
        kind="CHANNEL", metadata={
            "handle": HANDLE, "telegram_entity_id": CHANNEL_ID,
            "community_fit_pass": True,
            "community_fit_reason": "USER_MANUALLY_CONFIRMED",
            "native_research_status": "VERIFIED",
            "source_checked_at": datetime.now(UTC).isoformat(),
        },
    )
    return store


def _service(store, client, restriction_checker=None):
    return TelegramMembershipService(
        store, client_factory=lambda _: client,
        restriction_checker=restriction_checker or (lambda _project, _handle: None),
    )


def _approval(**changes):
    return TelegramMembershipApproveRequest(
        confirm_join=True, expected_channel_id=CHANNEL_ID,
        expected_linked_chat_id=CHAT_ID, **changes
    )


@pytest.mark.asyncio
async def test_preview_is_read_only_approval_and_exact_execution_once():
    store = _setup()
    client = FakeClient()
    service = _service(store, client)
    preview = await service.preview(PROJECT, HANDLE)
    assert preview["status"] == "AWAITING_CUSTOMER_APPROVAL"
    assert preview["linked_chat_id"] == CHAT_ID
    assert preview["channel_id"] == CHANNEL_ID
    assert "join" not in client.calls
    assert store.list_namespace(ATTEMPTS_NS) == []
    intent_id = __import__("uuid").UUID(preview["id"])

    with pytest.raises(TelegramMembershipError, match="EXPLICIT_CUSTOMER_APPROVAL"):
        await service.execute(PROJECT, intent_id)
    with pytest.raises(TelegramMembershipError, match="EXPECTED_NATIVE_IDS_MISMATCH"):
        service.approve(PROJECT, intent_id, TelegramMembershipApproveRequest(
            confirm_join=True, expected_channel_id=CHANNEL_ID,
            expected_linked_chat_id=CHAT_ID + 1,
        ))
    approved = service.approve(PROJECT, intent_id, _approval())
    assert approved["status"] == "CUSTOMER_APPROVED"
    assert "join" not in client.calls
    outcome = await service.execute(PROJECT, intent_id)
    assert outcome["status"] == "JOINED_AND_READY"
    assert outcome["joined"] is True
    assert outcome["write_access"] == "READY"
    assert client.calls.count("join") == 1
    assert store.list_namespace("distribution_action") == []
    with pytest.raises(TelegramMembershipError, match="EXPLICIT_CUSTOMER_APPROVAL"):
        await service.execute(PROJECT, intent_id)


@pytest.mark.asyncio
async def test_native_chat_changed_after_approval_never_joins():
    store = _setup()
    client = FakeClient()
    service = _service(store, client)
    intent = await service.preview(PROJECT, HANDLE)
    ident = __import__("uuid").UUID(intent["id"])
    service.approve(PROJECT, ident, _approval())
    client.linked_id = CHAT_ID + 55
    client.group.id = CHAT_ID + 55
    result = await service.execute(PROJECT, ident)
    assert result["status"] == "INDETERMINATE"
    assert result["reason"] == "LINKED_DISCUSSION_CHANGED"
    assert "join" not in client.calls


@pytest.mark.asyncio
async def test_no_join_when_already_member():
    store = _setup()
    client = FakeClient(member=True)
    service = _service(store, client)
    result = await service.preview(PROJECT, HANDLE)
    assert result["status"] == "ALREADY_MEMBER"
    assert "join" not in client.calls


@pytest.mark.asyncio
async def test_uncertain_outcome_no_second_join():
    store = _setup()
    client = FakeClient(fail_join=True)
    service = _service(store, client)
    first = await service.preview(PROJECT, HANDLE)
    ident = __import__("uuid").UUID(first["id"])
    service.approve(PROJECT, ident, _approval())
    outcome = await service.execute(PROJECT, ident)
    assert outcome["status"] in ("BLOCKED", "JOINED_BUT_RESTRICTED", "INDETERMINATE")
    assert client.calls.count("join") == 1
    assert store.get(ATTEMPTS_NS, str(ident)) is not None
    with pytest.raises(TelegramMembershipError):
        await service.execute(PROJECT, ident)


@pytest.mark.asyncio
async def test_bad_audience_or_duplicate_rejected_before_session():
    for duplicates in (True, False):
        store = _setup(duplicates=duplicates)
        if not duplicates:
            row = store.get("audience_distribution_map", str(PRODUCT))
            row["opportunities"][0]["metadata"]["research_audience_status"] = "REVIEW_REQUIRED"
            store.put("audience_distribution_map", str(PRODUCT), row)
        calls = []
        def client_factory(_):
            calls.append(1)
            raise AssertionError("No account access")
        service = TelegramMembershipService(
            store, client_factory=client_factory,
            restriction_checker=lambda *_args: None,
        )
        with pytest.raises(TelegramMembershipError):
            await service.preview(PROJECT, HANDLE)
        assert calls == []


@pytest.mark.asyncio
async def test_restricted_account_and_disconnected_session_do_not_join():
    for disconnected in (False, True):
        store = _setup(connected=not disconnected)
        client = FakeClient()
        service = _service(
            store, client,
            restriction_checker=(
                (lambda *_: None) if disconnected else
                (lambda *_: {"reason": "ACCOUNT_BANNED"})
            ),
        )
        with pytest.raises(TelegramMembershipError):
            await service.preview(PROJECT, HANDLE)
        assert not client.calls


@pytest.mark.asyncio
async def test_expiry_blocks_approval_and_execution():
    store = _setup()
    client = FakeClient()
    service = _service(store, client)
    result = await service.preview(PROJECT, HANDLE)
    ident = __import__("uuid").UUID(result["id"])
    row = store.get(INTENTS_NS, str(ident))
    row["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store.put(INTENTS_NS, str(ident), row)
    with pytest.raises(TelegramMembershipError, match="JOIN_INTENT_EXPIRED"):
        service.approve(PROJECT, ident, _approval())
    assert client.calls.count("join") == 0

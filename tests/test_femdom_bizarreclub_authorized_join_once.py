"""Only the specifically authorized BizarreClub linked-group join can mutate Telegram."""
from __future__ import annotations

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
from app.femdom_bizarreclub_authorized_join_once import (
    EXPECTED_LINKED_CHAT_ID,
    HANDLE,
    OPERATION_KEY,
    _RESULT_NAMESPACE,
    join_once,
)
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE


def _store():
    store = MemoryRuntimeStateStore()
    store.put("customer_acquisition_projects", str(_PROJECT), {
        "id": str(_PROJECT), "product_id": str(_PRODUCT),
        "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
    })
    store.put(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(_PROJECT), {
        "status": "ACTIVE",
    })
    opp = DistributionOpportunityView(
        id=uuid4(), icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key="telegram:234567:community",
        title="Bizarre Club", url="https://t.me/bizarreclubmsk",
        metadata={
            "handle": HANDLE, "telegram_entity_id": 234567,
            "research_audience_status": "DIRECT_CONFIRMED",
            "native_research_status": "VERIFIED",
            "publisher_permission_verified": False,
        },
    )
    store.put("audience_distribution_map", str(_PRODUCT), AudienceDistributionMapView(
        product_id=_PRODUCT, top_icp_count=1, opportunity_count=1,
        opportunities=[opp],
    ).model_dump(mode="json"))
    return store


class FakeClient:
    def __init__(self, *, member=False, linked_id=EXPECTED_LINKED_CHAT_ID,
                 channel_id=234567, join_error=None):
        self.member = member
        self.linked_id = linked_id
        self.channel_id = channel_id
        self.join_error = join_error
        self.join_calls = 0
        self.connected = False
        self.disconnected = False
        self.linked = SimpleNamespace(
            id=linked_id, default_banned_rights=None,
        )

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return True

    async def get_entity(self, name):
        assert name == "@bizarreclubmsk"
        return SimpleNamespace(id=self.channel_id, broadcast=True)

    async def __call__(self, request):
        if isinstance(request, GetFullChannelRequest):
            return SimpleNamespace(
                full_chat=SimpleNamespace(linked_chat_id=self.linked_id),
                chats=[self.linked],
            )
        if isinstance(request, GetParticipantRequest):
            if not self.member:
                raise UserNotParticipantError(request)
            return SimpleNamespace(participant=SimpleNamespace(banned_rights=None))
        if isinstance(request, JoinChannelRequest):
            assert request.channel is self.linked
            self.join_calls += 1
            if self.join_error is not None:
                raise self.join_error
            self.member = True
            return SimpleNamespace()
        raise AssertionError("Unexpected Telegram call")


@pytest.mark.asyncio
async def test_authorized_join_exact_chat_once_then_idempotent():
    store = _store()
    fake = FakeClient()
    first = await join_once(store, client_factory=lambda: fake)
    assert first["status"] == "JOINED_AND_READY"
    assert first["joined"] is True
    assert first["write_access"] == "READY"
    assert first["messages_sent"] == 0
    assert first["profile_changes"] == 0
    assert first["publisher_approval_granted"] is False
    assert fake.join_calls == 1
    assert fake.disconnected
    saved = store.get(_RESULT_NAMESPACE, OPERATION_KEY)
    assert saved["status"] == "JOINED_AND_READY"
    second = await join_once(store, client_factory=lambda: fake)
    assert second["idempotent_replay"] is True
    assert fake.join_calls == 1
    assert store.list_namespace("distribution_action") == []


@pytest.mark.asyncio
async def test_already_member_does_not_join():
    client = FakeClient(member=True)
    result = await join_once(_store(), client_factory=lambda: client)
    assert result["status"] == "ALREADY_MEMBER"
    assert result["joined"] is False
    assert client.join_calls == 0


@pytest.mark.asyncio
async def test_linked_discussion_swap_blocks_join():
    client = FakeClient(linked_id=123)
    result = await join_once(_store(), client_factory=lambda: client)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "LINKED_CHAT_ID_CHANGED"
    assert client.join_calls == 0


@pytest.mark.asyncio
async def test_channel_entity_swap_blocks_join():
    client = FakeClient(channel_id=100)
    result = await join_once(_store(), client_factory=lambda: client)
    assert result["reason"] == "CHANNEL_ID_CHANGED"
    assert client.join_calls == 0


@pytest.mark.asyncio
async def test_wrong_publisher_or_product_never_connects():
    store = _store()
    project = store.get("customer_acquisition_projects", str(_PROJECT))
    project["channel_publisher_modes"]["TELEGRAM"] = "MANUAL"
    store.put("customer_acquisition_projects", str(_PROJECT), project)
    called = []
    def forbidden():
        called.append(1)
        raise AssertionError("No Telegram connection permitted")
    result = await join_once(store, client_factory=forbidden)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "CUSTOMER_OWNED_PUBLISHER_REQUIRED"
    assert called == []


@pytest.mark.asyncio
async def test_failed_join_never_retries_and_no_false_receipt():
    store = _store()
    client = FakeClient(join_error=RuntimeError("join not available"))
    result = await join_once(store, client_factory=lambda: client)
    assert result["status"] == "JOIN_FAILED"
    assert result["joined"] is False
    assert client.join_calls == 1
    assert store.get(_RESULT_NAMESPACE, OPERATION_KEY) is None

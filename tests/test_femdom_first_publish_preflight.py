"""No Telegram sends, joins, profile updates or new LLM drafts before approval."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_first_publish_preflight import run, verify_discussion_root
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT, _REVIEW_NAMESPACE
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightResult


def _case(*, mode="CHANNEL_COMMENT", audience="DIRECT_CONFIRMED",
          publisher="CLIENT_OWNED", draft_target=None):
    now = datetime.now(UTC)
    store = MemoryRuntimeStateStore()
    handle = "a_sfera" if mode == "CHANNEL_COMMENT" else "findom_gospoja0"
    url = ("https://t.me/a_sfera/2117" if mode == "CHANNEL_COMMENT"
           else "https://t.me/findom_gospoja0")
    kind = OpportunityKind.CHANNEL if mode == "CHANNEL_COMMENT" else OpportunityKind.GROUP
    metadata = {
        "handle": handle, "native_research_status": "VERIFIED",
        "research_audience_status": audience,
        "surface_capabilities": {"comment": "AVAILABLE", "standalone_post": "AVAILABLE"},
        "research_last_native_checked_at": now.isoformat(),
        "research_latest_post_url": url if mode == "CHANNEL_COMMENT" else None,
        "research_latest_post_id": 2117 if mode == "CHANNEL_COMMENT" else None,
        "research_latest_post_published_at": (now - timedelta(hours=1)).isoformat(),
        "research_latest_post_excerpt": (
            "Встреча в сообществе: объявлена программа мероприятия и правила участия."
        ),
        "about": "Группа для общения взрослых участников и тематических знакомств",
        "actionability": "NEEDS_PREFLIGHT",
        "action_target_url": url,
        "action_target_specific": mode == "CHANNEL_COMMENT",
        "action_target_mode": mode,
    }
    item = DistributionOpportunityView(
        id=uuid4(), icp_id=uuid4(), platform=DistributionPlatform.TELEGRAM,
        kind=kind, canonical_key="telegram:123:community", title=handle,
        url=f"https://t.me/{handle}", metadata=metadata,
    )
    store.put("audience_distribution_map", str(_PRODUCT), AudienceDistributionMapView(
        product_id=_PRODUCT, opportunity_count=1, top_icp_count=1,
        opportunities=[item],
    ).model_dump(mode="json"))
    store.put("customer_acquisition_projects", str(_PROJECT), {
        "id": str(_PROJECT), "product_id": str(_PRODUCT),
        "channel_publisher_modes": {"TELEGRAM": publisher},
    })
    post_id = 2117 if mode == "CHANNEL_COMMENT" else 0
    store.put(_REVIEW_NAMESPACE, f"{_PROJECT}:{handle}:{post_id}", {
        "status": "AWAITING_HUMAN_CONTEXT_AND_POLICY_REVIEW",
        "target_url": draft_target or url, "post_id": post_id,
        "surface": mode, "audience_status": audience,
        "publisher_permission_verified": False, "published": False,
        "comment": "Как интересно устроено знакомство участников на таких встречах?",
        "prompt_version": "community_message_v13_group_hook",
    })
    return store, handle, url, now


class FakePreflight:
    def __init__(self, status="READY"):
        self.status = status
        self.calls = []

    async def check_target(self, project_id, url, mode):
        self.calls.append((project_id, url, mode))
        return TelegramOpportunityPreflightResult(status=self.status)


async def good_profile(_store, _project_id):
    return {"tracking_profile_route_verified": True, "avatar_present": True}


async def good_discussion(_project_id, url, _checker):
    return {"status": "DISCUSSION_ROOT_VERIFIED", "target_url": url}


@pytest.mark.asyncio
async def test_channel_only_moves_to_human_review_after_all_read_only_checks():
    store, handle, url, now = _case()
    checker = FakePreflight()
    report = await run(
        store, project_id=_PROJECT, handle=handle,
        preflight=checker, profile_checker=good_profile,
        discussion_checker=good_discussion, now=now,
    )
    assert report["status"] == "TECHNICALLY_READY_FOR_HUMAN_REVIEW"
    assert report["reason"] == "COMMUNITY_RULES_AND_EXACT_TEXT_APPROVAL_REQUIRED"
    assert report["send_allowed"] is False
    assert report["published"] is False
    assert report["joined"] is False
    assert report["distribution_action_created"] is False
    assert report["customer_approval_recorded"] is False
    assert checker.calls == [(_PROJECT, url, "CHANNEL_COMMENT")]
    assert store.list_namespace("distribution_action") == []


@pytest.mark.asyncio
async def test_stale_draft_target_does_not_touch_connected_account():
    store, handle, _url, now = _case(draft_target="https://t.me/a_sfera/2100")
    checker = FakePreflight()
    result = await run(
        store, project_id=_PROJECT, handle=handle,
        preflight=checker, now=now,
    )
    assert result["reason"] == "CURRENT_DRAFT_TARGET_OR_POLICY_MISMATCH"
    assert checker.calls == []


@pytest.mark.asyncio
async def test_disallowed_publish_mode_or_research_audience_never_preflights():
    for kwargs in ({"publisher": "MANUAL"}, {"audience": "REVIEW_REQUIRED"}):
        store, handle, _, now = _case(**kwargs)
        checker = FakePreflight()
        result = await run(
            store, project_id=_PROJECT, handle=handle,
            preflight=checker, now=now,
        )
        assert result["status"] == "BLOCKED"
        assert not checker.calls


@pytest.mark.asyncio
async def test_join_required_and_unverified_attribution_stay_blocked():
    store, handle, _, now = _case(mode="STANDALONE_POST")
    checker = FakePreflight("JOIN_REQUIRED")
    result = await run(
        store, project_id=_PROJECT, handle=handle,
        preflight=checker, profile_checker=good_profile, now=now,
    )
    assert result["reason"] == "ACCOUNT_PREFLIGHT_NOT_READY"
    assert result["joined"] is False

    checker = FakePreflight()
    async def unverified(_store, _project):
        return {"tracking_profile_route_verified": False}
    result = await run(
        store, project_id=_PROJECT, handle=handle,
        preflight=checker, profile_checker=unverified, now=now,
    )
    assert result["reason"] == "ATTRIBUTION_PROFILE_ROUTE_UNVERIFIED"
    assert result["send_allowed"] is False


@pytest.mark.asyncio
async def test_unresolved_linked_discussion_never_claims_sendable():
    store, handle, _, now = _case()
    result = await run(
        store, project_id=_PROJECT, handle=handle,
        preflight=FakePreflight(), profile_checker=good_profile,
        discussion_checker=lambda _project, _url, _checker: _blocked_discussion(),
        now=now,
    )
    assert result["reason"] == "DISCUSSION_ROUTE_NOT_VERIFIED"
    assert result["send_allowed"] is False


async def _blocked_discussion():
    return {"status": "DISCUSSION_ROOT_NOT_VERIFIED"}


@pytest.mark.asyncio
async def test_real_discussion_route_checker_reads_latest_and_discussion_only():
    class FakeClient:
        def __init__(self):
            self.events = []

        async def connect(self):
            self.events.append("connect")

        async def disconnect(self):
            self.events.append("disconnect")

        async def is_user_authorized(self):
            return True

        async def get_entity(self, name):
            assert name == "@a_sfera"
            return SimpleNamespace(id=1)

        async def get_messages(self, entity, *, limit):
            assert limit == 1
            return [SimpleNamespace(id=2117)]

        async def __call__(self, request):
            assert type(request).__name__ == "GetDiscussionMessageRequest"
            return SimpleNamespace(
                chats=[SimpleNamespace(id=777)],
                messages=[SimpleNamespace(peer_id=SimpleNamespace(channel_id=777))],
            )

    fake = FakeClient()
    class Service:
        @staticmethod
        def parse_comment_target(url):
            return SimpleNamespace(handle="a_sfera", post_id=2117)
        def _client_for_project(self, *_args):
            return fake, None
        @staticmethod
        async def _disconnect_safely(client):
            await client.disconnect()

    result = await verify_discussion_root(
        _PROJECT, "https://t.me/a_sfera/2117", Service()
    )
    assert result["status"] == "DISCUSSION_ROOT_VERIFIED"
    assert fake.events == ["connect", "disconnect"]

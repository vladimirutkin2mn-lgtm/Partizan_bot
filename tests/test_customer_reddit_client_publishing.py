from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.audience_intelligence_service import audience_intelligence_service
from app.customer_account import customer_account_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.growth_balance import growth_balance_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service
from app.provider_secret_store import PROVIDER_SECRET_NAMESPACE
from app.reddit_client_publishing import (
    CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
    CustomerRedditClientPublishError,
    HttpxRedditClientPublishTransport,
    RedditClientPublishTransportError,
    RedditIdentity,
    RedditObservationResult,
    RedditPublishRequest,
    RedditPublishResult,
    RedditRemoteState,
    RedditTokenBundle,
    customer_reddit_client_publish_service,
)
from app.runtime_store import get_runtime_store


PUBLISH_CONFIRMATION = {"confirm_publish": True}


class FakeRedditClientTransport:
    def __init__(self) -> None:
        self.exchange_calls: list[dict] = []
        self.refresh_calls: list[str] = []
        self.identity_calls: list[str] = []
        self.publish_calls: list[dict] = []
        self.observe_calls: list[dict] = []
        self.publish_error: RedditClientPublishTransportError | None = None
        self.observations = [
            RedditObservationResult(
                state=RedditRemoteState.PRESENT,
                score=7,
                reply_count=3,
            )
        ]

    async def exchange_code(self, *, code: str, redirect_uri: str) -> RedditTokenBundle:
        self.exchange_calls.append({"code": code, "redirect_uri": redirect_uri})
        return RedditTokenBundle(
            access_token="customer-reddit-access-secret",
            refresh_token="customer-reddit-refresh-secret",
            scopes=["identity", "read", "submit"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def refresh(self, refresh_token: str) -> RedditTokenBundle:
        self.refresh_calls.append(refresh_token)
        return RedditTokenBundle(
            access_token="customer-reddit-refreshed-access-secret",
            refresh_token=refresh_token,
            scopes=["identity", "read", "submit"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def identity(self, access_token: str) -> RedditIdentity:
        self.identity_calls.append(access_token)
        return RedditIdentity(username="founder_reddit")

    async def publish(
        self,
        *,
        access_token: str,
        target,
        action_type,
        text: str,
    ) -> RedditPublishResult:
        self.publish_calls.append(
            {
                "access_token": access_token,
                "target": target,
                "action_type": action_type,
                "text": text,
            }
        )
        if self.publish_error is not None:
            raise self.publish_error
        suffix = "post123" if action_type.value == "STANDALONE_POST" else "comment123"
        fullname = "t3_post123" if action_type.value == "STANDALONE_POST" else "t1_comment123"
        return RedditPublishResult(
            fullname=fullname,
            url=f"https://www.reddit.com/r/relationships/comments/{suffix}/partizan_test/",
            published_at=datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
        )

    async def observe(self, *, access_token: str, fullname: str) -> RedditObservationResult:
        self.observe_calls.append({"access_token": access_token, "fullname": fullname})
        if self.observations:
            return self.observations.pop(0)
        return RedditObservationResult(state=RedditRemoteState.UNKNOWN)


@pytest.fixture(autouse=True)
def reset_state():
    settings = customer_reddit_client_publish_service._settings
    previous = {
        "provider": settings.reddit_client_publish_provider,
        "public_ready": settings.reddit_client_publish_public_ready,
        "commercial": settings.reddit_commercial_access_verified,
        "client_id": settings.reddit_client_publish_client_id,
        "client_secret": settings.reddit_client_publish_client_secret,
        "user_agent": settings.reddit_client_publish_user_agent,
        "public_base": settings.partizan_public_base_url,
        "encryption_key": settings.provider_secret_encryption_key,
    }
    previous_transport = customer_reddit_client_publish_service._transport

    settings.reddit_client_publish_provider = "unavailable"
    settings.reddit_client_publish_public_ready = False
    settings.reddit_commercial_access_verified = False
    settings.reddit_client_publish_client_id = None
    settings.reddit_client_publish_client_secret = None
    settings.reddit_client_publish_user_agent = None
    settings.partizan_public_base_url = None
    settings.provider_secret_encryption_key = None

    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    customer_reddit_client_publish_service.reset()
    get_runtime_store().clear_namespace(PROVIDER_SECRET_NAMESPACE)
    try:
        yield
    finally:
        customer_reddit_client_publish_service._transport = previous_transport
        settings.reddit_client_publish_provider = previous["provider"]
        settings.reddit_client_publish_public_ready = previous["public_ready"]
        settings.reddit_commercial_access_verified = previous["commercial"]
        settings.reddit_client_publish_client_id = previous["client_id"]
        settings.reddit_client_publish_client_secret = previous["client_secret"]
        settings.reddit_client_publish_user_agent = previous["user_agent"]
        settings.partizan_public_base_url = previous["public_base"]
        settings.provider_secret_encryption_key = previous["encryption_key"]


def _enable_client_publish(transport: FakeRedditClientTransport) -> None:
    settings = customer_reddit_client_publish_service._settings
    settings.reddit_client_publish_provider = "oauth"
    settings.reddit_client_publish_public_ready = True
    settings.reddit_commercial_access_verified = True
    settings.reddit_client_publish_client_id = "reddit-client-id"
    settings.reddit_client_publish_client_secret = SecretStr("reddit-client-secret")
    settings.reddit_client_publish_user_agent = "web:partizanlabs.com:0.8 (by /u/partizanlabs)"
    settings.partizan_public_base_url = "https://partizan.example"
    settings.provider_secret_encryption_key = SecretStr(
        Fernet.generate_key().decode("ascii")
    )
    customer_reddit_client_publish_service._transport = transport


def _registered_client(*, email: str = "reddit-client-owned@example.com") -> tuple[TestClient, object]:
    client = TestClient(app)
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    response = client.post(
        "/customer/account/register",
        json={
            "email": email,
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def _connect(client: TestClient, project_id) -> None:
    started = client.post(f"/customer/workspace/{project_id}/reddit/connection/start")
    assert started.status_code == 200
    authorization_url = started.json()["authorization_url"]
    state = parse_qs(urlsplit(authorization_url).query)["state"][0]
    completed = client.get(
        "/customer/reddit/oauth/callback",
        params={"state": state, "code": "oauth-code"},
        follow_redirects=False,
    )
    assert completed.status_code == 303
    assert "reddit=connected" in completed.headers["location"]


def _bind_project_to_product(project_id, product_id: str) -> None:
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
    assert project is not None
    project["product_id"] = product_id
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(project_id), project)


def _select_client_owned(client: TestClient, project_id) -> None:
    response = client.put(
        f"/customer/workspace/{project_id}/channels",
        json={"channels": [{"platform": "REDDIT", "publisher_mode": "CLIENT_OWNED"}]},
    )
    assert response.status_code == 200


def _product_and_action(
    *,
    action_type: str = "COMMENT",
    approve: bool = True,
) -> tuple[str, str, str]:
    client = TestClient(app)
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 200\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    distribution = client.get(f"/v1/products/{product_id}/distribution").json()
    reddit = next(item for item in distribution["opportunities"] if item["platform"] == "REDDIT")
    opportunity = audience_intelligence_service.find_opportunity(UUID(reddit["id"]))
    subreddit = str(opportunity.metadata.get("subreddit") or opportunity.title).removeprefix("r/")
    thread_url = f"https://www.reddit.com/r/{subreddit}/comments/fresh123/useful_thread/"
    metadata = dict(opportunity.metadata)
    enrichment = dict(metadata.get("enrichment", {}))
    enrichment["action_targets"] = [
        {
            "url": thread_url,
            "title": "Fresh useful thread",
            "snippet": "A current relationship question with active discussion.",
            "source": "reddit_research",
            "checked_at": datetime.now(UTC).isoformat(),
            "published_at": datetime.now(UTC).isoformat(),
            "freshness_basis": "metadata.published_at",
            "freshness_status": "FRESH",
        }
    ]
    metadata["enrichment"] = enrichment
    audience_intelligence_service.update_opportunity(
        opportunity.model_copy(update={"metadata": metadata})
    )

    now = datetime.now(UTC)
    policy = client.put(
        f"/v1/distribution-opportunities/{reddit['id']}/community-policy",
        json={
            "commercial_participation_allowed": True,
            "self_promotion_allowed": True,
            "links_allowed": True,
            "product_mentions_allowed": True,
            "standalone_posts_allowed": True,
            "comments_allowed": True,
            "disclosure_required": False,
            "special_promotion_windows": [],
            "ai_content_constraints": [],
            "evidence": [{"url": f"https://www.reddit.com/r/{subreddit}/about/rules/"}],
            "source": "manual_review",
            "research_status": "MANUAL",
            "last_checked_at": now.isoformat(),
            "fresh_until": (now + timedelta(days=7)).isoformat(),
            "confidence": 100,
        },
    )
    assert policy.status_code == 200

    identity = client.post(
        "/v1/distribution-identities",
        json={
            "platform": "REDDIT",
            "theme": "Relationship advice",
            "language": "English",
            "public_positioning": "Founder account",
            "profile_config": {},
            "allowed_opportunity_kinds": ["SUBREDDIT"],
            "allowed_actions": ["COMMENT", "REPLY", "STANDALONE_POST"],
        },
    )
    assert identity.status_code == 201
    slot = client.post(
        f"/v1/products/{product_id}/campaign-slots",
        json={
            "distribution_identity_id": identity.json()["id"],
            "status": "ACTIVE",
            "attribution_route": "https://example.com/relationships",
        },
    )
    assert slot.status_code == 201
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    tactic = {
        "COMMENT": "reddit_comment",
        "REPLY": "reddit_reply",
        "STANDALONE_POST": "reddit_value_post",
    }[action_type]
    play = next(
        item
        for item in plays.json()["plays"]
        if item["tactic_id"] == tactic
        and item["opportunity_id"] == reddit["id"]
        and item["status"] == "READY"
    )
    target_url = thread_url
    if action_type == "REPLY":
        target_url = f"{thread_url}comment456/"
    if action_type == "STANDALONE_POST":
        target_url = str(opportunity.url)
    prepare_payload = {
        "destination_url": "https://example.com/oracle",
        "target_url": target_url,
        "context_text": "Fresh useful thread with a current relationship question.",
        "content_text": "A useful value-first contribution for this community.",
    }
    if action_type == "STANDALONE_POST":
        prepare_payload["title"] = "A practical framework for relationship uncertainty"
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json=prepare_payload,
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    if approve:
        approved = client.post(f"/v1/distribution-actions/{action_id}/approve")
        assert approved.status_code == 200, approved.text
    return product_id, action_id, reddit["id"]


def _make_policy_stale(opportunity_id: str) -> None:
    client = TestClient(app)
    old = datetime.now(UTC) - timedelta(days=8)
    response = client.put(
        f"/v1/distribution-opportunities/{opportunity_id}/community-policy",
        json={
            "commercial_participation_allowed": True,
            "self_promotion_allowed": True,
            "links_allowed": True,
            "product_mentions_allowed": True,
            "standalone_posts_allowed": True,
            "comments_allowed": True,
            "source": "manual_review",
            "research_status": "MANUAL",
            "last_checked_at": old.isoformat(),
            "fresh_until": (old + timedelta(days=7)).isoformat(),
        },
    )
    assert response.status_code == 200


def _require_disclosure(opportunity_id: str) -> None:
    client = TestClient(app)
    now = datetime.now(UTC)
    response = client.put(
        f"/v1/distribution-opportunities/{opportunity_id}/community-policy",
        json={
            "commercial_participation_allowed": True,
            "self_promotion_allowed": True,
            "links_allowed": True,
            "product_mentions_allowed": True,
            "standalone_posts_allowed": True,
            "comments_allowed": True,
            "disclosure_required": True,
            "source": "manual_review",
            "research_status": "MANUAL",
            "last_checked_at": now.isoformat(),
            "fresh_until": (now + timedelta(days=7)).isoformat(),
        },
    )
    assert response.status_code == 200


def _publish(client: TestClient, project_id, action_id: str):
    return client.post(
        f"/customer/workspace/{project_id}/reddit/actions/{action_id}/publish",
        json=PUBLISH_CONFIRMATION,
    )


def test_reddit_client_owned_is_fail_closed_without_commercial_access() -> None:
    settings = customer_reddit_client_publish_service._settings
    settings.reddit_client_publish_provider = "oauth"
    settings.reddit_client_publish_public_ready = True
    settings.reddit_client_publish_client_id = "configured"
    settings.reddit_client_publish_client_secret = SecretStr("configured")
    settings.reddit_client_publish_user_agent = "partizan-test"
    settings.partizan_public_base_url = "https://partizan.example"
    settings.provider_secret_encryption_key = SecretStr(Fernet.generate_key().decode("ascii"))
    client, preview = _registered_client()

    channels = client.get(f"/customer/workspace/{preview.project_id}/channels")
    assert channels.status_code == 200
    reddit = next(item for item in channels.json() if item["platform"] == "REDDIT")
    mode = next(item for item in reddit["publisher_modes"] if item["mode"] == "CLIENT_OWNED")
    assert mode["available"] is False
    assert "commercial" in mode["blocker"].lower()

    started = client.post(f"/customer/workspace/{preview.project_id}/reddit/connection/start")
    assert started.status_code == 409
    assert "commercial" in started.json()["detail"].lower()


def test_oauth_tokens_are_encrypted_and_never_returned_to_browser() -> None:
    transport = FakeRedditClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()

    _connect(client, preview.project_id)
    connection = client.get(f"/customer/workspace/{preview.project_id}/reddit/connection")
    assert connection.status_code == 200
    assert connection.json()["status"] == "ACTIVE"
    assert connection.json()["username"] == "founder_reddit"
    for secret in (
        "customer-reddit-access-secret",
        "customer-reddit-refresh-secret",
        "reddit-client-secret",
    ):
        assert secret not in connection.text

    persisted = get_runtime_store().list_namespace(PROVIDER_SECRET_NAMESPACE)
    persisted_text = str(persisted)
    assert "customer-reddit-access-secret" not in persisted_text
    assert "customer-reddit-refresh-secret" not in persisted_text
    connection_record = get_runtime_store().get(
        CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
        str(preview.project_id),
    )
    assert connection_record is not None
    assert "access_token" not in str(connection_record)
    assert "refresh_token" not in str(connection_record)


def test_publish_requires_explicit_per_action_confirmation() -> None:
    transport = FakeRedditClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id, _ = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    blocked = client.post(
        f"/customer/workspace/{preview.project_id}/reddit/actions/{action_id}/publish",
        json={},
    )
    assert blocked.status_code == 409
    assert "confirmation" in blocked.json()["detail"].lower()
    assert transport.publish_calls == []


def test_publish_requires_approved_action_and_rechecks_policy_freshness() -> None:
    transport = FakeRedditClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id, opportunity_id = _product_and_action(approve=False)
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    not_approved = _publish(client, preview.project_id, action_id)
    assert not_approved.status_code == 409
    assert "APPROVED" in not_approved.json()["detail"]
    assert transport.publish_calls == []

    approved = TestClient(app).post(f"/v1/distribution-actions/{action_id}/approve")
    assert approved.status_code == 200, approved.text
    _make_policy_stale(opportunity_id)
    stale = _publish(client, preview.project_id, action_id)
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"].lower()
    assert transport.publish_calls == []


def test_publish_rechecks_required_disclosure_without_changing_approved_text() -> None:
    transport = FakeRedditClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id, opportunity_id = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)
    approved_text = distribution_execution_service.get_action(UUID(action_id)).content_text

    _require_disclosure(opportunity_id)
    blocked = _publish(client, preview.project_id, action_id)
    assert blocked.status_code == 409
    assert "disclosure" in blocked.json()["detail"].lower()
    assert transport.publish_calls == []
    assert distribution_execution_service.get_action(UUID(action_id)).content_text == approved_text


def test_approved_comment_publish_is_idempotent_and_records_safe_receipt() -> None:
    transport = FakeRedditClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id, _ = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    first = _publish(client, preview.project_id, action_id)
    assert first.status_code == 200
    assert first.json()["outcome"] == "EXECUTED"
    assert first.json()["external_reference"] == "reddit-client:t1_comment123"
    assert first.json()["metadata"]["remote_fullname"] == "t1_comment123"
    assert len(transport.publish_calls) == 1
    assert transport.publish_calls[0]["target"].parent_fullname == "t3_fresh123"
    assert "customer-reddit-access-secret" not in first.text

    second = _publish(client, preview.project_id, action_id)
    assert second.status_code == 200
    assert second.json()["external_reference"] == first.json()["external_reference"]
    assert len(transport.publish_calls) == 1


def test_standalone_post_uses_first_class_approved_title_and_no_vote_or_dm_surface_exists() -> None:
    transport = FakeRedditClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id, _ = _product_and_action(action_type="STANDALONE_POST")
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    action = distribution_execution_service.get_action(UUID(action_id))
    assert action.content_payload["title"].startswith("A practical framework")
    published = _publish(client, preview.project_id, action_id)
    assert published.status_code == 200
    assert published.json()["outcome"] == "EXECUTED"
    assert transport.publish_calls[0]["target"].title.startswith("A practical framework")

    forbidden = {
        "vote",
        "upvote",
        "downvote",
        "message",
        "dm",
        "subscribe",
        "join",
        "moderate",
    }
    assert forbidden.isdisjoint(set(dir(HttpxRedditClientPublishTransport)))


def test_wrong_subreddit_target_fails_before_transport() -> None:
    transport = FakeRedditClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id, _ = _product_and_action(approve=False)
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    edited = TestClient(app).patch(
        f"/v1/distribution-actions/{action_id}",
        json={
            "target_url": (
                "https://www.reddit.com/r/not_the_selected_sub/comments/fresh123/x/"
            )
        },
    )
    assert edited.status_code == 200, edited.text
    approved = TestClient(app).post(f"/v1/distribution-actions/{action_id}/approve")
    assert approved.status_code == 200, approved.text

    blocked = _publish(client, preview.project_id, action_id)
    assert blocked.status_code == 409
    assert "selected subreddit" in blocked.json()["detail"].lower()
    assert transport.publish_calls == []


def test_observation_records_score_replies_removal_and_safe_action_summary() -> None:
    transport = FakeRedditClientTransport()
    transport.observations = [
        RedditObservationResult(
            state=RedditRemoteState.PRESENT,
            score=12,
            reply_count=4,
        ),
        RedditObservationResult(
            state=RedditRemoteState.REMOVED,
            score=12,
            reply_count=4,
            restriction_signal="THING_REMOVED_OR_UNAVAILABLE",
        ),
    ]
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id, _ = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)
    assert _publish(client, preview.project_id, action_id).status_code == 200

    present = client.post(
        f"/customer/workspace/{preview.project_id}/reddit/actions/{action_id}/observe"
    )
    assert present.status_code == 200
    assert present.json()["state"] == "PRESENT"
    assert present.json()["score"] == 12
    assert present.json()["reply_count"] == 4
    action = distribution_execution_service.get_action(UUID(action_id))
    summary = action.operational_metadata["external_observations"]["reddit"]
    assert summary["state"] == "PRESENT"
    assert summary["score"] == 12

    removed = client.post(
        f"/customer/workspace/{preview.project_id}/reddit/actions/{action_id}/observe"
    )
    assert removed.status_code == 200
    assert removed.json()["state"] == "REMOVED"
    assert removed.json()["restriction_signal"] == "THING_REMOVED_OR_UNAVAILABLE"
    assert len(removed.json()["history"]) == 2
    action = distribution_execution_service.get_action(UUID(action_id))
    summary = action.operational_metadata["external_observations"]["reddit"]
    assert summary["state"] == "REMOVED"
    assert summary["restriction_signal"] == "THING_REMOVED_OR_UNAVAILABLE"
    assert "customer-reddit-access-secret" not in removed.text
    assert "customer-reddit-refresh-secret" not in removed.text
    assert "customer-reddit-access-secret" not in str(action.operational_metadata)


def test_reddit_transport_errors_remain_sanitized() -> None:
    transport = FakeRedditClientTransport()
    transport.publish_error = RedditClientPublishTransportError(
        "WRITE_FORBIDDEN",
        restriction_signal="COMMUNITY_OR_ACCOUNT_RESTRICTED",
    )
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id, _ = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    failed = _publish(client, preview.project_id, action_id)
    assert failed.status_code == 200
    assert failed.json()["outcome"] == "FAILED"
    assert failed.json()["metadata"]["error_code"] == "WRITE_FORBIDDEN"
    assert failed.json()["metadata"]["restriction_signal"] == "COMMUNITY_OR_ACCOUNT_RESTRICTED"


def test_missing_required_oauth_scopes_fail_closed() -> None:
    transport = FakeRedditClientTransport()

    async def bad_exchange(*, code: str, redirect_uri: str) -> RedditTokenBundle:
        return RedditTokenBundle(
            access_token="access",
            refresh_token="refresh",
            scopes=["identity", "read"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    transport.exchange_code = bad_exchange
    _enable_client_publish(transport)
    client, preview = _registered_client()
    started = client.post(f"/customer/workspace/{preview.project_id}/reddit/connection/start")
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    callback = client.get(
        "/customer/reddit/oauth/callback",
        params={"state": state, "code": "oauth-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert "reddit=error" in callback.headers["location"]
    assert customer_reddit_client_publish_service.is_connected(preview.project_id) is False


def test_service_rejects_cross_project_action_before_using_reddit_token() -> None:
    transport = FakeRedditClientTransport()
    _enable_client_publish(transport)
    client_a, preview_a = _registered_client()
    _connect(client_a, preview_a.project_id)
    _, action_id, _ = _product_and_action()

    _, preview_b = _registered_client(email="reddit-other@example.com")
    with pytest.raises(CustomerRedditClientPublishError, match="does not belong"):
        import asyncio

        asyncio.run(
            customer_reddit_client_publish_service.publish(
                preview_b.project_id,
                preview_b.customer_token,
                UUID(action_id),
                RedditPublishRequest(),
            )
        )
    assert transport.publish_calls == []

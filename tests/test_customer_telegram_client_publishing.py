from datetime import UTC, datetime
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
from app.runtime_store import get_runtime_store
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    CustomerTelegramClientPublishError,
    TelegramClientIdentity,
    TelegramClientPublishTransportError,
    TelegramLoginCompleteResult,
    TelegramLoginStartResult,
    TelegramPublishResult,
    customer_telegram_client_publish_service,
)

APPROVED_TARGET = "https://t.me/relationship_group"
APPROVED_CONTENT = "A useful relationship reflection for the group."


class FakeTelegramClientTransport:
    def __init__(self) -> None:
        self.begin_calls: list[str] = []
        self.complete_calls: list[dict] = []
        self.publish_calls: list[dict] = []
        self.require_password = False
        self.publish_error: TelegramClientPublishTransportError | None = None

    async def begin_login(self, phone_number: str) -> TelegramLoginStartResult:
        self.begin_calls.append(phone_number)
        return TelegramLoginStartResult(
            session="temporary-secret-session",
            phone_code_hash="secret-code-hash",
        )

    async def complete_login(
        self,
        *,
        session: str,
        phone_number: str,
        phone_code_hash: str,
        code: str,
        password: str | None,
    ) -> TelegramLoginCompleteResult:
        self.complete_calls.append(
            {
                "session": session,
                "phone_number": phone_number,
                "phone_code_hash": phone_code_hash,
                "code": code,
                "password": password,
            }
        )
        if self.require_password and password is None:
            return TelegramLoginCompleteResult(
                session="temporary-secret-session-after-code",
                password_required=True,
            )
        return TelegramLoginCompleteResult(
            session="authorised-customer-session-secret",
            identity=TelegramClientIdentity(
                user_id=777001,
                username="founder_account",
                display_name="Founder Account",
            ),
        )

    async def publish(
        self,
        *,
        session: str,
        target,
        action_type,
        text: str,
    ) -> TelegramPublishResult:
        self.publish_calls.append(
            {
                "session": session,
                "target": target,
                "action_type": action_type,
                "text": text,
            }
        )
        if self.publish_error is not None:
            raise self.publish_error
        return TelegramPublishResult(
            peer_id=123456,
            message_id=88,
            published_at=datetime(2026, 9, 8, 16, 0, tzinfo=UTC),
            executed_url="https://t.me/relationship_group/88",
        )


@pytest.fixture(autouse=True)
def reset_state():
    settings = customer_telegram_client_publish_service._settings
    previous = {
        "provider": settings.telegram_client_publish_provider,
        "public_ready": settings.telegram_client_publish_public_ready,
        "api_id": settings.telegram_client_publish_api_id,
        "api_hash": settings.telegram_client_publish_api_hash,
        "encryption_key": settings.provider_secret_encryption_key,
        "research_ready": settings.telegram_research_public_ready,
    }
    previous_transport = customer_telegram_client_publish_service._transport

    settings.telegram_client_publish_provider = "unavailable"
    settings.telegram_client_publish_public_ready = False
    settings.telegram_client_publish_api_id = None
    settings.telegram_client_publish_api_hash = None
    settings.provider_secret_encryption_key = None
    settings.telegram_research_public_ready = False

    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    customer_telegram_client_publish_service.reset()
    get_runtime_store().clear_namespace(PROVIDER_SECRET_NAMESPACE)
    try:
        yield
    finally:
        customer_telegram_client_publish_service._transport = previous_transport
        settings.telegram_client_publish_provider = previous["provider"]
        settings.telegram_client_publish_public_ready = previous["public_ready"]
        settings.telegram_client_publish_api_id = previous["api_id"]
        settings.telegram_client_publish_api_hash = previous["api_hash"]
        settings.provider_secret_encryption_key = previous["encryption_key"]
        settings.telegram_research_public_ready = previous["research_ready"]


def _enable_client_publish(transport: FakeTelegramClientTransport) -> None:
    settings = customer_telegram_client_publish_service._settings
    settings.telegram_client_publish_provider = "telethon"
    settings.telegram_client_publish_public_ready = True
    settings.telegram_client_publish_api_id = 12345
    settings.telegram_client_publish_api_hash = SecretStr("app-api-hash-secret")
    settings.provider_secret_encryption_key = SecretStr(
        Fernet.generate_key().decode("ascii")
    )
    customer_telegram_client_publish_service._transport = transport


def _registered_client() -> tuple[TestClient, object]:
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
            "email": "telegram-client-owned@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def _connect(client: TestClient, project_id) -> None:
    started = client.post(
        f"/customer/workspace/{project_id}/telegram/connection/start",
        json={"phone_number": "+15551234567"},
    )
    assert started.status_code == 200
    challenge_id = started.json()["challenge_id"]
    confirmed = client.post(
        f"/customer/workspace/{project_id}/telegram/connection/confirm",
        json={"challenge_id": challenge_id, "code": "12345"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ACTIVE"


def _product_and_action(*, approve: bool = True) -> tuple[str, str]:
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

    identity = client.post(
        "/v1/distribution-identities",
        json={
            "platform": "TELEGRAM",
            "theme": "Relationship advice",
            "language": "English",
            "public_positioning": "Founder account",
            "profile_config": {},
            "allowed_opportunity_kinds": ["GROUP"],
            "allowed_actions": ["STANDALONE_POST"],
        },
    )
    assert identity.status_code == 201
    slot = client.post(
        f"/v1/products/{product_id}/campaign-slots",
        json={
            "distribution_identity_id": identity.json()["id"],
            "status": "ACTIVE",
            "attribution_route": "https://partizan.example/relationships",
        },
    )
    assert slot.status_code == 201
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    play = next(
        item
        for item in plays.json()["plays"]
        if item["tactic_id"] == "telegram_group_post" and item["status"] == "READY"
    )
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={
            "destination_url": "https://example.com/oracle",
            "target_url": APPROVED_TARGET,
            "content_text": APPROVED_CONTENT,
        },
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    if approve:
        assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    return product_id, action_id


def _bind_project_to_product(project_id, product_id: str) -> None:
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
    assert project is not None
    project["product_id"] = product_id
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(project_id), project)


def _select_client_owned(client: TestClient, project_id) -> None:
    response = client.put(
        f"/customer/workspace/{project_id}/channels",
        json={"channels": [{"platform": "TELEGRAM", "publisher_mode": "CLIENT_OWNED"}]},
    )
    assert response.status_code == 200


def _confirmed_publish_payload() -> dict:
    return {
        "confirm_publish": True,
        "expected_target_url": APPROVED_TARGET,
        "expected_content_text": APPROVED_CONTENT,
    }


def test_client_owned_telegram_connection_is_fail_closed_by_default() -> None:
    client, preview = _registered_client()
    response = client.post(
        f"/customer/workspace/{preview.project_id}/telegram/connection/start",
        json={"phone_number": "+15551234567"},
    )
    assert response.status_code == 409
    assert "unavailable" in response.json()["detail"].lower()


def test_login_session_and_phone_are_encrypted_and_never_returned_to_browser() -> None:
    transport = FakeTelegramClientTransport()
    transport.require_password = True
    _enable_client_publish(transport)
    client, preview = _registered_client()

    started = client.post(
        f"/customer/workspace/{preview.project_id}/telegram/connection/start",
        json={"phone_number": "+15551234567"},
    )
    assert started.status_code == 200
    assert "+15551234567" not in started.text
    assert "temporary-secret-session" not in started.text
    assert "secret-code-hash" not in started.text

    persisted_secrets = get_runtime_store().list_namespace(PROVIDER_SECRET_NAMESPACE)
    assert persisted_secrets
    persisted_text = str(persisted_secrets)
    for secret in ("+15551234567", "temporary-secret-session", "secret-code-hash"):
        assert secret not in persisted_text

    challenge_id = started.json()["challenge_id"]
    password_step = client.post(
        f"/customer/workspace/{preview.project_id}/telegram/connection/confirm",
        json={"challenge_id": challenge_id, "code": "12345"},
    )
    assert password_step.status_code == 200
    assert password_step.json()["status"] == "PASSWORD_REQUIRED"
    assert "12345" not in password_step.text
    assert "temporary-secret-session-after-code" not in password_step.text

    connected = client.post(
        f"/customer/workspace/{preview.project_id}/telegram/connection/confirm",
        json={
            "challenge_id": challenge_id,
            "code": "12345",
            "password": "two-factor-secret",
        },
    )
    assert connected.status_code == 200
    assert connected.json()["status"] == "ACTIVE"
    assert connected.json()["telegram_user_id"] == 777001
    for secret in (
        "authorised-customer-session-secret",
        "app-api-hash-secret",
        "two-factor-secret",
    ):
        assert secret not in connected.text

    safe_status = client.get(
        f"/customer/workspace/{preview.project_id}/telegram/connection"
    )
    assert safe_status.status_code == 200
    assert safe_status.json()["username"] == "founder_account"
    assert "session" not in safe_status.text.lower()

    channel = next(
        item
        for item in client.get(
            f"/customer/workspace/{preview.project_id}/channels"
        ).json()
        if item["platform"] == "TELEGRAM"
    )
    assert channel["connected"] is True


def test_research_readiness_alone_still_cannot_enable_client_owned_publish() -> None:
    settings = customer_telegram_client_publish_service._settings
    settings.telegram_research_public_ready = True
    client, preview = _registered_client()

    channels = client.get(f"/customer/workspace/{preview.project_id}/channels")
    assert channels.status_code == 200
    telegram = next(item for item in channels.json() if item["platform"] == "TELEGRAM")
    modes = {item["mode"]: item for item in telegram["publisher_modes"]}
    assert modes["CLIENT_OWNED"]["available"] is False
    assert telegram["connected"] is False


def test_customer_community_action_feed_is_project_scoped_and_secret_safe() -> None:
    transport = FakeTelegramClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)

    response = client.get(f"/customer/workspace/{preview.project_id}/community-actions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["action_id"] == action_id
    assert items[0]["platform"] == "TELEGRAM"
    assert items[0]["action_status"] == "APPROVED"
    assert items[0]["target_url"] == APPROVED_TARGET
    assert items[0]["content_text"] == APPROVED_CONTENT
    assert items[0]["replies"] == 0
    assert items[0]["removals"] == 0
    for forbidden in (
        "authorised-customer-session-secret",
        "app-api-hash-secret",
        "secret_reference",
        "operational_metadata",
    ):
        assert forbidden not in response.text


def test_customer_publish_requires_explicit_confirmation() -> None:
    transport = FakeTelegramClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    response = client.post(
        f"/customer/workspace/{preview.project_id}/telegram/actions/{action_id}/publish",
        json={},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Explicit publish confirmation is required"
    assert transport.publish_calls == []


def test_customer_publish_rejects_stale_review_snapshot() -> None:
    transport = FakeTelegramClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    response = client.post(
        f"/customer/workspace/{preview.project_id}/telegram/actions/{action_id}/publish",
        json={
            "confirm_publish": True,
            "expected_target_url": APPROVED_TARGET,
            "expected_content_text": "Different reviewed content",
        },
    )
    assert response.status_code == 409
    assert "refresh and review" in response.json()["detail"]
    assert transport.publish_calls == []


def test_publish_requires_explicit_action_approval() -> None:
    transport = FakeTelegramClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id = _product_and_action(approve=False)
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    response = client.post(
        f"/customer/workspace/{preview.project_id}/telegram/actions/{action_id}/publish",
        json=_confirmed_publish_payload(),
    )
    assert response.status_code == 409
    assert "APPROVED" in response.json()["detail"]
    assert transport.publish_calls == []


def test_approved_client_owned_publish_records_remote_receipt_without_session_secret() -> None:
    transport = FakeTelegramClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    published = client.post(
        f"/customer/workspace/{preview.project_id}/telegram/actions/{action_id}/publish",
        json=_confirmed_publish_payload(),
    )
    assert published.status_code == 200
    payload = published.json()
    assert payload["outcome"] == "EXECUTED"
    assert payload["external_reference"] == "telegram-client:123456:88"
    assert payload["executed_url"] == "https://t.me/relationship_group/88"
    assert payload["metadata"]["remote_message_id"] == 88
    assert "authorised-customer-session-secret" not in published.text
    assert "app-api-hash-secret" not in published.text
    assert transport.publish_calls[0]["session"] == "authorised-customer-session-secret"

    action = distribution_execution_service.get_action(UUID(action_id))
    assert action.status.value == "EXECUTED"


def test_provider_restriction_is_recorded_and_action_stays_approved() -> None:
    transport = FakeTelegramClientTransport()
    transport.publish_error = TelegramClientPublishTransportError(
        "WRITE_FORBIDDEN",
        restriction_signal="WRITE_RESTRICTED",
    )
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)
    product_id, action_id = _product_and_action()
    _bind_project_to_product(preview.project_id, product_id)
    _select_client_owned(client, preview.project_id)

    response = client.post(
        f"/customer/workspace/{preview.project_id}/telegram/actions/{action_id}/publish",
        json=_confirmed_publish_payload(),
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "FAILED"
    assert response.json()["metadata"]["error_code"] == "WRITE_FORBIDDEN"
    assert response.json()["metadata"]["restriction_signal"] == "WRITE_RESTRICTED"
    assert distribution_execution_service.get_action(UUID(action_id)).status.value == "APPROVED"


def test_duplicate_and_frequency_guards_are_fail_closed() -> None:
    transport = FakeTelegramClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    service = customer_telegram_client_publish_service

    service._record_publish(preview.project_id, "same-fingerprint")
    with pytest.raises(CustomerTelegramClientPublishError, match="Duplicate"):
        service._enforce_publish_guard(preview.project_id, "same-fingerprint")
    with pytest.raises(CustomerTelegramClientPublishError, match="one confirmed publish per minute"):
        service._enforce_publish_guard(preview.project_id, "different-fingerprint")


def test_disconnect_deletes_authorised_session_reference() -> None:
    transport = FakeTelegramClientTransport()
    _enable_client_publish(transport)
    client, preview = _registered_client()
    _connect(client, preview.project_id)

    connection_record = get_runtime_store().get(
        CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
        str(preview.project_id),
    )
    assert connection_record is not None
    reference = str(connection_record["secret_reference"])

    disconnected = client.delete(
        f"/customer/workspace/{preview.project_id}/telegram/connection"
    )
    assert disconnected.status_code == 200
    assert disconnected.json()["status"] == "DISCONNECTED"
    assert get_runtime_store().get(PROVIDER_SECRET_NAMESPACE, reference) is None

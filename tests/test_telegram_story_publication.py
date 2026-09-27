import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import (
    TelegramStoryPublishResult,
    TelegramStoryViewsSnapshot,
)
from app.telegram_profile_conversion_pack import TelegramProfilePackStatus
from app.telegram_story_publication import (
    TelegramStoryImageInput,
    TelegramStoryPublicationApprovalRequest,
    TelegramStoryPublicationCreateRequest,
    TelegramStoryPublicationDeleteRequest,
    TelegramStoryPublicationError,
    TelegramStoryPublicationPublishRequest,
    TelegramStoryPublicationService,
    TelegramStoryPublicationStatus,
)


def _pack(status=TelegramProfilePackStatus.APPLIED):
    return SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        product_id=uuid4(),
        action_id=uuid4(),
        experiment_id=uuid4(),
        status=status,
        fingerprint="a" * 64,
    )


def _request() -> TelegramStoryPublicationCreateRequest:
    png = b"\x89PNG\r\n\x1a\n" + b"story-image"
    return TelegramStoryPublicationCreateRequest(
        caption="A useful product story",
        image=TelegramStoryImageInput(
            filename="story.png",
            mime_type="image/png",
            content_base64=base64.b64encode(png).decode("ascii"),
        ),
        noforwards=True,
    )


class FakePackService:
    def __init__(self, pack):
        self.pack = pack

    def get(self, project_id, customer_token, pack_id):
        assert project_id == self.pack.project_id
        assert customer_token == "customer-token"
        assert pack_id == self.pack.id
        return self.pack


class FakePublishService:
    def __init__(self):
        self.publish_calls = []
        self.observe_calls = []
        self.delete_calls = []
        self.views = 0
        self.forwards = 0
        self.reactions = 0
        self.story_id = 77

    async def publish_story_internal(self, project_id, **kwargs):
        self.publish_calls.append({"project_id": project_id, **kwargs})
        return TelegramStoryPublishResult(
            story_id=self.story_id,
            published_at=datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
        )

    async def story_views_internal(self, project_id, story_id):
        self.observe_calls.append((project_id, story_id))
        return TelegramStoryViewsSnapshot(
            story_id=story_id,
            views_count=self.views,
            forwards_count=self.forwards,
            reactions_count=self.reactions,
        )

    async def delete_story_internal(self, project_id, story_id):
        self.delete_calls.append((project_id, story_id))


class FakeSignalService:
    def __init__(self):
        self.attach_calls = []
        self.record_calls = []

    def attach(self, project_id, customer_token, pack_id, payload):
        self.attach_calls.append(
            {
                "project_id": project_id,
                "customer_token": customer_token,
                "pack_id": pack_id,
                "story_id": payload.story_id,
                "confirm_attach": payload.confirm_attach,
            }
        )
        return SimpleNamespace(id=uuid4())

    def record_views_internal(
        self,
        project_id,
        pack_id,
        *,
        story_id,
        cumulative_views,
        expired=False,
    ):
        self.record_calls.append(
            {
                "project_id": project_id,
                "pack_id": pack_id,
                "story_id": story_id,
                "cumulative_views": cumulative_views,
                "expired": expired,
            }
        )
        return SimpleNamespace()


def _service(pack=None):
    pack = pack or _pack()
    publish = FakePublishService()
    signals = FakeSignalService()
    service = TelegramStoryPublicationService(
        store=MemoryRuntimeStateStore(),
        pack_service=FakePackService(pack),
        publish_service=publish,
        story_signal_service=signals,
    )
    return service, publish, signals, pack


def _approved(service, pack):
    created = service.create(
        pack.project_id,
        "customer-token",
        pack.id,
        _request(),
    )
    approved = service.approve(
        pack.project_id,
        "customer-token",
        created.id,
        TelegramStoryPublicationApprovalRequest(
            confirm=True,
            expected_fingerprint=created.fingerprint,
        ),
    )
    return created, approved


def test_story_draft_has_exact_fingerprint_and_private_image_payload() -> None:
    service, _, _, pack = _service()

    created = service.create(
        pack.project_id,
        "customer-token",
        pack.id,
        _request(),
    )

    assert created.status == TelegramStoryPublicationStatus.DRAFT
    assert created.profile_pack_fingerprint == pack.fingerprint
    assert created.image.mime_type == "image/png"
    assert created.image.preview_path.endswith(f"/{created.id}/image")
    assert len(created.fingerprint) == 64

    persisted = service._store.list_namespace("telegram_story_publication")[0]
    assert persisted["image_content_base64"]
    assert "image_content_base64" not in created.model_dump()


def test_story_approval_requires_exact_reviewed_fingerprint() -> None:
    service, _, _, pack = _service()
    created = service.create(
        pack.project_id,
        "customer-token",
        pack.id,
        _request(),
    )

    with pytest.raises(TelegramStoryPublicationError, match="changed"):
        service.approve(
            pack.project_id,
            "customer-token",
            created.id,
            TelegramStoryPublicationApprovalRequest(
                confirm=True,
                expected_fingerprint="0" * 64,
            ),
        )

    approved = service.approve(
        pack.project_id,
        "customer-token",
        created.id,
        TelegramStoryPublicationApprovalRequest(
            confirm=True,
            expected_fingerprint=created.fingerprint,
        ),
    )

    assert approved.status == TelegramStoryPublicationStatus.READY
    assert approved.approved_fingerprint == approved.fingerprint


@pytest.mark.asyncio
async def test_story_publish_requires_applied_profile_pack_and_attaches_signal() -> None:
    service, publish, signals, pack = _service()
    _, approved = _approved(service, pack)

    published = await service.publish(
        pack.project_id,
        "customer-token",
        approved.id,
        TelegramStoryPublicationPublishRequest(
            confirm_publish=True,
            expected_fingerprint=approved.fingerprint,
        ),
    )

    assert published.status == TelegramStoryPublicationStatus.PUBLISHED
    assert published.story_id == 77
    assert publish.publish_calls[0]["caption"] == "A useful product story"
    assert publish.publish_calls[0]["period_seconds"] == 86400
    assert publish.publish_calls[0]["noforwards"] is True
    assert signals.attach_calls == [
        {
            "project_id": pack.project_id,
            "customer_token": "customer-token",
            "pack_id": pack.id,
            "story_id": 77,
            "confirm_attach": True,
        }
    ]


@pytest.mark.asyncio
async def test_story_publish_is_blocked_until_profile_pack_is_applied() -> None:
    pack = _pack(status=TelegramProfilePackStatus.READY)
    service, publish, _, _ = _service(pack)
    _, approved = _approved(service, pack)

    with pytest.raises(TelegramStoryPublicationError, match="Apply"):
        await service.publish(
            pack.project_id,
            "customer-token",
            approved.id,
            TelegramStoryPublicationPublishRequest(
                confirm_publish=True,
                expected_fingerprint=approved.fingerprint,
            ),
        )

    assert publish.publish_calls == []


@pytest.mark.asyncio
async def test_story_observation_updates_provider_counts_and_proxy_signal() -> None:
    service, publish, signals, pack = _service()
    _, approved = _approved(service, pack)
    published = await service.publish(
        pack.project_id,
        "customer-token",
        approved.id,
        TelegramStoryPublicationPublishRequest(
            confirm_publish=True,
            expected_fingerprint=approved.fingerprint,
        ),
    )
    publish.views = 9
    publish.forwards = 2
    publish.reactions = 3

    observed = await service.observe(
        pack.project_id,
        "customer-token",
        published.id,
    )

    assert observed.view_count == 9
    assert observed.forwards_count == 2
    assert observed.reactions_count == 3
    assert signals.record_calls[-1] == {
        "project_id": pack.project_id,
        "pack_id": pack.id,
        "story_id": 77,
        "cumulative_views": 9,
        "expired": False,
    }


@pytest.mark.asyncio
async def test_story_delete_observes_then_deletes_and_expires_proxy_signal() -> None:
    service, publish, signals, pack = _service()
    _, approved = _approved(service, pack)
    published = await service.publish(
        pack.project_id,
        "customer-token",
        approved.id,
        TelegramStoryPublicationPublishRequest(
            confirm_publish=True,
            expected_fingerprint=approved.fingerprint,
        ),
    )
    publish.views = 5

    deleted = await service.delete(
        pack.project_id,
        "customer-token",
        published.id,
        TelegramStoryPublicationDeleteRequest(confirm_delete=True),
    )

    assert deleted.status == TelegramStoryPublicationStatus.DELETED
    assert publish.delete_calls == [(pack.project_id, 77)]
    assert signals.record_calls[-1] == {
        "project_id": pack.project_id,
        "pack_id": pack.id,
        "story_id": 77,
        "cumulative_views": 5,
        "expired": True,
    }


def test_story_draft_is_invalidated_when_profile_pack_changes() -> None:
    service, _, _, pack = _service()
    created = service.create(
        pack.project_id,
        "customer-token",
        pack.id,
        _request(),
    )
    pack.fingerprint = "b" * 64

    with pytest.raises(TelegramStoryPublicationError, match="profile pack changed"):
        service.approve(
            pack.project_id,
            "customer-token",
            created.id,
            TelegramStoryPublicationApprovalRequest(
                confirm=True,
                expected_fingerprint=created.fingerprint,
            ),
        )

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.distribution_types import DistributionActionStatus, DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import TelegramProfileSnapshot
from app.telegram_profile_conversion_pack import (
    TelegramProfileAvatarInput,
    TelegramProfileConversionPackService,
    TelegramProfileCTAType,
    TelegramProfilePackApplyRequest,
    TelegramProfilePackApprovalRequest,
    TelegramProfilePackCreateRequest,
    TelegramProfilePackError,
    TelegramProfilePackRollbackRequest,
    TelegramProfilePackStatus,
    TelegramProfilePackUpdateRequest,
)


class FakeFunnelService:
    def __init__(self, product_id):
        self.product_id = product_id

    def get_project_payload(self, project_id, customer_token):
        assert customer_token == "customer-token"
        return {"id": str(project_id), "product_id": str(self.product_id)}


class FakeExecutionService:
    def __init__(self, *, action, experiment):
        self.action = action
        self.experiment = experiment

    def get_action(self, action_id):
        if action_id != self.action.id:
            raise KeyError(action_id)
        return self.action

    def get_experiment(self, experiment_id):
        if experiment_id != self.experiment.id:
            raise KeyError(experiment_id)
        return self.experiment


class FakePublishService:
    def __init__(self):
        self.profile = TelegramProfileSnapshot(
            about="Founder",
            username="founder",
            display_name="Founder",
            first_name="Founder",
            last_name="",
            avatar=None,
        )

    async def profile_internal(self, project_id):
        return self.profile


class FakeMutationService:
    def __init__(self):
        self.apply_calls = []
        self.rollback_calls = []
        self.mutation_id = uuid4()

    async def apply_internal(self, project_id, **kwargs):
        self.apply_calls.append({"project_id": project_id, **kwargs})
        return SimpleNamespace(mutation_id=self.mutation_id)

    async def rollback_internal(self, project_id, mutation_id):
        self.rollback_calls.append(
            {"project_id": project_id, "mutation_id": mutation_id}
        )
        return SimpleNamespace(mutation_id=mutation_id)


def _service():
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    project_id = uuid4()
    action_id = uuid4()
    experiment_id = uuid4()
    campaign_id = uuid4()
    action = SimpleNamespace(
        id=action_id,
        platform=DistributionPlatform.TELEGRAM,
        status=DistributionActionStatus.APPROVED,
        experiment_id=experiment_id,
        campaign_slot_id=campaign_id,
    )
    experiment = SimpleNamespace(id=experiment_id, product_id=product_id)
    mutations = FakeMutationService()
    service = TelegramProfileConversionPackService(
        store=store,
        funnel_service=FakeFunnelService(product_id),
        execution_service=FakeExecutionService(action=action, experiment=experiment),
        publish_service=FakePublishService(),
        mutation_service=mutations,
    )
    return service, mutations, project_id, action_id, experiment_id, campaign_id


def _request(action_id, *, avatar=None):
    return TelegramProfilePackCreateRequest(
        action_id=action_id,
        name="FemDom profile",
        display_name="Nikolay | FemDom",
        bio="FemDom ↓\nhttps://t.me/femdom",
        cta_type=TelegramProfileCTAType.TELEGRAM_PUBLIC_LINK,
        cta_value="https://t.me/femdom",
        avatar=avatar,
    )


def test_pack_starts_as_draft_and_fingerprint_changes_when_draft_changes() -> None:
    service, _, project_id, action_id, experiment_id, campaign_id = _service()

    created = service.create(
        project_id,
        "customer-token",
        _request(action_id),
    )

    assert created.status == TelegramProfilePackStatus.DRAFT
    assert created.experiment_id == experiment_id
    assert created.campaign_id == campaign_id
    assert created.cta_value == "https://t.me/femdom"
    assert created.avatar is None

    original_fingerprint = created.fingerprint
    updated = service.update_draft(
        project_id,
        "customer-token",
        created.id,
        TelegramProfilePackUpdateRequest(
            name="FemDom stronger profile",
            display_name=created.display_name,
            bio=created.bio,
            cta_type=created.cta_type,
            cta_value=created.cta_value,
            keep_existing_avatar=False,
            story_enabled=False,
        ),
    )

    assert updated.status == TelegramProfilePackStatus.DRAFT
    assert updated.fingerprint != original_fingerprint
    assert updated.approved_fingerprint is None


def test_approval_is_exact_fingerprint_gated() -> None:
    service, _, project_id, action_id, _, _ = _service()
    created = service.create(project_id, "customer-token", _request(action_id))

    with pytest.raises(TelegramProfilePackError, match="changed"):
        service.approve(
            project_id,
            "customer-token",
            created.id,
            TelegramProfilePackApprovalRequest(
                confirm=True,
                expected_fingerprint="0" * 64,
            ),
        )

    approved = service.approve(
        project_id,
        "customer-token",
        created.id,
        TelegramProfilePackApprovalRequest(
            confirm=True,
            expected_fingerprint=created.fingerprint,
        ),
    )

    assert approved.status == TelegramProfilePackStatus.READY
    assert approved.approved_fingerprint == approved.fingerprint


@pytest.mark.asyncio
async def test_preview_compares_live_profile_with_proposed_pack() -> None:
    service, _, project_id, action_id, _, _ = _service()
    created = service.create(project_id, "customer-token", _request(action_id))

    preview = await service.preview(project_id, "customer-token", created.id)

    assert preview.current.bio == "Founder"
    assert preview.proposed.bio == "FemDom ↓\nhttps://t.me/femdom"
    assert preview.proposed.display_name == "Nikolay | FemDom"
    assert preview.changed_fields == ["bio", "display_name"]
    assert preview.requires_confirmation is True


@pytest.mark.asyncio
async def test_approved_pack_applies_and_rolls_back_through_safe_mutation_layer() -> None:
    service, mutations, project_id, action_id, experiment_id, campaign_id = _service()
    created = service.create(project_id, "customer-token", _request(action_id))
    approved = service.approve(
        project_id,
        "customer-token",
        created.id,
        TelegramProfilePackApprovalRequest(
            confirm=True,
            expected_fingerprint=created.fingerprint,
        ),
    )

    applied = await service.apply(
        project_id,
        "customer-token",
        approved.id,
        TelegramProfilePackApplyRequest(
            confirm_apply=True,
            expected_fingerprint=approved.fingerprint,
        ),
    )

    assert applied.status == TelegramProfilePackStatus.APPLIED
    assert applied.mutation_id == mutations.mutation_id
    assert mutations.apply_calls[0]["experiment_id"] == experiment_id
    assert mutations.apply_calls[0]["campaign_id"] == campaign_id
    assert mutations.apply_calls[0]["about"] == "FemDom ↓\nhttps://t.me/femdom"
    assert mutations.apply_calls[0]["display_name"] == "Nikolay | FemDom"

    rolled_back = await service.rollback(
        project_id,
        "customer-token",
        applied.id,
        TelegramProfilePackRollbackRequest(confirm_rollback=True),
    )

    assert rolled_back.status == TelegramProfilePackStatus.ROLLED_BACK
    assert mutations.rollback_calls == [
        {"project_id": project_id, "mutation_id": mutations.mutation_id}
    ]


def test_avatar_is_stored_privately_and_exposed_via_authenticated_preview_path() -> None:
    service, _, project_id, action_id, _, _ = _service()
    png = b"\x89PNG\r\n\x1a\n" + b"profile-image"
    import base64

    created = service.create(
        project_id,
        "customer-token",
        _request(
            action_id,
            avatar=TelegramProfileAvatarInput(
                filename="femdom.png",
                mime_type="image/png",
                content_base64=base64.b64encode(png).decode("ascii"),
            ),
        ),
    )

    assert created.avatar is not None
    assert created.avatar.size_bytes == len(png)
    assert created.avatar.preview_path.endswith(f"/{created.id}/avatar")
    persisted = service._store.list_namespace("telegram_profile_conversion_pack")[0]
    assert "avatar_content_base64" in persisted
    assert "avatar_content_base64" not in created.model_dump()

    content, mime_type, filename = service.avatar_bytes(
        project_id,
        "customer-token",
        created.id,
    )
    assert content == png
    assert mime_type == "image/png"
    assert filename == "femdom.png"


def test_telegram_native_cta_requires_telegram_host() -> None:
    service, _, project_id, action_id, _, _ = _service()

    request = _request(action_id).model_copy(
        update={
            "bio": "FemDom ↓\nhttps://example.com/femdom",
            "cta_value": "https://example.com/femdom",
        }
    )
    with pytest.raises(TelegramProfilePackError, match="Telegram link"):
        service.create(project_id, "customer-token", request)

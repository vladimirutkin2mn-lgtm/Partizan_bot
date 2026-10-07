import argparse
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import customer_telegram_profile_apply_reviewed as module
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import TelegramProfilePhotoReference, TelegramProfileSnapshot
from app.telegram_profile_conversion import TelegramProfileMutationStatus


class _FakePublishService:
    def __init__(self) -> None:
        self.profile = TelegramProfileSnapshot(
            about="https://t.me/example",
            username="unchanged_user",
            display_name="Nikolay",
            first_name="Nikolay",
            last_name="",
            avatar=None,
        )

    async def profile_internal(self, project_id):
        del project_id
        return self.profile


class _FakeMutationService:
    def __init__(self, publisher: _FakePublishService) -> None:
        self.publisher = publisher
        self.apply_calls = 0

    async def apply_internal(self, project_id, **kwargs):
        del project_id
        self.apply_calls += 1
        self.publisher.profile = TelegramProfileSnapshot(
            about=kwargs["about"],
            username="unchanged_user",
            display_name=kwargs["display_name"],
            first_name=kwargs["display_name"],
            last_name="",
            avatar=TelegramProfilePhotoReference(
                photo_id=1,
                access_hash=2,
                file_reference_b64="AQ==",
            ),
        )
        return SimpleNamespace(
            mutation_id=uuid4(),
            status=TelegramProfileMutationStatus.APPLIED,
            before_snapshot_id=uuid4(),
            after_snapshot_id=uuid4(),
        )

    async def rollback_internal(self, project_id, mutation_id):
        raise AssertionError(f"unexpected rollback: {project_id} {mutation_id}")


def _write_config(tmp_path, avatar: bytes) -> tuple[object, str]:
    avatar_path = tmp_path / "avatar.b64"
    avatar_path.write_text(base64.b64encode(avatar).decode("ascii"), encoding="ascii")
    avatar_sha = hashlib.sha256(avatar).hexdigest()
    config = {
        "schema_version": 1,
        "operation_id": "reviewed-profile-v1",
        "proposal_id": "proposal-v1",
        "authorization_scope": "PROFILE_ONLY",
        "display_name": "Nika",
        "about": "То, что не пишу в комментариях ↓\nhttps://t.me/example",
        "native_destination": "https://t.me/example",
        "avatar_b64_file": str(avatar_path),
        "avatar_filename": "avatar.jpg",
        "avatar_sha256": avatar_sha,
        "story_publish_authorized": False,
        "community_publish_authorized": False,
        "community_publish_authorization_phrase": "разрешаю отправку",
    }
    config_path = tmp_path / "profile.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return config_path, avatar_sha


@pytest.mark.asyncio
async def test_reviewed_profile_apply_is_profile_only_reversible_and_idempotent(
    tmp_path, monkeypatch
) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(product_id)},
    )
    config_path, avatar_sha = _write_config(tmp_path, b"approved-avatar-bytes")
    publisher = _FakePublishService()
    mutations = _FakeMutationService(publisher)

    monkeypatch.setattr(module, "get_runtime_store", lambda: store)
    monkeypatch.setattr(module, "customer_telegram_client_publish_service", publisher)
    monkeypatch.setattr(module, "telegram_profile_conversion_service", mutations)
    monkeypatch.setattr(
        module.product_intake_service,
        "get_product",
        lambda resolved_product_id: SimpleNamespace(
            id=resolved_product_id,
            name="FemDom — reviewed project",
        ),
    )
    args = argparse.Namespace(
        project_id=project_id,
        expected_product_id=product_id,
        expected_product_name="FemDom",
        profile_config=config_path,
        confirm=module.CONFIRMATION,
    )

    first = await module.run(args)
    second = await module.run(args)

    assert first["status"] == "APPLIED"
    assert first["display_name"] == "Nika"
    assert first["username_before"] == "unchanged_user"
    assert first["username_after"] == "unchanged_user"
    assert first["avatar_sha256"] == avatar_sha
    assert first["story_published"] is False
    assert first["community_content_published"] is False
    assert second["status"] == "ALREADY_APPLIED"
    assert mutations.apply_calls == 1


@pytest.mark.asyncio
async def test_reviewed_profile_apply_rejects_publish_authorization(tmp_path) -> None:
    config_path, _ = _write_config(tmp_path, b"approved-avatar-bytes")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["community_publish_authorized"] = True
    config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="cannot authorize community publication"):
        module._load_config(config_path)


def test_committed_femdom_avatar_payload_matches_reviewed_hash() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config_path = repository_root / "ops/femdom/telegram_profile_v2.json"
    config = module._load_config(config_path)

    avatar = module._avatar_bytes(config, config_path=config_path)

    assert len(avatar) == 13701
    assert avatar.startswith(b"\xff\xd8\xff")
    assert avatar.endswith(b"\xff\xd9")
    assert hashlib.sha256(avatar).hexdigest() == (
        "bcddb2d77ac84beb8eefc19491254c4b38e0effd0d101426ad590100e9dcafe2"
    )
    assert config["community_publish_authorization_phrase"] == "разрешаю отправку"
    assert config["community_publish_authorized"] is False
    assert config["story_publish_authorized"] is False

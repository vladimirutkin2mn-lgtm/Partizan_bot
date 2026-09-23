import pytest

from app.customer_telegram_profile_preview import _proposed_about
from app.telegram_profile_inspection import (
    CustomerTelegramProfileInspectionService,
    TelegramProfileInspectionError,
    TelegramProfileSnapshot,
)


class FakeStore:
    def __init__(self, connection: dict | None) -> None:
        self.connection = connection

    def get(self, namespace: str, key: str):
        return self.connection


class FakeSecretStore:
    def get(self, reference: str) -> str | None:
        return "session-value" if reference == "session-ref" else None


class FakeTransport:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.sessions: list[str] = []

    async def read_profile(self, *, session: str) -> TelegramProfileSnapshot:
        self.sessions.append(session)
        return TelegramProfileSnapshot(
            user_id=self.user_id,
            username="femdom_profile",
            first_name="FemDom",
            about="Existing public bio",
        )


@pytest.mark.asyncio
async def test_profile_inspection_reads_only_connected_identity() -> None:
    project_id = __import__("uuid").uuid4()
    transport = FakeTransport(user_id=123)
    service = CustomerTelegramProfileInspectionService(
        store=FakeStore(
            {
                "status": "ACTIVE",
                "secret_reference": "session-ref",
                "telegram_user_id": 123,
            }
        ),
        secret_store=FakeSecretStore(),
        transport=transport,
    )

    snapshot = await service.inspect_internal(project_id)

    assert snapshot.about == "Existing public bio"
    assert snapshot.username == "femdom_profile"
    assert transport.sessions == ["session-value"]


@pytest.mark.asyncio
async def test_profile_inspection_rejects_identity_mismatch() -> None:
    project_id = __import__("uuid").uuid4()
    service = CustomerTelegramProfileInspectionService(
        store=FakeStore(
            {
                "status": "ACTIVE",
                "secret_reference": "session-ref",
                "telegram_user_id": 123,
            }
        ),
        secret_store=FakeSecretStore(),
        transport=FakeTransport(user_id=999),
    )

    with pytest.raises(TelegramProfileInspectionError, match="does not match"):
        await service.inspect_internal(project_id)


def test_proposed_profile_bio_uses_stable_route_and_stays_compact() -> None:
    route = "https://partizanlabs.com/p/7a729985b6ac4e16b44fc675"

    proposal = _proposed_about("FemDom", route)

    assert proposal == f"FemDom → {route}"
    assert len(proposal) <= 70

from types import SimpleNamespace
from uuid import uuid4

from app.creative_assets import CreativeReadinessStatus
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.execution_adapters import AdapterExecutionOutcome, MetaAdsExecutionAdapter


class FakeSecretResolver:
    def resolve(self, name: str) -> str | None:
        assert name == "META_TEST_TOKEN"
        return "secret-token"


class FakeConnection:
    def __init__(self, default_image_url=None) -> None:
        self.status = SimpleNamespace(value="ACTIVE")
        self.default_image_url = default_image_url
        self.access_token_env = "META_TEST_TOKEN"
        self.test_days = 5
        self.budget_minor_unit_factor = 100
        self.country_codes = ["US"]
        self.api_version = "v99.0"

    def model_copy(self, *, update: dict):
        clone = FakeConnection(self.default_image_url)
        clone.__dict__.update(self.__dict__)
        clone.__dict__.update(update)
        return clone


class FakeConnectionService:
    def __init__(self, connection) -> None:
        self.connection = connection

    def get_meta(self, product_id):
        return self.connection


class FakeSpecService:
    def get(self, action_id):
        return SimpleNamespace(
            platform=DistributionPlatform.INSTAGRAM,
            launch_mode=SimpleNamespace(value="CREATE_PAUSED"),
            tactic_id="instagram_ads",
            budget_cap=50.0,
            creative_brief={
                "product_name": "Partizan",
                "message_hook": "Find customers",
                "value_proposition": "Autonomous acquisition",
            },
            destination_url="https://example.com/landing",
        )


class FakeMetaClient:
    def __init__(self) -> None:
        self.calls = []

    def create_campaign(self, **kwargs):
        self.calls.append(("campaign", kwargs))
        return "cmp"

    def create_ad_set(self, **kwargs):
        self.calls.append(("adset", kwargs))
        return "set"

    def create_ad_creative(self, **kwargs):
        self.calls.append(("creative", kwargs))
        return "creative"

    def create_ad(self, **kwargs):
        self.calls.append(("ad", kwargs))
        return "ad"


def _action():
    return SimpleNamespace(
        id=uuid4(),
        experiment_id=uuid4(),
        platform=DistributionPlatform.INSTAGRAM,
        action_type=DistributionActionType.PAID_CAMPAIGN,
    )


def _adapter(connection, client):
    return MetaAdsExecutionAdapter(
        client=client,
        secret_resolver=FakeSecretResolver(),
        connection_service=FakeConnectionService(connection),
        spec_service=FakeSpecService(),
    )


def test_meta_staging_fails_before_provider_mutation_without_any_usable_image(monkeypatch) -> None:
    action = _action()
    client = FakeMetaClient()
    monkeypatch.setattr(
        "app.execution_adapters.distribution_execution_service.get_experiment",
        lambda experiment_id: SimpleNamespace(product_id=uuid4()),
    )
    monkeypatch.setattr(
        "app.execution_adapters.creative_asset_service.readiness",
        lambda action_id: SimpleNamespace(
            status=CreativeReadinessStatus.BLOCKED,
            selected_asset=None,
        ),
    )

    result = _adapter(FakeConnection(default_image_url=None), client).execute(action)

    assert result.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert "CreativeAsset" in result.message
    assert client.calls == []
    assert result.metadata["spend_started"] is False


def test_action_level_creative_overrides_connection_default_for_meta(monkeypatch) -> None:
    action = _action()
    client = FakeMetaClient()
    asset_id = uuid4()
    asset_url = "https://partizan.example.com/v1/public/creative-blobs/image-id"
    monkeypatch.setattr(
        "app.execution_adapters.distribution_execution_service.get_experiment",
        lambda experiment_id: SimpleNamespace(product_id=uuid4()),
    )
    monkeypatch.setattr(
        "app.execution_adapters.creative_asset_service.readiness",
        lambda action_id: SimpleNamespace(
            status=CreativeReadinessStatus.READY,
            selected_asset=SimpleNamespace(id=asset_id, public_url=asset_url),
        ),
    )

    result = _adapter(
        FakeConnection(default_image_url="https://legacy.example.com/default.jpg"),
        client,
    ).execute(action)

    assert result.outcome == AdapterExecutionOutcome.STAGED
    creative_call = next(kwargs for step, kwargs in client.calls if step == "creative")
    assert str(creative_call["connection"].default_image_url) == asset_url
    assert result.metadata["creative_asset_id"] == str(asset_id)

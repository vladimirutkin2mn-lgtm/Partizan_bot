from types import SimpleNamespace
from uuid import uuid4

from app.telegram_native_attribution import TelegramNativeAttributionError
from app.telegram_profile_conversion_pack import TelegramProfileCTAType, TelegramProfilePackStatus
from app.telegram_profile_learning import (
    TelegramProfileLearningService,
    TelegramProfileLearningSignal,
)


def _pack(*, status=TelegramProfilePackStatus.APPLIED, bio="Product ↓\nhttps://t.me/+invite"):
    return SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        product_id=uuid4(),
        action_id=uuid4(),
        experiment_id=uuid4(),
        status=status,
        display_name="Product profile",
        bio=bio,
        cta_type=TelegramProfileCTAType.TELEGRAM_CHANNEL_INVITE,
        cta_value="https://t.me/+invite",
        avatar=SimpleNamespace(sha256="a" * 64),
    )


class FakePackService:
    def __init__(self, packs):
        self.packs = packs

    def list(self, project_id, customer_token):
        assert customer_token == "customer-token"
        return self.packs


class FakeExecutionService:
    def __init__(self, actions):
        self.actions = actions

    def get_action(self, action_id):
        return self.actions[action_id]


class FakeAnalyticsService:
    def __init__(self, rows):
        self.rows = rows

    def experiment_analytics(self, experiment_id):
        value = self.rows[experiment_id]
        if isinstance(value, Exception):
            raise value
        return value


class FakeNativeAttributionService:
    def __init__(self, kinds):
        self.kinds = kinds

    def get(self, project_id, customer_token, pack_id):
        kind = self.kinds.get(pack_id)
        if kind is None:
            raise TelegramNativeAttributionError("not provisioned")
        return SimpleNamespace(kind=SimpleNamespace(value=kind))


def _analytics(**overrides):
    values = {
        "visits": 0,
        "joins": 0,
        "bot_starts": 0,
        "signups": 0,
        "activated_users": 0,
        "paid_users": 0,
        "revenue": 0.0,
    }
    values.update(overrides)
    return SimpleNamespace(
        metrics=SimpleNamespace(**values),
        replies=0,
        removals=0,
    )


def test_profile_learning_combines_profile_message_and_deepest_conversion_signal() -> None:
    pack = _pack()
    action = SimpleNamespace(
        content_payload={
            "draft_variant": "expertise_signal",
            "conversion_mechanism": "PROFILE_CLICK",
        }
    )
    service = TelegramProfileLearningService(
        pack_service=FakePackService([pack]),
        execution_service=FakeExecutionService({pack.action_id: action}),
        analytics_service=FakeAnalyticsService(
            {
                pack.experiment_id: _analytics(
                    visits=12,
                    joins=4,
                    signups=2,
                    activated_users=1,
                    revenue=0,
                )
            }
        ),
        native_attribution_service=FakeNativeAttributionService(
            {pack.id: "CHANNEL_INVITE"}
        ),
    )

    result = service.overview(pack.project_id, "customer-token")

    assert result.experiment_count == 1
    assert result.measured_experiment_count == 1
    row = result.rows[0]
    assert row.message_strategy == "expertise_signal"
    assert row.conversion_mechanism == "PROFILE_CLICK"
    assert row.native_attribution_kind == "CHANNEL_INVITE"
    assert row.visits == 12
    assert row.joins == 4
    assert row.signups == 2
    assert row.activated_users == 1
    assert row.deepest_signal == TelegramProfileLearningSignal.ACTIVATED
    assert row.deepest_signal_count == 1


def test_profile_treatment_key_is_independent_of_action_and_experiment_identity() -> None:
    first = _pack()
    second = _pack()
    second.display_name = first.display_name
    second.bio = first.bio
    second.cta_type = first.cta_type
    second.cta_value = first.cta_value
    second.avatar = first.avatar
    actions = {
        first.action_id: SimpleNamespace(content_payload={}),
        second.action_id: SimpleNamespace(content_payload={}),
    }
    analytics = {
        first.experiment_id: _analytics(),
        second.experiment_id: _analytics(),
    }
    service = TelegramProfileLearningService(
        pack_service=FakePackService([first, second]),
        execution_service=FakeExecutionService(actions),
        analytics_service=FakeAnalyticsService(analytics),
        native_attribution_service=FakeNativeAttributionService({}),
    )

    result = service.overview(first.project_id, "customer-token")

    assert len(result.rows) == 2
    assert result.rows[0].profile_treatment_key == result.rows[1].profile_treatment_key


def test_profile_learning_keeps_unmeasurable_experiment_visible_without_crashing() -> None:
    pack = _pack()
    service = TelegramProfileLearningService(
        pack_service=FakePackService([pack]),
        execution_service=FakeExecutionService(
            {pack.action_id: SimpleNamespace(content_payload={"draft_variant": "question"})}
        ),
        analytics_service=FakeAnalyticsService(
            {pack.experiment_id: ValueError("experiment is not measurable yet")}
        ),
        native_attribution_service=FakeNativeAttributionService({}),
    )

    result = service.overview(pack.project_id, "customer-token")

    assert result.measured_experiment_count == 0
    assert result.rows[0].analytics_available is False
    assert result.rows[0].deepest_signal == TelegramProfileLearningSignal.NONE
    assert result.rows[0].message_strategy == "question"


def test_archived_profile_pack_is_excluded_from_learning() -> None:
    pack = _pack(status=TelegramProfilePackStatus.ARCHIVED)
    service = TelegramProfileLearningService(
        pack_service=FakePackService([pack]),
        execution_service=FakeExecutionService({}),
        analytics_service=FakeAnalyticsService({}),
        native_attribution_service=FakeNativeAttributionService({}),
    )

    result = service.overview(pack.project_id, "customer-token")

    assert result.rows == []
    assert result.experiment_count == 0

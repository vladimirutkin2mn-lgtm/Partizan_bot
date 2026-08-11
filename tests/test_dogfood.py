from uuid import UUID

import pytest

from app.analytics_service import analytics_service
from app.channel_service import channel_service
from app.dogfood import DogfoodManifest, DogfoodOptions, DogfoodRunner
from app.execution_service import execution_service
from app.growth_manager_service import growth_manager_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.product_intake import product_intake_service


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    channel_service.reset()
    growth_play_service.reset()
    execution_service.reset()
    analytics_service.reset()
    growth_manager_service.reset()


def _manifest(with_destination: bool = False) -> DogfoodManifest:
    return DogfoodManifest(
        name="oracle-dogfood-test",
        brief=(
            "Product: Oracle\n"
            "Description: Telegram AI entertainment product with personalized "
            "relationship readings.\n"
            "Problem: People want clarity when relationships feel uncertain.\n"
            "Value proposition: Personalized relationship readings available on demand.\n"
            "USP: Personalized to the user's situation instead of a generic horoscope.\n"
            "Market: US\n"
            "Language: English\n"
            "Price: 9.99\n"
            "Pricing model: subscription\n"
            "Goal: Acquire the first 20 paid users\n"
            "Budget: 100\n"
            "Max CAC: 5"
        ),
        destination_url="https://example.com/oracle" if with_destination else None,
        contact_email="partner@example.com" if with_destination else None,
        contact_name="Alex" if with_destination else None,
    )


@pytest.mark.asyncio
async def test_dogfood_research_cycle_reaches_ranked_plays() -> None:
    report = await DogfoodRunner().run(_manifest())
    assert report.product_status == "CONFIRMED"
    assert report.icp_count >= 10
    assert report.channel_count >= 30
    assert report.play_count >= 20
    assert report.selected_play_rank == 1
    assert report.selected_play_priority is not None
    assert any("SEARCH_PROVIDER is mock" in blocker for blocker in report.blockers)
    assert any("destination URL" in blocker for blocker in report.blockers)


@pytest.mark.asyncio
async def test_dogfood_can_prepare_but_not_send_without_second_approval() -> None:
    report = await DogfoodRunner().run(
        _manifest(with_destination=True),
        DogfoodOptions(prepare_execution=True),
    )
    assert report.execution_package_id is not None
    assert report.experiment_id is not None
    assert report.execution_status == "PREPARED"
    assert any("EXECUTION_PROVIDER is mock" in blocker for blocker in report.blockers)


@pytest.mark.asyncio
async def test_dogfood_can_exercise_full_mock_run_with_explicit_flags() -> None:
    report = await DogfoodRunner().run(
        _manifest(with_destination=True),
        DogfoodOptions(
            prepare_execution=True,
            approve_execution=True,
            run_execution=True,
        ),
    )
    assert report.execution_status == "SENT"
    assert report.experiment_id is not None
    experiment = execution_service.get_experiment(UUID(report.experiment_id))
    assert experiment.status == "RUNNING"


@pytest.mark.asyncio
async def test_dogfood_run_requires_explicit_execution_approval() -> None:
    with pytest.raises(ValueError, match="run_execution requires approve_execution"):
        await DogfoodRunner().run(
            _manifest(with_destination=True),
            DogfoodOptions(
                prepare_execution=True,
                run_execution=True,
            ),
        )


@pytest.mark.asyncio
async def test_live_search_guard_rejects_mock_provider() -> None:
    with pytest.raises(ValueError, match="SEARCH_PROVIDER=openai"):
        await DogfoodRunner().run(
            _manifest(),
            DogfoodOptions(require_live_search=True),
        )

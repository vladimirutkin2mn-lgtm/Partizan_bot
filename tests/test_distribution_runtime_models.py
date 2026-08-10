from app.db import Base

import app.distribution_models  # noqa: F401
import app.distribution_runtime_models  # noqa: F401
import app.runtime_models  # noqa: F401


def test_channel_first_runtime_tables_are_registered() -> None:
    expected = {
        "runtime_snapshots",
        "distribution_opportunities",
        "distribution_identities",
        "community_policies",
        "campaign_slots",
        "distribution_actions",
        "distribution_plays",
        "distribution_experiments",
        "distribution_analytics_events",
        "distribution_experiment_spend",
        "distribution_growth_decisions",
        "distribution_learning_entries",
    }

    assert expected.issubset(Base.metadata.tables)


def test_distribution_experiment_links_to_channel_first_objects() -> None:
    table = Base.metadata.tables["distribution_experiments"]
    foreign_keys = {
        (foreign_key.parent.name, foreign_key.target_fullname)
        for foreign_key in table.foreign_keys
    }

    assert ("distribution_play_id", "distribution_plays.id") in foreign_keys
    assert ("opportunity_id", "distribution_opportunities.id") in foreign_keys
    assert ("action_id", "distribution_actions.id") in foreign_keys

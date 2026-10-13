from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _asset() -> str:
    return (ROOT / "app" / "web" / "workspace.live-opportunities.v1.js").read_text(
        encoding="utf-8"
    )


def test_workspace_has_separate_research_communities_panel() -> None:
    source = _asset()

    assert "Communities to watch" in source
    assert "These are research leads, not publish targets." in source
    assert "/research-communities" in source
    assert "Research only" in source
    assert "Open community ↗" in source


def test_research_community_card_has_no_publish_or_action_target_controls() -> None:
    source = _asset()
    start = source.index("const researchCard =")
    end = source.index("const render =", start)
    research_card = source[start:end]

    assert "item.community_url" in research_card
    assert "item.write_access" in research_card
    assert "action_target_url" not in research_card
    assert "publishability" not in research_card
    assert "recommended_action" not in research_card
    assert "suggested_content" not in research_card
    assert "Draft" not in research_card


def test_workspace_shows_aggregate_participant_evidence_without_identity_fields() -> None:
    source = _asset()
    start = source.index("const participantSummary =")
    end = source.index("const card =", start)
    participant_summary = source[start:end]

    assert "Observed audience" in participant_summary
    assert "item.observed_participant_count" in participant_summary
    assert "item.matched_participant_count" in participant_summary
    assert "item.matched_message_count" in participant_summary
    assert "item.repeat_participant_count" in participant_summary
    assert "item.participant_evidence_checked_at" in participant_summary
    assert "actor_id" not in participant_summary
    assert "participant_ids" not in participant_summary
    assert "usernames" not in participant_summary
    assert "action_target_url" not in participant_summary
    assert "publishability" not in participant_summary

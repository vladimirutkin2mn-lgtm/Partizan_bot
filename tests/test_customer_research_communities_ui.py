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

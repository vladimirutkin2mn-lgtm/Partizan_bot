from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_research_cards_show_intelligence_dimensions_and_exploration_badge() -> None:
    source = (ROOT / "app" / "web" / "workspace.live-opportunities.v1.js").read_text(
        encoding="utf-8"
    )
    start = source.index("const researchCard =")
    end = source.index("const render =", start)
    research_card = source[start:end]

    assert "Audience fit" in research_card
    assert "Context fit" in research_card
    assert "Actionability" in research_card
    assert "Confidence" in research_card
    assert "Adjacent exploration" in research_card
    assert "item.exploration_candidate" in research_card
    assert "action_target_url" not in research_card
    assert "recommended_action" not in research_card

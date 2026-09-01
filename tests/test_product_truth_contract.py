from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_public_legal_copy_matches_product_understanding_and_research_before_spend() -> None:
    privacy = (ROOT / "app" / "web" / "privacy.v1.html").read_text(encoding="utf-8")
    terms = (ROOT / "app" / "web" / "terms.v1.html").read_text(encoding="utf-8")

    assert "Product Understanding" in privacy
    assert "evidence-backed acquisition opportunities before funding" in privacy
    assert "free scan" not in privacy.lower()

    assert "Product analysis, Acquisition Plan and acquisition budget" in terms
    assert "evidence-backed opportunity before acquisition funding is required" in terms
    assert "Adding acquisition budget is not permission or proof that Partizan can spend it" in terms
    assert "free scan" not in terms.lower()


def test_repository_sources_of_truth_keep_issue_160_fail_closed() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    autopilot = (ROOT / "docs" / "CUSTOMER_AUTOPILOT.md").read_text(encoding="utf-8")
    status = (ROOT / "docs" / "CURRENT_STATUS.md").read_text(encoding="utf-8")

    for document in (readme, autopilot, status):
        assert "#160" in document
        assert "settlement_ready=false" in document

    assert "fund Growth Balance and let Partizan execute autonomously" not in readme
    assert "free pre-scan" not in autopilot.lower()
    assert "Issue #121 closed on 27 August 2026" in status
    assert "Issue #160 remains open" in status
    assert "real infrastructure pending" not in status
    assert "#121 remains open" not in status

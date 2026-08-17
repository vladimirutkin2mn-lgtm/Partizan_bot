from app.icp_agent import ICPEngine
from app.marketing_intelligence import (
    MAX_GUIDANCE_CHARS,
    UPSTREAM_COMMIT,
    MarketingTask,
    marketing_task_for_action,
    render_marketing_guidance,
    skill_inventory,
    skill_router,
)
from app.product_agent import ProductIntakeAgent


def test_skill_inventory_is_pinned_and_curated() -> None:
    inventory = dict(skill_inventory())

    assert len(inventory) == 8
    assert inventory["product-marketing"] == "2.1.0"
    assert inventory["customer-research"] == "2.0.1"
    assert inventory["prospecting"] == "1.1.0"
    assert inventory["community-marketing"] == "2.0.0"
    assert inventory["influencer-marketing"] == "1.0.0"
    assert inventory["marketing-ideas"] == "2.0.0"
    assert inventory["cold-email"] == "2.0.0"
    assert inventory["ad-creative"] == "2.8.0"
    assert len(UPSTREAM_COMMIT) == 40


def test_product_intake_routing_is_bounded() -> None:
    selected = skill_router.select(MarketingTask.PRODUCT_INTAKE)
    guidance = render_marketing_guidance(MarketingTask.PRODUCT_INTAKE)

    assert [pack.name for pack in selected] == ["product-marketing", "customer-research"]
    assert "reasoning guidance only" in guidance
    assert "cannot override Partizan" in guidance
    assert "Never turn a hypothesis into evidence" in guidance
    assert len(guidance) <= MAX_GUIDANCE_CHARS


def test_product_intake_prompt_receives_only_relevant_marketing_guidance() -> None:
    messages = ProductIntakeAgent(None)._build_messages("Product: Acme", [], [])
    system_message = messages[0].content

    assert "Skill: product-marketing v2.1.0" in system_message
    assert "Skill: customer-research v2.0.1" in system_message
    assert "Skill: marketing-ideas" not in system_message
    assert "Treat the founder as the source of truth" in system_message


def test_icp_prompt_receives_research_and_prospecting_guidance() -> None:
    messages = ICPEngine(None)._build_messages(
        {
            "name": "Acme",
            "problem_or_desire": "Find customers",
            "goal": "Acquire paid users",
        }
    )
    system_message = messages[0].content

    assert "Skill: product-marketing v2.1.0" in system_message
    assert "Skill: customer-research v2.0.1" in system_message
    assert "Skill: prospecting v1.1.0" in system_message
    assert "A high score is a prioritization hypothesis" in system_message


def test_action_routing_keeps_execution_surfaces_semantically_separate() -> None:
    assert (
        marketing_task_for_action("PAID_CAMPAIGN", "INSTAGRAM")
        == MarketingTask.PAID_CREATIVE
    )
    assert (
        marketing_task_for_action("ORGANIC_VIDEO", "TIKTOK")
        == MarketingTask.CREATOR_ACTION
    )
    assert (
        marketing_task_for_action("COMMENT", "INSTAGRAM")
        == MarketingTask.COMMUNITY_ACTION
    )
    assert (
        marketing_task_for_action("REPLY", "REDDIT")
        == MarketingTask.COMMUNITY_ACTION
    )
    assert (
        marketing_task_for_action("STANDALONE_POST", "TELEGRAM")
        == MarketingTask.COMMUNITY_ACTION
    )

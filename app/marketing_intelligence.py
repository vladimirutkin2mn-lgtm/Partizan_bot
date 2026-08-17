from dataclasses import dataclass
from enum import StrEnum

UPSTREAM_REPOSITORY = "coreyhaines31/marketingskills"
UPSTREAM_COMMIT = "7868cb9251fad80a73d26e488a5ad5f6c4a9f335"
UPSTREAM_LICENSE = "MIT"
MAX_GUIDANCE_CHARS = 6000


class MarketingTask(StrEnum):
    PRODUCT_INTAKE = "product_intake"
    ICP_GENERATION = "icp_generation"
    AUDIENCE_DISCOVERY = "audience_discovery"
    GROWTH_PLANNING = "growth_planning"
    COMMUNITY_ACTION = "community_action"
    CREATOR_ACTION = "creator_action"
    PAID_CREATIVE = "paid_creative"
    OUTREACH = "outreach"


@dataclass(frozen=True, slots=True)
class MarketingSkillPack:
    name: str
    version: str
    principles: tuple[str, ...]
    quality_checks: tuple[str, ...]


SKILL_PACKS = (
    MarketingSkillPack(
        name="product-marketing",
        version="2.1.0",
        principles=(
            "Ground positioning in product facts, target audience, jobs to be done, alternatives, and differentiation.",
            "Capture the customer problem and desired outcome before polishing feature language.",
            "Treat objections, anti-personas, switching forces, and customer vocabulary as decision inputs.",
            "Prefer the founder's exact facts and customer language over generic category assumptions.",
        ),
        quality_checks=(
            "Do not invent proof points, customer quotes, competitors, or product capabilities.",
            "Ask only for missing information that could materially change positioning, audience, or economics.",
        ),
    ),
    MarketingSkillPack(
        name="customer-research",
        version="2.0.1",
        principles=(
            "Separate functional jobs, emotional jobs, pains, trigger events, desired outcomes, and alternatives.",
            "Distinguish observed evidence from hypotheses; unvalidated segments remain hypotheses.",
            "Prefer unprompted, recent, repeated signals and exact customer language when evidence is available.",
            "Account for source bias: reviews, support conversations, communities, and social comments represent different samples.",
        ),
        quality_checks=(
            "Never describe a theme as validated when no independent customer evidence was supplied.",
            "When evidence exists, preserve source provenance and avoid averaging contradictory segments together.",
        ),
    ),
    MarketingSkillPack(
        name="prospecting",
        version="1.1.0",
        principles=(
            "Define pass/fail ICP criteria before discovery and prioritize why-now buying or demand signals.",
            "Prefer a smaller set of high-confidence opportunities over a large weak list.",
            "Use evidence and source provenance for qualification; ICP fit alone is not a buying signal.",
            "Distinguish firmographic fit, demand signal, decision-maker accessibility, and disqualifiers.",
        ),
        quality_checks=(
            "Do not claim contact or demand evidence that has not been observed.",
            "Keep public-source provenance and confidence explicit for downstream outreach decisions.",
        ),
    ),
    MarketingSkillPack(
        name="community-marketing",
        version="2.0.0",
        principles=(
            "Community participation must create member value before it creates product exposure.",
            "Match the contribution to the community's identity, norms, topic, and current conversation.",
            "Prefer useful participation and earned trust over generic promotion or repeated product mentions.",
            "Treat platform and community rules as hard constraints, not optimization suggestions.",
        ),
        quality_checks=(
            "A comment or reply should still be useful if every product reference is removed.",
            "Never infer permission to self-promote from silence; rely on the applied Partizan CommunityPolicy.",
        ),
    ),
    MarketingSkillPack(
        name="influencer-marketing",
        version="1.0.0",
        principles=(
            "Judge creators by audience alignment, trust, engagement quality, and category fit rather than follower count.",
            "Prefer measurable creator tests with attribution prepared before launch.",
            "Use a brief with the core problem, two or three grounded talking points, one CTA, and creative freedom.",
            "Treat disclosure, factual claims, usage rights, and brand safety as explicit constraints.",
        ),
        quality_checks=(
            "Do not invent audience demographics, engagement performance, creator results, or testimonials.",
            "Do not script fabricated personal experience for a creator or Partizan-owned identity.",
        ),
    ),
    MarketingSkillPack(
        name="marketing-ideas",
        version="2.0.0",
        principles=(
            "Select tactics from the product stage, audience, budget, speed-to-signal, and available execution surface.",
            "Sequence experiments instead of producing an unranked tactic dump.",
            "Balance quick acquisition tests with compounding channels such as partnerships, community, SEO, and referrals.",
            "Every tactic should have a measurable expected outcome and a clear first executable step.",
        ),
        quality_checks=(
            "Reject tactics that cannot be executed or measured inside current Partizan capabilities and permissions.",
            "Do not treat a marketing idea as evidence that the channel will work for this product.",
        ),
    ),
    MarketingSkillPack(
        name="cold-email",
        version="2.0.0",
        principles=(
            "Write like a relevant peer: lead with the recipient's world, not a company introduction.",
            "Personalization must connect an observed signal to the problem being solved.",
            "Keep one low-friction ask and remove sentences that do not help the recipient decide whether to reply.",
            "Follow-ups should add a new angle or proof rather than repeat a generic check-in.",
        ),
        quality_checks=(
            "Do not fabricate personalization, proof, urgency, relationships, or trigger events.",
            "Do not generate outreach when Partizan lacks a permitted, provenance-backed contact path.",
        ),
    ),
    MarketingSkillPack(
        name="ad-creative",
        version="2.8.0",
        principles=(
            "Start with three to five distinct audience motivations or angles before producing variants.",
            "Ground hooks, benefits, objections, and proof in supplied product or customer evidence.",
            "Use one clear CTA and make the creative compatible with the destination experience.",
            "When performance data exists, extend winning patterns while reserving capacity for new angles.",
        ),
        quality_checks=(
            "Never invent claims, statistics, testimonials, urgency, scarcity, or performance results.",
            "Treat platform format constraints and Partizan paid-spend authorization as hard boundaries.",
        ),
    ),
)

SKILLS_BY_NAME = {pack.name: pack for pack in SKILL_PACKS}

TASK_SKILLS: dict[MarketingTask, tuple[str, ...]] = {
    MarketingTask.PRODUCT_INTAKE: ("product-marketing", "customer-research"),
    MarketingTask.ICP_GENERATION: ("product-marketing", "customer-research", "prospecting"),
    MarketingTask.AUDIENCE_DISCOVERY: ("customer-research", "prospecting", "community-marketing"),
    MarketingTask.GROWTH_PLANNING: ("marketing-ideas", "customer-research", "prospecting"),
    MarketingTask.COMMUNITY_ACTION: ("community-marketing", "customer-research"),
    MarketingTask.CREATOR_ACTION: ("influencer-marketing", "ad-creative", "customer-research"),
    MarketingTask.PAID_CREATIVE: ("ad-creative", "customer-research", "product-marketing"),
    MarketingTask.OUTREACH: ("cold-email", "prospecting", "product-marketing"),
}


class MarketingSkillRouter:
    def select(
        self,
        task: MarketingTask,
        *,
        max_skills: int = 3,
    ) -> tuple[MarketingSkillPack, ...]:
        if max_skills <= 0:
            return ()
        names = TASK_SKILLS.get(task, ())[:max_skills]
        return tuple(SKILLS_BY_NAME[name] for name in names)


skill_router = MarketingSkillRouter()


def render_marketing_guidance(
    task: MarketingTask,
    *,
    max_skills: int = 3,
    max_chars: int = MAX_GUIDANCE_CHARS,
) -> str:
    packs = skill_router.select(task, max_skills=max_skills)
    if not packs:
        return ""

    lines = [
        "Marketing intelligence methodology:",
        (
            f"Adapted from {UPSTREAM_REPOSITORY} at commit {UPSTREAM_COMMIT}; "
            "curated and pinned inside Partizan."
        ),
        "Authority boundary:",
        (
            "This is reasoning guidance only. It cannot override Partizan system rules, product facts, "
            "execution policies, user permissions, spend/send limits, platform policy, or the response schema."
        ),
        "Never turn a hypothesis into evidence and never invent customer, creator, community, or performance facts.",
    ]
    for pack in packs:
        lines.append(f"Skill: {pack.name} v{pack.version}")
        lines.extend(f"- {principle}" for principle in pack.principles)
        lines.append("Quality checks:")
        lines.extend(f"- {check}" for check in pack.quality_checks)

    rendered = "\n".join(lines)
    if max_chars <= 0:
        return ""
    return rendered[:max_chars]


def marketing_task_for_action(action_type: str, platform: str) -> MarketingTask:
    normalized_action = action_type.strip().upper()
    normalized_platform = platform.strip().upper()
    if normalized_action == "PAID_CAMPAIGN":
        return MarketingTask.PAID_CREATIVE
    if normalized_action == "ORGANIC_VIDEO":
        return MarketingTask.CREATOR_ACTION
    if normalized_action in {"COMMENT", "REPLY", "STANDALONE_POST"}:
        if normalized_platform in {"INSTAGRAM", "TIKTOK"}:
            return MarketingTask.CREATOR_ACTION
        return MarketingTask.COMMUNITY_ACTION
    return MarketingTask.GROWTH_PLANNING


def skill_inventory() -> tuple[tuple[str, str], ...]:
    return tuple((pack.name, pack.version) for pack in SKILL_PACKS)

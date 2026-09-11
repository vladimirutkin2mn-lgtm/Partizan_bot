from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

from app.audience_intelligence_service import AUDIENCE_OPPORTUNITY_NAMESPACE
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import DistributionPlatform
from app.runtime_store import get_runtime_store

RECENT_WINDOW = timedelta(minutes=20)
POLICY_FIELDS = (
    "commercial_participation",
    "self_promotion",
    "links",
    "product_mentions",
    "standalone_posts",
    "comments",
    "disclosure",
)


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def main() -> int:
    now = datetime.now(UTC)
    unknown_counts: Counter[str] = Counter()
    research_status_counts: Counter[str] = Counter()
    recent_count = 0
    fully_known_count = 0
    evidence_backed_count = 0

    for payload in get_runtime_store().list_namespace(AUDIENCE_OPPORTUNITY_NAMESPACE):
        try:
            opportunity = DistributionOpportunityView.model_validate(payload)
        except ValueError:
            continue
        if opportunity.platform != DistributionPlatform.REDDIT:
            continue
        proposal = opportunity.metadata.get("policy_proposal")
        if not isinstance(proposal, dict):
            continue
        generated_at = _parse_time(proposal.get("generated_at"))
        if generated_at is None:
            continue
        age = now - generated_at
        if age < timedelta(0) or age > RECENT_WINDOW:
            continue

        recent_count += 1
        unknown_fields = [
            field
            for field in POLICY_FIELDS
            if str(proposal.get(field, "UNKNOWN")).upper() == "UNKNOWN"
        ]
        unknown_counts.update(unknown_fields)
        if not unknown_fields:
            fully_known_count += 1
        if proposal.get("evidence"):
            evidence_backed_count += 1

        research = opportunity.metadata.get("community_policy_research")
        if isinstance(research, dict):
            status = str(research.get("research_status") or "MISSING").upper()
        else:
            status = "MISSING"
        research_status_counts[status] += 1

    print(
        json.dumps(
            {
                "status": "OK",
                "recent_reddit_policy_proposal_count": recent_count,
                "fully_known_policy_proposal_count": fully_known_count,
                "evidence_backed_policy_proposal_count": evidence_backed_count,
                "unknown_field_counts": dict(sorted(unknown_counts.items())),
                "research_status_counts": dict(sorted(research_status_counts.items())),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

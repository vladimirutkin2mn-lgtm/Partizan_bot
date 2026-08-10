import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import DistributionPlatform
from app.opportunity_enrichment_schemas import (
    CommunityPolicyProposalView,
    OpportunityEnrichmentView,
    ProductOpportunityEnrichmentView,
)
from app.schemas import ProductProfileView
from app.search import DiscoveryQuery, SearchHit, SearchProvider, SourceClass, get_search_provider


class OpportunityEnrichmentService:
    def __init__(self, provider: SearchProvider | None = None) -> None:
        self._provider = provider or get_search_provider()

    async def enrich_product(
        self,
        product: ProductProfileView,
        opportunities: list[DistributionOpportunityView],
        *,
        max_opportunities: int = 20,
    ) -> ProductOpportunityEnrichmentView:
        selected = sorted(
            opportunities,
            key=lambda item: (-(item.relevance_score or 0), str(item.id)),
        )[:max_opportunities]
        results: list[OpportunityEnrichmentView] = []
        for opportunity in selected:
            results.append(await self.enrich(product, opportunity))
        return ProductOpportunityEnrichmentView(
            product_id=product.id,
            requested_count=len(selected),
            enriched_count=sum(not item.partial_failure for item in results),
            partial_failure_count=sum(item.partial_failure for item in results),
            results=results,
        )

    async def enrich(
        self,
        product: ProductProfileView,
        opportunity: DistributionOpportunityView,
        *,
        limit: int = 6,
    ) -> OpportunityEnrichmentView:
        query = self._query(product, opportunity)
        try:
            hits = await self._provider.search(query, limit=limit)
        except Exception as exc:
            return OpportunityEnrichmentView(
                opportunity=opportunity,
                new_evidence_count=0,
                partial_failure=True,
                failure_reason=f"{type(exc).__name__}: {str(exc)[:400]}",
            )

        relevant_hits = [
            hit for hit in hits if self._matches_platform(opportunity.platform, hit.url)
        ]
        now = datetime.now(UTC)
        new_evidence = [self._evidence(hit, now) for hit in relevant_hits]
        merged_evidence = self._merge_evidence(opportunity.evidence, new_evidence)
        metadata = dict(opportunity.metadata)
        enrichment = dict(metadata.get("enrichment", {}))
        enrichment.update(
            {
                "last_enriched_at": now.isoformat(),
                "query": query.query,
                "provider": type(self._provider).__name__,
                "evidence_count": len(relevant_hits),
                "size_evidence": self._size_evidence(relevant_hits),
                "activity_evidence": self._activity_evidence(relevant_hits),
                "action_targets": self._action_targets(opportunity.platform, relevant_hits),
                "missing_data": self._missing_data(opportunity.platform, relevant_hits),
            }
        )
        metadata["enrichment"] = enrichment

        policy_proposal = None
        if opportunity.platform == DistributionPlatform.REDDIT:
            policy_proposal = RedditPolicyProposalBuilder().build(
                opportunity,
                relevant_hits,
                generated_at=now,
            )
            metadata["policy_proposal"] = policy_proposal.model_dump(mode="json")

        updated = opportunity.model_copy(
            update={
                "metadata": metadata,
                "evidence": merged_evidence,
            }
        )
        audience_intelligence_service.update_opportunity(updated)
        return OpportunityEnrichmentView(
            opportunity=updated,
            policy_proposal=policy_proposal,
            new_evidence_count=len(new_evidence),
        )

    def _query(
        self,
        product: ProductProfileView,
        opportunity: DistributionOpportunityView,
    ) -> DiscoveryQuery:
        market = product.market or "online"
        language = product.language or ""
        if opportunity.platform == DistributionPlatform.TELEGRAM:
            handle = opportunity.metadata.get("handle") or opportunity.title
            return DiscoveryQuery(
                SourceClass.COMMUNITY,
                (
                    f"site:t.me/{str(handle).lstrip('@')} {handle} members subscribers "
                    f"posts comments discussion activity {market} {language}"
                ),
            )
        if opportunity.platform == DistributionPlatform.INSTAGRAM:
            handle = opportunity.metadata.get("account_handle") or opportunity.title
            return DiscoveryQuery(
                SourceClass.CREATOR,
                (
                    f"site:instagram.com {handle} recent reels posts creator followers "
                    f"{market} {language}"
                ),
            )
        if opportunity.platform == DistributionPlatform.REDDIT:
            subreddit = opportunity.metadata.get("subreddit") or opportunity.title
            subreddit = str(subreddit).removeprefix("r/")
            return DiscoveryQuery(
                SourceClass.COMMUNITY,
                (
                    f"site:reddit.com/r/{subreddit} rules promotion self-promotion links "
                    f"advertising community activity members {market} {language}"
                ),
            )
        topic = opportunity.metadata.get("topic") or opportunity.title
        return DiscoveryQuery(
            SourceClass.CREATOR,
            f"site:tiktok.com {topic} recent videos creators hashtags {market} {language}",
        )

    def _matches_platform(self, platform: DistributionPlatform, url: str) -> bool:
        try:
            host = urlsplit(url.strip()).netloc.lower().removeprefix("www.")
        except ValueError:
            return False
        hosts = {
            DistributionPlatform.TELEGRAM: {"t.me", "telegram.me"},
            DistributionPlatform.INSTAGRAM: {"instagram.com"},
            DistributionPlatform.REDDIT: {"reddit.com"},
            DistributionPlatform.TIKTOK: {"tiktok.com"},
        }
        expected = hosts[platform]
        return any(host == item or host.endswith(f".{item}") for item in expected)

    def _evidence(self, hit: SearchHit, checked_at: datetime) -> dict:
        return {
            "purpose": "opportunity_enrichment",
            "query": hit.query,
            "title": hit.title,
            "url": hit.url,
            "snippet": hit.snippet,
            "checked_at": checked_at.isoformat(),
        }

    def _merge_evidence(self, existing: list[dict], incoming: list[dict]) -> list[dict]:
        merged = [dict(item) for item in existing]
        known = {
            (str(item.get("url", "")), str(item.get("purpose", "discovery")))
            for item in merged
        }
        for item in incoming:
            key = (str(item.get("url", "")), str(item.get("purpose", "")))
            if key in known:
                continue
            merged.append(item)
            known.add(key)
        return merged

    def _size_evidence(self, hits: list[SearchHit]) -> list[str]:
        evidence: set[str] = set()
        pattern = re.compile(
            r"\b\d[\d,.]*\s*(?:k|m)?\s*(?:members|subscribers|followers|users)\b",
            re.IGNORECASE,
        )
        for hit in hits:
            evidence.update(match.group(0) for match in pattern.finditer(hit.snippet))
        return sorted(evidence)[:20]

    def _activity_evidence(self, hits: list[SearchHit]) -> list[dict]:
        evidence: list[dict] = []
        terms = ("active", "daily", "weekly", "recent", "posts", "comments", "discussion")
        for hit in hits:
            text = f"{hit.title} {hit.snippet}".lower()
            matched = [term for term in terms if term in text]
            if matched:
                evidence.append(
                    {
                        "url": hit.url,
                        "matched_terms": matched,
                        "snippet": hit.snippet[:400],
                    }
                )
        return evidence[:20]

    def _action_targets(
        self,
        platform: DistributionPlatform,
        hits: list[SearchHit],
    ) -> list[dict]:
        targets: list[dict] = []
        for hit in hits:
            parts = urlsplit(hit.url)
            segments = [segment for segment in parts.path.split("/") if segment]
            if platform == DistributionPlatform.INSTAGRAM:
                if not segments or segments[0].lower() not in {"p", "reel", "reels"}:
                    continue
            elif platform == DistributionPlatform.TIKTOK:
                if "video" not in [segment.lower() for segment in segments]:
                    continue
            else:
                continue
            targets.append(
                {
                    "url": hit.url,
                    "title": hit.title,
                    "snippet": hit.snippet[:400],
                }
            )
        return targets[:30]

    def _missing_data(
        self,
        platform: DistributionPlatform,
        hits: list[SearchHit],
    ) -> list[str]:
        missing: list[str] = []
        if not hits:
            return ["enrichment_evidence"]
        if not self._size_evidence(hits):
            missing.append("audience_size")
        if not self._activity_evidence(hits):
            missing.append("activity")
        if platform in {DistributionPlatform.INSTAGRAM, DistributionPlatform.TIKTOK}:
            if not self._action_targets(platform, hits):
                missing.append("action_targets")
        if platform == DistributionPlatform.REDDIT:
            missing.append("policy_review")
        return missing


class RedditPolicyProposalBuilder:
    def build(
        self,
        opportunity: DistributionOpportunityView,
        hits: list[SearchHit],
        *,
        generated_at: datetime,
    ) -> CommunityPolicyProposalView:
        evidence = [
            {
                "url": hit.url,
                "title": hit.title,
                "snippet": hit.snippet[:600],
            }
            for hit in hits
        ]
        text = " ".join(f"{hit.title} {hit.snippet}" for hit in hits).lower()
        fields: dict[str, str] = {}
        rationale: list[str] = []

        fields["commercial_participation"] = self._state(
            text,
            positive=("promotion is allowed", "promotional content is allowed"),
            negative=("no promotion", "no advertising", "no commercial promotion"),
        )
        fields["self_promotion"] = self._state(
            text,
            positive=("self-promotion is allowed", "self promotion is allowed"),
            negative=("no self-promotion", "no self promotion", "self-promotion is prohibited"),
        )
        fields["links"] = self._state(
            text,
            positive=("links are allowed", "external links allowed", "links allowed"),
            negative=("no links", "links are not allowed", "external links are prohibited"),
        )
        fields["product_mentions"] = self._state(
            text,
            positive=("product mentions are allowed", "product mentions allowed"),
            negative=("no product mentions", "product mentions are prohibited"),
        )
        fields["standalone_posts"] = self._state(
            text,
            positive=("promotional posts are allowed", "promotion posts are allowed"),
            negative=("no promotional posts", "promotional posts are prohibited"),
        )
        fields["comments"] = self._state(
            text,
            positive=("promotional comments are allowed", "promotion in comments is allowed"),
            negative=("no promotional comments", "promotion in comments is prohibited"),
        )
        disclosure = self._disclosure_state(text)

        known = sum(value != "UNKNOWN" for value in fields.values())
        known += disclosure != "UNKNOWN"
        if not hits:
            rationale.append("No enrichment evidence was found; all policy fields remain UNKNOWN.")
        else:
            rationale.append(
                f"Policy proposal is based on {len(hits)} public evidence item(s); "
                f"{known} field(s) have explicit textual signals."
            )
        rationale.append(
            "UNKNOWN is intentionally restrictive and the proposal is not applied automatically."
        )
        confidence = min(95.0, known * 12.0) if hits else 0.0
        return CommunityPolicyProposalView(
            opportunity_id=opportunity.id,
            commercial_participation=fields["commercial_participation"],
            self_promotion=fields["self_promotion"],
            links=fields["links"],
            product_mentions=fields["product_mentions"],
            standalone_posts=fields["standalone_posts"],
            comments=fields["comments"],
            disclosure=disclosure,
            confidence=confidence,
            rationale=rationale,
            evidence=evidence,
            generated_at=generated_at,
        )

    def _state(
        self,
        text: str,
        *,
        positive: tuple[str, ...],
        negative: tuple[str, ...],
    ) -> str:
        has_positive = any(pattern in text for pattern in positive)
        has_negative = any(pattern in text for pattern in negative)
        if has_positive == has_negative:
            return "UNKNOWN"
        return "ALLOWED" if has_positive else "DISALLOWED"

    def _disclosure_state(self, text: str) -> str:
        required = any(
            pattern in text
            for pattern in (
                "disclosure required",
                "must disclose",
                "disclose your affiliation",
                "disclose affiliation",
            )
        )
        not_required = any(
            pattern in text
            for pattern in ("no disclosure required", "disclosure is not required")
        )
        if required == not_required:
            return "UNKNOWN"
        return "REQUIRED" if required else "NOT_REQUIRED"


opportunity_enrichment_service = OpportunityEnrichmentService()

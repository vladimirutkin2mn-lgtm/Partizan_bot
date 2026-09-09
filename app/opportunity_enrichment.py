import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import DistributionPlatform
from app.opportunity_enrichment_schemas import (
    CommunityPolicyProposalView,
    OpportunityEnrichmentView,
    ProductOpportunityEnrichmentView,
)
from app.reddit_research import (
    REDDIT_MAX_ACTION_TARGETS,
    action_target_is_fresh,
    reddit_thread_target_from_hit,
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
        results.sort(
            key=lambda item: (
                -(item.opportunity.relevance_score or 0),
                str(item.opportunity.id),
            )
        )
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
            thread_hits: list[SearchHit] = []
            if opportunity.platform == DistributionPlatform.REDDIT:
                thread_hits = await self._provider.search(
                    self._reddit_thread_query(product, opportunity),
                    limit=min(REDDIT_MAX_ACTION_TARGETS, max(1, limit)),
                )
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
        relevant_thread_hits = [
            hit
            for hit in thread_hits
            if self._matches_platform(DistributionPlatform.REDDIT, hit.url)
        ]
        now = datetime.now(UTC)
        evidence_hits = self._dedupe_hits([*relevant_hits, *relevant_thread_hits])
        new_evidence = [self._evidence(hit, now) for hit in evidence_hits]
        merged_evidence = self._merge_evidence(opportunity.evidence, new_evidence)
        metadata = dict(opportunity.metadata)
        enrichment = dict(metadata.get("enrichment", {}))
        action_targets = self._action_targets(
            opportunity.platform,
            relevant_thread_hits if opportunity.platform == DistributionPlatform.REDDIT else relevant_hits,
            checked_at=now,
            opportunity=opportunity,
        )
        enrichment.update(
            {
                "last_enriched_at": now.isoformat(),
                "query": query.query,
                "provider": type(self._provider).__name__,
                "evidence_count": len(evidence_hits),
                "size_evidence": self._size_evidence(evidence_hits),
                "activity_evidence": self._activity_evidence(evidence_hits),
                "action_targets": action_targets,
                "missing_data": self._missing_data(
                    opportunity.platform,
                    evidence_hits,
                    action_targets=action_targets,
                ),
            }
        )
        metadata["enrichment"] = enrichment

        policy_proposal = None
        relevance_score = opportunity.relevance_score
        if opportunity.platform == DistributionPlatform.REDDIT:
            policy_proposal = RedditPolicyProposalBuilder().build(
                opportunity,
                relevant_hits,
                generated_at=now,
            )
            metadata["policy_proposal"] = policy_proposal.model_dump(mode="json")
            applied_policy = self._persist_reddit_research_policy(
                opportunity,
                policy_proposal,
                checked_at=now,
            )
            fresh_targets = [
                target
                for target in action_targets
                if action_target_is_fresh(target, now=now)
            ]
            ranking = self._reddit_ranking(
                product_id=product.id,
                opportunity=opportunity,
                proposal=policy_proposal,
                fresh_target_count=len(fresh_targets),
                activity_evidence=enrichment["activity_evidence"],
            )
            metadata["community_policy_research"] = {
                "policy_id": str(applied_policy.id),
                "research_status": applied_policy.research_status,
                "source": applied_policy.source,
                "last_checked_at": applied_policy.last_checked_at.isoformat()
                if applied_policy.last_checked_at
                else None,
                "fresh_until": applied_policy.fresh_until.isoformat()
                if applied_policy.fresh_until
                else None,
                "confidence": applied_policy.confidence,
                "evidence_count": len(applied_policy.evidence),
            }
            metadata["ranking"] = ranking
            relevance_score = ranking["score"]
            missing = list(enrichment["missing_data"])
            if policy_proposal.has_unknowns and "policy_review" not in missing:
                missing.append("policy_review")
            if not fresh_targets and "fresh_action_targets" not in missing:
                missing.append("fresh_action_targets")
            enrichment["missing_data"] = missing

        updated = opportunity.model_copy(
            update={
                "metadata": metadata,
                "evidence": merged_evidence,
                "relevance_score": relevance_score,
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
                    f"advertising product mentions comments posts megathread AI content "
                    f"{market} {language}"
                ),
            )
        topic = opportunity.metadata.get("topic") or opportunity.title
        return DiscoveryQuery(
            SourceClass.CREATOR,
            f"site:tiktok.com {topic} recent videos creators hashtags {market} {language}",
        )

    def _reddit_thread_query(
        self,
        product: ProductProfileView,
        opportunity: DistributionOpportunityView,
    ) -> DiscoveryQuery:
        subreddit = str(
            opportunity.metadata.get("subreddit") or opportunity.title
        ).removeprefix("r/")
        problem = (product.problem_or_desire or product.description or product.name)[:240]
        market = product.market or ""
        language = product.language or ""
        return DiscoveryQuery(
            SourceClass.COMMUNITY,
            (
                f"site:reddit.com/r/{subreddit}/comments recent discussion {problem} "
                f"{market} {language}"
            ),
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

    def _dedupe_hits(self, hits: list[SearchHit]) -> list[SearchHit]:
        output: list[SearchHit] = []
        seen: set[str] = set()
        for hit in hits:
            if hit.url in seen:
                continue
            output.append(hit)
            seen.add(hit.url)
        return output

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
        *,
        checked_at: datetime,
        opportunity: DistributionOpportunityView,
    ) -> list[dict]:
        targets: list[dict] = []
        for hit in hits:
            if platform == DistributionPlatform.REDDIT:
                subreddit = str(
                    opportunity.metadata.get("subreddit") or opportunity.title
                ).removeprefix("r/")
                target = reddit_thread_target_from_hit(
                    hit,
                    subreddit=subreddit,
                    checked_at=checked_at,
                )
                if target is not None:
                    targets.append(target)
                continue

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
        if platform == DistributionPlatform.REDDIT:
            targets.sort(
                key=lambda item: (
                    0 if item.get("freshness_status") == "FRESH" else 1,
                    str(item.get("published_at") or ""),
                    str(item.get("url") or ""),
                )
            )
            return targets[:REDDIT_MAX_ACTION_TARGETS]
        return targets[:30]

    def _missing_data(
        self,
        platform: DistributionPlatform,
        hits: list[SearchHit],
        *,
        action_targets: list[dict],
    ) -> list[str]:
        missing: list[str] = []
        if not hits:
            return ["enrichment_evidence"]
        if not self._size_evidence(hits):
            missing.append("audience_size")
        if not self._activity_evidence(hits):
            missing.append("activity")
        if platform in {DistributionPlatform.INSTAGRAM, DistributionPlatform.TIKTOK}:
            if not action_targets:
                missing.append("action_targets")
        if platform == DistributionPlatform.REDDIT:
            if not any(action_target_is_fresh(item) for item in action_targets):
                missing.append("fresh_action_targets")
        return missing

    def _persist_reddit_research_policy(
        self,
        opportunity: DistributionOpportunityView,
        proposal: CommunityPolicyProposalView,
        *,
        checked_at: datetime,
    ):
        from app.distribution_control_plane_schemas import CommunityPolicyUpsertRequest
        from app.distribution_control_plane_service import distribution_control_plane_service

        verified = bool(proposal.evidence) and not proposal.has_unknowns
        return distribution_control_plane_service.upsert_policy(
            opportunity.id,
            CommunityPolicyUpsertRequest(
                commercial_participation_allowed=(
                    proposal.commercial_participation == "ALLOWED"
                ),
                self_promotion_allowed=proposal.self_promotion == "ALLOWED",
                links_allowed=proposal.links == "ALLOWED",
                product_mentions_allowed=proposal.product_mentions == "ALLOWED",
                standalone_posts_allowed=proposal.standalone_posts == "ALLOWED",
                comments_allowed=proposal.comments == "ALLOWED",
                disclosure_required=proposal.disclosure == "REQUIRED",
                special_promotion_windows=proposal.special_promotion_windows,
                ai_content_constraints=proposal.ai_content_constraints,
                evidence=proposal.evidence,
                source="indexed_public_research",
                research_status="VERIFIED" if verified else "PARTIAL",
                last_checked_at=checked_at,
                confidence=proposal.confidence,
            ),
        )

    def _reddit_ranking(
        self,
        *,
        product_id: UUID,
        opportunity: DistributionOpportunityView,
        proposal: CommunityPolicyProposalView,
        fresh_target_count: int,
        activity_evidence: list[dict],
    ) -> dict:
        relevance = float(opportunity.relevance_score or 0)
        activity = min(100.0, fresh_target_count * 20.0 + len(activity_evidence) * 5.0)
        if proposal.has_unknowns or not proposal.evidence:
            policy_fit = 0.0
        elif proposal.commercial_participation != "ALLOWED":
            policy_fit = 10.0
        elif proposal.comments == "ALLOWED" or proposal.standalone_posts == "ALLOWED":
            policy_fit = 100.0
        else:
            policy_fit = 30.0
        prior_outcomes = self._prior_outcome_score(product_id, opportunity.id)
        score = round(
            relevance * 0.55
            + activity * 0.20
            + policy_fit * 0.15
            + prior_outcomes * 0.10,
            2,
        )
        return {
            "score": max(0.0, min(100.0, score)),
            "relevance": round(relevance, 2),
            "activity": round(activity, 2),
            "policy_fit": round(policy_fit, 2),
            "prior_outcomes": round(prior_outcomes, 2),
            "fresh_thread_count": fresh_target_count,
        }

    def _prior_outcome_score(self, product_id: UUID, opportunity_id: UUID) -> float:
        try:
            from app.distribution_analytics_service import distribution_analytics_service

            analytics = distribution_analytics_service.product_analytics(product_id)
        except (KeyError, RuntimeError, ValueError):
            return 50.0
        matching = [
            item
            for item in analytics.experiments
            if item.play.opportunity_id == opportunity_id
        ]
        if not matching:
            return 50.0
        event_count = sum(item.event_count for item in matching)
        paid_users = sum(item.metrics.paid_users for item in matching)
        revenue = sum(item.metrics.revenue for item in matching)
        score = 40.0 + min(30.0, event_count * 3.0)
        if paid_users:
            score += 20.0
        if revenue > 0:
            score += 10.0
        return max(0.0, min(100.0, score))


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
                "checked_at": generated_at.isoformat(),
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
        special_windows = self._special_promotion_windows(text, evidence)
        ai_constraints = self._ai_constraints(text)

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
            "UNKNOWN is intentionally restrictive; PARTIAL research is persisted "
            "fail-closed and cannot authorize execution."
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
            special_promotion_windows=special_windows,
            ai_content_constraints=ai_constraints,
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
        not_required_patterns = (
            "no disclosure required",
            "disclosure is not required",
        )
        if any(pattern in text for pattern in not_required_patterns):
            stripped = text
            for pattern in not_required_patterns:
                stripped = stripped.replace(pattern, "")
            if not any(
                pattern in stripped
                for pattern in (
                    "disclosure required",
                    "must disclose",
                    "disclose your affiliation",
                    "disclose affiliation",
                )
            ):
                return "NOT_REQUIRED"
        required = any(
            pattern in text
            for pattern in (
                "disclosure required",
                "must disclose",
                "disclose your affiliation",
                "disclose affiliation",
            )
        )
        if required:
            return "REQUIRED"
        return "UNKNOWN"

    def _special_promotion_windows(self, text: str, evidence: list[dict]) -> list[dict]:
        terms = ("megathread", "promo thread", "promotion thread", "self-promotion thread")
        if not any(term in text for term in terms):
            return []
        return [
            {
                "kind": "designated_promotion_surface",
                "description": (
                    "Promotion appears limited to a designated thread or window; verify the "
                    "current pinned/rules surface before execution."
                ),
                "evidence_urls": [item["url"] for item in evidence[:5]],
            }
        ]

    def _ai_constraints(self, text: str) -> list[str]:
        constraints: list[str] = []
        if any(
            term in text
            for term in (
                "no ai-generated content",
                "no ai generated content",
                "ai-generated content is prohibited",
                "ai content is prohibited",
            )
        ):
            constraints.append("AI_GENERATED_CONTENT_PROHIBITED")
        if "ai content must be disclosed" in text or "disclose ai" in text:
            constraints.append("AI_CONTENT_DISCLOSURE_REQUIRED")
        return constraints


opportunity_enrichment_service = OpportunityEnrichmentService()

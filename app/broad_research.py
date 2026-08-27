from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, Field

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import ICPGenerationResponse, ICPView, ProductProfileView
from app.search import DiscoveryQuery, SearchHit, SearchProvider, SourceClass, get_search_provider

BROAD_RESEARCH_NAMESPACE = "broad_research_maps"
_BROAD_RESEARCH_UUID_NAMESPACE = UUID("5aa1e843-17ee-45b2-99bb-30e91d6efe28")


class ResearchSurface(StrEnum):
    CREATOR = "CREATOR"
    MEDIA = "MEDIA"
    PARTNERSHIP = "PARTNERSHIP"
    SEARCH = "SEARCH"
    DIRECTORY = "DIRECTORY"
    COMMUNITY = "COMMUNITY"


class ResearchExecutionStatus(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    OUTREACH_POSSIBLE = "OUTREACH_POSSIBLE"
    MANUAL_HANDOFF = "MANUAL_HANDOFF"


class BroadResearchEvidenceView(BaseModel):
    query: str = Field(min_length=1, max_length=800)
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2000)
    snippet: str = Field(default="", max_length=1200)


class BroadResearchOpportunityView(BaseModel):
    id: UUID
    product_id: UUID
    icp_id: UUID
    surface: ResearchSurface
    kind: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    rationale: str = Field(min_length=1, max_length=1200)
    relevance_score: float = Field(ge=0, le=100)
    execution_status: ResearchExecutionStatus
    execution_requirement: str = Field(min_length=1, max_length=500)
    provenance: list[BroadResearchEvidenceView] = Field(min_length=1)


class BroadResearchMapView(BaseModel):
    product_id: UUID
    icp_count: int = Field(ge=1)
    opportunity_count: int = Field(ge=0)
    opportunities: list[BroadResearchOpportunityView] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ResearchQuery:
    surface: ResearchSurface
    kind: str
    icp: ICPView
    discovery_query: DiscoveryQuery


_SURFACE_EXECUTION = {
    ResearchSurface.CREATOR: (
        ResearchExecutionStatus.OUTREACH_POSSIBLE,
        (
            "Partizan may prepare outreach, but contact access, review and recipient "
            "consent/response are still required."
        ),
    ),
    ResearchSurface.MEDIA: (
        ResearchExecutionStatus.OUTREACH_POSSIBLE,
        (
            "Partizan may prepare outreach or a pitch; publication access and editorial "
            "approval are not implied."
        ),
    ),
    ResearchSurface.PARTNERSHIP: (
        ResearchExecutionStatus.OUTREACH_POSSIBLE,
        "Partnership or affiliate execution requires contact, commercial agreement and partner approval.",
    ),
    ResearchSurface.SEARCH: (
        ResearchExecutionStatus.RESEARCH_ONLY,
        "This is a Search/SEO research cluster, not a connected advertising or publishing integration.",
    ),
    ResearchSurface.DIRECTORY: (
        ResearchExecutionStatus.MANUAL_HANDOFF,
        (
            "Listing or review-site placement requires the site's own submission/account "
            "process or a manual handoff."
        ),
    ),
    ResearchSurface.COMMUNITY: (
        ResearchExecutionStatus.MANUAL_HANDOFF,
        (
            "This public community is research evidence only until a permissioned Partizan "
            "execution path exists for it."
        ),
    ),
}


class BroadResearchService:
    """Discover public-web growth surfaces without creating execution permissions."""

    def __init__(
        self,
        store: RuntimeStateStore,
        search_provider: SearchProvider | None = None,
    ) -> None:
        self._store = store
        self._search_provider = search_provider

    def get(self, product_id: UUID) -> BroadResearchMapView | None:
        payload = self._store.get(BROAD_RESEARCH_NAMESPACE, str(product_id))
        if payload is None:
            return None
        return BroadResearchMapView.model_validate(payload)

    async def discover(
        self,
        product: ProductProfileView,
        icp_result: ICPGenerationResponse,
    ) -> BroadResearchMapView:
        icps = list(icp_result.icps[:2])
        queries = [query for icp in icps for query in self._queries(product, icp)]
        provider = self._search_provider or get_search_provider()
        results = await asyncio.gather(
            *(provider.search(item.discovery_query, limit=2) for item in queries),
            return_exceptions=True,
        )

        opportunities: list[BroadResearchOpportunityView] = []
        seen: set[tuple[ResearchSurface, str]] = set()
        for query, result in zip(queries, results, strict=True):
            if isinstance(result, BaseException) or not result:
                continue
            hits = list(result)
            if query.surface == ResearchSurface.SEARCH:
                opportunity = self._search_cluster(product, query, hits)
                dedupe_key = (query.surface, query.discovery_query.query.casefold())
                if dedupe_key not in seen:
                    seen.add(dedupe_key)
                    opportunities.append(opportunity)
                continue
            for hit in hits:
                normalized_url = hit.url.rstrip("/").casefold()
                dedupe_key = (query.surface, normalized_url)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                opportunities.append(self._hit_opportunity(product, query, hit))

        opportunities.sort(
            key=lambda item: (-item.relevance_score, item.surface.value, item.title.casefold())
        )
        view = BroadResearchMapView(
            product_id=product.id,
            icp_count=len(icps),
            opportunity_count=len(opportunities),
            opportunities=opportunities,
        )
        self._store.put(
            BROAD_RESEARCH_NAMESPACE,
            str(product.id),
            view.model_dump(mode="json"),
        )
        return view

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(BROAD_RESEARCH_NAMESPACE)

    def _queries(self, product: ProductProfileView, icp: ICPView) -> list[_ResearchQuery]:
        context = self._context(product, icp)
        return [
            self._query(
                ResearchSurface.CREATOR,
                "CREATOR_PROFILE",
                icp,
                SourceClass.CREATOR,
                f"{context} creators experts YouTube X blogs",
            ),
            self._query(
                ResearchSurface.MEDIA,
                "PUBLICATION_OR_PODCAST",
                icp,
                SourceClass.NEWSLETTER_SITE,
                f"{context} newsletter podcast publication media",
            ),
            self._query(
                ResearchSurface.PARTNERSHIP,
                "PARTNER_OR_AFFILIATE",
                icp,
                SourceClass.NEWSLETTER_SITE,
                f"{context} partner affiliate integration marketplace",
            ),
            self._query(
                ResearchSurface.SEARCH,
                "QUERY_CLUSTER",
                icp,
                SourceClass.NEWSLETTER_SITE,
                f"{icp.pain} {icp.desired_outcome} alternatives how to",
            ),
            self._query(
                ResearchSurface.DIRECTORY,
                "DIRECTORY_OR_REVIEW_SITE",
                icp,
                SourceClass.NEWSLETTER_SITE,
                f"{context} directory reviews alternatives comparison",
            ),
            self._query(
                ResearchSurface.COMMUNITY,
                "PUBLIC_COMMUNITY",
                icp,
                SourceClass.COMMUNITY,
                f"{context} forum Discord community discussion",
            ),
        ]

    @staticmethod
    def _query(
        surface: ResearchSurface,
        kind: str,
        icp: ICPView,
        source_class: SourceClass,
        text: str,
    ) -> _ResearchQuery:
        return _ResearchQuery(
            surface=surface,
            kind=kind,
            icp=icp,
            discovery_query=DiscoveryQuery(source_class=source_class, query=" ".join(text.split())[:800]),
        )

    @staticmethod
    def _context(product: ProductProfileView, icp: ICPView) -> str:
        product_context = product.problem_or_desire or product.value_proposition or product.description
        return " ".join(f"{icp.title} {product_context}".split())[:500]

    def _hit_opportunity(
        self,
        product: ProductProfileView,
        query: _ResearchQuery,
        hit: SearchHit,
    ) -> BroadResearchOpportunityView:
        status, requirement = _SURFACE_EXECUTION[query.surface]
        evidence = self._evidence(hit)
        return BroadResearchOpportunityView(
            id=self._id(product.id, query.surface, hit.url),
            product_id=product.id,
            icp_id=query.icp.id,
            surface=query.surface,
            kind=query.kind,
            title=hit.title[:500],
            url=hit.url[:2000],
            rationale=self._rationale(query.surface, query.icp),
            relevance_score=self._score(query.icp, 1),
            execution_status=status,
            execution_requirement=requirement,
            provenance=[evidence],
        )

    def _search_cluster(
        self,
        product: ProductProfileView,
        query: _ResearchQuery,
        hits: list[SearchHit],
    ) -> BroadResearchOpportunityView:
        status, requirement = _SURFACE_EXECUTION[ResearchSurface.SEARCH]
        query_text = query.discovery_query.query
        return BroadResearchOpportunityView(
            id=self._id(product.id, ResearchSurface.SEARCH, query_text),
            product_id=product.id,
            icp_id=query.icp.id,
            surface=ResearchSurface.SEARCH,
            kind=query.kind,
            title=f"Search cluster: {query.icp.pain}"[:500],
            url=None,
            rationale=(
                f"Search intent around the pain and desired outcome of {query.icp.title} "
                "can be tested as an SEO/content cluster before any paid-search integration exists."
            )[:1200],
            relevance_score=self._score(query.icp, len(hits)),
            execution_status=status,
            execution_requirement=requirement,
            provenance=[self._evidence(hit) for hit in hits],
        )

    @staticmethod
    def _evidence(hit: SearchHit) -> BroadResearchEvidenceView:
        return BroadResearchEvidenceView(
            query=hit.query[:800],
            title=hit.title[:500],
            url=hit.url[:2000],
            snippet=hit.snippet[:1200],
        )

    @staticmethod
    def _score(icp: ICPView, evidence_count: int) -> float:
        return round(min(100.0, 45.0 + icp.score * 0.45 + min(evidence_count, 3) * 3.0), 1)

    @staticmethod
    def _rationale(surface: ResearchSurface, icp: ICPView) -> str:
        descriptions = {
            ResearchSurface.CREATOR: (
                "A concrete creator audience may already concentrate this customer segment."
            ),
            ResearchSurface.MEDIA: (
                "A specialist media, newsletter or podcast may already aggregate this customer segment."
            ),
            ResearchSurface.PARTNERSHIP: (
                "A complementary business or affiliate surface may provide distribution "
                "through a partner relationship."
            ),
            ResearchSurface.DIRECTORY: (
                "A directory, comparison or review surface may intercept customers while "
                "they evaluate alternatives."
            ),
            ResearchSurface.COMMUNITY: (
                "A public community may contain active discussions around this segment's "
                "problem and alternatives."
            ),
        }
        return f"{descriptions[surface]} Target segment: {icp.title}."[:1200]

    @staticmethod
    def _id(product_id: UUID, surface: ResearchSurface, value: str) -> UUID:
        digest = hashlib.sha256(value.strip().casefold().encode("utf-8")).hexdigest()
        return uuid5(_BROAD_RESEARCH_UUID_NAMESPACE, f"{product_id}:{surface.value}:{digest}")


broad_research_service = BroadResearchService(get_runtime_store())

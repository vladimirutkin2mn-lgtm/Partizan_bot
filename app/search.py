import asyncio
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.config import get_settings


class SourceClass(StrEnum):
    COMMUNITY = "community"
    CREATOR = "creator"
    NEWSLETTER_SITE = "newsletter_site"
    DIRECTORY = "directory"
    PARTNERSHIP = "partnership"
    SEARCH = "search"


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    source_class: SourceClass
    query: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    query: str
    source_class: SourceClass


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, discovery_query: DiscoveryQuery, limit: int = 5) -> list[SearchHit]:
        raise NotImplementedError


class MockSearchProvider(SearchProvider):
    async def search(self, discovery_query: DiscoveryQuery, limit: int = 5) -> list[SearchHit]:
        digest = hashlib.sha1(discovery_query.query.encode("utf-8")).hexdigest()[:8]
        hits: list[SearchHit] = []
        for index in range(1, limit + 1):
            query = discovery_query.query.lower()
            if "site:t.me" in query:
                url = f"https://t.me/partizan_{digest}_{index}"
            elif "site:instagram.com" in query:
                url = f"https://www.instagram.com/partizan_{digest}_{index}/"
            elif "site:reddit.com" in query:
                url = f"https://www.reddit.com/r/partizan_{digest}_{index}"
            elif "site:tiktok.com" in query:
                url = f"https://www.tiktok.com/@partizan_{digest}/video/{1000 + index}"
            elif discovery_query.source_class == SourceClass.COMMUNITY:
                url = f"https://www.reddit.com/r/partizan_{digest}_{index}"
            elif discovery_query.source_class == SourceClass.CREATOR:
                url = f"https://www.youtube.com/@partizan_{digest}_{index}"
            else:
                url = f"https://partizan-{digest}-{index}.example.com/"
            hits.append(
                SearchHit(
                    title=f"Mock {discovery_query.source_class.value} opportunity {index}",
                    url=url,
                    snippet=f"Search evidence for: {discovery_query.query}",
                    query=discovery_query.query,
                    source_class=discovery_query.source_class,
                )
            )
        return hits


class OpenAIWebSearchProvider(SearchProvider):
    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model

    async def search(self, discovery_query: DiscoveryQuery, limit: int = 5) -> list[SearchHit]:
        source_instruction = {
            SourceClass.COMMUNITY: (
                "Find concrete public communities and recent discussion threads where this "
                "audience is actively discussing the problem, alternatives or desired outcome. "
                "Prefer a recent discussion URL when it provides stronger evidence than a generic "
                "community home page."
            ),
            SourceClass.CREATOR: (
                "Find concrete creators whose public profile or recent content demonstrates a "
                "clear audience overlap with the query. Prefer a source that makes the overlap "
                "verifiable rather than a generic creator directory."
            ),
            SourceClass.NEWSLETTER_SITE: (
                "Find concrete newsletters, niche publications or specialist websites with an "
                "audience relevant to the query."
            ),
            SourceClass.DIRECTORY: (
                "Find concrete public directories, comparison sites, review sites or marketplaces "
                "where this audience actively evaluates products like the one in the query."
            ),
            SourceClass.PARTNERSHIP: (
                "Find concrete complementary businesses, affiliate programs, integrations or "
                "distribution partners with a plausible audience overlap."
            ),
            SourceClass.SEARCH: (
                "Find concrete public pages that demonstrate recurring search intent, questions, "
                "alternatives or how-to demand around the problem in the query."
            ),
        }[discovery_query.source_class]
        prompt = (
            f"{source_instruction}\n\n"
            f"Search query: {discovery_query.query}\n\n"
            f"Return a concise answer citing up to {limit} strong concrete sources. "
            "Every cited source must materially support why this is a plausible distribution "
            "opportunity: audience overlap, active problem/intent, or a relevant evaluation path. "
            "Prefer recent public evidence when recency is available. Do not cite a source merely "
            "because it contains matching keywords. If no source clears that evidence bar, return "
            "no citations rather than inventing or stretching relevance."
        )
        response = await asyncio.to_thread(
            self._client.responses.create,
            model=self._model,
            tools=[{"type": "web_search"}],
            input=prompt,
        )
        hits = self._extract_hits(response, discovery_query)
        if not hits:
            raise RuntimeError("Web search returned no URL citations")
        return hits[:limit]

    def _extract_hits(
        self,
        response: Any,
        discovery_query: DiscoveryQuery,
    ) -> list[SearchHit]:
        fallback_text = str(getattr(response, "output_text", "") or "")
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for output_item in getattr(response, "output", []) or []:
            for content in getattr(output_item, "content", []) or []:
                snippet = str(getattr(content, "text", "") or fallback_text)
                for annotation in getattr(content, "annotations", []) or []:
                    url = self._read(annotation, "url")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    title = self._read(annotation, "title") or url
                    hits.append(
                        SearchHit(
                            title=str(title),
                            url=str(url),
                            snippet=snippet[:800],
                            query=discovery_query.query,
                            source_class=discovery_query.source_class,
                        )
                    )
        return hits

    def _read(self, value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)


def get_search_provider() -> SearchProvider:
    settings = get_settings()
    if settings.search_provider == "mock":
        return MockSearchProvider()
    if settings.search_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when SEARCH_PROVIDER=openai")
        return OpenAIWebSearchProvider(
            api_key=settings.openai_api_key,
            model=settings.search_model,
        )
    raise ValueError(f"Unsupported search provider: {settings.search_provider}")

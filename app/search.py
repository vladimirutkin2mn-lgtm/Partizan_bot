import asyncio
import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.config import get_settings
from app.openai_usage import log_openai_usage, openai_task_options


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
    metadata: dict = field(default_factory=dict)


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
        self._first_request_lock = asyncio.Lock()
        self._first_request_completed = False
        self._fatal_search_error: Exception | None = None

    async def search(self, discovery_query: DiscoveryQuery, limit: int = 5) -> list[SearchHit]:
        source_instruction = {
            SourceClass.COMMUNITY: (
                "Find concrete public communities where this audience is present and can be "
                "reached. A post or thread may be used as evidence that the community fits, but "
                "return the community as the primary distribution surface rather than treating "
                "multiple posts from the same community as separate opportunities."
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
        response = await self._create_response(prompt)
        hits = self._extract_hits(response, discovery_query)
        return hits[:limit]

    async def _create_response(self, prompt: str) -> Any:
        if self._fatal_search_error is not None:
            raise self._fatal_search_error

        if not self._first_request_completed:
            async with self._first_request_lock:
                if self._fatal_search_error is not None:
                    raise self._fatal_search_error
                if not self._first_request_completed:
                    try:
                        return await self._request_response(prompt)
                    finally:
                        self._first_request_completed = True

        return await self._request_response(prompt)

    async def _request_response(self, prompt: str) -> Any:
        options = openai_task_options(self._model)
        started = time.perf_counter()
        try:
            response = await asyncio.to_thread(
                self._client.responses.create,
                **options,
                tools=[{"type": "web_search"}],
                input=prompt,
            )
        except Exception as exc:
            if self._is_credit_balance_exhausted(exc):
                self._fatal_search_error = exc
            raise
        log_openai_usage(
            response,
            options=options,
            duration_seconds=time.perf_counter() - started,
        )
        return response

    @staticmethod
    def _is_credit_balance_exhausted(exc: Exception) -> bool:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            details = body.get("error", body)
            if isinstance(details, dict):
                error_type = str(details.get("type") or "").lower()
                error_code = str(details.get("code") or "").lower()
                if error_type == "insufficient_quota":
                    return True
                if error_code == "credit_balance_exhausted":
                    return True

        message = f"{exc} {body or ''}".lower()
        return any(
            marker in message
            for marker in (
                "insufficient_quota",
                "credit_balance_exhausted",
                "no credits remaining",
            )
        )

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
                content_text = str(getattr(content, "text", "") or fallback_text)
                for annotation in getattr(content, "annotations", []) or []:
                    url = self._read(annotation, "url")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    title = self._read(annotation, "title") or url
                    snippet, binding = self._annotation_snippet(
                        content_text,
                        annotation,
                        fallback=str(title),
                    )
                    hits.append(
                        SearchHit(
                            title=str(title),
                            url=str(url),
                            snippet=snippet[:800],
                            query=discovery_query.query,
                            source_class=discovery_query.source_class,
                            metadata={"evidence_binding": binding},
                        )
                    )
        return hits

    def _annotation_snippet(
        self,
        text: str,
        annotation: Any,
        *,
        fallback: str,
    ) -> tuple[str, str]:
        """Bind evidence to one citation instead of copying the whole answer to every URL."""
        start = self._read(annotation, "start_index")
        end = self._read(annotation, "end_index")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
            window_start = max(0, start - 220)
            window_end = min(len(text), end + 220)
            snippet = " ".join(text[window_start:window_end].split())
            if snippet:
                return snippet, "ANNOTATION_WINDOW"

        annotation_text = self._read(annotation, "text")
        if annotation_text:
            snippet = " ".join(str(annotation_text).split())
            if snippet:
                return snippet, "ANNOTATION_TEXT"

        return " ".join(str(fallback or "").split()), "TITLE_ONLY"

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

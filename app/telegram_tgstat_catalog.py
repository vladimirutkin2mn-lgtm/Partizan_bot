"""Bounded opt-in public TGStat catalog reader; never a publisher or verifier.

Only a publicly documented category URL is fetched. CAPTCHA/403/rate limits
are reported; there are no anti-bot bypasses or credentialed catalog calls.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.runtime_store import RuntimeStateStore
from app.telegram_catalog_import import import_public_catalog

TGSTAT_CATALOG_RUN_NAMESPACE = "telegram_tgstat_catalog_run"
TGSTAT_CATALOG_DAILY_CLAIM_NAMESPACE = "telegram_tgstat_catalog_daily_claim"
TGSTAT_COOLDOWN = timedelta(hours=24)
TGSTAT_MAX_HTML_BYTES = 262144
TGSTAT_MAX_RESULTS = 12

# Deliberately small semantic category map: never silently fetch a generic
# ranking unrelated to a product or infer a geographical location.
_CATEGORY_TERMS = (
    ("erotica", ("femdom", "bdsm", "fetish", "фемдом", "фетиш", "кинк", "бдсм")),
    ("tech", ("python", "developer", "programming", "software", "программи", "разработ")),
    ("business", ("startup", "founder", "business", "предприним", "стартап")),
    ("marketing", ("marketing", "маркетинг", "реклам")),
    ("education", ("education", "university", "обучен", "образован")),
)


class TGStatCatalogError(RuntimeError):
    def __init__(self, status: str, retry_after_seconds: int = 0):
        self.status = status
        self.retry_after_seconds = max(0, retry_after_seconds)
        super().__init__(status)


class TGStatPublicCatalogTransport(Protocol):
    async def fetch(self, *, category: str, surface: str) -> list[dict]: ...


class _TGStatLinks(HTMLParser):
    """Extract only public TGStat peer links; never parse user rosters or messages."""

    _PEER = re.compile(r"^/(?:[a-z]{2}/)?(chat|channel)/@([A-Za-z][A-Za-z0-9_]{4,31})/?$")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: dict[str, dict] = {}
        self._current: str | None = None
        self._title: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        parsed = urlsplit(str(href))
        if parsed.netloc and parsed.netloc.lower() not in {"tgstat.com", "www.tgstat.com"}:
            return
        match = self._PEER.fullmatch(parsed.path)
        if not match:
            return
        kind, handle = match.groups()
        if handle.casefold().endswith("bot"):
            return
        self._current = handle.casefold()
        self._title = []
        self.items.setdefault(
            self._current,
            {
                "username": handle,
                "title": handle,
                "type": "group" if kind == "chat" else "channel",
                "source_url": f"https://tgstat.com{parsed.path}",
            },
        )

    def handle_data(self, data):
        if self._current is not None:
            self._title.append(data[:120])

    def handle_endtag(self, tag):
        if tag == "a" and self._current is not None:
            title = " ".join(" ".join(self._title).split())[:160]
            if title:
                self.items[self._current]["title"] = title
            self._current = None
            self._title = []


def parse_tgstat_category_html(body: str, *, surface: str) -> list[dict]:
    if surface not in {"chats", "channels"}:
        raise ValueError("Unsupported TGStat surface")
    if len(body.encode("utf-8")) > TGSTAT_MAX_HTML_BYTES:
        raise TGStatCatalogError("RESPONSE_TOO_LARGE")
    parser = _TGStatLinks()
    parser.feed(body)
    kind = "group" if surface == "chats" else "channel"
    rows = [row for row in parser.items.values() if row["type"] == kind]
    if not rows:
        # A layout change, paywall or challenge is NOT a successful empty search.
        raise TGStatCatalogError("FORMAT_UNKNOWN")
    return rows[:TGSTAT_MAX_RESULTS]


class TGStatPublicCatalog:
    async def fetch(self, *, category: str, surface: str) -> list[dict]:
        valid = {item[0] for item in _CATEGORY_TERMS}
        if category not in valid or surface not in {"chats", "channels"}:
            raise ValueError("Unexpected TGStat category or surface")
        url = f"https://tgstat.com/ru/ratings/{surface}/{category}?sort=members"
        try:
            async with httpx.AsyncClient(
                timeout=8.0,
                follow_redirects=False,
                headers={"Accept": "text/html", "User-Agent": "PartizanCommunityResearch/1.0"},
            ) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code in {403, 429, 503}:
                        retry = response.headers.get("Retry-After", "")
                        seconds = int(retry) if retry.isdigit() else 0
                        raise TGStatCatalogError("SOURCE_THROTTLED", seconds)
                    if response.status_code != 200:
                        raise TGStatCatalogError("SOURCE_HTTP_ERROR")
                    if "text/html" not in response.headers.get("Content-Type", ""):
                        raise TGStatCatalogError("FORMAT_UNKNOWN")
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > TGSTAT_MAX_HTML_BYTES:
                            raise TGStatCatalogError("RESPONSE_TOO_LARGE")
                        chunks.append(chunk)
            body = b"".join(chunks).decode("utf-8", errors="replace")
            return parse_tgstat_category_html(body, surface=surface)
        except httpx.RequestError as exc:
            raise TGStatCatalogError("SOURCE_UNAVAILABLE") from exc


def _category_for(product, icps) -> str | None:
    values = [
        getattr(product, "problem_or_desire", ""),
        getattr(product, "description", ""),
        getattr(product, "name", ""),
    ]
    for icp in list(icps)[:3]:
        values.extend(
            (getattr(icp, "title", ""), getattr(icp, "pain", ""), getattr(icp, "description", ""))
        )
    text = " ".join(str(value or "") for value in values).casefold()
    for category, terms in _CATEGORY_TERMS:
        if any(term in text for term in terms):
            return category
    return None


def _parse_time(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(str(value))
        return result.astimezone(UTC) if result.tzinfo else result.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)


class TGStatCatalogExpansion:
    """One category-page fetch per product per day, persistent and fail-closed."""

    def __init__(
        self,
        store: RuntimeStateStore,
        *,
        source: TGStatPublicCatalogTransport | None = None,
        enabled: bool = False,
    ):
        self._store = store
        self._source = source or TGStatPublicCatalog()
        self.enabled = enabled

    async def replenish(self, product, icps, *, confirmed_count: int, now=None) -> dict:
        product_id = getattr(product, "id", None)
        if not self.enabled:
            return {"status": "DISABLED", "staged_count": 0}
        if product_id is None or not icps:
            return {"status": "NO_PRODUCT", "staged_count": 0}
        if confirmed_count >= 3:
            return {"status": "SUFFICIENT_REGISTRY", "staged_count": 0}
        category = _category_for(product, icps)
        if category is None:
            return {"status": "NO_CATEGORY", "staged_count": 0}

        checked = now or datetime.now(UTC)
        checked = checked.astimezone(UTC) if checked.tzinfo else checked.replace(tzinfo=UTC)
        key = str(product_id)
        state = self._store.get(TGSTAT_CATALOG_RUN_NAMESPACE, key) or {}
        due = _parse_time(state.get("next_attempt_at"))
        if checked < due:
            return {"status": "NOT_DUE", "staged_count": 0}
        # Rotate across chats and channels. No fresh LLM call, no full-page crawl.
        surface = "chats" if int(state.get("attempt_number") or 0) % 2 == 0 else "channels"
        claim_key = f"{key}:{checked.date().isoformat()}"
        if not self._store.put_if_absent(
            TGSTAT_CATALOG_DAILY_CLAIM_NAMESPACE,
            claim_key,
            {"product_id": key, "claimed_at": checked.isoformat()},
        ):
            return {"status": "ALREADY_CLAIMED", "staged_count": 0}

        state.update({
            "product_id": key,
            "source": "TGSTAT_PUBLIC",
            "category": category,
            "surface": surface,
            "attempt_number": int(state.get("attempt_number") or 0) + 1,
            "last_attempt_at": checked.isoformat(),
            "next_attempt_at": (checked + TGSTAT_COOLDOWN).isoformat(),
            "status": "RUNNING",
            "staged_count": 0,
            "source_requests": 1,
        })
        self._store.put(TGSTAT_CATALOG_RUN_NAMESPACE, key, state)
        try:
            candidates = await self._source.fetch(category=category, surface=surface)
            if not isinstance(candidates, list):
                raise TGStatCatalogError("FORMAT_UNKNOWN")
            # Reuse source validation and the canonical alias-aware registry.
            from app.telegram_catalog_import import parse_public_catalog

            parsed = parse_public_catalog(candidates, source="tgstat-public")
            if not parsed:
                raise TGStatCatalogError("NO_VALID_COMMUNITIES")
            result = import_public_catalog(
                store=self._store,
                product_id=product_id,
                source="tgstat-public",
                communities=parsed,
                commit=True,
            )
            state["status"] = "STAGED_UNVERIFIED"
            state["staged_count"] = result["registry_mutations"]
        except TGStatCatalogError as exc:
            state["status"] = exc.status
            state["next_attempt_at"] = (
                checked + max(TGSTAT_COOLDOWN, timedelta(seconds=exc.retry_after_seconds))
            ).isoformat()
        except Exception as exc:
            state["status"] = "RETRYABLE_FAILED"
            state["error_type"] = type(exc).__name__
        self._store.put(TGSTAT_CATALOG_RUN_NAMESPACE, key, state)
        return {"status": state["status"], "staged_count": state["staged_count"]}

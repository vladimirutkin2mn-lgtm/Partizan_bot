from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

_MAX_BYTES = 1_000_000
_MAX_TEXT = 40_000
_MAX_REDIRECTS = 3


class WebsiteReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WebsiteSnapshot:
    url: str
    title: str
    description: str
    text: str


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.description = ""
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        if name in {"script", "style", "noscript", "svg", "template"}:
            self._skip_depth += 1
            return
        if name == "title":
            self._in_title = True
            return
        if name == "meta":
            values = {key.casefold(): (value or "") for key, value in attrs}
            marker = (values.get("name") or values.get("property") or "").casefold()
            if marker in {"description", "og:description"} and values.get("content"):
                if not self.description:
                    self.description = values["content"].strip()

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in {"script", "style", "noscript", "svg", "template"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if name == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        if not self._skip_depth:
            self.text_parts.append(value)


def _assert_public_url(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise WebsiteReadError("Website must be an absolute http(s) URL.")
    if parts.username or parts.password:
        raise WebsiteReadError("Website credentials are not allowed in the URL.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise WebsiteReadError("Website port is invalid.") from exc
    if port not in {80, 443}:
        raise WebsiteReadError("Only standard website ports 80 and 443 are allowed.")
    host = parts.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise WebsiteReadError("Only public websites can be scanned.")
    return host, port


async def _assert_public_dns(url: str) -> None:
    host, port = _assert_public_url(url)
    try:
        rows = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebsiteReadError("Partizan could not resolve this website.") from exc
    addresses = {row[4][0] for row in rows if row and row[4]}
    if not addresses:
        raise WebsiteReadError("Partizan could not resolve this website.")
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if not ip.is_global:
            raise WebsiteReadError("Only public websites can be scanned.")


async def read_public_website(url: str) -> WebsiteSnapshot:
    current = url
    headers = {
        "User-Agent": "PartizanBot/1.0 (+https://partizanlabs.com)",
        "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
    }
    timeout = httpx.Timeout(8.0, connect=5.0)
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            await _assert_public_dns(current)
            try:
                response = await client.get(current)
            except httpx.HTTPError as exc:
                raise WebsiteReadError("Partizan could not read this website right now.") from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                target = response.headers.get("location")
                if not target:
                    raise WebsiteReadError("Website returned an invalid redirect.")
                current = urljoin(current, target)
                continue
            if response.status_code >= 400:
                raise WebsiteReadError(
                    f"Website returned HTTP {response.status_code}; try a public product page instead."
                )
            content_type = response.headers.get("content-type", "").casefold()
            if content_type and "text/html" not in content_type and "text/plain" not in content_type:
                raise WebsiteReadError("The supplied URL is not a readable web page.")
            raw = response.content
            if len(raw) > _MAX_BYTES:
                raw = raw[:_MAX_BYTES]
            encoding = response.encoding or "utf-8"
            try:
                html = raw.decode(encoding, errors="replace")
            except LookupError:
                html = raw.decode("utf-8", errors="replace")
            parser = _PageParser()
            parser.feed(html)
            text = " ".join(parser.text_parts)
            if len(text) > _MAX_TEXT:
                text = text[:_MAX_TEXT]
            if len(text.strip()) < 80:
                raise WebsiteReadError(
                    "Partizan could not extract enough public product information from this page."
                )
            title = " ".join(parser.title_parts).strip()[:500]
            return WebsiteSnapshot(
                url=str(response.url),
                title=title,
                description=parser.description[:1200],
                text=text,
            )
    raise WebsiteReadError("Website redirected too many times.")

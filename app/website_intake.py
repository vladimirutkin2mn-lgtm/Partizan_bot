from __future__ import annotations

import asyncio
import http.client
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

_MAX_BYTES = 1_000_000
_MAX_TEXT = 40_000
_MAX_REDIRECTS = 3
_CONNECT_TIMEOUT_SECONDS = 5.0
_READ_TIMEOUT_SECONDS = 8.0
_USER_AGENT = "PartizanBot/1.0 (+https://partizanlabs.com)"
_ACCEPT = "text/html,text/plain;q=0.9,*/*;q=0.1"


class WebsiteReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WebsiteSnapshot:
    url: str
    title: str
    description: str
    text: str


@dataclass(frozen=True, slots=True)
class _PinnedResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


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
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise WebsiteReadError("Website URL contains invalid control characters.")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise WebsiteReadError("Website must be an absolute http(s) URL.")
    if parts.username or parts.password:
        raise WebsiteReadError("Website credentials are not allowed in the URL.")
    expected_port = 443 if parts.scheme == "https" else 80
    try:
        port = parts.port or expected_port
    except ValueError as exc:
        raise WebsiteReadError("Website port is invalid.") from exc
    if port != expected_port:
        raise WebsiteReadError("Only the standard port for http(s) websites is allowed.")
    host = parts.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise WebsiteReadError("Only public websites can be scanned.")
    return host, port


async def _resolve_public_addresses(url: str) -> tuple[str, int, tuple[str, ...]]:
    host, port = _assert_public_url(url)
    try:
        rows = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebsiteReadError("Partizan could not resolve this website.") from exc

    addresses: list[str] = []
    for row in rows:
        if not row or not row[4]:
            continue
        raw = str(row[4][0])
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise WebsiteReadError("Partizan received an invalid website address.") from exc
        if not ip.is_global:
            raise WebsiteReadError("Only public websites can be scanned.")
        normalized = str(ip)
        if normalized not in addresses:
            addresses.append(normalized)

    if not addresses:
        raise WebsiteReadError("Partizan could not resolve this website.")
    return host, port, tuple(addresses)


def _open_pinned_socket(address: str, port: int) -> socket.socket:
    ip = ipaddress.ip_address(address)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(_CONNECT_TIMEOUT_SECONDS)
    try:
        target = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
        sock.connect(target)
        sock.settimeout(_READ_TIMEOUT_SECONDS)
        return sock
    except Exception:
        sock.close()
        raise


def _request_target(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if not parts.query:
        return path
    query = quote(parts.query, safe="=&?/%:@!$'()*+,;~-._")
    return f"{path}?{query}"


def _host_header(host: str) -> str:
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        return host.encode("idna").decode("ascii")
    return f"[{parsed}]" if parsed.version == 6 else str(parsed)


def _fetch_pinned(url: str, address: str) -> _PinnedResponse:
    host, port = _assert_public_url(url)
    parts = urlsplit(url)
    raw_socket = _open_pinned_socket(address, port)
    sock: socket.socket = raw_socket
    try:
        if parts.scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(raw_socket, server_hostname=host)
            sock.settimeout(_READ_TIMEOUT_SECONDS)

        request = (
            f"GET {_request_target(url)} HTTP/1.1\r\n"
            f"Host: {_host_header(host)}\r\n"
            f"User-Agent: {_USER_AGENT}\r\n"
            f"Accept: {_ACCEPT}\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")

        sock.sendall(request)
        response = http.client.HTTPResponse(sock)
        response.begin()
        content = response.read(_MAX_BYTES + 1)
        headers = {key.casefold(): value for key, value in response.getheaders()}
        status_code = response.status
        response.close()
        if len(content) > _MAX_BYTES:
            content = content[:_MAX_BYTES]
        return _PinnedResponse(
            status_code=status_code,
            headers=headers,
            content=content,
        )
    finally:
        try:
            sock.close()
        finally:
            if sock is not raw_socket:
                raw_socket.close()


def _decode_content(content: bytes, content_type: str) -> str:
    match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.IGNORECASE)
    encoding = match.group(1) if match else "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


async def _fetch_public_page(url: str) -> _PinnedResponse:
    _host, _port, addresses = await _resolve_public_addresses(url)
    last_error: Exception | None = None
    for address in addresses:
        try:
            return await asyncio.to_thread(_fetch_pinned, url, address)
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
            continue
    raise WebsiteReadError("Partizan could not read this website right now.") from last_error


async def read_public_website(url: str) -> WebsiteSnapshot:
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        response = await _fetch_public_page(current)
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
        content_encoding = response.headers.get("content-encoding", "").casefold().strip()
        if content_encoding not in {"", "identity"}:
            raise WebsiteReadError("Website returned an unsupported compressed response.")

        html = _decode_content(response.content, content_type)
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
            url=current,
            title=title,
            description=parser.description[:1200],
            text=text,
        )
    raise WebsiteReadError("Website redirected too many times.")

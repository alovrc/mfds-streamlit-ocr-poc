"""Bounded public-web collection for the OCR PoC.

The collector deliberately supports only public HTTP(S) resources. It validates
every redirect and resolved address to prevent the Streamlit server from being
used to reach private or link-local services.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (compatible; MFDS-OCR-PoC/1.0; "
    "+https://github.com/alovrc/mfds-streamlit)"
)
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
REQUEST_TIMEOUT_SECONDS = 12.0
_CHARSET_PATTERN = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", re.IGNORECASE)


class CaptureError(RuntimeError):
    """Expected and display-safe collection failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PageCapture:
    """Collected page content before image OCR."""

    requested_url: str
    final_url: str
    title: str
    body_text: str
    image_urls: tuple[str, ...]


Resolver = Callable[..., Iterable[tuple]]


def validate_public_url(url: str, resolver: Resolver = socket.getaddrinfo) -> str:
    """Return a normalized public URL or raise a safe validation error."""

    candidate = url.strip()
    if not candidate:
        raise CaptureError("URL_REQUIRED", "URL을 입력하세요.")
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise CaptureError(
            "URL_SCHEME_BLOCKED",
            "http 또는 https URL만 사용할 수 있습니다.",
        )
    if parsed.username or parsed.password:
        raise CaptureError(
            "URL_CREDENTIALS_BLOCKED",
            "사용자 정보가 포함된 URL은 사용할 수 없습니다.",
        )
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise CaptureError("URL_HOST_REQUIRED", "URL 호스트를 확인하세요.")
    if hostname == "localhost" or hostname.endswith(".local"):
        raise CaptureError(
            "URL_PRIVATE_HOST_BLOCKED",
            "내부 또는 로컬 호스트에는 접근할 수 없습니다.",
        )
    if parsed.port not in {None, 80, 443}:
        raise CaptureError(
            "URL_PORT_BLOCKED",
            "80 또는 443 포트만 사용할 수 있습니다.",
        )

    try:
        addresses = {
            item[4][0]
            for item in resolver(hostname, parsed.port or parsed.scheme)
            if item[4]
        }
    except (OSError, socket.gaierror) as error:
        raise CaptureError(
            "URL_DNS_FAILED",
            "URL 호스트 주소를 확인할 수 없습니다.",
        ) from error
    if not addresses:
        raise CaptureError(
            "URL_DNS_FAILED",
            "URL 호스트 주소를 확인할 수 없습니다.",
        )
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as error:
            raise CaptureError(
                "URL_DNS_INVALID",
                "URL 호스트가 유효한 IP 주소로 확인되지 않았습니다.",
            ) from error
        if not ip.is_global:
            raise CaptureError(
                "URL_PRIVATE_IP_BLOCKED",
                "내부·사설·예약 IP 주소에는 접근할 수 없습니다.",
            )
    return candidate


def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    length = response.headers.get("content-length")
    if length:
        try:
            if int(length) > max_bytes:
                raise CaptureError(
                    "RESPONSE_TOO_LARGE",
                    "허용된 다운로드 용량을 초과했습니다.",
                )
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise CaptureError(
                "RESPONSE_TOO_LARGE",
                "허용된 다운로드 용량을 초과했습니다.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_public_bytes(
    url: str,
    *,
    max_bytes: int,
    expected_prefix: str,
    resolver: Resolver = socket.getaddrinfo,
    client: httpx.Client | None = None,
) -> tuple[str, bytes, str]:
    """Fetch a bounded public resource and revalidate every redirect."""

    current = validate_public_url(url, resolver)
    owns_client = client is None
    active_client = client or httpx.Client()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with active_client.stream(
                    "GET",
                    current,
                    headers={"User-Agent": USER_AGENT},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise CaptureError(
                                "REDIRECT_LOCATION_MISSING",
                                "이동할 URL이 없는 리다이렉트 응답입니다.",
                            )
                        current = validate_public_url(
                            urljoin(current, location),
                            resolver,
                        )
                        continue
                    response.raise_for_status()
                    content_type_header = response.headers.get("content-type", "")
                    media_type = content_type_header.split(";", 1)[0].strip().lower()
                    if not media_type.startswith(expected_prefix):
                        raise CaptureError(
                            "UNSUPPORTED_CONTENT_TYPE",
                            f"지원하지 않는 콘텐츠 형식입니다: {media_type or '-'}",
                        )
                    return (
                        str(response.url),
                        _read_limited(response, max_bytes),
                        content_type_header,
                    )
            except CaptureError:
                raise
            except httpx.TimeoutException as error:
                raise CaptureError(
                    "FETCH_TIMEOUT",
                    "URL 다운로드 시간이 초과되었습니다.",
                ) from error
            except httpx.HTTPError as error:
                raise CaptureError(
                    "FETCH_FAILED",
                    "URL을 내려받지 못했습니다.",
                ) from error
        raise CaptureError(
            "TOO_MANY_REDIRECTS",
            "URL 리다이렉트 횟수가 제한을 초과했습니다.",
        )
    finally:
        if owns_client:
            active_client.close()


def _decode_html(content: bytes, content_type: str) -> str:
    """Decode HTML using its declared charset before Korean legacy fallbacks."""

    candidates: list[str] = []
    header_match = _CHARSET_PATTERN.search(content_type)
    if header_match:
        candidates.append(header_match.group(1))

    # Charset declarations appear near the start of an HTML document and are
    # ASCII-compatible, so this inspection does not corrupt Korean text.
    html_head = content[:8192].decode("ascii", errors="ignore")
    meta_match = _CHARSET_PATTERN.search(html_head)
    if meta_match:
        candidates.append(meta_match.group(1))

    candidates.extend(("utf-8-sig", "cp949", "euc-kr"))
    attempted: set[str] = set()
    for charset in candidates:
        normalized = charset.strip().lower()
        if not normalized or normalized in attempted:
            continue
        attempted.add(normalized)
        try:
            return content.decode(normalized, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


def extract_page_content(html_text: str, base_url: str) -> PageCapture:
    """Extract a readable title, body and unique public image URLs."""

    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()

    title_node = soup.select_one('meta[property="og:title"]')
    if title_node and title_node.get("content"):
        title = str(title_node["content"]).strip()
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)
    else:
        title = ""

    container = None
    for selector in (
        ".se-main-container",
        "#postViewArea",
        ".post-view",
        'div[id^="post-view"]',
        "article",
        "main",
        "body",
    ):
        container = soup.select_one(selector)
        if container is not None:
            break
    body_text = container.get_text("\n", strip=True) if container else ""
    body_lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    body_text = "\n".join(body_lines)

    image_urls: list[str] = []
    seen: set[str] = set()
    image_scope = container if container is not None else soup
    for node in image_scope.select("img"):
        value = (
            node.get("data-lazy-src")
            or node.get("data-src")
            or node.get("data-original")
            or node.get("src")
        )
        if not value:
            continue
        absolute = urljoin(base_url, str(value).strip())
        parsed_image = urlsplit(absolute)
        if parsed_image.scheme not in {"http", "https"}:
            continue
        path = parsed_image.path.lower()
        host = (parsed_image.hostname or "").lower()
        if (
            (host == "ssl.pstatic.net" and path.startswith("/static/"))
            or path.endswith(("/blank.gif", "/spacer.gif"))
            or path == "/profileimage"
        ):
            continue
        if absolute not in seen:
            seen.add(absolute)
            image_urls.append(absolute)

    return PageCapture(
        requested_url=base_url,
        final_url=base_url,
        title=title,
        body_text=body_text,
        image_urls=tuple(image_urls),
    )


def collect_page(
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
    client: httpx.Client | None = None,
) -> PageCapture:
    """Collect a page, including Naver's mainFrame document when present."""

    final_url, content, content_type = fetch_public_bytes(
        url,
        max_bytes=MAX_HTML_BYTES,
        expected_prefix="text/html",
        resolver=resolver,
        client=client,
    )
    html_text = _decode_html(content, content_type)
    soup = BeautifulSoup(html_text, "html.parser")
    frame = soup.select_one("iframe#mainFrame")
    if frame and frame.get("src"):
        frame_url = urljoin(final_url, str(frame["src"]))
        final_url, content, content_type = fetch_public_bytes(
            frame_url,
            max_bytes=MAX_HTML_BYTES,
            expected_prefix="text/html",
            resolver=resolver,
            client=client,
        )
        html_text = _decode_html(content, content_type)
    extracted = extract_page_content(html_text, final_url)
    return PageCapture(
        requested_url=url,
        final_url=final_url,
        title=extracted.title,
        body_text=extracted.body_text,
        image_urls=extracted.image_urls,
    )


def fetch_image(
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
    client: httpx.Client | None = None,
) -> tuple[str, bytes, str]:
    """Fetch one bounded public image."""

    return fetch_public_bytes(
        url,
        max_bytes=MAX_IMAGE_BYTES,
        expected_prefix="image/",
        resolver=resolver,
        client=client,
    )

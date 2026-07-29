import socket

import pytest

from web_capture import CaptureError, _decode_html, extract_page_content, validate_public_url


def test_decode_html_honors_declared_korean_charset() -> None:
    html = "<html><body>여성 건강 정보</body></html>".encode("cp949")

    assert _decode_html(html, "text/html; charset=EUC-KR") == (
        "<html><body>여성 건강 정보</body></html>"
    )


def test_decode_html_uses_meta_charset_when_header_is_missing() -> None:
    html = '<meta charset="cp949"><p>건강기능식품</p>'.encode("cp949")

    assert _decode_html(html, "text/html") == '<meta charset="cp949"><p>건강기능식품</p>'


def resolver_for(address: str):
    def resolve(host: str, port: int | str):
        del host, port
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, 443),
            )
        ]

    return resolve


def test_validate_public_url_accepts_public_https() -> None:
    url = "https://example.com/post"

    assert validate_public_url(url, resolver_for("93.184.216.34")) == url


@pytest.mark.parametrize(
    "url,address,code",
    [
        ("file:///etc/passwd", "93.184.216.34", "URL_SCHEME_BLOCKED"),
        ("https://localhost/admin", "127.0.0.1", "URL_PRIVATE_HOST_BLOCKED"),
        ("https://example.com", "127.0.0.1", "URL_PRIVATE_IP_BLOCKED"),
        ("https://example.com:8080", "93.184.216.34", "URL_PORT_BLOCKED"),
    ],
)
def test_validate_public_url_blocks_unsafe_targets(
    url: str,
    address: str,
    code: str,
) -> None:
    with pytest.raises(CaptureError) as captured:
        validate_public_url(url, resolver_for(address))

    assert captured.value.code == code


def test_extract_page_content_prefers_article_and_deduplicates_images() -> None:
    html = """
    <html>
      <head><meta property="og:title" content="시험 광고"></head>
      <body>
        <nav>메뉴 문구</nav>
        <div class="se-main-container">
          <p>질병을 치료합니다.</p>
          <img src="/image/a.jpg">
          <img data-lazy-src="/image/a.jpg">
          <img data-src="https://cdn.example.com/b.png">
          <img src="https://ssl.pstatic.net/static/common/images/bu_bar.gif">
        </div>
      </body>
    </html>
    """

    result = extract_page_content(html, "https://blog.example.com/post/1")

    assert result.title == "시험 광고"
    assert result.body_text == "질병을 치료합니다."
    assert result.image_urls == (
        "https://blog.example.com/image/a.jpg",
        "https://cdn.example.com/b.png",
    )

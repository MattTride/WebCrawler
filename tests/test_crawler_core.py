"""Unit tests for crawler_core: the tkinter-free fetch/parse logic."""
import pytest

import crawler_core
from crawler_core import (
    PageParser,
    clean,
    dedupe,
    fetch_url,
    first_srcset_url,
    looks_like_video,
    meta_encoding,
    normalize_url,
    safe_filename_from_url,
    url_suffix,
)


# --- normalize_url ---------------------------------------------------------

def test_normalize_url_adds_https_when_scheme_missing():
    assert normalize_url("example.com/path") == "https://example.com/path"


def test_normalize_url_keeps_existing_scheme():
    assert normalize_url("http://example.com") == "http://example.com"
    assert normalize_url("  https://example.com  ") == "https://example.com"


@pytest.mark.parametrize("bad", ["", "   ", "ftp://example.com", "https://"])
def test_normalize_url_rejects_invalid(bad):
    with pytest.raises(ValueError):
        normalize_url(bad)


# --- small helpers ---------------------------------------------------------

def test_clean_collapses_whitespace_and_handles_none():
    assert clean("  a\n\t b  ") == "a b"
    assert clean("") == ""
    assert clean(None) == ""


def test_dedupe_keeps_first_and_skips_empty():
    items = [{"u": "a"}, {"u": "a"}, {"u": ""}, {"u": "b"}]
    assert dedupe(items, "u") == [{"u": "a"}, {"u": "b"}]


def test_url_suffix_and_looks_like_video():
    assert url_suffix("http://x/clip.MP4?t=1") == ".mp4"
    assert looks_like_video("http://x/clip.mp4") is True
    assert looks_like_video("http://x/page.html") is False


def test_first_srcset_url_takes_first_candidate():
    assert first_srcset_url("a.jpg 1x, b.jpg 2x") == "a.jpg"
    assert first_srcset_url("") == ""


def test_safe_filename_from_url():
    assert safe_filename_from_url("http://x/dir/pic.jpg") == "pic.jpg"
    assert safe_filename_from_url("http://x/") == "media"
    assert len(safe_filename_from_url("http://x/" + "a" * 300 + ".jpg")) <= 120


def test_meta_encoding_reads_charset():
    assert meta_encoding(b'<meta charset="gbk">') == "gbk"
    assert meta_encoding(b"<html><body>no charset</body></html>") is None


# --- PageParser ------------------------------------------------------------

SAMPLE_HTML = '''
<html><head>
  <title>  Test  Page </title>
  <meta name="description" content="A sample description.">
  <meta property="og:image" content="https://cdn.example.com/og.png">
</head><body>
  <h1>Main Heading</h1>
  <h2>Sub</h2>
  <p>Hello <b>world</b>.</p>
  <script>var secretscript = 1;</script>
  <style>.box{color:tomato}</style>
  <a href="/about">About</a>
  <a href="https://other.com/x">External</a>
  <a href="javascript:void(0)">JS</a>
  <a href="mailto:a@b.com">Mail</a>
  <img src="/img/a.png" alt="Pic A">
  <img data-src="/img/lazy.png" alt="Lazy">
  <img srcset="/img/s1.png 1x, /img/s2.png 2x" alt="Srcset">
  <video src="/media/clip.mp4" poster="/media/poster.jpg"></video>
  <a href="/files/movie.webm">Download movie</a>
</body></html>
'''


@pytest.fixture
def parsed():
    p = PageParser("https://example.com/page/")
    p.feed(SAMPLE_HTML)
    p.close()
    return p


def test_parser_title_and_description(parsed):
    assert parsed.title == "Test Page"
    assert parsed.description == "A sample description."


def test_parser_headings(parsed):
    levels = [(h["level"], h["text"]) for h in parsed.headings]
    assert ("H1", "Main Heading") in levels
    assert ("H2", "Sub") in levels


def test_parser_links_resolve_and_filter(parsed):
    urls = [link["url"] for link in parsed.links]
    assert "https://example.com/about" in urls       # relative -> absolute
    assert "https://other.com/x" in urls
    assert not any(u.startswith(("javascript:", "mailto:")) for u in urls)


def test_parser_images_cover_all_sources(parsed):
    srcs = {img["src"] for img in parsed.images}
    assert {
        "https://example.com/img/a.png",       # src
        "https://example.com/img/lazy.png",    # data-src fallback
        "https://example.com/img/s1.png",      # srcset first candidate
        "https://cdn.example.com/og.png",      # og:image meta
        "https://example.com/media/poster.jpg",  # video poster
    } <= srcs


def test_parser_videos_from_tag_and_link(parsed):
    urls = {v["url"] for v in parsed.videos}
    assert "https://example.com/media/clip.mp4" in urls
    assert "https://example.com/files/movie.webm" in urls


def test_parser_body_text_excludes_script_and_style(parsed):
    text = parsed.body_text
    assert "Hello" in text
    assert "secretscript" not in text
    assert "tomato" not in text


# --- fetch_url (network mocked) -------------------------------------------

class _FakeHeaders:
    def get(self, key, default=""):
        return "text/html; charset=utf-8" if key == "Content-Type" else default

    def get_content_charset(self):
        return "utf-8"


class _FakeResponse:
    status = 200
    reason = "OK"
    headers = _FakeHeaders()

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _n=-1):
        return self._body

    def geturl(self):
        return "https://example.com/"


def test_fetch_url_decodes_and_parses(monkeypatch):
    body = b"<html><head><title>Hi</title></head><body><a href='/x'>L</a></body></html>"
    monkeypatch.setattr(
        crawler_core.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse(body),
    )
    result = fetch_url("example.com")
    assert result.status_code == 200
    assert result.reason == "OK"
    assert result.title == "Hi"
    assert result.encoding == "utf-8"
    assert result.truncated is False
    assert result.bytes_read == len(body)
    assert any(link["url"] == "https://example.com/x" for link in result.links)

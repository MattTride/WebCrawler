"""Unit tests for crawler_core: the tkinter-free fetch/parse logic."""
import urllib.error

import pytest

import crawler_core
from crawler_core import (
    PageParser,
    available_download_path,
    build_link_stats,
    clean,
    dedupe,
    estimate_word_count,
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


def test_available_download_path_avoids_disk_and_batch_conflicts(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"existing")
    reserved = set()
    first = available_download_path(tmp_path, "photo.jpg", reserved)
    second = available_download_path(tmp_path, "photo.jpg", reserved)
    assert first.name == "photo-2.jpg"
    assert second.name == "photo-3.jpg"


def test_meta_encoding_reads_charset():
    assert meta_encoding(b'<meta charset="gbk">') == "gbk"
    assert meta_encoding(b"<html><body>no charset</body></html>") is None


def test_estimate_word_count_handles_mixed_chinese_and_latin_text():
    assert estimate_word_count("你好 Codex studio-2") == 4


def test_build_link_stats_separates_internal_external_and_protocols():
    links = [
        {"url": "https://example.com/a"},
        {"url": "http://example.com/b"},
        {"url": "https://other.com/c"},
    ]
    assert build_link_stats(links, "https://example.com/") == {
        "total": 3, "internal": 2, "external": 1, "https": 2, "http": 1,
    }


# --- PageParser ------------------------------------------------------------

SAMPLE_HTML = '''
<html lang="zh-CN"><head>
  <title>  Test  Page </title>
  <meta name="description" content="A sample description.">
  <meta name="author" content="Crawl Team">
  <meta name="keywords" content="crawler, analysis">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta property="og:site_name" content="Example Studio">
  <meta property="og:type" content="article">
  <meta property="article:published_time" content="2026-08-01">
  <meta property="og:image" content="https://cdn.example.com/og.png">
  <link rel="canonical" href="/canonical">
  <link rel="stylesheet" href="/assets/site.css">
</head><body>
  <h1>Main Heading</h1>
  <h2>Sub</h2>
  <p>Hello <b>world</b>.</p>
  <script src="/assets/app.js">var secretscript = 1;</script>
  <style>.box{color:tomato}</style>
  <a href="/about">About</a>
  <a href="https://other.com/x">External</a>
  <a href="javascript:void(0)">JS</a>
  <a href="mailto:a@b.com">Mail</a>
  <a href="tel:+65-6123-4567">Call</a>
  <img src="/img/a.png" alt="Pic A">
  <img data-src="/img/lazy.png" alt="Lazy">
  <img srcset="/img/s1.png 1x, /img/s2.png 2x" alt="Srcset">
  <video src="/media/clip.mp4" poster="/media/poster.jpg"></video>
  <a href="/files/movie.webm">Download movie</a>
  <iframe src="/embed/demo"></iframe>
  <form action="/login" method="post">
    <input name="user"><input type="password" name="password"><textarea name="note"></textarea>
  </form>
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


def test_parser_collects_metadata_resources_contacts_and_forms(parsed):
    assert parsed.language == "zh-CN"
    assert parsed.canonical == "https://example.com/canonical"
    assert parsed.metadata["author"] == "Crawl Team"
    assert parsed.metadata["og:site_name"] == "Example Studio"
    assert parsed.resources == {
        "scripts": ["https://example.com/assets/app.js"],
        "stylesheets": ["https://example.com/assets/site.css"],
        "iframes": ["https://example.com/embed/demo"],
    }
    assert parsed.contacts["emails"] == ["a@b.com"]
    assert parsed.contacts["phones"] == ["+65-6123-4567"]
    assert parsed.forms == [{
        "action": "https://example.com/login",
        "method": "POST",
        "inputs": 3,
        "password_fields": 1,
    }]


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
    result = fetch_url("example.com", respect_robots=False)
    assert result.status_code == 200
    assert result.reason == "OK"
    assert result.title == "Hi"
    assert result.encoding == "utf-8"
    assert result.truncated is False
    assert result.bytes_read == len(body)
    assert any(link["url"] == "https://example.com/x" for link in result.links)
    assert result.word_count == 1
    assert result.reading_minutes == 1
    assert result.page_info["domain"] == "example.com"
    assert result.link_stats["internal"] == 1
    assert result.seo_report["score"] >= 0
    assert len(result.seo_report["checks"]) == 9


# --- robots.txt and retries ------------------------------------------------

def test_fetch_url_blocked_by_robots(monkeypatch):
    monkeypatch.setattr(crawler_core, "robots_allowed", lambda *a, **k: False)
    with pytest.raises(crawler_core.RobotsDisallowed):
        fetch_url("https://example.com/")


def test_fetch_url_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("temporarily down")
        return _FakeResponse(b"<title>OK</title>")

    monkeypatch.setattr(crawler_core.time, "sleep", lambda *_: None)
    monkeypatch.setattr(crawler_core.urllib.request, "urlopen", flaky)
    result = fetch_url("example.com", respect_robots=False)
    assert result.title == "OK"
    assert calls["n"] == 3  # 1 attempt + 2 retries (RETRIES=2)


def test_fetch_url_raises_friendly_message_after_retries(monkeypatch):
    monkeypatch.setattr(crawler_core.time, "sleep", lambda *_: None)

    def always_timeout(req, timeout=None):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(crawler_core.urllib.request, "urlopen", always_timeout)
    with pytest.raises(crawler_core.FetchError) as exc:
        fetch_url("example.com", respect_robots=False)
    assert "超时" in str(exc.value)


def test_robots_allowed_fails_open_when_unreachable(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("no robots.txt here")

    monkeypatch.setattr(crawler_core.urllib.request, "urlopen", boom)
    assert crawler_core.robots_allowed("https://example.com/page") is True


def test_robots_blocks_disallowed_path(monkeypatch):
    robots = "User-agent: *\nDisallow: /private/"
    monkeypatch.setattr(
        crawler_core.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse(robots.encode("utf-8")),
    )
    assert crawler_core.robots_allowed("https://example.com/private/secret") is False
    assert crawler_core.robots_allowed("https://example.com/public/ok") is True


# --- markdown export -------------------------------------------------------

def test_result_to_markdown_renders_sections():
    result = crawler_core.CrawlResult(
        requested_url="https://example.com",
        final_url="https://example.com/",
        status_code=200, reason="OK",
        content_type="text/html", encoding="utf-8",
        title="My Page", description="A page.",
        headings=[{"level": "H1", "text": "Top"}, {"level": "H2", "text": "Sub"}],
        links=[{"text": "Home", "url": "https://example.com/home"}],
        images=[{"alt": "Logo", "src": "https://example.com/logo.png"}],
        videos=[{"text": "Clip", "url": "https://example.com/clip.mp4"}],
        text="Body text here.",
        html_preview="<html>", bytes_read=123, truncated=False,
        fetched_at="2026-05-30 10:00:00",
        word_count=3, reading_minutes=1,
        page_info={"domain": "example.com", "language": "zh-CN", "canonical": "https://example.com/"},
        link_stats={"internal": 1, "external": 0},
        seo_report={"score": 88, "grade": "优秀", "issues": [], "checks": []},
    )
    md = crawler_core.result_to_markdown(result)
    assert md.startswith("# My Page")
    assert "A page." in md
    assert "- H1 Top" in md
    assert "  - H2 Sub" in md                                   # nested heading
    assert "[Home](https://example.com/home)" in md
    assert "![Logo](https://example.com/logo.png)" in md        # image syntax
    assert "[Clip](https://example.com/clip.mp4)" in md
    assert "## 正文" in md and "Body text here." in md
    assert "SEO 健康度：88 / 100" in md
    assert "正文字数：3" in md


# --- raw server response ---------------------------------------------------

def test_fetch_raw_response_includes_status_headers_body(monkeypatch):
    class FullHeaders:
        def get(self, key, default=""):
            return {"Content-Type": "text/html; charset=utf-8"}.get(key, default)

        def get_content_charset(self):
            return "utf-8"

        def items(self):
            return [("Content-Type", "text/html; charset=utf-8"), ("Server", "nginx")]

    class RawResp(_FakeResponse):
        headers = FullHeaders()

    monkeypatch.setattr(
        crawler_core.urllib.request, "urlopen",
        lambda req, timeout=None: RawResp(b"<html>hello-raw</html>"),
    )
    out = crawler_core.fetch_raw_response("example.com", respect_robots=False)
    assert out.startswith("HTTP 200 OK")
    assert "Server: nginx" in out
    assert "hello-raw" in out

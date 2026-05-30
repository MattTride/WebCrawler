"""Crawl core: fetch a URL and parse it into structured data.

Pure logic with no tkinter dependency, so it can be unit-tested and reused
(e.g. by a command-line interface) without launching the GUI.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path


MAX_BYTES = 1_500_000
PREVIEW_MAX_BYTES = 800_000
TIMEOUT = 15
DOWNLOAD_TIMEOUT = 45
PREVIEW_TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 MiniCrawler/1.0"
RETRIES = 2
RETRY_BACKOFF = 0.6
ROBOTS_TIMEOUT = 8
ROBOTS_MAX_BYTES = 200_000
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv", ".ogv", ".3gp", ".flv"}
VIDEO_META_KEYS = {
    "og:video", "og:video:url", "og:video:secure_url", "og:video:iframe",
    "twitter:player:stream",
}
IMAGE_META_KEYS = {"og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("请输入一个网址。")
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme:
        url = f"https://{url}"
        parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("网址格式不正确，目前只支持 http 和 https。")
    return url


def meta_encoding(raw: bytes) -> str | None:
    match = re.search(br"<meta[^>]+charset=[\"']?\s*([a-zA-Z0-9._-]+)", raw[:4096], re.I)
    return match.group(1).decode("ascii", "ignore") if match else None


def dedupe(items: list[dict[str, str]], key: str) -> list[dict[str, str]]:
    seen, out = set(), []
    for item in items:
        value = item.get(key, "")
        if value and value not in seen:
            seen.add(value)
            out.append(item)
    return out


def url_suffix(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return Path(urllib.parse.unquote(path)).suffix.lower()


def looks_like_video(url: str) -> bool:
    return url_suffix(url) in VIDEO_EXTENSIONS


def first_srcset_url(value: str) -> str:
    if not value:
        return ""
    first = value.split(",", 1)[0].strip()
    return first.split(" ", 1)[0].strip()


def safe_filename_from_url(url: str, fallback: str = "media") -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(urllib.parse.unquote(parsed.path)).name or fallback
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    if not name:
        name = fallback
    if "." not in name and looks_like_video(url):
        name += url_suffix(url)
    return name[:120]


@dataclass
class CrawlResult:
    requested_url: str
    final_url: str
    status_code: int | None
    reason: str
    content_type: str
    encoding: str
    title: str
    description: str
    headings: list[dict[str, str]]
    links: list[dict[str, str]]
    images: list[dict[str, str]]
    videos: list[dict[str, str]]
    text: str
    html_preview: str
    bytes_read: int
    truncated: bool
    fetched_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class PageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts, self.text_parts = [], []
        self.description = ""
        self.links, self.images, self.videos, self.headings = [], [], [], []
        self.in_title = False
        self.in_video = False
        self.skip_depth = 0
        self.current_link = None
        self.current_heading = None

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        attrs = {name.lower(): value or "" for name, value in attrs}
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = attrs.get("name", "").lower()
            prop = attrs.get("property", "").lower()
            content = attrs.get("content", "").strip()
            if not self.description and (name == "description" or prop == "og:description"):
                self.description = clean(attrs.get("content", ""))
            if content and (name in IMAGE_META_KEYS or prop in IMAGE_META_KEYS):
                self.images.append({"alt": prop or name or "页面图片", "src": urllib.parse.urljoin(self.base_url, content)})
            if content and (name in VIDEO_META_KEYS or prop in VIDEO_META_KEYS):
                self.add_video(content, prop or name or "视频")
        elif tag == "a":
            href = attrs.get("href", "").strip()
            if href and not href.lower().startswith(("javascript:", "mailto:", "tel:")):
                url = urllib.parse.urljoin(self.base_url, href)
                self.current_link = {"url": url, "parts": []}
                if looks_like_video(url):
                    self.add_video(url, "视频链接")
        elif tag == "img":
            src = (
                attrs.get("src", "").strip()
                or attrs.get("data-src", "").strip()
                or attrs.get("data-original", "").strip()
                or attrs.get("data-lazy-src", "").strip()
                or first_srcset_url(attrs.get("srcset", ""))
                or first_srcset_url(attrs.get("data-srcset", ""))
            )
            if src:
                self.images.append({"alt": clean(attrs.get("alt", "")), "src": urllib.parse.urljoin(self.base_url, src)})
        elif tag == "video":
            self.in_video = True
            src = attrs.get("src", "").strip()
            if src:
                self.add_video(src, clean(attrs.get("title", "")) or "视频")
            poster = attrs.get("poster", "").strip()
            if poster:
                self.images.append({"alt": "视频封面", "src": urllib.parse.urljoin(self.base_url, poster)})
        elif tag == "source":
            src = attrs.get("src", "").strip()
            media_type = attrs.get("type", "").lower()
            if src and (self.in_video or media_type.startswith("video/") or looks_like_video(src)):
                self.add_video(src, media_type or "视频")
        elif tag in HEADINGS:
            self.current_heading = {"level": tag.upper(), "parts": []}

    def handle_data(self, data: str):
        if self.skip_depth or not data:
            return
        if self.in_title:
            self.title_parts.append(data)
        if self.current_link is not None:
            self.current_link["parts"].append(data)
        if self.current_heading is not None:
            self.current_heading["parts"].append(data)
        text = clean(data)
        if text and not self.in_title:
            self.text_parts.append(text)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag == "title":
            self.in_title = False
        elif tag == "a" and self.current_link:
            text = clean(" ".join(self.current_link["parts"])) or "无文字链接"
            self.links.append({"text": text[:240], "url": self.current_link["url"]})
            self.current_link = None
        elif tag == "video":
            self.in_video = False
        elif tag in HEADINGS and self.current_heading:
            text = clean(" ".join(self.current_heading["parts"]))
            if text:
                self.headings.append({"level": self.current_heading["level"], "text": text[:240]})
            self.current_heading = None

    def add_video(self, src: str, label: str = "视频"):
        url = urllib.parse.urljoin(self.base_url, src)
        text = clean(label) or Path(urllib.parse.urlparse(url).path).name or "视频"
        self.videos.append({"text": text[:240], "url": url})

    @property
    def title(self) -> str:
        return clean(" ".join(self.title_parts))

    @property
    def body_text(self) -> str:
        return clean(" ".join(self.text_parts))[:20_000]


class FetchError(Exception):
    """A network-level failure carrying a human-friendly Chinese message."""


class RobotsDisallowed(FetchError):
    """Raised when robots.txt forbids fetching the target URL."""


def friendly_network_error(err: Exception) -> str:
    text = str(getattr(err, "reason", err)) or err.__class__.__name__
    low = text.lower()
    if "timed out" in low or isinstance(getattr(err, "reason", None), TimeoutError):
        return "请求超时，目标网站响应太慢，请稍后重试。"
    if "getaddrinfo" in low or "name or service" in low or "nodename nor servname" in low:
        return "无法解析域名，请检查网址是否正确。"
    if "connection refused" in low:
        return "目标服务器拒绝连接。"
    if "ssl" in low or "certificate" in low or "cert" in low:
        return f"HTTPS 安全连接失败：{text}"
    return f"网络错误：{text}"


def robots_allowed(url: str, user_agent: str = USER_AGENT, timeout: float = ROBOTS_TIMEOUT) -> bool:
    """Best-effort robots.txt check. Fails open (returns True) when robots.txt
    cannot be read, matching common crawler behaviour."""
    parts = urllib.parse.urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(ROBOTS_MAX_BYTES).decode("utf-8", "replace")
    except Exception:
        return True
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(body.splitlines())
    return parser.can_fetch(user_agent, url)


def fetch_url(url: str, respect_robots: bool = True) -> CrawlResult:
    url = normalize_url(url)
    if respect_robots and not robots_allowed(url):
        raise RobotsDisallowed("该网站的 robots.txt 不允许抓取此页面。")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
    })
    final_url, status, reason, content_type, charset = url, None, "", "", None
    raw = b""
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read(MAX_BYTES + 1)
                final_url, status, reason = resp.geturl(), getattr(resp, "status", None), getattr(resp, "reason", "")
                content_type, charset = resp.headers.get("Content-Type", ""), resp.headers.get_content_charset()
            break
        except urllib.error.HTTPError as err:
            raw = err.read(MAX_BYTES + 1)
            final_url, status, reason = err.geturl(), err.code, err.reason or ""
            content_type, charset = err.headers.get("Content-Type", ""), err.headers.get_content_charset()
            break
        except urllib.error.URLError as err:
            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            raise FetchError(friendly_network_error(err)) from err

    truncated = len(raw) > MAX_BYTES
    raw = raw[:MAX_BYTES]
    encoding = charset or meta_encoding(raw) or "utf-8"
    try:
        html = raw.decode(encoding, "replace")
    except LookupError:
        encoding = "utf-8"
        html = raw.decode(encoding, "replace")
    parser = PageParser(final_url)
    parser.feed(html)
    parser.close()
    return CrawlResult(
        requested_url=url,
        final_url=final_url,
        status_code=status,
        reason=reason,
        content_type=content_type or "未知",
        encoding=encoding,
        title=parser.title or "未发现标题",
        description=parser.description,
        headings=parser.headings[:80],
        links=dedupe(parser.links, "url")[:500],
        images=dedupe(parser.images, "src")[:300],
        videos=dedupe(parser.videos, "url")[:300],
        text=parser.body_text,
        html_preview=html[:30_000],
        bytes_read=len(raw),
        truncated=truncated,
        fetched_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def download_file(url: str, path: Path) -> int:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    })
    written = 0
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        with path.open("wb") as file:
            while True:
                chunk = resp.read(128 * 1024)
                if not chunk:
                    break
                file.write(chunk)
                written += len(chunk)
    return written


def fetch_preview_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=PREVIEW_TIMEOUT) as resp:
        return resp.read(PREVIEW_MAX_BYTES + 1)[:PREVIEW_MAX_BYTES]

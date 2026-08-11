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
from dataclasses import asdict, dataclass, field
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


def available_download_path(directory: Path, filename: str, reserved: set[Path] | None = None) -> Path:
    """Return a non-conflicting destination for a download batch."""
    reserved = reserved if reserved is not None else set()
    source = Path(filename)
    stem = source.stem or "media"
    suffix = source.suffix
    candidate = directory / f"{stem}{suffix}"
    number = 2
    while candidate.exists() or candidate in reserved:
        candidate = directory / f"{stem}-{number}{suffix}"
        number += 1
    reserved.add(candidate)
    return candidate


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
    word_count: int = 0
    reading_minutes: int = 0
    page_info: dict[str, str] = field(default_factory=dict)
    link_stats: dict[str, int] = field(default_factory=dict)
    contacts: dict[str, list[str]] = field(default_factory=dict)
    forms: list[dict[str, object]] = field(default_factory=list)
    resources: dict[str, list[str]] = field(default_factory=dict)
    seo_report: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def result_to_markdown(result: CrawlResult) -> str:
    """Render a CrawlResult as human-readable Markdown."""
    status = f"{result.status_code} {result.reason}".strip() if result.status_code else "无状态码"
    lines = [f"# {result.title}", ""]
    if result.description:
        lines += [result.description, ""]
    lines += [
        f"- 抓取时间：{result.fetched_at}",
        f"- 请求地址：{result.requested_url}",
        f"- 最终地址：{result.final_url}",
        f"- 状态：{status}",
        f"- 正文字数：{result.word_count}",
        f"- 预计阅读：{result.reading_minutes} 分钟",
        "",
    ]
    if result.seo_report:
        lines += [
            "## 页面情报",
            "",
            f"- SEO 健康度：{result.seo_report.get('score', 0)} / 100（{result.seo_report.get('grade', '')}）",
            f"- 站点域名：{result.page_info.get('domain', '')}",
            f"- 页面语言：{result.page_info.get('language', '') or '未声明'}",
            f"- Canonical：{result.page_info.get('canonical', '') or '未声明'}",
            f"- 内部链接：{result.link_stats.get('internal', 0)}",
            f"- 外部链接：{result.link_stats.get('external', 0)}",
            f"- 表单数量：{len(result.forms)}",
            "",
        ]
        issues = result.seo_report.get("issues", [])
        if issues:
            lines.append("### 优化建议")
            lines.extend(f"- {issue}" for issue in issues)
            lines.append("")
    if result.headings:
        lines.append("## 标题结构")
        for h in result.headings:
            indent = "  " * (int(h["level"][1]) - 1)
            lines.append(f"{indent}- {h['level']} {h['text']}")
        lines.append("")
    for name, items, label_key, url_key, is_image in (
        ("链接", result.links, "text", "url", False),
        ("图片", result.images, "alt", "src", True),
        ("视频", result.videos, "text", "url", False),
    ):
        if not items:
            continue
        lines.append(f"## {name}（{len(items)}）")
        for item in items:
            url = item.get(url_key, "")
            label = clean(item.get(label_key, "")) or url
            lines.append(f"- {'!' if is_image else ''}[{label}]({url})")
        lines.append("")
    if result.text:
        lines += ["## 正文", "", result.text, ""]
    return "\n".join(lines).rstrip() + "\n"


class PageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts, self.text_parts = [], []
        self.description = ""
        self.links, self.images, self.videos, self.headings = [], [], [], []
        self.metadata: dict[str, str] = {}
        self.resources = {"scripts": [], "stylesheets": [], "iframes": []}
        self.contacts = {"emails": [], "phones": []}
        self.forms: list[dict[str, object]] = []
        self.language = ""
        self.canonical = ""
        self.in_title = False
        self.in_video = False
        self.skip_depth = 0
        self.current_link = None
        self.current_heading = None
        self.current_form = None

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        attrs = {name.lower(): value or "" for name, value in attrs}
        if tag == "script":
            src = attrs.get("src", "").strip()
            if src:
                self.resources["scripts"].append(urllib.parse.urljoin(self.base_url, src))
            self.skip_depth += 1
            return
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "html":
            self.language = clean(attrs.get("lang", ""))
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = attrs.get("name", "").lower()
            prop = attrs.get("property", "").lower()
            http_equiv = attrs.get("http-equiv", "").lower()
            content = attrs.get("content", "").strip()
            meta_key = name or prop or http_equiv
            if meta_key and content and meta_key not in self.metadata:
                self.metadata[meta_key] = clean(content)
            if not self.description and (name == "description" or prop == "og:description"):
                self.description = clean(attrs.get("content", ""))
            if content and (name in IMAGE_META_KEYS or prop in IMAGE_META_KEYS):
                self.images.append({"alt": prop or name or "页面图片", "src": urllib.parse.urljoin(self.base_url, content)})
            if content and (name in VIDEO_META_KEYS or prop in VIDEO_META_KEYS):
                self.add_video(content, prop or name or "视频")
        elif tag == "link":
            href = attrs.get("href", "").strip()
            rel = {part.lower() for part in attrs.get("rel", "").split()}
            if href and "canonical" in rel:
                self.canonical = urllib.parse.urljoin(self.base_url, href)
            if href and "stylesheet" in rel:
                self.resources["stylesheets"].append(urllib.parse.urljoin(self.base_url, href))
        elif tag == "a":
            href = attrs.get("href", "").strip()
            lower_href = href.lower()
            if lower_href.startswith("mailto:"):
                email = href[7:].split("?", 1)[0].strip()
                if email:
                    self.contacts["emails"].append(email)
            elif lower_href.startswith("tel:"):
                phone = href[4:].split("?", 1)[0].strip()
                if phone:
                    self.contacts["phones"].append(phone)
            elif href and not lower_href.startswith("javascript:"):
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
        elif tag == "iframe":
            src = attrs.get("src", "").strip()
            if src:
                self.resources["iframes"].append(urllib.parse.urljoin(self.base_url, src))
        elif tag == "form":
            self.current_form = {
                "action": urllib.parse.urljoin(self.base_url, attrs.get("action", "") or self.base_url),
                "method": (attrs.get("method", "get") or "get").upper(),
                "inputs": 0,
                "password_fields": 0,
            }
        elif tag in {"input", "select", "textarea"} and self.current_form is not None:
            self.current_form["inputs"] = int(self.current_form["inputs"]) + 1
            if tag == "input" and attrs.get("type", "text").lower() == "password":
                self.current_form["password_fields"] = int(self.current_form["password_fields"]) + 1
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
        elif tag == "form" and self.current_form is not None:
            self.forms.append(self.current_form)
            self.current_form = None
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


def estimate_word_count(text: str) -> int:
    """Estimate readable units for mixed Chinese and Latin text."""
    chinese = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text or ""))
    latin = len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*", text or ""))
    return chinese + latin


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def collect_contacts(parser: PageParser, text: str) -> dict[str, list[str]]:
    emails = list(parser.contacts["emails"])
    emails.extend(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I))
    phones = list(parser.contacts["phones"])
    phones.extend(clean(value) for value in re.findall(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)", text))
    return {"emails": unique_strings(emails)[:50], "phones": unique_strings(phones)[:50]}


def build_link_stats(links: list[dict[str, str]], final_url: str) -> dict[str, int]:
    host = urllib.parse.urlparse(final_url).netloc.lower()
    stats = {"total": len(links), "internal": 0, "external": 0, "https": 0, "http": 0}
    for item in links:
        parsed = urllib.parse.urlparse(item.get("url", ""))
        if parsed.netloc.lower() == host:
            stats["internal"] += 1
        else:
            stats["external"] += 1
        if parsed.scheme == "https":
            stats["https"] += 1
        elif parsed.scheme == "http":
            stats["http"] += 1
    return stats


def build_page_info(parser: PageParser, final_url: str) -> dict[str, str]:
    parts = urllib.parse.urlparse(final_url)
    metadata = parser.metadata
    return {
        "domain": parts.netloc,
        "language": parser.language,
        "canonical": parser.canonical,
        "author": metadata.get("author", ""),
        "keywords": metadata.get("keywords", ""),
        "published_time": metadata.get("article:published_time", metadata.get("datepublished", "")),
        "site_name": metadata.get("og:site_name", ""),
        "page_type": metadata.get("og:type", ""),
        "generator": metadata.get("generator", ""),
        "robots": metadata.get("robots", ""),
        "viewport": metadata.get("viewport", ""),
    }


def build_seo_report(parser: PageParser, final_url: str) -> dict[str, object]:
    title_length = len(parser.title)
    description_length = len(parser.description)
    h1_count = sum(1 for item in parser.headings if item["level"] == "H1")
    missing_alt = sum(1 for image in parser.images if not clean(image.get("alt", "")))
    robots = parser.metadata.get("robots", "").lower()
    checks = [
        ("标题长度", 10 <= title_length <= 60, f"当前 {title_length} 字，建议 10-60 字", 15),
        ("页面描述", 50 <= description_length <= 160, f"当前 {description_length} 字，建议 50-160 字", 15),
        ("唯一 H1", h1_count == 1, f"检测到 {h1_count} 个 H1", 12),
        ("Canonical", bool(parser.canonical), parser.canonical or "未声明规范地址", 10),
        ("页面语言", bool(parser.language), parser.language or "html 未声明 lang", 8),
        ("移动适配", bool(parser.metadata.get("viewport")), "已声明 viewport" if parser.metadata.get("viewport") else "未声明 viewport", 8),
        ("图片替代文本", missing_alt == 0, f"{missing_alt} 张图片缺少 alt", 12),
        ("HTTPS", urllib.parse.urlparse(final_url).scheme == "https", final_url, 10),
        ("允许索引", "noindex" not in robots, robots or "未发现 noindex", 10),
    ]
    score = sum(weight for _label, passed, _detail, weight in checks if passed)
    grade = "优秀" if score >= 85 else "良好" if score >= 70 else "需改进" if score >= 50 else "风险较高"
    return {
        "score": score,
        "grade": grade,
        "issues": [f"{label}：{detail}" for label, passed, detail, _weight in checks if not passed],
        "checks": [
            {"label": label, "passed": passed, "detail": detail, "weight": weight}
            for label, passed, detail, weight in checks
        ],
    }


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


HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5"


def _open_response(url: str, respect_robots: bool, accept: str):
    """Fetch raw bytes with a robots check and retry. Returns
    (final_url, status, reason, headers, raw_bytes). HTTP error responses
    (e.g. 404) are returned, not raised, so callers can inspect them."""
    if respect_robots and not robots_allowed(url):
        raise RobotsDisallowed("该网站的 robots.txt 不允许抓取此页面。")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read(MAX_BYTES + 1)
                return resp.geturl(), getattr(resp, "status", None), getattr(resp, "reason", ""), resp.headers, raw
        except urllib.error.HTTPError as err:
            raw = err.read(MAX_BYTES + 1)
            return err.geturl(), err.code, err.reason or "", err.headers, raw
        except urllib.error.URLError as err:
            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            raise FetchError(friendly_network_error(err)) from err


def fetch_url(url: str, respect_robots: bool = True) -> CrawlResult:
    url = normalize_url(url)
    final_url, status, reason, headers, raw = _open_response(url, respect_robots, HTML_ACCEPT)
    content_type, charset = headers.get("Content-Type", ""), headers.get_content_charset()
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
    links = dedupe(parser.links, "url")[:500]
    images = dedupe(parser.images, "src")[:300]
    videos = dedupe(parser.videos, "url")[:300]
    text = parser.body_text
    word_count = estimate_word_count(text)
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
        links=links,
        images=images,
        videos=videos,
        text=text,
        html_preview=html[:30_000],
        bytes_read=len(raw),
        truncated=truncated,
        fetched_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        word_count=word_count,
        reading_minutes=max(1, (word_count + 299) // 300) if word_count else 0,
        page_info=build_page_info(parser, final_url),
        link_stats=build_link_stats(links, final_url),
        contacts=collect_contacts(parser, text),
        forms=parser.forms[:100],
        resources={key: unique_strings(values)[:300] for key, values in parser.resources.items()},
        seo_report=build_seo_report(parser, final_url),
    )


def fetch_raw_response(url: str, respect_robots: bool = True, max_body_chars: int = 200_000) -> str:
    """Fetch the URL and return the raw server response as text: the status
    line, every response header, then the decoded body. Useful for seeing
    what the server actually returns right now (e.g. for JS-rendered pages)."""
    url = normalize_url(url)
    final_url, status, reason, headers, raw = _open_response(url, respect_robots, "*/*")
    truncated = len(raw) > MAX_BYTES
    raw = raw[:MAX_BYTES]
    encoding = headers.get_content_charset() or meta_encoding(raw) or "utf-8"
    try:
        body = raw.decode(encoding, "replace")
    except LookupError:
        body = raw.decode("utf-8", "replace")
    lines = [
        f"HTTP {status} {reason}".rstrip(),
        f"最终地址：{final_url}",
        f"读取大小：{len(raw):,} bytes" + ("（已截断）" if truncated else ""),
        "",
    ]
    for key, value in headers.items():
        lines.append(f"{key}: {value}")
    lines += ["", "=" * 60, ""]
    clipped = body[:max_body_chars]
    if len(body) > max_body_chars:
        clipped += "\n\n（正文过长，已截断预览）"
    return "\n".join(lines) + clipped + "\n"


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

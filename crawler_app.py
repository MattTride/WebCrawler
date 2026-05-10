from __future__ import annotations

import base64
import io
import json
import queue
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

APP_NAME = "网页爬虫小程序"
MAX_BYTES = 1_500_000
PREVIEW_MAX_BYTES = 800_000
TIMEOUT = 15
DOWNLOAD_TIMEOUT = 45
PREVIEW_TIMEOUT = 8
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


def fetch_url(url: str) -> CrawlResult:
    url = normalize_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 MiniCrawler/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
    })
    final_url, status, reason, content_type, charset = url, None, "", "", None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES + 1)
            final_url, status, reason = resp.geturl(), getattr(resp, "status", None), getattr(resp, "reason", "")
            content_type, charset = resp.headers.get("Content-Type", ""), resp.headers.get_content_charset()
    except urllib.error.HTTPError as err:
        raw = err.read(MAX_BYTES + 1)
        final_url, status, reason = err.geturl(), err.code, err.reason or ""
        content_type, charset = err.headers.get("Content-Type", ""), err.headers.get_content_charset()

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
        "User-Agent": "Mozilla/5.0 MiniCrawler/1.0",
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
        "User-Agent": "Mozilla/5.0 MiniCrawler/1.0",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=PREVIEW_TIMEOUT) as resp:
        return resp.read(PREVIEW_MAX_BYTES + 1)[:PREVIEW_MAX_BYTES]


class CrawlerApp(tk.Frame):
    C = {
        "bg": "#f4eee6", "panel": "#fffaf3", "alt": "#f9f1e8", "side": "#eadfd1",
        "line": "#d8c8b8", "ink": "#2f2a25", "muted": "#74695f",
        "accent": "#c76645", "accent_dark": "#9d4e35", "accent_soft": "#f3d6c9", "green": "#46685c",
    }
    PLACEHOLDER = "粘贴或输入网页 URL，例如：https://example.com"

    def __init__(self, root: tk.Tk):
        super().__init__(root, bg=self.C["bg"])
        self.root, self.q, self.result = root, queue.Queue(), None
        self.pages, self.tabs, self.lists = {}, {}, {}
        self.link_items, self.image_items, self.video_items, self.placeholder = [], [], [], True
        self.preview_slots, self.preview_photos, self.preview_generation = {}, [], 0
        self.status = tk.StringVar(value="等待 URL")
        self.metrics = {k: tk.StringVar(value=v) for k, v in {
            "链接": "0", "图片": "0", "视频": "0", "标题": "0", "状态": "未抓取",
        }.items()}
        root.title(APP_NAME)
        root.geometry("1180x780")
        root.minsize(960, 660)
        root.configure(bg=self.C["bg"])
        self.pack(fill="both", expand=True)
        self.build()
        self.poll()

    def label(self, parent, text="", var=None, size=13, weight="normal", fg=None, bg=None):
        return tk.Label(parent, text=text, textvariable=var, bg=bg or self.C["bg"], fg=fg or self.C["ink"],
                        font=("Helvetica", size, weight), anchor="w", justify="left")

    def button(self, parent, text, command, primary=False):
        bg = self.C["accent"] if primary else self.C["panel"]
        fg = "#fffaf3" if primary else self.C["ink"]
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, relief="flat", bd=0, padx=18, pady=10,
                         activebackground=self.C["accent_dark"] if primary else self.C["accent_soft"],
                         activeforeground=fg, font=("Helvetica", 13, "bold" if primary else "normal"),
                         cursor="hand2", highlightthickness=1, highlightbackground=self.C["line"])

    def build(self):
        self.sidebar()
        work = tk.Frame(self, bg=self.C["bg"])
        work.pack(side="left", fill="both", expand=True)
        self.topbar(work)
        self.results(work)

    def sidebar(self):
        side = tk.Frame(self, bg=self.C["side"], width=286, highlightbackground=self.C["line"], highlightthickness=1)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.label(side, "Crawl Studio", size=23, weight="bold", bg=self.C["side"]).pack(anchor="w", padx=24, pady=(28, 4))
        self.label(side, "像对话一样抓取网页", size=12, fg=self.C["muted"], bg=self.C["side"]).pack(anchor="w", padx=24)
        note = tk.Frame(side, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
        note.pack(fill="x", padx=20, pady=(30, 18))
        self.label(note, "当前任务", size=12, weight="bold", bg=self.C["panel"]).pack(anchor="w", padx=14, pady=(12, 3))
        self.label(note, "输入 URL 后，我会提取标题、正文、链接、图片、视频和 HTML 预览。", size=12, fg=self.C["muted"], bg=self.C["panel"]).pack(fill="x", padx=14, pady=(0, 14))
        grid = tk.Frame(side, bg=self.C["side"])
        grid.pack(fill="x", padx=20)
        for i, name in enumerate(("链接", "图片", "视频", "标题", "状态")):
            card = tk.Frame(grid, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=4, pady=4)
            grid.grid_columnconfigure(i % 2, weight=1)
            self.label(card, name, size=11, fg=self.C["muted"], bg=self.C["panel"]).pack(anchor="w", padx=12, pady=(10, 0))
            self.label(card, var=self.metrics[name], size=18, weight="bold", bg=self.C["panel"]).pack(anchor="w", padx=12, pady=(0, 10))

    def topbar(self, parent):
        bar = tk.Frame(parent, bg=self.C["bg"])
        bar.pack(fill="x", padx=28, pady=(26, 16))
        left = tk.Frame(bar, bg=self.C["bg"])
        left.pack(side="left", fill="x", expand=True)
        self.label(left, APP_NAME, size=25, weight="bold").pack(anchor="w")
        self.label(left, "把一个网页变成结构化信息：摘要、正文、链接、图片、视频，一次看清。", size=13, fg=self.C["muted"]).pack(anchor="w", pady=(4, 0))
        badge = tk.Frame(bar, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
        badge.pack(side="right")
        self.label(badge, var=self.status, size=12, weight="bold", fg=self.C["green"], bg=self.C["panel"]).pack(padx=14, pady=8)

    def results(self, parent):
        panel = tk.Frame(parent, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
        panel.pack(fill="both", expand=True, padx=28, pady=(0, 24))
        head = tk.Frame(panel, bg=self.C["panel"])
        head.pack(fill="x", padx=18, pady=(16, 8))
        self.label(head, "抓取结果", size=17, weight="bold", bg=self.C["panel"]).pack(side="left")
        self.label(head, "结果会出现在这里", size=12, fg=self.C["muted"], bg=self.C["panel"]).pack(side="left", padx=(10, 0))
        tabs = tk.Frame(panel, bg=self.C["panel"])
        tabs.pack(fill="x", padx=14, pady=(0, 10))
        for name in ("概览", "正文", "链接", "图片", "视频", "HTML预览"):
            btn = tk.Button(tabs, text=name, command=lambda n=name: self.show(n), relief="flat", bd=0, padx=16, pady=9,
                            bg=self.C["alt"], fg=self.C["muted"], activebackground=self.C["accent_soft"],
                            font=("Helvetica", 13, "bold"), cursor="hand2")
            btn.pack(side="left", padx=(0, 8))
            self.tabs[name] = btn
        self.inline_composer(panel)
        content = tk.Frame(panel, bg=self.C["panel"])
        content.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)
        self.holder = tk.Frame(content, bg=self.C["panel"])
        self.holder.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.preview_panel(content)
        self.summary = self.text_page("概览")
        self.body = self.text_page("正文")
        self.list_page("链接")
        self.list_page("图片")
        self.list_page("视频")
        self.html = self.text_page("HTML预览")
        self.set_text(self.summary, "准备好了。\n\n把网页 URL 粘贴到上方输入框，然后点击“获取”。")
        self.render_media_preview([], [])
        self.show("概览")

    def inline_composer(self, parent):
        box = tk.Frame(parent, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
        box.pack(fill="x", padx=14, pady=(0, 14))
        top = tk.Frame(box, bg=self.C["panel"])
        top.pack(fill="x", padx=16, pady=(12, 6))
        self.label(top, "将 URL 输入⬇️", size=14, weight="bold", fg=self.C["accent_dark"], bg=self.C["panel"]).pack(side="left")
        body = tk.Frame(box, bg=self.C["panel"])
        body.pack(fill="x", padx=16, pady=(0, 14))
        self.url = tk.Text(body, height=2, wrap="word", relief="flat", bd=0, padx=14, pady=12,
                           font=("Helvetica", 16), bg="#fff4e8", fg=self.C["muted"],
                           insertbackground=self.C["ink"], highlightthickness=2,
                           highlightbackground=self.C["accent_soft"], highlightcolor=self.C["accent"])
        self.url.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.url.insert("1.0", self.PLACEHOLDER)
        self.url.bind("<FocusIn>", self.clear_placeholder)
        self.url.bind("<FocusOut>", self.restore_placeholder)
        self.url.bind("<Return>", self.key_fetch)
        actions = tk.Frame(body, bg=self.C["panel"])
        actions.pack(side="right", fill="y")
        self.fetch_btn = self.button(actions, "获取", self.start_fetch, True)
        self.fetch_btn.pack(fill="x")
        row = tk.Frame(actions, bg=self.C["panel"])
        row.pack(fill="x", pady=(8, 0))
        self.clear_btn = self.button(row, "清空", self.clear_results)
        self.clear_btn.pack(side="left")
        self.save_btn = self.button(row, "保存", self.save_results)
        self.save_btn.configure(state=tk.DISABLED)
        self.save_btn.pack(side="left", padx=(8, 0))

    def preview_panel(self, parent):
        panel = tk.Frame(parent, bg=self.C["alt"], width=310, highlightbackground=self.C["line"], highlightthickness=1)
        panel.grid(row=0, column=1, sticky="ns")
        panel.grid_propagate(False)
        self.label(panel, "媒体预览", size=15, weight="bold", bg=self.C["alt"]).pack(anchor="w", padx=14, pady=(14, 2))
        self.label(panel, "图片会自动显示；点卡片即可下载。", size=11, fg=self.C["muted"], bg=self.C["alt"]).pack(anchor="w", padx=14, pady=(0, 10))
        canvas = tk.Canvas(panel, bg=self.C["alt"], bd=0, highlightthickness=0)
        scroll = tk.Scrollbar(panel, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 12))
        scroll.pack(side="right", fill="y", pady=(0, 12))
        self.preview_inner = tk.Frame(canvas, bg=self.C["alt"])
        window = canvas.create_window((0, 0), window=self.preview_inner, anchor="nw")
        self.preview_canvas = canvas
        self.preview_inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

    def text_page(self, name):
        frame = tk.Frame(self.holder, bg=self.C["panel"])
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        text = tk.Text(frame, wrap="word", relief="flat", bd=0, padx=18, pady=16, font=("Helvetica", 14),
                       bg=self.C["alt"], fg=self.C["ink"], insertbackground=self.C["ink"],
                       selectbackground=self.C["accent_soft"], undo=False)
        scroll = tk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.pages[name] = frame
        return text

    def list_page(self, name):
        frame = tk.Frame(self.holder, bg=self.C["panel"])
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        tools = tk.Frame(frame, bg=self.C["panel"])
        tools.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.button(tools, "复制选中地址", lambda n=name: self.copy_url(n)).pack(side="left")
        self.button(tools, "打开选中地址", lambda n=name: self.open_url(n)).pack(side="left", padx=(8, 0))
        if name in {"图片", "视频"}:
            self.button(tools, "下载选中资源", lambda n=name: self.download_selected(n)).pack(side="left", padx=(8, 0))
        box = tk.Listbox(frame, selectmode=tk.BROWSE, relief="flat", bd=0, font=("Helvetica", 14),
                         bg=self.C["alt"], fg=self.C["ink"], selectbackground=self.C["accent_soft"])
        scroll = tk.Scrollbar(frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        box.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        box.bind("<Double-Button-1>", lambda _e, n=name: self.open_url(n))
        self.pages[name], self.lists[name] = frame, box

    def show(self, name):
        for key, page in self.pages.items():
            page.pack_forget()
            self.tabs[key].configure(bg=self.C["accent_soft"] if key == name else self.C["alt"],
                                     fg=self.C["ink"] if key == name else self.C["muted"])
        self.pages[name].pack(fill="both", expand=True)

    def clear_placeholder(self, _e=None):
        if self.placeholder:
            self.url.delete("1.0", tk.END)
            self.url.configure(fg=self.C["ink"])
            self.placeholder = False

    def restore_placeholder(self, _e=None):
        if not self.url.get("1.0", "end-1c").strip():
            self.url.delete("1.0", tk.END)
            self.url.insert("1.0", self.PLACEHOLDER)
            self.url.configure(fg=self.C["muted"])
            self.placeholder = True

    def read_url(self):
        return "" if self.placeholder else self.url.get("1.0", "end-1c").strip()

    def write_url(self, value):
        self.placeholder = False
        self.url.configure(fg=self.C["ink"])
        self.url.delete("1.0", tk.END)
        self.url.insert("1.0", value)

    def key_fetch(self, _e=None):
        self.start_fetch()
        return "break"

    def start_fetch(self):
        try:
            url = normalize_url(self.read_url())
        except ValueError as err:
            messagebox.showwarning(APP_NAME, str(err))
            return
        self.write_url(url)
        self.result = None
        self.save_btn.configure(state=tk.DISABLED)
        self.set_text(self.summary, "正在准备新的抓取任务...")
        self.set_text(self.body, "")
        self.set_text(self.html, "")
        self.fill_list("链接", [])
        self.fill_list("图片", [])
        self.fill_list("视频", [])
        self.render_media_preview([], [])
        self.set_busy(True, "正在抓取")
        self.show("概览")
        threading.Thread(target=self.worker, args=(url,), daemon=True).start()

    def worker(self, url):
        try:
            self.q.put(("ok", fetch_url(url)))
        except Exception as err:
            self.q.put(("err", str(err)))

    def poll(self):
        try:
            kind, payload = self.q.get_nowait()
            if kind == "ok":
                self.show_result(payload)
            elif kind == "download_ok":
                self.status.set("下载完成")
                messagebox.showinfo(APP_NAME, f"资源已保存：\n{payload}")
            elif kind == "download_err":
                self.status.set("下载失败")
                messagebox.showerror(APP_NAME, f"下载失败：\n{payload}")
            elif kind == "preview_image":
                slot_id, raw = payload
                self.show_preview_image(slot_id, raw)
            elif kind == "preview_error":
                self.mark_preview_unavailable(payload)
            else:
                self.show_error(payload)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_result(self, result: CrawlResult):
        self.result = result
        self.set_busy(False, "抓取完成")
        self.save_btn.configure(state=tk.NORMAL)
        self.metrics["状态"].set(str(result.status_code or "完成"))
        self.metrics["链接"].set(str(len(result.links)))
        self.metrics["图片"].set(str(len(result.images)))
        self.metrics["视频"].set(str(len(result.videos)))
        self.metrics["标题"].set(str(len(result.headings)))
        headings = "\n".join(f"{h['level']}  {h['text']}" for h in result.headings[:30]) or "未发现标题结构"
        status = f"{result.status_code} {result.reason}".strip() if result.status_code else "无状态码"
        self.set_text(self.summary, f"""抓取时间：{result.fetched_at}
请求地址：{result.requested_url}
最终地址：{result.final_url}
状态：{status}
内容类型：{result.content_type}
编码：{result.encoding}
读取大小：{result.bytes_read:,} bytes
内容被截断：{"是" if result.truncated else "否"}
链接数量：{len(result.links)}
图片数量：{len(result.images)}
视频数量：{len(result.videos)}

页面标题：
{result.title}

页面描述：
{result.description or "未发现 description"}

标题结构：
{headings}
""")
        self.set_text(self.body, result.text or "未提取到可读正文。")
        self.set_text(self.html, result.html_preview)
        self.fill_list("链接", result.links)
        self.fill_list("图片", result.images)
        self.fill_list("视频", result.videos)
        self.render_media_preview(result.images, result.videos)
        self.show("概览")

    def show_error(self, message: str):
        self.set_busy(False, "抓取失败")
        self.metrics["状态"].set("失败")
        self.set_text(self.summary, f"抓取失败：\n{message}\n\n请检查网址、网络连接，或稍后重试。")
        self.show("概览")
        messagebox.showerror(APP_NAME, f"抓取失败：\n{message}")

    def set_busy(self, busy: bool, status: str):
        self.status.set(status)
        self.fetch_btn.configure(text="获取中..." if busy else "获取", state=tk.DISABLED if busy else tk.NORMAL)
        self.clear_btn.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def clear_results(self):
        self.result = None
        self.save_btn.configure(state=tk.DISABLED)
        self.set_text(self.summary, "准备好了。\n\n把网页 URL 粘贴到上方输入框，然后点击“获取”。")
        self.set_text(self.body, "")
        self.set_text(self.html, "")
        self.fill_list("链接", [])
        self.fill_list("图片", [])
        self.fill_list("视频", [])
        self.render_media_preview([], [])
        for key, value in {"链接": "0", "图片": "0", "视频": "0", "标题": "0", "状态": "未抓取"}.items():
            self.metrics[key].set(value)
        self.status.set("等待 URL")
        self.show("概览")

    def set_text(self, widget, value):
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.see("1.0")

    def render_media_preview(self, images, videos):
        self.preview_generation += 1
        generation = self.preview_generation
        self.preview_slots.clear()
        self.preview_photos.clear()
        for child in self.preview_inner.winfo_children():
            child.destroy()

        items = []
        for item in images[:10]:
            url = item.get("src", "")
            if url:
                items.append(("image", item.get("alt") or "图片资源", url))
        for item in videos[:8]:
            url = item.get("url", "")
            if url:
                items.append(("video", item.get("text") or "视频资源", url))

        if not items:
            empty = tk.Frame(self.preview_inner, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
            empty.pack(fill="x", padx=4, pady=4)
            self.label(empty, "等待媒体结果", size=13, weight="bold", bg=self.C["panel"]).pack(anchor="w", padx=12, pady=(12, 3))
            self.label(empty, "获取网页后，图片和视频会在这里直接出现。", size=11, fg=self.C["muted"], bg=self.C["panel"]).pack(fill="x", padx=12, pady=(0, 12))
            return

        for index, (kind, title, url) in enumerate(items):
            card = tk.Frame(self.preview_inner, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1, cursor="hand2")
            card.pack(fill="x", padx=4, pady=5)
            card.bind("<Button-1>", lambda _e, u=url: self.confirm_download(u))

            if kind == "image":
                thumb = tk.Label(card, text="正在加载图片预览...", bg="#fff4e8", fg=self.C["muted"],
                                 height=7, wraplength=245, justify="center", cursor="hand2")
                thumb.pack(fill="x", padx=10, pady=(10, 7))
                thumb.bind("<Button-1>", lambda _e, u=url: self.confirm_download(u))
                slot_id = (generation, index)
                self.preview_slots[slot_id] = thumb
                threading.Thread(target=self.preview_image_worker, args=(slot_id, url), daemon=True).start()
            else:
                box = tk.Label(card, text="视频资源\n点击选择下载", bg=self.C["accent_soft"], fg=self.C["ink"],
                               height=5, font=("Helvetica", 13, "bold"), cursor="hand2", justify="center")
                box.pack(fill="x", padx=10, pady=(10, 7))
                box.bind("<Button-1>", lambda _e, u=url: self.confirm_download(u))

            name = self.shorten(title or safe_filename_from_url(url, "media"), 34)
            desc = self.shorten(url, 48)
            title_label = self.label(card, name, size=12, weight="bold", bg=self.C["panel"])
            title_label.pack(fill="x", padx=10)
            title_label.bind("<Button-1>", lambda _e, u=url: self.confirm_download(u))
            url_label = self.label(card, desc, size=10, fg=self.C["muted"], bg=self.C["panel"])
            url_label.pack(fill="x", padx=10, pady=(2, 10))
            url_label.bind("<Button-1>", lambda _e, u=url: self.confirm_download(u))

        self.preview_canvas.yview_moveto(0)

    def preview_image_worker(self, slot_id, url):
        try:
            self.q.put(("preview_image", (slot_id, fetch_preview_bytes(url))))
        except Exception:
            self.q.put(("preview_error", slot_id))

    def show_preview_image(self, slot_id, raw):
        if slot_id[0] != self.preview_generation:
            return
        label = self.preview_slots.get(slot_id)
        if label is None:
            return
        photo = self.make_preview_photo(raw)
        if photo is None:
            self.mark_preview_unavailable(slot_id)
            return
        label.configure(image=photo, text="", height=0)
        label.image = photo
        self.preview_photos.append(photo)

    def mark_preview_unavailable(self, slot_id):
        if slot_id[0] != self.preview_generation:
            return
        label = self.preview_slots.get(slot_id)
        if label is not None:
            label.configure(text="图片暂时无法预览\n点击选择下载", fg=self.C["muted"], height=7)

    def make_preview_photo(self, raw: bytes):
        try:
            from PIL import Image, ImageTk
            image = Image.open(io.BytesIO(raw))
            image.thumbnail((245, 150))
            return ImageTk.PhotoImage(image)
        except Exception:
            pass

        try:
            encoded = base64.b64encode(raw).decode("ascii")
            photo = tk.PhotoImage(data=encoded)
            return self.scale_photo(photo)
        except Exception:
            pass

        return self.make_sips_photo(raw)

    def scale_photo(self, photo, max_width=245, max_height=150):
        factor = max((photo.width() + max_width - 1) // max_width, (photo.height() + max_height - 1) // max_height, 1)
        return photo.subsample(factor) if factor > 1 else photo

    def make_sips_photo(self, raw: bytes):
        input_path, output_path = None, None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as file:
                file.write(raw)
                input_path = Path(file.name)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as file:
                output_path = Path(file.name)
            subprocess.run(
                ["sips", "-s", "format", "png", str(input_path), "--out", str(output_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4,
                check=True,
            )
            return self.scale_photo(tk.PhotoImage(file=str(output_path)))
        except Exception:
            return None
        finally:
            if input_path:
                input_path.unlink(missing_ok=True)
            if output_path:
                output_path.unlink(missing_ok=True)

    def shorten(self, value, limit):
        value = clean(value)
        return value if len(value) <= limit else f"{value[:limit - 3]}..."

    def records_for(self, name):
        if name == "链接":
            return self.link_items
        if name == "图片":
            return self.image_items
        return self.video_items

    def fill_list(self, name, records):
        target = self.records_for(name)
        target[:] = records
        box = self.lists[name]
        box.delete(0, tk.END)
        if name == "图片":
            label_key, url_key = "alt", "src"
        else:
            label_key, url_key = "text", "url"
        for i, item in enumerate(records, 1):
            box.insert(tk.END, f"{i}. {self.shorten(item.get(label_key) or '无说明', 62)}    {self.shorten(item.get(url_key, ''), 116)}")

    def selected_url(self, name):
        box = self.lists[name]
        if not box.curselection():
            return None
        records = self.records_for(name)
        item = records[box.curselection()[0]]
        return item.get("url") or item.get("src")

    def copy_url(self, name):
        url = self.selected_url(name)
        if not url:
            messagebox.showinfo(APP_NAME, "请先选择一条记录。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.status.set("已复制地址")

    def open_url(self, name):
        url = self.selected_url(name)
        if not url:
            messagebox.showinfo(APP_NAME, "请先选择一条记录。")
            return
        webbrowser.open(url)
        self.status.set("已打开地址")

    def download_selected(self, name):
        url = self.selected_url(name)
        if not url:
            messagebox.showinfo(APP_NAME, "请先选择一条记录。")
            return
        self.choose_download_path(url)

    def confirm_download(self, url):
        if messagebox.askyesno(APP_NAME, f"要下载这个资源吗？\n\n{self.shorten(url, 90)}"):
            self.choose_download_path(url)

    def choose_download_path(self, url):
        downloads = Path.home() / "Downloads"
        initialdir = downloads if downloads.exists() else Path.cwd()
        path = filedialog.asksaveasfilename(
            title="保存资源",
            initialdir=str(initialdir),
            initialfile=safe_filename_from_url(url, "media"),
            filetypes=(("所有文件", "*.*"),),
        )
        if not path:
            return
        self.status.set("正在下载")
        threading.Thread(target=self.download_worker, args=(url, Path(path)), daemon=True).start()

    def download_worker(self, url, path):
        try:
            download_file(url, path)
            self.q.put(("download_ok", str(path)))
        except Exception as err:
            self.q.put(("download_err", str(err)))

    def save_results(self):
        if self.result is None:
            messagebox.showinfo(APP_NAME, "当前没有可保存的抓取结果。")
            return
        path = filedialog.asksaveasfilename(
            title="保存抓取结果",
            initialdir=str(Path.cwd()),
            initialfile=f"crawler-result-{time.strftime('%Y%m%d-%H%M%S')}.json",
            defaultextension=".json",
            filetypes=(("JSON 文件", "*.json"), ("所有文件", "*.*")),
        )
        if path:
            Path(path).write_text(json.dumps(self.result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.status.set("结果已保存")
            messagebox.showinfo(APP_NAME, "抓取结果已保存。")


def main():
    root = tk.Tk()
    CrawlerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

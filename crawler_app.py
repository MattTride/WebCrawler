from __future__ import annotations

import base64
import io
import json
import queue
import subprocess
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

from crawler_core import (
    CrawlResult,
    available_download_path,
    clean,
    download_file,
    fetch_preview_bytes,
    fetch_raw_response,
    fetch_url,
    normalize_url,
    result_to_csv,
    result_to_html,
    result_to_markdown,
    safe_filename_from_url,
)
from workspace_store import HistoryStore

APP_NAME = "网页爬虫小程序"
APP_VERSION = "2.0.0"
PROJECT_URL = "https://github.com/MattTride/WebCrawler"


class CrawlerApp(tk.Frame):
    C = {
        "bg": "#F1F3F2", "panel": "#FFFFFF", "alt": "#F7F8F7", "side": "#252925",
        "side_alt": "#323732", "side_text": "#F2F4F1", "line": "#D9DEDB",
        "ink": "#202521", "muted": "#68716B", "accent": "#D36B47",
        "accent_dark": "#A84D30", "accent_soft": "#F5E2DB", "green": "#2F6B57",
        "blue": "#3E6473", "warning": "#9A6726", "input": "#FFFFFF",
    }
    PLACEHOLDER = "粘贴或输入网页 URL，例如：https://example.com"
    PAGE_HINTS = {
        "概览": "请求状态、页面结构与核心统计",
        "页面情报": "SEO 健康度、站点元数据、表单与资源诊断",
        "正文": "服务器返回页面中的可读文本",
        "链接": "页面内外部链接与目标地址",
        "图片": "图片资源、替代文本与下载入口",
        "视频": "视频源、播放器资源与下载入口",
        "HTML预览": "服务器返回的原始 HTML 片段",
        "历史记录": "保存在本机的最近抓取任务，可快速再次运行",
    }

    def __init__(self, root: tk.Tk, history_path: Path | None = None):
        super().__init__(root, bg=self.C["bg"])
        self.root, self.q, self.result = root, queue.Queue(), None
        self.pages, self.tabs, self.lists = {}, {}, {}
        self.filter_vars: dict[str, tk.StringVar] = {}
        self.filtered_items: dict[str, list[dict[str, str]]] = {"链接": [], "图片": [], "视频": []}
        self.history_store = HistoryStore(history_path)
        self.history_items: list[dict[str, object]] = []
        self.link_items, self.image_items, self.video_items, self.placeholder = [], [], [], True
        self.preview_slots, self.preview_photos, self.preview_generation = {}, [], 0
        self.status = tk.StringVar(value="等待 URL")
        self.respect_robots = tk.BooleanVar(value=True)
        self.metrics = {k: tk.StringVar(value=v) for k, v in {
            "链接": "0", "图片": "0", "视频": "0", "标题": "0", "SEO": "--", "状态": "未抓取",
        }.items()}
        root.title(APP_NAME)
        root.geometry("1440x900")
        root.minsize(1120, 720)
        root.configure(bg=self.C["bg"])
        try:
            root.tk.call("tk", "appname", "Crawl Studio")
        except tk.TclError:
            pass
        self.configure_menus()
        self.pack(fill="both", expand=True)
        self.build()
        self.poll()

    def label(self, parent, text="", var=None, size=13, weight="normal", fg=None, bg=None):
        return tk.Label(parent, text=text, textvariable=var, bg=bg or self.C["bg"], fg=fg or self.C["ink"],
                        font=("Helvetica", size, weight), anchor="w", justify="left")

    def button(self, parent, text, command, primary=False):
        bg = self.C["accent"] if primary else self.C["panel"]
        fg = self.C["ink"]
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, relief="flat", bd=0, padx=18, pady=10,
                         activebackground=self.C["accent_dark"] if primary else self.C["accent_soft"],
                         activeforeground=fg, font=("Helvetica", 13, "bold" if primary else "normal"),
                         disabledforeground=self.C["muted"],
                         cursor="hand2", highlightthickness=1,
                         highlightbackground=self.C["accent_dark"] if primary else self.C["line"])

    def configure_menus(self):
        menubar = tk.Menu(self.root)
        modifier = "Command" if self.root.tk.call("tk", "windowingsystem") == "aqua" else "Control"
        accelerator = "⌘" if modifier == "Command" else "Ctrl+"

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="定位网址", accelerator=f"{accelerator}L", command=self.focus_url)
        file_menu.add_command(label="获取网页", accelerator=f"{accelerator}↩", command=self.start_fetch)
        file_menu.add_separator()
        file_menu.add_command(label="导出报告", accelerator=f"{accelerator}S", command=self.save_results)
        file_menu.add_command(label="查看原始响应", command=self.show_raw_response)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.destroy)
        menubar.add_cascade(label="文件", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        for name in ("概览", "页面情报", "正文", "链接", "图片", "视频", "HTML预览", "历史记录"):
            view_menu.add_command(label=name, command=lambda n=name: self.show(n))
        menubar.add_cascade(label="视图", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="项目主页", command=lambda: webbrowser.open(PROJECT_URL))
        help_menu.add_command(label="关于 Crawl Studio", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)

        self.root.bind_all(f"<{modifier}-l>", self.focus_url)
        self.root.bind_all(f"<{modifier}-Return>", self.key_fetch)
        self.root.bind_all(f"<{modifier}-s>", self.shortcut_export)

    def focus_url(self, _event=None):
        self.clear_placeholder()
        self.url.focus_set()
        self.url.tag_add(tk.SEL, "1.0", "end-1c")
        return "break"

    def shortcut_export(self, _event=None):
        self.save_results()
        return "break"

    def show_about(self):
        messagebox.showinfo(
            "关于 Crawl Studio",
            f"Crawl Studio {APP_VERSION}\n\n"
            "单页网页采集、页面情报、媒体提取与结构化报告工作台。\n\n"
            "MIT License · MattTride/WebCrawler",
        )

    def build(self):
        self.sidebar()
        work = tk.Frame(self, bg=self.C["bg"])
        work.pack(side="left", fill="both", expand=True)
        self.topbar(work)
        self.results(work)

    def sidebar(self):
        side = tk.Frame(self, bg=self.C["side"], width=232)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=self.C["side"])
        brand.pack(fill="x", padx=20, pady=(24, 26))
        self.label(brand, "CRAWL STUDIO", size=18, weight="bold", fg=self.C["side_text"], bg=self.C["side"]).pack(anchor="w")
        self.label(brand, "网页采集与内容分析工作台", size=10, fg="#AEB7B0", bg=self.C["side"]).pack(anchor="w", pady=(5, 0))

        self.label(side, "工作区", size=10, weight="bold", fg="#8F9A92", bg=self.C["side"]).pack(anchor="w", padx=20, pady=(0, 8))
        for name in ("概览", "页面情报", "正文", "链接", "图片", "视频", "HTML预览", "历史记录"):
            btn = tk.Label(
                side, text=name, anchor="w", padx=18, pady=11,
                bg=self.C["side"], fg=self.C["side_text"],
                font=("Helvetica", 12, "bold"), cursor="hand2",
            )
            btn.pack(fill="x", padx=10, pady=1)
            btn.bind("<Button-1>", lambda _e, n=name: self.show(n))
            btn.bind("<Enter>", lambda _e, n=name: self.nav_hover(n, True))
            btn.bind("<Leave>", lambda _e, n=name: self.nav_hover(n, False))
            self.tabs[name] = btn

        spacer = tk.Frame(side, bg=self.C["side"])
        spacer.pack(fill="both", expand=True)
        footer = tk.Frame(side, bg=self.C["side_alt"], highlightbackground="#3C433D", highlightthickness=1)
        footer.pack(fill="x", padx=14, pady=14)
        self.label(footer, "运行状态", size=10, weight="bold", fg="#AEB7B0", bg=self.C["side_alt"]).pack(anchor="w", padx=12, pady=(11, 3))
        self.label(footer, var=self.status, size=12, weight="bold", fg=self.C["side_text"], bg=self.C["side_alt"]).pack(anchor="w", padx=12, pady=(0, 11))

    def topbar(self, parent):
        bar = tk.Frame(parent, bg=self.C["bg"])
        bar.pack(fill="x", padx=24, pady=(20, 14))
        left = tk.Frame(bar, bg=self.C["bg"])
        left.pack(side="left", fill="x", expand=True)
        self.label(left, "网页采集工作台", size=23, weight="bold").pack(anchor="w")
        self.label(left, "单页抓取、媒体提取、源代码检查与结构化导出", size=11, fg=self.C["muted"]).pack(anchor="w", pady=(4, 0))
        badge = tk.Frame(bar, bg="#E6F0EB", highlightbackground="#C8D9D0", highlightthickness=1)
        badge.pack(side="right", padx=(12, 0))
        self.label(badge, var=self.status, size=11, weight="bold", fg=self.C["green"], bg="#E6F0EB").pack(padx=13, pady=8)

    def results(self, parent):
        panel = tk.Frame(parent, bg=self.C["bg"])
        panel.pack(fill="both", expand=True, padx=24, pady=(0, 22))
        self.inline_composer(panel)
        self.metrics_strip(panel)
        content = tk.Frame(panel, bg=self.C["bg"])
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)
        main = tk.Frame(content, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
        main.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        page_head = tk.Frame(main, bg=self.C["panel"])
        page_head.pack(fill="x", padx=18, pady=(15, 10))
        self.page_title = tk.StringVar(value="概览")
        self.page_hint = tk.StringVar(value=self.PAGE_HINTS["概览"])
        self.label(page_head, var=self.page_title, size=16, weight="bold", bg=self.C["panel"]).pack(side="left")
        self.label(page_head, var=self.page_hint, size=10, fg=self.C["muted"], bg=self.C["panel"]).pack(side="left", padx=(10, 0), pady=(4, 0))
        self.holder = tk.Frame(main, bg=self.C["panel"])
        self.holder.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.preview_panel(content)
        self.summary = self.text_page("概览")
        self.intelligence = self.text_page("页面情报")
        self.body = self.text_page("正文")
        self.list_page("链接")
        self.list_page("图片")
        self.list_page("视频")
        self.html = self.text_page("HTML预览")
        self.history_page()
        self.set_text(self.summary, "准备好了。\n\n把网页 URL 粘贴到上方输入框，然后点击“获取”。")
        self.render_media_preview([], [])
        self.show("概览")

    def inline_composer(self, parent):
        box = tk.Frame(parent, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
        box.pack(fill="x", pady=(0, 10))
        top = tk.Frame(box, bg=self.C["panel"])
        top.pack(fill="x", padx=16, pady=(12, 7))
        self.label(top, "目标网址", size=12, weight="bold", bg=self.C["panel"]).pack(side="left")
        self.label(top, "输入后按回车或点击获取", size=10, fg=self.C["muted"], bg=self.C["panel"]).pack(side="left", padx=(9, 0))
        body = tk.Frame(box, bg=self.C["panel"])
        body.pack(fill="x", padx=16, pady=(0, 10))
        self.url = tk.Text(body, height=1, wrap="word", relief="flat", bd=0, padx=13, pady=11,
                           font=("Helvetica", 14), bg=self.C["input"], fg=self.C["muted"],
                           insertbackground=self.C["ink"], highlightthickness=1,
                           highlightbackground=self.C["line"], highlightcolor=self.C["accent"])
        self.url.pack(fill="x", expand=True)
        self.url.insert("1.0", self.PLACEHOLDER)
        self.url.bind("<FocusIn>", self.clear_placeholder)
        self.url.bind("<FocusOut>", self.restore_placeholder)
        self.url.bind("<Return>", self.key_fetch)
        row = tk.Frame(box, bg=self.C["panel"])
        row.pack(fill="x", padx=16, pady=(0, 12))
        self.fetch_btn = self.button(row, "获取", self.start_fetch, True)
        self.fetch_btn.pack(side="left", ipadx=28)
        self.clear_btn = self.button(row, "清空", self.clear_results)
        self.clear_btn.pack(side="left", padx=(10, 0))
        self.save_btn = self.button(row, "导出报告", self.save_results)
        self.save_btn.configure(state=tk.DISABLED)
        self.save_btn.pack(side="left", padx=(10, 0))
        self.raw_btn = self.button(row, "原始响应", self.show_raw_response)
        self.raw_btn.pack(side="left", padx=(10, 0))
        self.robots_check = tk.Checkbutton(
            row, text="遵守 robots.txt", variable=self.respect_robots,
            bg=self.C["panel"], fg=self.C["muted"], activebackground=self.C["panel"],
            activeforeground=self.C["ink"], selectcolor=self.C["panel"],
            font=("Helvetica", 12), cursor="hand2", bd=0, highlightthickness=0)
        self.robots_check.pack(side="right")

    def metrics_strip(self, parent):
        strip = tk.Frame(parent, bg=self.C["bg"])
        strip.pack(fill="x", pady=(0, 10))
        for i, name in enumerate(("状态", "SEO", "链接", "图片", "视频", "标题")):
            tile = tk.Frame(strip, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            strip.grid_columnconfigure(i, weight=1)
            self.label(tile, name, size=9, weight="bold", fg=self.C["muted"], bg=self.C["panel"]).pack(anchor="w", padx=12, pady=(9, 2))
            color = self.C["green"] if name == "状态" else self.C["ink"]
            self.label(tile, var=self.metrics[name], size=15, weight="bold", fg=color, bg=self.C["panel"]).pack(anchor="w", padx=12, pady=(0, 9))

    def preview_panel(self, parent):
        panel = tk.Frame(parent, bg=self.C["panel"], width=326, highlightbackground=self.C["line"], highlightthickness=1)
        panel.grid(row=0, column=1, sticky="ns")
        panel.grid_propagate(False)
        self.label(panel, "媒体检查器", size=15, weight="bold", bg=self.C["panel"]).pack(anchor="w", padx=14, pady=(14, 2))
        self.label(panel, "图片直接预览，点击资源选择下载", size=10, fg=self.C["muted"], bg=self.C["panel"]).pack(anchor="w", padx=14, pady=(0, 10))
        canvas = tk.Canvas(panel, bg=self.C["panel"], bd=0, highlightthickness=0)
        scroll = tk.Scrollbar(panel, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 12))
        scroll.pack(side="right", fill="y", pady=(0, 12))
        self.preview_inner = tk.Frame(canvas, bg=self.C["panel"])
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
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        tools = tk.Frame(frame, bg=self.C["panel"])
        tools.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.button(tools, "复制选中地址", lambda n=name: self.copy_url(n)).pack(side="left")
        self.button(tools, "打开选中地址", lambda n=name: self.open_url(n)).pack(side="left", padx=(8, 0))
        self.button(tools, "复制全部", lambda n=name: self.copy_all(n)).pack(side="left", padx=(8, 0))
        if name in {"图片", "视频"}:
            self.button(tools, "下载选中资源", lambda n=name: self.download_selected(n)).pack(side="left", padx=(8, 0))
            self.button(tools, "批量下载", lambda n=name: self.download_batch(n), primary=True).pack(side="left", padx=(8, 0))

        search = tk.Frame(frame, bg=self.C["alt"], highlightbackground=self.C["line"], highlightthickness=1)
        search.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.label(search, "筛选", size=10, weight="bold", fg=self.C["muted"], bg=self.C["alt"]).pack(side="left", padx=(12, 8), pady=9)
        variable = tk.StringVar()
        entry = tk.Entry(
            search, textvariable=variable, relief="flat", bd=0, font=("Helvetica", 12),
            bg=self.C["alt"], fg=self.C["ink"], insertbackground=self.C["ink"],
            highlightthickness=0,
        )
        entry.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=8)
        self.button(search, "重置", lambda v=variable: v.set("")).pack(side="right", padx=(0, 6), pady=4)

        selectmode = tk.EXTENDED if name in {"图片", "视频"} else tk.BROWSE
        box = tk.Listbox(frame, selectmode=selectmode, relief="flat", bd=0, font=("Helvetica", 14),
                         bg=self.C["alt"], fg=self.C["ink"], selectbackground=self.C["accent_soft"])
        scroll = tk.Scrollbar(frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        box.grid(row=2, column=0, sticky="nsew")
        scroll.grid(row=2, column=1, sticky="ns")
        box.bind("<Double-Button-1>", lambda _e, n=name: self.open_url(n))
        self.pages[name], self.lists[name] = frame, box
        self.filter_vars[name] = variable
        variable.trace_add("write", lambda *_args, n=name: self.apply_filter(n))

    def history_page(self):
        frame = tk.Frame(self.holder, bg=self.C["panel"])
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        tools = tk.Frame(frame, bg=self.C["panel"])
        tools.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.button(tools, "再次抓取", self.rerun_history, True).pack(side="left")
        self.button(tools, "复制网址", self.copy_history_url).pack(side="left", padx=(8, 0))
        self.button(tools, "删除记录", self.delete_history).pack(side="left", padx=(8, 0))
        self.button(tools, "清空历史", self.clear_history).pack(side="left", padx=(8, 0))
        self.label(tools, "任务摘要仅保存在本机", size=10, fg=self.C["muted"], bg=self.C["panel"]).pack(side="right", pady=9)

        self.history_list = tk.Listbox(
            frame, selectmode=tk.BROWSE, relief="flat", bd=0, font=("Helvetica", 13),
            bg=self.C["alt"], fg=self.C["ink"], selectbackground=self.C["accent_soft"],
        )
        scroll = tk.Scrollbar(frame, orient="vertical", command=self.history_list.yview)
        self.history_list.configure(yscrollcommand=scroll.set)
        self.history_list.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.history_list.bind("<Double-Button-1>", lambda _e: self.rerun_history())
        self.pages["历史记录"] = frame
        self.refresh_history()

    def show(self, name):
        if name == "历史记录":
            self.refresh_history()
        self.active_page = name
        for key, page in self.pages.items():
            page.pack_forget()
            self.tabs[key].configure(
                bg=self.C["accent"] if key == name else self.C["side"],
                fg="#FFFFFF" if key == name else self.C["side_text"],
            )
        self.page_title.set(name)
        self.page_hint.set(self.PAGE_HINTS.get(name, ""))
        self.pages[name].pack(fill="both", expand=True)

    def nav_hover(self, name, entering):
        if getattr(self, "active_page", "概览") != name:
            self.tabs[name].configure(bg=self.C["side_alt"] if entering else self.C["side"])

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
        raw_url = self.read_url()
        if not raw_url.strip():
            self.status.set("请输入链接")
            messagebox.showwarning(APP_NAME, "请输入链接")
            return
        try:
            url = normalize_url(raw_url)
        except ValueError as err:
            messagebox.showwarning(APP_NAME, str(err))
            return
        self.write_url(url)
        self.result = None
        self.save_btn.configure(state=tk.DISABLED)
        self.set_text(self.summary, "正在准备新的抓取任务...")
        self.set_text(self.intelligence, "")
        self.set_text(self.body, "")
        self.set_text(self.html, "")
        self.fill_list("链接", [])
        self.fill_list("图片", [])
        self.fill_list("视频", [])
        self.render_media_preview([], [])
        self.set_busy(True, "正在抓取")
        self.show("概览")
        threading.Thread(target=self.worker, args=(url, self.respect_robots.get()), daemon=True).start()

    def worker(self, url, respect_robots):
        try:
            self.q.put(("ok", fetch_url(url, respect_robots=respect_robots)))
        except Exception as err:
            self.q.put(("err", str(err)))

    def show_raw_response(self):
        raw_url = self.read_url()
        if not raw_url.strip():
            self.status.set("请输入链接")
            messagebox.showwarning(APP_NAME, "请输入链接")
            return
        try:
            url = normalize_url(raw_url)
        except ValueError as err:
            messagebox.showwarning(APP_NAME, str(err))
            return
        self.write_url(url)
        self.set_busy(True, "正在获取原始响应")
        threading.Thread(target=self.raw_worker, args=(url, self.respect_robots.get()), daemon=True).start()

    def raw_worker(self, url, respect_robots):
        try:
            self.q.put(("raw_ok", fetch_raw_response(url, respect_robots=respect_robots)))
        except Exception as err:
            self.q.put(("raw_err", str(err)))

    def open_raw_window(self, text):
        win = tk.Toplevel(self.root)
        win.title("服务器原始响应")
        win.geometry("780x640")
        win.configure(bg=self.C["bg"])
        frame = tk.Frame(win, bg=self.C["panel"], highlightbackground=self.C["line"], highlightthickness=1)
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        box = tk.Text(frame, wrap="none", relief="flat", bd=0, padx=16, pady=14,
                      font=("Menlo", 12), bg=self.C["alt"], fg=self.C["ink"],
                      insertbackground=self.C["ink"], selectbackground=self.C["accent_soft"])
        yscroll = tk.Scrollbar(frame, orient="vertical", command=box.yview)
        xscroll = tk.Scrollbar(frame, orient="horizontal", command=box.xview)
        box.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        box.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        box.insert("1.0", text)
        box.configure(state="disabled")

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
            elif kind == "batch_progress":
                current, total, filename = payload
                self.status.set(f"批量下载 {current}/{total}：{filename}")
            elif kind == "batch_done":
                directory, successes, failures = payload
                self.status.set(f"批量下载完成：{len(successes)} 个")
                detail = f"已保存 {len(successes)} 个资源到：\n{directory}"
                if failures:
                    detail += f"\n\n{len(failures)} 个资源下载失败。"
                    messagebox.showwarning(APP_NAME, detail)
                else:
                    messagebox.showinfo(APP_NAME, detail)
            elif kind == "preview_image":
                slot_id, raw = payload
                self.show_preview_image(slot_id, raw)
            elif kind == "preview_error":
                self.mark_preview_unavailable(payload)
            elif kind == "raw_ok":
                self.set_busy(False, "原始响应已获取")
                self.open_raw_window(payload)
            elif kind == "raw_err":
                self.set_busy(False, "获取失败")
                messagebox.showerror(APP_NAME, f"获取原始响应失败：\n{payload}")
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
        self.metrics["SEO"].set(f"{result.seo_report.get('score', 0)}/100")
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
正文字数：{result.word_count:,}
预计阅读：{result.reading_minutes} 分钟
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
        self.set_text(self.intelligence, self.format_intelligence(result))
        self.set_text(self.body, result.text or "未提取到可读正文。")
        self.set_text(self.html, result.html_preview)
        self.fill_list("链接", result.links)
        self.fill_list("图片", result.images)
        self.fill_list("视频", result.videos)
        self.render_media_preview(result.images, result.videos)
        try:
            self.history_store.add_result(result)
            self.refresh_history()
        except OSError:
            self.status.set("抓取完成，历史保存失败")
        self.show("概览")

    def show_error(self, message: str):
        self.set_busy(False, "抓取失败")
        self.metrics["状态"].set("失败")
        self.set_text(self.summary, f"抓取失败：\n{message}\n\n请检查网址、网络连接，或稍后重试。")
        self.show("概览")
        messagebox.showerror(APP_NAME, f"抓取失败：\n{message}")

    def set_busy(self, busy: bool, status: str):
        self.status.set(status)
        state = tk.DISABLED if busy else tk.NORMAL
        self.fetch_btn.configure(text="获取中..." if busy else "获取", state=state)
        self.clear_btn.configure(state=state)
        self.raw_btn.configure(state=state)

    def clear_results(self):
        self.result = None
        self.save_btn.configure(state=tk.DISABLED)
        self.set_text(self.summary, "准备好了。\n\n把网页 URL 粘贴到上方输入框，然后点击“获取”。")
        self.set_text(self.intelligence, "")
        self.set_text(self.body, "")
        self.set_text(self.html, "")
        self.fill_list("链接", [])
        self.fill_list("图片", [])
        self.fill_list("视频", [])
        self.render_media_preview([], [])
        for key, value in {"链接": "0", "图片": "0", "视频": "0", "标题": "0", "SEO": "--", "状态": "未抓取"}.items():
            self.metrics[key].set(value)
        self.status.set("等待 URL")
        self.show("概览")

    def set_text(self, widget, value):
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.see("1.0")

    def format_intelligence(self, result: CrawlResult) -> str:
        seo = result.seo_report or {}
        info = result.page_info or {}
        links = result.link_stats or {}
        contacts = result.contacts or {}
        resources = result.resources or {}
        checks = seo.get("checks", [])
        passed = sum(1 for check in checks if check.get("passed"))

        lines = [
            "SEO 健康度",
            f"{seo.get('score', 0)} / 100  ·  {seo.get('grade', '未评级')}",
            f"通过检查：{passed} / {len(checks)}",
            "",
            "页面身份",
            f"域名：{info.get('domain') or '未知'}",
            f"语言：{info.get('language') or '未声明'}",
            f"站点名称：{info.get('site_name') or '未声明'}",
            f"页面类型：{info.get('page_type') or '未声明'}",
            f"作者：{info.get('author') or '未声明'}",
            f"发布时间：{info.get('published_time') or '未声明'}",
            f"Canonical：{info.get('canonical') or '未声明'}",
            f"Robots：{info.get('robots') or '未声明'}",
            f"生成器：{info.get('generator') or '未声明'}",
            "",
            "内容与链接",
            f"正文字数：{result.word_count:,}",
            f"预计阅读：{result.reading_minutes} 分钟",
            f"链接总数：{links.get('total', len(result.links))}",
            f"内部链接：{links.get('internal', 0)}",
            f"外部链接：{links.get('external', 0)}",
            f"HTTPS 链接：{links.get('https', 0)}",
            f"HTTP 链接：{links.get('http', 0)}",
            "",
            "联系方式与表单",
            f"邮箱：{', '.join(contacts.get('emails', [])) or '未发现'}",
            f"电话：{', '.join(contacts.get('phones', [])) or '未发现'}",
            f"表单数量：{len(result.forms)}",
        ]
        for index, form in enumerate(result.forms[:20], 1):
            lines.append(
                f"  表单 {index}：{form.get('method', 'GET')}  {form.get('action', '')}  "
                f"字段 {form.get('inputs', 0)} / 密码字段 {form.get('password_fields', 0)}"
            )

        lines += [
            "",
            "页面资源",
            f"脚本：{len(resources.get('scripts', []))}",
            f"样式表：{len(resources.get('stylesheets', []))}",
            f"内嵌框架：{len(resources.get('iframes', []))}",
            "",
            "SEO 检查",
        ]
        for check in checks:
            state = "通过" if check.get("passed") else "待优化"
            lines.append(f"[{state}] {check.get('label', '')}：{check.get('detail', '')}")

        issues = seo.get("issues", [])
        lines += ["", "优先建议"]
        lines.extend(f"{index}. {issue}" for index, issue in enumerate(issues, 1))
        if not issues:
            lines.append("当前基础 SEO 检查全部通过。")
        return "\n".join(lines)

    def refresh_history(self):
        self.history_items = self.history_store.list_entries()
        self.history_list.delete(0, tk.END)
        if not self.history_items:
            self.history_list.insert(tk.END, "暂无历史记录。完成一次抓取后会显示在这里。")
            return
        for entry in self.history_items:
            title = self.shorten(str(entry.get("title") or "未发现标题"), 42)
            url = self.shorten(str(entry.get("final_url") or entry.get("requested_url") or ""), 68)
            status = entry.get("status_code") or "--"
            score = entry.get("seo_score", 0)
            fetched_at = entry.get("fetched_at", "")
            self.history_list.insert(
                tk.END,
                f"{fetched_at}    {status}    SEO {score}    {title}    {url}",
            )

    def selected_history(self):
        selection = self.history_list.curselection()
        if not selection or not self.history_items:
            return None
        index = selection[0]
        return self.history_items[index] if index < len(self.history_items) else None

    def rerun_history(self):
        entry = self.selected_history()
        if not entry:
            messagebox.showinfo(APP_NAME, "请先选择一条历史记录。")
            return
        self.write_url(str(entry.get("final_url") or entry.get("requested_url") or ""))
        self.start_fetch()

    def copy_history_url(self):
        entry = self.selected_history()
        if not entry:
            messagebox.showinfo(APP_NAME, "请先选择一条历史记录。")
            return
        url = str(entry.get("final_url") or entry.get("requested_url") or "")
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.status.set("已复制历史网址")

    def delete_history(self):
        entry = self.selected_history()
        if not entry:
            messagebox.showinfo(APP_NAME, "请先选择一条历史记录。")
            return
        self.history_store.delete(str(entry.get("id", "")))
        self.refresh_history()
        self.status.set("历史记录已删除")

    def clear_history(self):
        if not self.history_items:
            return
        if messagebox.askyesno(APP_NAME, "确定清空全部本地历史记录吗？"):
            self.history_store.clear()
            self.refresh_history()
            self.status.set("历史记录已清空")

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
                thumb = tk.Label(card, text="正在加载图片预览...", bg=self.C["alt"], fg=self.C["muted"],
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
        self.apply_filter(name)

    def apply_filter(self, name):
        records = self.records_for(name)
        query = self.filter_vars[name].get().strip().lower()
        if name == "图片":
            label_key, url_key = "alt", "src"
        else:
            label_key, url_key = "text", "url"
        visible = [
            item for item in records
            if not query or query in f"{item.get(label_key, '')} {item.get(url_key, '')}".lower()
        ]
        self.filtered_items[name] = visible
        box = self.lists[name]
        box.delete(0, tk.END)
        for i, item in enumerate(visible, 1):
            box.insert(tk.END, f"{i}. {self.shorten(item.get(label_key) or '无说明', 62)}    {self.shorten(item.get(url_key, ''), 116)}")

    def visible_records(self, name):
        return self.filtered_items[name]

    def selected_url(self, name):
        box = self.lists[name]
        if not box.curselection():
            return None
        records = self.visible_records(name)
        item = records[box.curselection()[0]]
        return item.get("url") or item.get("src")

    def selected_urls(self, name):
        records = self.visible_records(name)
        urls = []
        for index in self.lists[name].curselection():
            if index < len(records):
                item = records[index]
                url = item.get("url") or item.get("src")
                if url:
                    urls.append(url)
        return urls

    def copy_url(self, name):
        url = self.selected_url(name)
        if not url:
            messagebox.showinfo(APP_NAME, "请先选择一条记录。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.status.set("已复制地址")

    def all_urls(self, name):
        urls = [item.get("url") or item.get("src") for item in self.records_for(name)]
        return [u for u in urls if u]

    def copy_all(self, name):
        urls = self.all_urls(name)
        if not urls:
            messagebox.showinfo(APP_NAME, "当前列表为空。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(urls))
        self.status.set(f"已复制全部 {len(urls)} 条地址")

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

    def download_batch(self, name):
        urls = self.selected_urls(name)
        if not urls:
            messagebox.showinfo(APP_NAME, "请先选择一个或多个资源。按住 Shift 或 Command 可多选。")
            return
        directory = filedialog.askdirectory(
            title="选择批量下载文件夹",
            initialdir=str(self.default_save_dir()),
        )
        if not directory:
            return
        self.status.set(f"准备下载 {len(urls)} 个资源")
        threading.Thread(
            target=self.batch_download_worker,
            args=(urls, Path(directory)),
            daemon=True,
        ).start()

    def confirm_download(self, url):
        if messagebox.askyesno(APP_NAME, f"要下载这个资源吗？\n\n{self.shorten(url, 90)}"):
            self.choose_download_path(url)

    def default_save_dir(self):
        downloads = Path.home() / "Downloads"
        return downloads if downloads.exists() else Path.home()

    def choose_download_path(self, url):
        initialdir = self.default_save_dir()
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

    def batch_download_worker(self, urls, directory):
        successes, failures, reserved = [], [], set()
        for index, url in enumerate(urls, 1):
            path = available_download_path(
                directory,
                safe_filename_from_url(url, f"media-{index}"),
                reserved,
            )
            self.q.put(("batch_progress", (index, len(urls), path.name)))
            try:
                download_file(url, path)
                successes.append(str(path))
            except Exception as err:
                path.unlink(missing_ok=True)
                failures.append({"url": url, "error": str(err)})
        self.q.put(("batch_done", (str(directory), successes, failures)))

    def save_results(self):
        if self.result is None:
            messagebox.showinfo(APP_NAME, "当前没有可保存的抓取结果。")
            return
        path = filedialog.asksaveasfilename(
            title="保存抓取结果",
            initialdir=str(self.default_save_dir()),
            initialfile=f"crawler-result-{time.strftime('%Y%m%d-%H%M%S')}.json",
            defaultextension=".json",
            filetypes=(
                ("JSON 数据", "*.json"),
                ("Markdown 报告", "*.md"),
                ("HTML 报告", "*.html"),
                ("CSV 资源清单", "*.csv"),
                ("所有文件", "*.*"),
            ),
        )
        if path:
            try:
                suffix = Path(path).suffix.lower()
                if suffix == ".md":
                    content, encoding = result_to_markdown(self.result), "utf-8"
                elif suffix in {".html", ".htm"}:
                    content, encoding = result_to_html(self.result), "utf-8"
                elif suffix == ".csv":
                    content, encoding = result_to_csv(self.result), "utf-8-sig"
                else:
                    content = json.dumps(self.result.to_dict(), ensure_ascii=False, indent=2)
                    encoding = "utf-8"
                Path(path).write_text(content, encoding=encoding)
            except OSError as err:
                self.status.set("导出失败")
                messagebox.showerror(APP_NAME, f"导出报告失败：\n{err}")
                return
            self.status.set("报告已导出")
            messagebox.showinfo(APP_NAME, "抓取报告已导出。")


def main():
    root = tk.Tk()
    CrawlerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

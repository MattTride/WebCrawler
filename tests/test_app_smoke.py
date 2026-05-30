"""Smoke test: the tkinter app must construct and realize without error.

Realizing every widget surfaces bad color names, missing palette keys and
layout mistakes. Skipped automatically when no display is available
(e.g. headless CI).
"""
import pytest

tk = pytest.importorskip("tkinter")

import crawler_app


def test_app_constructs_and_realizes():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    try:
        app = crawler_app.CrawlerApp(root)
        root.update()  # force every widget to realize
        assert app.respect_robots.get() is True
        app.open_raw_window("HTTP 200 OK\n\nsample body")
        root.update()  # realize the raw-response popup too
    finally:
        root.destroy()

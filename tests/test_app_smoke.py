"""Smoke test: the tkinter app must construct and realize without error.

Realizing every widget surfaces bad color names, missing palette keys and
layout mistakes. Skipped automatically when no display is available
(e.g. headless CI).
"""
import pytest

tk = pytest.importorskip("tkinter")

import crawler_app


def test_app_constructs_and_realizes(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    try:
        app = crawler_app.CrawlerApp(root, history_path=tmp_path / "history.json")
        root.update()  # force every widget to realize
        assert app.respect_robots.get() is True
        assert "页面情报" in app.pages
        assert "历史记录" in app.pages
        assert "SEO" in app.metrics

        app.fill_list("链接", [{"text": "a", "url": "u1"}, {"text": "b", "url": "u2"}])
        assert app.all_urls("链接") == ["u1", "u2"]
        app.filter_vars["链接"].set("b")
        assert app.visible_records("链接") == [{"text": "b", "url": "u2"}]
        app.filter_vars["链接"].set("")

        app.set_busy(True, "busy")
        assert str(app.raw_btn["state"]) == "disabled"
        app.set_busy(False, "idle")
        assert str(app.raw_btn["state"]) == "normal"

        app.open_raw_window("HTTP 200 OK\n\nsample body")
        root.update()  # realize the raw-response popup too
    finally:
        root.destroy()

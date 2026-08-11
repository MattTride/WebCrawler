from pathlib import Path

from crawler_core import CrawlResult
from workspace_store import HistoryStore


def make_result(url: str, title: str = "Page") -> CrawlResult:
    return CrawlResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        reason="OK",
        content_type="text/html",
        encoding="utf-8",
        title=title,
        description="",
        headings=[],
        links=[{"text": "Home", "url": url}],
        images=[],
        videos=[],
        text="Body",
        html_preview="<html>",
        bytes_read=10,
        truncated=False,
        fetched_at="2026-08-11 12:00:00",
        seo_report={"score": 82},
    )


def test_history_store_round_trip_and_delete(tmp_path: Path):
    store = HistoryStore(tmp_path / "history.json")
    first = store.add_result(make_result("https://example.com/a", "First"))
    store.add_result(make_result("https://example.com/b", "Second"))

    entries = store.list_entries()
    assert [entry["title"] for entry in entries] == ["Second", "First"]
    assert entries[0]["links"] == 1
    assert entries[0]["seo_score"] == 82

    assert store.delete(str(first["id"])) is True
    assert [entry["title"] for entry in store.list_entries()] == ["Second"]
    assert store.delete("missing") is False


def test_history_store_deduplicates_and_honors_limit(tmp_path: Path):
    store = HistoryStore(tmp_path / "history.json", limit=2)
    store.add_result(make_result("https://example.com/a", "First"))
    store.add_result(make_result("https://example.com/b", "Second"))
    store.add_result(make_result("https://example.com/a", "First updated"))
    store.add_result(make_result("https://example.com/c", "Third"))

    entries = store.list_entries()
    assert [entry["title"] for entry in entries] == ["Third", "First updated"]


def test_history_store_recovers_from_invalid_json_and_clears(tmp_path: Path):
    path = tmp_path / "history.json"
    path.write_text("not json", encoding="utf-8")
    store = HistoryStore(path)

    assert store.list_entries() == []
    store.add_result(make_result("https://example.com"))
    store.clear()
    assert store.list_entries() == []

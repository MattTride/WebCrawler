"""Local persistence helpers for Crawl Studio."""
from __future__ import annotations

import json
import time
from pathlib import Path

from crawler_core import CrawlResult


HISTORY_LIMIT = 50


def default_history_path() -> Path:
    return Path.home() / ".crawl_studio" / "history.json"


class HistoryStore:
    """Persist small crawl summaries locally without storing page contents."""

    def __init__(self, path: Path | None = None, limit: int = HISTORY_LIMIT):
        self.path = path or default_history_path()
        self.limit = max(1, limit)

    def list_entries(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [entry for entry in payload if isinstance(entry, dict)][:self.limit]

    def add_result(self, result: CrawlResult) -> dict[str, object]:
        entry = {
            "id": str(time.time_ns()),
            "title": result.title,
            "requested_url": result.requested_url,
            "final_url": result.final_url,
            "status_code": result.status_code,
            "fetched_at": result.fetched_at,
            "seo_score": result.seo_report.get("score", 0),
            "links": len(result.links),
            "images": len(result.images),
            "videos": len(result.videos),
        }
        entries = [
            item for item in self.list_entries()
            if item.get("final_url") != result.final_url
        ]
        self._write([entry, *entries][:self.limit])
        return entry

    def delete(self, entry_id: str) -> bool:
        entries = self.list_entries()
        remaining = [entry for entry in entries if entry.get("id") != entry_id]
        if len(remaining) == len(entries):
            return False
        self._write(remaining)
        return True

    def clear(self) -> None:
        self._write([])

    def _write(self, entries: list[dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

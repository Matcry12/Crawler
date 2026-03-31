from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ManifestCollector:
    def __init__(self) -> None:
        self.pages: list[dict[str, Any]] = []
        self.site_stats: dict[str, dict[str, Any]] = {}
        self._start_time: datetime = datetime.now(timezone.utc)

    def add_page(
        self,
        url: str,
        filepath: Path | None,
        depth: int = 0,
        word_count: int = 0,
        status: str = "success",
        title: str = "",
        retries: int = 0,
        error: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "url": url,
            "file": str(filepath.relative_to(filepath.parents[len(filepath.parts) - 2])) if filepath else None,
            "title": title,
            "depth": depth,
            "word_count": word_count,
            "status": status,
            "crawled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if retries > 0:
            entry["retries"] = retries
        if error:
            entry["error"] = error
        self.pages.append(entry)

    def update_site_stats(
        self,
        root_url: str,
        crawled: int,
        failed: int,
        skipped: int,
        max_depth: int,
        retries: int = 0,
    ) -> None:
        self.site_stats[root_url] = {
            "root_url": root_url,
            "pages_crawled": crawled,
            "pages_failed": failed,
            "pages_skipped": skipped,
            "max_depth_reached": max_depth,
            "total_retries": retries,
        }

    def save(self, output_dir: str) -> Path:
        end_time = datetime.now(timezone.utc)
        duration = (end_time - self._start_time).total_seconds()

        status_counts = Counter(p["status"] for p in self.pages)
        total_words = sum(p.get("word_count", 0) for p in self.pages)

        manifest = {
            "generated_at": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool_version": "0.2.0",
            "duration_seconds": round(duration, 1),
            "quality_summary": {
                "total_pages": len(self.pages),
                "success": status_counts.get("success", 0),
                "failed": status_counts.get("failed", 0),
                "empty_content": status_counts.get("empty_content", 0),
                "low_quality": status_counts.get("low_quality", 0),
                "total_words": total_words,
            },
            "sites": list(self.site_stats.values()),
            "pages": self.pages,
        }
        path = Path(output_dir) / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

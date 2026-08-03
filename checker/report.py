"""巡回結果を画面用の JSON（docs/data/feed.json）に書き出す。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import SiteConfig
from .store import SiteState, now_iso

FEED_VERSION = 1

#: 横断タイムラインに載せる最大件数。
TIMELINE_LIMIT = 300


@dataclass(frozen=True)
class SiteResult:
    """1サイトの巡回結果。"""

    site: SiteConfig
    state: SiteState
    new_items: list[dict[str, str]]
    #: ページから抽出できたリンクの総数（設定を調整するときの目安）。
    link_count: int = 0
    #: 初回巡回で、記録のみ行った場合に True。
    seeded_now: bool = False


def build_feed(results: list[SiteResult], generated_at: str | None = None) -> dict[str, Any]:
    """feed.json の中身を組み立てる。"""
    generated_at = generated_at or now_iso()

    sites: list[dict[str, Any]] = []
    timeline: list[dict[str, str]] = []

    for result in results:
        state = result.state
        sites.append(
            {
                "id": result.site.id,
                "name": result.site.name,
                "url": result.site.url,
                "status": state.last_status,
                "error": state.last_error,
                "consecutive_errors": state.consecutive_errors,
                "last_checked": state.last_checked,
                "last_update": state.last_update,
                "new_count": len(result.new_items),
                "link_count": result.link_count,
                "seeded_now": result.seeded_now,
                "items": state.recent,
            }
        )
        for item in state.recent:
            timeline.append(
                {
                    "site_id": result.site.id,
                    "site_name": result.site.name,
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "first_seen": item.get("first_seen", ""),
                }
            )

    timeline.sort(key=lambda item: item.get("first_seen", ""), reverse=True)

    return {
        "version": FEED_VERSION,
        "generated_at": generated_at,
        "site_count": len(results),
        "new_count": sum(len(r.new_items) for r in results),
        "error_count": sum(1 for r in results if r.state.last_status == "error"),
        "sites": sites,
        "timeline": timeline[:TIMELINE_LIMIT],
    }


def write_feed(path: str | Path, feed: dict[str, Any]) -> None:
    """feed.json を書き出す。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

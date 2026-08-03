"""巡回状態（既知リンク）の保存と差分検出。

state.json はサイトごとに「これまでに見たURL」を持つ。次の巡回でそこに無いURLが
出てきたら新着とみなす。初回巡回だけは全リンクが未知になってしまうので、新着0件で
記録だけして終える（``seeded``）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .extractor import Link

STATE_VERSION = 1


def now_iso() -> str:
    """秒精度のUTC ISO8601文字列。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SiteState:
    """1サイト分の巡回状態。"""

    seeded: bool = False
    first_checked: str | None = None
    last_checked: str | None = None
    #: 最後に新着を検知した時刻。
    last_update: str | None = None
    last_status: str = "pending"
    last_error: str | None = None
    consecutive_errors: int = 0
    #: URL -> {"title": str, "first_seen": str}
    known: dict[str, dict[str, str]] = field(default_factory=dict)
    #: 画面に出す新着履歴（新しい順）。
    recent: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SiteState":
        known_raw = raw.get("known") or {}
        known = {
            str(url): {
                "title": str(meta.get("title", "")),
                "first_seen": str(meta.get("first_seen", "")),
            }
            for url, meta in known_raw.items()
            if isinstance(meta, dict)
        }
        recent = [item for item in (raw.get("recent") or []) if isinstance(item, dict)]
        return cls(
            seeded=bool(raw.get("seeded", False)),
            first_checked=raw.get("first_checked"),
            last_checked=raw.get("last_checked"),
            last_update=raw.get("last_update"),
            last_status=str(raw.get("last_status", "pending")),
            last_error=raw.get("last_error"),
            consecutive_errors=int(raw.get("consecutive_errors", 0) or 0),
            known=known,
            recent=recent,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "seeded": self.seeded,
            "first_checked": self.first_checked,
            "last_checked": self.last_checked,
            "last_update": self.last_update,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "consecutive_errors": self.consecutive_errors,
            "known": self.known,
            "recent": self.recent,
        }


@dataclass
class State:
    """state.json 全体。"""

    sites: dict[str, SiteState] = field(default_factory=dict)

    def for_site(self, site_id: str) -> SiteState:
        return self.sites.get(site_id) or SiteState()

    def set_site(self, site_id: str, state: SiteState) -> None:
        self.sites[site_id] = state

    def prune(self, keep_ids: set[str]) -> None:
        """sites.yml から消えたサイトの状態を捨てる。"""
        for site_id in list(self.sites):
            if site_id not in keep_ids:
                del self.sites[site_id]


def load_state(path: str | Path) -> State:
    """state.json を読み込む。無ければ空の状態を返す。"""
    path = Path(path)
    if not path.exists():
        return State()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 壊れていても巡回自体は続けたいので、初回扱いにフォールバックする。
        return State()
    sites_raw = raw.get("sites") if isinstance(raw, dict) else None
    if not isinstance(sites_raw, dict):
        return State()
    return State(sites={sid: SiteState.from_dict(v) for sid, v in sites_raw.items() if isinstance(v, dict)})


def save_state(path: str | Path, state: State) -> None:
    """state.json を書き出す。差分が読みやすいようにキーはソートする。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "updated_at": now_iso(),
        "sites": {sid: state.sites[sid].to_dict() for sid in sorted(state.sites)},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def apply_links(
    previous: SiteState,
    links: list[Link],
    *,
    max_items: int,
    known_limit: int,
    timestamp: str | None = None,
) -> tuple[SiteState, list[dict[str, str]]]:
    """取得したリンク一覧を状態に反映し、(新しい状態, 新着アイテム) を返す。

    初回（``seeded`` が False）は全件を既知として記録するだけで、新着は返さない。
    """
    timestamp = timestamp or now_iso()
    is_first_run = not previous.seeded

    new_items: list[dict[str, str]] = []
    known = dict(previous.known)

    for link in links:
        if link.url in known:
            # タイトルが後から埋まることがあるので拾っておく。
            if link.title and not known[link.url].get("title"):
                known[link.url]["title"] = link.title
            continue
        known[link.url] = {"title": link.title, "first_seen": timestamp}
        if not is_first_run:
            new_items.append({"title": link.title, "url": link.url, "first_seen": timestamp})

    # 上限を超えたぶんは古い順に捨てる。ただし今ページに載っているURLは必ず残す。
    if len(known) > known_limit:
        on_page = {link.url for link in links}
        prunable = sorted(
            (url for url in known if url not in on_page),
            key=lambda url: known[url].get("first_seen", ""),
        )
        for url in prunable[: len(known) - known_limit]:
            del known[url]

    recent = new_items + list(previous.recent)
    state = SiteState(
        seeded=True,
        first_checked=previous.first_checked or timestamp,
        last_checked=timestamp,
        last_update=timestamp if new_items else previous.last_update,
        last_status="ok",
        last_error=None,
        consecutive_errors=0,
        known=known,
        recent=recent[:max_items],
    )
    return state, new_items


def apply_error(previous: SiteState, error: str, timestamp: str | None = None) -> SiteState:
    """取得に失敗したときの状態。既知リンクはそのまま残す。"""
    timestamp = timestamp or now_iso()
    return SiteState(
        seeded=previous.seeded,
        first_checked=previous.first_checked,
        last_checked=timestamp,
        last_update=previous.last_update,
        last_status="error",
        last_error=error,
        consecutive_errors=previous.consecutive_errors + 1,
        known=dict(previous.known),
        recent=list(previous.recent),
    )

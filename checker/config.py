"""sites.yml の読み込みと正規化。

sites.yml は ``defaults`` と ``sites`` の2ブロックからなる。``defaults`` に書いた値は
全サイト共通の初期値になり、各サイトで同じキーを書けばそれが優先される。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; SiteUpdateMonitor/0.1; "
    "+https://github.com/eldoradoshambala-hub)"
)

# サイト単位で指定できるキー。defaults にも同じキーが書ける。
_SITE_KEYS = {
    "selector",
    "include",
    "exclude",
    "use_default_exclude",
    "skip_navigation",
    "allow_hosts",
    "allow_external",
    "min_title_length",
    "max_items",
    "timeout",
    "retries",
    "user_agent",
    "enabled",
}


class ConfigError(Exception):
    """sites.yml の内容が不正なときに送出する。"""


@dataclass(frozen=True)
class SiteConfig:
    """1サイト分の巡回設定。"""

    id: str
    name: str
    url: str
    #: 記事リンクを絞り込む CSS セレクタ。未指定なら全 <a> が対象。
    selector: str | None = None
    #: URL にこの文字列を含むリンクだけを残す（いずれか1つに一致すればよい）。
    include: tuple[str, ...] = ()
    #: URL にこの文字列を含むリンクを除外する。
    exclude: tuple[str, ...] = ()
    #: 組み込みの除外パターン（/feed, /login など）を使うか。
    use_default_exclude: bool = True
    #: <nav> <header> <footer> の中のリンクを除外するか。selector 指定時は無視される。
    skip_navigation: bool = True
    #: 同一サイト扱いにする追加ホスト名。
    allow_hosts: tuple[str, ...] = ()
    #: 外部ドメインへのリンクも新着候補に含めるか。
    allow_external: bool = False
    #: リンクテキストがこの文字数未満なら除外する。
    min_title_length: int = 0
    #: 画面に残す新着履歴の件数。
    max_items: int = 50
    timeout: int = 20
    retries: int = 2
    user_agent: str = DEFAULT_USER_AGENT
    enabled: bool = True

    @property
    def known_limit(self) -> int:
        """state.json に保持する既知URLの上限。"""
        return max(1000, self.max_items * 40)


@dataclass(frozen=True)
class AppConfig:
    """sites.yml 全体。"""

    sites: tuple[SiteConfig, ...] = ()
    #: 同時に巡回するサイト数。
    concurrency: int = 4

    def enabled_sites(self, only: list[str] | None = None) -> list[SiteConfig]:
        """巡回対象のサイトを返す。``only`` を渡すとその id に絞る。"""
        sites = [s for s in self.sites if s.enabled]
        if only:
            wanted = set(only)
            unknown = wanted - {s.id for s in self.sites}
            if unknown:
                raise ConfigError(f"存在しないサイトIDです: {', '.join(sorted(unknown))}")
            sites = [s for s in sites if s.id in wanted]
        return sites


def slugify(value: str) -> str:
    """URL などから id に使える文字列を作る。"""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "site"


def _as_tuple(value: Any, field_name: str, site_label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise ConfigError(f"{site_label}: {field_name} は文字列またはリストで指定してください")


def _coerce(raw: dict[str, Any], site_label: str) -> dict[str, Any]:
    """YAML の生の値を SiteConfig のフィールド型に合わせる。"""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _SITE_KEYS:
            continue
        if key in ("include", "exclude", "allow_hosts"):
            out[key] = _as_tuple(value, key, site_label)
        elif key in ("min_title_length", "max_items", "timeout", "retries"):
            try:
                out[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{site_label}: {key} は整数で指定してください") from exc
        elif key in ("enabled", "allow_external", "use_default_exclude", "skip_navigation"):
            out[key] = bool(value)
        elif value is not None:
            out[key] = str(value)
    return out


def parse_config(raw: Any) -> AppConfig:
    """パース済みの YAML データから AppConfig を組み立てる。"""
    if raw is None:
        raise ConfigError("sites.yml が空です")
    if not isinstance(raw, dict):
        raise ConfigError("sites.yml のトップレベルはマッピングにしてください")

    defaults_raw = raw.get("defaults") or {}
    if not isinstance(defaults_raw, dict):
        raise ConfigError("defaults はマッピングにしてください")

    sites_raw = raw.get("sites")
    if not isinstance(sites_raw, list) or not sites_raw:
        raise ConfigError("sites に監視対象を1件以上書いてください")

    concurrency = int(defaults_raw.get("concurrency", 4) or 4)
    base_overrides = _coerce(defaults_raw, "defaults")

    sites: list[SiteConfig] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(sites_raw, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"sites[{index}]: 各サイトはマッピングにしてください")

        url = str(entry.get("url") or "").strip()
        if not url:
            raise ConfigError(f"sites[{index}]: url は必須です")
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"sites[{index}]: url は http(s):// で始めてください: {url}")

        name = str(entry.get("name") or url).strip()
        site_id = str(entry.get("id") or "").strip() or slugify(name)
        if site_id in seen_ids:
            raise ConfigError(f"id が重複しています: {site_id}")
        seen_ids.add(site_id)

        site = SiteConfig(id=site_id, name=name, url=url)
        site = replace(site, **base_overrides)
        site = replace(site, **_coerce(entry, f"sites[{index}] ({name})"))
        sites.append(site)

    return AppConfig(sites=tuple(sites), concurrency=max(1, concurrency))


def load_config(path: str | Path) -> AppConfig:
    """sites.yml を読み込む。"""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"設定ファイルが見つかりません: {path}")
    with path.open(encoding="utf-8") as fh:
        return parse_config(yaml.safe_load(fh))

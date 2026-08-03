"""HTML から「記事らしいリンク」を抜き出す。

方針は控えめなフィルタリング。「記事ではありえないもの」だけを除外し、
判断に迷うものは残す。取りこぼした記事は気づけないが、余分なリンクは
前回との差分で自然に消えるうえ、一覧に出ても無視できるため。
唯一の例外が <nav> <header> <footer> で、ここに記事一覧が置かれることは
まずないので既定で除外する。うまく取れないサイトは sites.yml の
``selector`` / ``include`` / ``exclude`` で個別に補正する。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .config import SiteConfig

#: 記事本文ではありえない拡張子。
SKIP_SUFFIXES = (
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".exe", ".dmg", ".apk",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".m4a", ".wav",
    ".woff", ".woff2", ".ttf", ".eot",
)

#: 記事ではないと断定できるパスだけを対象にした組み込み除外パターン。
DEFAULT_EXCLUDE_PATTERNS = (
    re.compile(r"/(feed|rss|atom)/?$", re.I),
    re.compile(r"/wp-(json|login|admin)", re.I),
    re.compile(r"/xmlrpc\.php$", re.I),
    re.compile(r"/sitemap(\.xml|/)?$", re.I),
    re.compile(r"/(login|logout|signin|signout|signup|register)/?$", re.I),
)

#: 計測用のクエリパラメータ。同じ記事が別URL扱いになるのを防ぐため落とす。
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "yclid", "igshid", "_ga"}

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Link:
    """抽出した1本のリンク。"""

    url: str
    title: str


def normalize_url(url: str) -> str:
    """比較用にURLを正規化する（フラグメント除去・計測パラメータ除去など）。"""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc.rsplit(":", 1)[0]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc.rsplit(":", 1)[0]

    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_PARAMS
        ]
    )
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


def base_host(host: str) -> str:
    """先頭の www. を落としたホスト名。"""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def is_same_site(link_host: str, page_host: str, allow_hosts: tuple[str, ...] = ()) -> bool:
    """リンク先を同一サイト扱いにしてよいか判定する（サブドメインは同一扱い）。"""
    link_host = link_host.lower()
    if any(link_host == h.lower() or link_host.endswith("." + h.lower()) for h in allow_hosts):
        return True
    root = base_host(page_host)
    return base_host(link_host) == root or link_host.endswith("." + root)


def _clean_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _link_title(anchor) -> str:
    """アンカーの表示文字列。テキストが無ければ画像の alt や title 属性で補う。"""
    text = _clean_text(anchor.get_text(" ", strip=True))
    if text:
        return text
    for image in anchor.find_all("img"):
        alt = _clean_text(image.get("alt") or "")
        if alt:
            return alt
    return _clean_text(anchor.get("title") or "")


#: 記事一覧が置かれることのない領域。ここに入るリンクはナビゲーションとみなす。
NAVIGATION_TAGS = ("nav", "header", "footer")


def in_navigation(anchor) -> bool:
    """<nav> <header> <footer> の中にあるリンクか。"""
    return anchor.find_parent(NAVIGATION_TAGS) is not None


def _anchors(soup: BeautifulSoup, selector: str | None, skip_navigation: bool = True):
    """対象となる <a> 要素を集める。"""
    if not selector:
        anchors = soup.find_all("a", href=True)
        if skip_navigation:
            anchors = [a for a in anchors if not in_navigation(a)]
        return anchors

    # selector を明示しているならその範囲を尊重し、ナビゲーション判定はしない。
    anchors = []
    seen = set()
    for node in soup.select(selector):
        candidates = [node] if node.name == "a" else []
        candidates.extend(node.find_all("a", href=True))
        for anchor in candidates:
            if not anchor.get("href"):
                continue
            if id(anchor) in seen:
                continue
            seen.add(id(anchor))
            anchors.append(anchor)
    return anchors


def parse_html(content: bytes | str, encoding_hint: str | None = None) -> BeautifulSoup:
    """HTML をパースする。バイト列なら meta charset から文字コードを判定させる。"""
    if isinstance(content, bytes):
        return BeautifulSoup(content, "lxml", from_encoding=encoding_hint)
    return BeautifulSoup(content, "lxml")


def extract_links(
    content: bytes | str,
    page_url: str,
    site: SiteConfig,
    encoding_hint: str | None = None,
) -> list[Link]:
    """ページから新着候補のリンクを抽出する。出現順で、URL重複は除去済み。"""
    soup = parse_html(content, encoding_hint)

    # <base href> があれば相対URLの基準はそちら。
    base_tag = soup.find("base", href=True)
    base_url = urljoin(page_url, base_tag["href"]) if base_tag else page_url

    page_host = urlsplit(page_url).netloc
    page_key = normalize_url(page_url)

    links: dict[str, Link] = {}
    for anchor in _anchors(soup, site.selector, site.skip_navigation):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue

        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue

        url = normalize_url(absolute)
        if url == page_key:
            continue

        path = urlsplit(url).path
        if path in ("", "/"):
            # サイトのトップページは更新の目印にならない。
            continue
        if path.lower().endswith(SKIP_SUFFIXES):
            continue
        if not site.allow_external and not is_same_site(parts.netloc, page_host, site.allow_hosts):
            continue
        if site.use_default_exclude and any(p.search(path) for p in DEFAULT_EXCLUDE_PATTERNS):
            continue
        if site.include and not any(needle in url for needle in site.include):
            continue
        if any(needle in url for needle in site.exclude):
            continue

        title = _link_title(anchor)
        if len(title) < site.min_title_length:
            continue

        existing = links.get(url)
        if existing is None:
            links[url] = Link(url=url, title=title)
        elif not existing.title and title:
            # 同じURLが画像リンクとテキストリンクで2回出るケース。文字列がある方を採る。
            links[url] = Link(url=url, title=title)

    return list(links.values())


def path_prefix_stats(links: list[Link], depth: int = 2, top: int = 10) -> list[tuple[str, int]]:
    """URLをディレクトリ単位でまとめた件数。`inspect` で include を決める手がかりに使う。

    記事は同じディレクトリの下に並ぶことが多いので、末尾のファイル名は落として数える。
    """
    counter: Counter[str] = Counter()
    for link in links:
        segments = [s for s in urlsplit(link.url).path.split("/") if s]
        if segments and "." in segments[-1]:
            segments.pop()  # 末尾がファイル名ならディレクトリまでで揃える
        counter["/" + "/".join(segments[:depth])] += 1
    return counter.most_common(top)

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

#: 遅延読み込みで本来の画像URLが入る属性。src には仮の画像が入っていることがあるので先に見る。
_LAZY_SRC_ATTRS = ("data-src", "data-lazy-src", "data-original", "data-lazy", "data-echo")
_SRCSET_ATTRS = ("data-srcset", "data-lazy-srcset", "srcset")
#: 背景画像を遅延読み込みするときの属性。
_LAZY_BACKGROUND_ATTRS = ("data-bg", "data-background", "data-background-image", "data-bg-src")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.+?)\1\s*\)", re.I)

#: 記事の画像ではない（読み込み中の仮画像や「No Image」）とみなすファイル名。
_PLACEHOLDER_IMAGE = re.compile(
    r"no[-_]?(image|img|photo)|now[-_]?printing|spacer|blank|placeholder|loading|lazy", re.I
)

#: width/height 属性がこれ未満の画像はアイコンや計測用とみなす（px）。
MIN_IMAGE_SIZE = 40

#: リンクの中に画像が無いとき、何階層上まで「そのリンク専用の囲み（カード）」を探すか。
CARD_DEPTH = 3


@dataclass(frozen=True)
class Link:
    """抽出した1本のリンク。"""

    url: str
    title: str
    #: 一覧ページで記事に添えられていた画像のURL。見つからなければ空文字。
    image: str = ""


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


def _resolve_href(href: str, base_url: str) -> str | None:
    """href を比較用の絶対URLにする。ページ内リンクや javascript: などは None。"""
    href = href.strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    absolute = urljoin(base_url, href)
    if urlsplit(absolute).scheme not in ("http", "https"):
        return None
    return normalize_url(absolute)


def _image_url(raw: str | None, base_url: str) -> str:
    """画像として使えるURLなら絶対URLにして返す。仮画像やアイコンらしいものは空文字。"""
    raw = (raw or "").strip()
    if not raw or raw.startswith("data:"):
        return ""
    absolute = urljoin(base_url, raw)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or len(absolute) > 2048:
        return ""
    filename = parts.path.rsplit("/", 1)[-1]
    # SVG はほぼアイコン。記事の写真に使われることはまずない。
    if filename.lower().endswith(".svg") or _PLACEHOLDER_IMAGE.search(filename):
        return ""
    return absolute


def _is_tiny(image) -> bool:
    """width/height 属性からアイコンや計測用の画像と分かるか。"""
    for attr in ("width", "height"):
        match = re.match(r"\s*(\d+)", image.get(attr) or "")
        if match and int(match.group(1)) < MIN_IMAGE_SIZE:
            return True
    return False


def _img_source(image, base_url: str) -> str:
    """<img> の画像URL。遅延読み込みの属性、src、srcset の順に見る。"""
    if _is_tiny(image):
        return ""
    for attr in _LAZY_SRC_ATTRS + ("src",):
        url = _image_url(image.get(attr), base_url)
        if url:
            return url
    for attr in _SRCSET_ATTRS:
        # srcset="a.jpg 300w, b.jpg 768w"。URL自体がカンマを含むことがあるので、
        # カンマでは割らずに先頭の候補（最初の空白まで）だけを使う。
        first = (image.get(attr) or "").split()
        if first:
            url = _image_url(first[0].rstrip(","), base_url)
            if url:
                return url
    return ""


def _background_source(node, base_url: str) -> str:
    """style="background-image: url(...)" や data-bg に書かれた画像URL。"""
    for attr in _LAZY_BACKGROUND_ATTRS + ("style",):
        value = node.get(attr)
        if not value:
            continue
        match = _CSS_URL.search(value)
        if match:
            url = _image_url(match.group(2), base_url)
        elif attr != "style":
            url = _image_url(value, base_url)
        else:
            url = ""
        if url:
            return url
    return ""


def _first_image(node, base_url: str) -> str:
    """要素とその中から、最初に見つかった記事画像のURL。"""
    for element in [node, *node.find_all(True)]:
        url = _img_source(element, base_url) if element.name == "img" else ""
        url = url or _background_source(element, base_url)
        if url:
            return url
    return ""


def _link_image(anchor, url: str, base_url: str) -> str:
    """リンクに添える画像のURL。見つからなければ空文字。

    まずリンクの中を探す。無ければ親をたどり、「このリンクと同じURLへのリンクしか
    含まない要素」（記事1件分のカード）の中を探す。別の記事へのリンクが混ざる要素まで
    来たら一覧そのものなので、他の記事の画像を取り違えないようにそこで諦める。
    """
    image = _first_image(anchor, base_url)
    if image:
        return image

    def links_elsewhere(tag) -> bool:
        return tag.name == "a" and _resolve_href(tag.get("href") or "", base_url) not in (None, url)

    node = anchor
    for _ in range(CARD_DEPTH):
        node = node.parent
        if node is None or node.name in ("body", "html", "[document]"):
            break
        # find() は最初の1件で打ち切るので、長い一覧でも全リンクを調べずに済む。
        if node.find(links_elsewhere) is not None:
            return ""
        image = _first_image(node, base_url)
        if image:
            return image
    return ""


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
        url = _resolve_href(anchor.get("href") or "", base_url)
        if url is None or url == page_key:
            continue

        parts = urlsplit(url)
        path = parts.path
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
        if existing is not None and existing.title and existing.image:
            continue
        image = _link_image(anchor, url, base_url)
        if existing is None:
            links[url] = Link(url=url, title=title, image=image)
        else:
            # 同じURLが画像リンクとテキストリンクで2回出るケース。欠けている方を補い合う。
            links[url] = Link(url=url, title=existing.title or title, image=existing.image or image)

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

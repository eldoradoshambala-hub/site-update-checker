from checker.config import SiteConfig
from checker.extractor import Link, extract_links, is_same_site, normalize_url, path_prefix_stats

PAGE_URL = "https://news.example.jp/list/"


def site(**kwargs) -> SiteConfig:
    return SiteConfig(id="t", name="t", url=PAGE_URL, **kwargs)


def urls(links):
    return [link.url for link in links]


HTML = """
<html><head><title>一覧</title></head><body>
<nav>
  <a href="/">トップ</a>
  <a href="/list/">ニュース</a>
  <a href="/login">ログイン</a>
</nav>
<ul class="articles">
  <li><a href="/article/2026/001.html">記事タイトル その1</a></li>
  <li><a href="../article/2026/002.html">記事タイトル その2</a></li>
  <li><a href="https://news.example.jp/article/2026/003.html?utm_source=x#top">記事タイトル その3</a></li>
</ul>
<aside>
  <a href="https://other.example.com/ad">外部の広告</a>
  <a href="/assets/logo.png">画像</a>
  <a href="/feed">RSS</a>
  <a href="mailto:info@example.jp">メール</a>
  <a href="javascript:void(0)">スクリプト</a>
  <a href="#main">ページ内</a>
</aside>
</body></html>
"""


def test_extracts_article_links_and_drops_non_articles():
    links = extract_links(HTML, PAGE_URL, site())
    assert urls(links) == [
        "https://news.example.jp/article/2026/001.html",
        "https://news.example.jp/article/2026/002.html",
        "https://news.example.jp/article/2026/003.html",
    ]
    assert links[0].title == "記事タイトル その1"


def test_relative_and_tracking_and_fragment_are_normalized():
    links = urls(extract_links(HTML, PAGE_URL, site()))
    # ../ を含む相対パスは絶対化され、utm_ とフラグメントは落ちる。
    assert "https://news.example.jp/article/2026/002.html" in links
    assert "https://news.example.jp/article/2026/003.html" in links


def test_selector_narrows_the_scope():
    links = extract_links(HTML, PAGE_URL, site(selector="ul.articles a"))
    assert len(links) == 3
    links = extract_links(HTML, PAGE_URL, site(selector="nav a"))
    # nav 内でもトップページ・自分自身・ログインは除外される。
    assert links == []


def test_include_and_exclude_filters():
    assert len(extract_links(HTML, PAGE_URL, site(include=("/article/2026/00",)))) == 3
    assert urls(extract_links(HTML, PAGE_URL, site(exclude=("001", "002")))) == [
        "https://news.example.jp/article/2026/003.html"
    ]


def test_allow_external_includes_other_domains():
    links = extract_links(HTML, PAGE_URL, site(allow_external=True))
    assert "https://other.example.com/ad" in urls(links)


NAV_HTML = """
<html><body>
<header><a href="/a/logo">サイト名</a></header>
<nav><a href="/gourmet/">グルメ</a><a href="/event/">イベント</a></nav>
<main>
  <a href="/event/20260803-matsuri/">花火大会が今年も開催されます</a>
  <a href="/gourmet/20260802-cafe/">新しいカフェがオープン</a>
</main>
<footer><a href="/company/">会社案内</a><a href="/privacy2/">プライバシーポリシー</a></footer>
</body></html>
"""


def test_navigation_header_and_footer_links_are_skipped():
    # 記事一覧が <nav> や <footer> に置かれることはないので、既定で除外する。
    links = extract_links(NAV_HTML, PAGE_URL, site())
    assert urls(links) == [
        "https://news.example.jp/event/20260803-matsuri/",
        "https://news.example.jp/gourmet/20260802-cafe/",
    ]


def test_navigation_skipping_can_be_turned_off():
    links = urls(extract_links(NAV_HTML, PAGE_URL, site(skip_navigation=False)))
    assert "https://news.example.jp/gourmet/" in links
    assert "https://news.example.jp/company/" in links


def test_selector_takes_precedence_over_navigation_skipping():
    # selector を書いた人の指定を尊重し、<nav> の中でも拾う。
    links = extract_links(NAV_HTML, PAGE_URL, site(selector="nav a"))
    assert urls(links) == ["https://news.example.jp/gourmet/", "https://news.example.jp/event/"]


def test_links_nested_deep_inside_a_footer_are_still_skipped():
    html = '<footer><div><ul><li><a href="/x/1">奥に入ったフッタのリンク</a></li></ul></div></footer>'
    assert extract_links(html, PAGE_URL, site()) == []


def test_default_exclude_can_be_turned_off():
    # /login はサンプルHTMLの <nav> の中にあるので、そちらの除外も併せて切る。
    links = extract_links(HTML, PAGE_URL, site(use_default_exclude=False, skip_navigation=False))
    assert "https://news.example.jp/login" in urls(links)


def test_min_title_length_drops_short_labels():
    html = '<a href="/a/1">続き</a><a href="/a/2">きちんとした記事タイトル</a>'
    links = extract_links(html, PAGE_URL, site(min_title_length=5))
    assert urls(links) == ["https://news.example.jp/a/2"]


def test_title_falls_back_to_image_alt_then_title_attribute():
    html = (
        '<a href="/a/1"><img src="x.png" alt="画像記事"></a>'
        '<a href="/a/2" title="属性のタイトル"><img src="y.png"></a>'
    )
    links = extract_links(html, PAGE_URL, site())
    assert [link.title for link in links] == ["画像記事", "属性のタイトル"]


def test_duplicate_urls_keep_the_one_with_text():
    html = '<a href="/a/1"><img src="x.png"></a><a href="/a/1">本当のタイトル</a>'
    links = extract_links(html, PAGE_URL, site())
    assert len(links) == 1
    assert links[0].title == "本当のタイトル"


def test_base_tag_is_honoured():
    html = '<head><base href="https://news.example.jp/2026/"></head><body><a href="a.html">記事</a></body>'
    links = extract_links(html, PAGE_URL, site())
    assert urls(links) == ["https://news.example.jp/2026/a.html"]


def test_shift_jis_bytes_are_decoded_via_meta_charset():
    html = (
        '<html><head><meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
        '</head><body><a href="/a/1">日本語の見出し</a></body></html>'
    ).encode("shift_jis")
    links = extract_links(html, PAGE_URL, site())
    assert links[0].title == "日本語の見出し"


def test_normalize_url_strips_defaults_and_tracking():
    assert normalize_url("HTTPS://News.Example.JP:443/a?utm_medium=x&id=3#f") == (
        "https://news.example.jp/a?id=3"
    )
    assert normalize_url("http://example.jp:80") == "http://example.jp/"


def test_is_same_site_allows_subdomains_and_extra_hosts():
    assert is_same_site("news.example.jp", "www.example.jp")
    assert is_same_site("www.example.jp", "example.jp")
    assert not is_same_site("example.com", "example.jp")
    assert is_same_site("cdn.partner.com", "example.jp", allow_hosts=("partner.com",))


def test_path_prefix_stats_groups_by_directory():
    links = extract_links(HTML, PAGE_URL, site())
    # 末尾のファイル名は落として、記事が並ぶディレクトリ単位でまとまる。
    assert path_prefix_stats(links)[0] == ("/article/2026", 3)


def test_path_prefix_stats_puts_top_level_pages_under_root():
    links = [Link(url="https://a.jp/company.html", title="A"), Link(url="https://a.jp/x/y/1", title="B")]
    assert dict(path_prefix_stats(links)) == {"/": 1, "/x/y": 1}

"""ローカルにHTTPサーバーを立てて、実際にHTTP経由で巡回する統合テスト。"""

from __future__ import annotations

import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from checker.config import parse_config
from checker.main import run
from checker.report import build_feed, write_feed
from checker.store import State, load_state, save_state

LIST_PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>お知らせ</title></head>
<body>
  <nav><a href="/">ホーム</a><a href="/about.html">会社情報</a></nav>
  <ul id="news">
    {items}
  </ul>
  <footer><a href="/privacy.html">プライバシーポリシー</a></footer>
</body></html>
"""


def write_list_page(root, articles):
    """記事一覧ページを書き出す。``articles`` は (URLのスラグ, 見出し) の並び。

    実サイトと同じく、記事ごとにURLは固定で、新しい記事が先頭に積まれる想定。
    """
    items = "\n".join(
        f'<li><a href="/news/{slug}.html">{title}</a></li>' for slug, title in articles
    )
    (root / "news").mkdir(exist_ok=True)
    (root / "news" / "index.html").write_text(LIST_PAGE.format(items=items), encoding="utf-8")


@pytest.fixture
def server(tmp_path):
    """tmp_path をドキュメントルートにした HTTP サーバー。"""
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def make_config(base_url, **overrides):
    site = {"id": "local", "name": "ローカル検証サイト", "url": f"{base_url}/news/", "retries": 0}
    site.update(overrides)
    return parse_config(yaml.safe_load(yaml.safe_dump({"sites": [site]})))


def test_crawl_detects_only_newly_added_articles(tmp_path, server):
    write_list_page(tmp_path, [("a", "最初のお知らせ"), ("b", "2番目のお知らせ")])
    config = make_config(server)
    state = State()

    first = run(config, state, timestamp="2026-08-03T00:00:00+00:00")[0]
    assert first.state.last_status == "ok"
    assert first.seeded_now is True
    # 初回は記事もナビゲーションも区別せず記録するだけで、新着は報告しない。
    assert first.new_items == []
    assert first.link_count == 4  # 記事2件 + /about.html + /privacy.html
    state.set_site("local", first.state)

    # 2回目：ページが変わっていなければ新着ゼロ。
    # 毎回出てくるナビゲーションやフッターのリンクは既知になっているので出てこない。
    second = run(config, state, timestamp="2026-08-03T03:00:00+00:00")[0]
    assert second.new_items == []
    assert second.seeded_now is False
    state.set_site("local", second.state)

    # 3回目：記事を1本足すとそれだけが新着になる。
    write_list_page(
        tmp_path, [("c", "新しいお知らせ"), ("a", "最初のお知らせ"), ("b", "2番目のお知らせ")]
    )
    third = run(config, state, timestamp="2026-08-03T12:00:00+00:00")[0]
    assert [item["title"] for item in third.new_items] == ["新しいお知らせ"]
    assert [item["url"] for item in third.new_items] == [f"{server}/news/c.html"]
    assert third.state.last_update == "2026-08-03T12:00:00+00:00"


def test_feed_json_is_written_for_the_frontend(tmp_path, server):
    write_list_page(tmp_path, [("a", "お知らせA")])
    config = make_config(server)
    state = State()

    for result in run(config, state, timestamp="2026-08-03T00:00:00+00:00"):  # 初回登録
        state.set_site(result.site.id, result.state)

    write_list_page(tmp_path, [("b", "お知らせB"), ("a", "お知らせA")])
    results = run(config, state, timestamp="2026-08-03T03:00:00+00:00")
    for result in results:
        state.set_site(result.site.id, result.state)

    out = tmp_path / "feed.json"
    write_feed(out, build_feed(results, generated_at="2026-08-03T03:00:00+00:00"))
    feed = json.loads(out.read_text(encoding="utf-8"))

    assert feed["new_count"] == 1
    assert feed["error_count"] == 0
    assert feed["sites"][0]["name"] == "ローカル検証サイト"
    assert feed["sites"][0]["status"] == "ok"
    assert feed["timeline"][0]["title"] == "お知らせB"
    assert feed["timeline"][0]["site_name"] == "ローカル検証サイト"
    assert feed["timeline"][0]["url"] == f"{server}/news/b.html"


def test_selector_limits_extraction_to_the_news_list(tmp_path, server):
    write_list_page(tmp_path, [("a", "お知らせA")])
    config = make_config(server, selector="#news a", use_default_exclude=False)
    result = run(config, State(), timestamp="2026-08-03T00:00:00+00:00")[0]
    assert result.link_count == 1


def test_missing_page_is_recorded_as_an_error(tmp_path, server):
    config = make_config(server, url=f"{server}/does-not-exist/")
    result = run(config, State(), timestamp="2026-08-03T00:00:00+00:00")[0]
    assert result.state.last_status == "error"
    assert "404" in result.state.last_error
    assert result.new_items == []


def test_unreachable_host_does_not_stop_other_sites(tmp_path, server):
    write_list_page(tmp_path, [("a", "お知らせA")])
    raw = {
        "sites": [
            {"id": "dead", "name": "落ちているサイト", "url": "http://127.0.0.1:1/news/", "retries": 0},
            {"id": "local", "name": "生きているサイト", "url": f"{server}/news/", "retries": 0},
        ]
    }
    results = {r.site.id: r for r in run(parse_config(raw), State(), timestamp="2026-08-03T00:00:00+00:00")}
    assert results["dead"].state.last_status == "error"
    assert results["local"].state.last_status == "ok"


def test_state_survives_a_save_and_reload_cycle(tmp_path, server):
    write_list_page(tmp_path, [("a", "お知らせA")])
    config = make_config(server)
    path = tmp_path / "state.json"

    state = State()
    for result in run(config, state, timestamp="2026-08-03T00:00:00+00:00"):
        state.set_site(result.site.id, result.state)
    save_state(path, state)

    # 別プロセスを想定してディスクから読み直す。既知リンクが引き継がれ、新着は出ない。
    reloaded = load_state(path)
    result = run(config, reloaded, timestamp="2026-08-03T03:00:00+00:00")[0]
    assert result.new_items == []
    assert result.seeded_now is False

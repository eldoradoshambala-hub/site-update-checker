"""既知URLの取り込み直し。登録済みサイトの現在の記事を一覧に出したいときに使う。"""

import json

import yaml

from checker.extractor import Link
from checker.main import command_refill, refill
from checker.store import SiteState, State, apply_links, load_state, save_state


def seeded_state(*urls) -> SiteState:
    state = SiteState(seeded=True)
    for url in urls:
        state, _ = apply_links(
            state,
            [Link(url=url, title=f"記事 {url}")],
            max_items=50,
            known_limit=1000,
            timestamp="2026-08-03T00:00:00+00:00",
        )
    return state


def test_refill_clears_known_urls_and_history():
    state = State(sites={"a": seeded_state("https://a/1", "https://a/2")})
    assert refill(state, ["a"]) == 1

    after = state.for_site("a")
    assert after.known == {}
    assert after.recent == []
    assert after.last_update is None
    # 次の巡回を「初回」扱いにしないので、全リンクが新着として報告される。
    assert after.seeded is True


def test_next_crawl_after_refill_reports_current_articles_as_new():
    state = State(sites={"a": seeded_state("https://a/1", "https://a/2")})
    refill(state, ["a"])

    current = [Link(url="https://a/1", title="記事1"), Link(url="https://a/2", title="記事2")]
    _, new_items = apply_links(
        state.for_site("a"), current, max_items=50, known_limit=1000,
        timestamp="2026-08-03T03:00:00+00:00",
    )
    assert [item["url"] for item in new_items] == ["https://a/1", "https://a/2"]


def test_refill_only_touches_named_sites():
    state = State(sites={"a": seeded_state("https://a/1"), "b": seeded_state("https://b/1")})
    refill(state, ["a"])
    assert state.for_site("a").known == {}
    assert state.for_site("b").known != {}


def test_refill_marks_a_never_crawled_site_so_its_first_crawl_reports_items():
    state = State()
    refill(state, ["fresh"])
    assert state.for_site("fresh").seeded is True


def _write_project(tmp_path):
    config = {"sites": [{"id": "a", "name": "A", "url": "https://a.example/"},
                        {"id": "b", "name": "B", "url": "https://b.example/"}]}
    (tmp_path / "sites.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    state = State(sites={"a": seeded_state("https://a/1"), "b": seeded_state("https://b/1")})
    save_state(tmp_path / "state.json", state)


class Args:
    def __init__(self, tmp_path, only=None):
        self.config = str(tmp_path / "sites.yml")
        self.state = str(tmp_path / "state.json")
        self.output = str(tmp_path / "feed.json")
        self.only = only


def test_command_refill_rewrites_state_and_feed(tmp_path):
    _write_project(tmp_path)
    assert command_refill(Args(tmp_path)) == 0

    state = load_state(tmp_path / "state.json")
    assert state.for_site("a").known == {}
    assert state.for_site("b").known == {}

    feed = json.loads((tmp_path / "feed.json").read_text(encoding="utf-8"))
    assert feed["timeline"] == []
    assert all(s["items"] == [] for s in feed["sites"])


def test_command_refill_with_only_leaves_other_sites_alone(tmp_path):
    _write_project(tmp_path)
    assert command_refill(Args(tmp_path, only=["a"])) == 0

    state = load_state(tmp_path / "state.json")
    assert state.for_site("a").known == {}
    assert state.for_site("b").known != {}


def test_command_refill_rejects_an_unknown_site_id(tmp_path, capsys):
    _write_project(tmp_path)
    assert command_refill(Args(tmp_path, only=["zzz"])) == 2
    assert "存在しないサイトID" in capsys.readouterr().err

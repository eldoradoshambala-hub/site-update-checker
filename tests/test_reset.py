"""新着履歴のリセット。include や selector を調整した直後の後始末に使う。"""

import json

import yaml

from checker.extractor import Link
from checker.main import command_reset, reset_history
from checker.store import SiteState, State, apply_links, load_state, save_state


def seeded_state(*urls) -> SiteState:
    """既知URLと新着履歴の両方を持った状態を作る。"""
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


def test_reset_clears_history_but_keeps_known_urls():
    state = State(sites={"a": seeded_state("https://a/1", "https://a/2")})
    before = dict(state.for_site("a").known)
    assert state.for_site("a").recent  # 前提: 履歴がある

    assert reset_history(state, ["a"]) == 1

    after = state.for_site("a")
    assert after.recent == []
    assert after.last_update is None
    # 既知URLが残っているので、消した記事が再び新着として出ることはない。
    assert after.known == before
    assert after.seeded is True


def test_reset_only_touches_named_sites():
    state = State(sites={"a": seeded_state("https://a/1"), "b": seeded_state("https://b/1")})
    reset_history(state, ["a"])
    assert state.for_site("a").recent == []
    assert state.for_site("b").recent != []


def test_reset_reports_zero_when_there_is_nothing_to_clear():
    state = State(sites={"a": SiteState(seeded=True)})
    assert reset_history(state, ["a"]) == 0


def test_reset_of_an_unknown_site_is_harmless():
    state = State()
    assert reset_history(state, ["missing"]) == 0


def _write_project(tmp_path):
    config = {"sites": [{"id": "a", "name": "A", "url": "https://a.example/"},
                        {"id": "b", "name": "B", "url": "https://b.example/"}]}
    (tmp_path / "sites.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    state = State(sites={"a": seeded_state("https://a/1"), "b": seeded_state("https://b/1")})
    save_state(tmp_path / "state.json", state)
    return tmp_path


class Args:
    def __init__(self, tmp_path, only=None):
        self.config = str(tmp_path / "sites.yml")
        self.state = str(tmp_path / "state.json")
        self.output = str(tmp_path / "feed.json")
        self.only = only


def test_command_reset_rewrites_state_and_feed(tmp_path):
    _write_project(tmp_path)
    assert command_reset(Args(tmp_path)) == 0

    state = load_state(tmp_path / "state.json")
    assert state.for_site("a").recent == []
    assert state.for_site("b").recent == []
    assert state.for_site("a").known  # 既知URLは残る

    feed = json.loads((tmp_path / "feed.json").read_text(encoding="utf-8"))
    assert feed["new_count"] == 0
    # 画面に出る履歴が空になっている。
    assert feed["timeline"] == []
    assert [s["id"] for s in feed["sites"]] == ["a", "b"]
    assert all(s["items"] == [] for s in feed["sites"])


def test_command_reset_with_only_leaves_other_sites_alone(tmp_path):
    _write_project(tmp_path)
    assert command_reset(Args(tmp_path, only=["a"])) == 0

    feed = json.loads((tmp_path / "feed.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in feed["sites"]}
    assert by_id["a"]["items"] == []
    assert by_id["b"]["items"] != []


def test_command_reset_rejects_an_unknown_site_id(tmp_path, capsys):
    _write_project(tmp_path)
    assert command_reset(Args(tmp_path, only=["zzz"])) == 2
    assert "存在しないサイトID" in capsys.readouterr().err

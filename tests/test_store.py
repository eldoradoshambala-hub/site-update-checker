import json

from checker.extractor import Link
from checker.store import SiteState, State, apply_error, apply_links, load_state, save_state


def links(*specs):
    return [Link(url=url, title=title) for url, title in specs]


def test_first_run_records_without_reporting_new_items():
    state, new_items = apply_links(
        SiteState(),
        links(("https://a/1", "記事1"), ("https://a/2", "記事2")),
        max_items=50,
        known_limit=1000,
        timestamp="2026-08-03T00:00:00+00:00",
    )
    assert new_items == []
    assert state.seeded is True
    assert set(state.known) == {"https://a/1", "https://a/2"}
    assert state.recent == []
    # 新着が無いので「最後の更新」はまだ無い。
    assert state.last_update is None


def test_second_run_reports_only_the_added_link():
    first, _ = apply_links(SiteState(), links(("https://a/1", "記事1")), max_items=50, known_limit=1000)
    second, new_items = apply_links(
        first,
        links(("https://a/2", "記事2"), ("https://a/1", "記事1")),
        max_items=50,
        known_limit=1000,
        timestamp="2026-08-03T03:00:00+00:00",
    )
    assert [item["url"] for item in new_items] == ["https://a/2"]
    assert second.last_update == "2026-08-03T03:00:00+00:00"
    assert [item["url"] for item in second.recent] == ["https://a/2"]


def test_unchanged_page_keeps_previous_last_update():
    first, _ = apply_links(SiteState(), links(("https://a/1", "記事1")), max_items=50, known_limit=1000)
    second, _ = apply_links(first, links(("https://a/2", "記事2")), max_items=50, known_limit=1000,
                            timestamp="2026-08-03T03:00:00+00:00")
    third, new_items = apply_links(second, links(("https://a/2", "記事2")), max_items=50, known_limit=1000,
                                   timestamp="2026-08-03T12:00:00+00:00")
    assert new_items == []
    assert third.last_update == "2026-08-03T03:00:00+00:00"
    assert third.last_checked == "2026-08-03T12:00:00+00:00"


def test_recent_history_is_capped_and_newest_first():
    state = SiteState(seeded=True)
    for i in range(10):
        state, _ = apply_links(
            state,
            links((f"https://a/{i}", f"記事{i}")),
            max_items=3,
            known_limit=1000,
            timestamp=f"2026-08-03T{i:02d}:00:00+00:00",
        )
    assert [item["url"] for item in state.recent] == ["https://a/9", "https://a/8", "https://a/7"]


def test_pruning_never_drops_links_still_on_the_page():
    state = SiteState(seeded=True)
    # 古い記事を大量に既知にする。
    state, _ = apply_links(
        state,
        links(*[(f"https://a/old{i}", f"古{i}") for i in range(10)]),
        max_items=50,
        known_limit=100,
        timestamp="2026-08-01T00:00:00+00:00",
    )
    current = links(*[(f"https://a/old{i}", f"古{i}") for i in range(8)])
    state, _ = apply_links(state, current, max_items=50, known_limit=5,
                           timestamp="2026-08-03T00:00:00+00:00")
    # 上限は5だが、ページに載っている8件は消されない。
    assert len(state.known) == 8
    for link in current:
        assert link.url in state.known


def test_title_is_backfilled_for_already_known_urls():
    state, _ = apply_links(SiteState(seeded=True), links(("https://a/1", "")), max_items=50, known_limit=1000)
    state, new_items = apply_links(state, links(("https://a/1", "あとから付いた見出し")),
                                   max_items=50, known_limit=1000)
    assert new_items == []
    assert state.known["https://a/1"]["title"] == "あとから付いた見出し"


def test_apply_error_keeps_known_links_and_counts_failures():
    ok, _ = apply_links(SiteState(), links(("https://a/1", "記事1")), max_items=50, known_limit=1000)
    failed = apply_error(ok, "HTTP 503", timestamp="2026-08-03T03:00:00+00:00")
    assert failed.last_status == "error"
    assert failed.consecutive_errors == 1
    assert failed.known == ok.known
    assert failed.seeded is True

    again = apply_error(failed, "タイムアウト（20秒）")
    assert again.consecutive_errors == 2


def test_state_round_trips_through_disk(tmp_path):
    state = State()
    site_state, _ = apply_links(SiteState(), links(("https://a/1", "記事1")), max_items=50, known_limit=1000)
    state.set_site("a", site_state)

    path = tmp_path / "state.json"
    save_state(path, state)
    restored = load_state(path)

    assert restored.for_site("a").to_dict() == site_state.to_dict()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_broken_state_file_falls_back_to_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_state(path).sites == {}


def test_prune_drops_sites_removed_from_config():
    state = State(sites={"a": SiteState(), "b": SiteState()})
    state.prune({"a"})
    assert set(state.sites) == {"a"}

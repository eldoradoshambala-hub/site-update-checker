"""巡回のエントリポイント。

    python -m checker                      # 全サイトを巡回して feed.json を更新
    python -m checker --only asahi         # 特定サイトだけ
    python -m checker --dry-run            # 状態を書き換えずに結果だけ表示
    python -m checker inspect <URL>        # 抽出結果を確認して設定を詰める
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import AppConfig, ConfigError, SiteConfig, load_config
from .extractor import extract_links, path_prefix_stats
from .fetcher import fetch
from .report import SiteResult, build_feed, write_feed
from .store import State, apply_error, apply_links, load_state, now_iso, save_state

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "sites.yml"
DEFAULT_STATE = ROOT / "data" / "state.json"
DEFAULT_OUTPUT = ROOT / "docs" / "data" / "feed.json"


def check_site(site: SiteConfig, previous, timestamp: str) -> SiteResult:
    """1サイトを取得して差分を取る。例外は投げずに結果へ畳み込む。"""
    result = fetch(site.url, timeout=site.timeout, user_agent=site.user_agent, retries=site.retries)
    if not result.ok:
        return SiteResult(
            site=site,
            state=apply_error(previous, result.error or "取得に失敗しました", timestamp),
            new_items=[],
        )

    try:
        links = extract_links(result.content, result.url, site, result.encoding_hint)
    except Exception as exc:  # パーサが想定外のHTMLで落ちても巡回全体は止めない
        return SiteResult(
            site=site,
            state=apply_error(previous, f"解析エラー: {exc.__class__.__name__}", timestamp),
            new_items=[],
        )

    if not links:
        return SiteResult(
            site=site,
            state=apply_error(previous, "リンクを1件も抽出できませんでした", timestamp),
            new_items=[],
        )

    was_seeded = previous.seeded
    state, new_items = apply_links(
        previous,
        links,
        max_items=site.max_items,
        known_limit=site.known_limit,
        timestamp=timestamp,
    )
    return SiteResult(
        site=site,
        state=state,
        new_items=new_items,
        link_count=len(links),
        seeded_now=not was_seeded,
    )


def run(
    config: AppConfig,
    state: State,
    *,
    only: list[str] | None = None,
    timestamp: str | None = None,
) -> list[SiteResult]:
    """対象サイトを並行に巡回する。"""
    timestamp = timestamp or now_iso()
    sites = config.enabled_sites(only)
    if not sites:
        return []

    with ThreadPoolExecutor(max_workers=min(config.concurrency, len(sites))) as pool:
        results = list(
            pool.map(lambda s: check_site(s, state.for_site(s.id), timestamp), sites)
        )
    return results


def _print_summary(results: list[SiteResult]) -> None:
    for result in sorted(results, key=lambda r: (r.state.last_status != "error", -len(r.new_items))):
        if result.state.last_status == "error":
            print(f"  [失敗] {result.site.name}: {result.state.last_error}")
        elif result.seeded_now:
            print(f"  [初回] {result.site.name}: {result.link_count}件のリンクを記録（新着判定は次回から）")
        elif result.new_items:
            print(f"  [新着] {result.site.name}: {len(result.new_items)}件")
            for item in result.new_items[:5]:
                print(f"         - {item['title'][:60] or '(タイトルなし)'}")
            if len(result.new_items) > 5:
                print(f"         … 他 {len(result.new_items) - 5}件")
        else:
            print(f"  [更新なし] {result.site.name}")


def command_check(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2

    state = load_state(args.state)
    timestamp = now_iso()
    results = run(config, state, only=args.only, timestamp=timestamp)

    if not results:
        print("巡回対象のサイトがありません。sites.yml を確認してください。", file=sys.stderr)
        return 1

    print(f"巡回完了 ({timestamp}) — {len(results)}サイト")
    _print_summary(results)

    new_total = sum(len(r.new_items) for r in results)
    error_total = sum(1 for r in results if r.state.last_status == "error")
    print(f"新着 {new_total}件 / エラー {error_total}件")

    if args.dry_run:
        print("--dry-run のため state.json と feed.json は更新していません。")
        return 0

    for result in results:
        state.set_site(result.site.id, result.state)

    # --only で一部だけ巡回したときも、画面には全サイトを出したいので前回の状態を使う。
    all_results = list(results)
    checked = {r.site.id for r in results}
    for site in config.sites:
        if site.id not in checked:
            all_results.append(SiteResult(site=site, state=state.for_site(site.id), new_items=[]))

    state.prune({s.id for s in config.sites})
    save_state(args.state, state)
    write_feed(args.output, build_feed(all_results, generated_at=timestamp))
    print(f"書き出し: {args.state} / {args.output}")
    return 0 if error_total == 0 else 0  # 一部失敗でもワークフローは成功扱いにする


def command_inspect(args: argparse.Namespace) -> int:
    """1ページ分の抽出結果を表示する。selector や include を決めるための確認用。"""
    site = SiteConfig(id="inspect", name="inspect", url=args.url, selector=args.selector)
    result = fetch(site.url, timeout=site.timeout, user_agent=site.user_agent, retries=1)
    if not result.ok:
        print(f"取得に失敗しました: {result.error}", file=sys.stderr)
        return 1

    links = extract_links(result.content, result.url, site, result.encoding_hint)
    print(f"{result.url}\n抽出リンク: {len(links)}件\n")

    print("パス別の件数（selector や include を決める手がかり）:")
    for prefix, count in path_prefix_stats(links):
        print(f"  {count:4d}  {prefix}")

    print(f"\n先頭 {min(args.limit, len(links))} 件:")
    for link in links[: args.limit]:
        print(f"  - {link.title[:70] or '(タイトルなし)'}\n    {link.url}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="checker", description="登録サイトの更新を巡回して検知する")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="全サイトを巡回する（既定）")
    check.add_argument("--config", default=str(DEFAULT_CONFIG), help="sites.yml のパス")
    check.add_argument("--state", default=str(DEFAULT_STATE), help="state.json のパス")
    check.add_argument("--output", default=str(DEFAULT_OUTPUT), help="feed.json の出力先")
    check.add_argument("--only", nargs="+", metavar="ID", help="指定した id のサイトだけ巡回する")
    check.add_argument("--dry-run", action="store_true", help="ファイルを書き換えずに結果だけ表示する")
    check.set_defaults(func=command_check)

    inspect = sub.add_parser("inspect", help="1ページの抽出結果を確認する")
    inspect.add_argument("url", help="確認したいページのURL")
    inspect.add_argument("--selector", help="試したい CSS セレクタ")
    inspect.add_argument("--limit", type=int, default=30, help="表示するリンク数")
    inspect.set_defaults(func=command_inspect)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # サブコマンド省略時は check とみなす。
    if not argv or argv[0].startswith("-"):
        argv.insert(0, "check")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

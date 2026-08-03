# サイト更新チェッカー

RSS に対応していないサイトの更新を自動で見張り、新着を一覧できる Web アプリです。
Inoreader に登録できないサイトを、同じ感覚でまとめてチェックすることを目的にしています。

- 巡回は **GitHub Actions** が 1日3回（JST 07:00 / 12:00 / 21:00）実行します
- 画面は **静的サイト**（HTML + CSS + JS）なので、サーバーの用意や維持費は不要です
- 新着はタイトル付きで一覧され、**クリックするとその記事に直接飛べます**

## 仕組み

```
sites.yml            監視したいサイトのURL一覧（自分で編集する）
    ↓
GitHub Actions       1日3回、各サイトのHTMLを取得
    ↓
checker/             ページ内のリンクを抽出し、前回の記録と突き合わせる
    ↓
data/state.json      これまでに見たURLの記録（差分の基準）
docs/data/feed.json  画面が読み込む巡回結果
    ↓
docs/index.html      新着一覧（GitHub Pages で公開）
```

更新の判定は **「前回は無かったリンクが増えていたら新着」** という方式です。
ナビゲーションやフッターのリンクは毎回同じなので、差分を取れば自然に消えます。
ページ全体のハッシュを比べる方式と違い、広告や日付表示の変化で誤検知せず、
**新着記事のURLそのもの**が取れるので一覧からワンクリックで飛べます。

初回の巡回だけは、そのとき載っているリンクを「既知」として記録するだけで終わります
（新着判定は2回目以降）。

## サイトを追加する

`sites.yml` の `sites:` に URL を1件書き足すだけです。
GitHub のブラウザ上で直接編集して保存すれば、**その場で巡回が走って反映されます**
（`sites.yml` の変更を検知して巡回ワークフローが起動します）。

```yaml
sites:
  - id: example-news          # 任意。省略すると name から自動生成される
    name: サンプル社 お知らせ   # 画面に表示される名前
    url: https://example.com/news/
```

### うまく新着が取れないとき

サイトによってはナビゲーションが多すぎたり、逆に記事が取れなかったりします。
まず `inspect` で何が取れているかを見てから調整してください。

```console
$ python -m checker inspect https://example.com/news/

抽出リンク: 8件

パス別の件数（selector や include を決める手がかり）:
     5  /news
     3  /

先頭 8 件:
  - 会社概要
    https://example.com/company.html
  - 2027年度 新卒採用を開始しました
    https://example.com/news/20260803-recruit.html
  ...
```

この例では記事が `/news/` の下に並んでいると分かるので、次のように絞り込みます。

| 設定 | 意味 | 例 |
| --- | --- | --- |
| `include` | URL にこの文字列を含むリンクだけを残す | `include: ["/news/"]` |
| `exclude` | URL にこの文字列を含むリンクを除く | `exclude: ["/category/", "/tag/"]` |
| `selector` | 記事一覧のCSSセレクタを直接指定する | `selector: "ul.news-list a"` |
| `min_title_length` | リンク文字列が短いものを捨てる | `min_title_length: 6` |
| `enabled` | 一時的に巡回を止める | `enabled: false` |

`selector` は最終手段です。`include` で足りることがほとんどで、
サイトのHTML構造が変わったときに壊れにくいのは `include` の方です。

そのほかの項目は `sites.yml` のコメントを参照してください。

## 画面の見方

| 表示 | 意味 |
| --- | --- |
| **新着** タブ | 全サイト横断の新着タイムライン（新しい順） |
| **サイト一覧** タブ | サイトごとの状態。未読が多い順に並ぶ |
| 未読 N | まだクリックしていない新着の件数 |
| 更新なし | 前回の巡回から新しいリンクが増えていない |
| 登録済み | 初回巡回でリンクを記録した状態。新着検知は次回から |
| エラー | 取得に失敗した。理由と連続失敗回数が表示される |

既読状態は**そのブラウザの localStorage にのみ**保存されます。
サーバーを持たない構成のため、端末をまたいだ同期はされません。

## ローカルで動かす

```bash
pip install -r requirements.txt

# 巡回する（state.json と feed.json が更新される）
python -m checker

# 特定のサイトだけ試す
python -m checker --only example-news

# ファイルを書き換えずに結果だけ見る
python -m checker --dry-run

# 画面を開く（file:// で直接開くと feed.json を読み込めません）
python -m http.server -d docs 8000
# → http://localhost:8000/
```

テスト:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## GitHub Pages で公開する

このリポジトリは開発用（private）のため、Pages は使えません。
運用は公開リポジトリに移して行います。

1. 新しい **public** リポジトリを作る
2. このリポジトリの中身を push する
3. Settings → Pages → Source を **Deploy from a branch**、ブランチ `main`、フォルダ `/docs` に設定する
4. Settings → Actions → General → Workflow permissions を **Read and write permissions** にする
   （巡回結果を自動コミットするために必要）
5. Actions タブの「巡回」を **Run workflow** で1回手動実行する（初回登録）

数分後に `https://<ユーザー名>.github.io/<リポジトリ名>/` で開けます。

> 公開リポジトリでは、監視サイトのURLと収集した記事タイトルも公開されます。
> 見せたくない場合は、巡回を private リポジトリで行い、結果の JSON だけを公開側に
> push する構成に変更できます。

## ファイル構成

```
sites.yml                   監視サイトの設定
checker/
  config.py                 sites.yml の読み込みと検証
  fetcher.py                HTML取得（リトライ・文字コード判定）
  extractor.py              リンク抽出・URL正規化・フィルタ
  store.py                  既知URLの保存と差分検出
  report.py                 画面用 feed.json の生成
  main.py                   CLI（check / inspect）
data/state.json             巡回の記録（自動更新）
docs/                       GitHub Pages で公開する画面
  index.html / style.css / app.js
  data/feed.json            巡回結果（自動更新）
tests/                      pytest（ローカルHTTPサーバーを使った統合テストを含む）
.github/workflows/
  crawl.yml                 1日3回の巡回と自動コミット
  test.yml                  push時のテスト
```

## 現時点でできないこと

段階2以降で必要になったら追加します。

- **JavaScript で描画されるサイト**（SPA）— HTMLを取得しただけでは記事が入っていないため、
  ヘッドレスブラウザでの取得処理が別途必要です
- **ログインが必要なサイト** — 認証情報の保管方法の設計が必要です
- **画面からのサイト追加** — 静的サイトのため、現在は `sites.yml` の編集で行います
- **端末をまたいだ既読の同期** — 既読はブラウザごとの保存です
- **新着のメール／通知** — 現在は画面を開いて確認する方式です

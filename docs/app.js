/*
 * 巡回結果 (data/feed.json) を読み込んで一覧表示する。
 * 既読状態はサーバーを持たないので localStorage に保存する。
 */
(function () {
  "use strict";

  var FEED_URL = "data/feed.json";
  var READ_KEY = "site-monitor.read";
  var PREFS_KEY = "site-monitor.prefs";
  var READ_LIMIT = 3000;

  var feed = null;
  var readMap = loadRead();
  var prefs = loadPrefs();

  var el = {
    meta: document.getElementById("meta"),
    view: document.getElementById("view"),
    search: document.getElementById("search"),
    unreadOnly: document.getElementById("unread-only"),
    markAll: document.getElementById("mark-all"),
    reload: document.getElementById("reload"),
    manualUpdate: document.getElementById("manual-update"),
    tabTimeline: document.getElementById("tab-timeline"),
    tabSites: document.getElementById("tab-sites"),
    footer: document.getElementById("footer-note")
  };

  /* ---------- 手動更新ボタン ---------- */

  // GitHub Pages の URL（https://OWNER.github.io/REPO/）から
  // Actions のワークフローページを逆算する。ページ側からトークンなしで
  // 巡回そのものは起動できないので、GitHub 上の実行画面へ橋渡しする。
  function actionsWorkflowUrl() {
    var host = location.hostname; // 例: eldoradoshambala-hub.github.io
    var owner = host.split(".")[0];
    var repo = location.pathname.split("/").filter(Boolean)[0];
    if (!host.endsWith(".github.io") || !owner || !repo) { return null; }
    return "https://github.com/" + owner + "/" + repo + "/actions/workflows/crawl.yml";
  }

  function setupManualUpdateButton() {
    var url = actionsWorkflowUrl();
    if (!url) {
      // ローカルプレビューなど、GitHub Pages 以外で開いているときは無効化する。
      el.manualUpdate.setAttribute("aria-disabled", "true");
      el.manualUpdate.removeAttribute("href");
      el.manualUpdate.title = "GitHub Pages で開いているときだけ使えます。";
      return;
    }
    el.manualUpdate.href = url;
  }

  /* ---------- 保存まわり ---------- */

  function loadRead() {
    try {
      var raw = JSON.parse(localStorage.getItem(READ_KEY));
      return raw && typeof raw === "object" ? raw : {};
    } catch (e) {
      return {};
    }
  }

  function saveRead() {
    var urls = Object.keys(readMap);
    if (urls.length > READ_LIMIT) {
      // 古い順に捨てる。既読の記録が無限に膨らむのを防ぐ。
      urls.sort(function (a, b) { return readMap[a] - readMap[b]; });
      urls.slice(0, urls.length - READ_LIMIT).forEach(function (url) { delete readMap[url]; });
    }
    try {
      localStorage.setItem(READ_KEY, JSON.stringify(readMap));
    } catch (e) { /* 容量超過などは無視する */ }
  }

  function loadPrefs() {
    try {
      var raw = JSON.parse(localStorage.getItem(PREFS_KEY)) || {};
      return { view: raw.view === "sites" ? "sites" : "timeline", unreadOnly: !!raw.unreadOnly };
    } catch (e) {
      return { view: "timeline", unreadOnly: false };
    }
  }

  function savePrefs() {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch (e) { /* 無視 */ }
  }

  function isRead(url) {
    return Object.prototype.hasOwnProperty.call(readMap, url);
  }

  function markRead(url) {
    if (!isRead(url)) {
      readMap[url] = Date.now();
      saveRead();
    }
  }

  function markAllRead() {
    allItems().forEach(function (item) { readMap[item.url] = Date.now(); });
    saveRead();
    render();
  }

  /* ---------- 表示ユーティリティ ---------- */

  function allItems() {
    if (!feed) { return []; }
    return feed.timeline || [];
  }

  function parseTime(value) {
    if (!value) { return null; }
    var t = new Date(value);
    return isNaN(t.getTime()) ? null : t;
  }

  function formatAbsolute(value) {
    var t = parseTime(value);
    if (!t) { return "―"; }
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return t.getFullYear() + "/" + pad(t.getMonth() + 1) + "/" + pad(t.getDate()) +
      " " + pad(t.getHours()) + ":" + pad(t.getMinutes());
  }

  function formatRelative(value) {
    var t = parseTime(value);
    if (!t) { return "―"; }
    var diff = Math.floor((Date.now() - t.getTime()) / 1000);
    if (diff < 60) { return "たった今"; }
    if (diff < 3600) { return Math.floor(diff / 60) + "分前"; }
    if (diff < 86400) { return Math.floor(diff / 3600) + "時間前"; }
    if (diff < 86400 * 30) { return Math.floor(diff / 86400) + "日前"; }
    return formatAbsolute(value);
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function displayTitle(item) {
    var title = (item.title || "").trim();
    if (title) { return title; }
    try {
      return decodeURI(new URL(item.url).pathname);
    } catch (e) {
      return item.url;
    }
  }

  function matchesQuery(text, query) {
    return !query || text.toLowerCase().indexOf(query) !== -1;
  }

  /* ---------- 描画 ---------- */

  function renderHeader() {
    if (!feed) { return; }
    var unread = allItems().filter(function (i) { return !isRead(i.url); }).length;
    el.meta.textContent = "";

    var parts = [];
    parts.push(document.createTextNode("最終巡回 " + formatAbsolute(feed.generated_at) + "（" + formatRelative(feed.generated_at) + "）・" + feed.site_count + "サイト・"));

    var unreadNode = element("span", unread ? "strong" : "", "未読 " + unread + "件");
    parts.push(unreadNode);

    if (feed.error_count) {
      parts.push(document.createTextNode("・"));
      parts.push(element("span", "warn", "エラー " + feed.error_count + "件"));
    }
    parts.forEach(function (node) { el.meta.appendChild(node); });
  }

  function buildEntry(item) {
    var link = element("a", "entry" + (isRead(item.url) ? " is-read" : ""));
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.appendChild(element("span", "entry-title", displayTitle(item)));

    var sub = element("div", "entry-sub");
    sub.appendChild(element("span", "site", item.site_name || ""));
    sub.appendChild(element("span", "when", formatRelative(item.first_seen)));
    link.appendChild(sub);

    link.addEventListener("click", function () {
      markRead(item.url);
      link.classList.add("is-read");
      renderHeader();
    });
    return link;
  }

  function renderTimeline(query) {
    var items = allItems().filter(function (item) {
      if (prefs.unreadOnly && isRead(item.url)) { return false; }
      return matchesQuery(displayTitle(item) + " " + (item.site_name || ""), query);
    });

    if (!items.length) {
      el.view.appendChild(element("p", "empty",
        prefs.unreadOnly ? "未読の新着はありません。" : "表示できる新着がありません。"));
      return;
    }
    items.forEach(function (item) { el.view.appendChild(buildEntry(item)); });
  }

  function siteBadge(site) {
    if (site.status === "error") { return element("span", "badge is-error", "エラー"); }
    if (site.seeded_now) { return element("span", "badge", "登録済み"); }
    var unread = (site.items || []).filter(function (i) { return !isRead(i.url); }).length;
    if (unread) { return element("span", "badge is-new", "未読 " + unread); }
    return element("span", "badge", "更新なし");
  }

  function buildSiteCard(site, query) {
    var items = (site.items || []).filter(function (item) {
      if (prefs.unreadOnly && isRead(item.url)) { return false; }
      return matchesQuery(displayTitle(item), query);
    });

    var card = element("details", "card");
    var unread = (site.items || []).filter(function (i) { return !isRead(i.url); }).length;
    card.open = unread > 0 || site.status === "error";

    var head = element("summary", "card-head");
    head.appendChild(element("span", "card-name", site.name));
    head.appendChild(siteBadge(site));
    head.appendChild(element("span", "card-time", formatRelative(site.last_checked)));
    card.appendChild(head);

    var body = element("div", "card-body");

    if (site.status === "error") {
      var message = "取得に失敗しました: " + (site.error || "原因不明");
      if (site.consecutive_errors > 1) {
        message += "（" + site.consecutive_errors + "回連続）";
      }
      body.appendChild(element("p", "card-error", message));
    } else if (site.seeded_now) {
      body.appendChild(element("p", "card-error",
        "初回巡回のため " + site.link_count + " 件のリンクを記録しました。新着の検知は次回からです。"));
    }

    if (items.length) {
      var list = element("ul", "card-links");
      items.forEach(function (item) {
        var row = document.createElement("li");
        var link = element("a", isRead(item.url) ? "is-read" : "");
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.appendChild(document.createTextNode(displayTitle(item)));
        link.appendChild(element("span", "when", formatRelative(item.first_seen)));
        link.addEventListener("click", function () {
          markRead(item.url);
          link.classList.add("is-read");
          renderHeader();
        });
        row.appendChild(link);
        list.appendChild(row);
      });
      body.appendChild(list);
    } else if (site.status !== "error" && !site.seeded_now) {
      body.appendChild(element("p", "card-source",
        prefs.unreadOnly ? "未読の記事はありません。" : "記録された更新はまだありません。"));
    }

    var source = element("a", "card-source", "サイトを開く →");
    source.href = site.url;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    body.appendChild(source);

    card.appendChild(body);
    return card;
  }

  function renderSites(query) {
    var sites = (feed.sites || []).filter(function (site) {
      if (matchesQuery(site.name + " " + site.url, query)) { return true; }
      return (site.items || []).some(function (item) { return matchesQuery(displayTitle(item), query); });
    });

    if (!sites.length) {
      el.view.appendChild(element("p", "empty", "該当するサイトがありません。"));
      return;
    }

    // 未読が多い順 → エラー → 名前順。
    sites.slice().sort(function (a, b) {
      var ua = (a.items || []).filter(function (i) { return !isRead(i.url); }).length;
      var ub = (b.items || []).filter(function (i) { return !isRead(i.url); }).length;
      if (ua !== ub) { return ub - ua; }
      var ea = a.status === "error" ? 1 : 0;
      var eb = b.status === "error" ? 1 : 0;
      if (ea !== eb) { return eb - ea; }
      return a.name.localeCompare(b.name, "ja");
    }).forEach(function (site) {
      el.view.appendChild(buildSiteCard(site, query));
    });
  }

  function render() {
    if (!feed) { return; }
    var query = el.search.value.trim().toLowerCase();

    el.view.textContent = "";
    if (prefs.view === "sites") {
      renderSites(query);
    } else {
      renderTimeline(query);
    }

    el.tabTimeline.classList.toggle("is-active", prefs.view !== "sites");
    el.tabTimeline.setAttribute("aria-selected", String(prefs.view !== "sites"));
    el.tabSites.classList.toggle("is-active", prefs.view === "sites");
    el.tabSites.setAttribute("aria-selected", String(prefs.view === "sites"));

    renderHeader();
  }

  function showError(message, hint) {
    el.meta.textContent = "読み込みに失敗しました";
    el.view.textContent = "";
    var box = element("p", "empty", message);
    if (hint) {
      box.appendChild(document.createElement("br"));
      box.appendChild(element("code", null, hint));
    }
    el.view.appendChild(box);
  }

  function applyFeed(data) {
    feed = data;
    el.footer.textContent = "自動巡回は1日1回（12:00）。来ていなければ上の「手動更新」から実行できます。既読状態はこのブラウザにのみ保存されます。";
    render();
  }

  function load() {
    el.view.textContent = "";
    el.view.appendChild(element("p", "empty", "読み込み中…"));

    fetch(FEED_URL + "?t=" + Date.now(), { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      })
      .then(applyFeed)
      .catch(function (error) {
        if (location.protocol === "file:") {
          showError("ローカルファイルを直接開くと読み込めません。次のコマンドでサーバーを起動してください。",
            "python -m http.server -d docs 8000");
        } else {
          showError("data/feed.json を読み込めませんでした（" + error.message + "）。巡回がまだ実行されていない可能性があります。");
        }
      });
  }

  /* ---------- イベント ---------- */

  el.search.addEventListener("input", render);
  el.reload.addEventListener("click", load);
  el.markAll.addEventListener("click", markAllRead);

  el.unreadOnly.addEventListener("change", function () {
    prefs.unreadOnly = el.unreadOnly.checked;
    savePrefs();
    render();
  });

  el.tabTimeline.addEventListener("click", function () {
    prefs.view = "timeline";
    savePrefs();
    render();
  });

  el.tabSites.addEventListener("click", function () {
    prefs.view = "sites";
    savePrefs();
    render();
  });

  el.unreadOnly.checked = prefs.unreadOnly;
  setupManualUpdateButton();
  load();
})();

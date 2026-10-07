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

  // サイトごとの色（グラデーションの始点と終点）。サイト名のチップ、サイト一覧の頭文字、
  // 記事の画像が無いときの代わりの絵に使う。
  var PALETTE = [
    ["#ff8a5b", "#ff4f8b"],
    ["#7c5cff", "#4f8bff"],
    ["#14b8a6", "#22c55e"],
    ["#f59e0b", "#ef4444"],
    ["#0ea5e9", "#6366f1"],
    ["#ec4899", "#a855f7"],
    ["#10b981", "#0ea5e9"],
    ["#f97316", "#eab308"]
  ];

  var WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

  // 画像の alt から取った見出しには「〜のサムネイル画像」が付いてくることがある。
  // 画像は横に並べて出すので、見出しとしては余計な部分を落とす。
  var THUMBNAIL_SUFFIX = /\s*の?(サムネイル|アイキャッチ)(画像)?$/;

  // これより小さい画像は「NEW」アイコンや計測用の画像を拾ってしまったものとみなす（px）。
  var MIN_IMAGE_SIZE = 40;

  var feed = null;
  var siteColors = {};
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
    tabTimelineCount: document.getElementById("tab-timeline-count"),
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
    render(false);
  }

  /* ---------- 表示ユーティリティ ---------- */

  function allItems() {
    if (!feed) { return []; }
    return feed.timeline || [];
  }

  function unreadCount(items) {
    return (items || []).filter(function (i) { return !isRead(i.url); }).length;
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

  function dayKey(t) {
    return t.getFullYear() + "-" + t.getMonth() + "-" + t.getDate();
  }

  // タイムラインの日付見出し。「今日」「昨日」、それより前は「10月4日（土）」。
  function dayLabel(t) {
    var now = new Date();
    if (dayKey(t) === dayKey(now)) { return "今日"; }
    var yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    if (dayKey(t) === dayKey(yesterday)) { return "昨日"; }
    var label = (t.getMonth() + 1) + "月" + t.getDate() + "日（" + WEEKDAYS[t.getDay()] + "）";
    return t.getFullYear() === now.getFullYear() ? label : t.getFullYear() + "年" + label;
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function displayTitle(item) {
    var title = (item.title || "").replace(THUMBNAIL_SUFFIX, "").trim();
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

  /* ---------- サイトの色と画像 ---------- */

  function hashString(text) {
    var hash = 0;
    for (var i = 0; i < text.length; i++) {
      hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
    }
    return hash;
  }

  // 色は id から決めるので、巡回のたびに変わったりはしない。
  // ほかのサイトと同じ色になったら、空いている次の色にずらす。
  function assignSiteColors(sites) {
    var used = {};
    siteColors = {};
    sites.forEach(function (site) {
      var slot = hashString(site.id) % PALETTE.length;
      for (var n = 0; n < PALETTE.length && used[slot]; n++) {
        slot = (slot + 1) % PALETTE.length;
      }
      used[slot] = true;
      siteColors[site.id] = PALETTE[slot];
    });
  }

  function paintSite(node, siteId) {
    var colors = siteColors[siteId] || PALETTE[0];
    node.style.setProperty("--site", colors[0]);
    node.style.setProperty("--site-2", colors[1]);
  }

  function siteInitial(name) {
    var first = Array.from((name || "").trim())[0] || "?";
    return first.toUpperCase();
  }

  // 記事の画像。取れなかったとき・読み込めなかったときは、サイトの色と頭文字で代わりの絵を出す。
  function buildThumb(item, siteName, className) {
    var box = element("span", "thumb" + (className ? " " + className : ""));
    box.setAttribute("aria-hidden", "true");

    function showPlaceholder() {
      box.textContent = "";
      box.classList.remove("is-loaded");
      box.classList.add("is-placeholder");
      box.appendChild(element("span", "thumb-initial", siteInitial(siteName)));
    }

    if (!item.image) {
      showPlaceholder();
      return box;
    }

    var img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    // 参照元を送らない。直リンクを Referer で断るサイトでも表示できることが多い。
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", showPlaceholder);
    img.addEventListener("load", function () {
      if (img.naturalWidth < MIN_IMAGE_SIZE || img.naturalHeight < MIN_IMAGE_SIZE) {
        showPlaceholder();
      } else {
        box.classList.add("is-loaded");
      }
    });
    img.src = item.image;
    box.appendChild(img);
    return box;
  }

  function buildChip(siteId, siteName) {
    var chip = element("span", "chip");
    paintSite(chip, siteId);
    chip.appendChild(element("span", "chip-name", siteName || ""));
    return chip;
  }

  function emptyState(emoji, message, sub) {
    var box = element("div", "empty");
    box.appendChild(element("span", "empty-emoji", emoji));
    box.appendChild(element("p", "empty-text", message));
    if (sub) { box.appendChild(element("p", "empty-sub", sub)); }
    return box;
  }

  /* ---------- 描画 ---------- */

  function renderHeader() {
    if (!feed) { return; }
    var unread = unreadCount(allItems());
    el.meta.textContent = "";
    el.meta.appendChild(element("span", null,
      "最終巡回 " + formatAbsolute(feed.generated_at) + "（" + formatRelative(feed.generated_at) + "）・" +
      feed.site_count + "サイト"));
    el.meta.appendChild(element("span", "meta-pill" + (unread ? " is-unread" : ""), "未読 " + unread + "件"));
    if (feed.error_count) {
      el.meta.appendChild(element("span", "meta-pill is-warn", "エラー " + feed.error_count + "件"));
    }
    el.tabTimelineCount.textContent = unread > 99 ? "99+" : unread ? String(unread) : "";
  }

  // 記事を開いたら既読にして、見た目と未読数を更新する。
  function onOpen(link, item) {
    link.addEventListener("click", function () {
      markRead(item.url);
      link.classList.add("is-read");
      var pill = link.querySelector(".new-pill");
      if (pill) { pill.remove(); }
      renderHeader();
    });
  }

  function buildEntry(item, index) {
    var read = isRead(item.url);
    var title = displayTitle(item);
    var link = element("a", "entry" + (read ? " is-read" : ""));
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.title = title;
    link.style.setProperty("--i", Math.min(index, 12));
    paintSite(link, item.site_id);
    link.appendChild(buildThumb(item, item.site_name));

    var body = element("span", "entry-body");
    body.appendChild(element("span", "entry-title", title));

    var sub = element("span", "entry-sub");
    sub.appendChild(buildChip(item.site_id, item.site_name));
    sub.appendChild(element("span", "when", formatRelative(item.first_seen)));
    if (!read) { sub.appendChild(element("span", "new-pill", "NEW")); }
    body.appendChild(sub);
    link.appendChild(body);

    onOpen(link, item);
    return link;
  }

  function renderTimeline(query) {
    var items = allItems().filter(function (item) {
      if (prefs.unreadOnly && isRead(item.url)) { return false; }
      return matchesQuery(displayTitle(item) + " " + (item.site_name || ""), query);
    });

    if (!items.length) {
      if (query) {
        el.view.appendChild(emptyState("🔍", "「" + el.search.value.trim() + "」に一致する新着はありません。"));
      } else if (prefs.unreadOnly && allItems().length) {
        el.view.appendChild(emptyState("🎉", "未読の新着はありません。", "ぜんぶ読み終わりました。"));
      } else {
        el.view.appendChild(emptyState("🌱", "表示できる新着がありません。", "新しい記事が見つかると、ここに並びます。"));
      }
      return;
    }

    // 新しい順に並んでいるので、日付が変わるところで見出しを挟む。
    var groups = [];
    items.forEach(function (item) {
      var t = parseTime(item.first_seen);
      var key = t ? dayKey(t) : "unknown";
      var last = groups[groups.length - 1];
      if (!last || last.key !== key) {
        last = { key: key, label: t ? dayLabel(t) : "日時不明", items: [] };
        groups.push(last);
      }
      last.items.push(item);
    });

    var index = 0;
    groups.forEach(function (group) {
      var section = element("section", "day");
      var head = element("h2", "day-head");
      head.appendChild(element("span", "day-label", group.label));
      head.appendChild(element("span", "day-count", group.items.length + "件"));
      section.appendChild(head);
      group.items.forEach(function (item) {
        section.appendChild(buildEntry(item, index++));
      });
      el.view.appendChild(section);
    });
  }

  function siteBadge(site) {
    if (site.status === "error") { return element("span", "badge is-error", "エラー"); }
    if (site.seeded_now) { return element("span", "badge", "登録済み"); }
    var unread = unreadCount(site.items);
    if (unread) { return element("span", "badge is-new", "未読 " + unread); }
    return element("span", "badge", "更新なし");
  }

  function buildSiteCard(site, query, index) {
    var items = (site.items || []).filter(function (item) {
      if (prefs.unreadOnly && isRead(item.url)) { return false; }
      return matchesQuery(displayTitle(item), query);
    });

    var card = element("details", "card");
    card.open = unreadCount(site.items) > 0 || site.status === "error";
    card.style.setProperty("--i", Math.min(index, 12));
    paintSite(card, site.id);

    var head = element("summary", "card-head");
    head.appendChild(element("span", "avatar", siteInitial(site.name)));
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
      body.appendChild(element("p", "card-note",
        "初回巡回のため " + site.link_count + " 件のリンクを記録しました。新着の検知は次回からです。"));
    }

    if (items.length) {
      var list = element("ul", "card-links");
      items.forEach(function (item) {
        var row = document.createElement("li");
        var title = displayTitle(item);
        var link = element("a", "card-link" + (isRead(item.url) ? " is-read" : ""));
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.title = title;
        link.appendChild(buildThumb(item, site.name, "thumb-sm"));

        var text = element("span", "card-link-body");
        text.appendChild(element("span", "card-link-title", title));
        text.appendChild(element("span", "when", formatRelative(item.first_seen)));
        link.appendChild(text);

        onOpen(link, item);
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
      el.view.appendChild(emptyState("🔍", "該当するサイトがありません。"));
      return;
    }

    // 未読が多い順 → エラー → 名前順。
    var wrap = element("div", "sites");
    sites.slice().sort(function (a, b) {
      var ua = unreadCount(a.items);
      var ub = unreadCount(b.items);
      if (ua !== ub) { return ub - ua; }
      var ea = a.status === "error" ? 1 : 0;
      var eb = b.status === "error" ? 1 : 0;
      if (ea !== eb) { return eb - ea; }
      return a.name.localeCompare(b.name, "ja");
    }).forEach(function (site, index) {
      wrap.appendChild(buildSiteCard(site, query, index));
    });
    el.view.appendChild(wrap);
  }

  // animate は読み込み直後やタブ切り替えのときだけ true。
  // 検索の入力中に毎回動くと落ち着かないので、そのときは動かさない。
  function render(animate) {
    if (!feed) { return; }
    var query = el.search.value.trim().toLowerCase();

    el.view.textContent = "";
    el.view.classList.toggle("is-animated", !!animate);
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
    var box = emptyState("🛰️", message);
    if (hint) { box.appendChild(element("code", null, hint)); }
    el.view.appendChild(box);
  }

  function applyFeed(data) {
    feed = data;
    assignSiteColors(feed.sites || []);
    el.footer.textContent = "自動巡回は1日1回（12:00）。来ていなければ上の「手動更新」から実行できます。既読状態はこのブラウザにのみ保存されます。";
    render(true);
  }

  function load() {
    el.view.textContent = "";
    el.view.appendChild(element("p", "empty", "読み込み中…"));
    el.reload.classList.add("is-loading");

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
      })
      .then(function () {
        el.reload.classList.remove("is-loading");
      });
  }

  /* ---------- イベント ---------- */

  el.search.addEventListener("input", function () { render(false); });
  el.reload.addEventListener("click", load);
  el.markAll.addEventListener("click", markAllRead);

  el.unreadOnly.addEventListener("change", function () {
    prefs.unreadOnly = el.unreadOnly.checked;
    savePrefs();
    render(false);
  });

  el.tabTimeline.addEventListener("click", function () {
    prefs.view = "timeline";
    savePrefs();
    render(true);
  });

  el.tabSites.addEventListener("click", function () {
    prefs.view = "sites";
    savePrefs();
    render(true);
  });

  el.unreadOnly.checked = prefs.unreadOnly;
  setupManualUpdateButton();
  load();
})();

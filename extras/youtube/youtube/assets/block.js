/* YouTube block behaviour. ONE IIFE. The only global it defines is
   window.YTBlock. It attaches exactly one long-lived listener: a delegated
   'click' on #youtube-block. Nothing else touches document/window.

   Playback opens youtube.com in a full browser (see openVideo). "Mark watched"
   is manual. No credentials, no OAuth, no embedded player. */
(function () {
  "use strict";
  if (window.YTBlock && window.YTBlock._init) return;

  var ROOT_ID = "youtube-block";
  var API = "/youtube/api";
  var OPENED_KEY = "yt-opened";

  var state = { videos: [], channels: [], status: null, newWindowDays: 14, authToken: "" };

  function root() { return document.getElementById(ROOT_ID); }
  function mount() {
    var r = root();
    return r ? r.querySelector(".yt-block-mount") : null;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtDur(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? h + ":" : "") + mm + ":" + String(s).padStart(2, "0");
  }
  function ago(iso) {
    var t = Date.parse(iso || "");
    if (!t) return "";
    var d = (Date.now() - t) / 1000;
    if (d < 3600) return Math.max(1, Math.round(d / 60)) + "m ago";
    if (d < 86400) return Math.round(d / 3600) + "h ago";
    return Math.round(d / 86400) + "d ago";
  }

  // Local "I've opened this on YouTube" marker so a card you've started doesn't
  // look untouched. Per-viewer, best-effort — storage can be unavailable.
  function openedSet() {
    try { return new Set(JSON.parse(localStorage.getItem(OPENED_KEY) || "[]")); }
    catch (e) { return new Set(); }
  }
  function markOpened(id) {
    try {
      var arr = JSON.parse(localStorage.getItem(OPENED_KEY) || "[]").filter(function (x) { return x !== id; });
      arr.unshift(id);
      localStorage.setItem(OPENED_KEY, JSON.stringify(arr.slice(0, 300)));
    } catch (e) { /* ignore */ }
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (opts.method && opts.method !== "GET") {
      headers["Content-Type"] = "application/json";
      if (state.authToken) headers["X-Auth-Token"] = state.authToken;
    }
    return fetch(API + path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) { return r.json().catch(function () { return {}; }); });
  }

  function watchUrl(id) { return "https://www.youtube.com/watch?v=" + encodeURIComponent(id); }

  // Open a video. On the machine the host app runs on, ask the server to launch the
  // OS default browser (the user's real Chrome with their extensions / logins),
  // not this isolated app profile. On a remote device (phone / travel laptop
  // over the LAN) just open it here.
  function openVideo(id) {
    var h = (window.location.hostname || "").toLowerCase();
    var isLocalHost = h === "localhost" || h === "127.0.0.1" || h === "::1" || h === "[::1]";
    if (!isLocalHost) { window.open(watchUrl(id), "_blank", "noopener"); return; }
    api("/open", { method: "POST", body: { id: id } }).then(function (d) {
      if (!d || d.opened !== "server") window.open(watchUrl(id), "_blank", "noopener");
    }).catch(function () { window.open(watchUrl(id), "_blank", "noopener"); });
  }

  // ---- data load ------------------------------------------------------

  function readIsland() {
    var el = root() && root().querySelector("#yt-data");
    if (!el) return;
    try {
      var d = JSON.parse(el.textContent || "{}");
      state.videos = d.videos || [];
      state.channels = d.channels || [];
      state.status = d.status || null;
      state.newWindowDays = d.new_window_days || 14;
      state.authToken = d.authToken || d.auth_token || "";
    } catch (e) { /* leave defaults */ }
  }

  function refresh() {
    return api("/data").then(function (d) {
      if (d && d.ok) {
        state.videos = d.videos || [];
        state.channels = d.channels || [];
        state.status = d.status || null;
        state.newWindowDays = d.new_window_days || state.newWindowDays;
      }
      render();
    });
  }

  // ---- render --------------------------------------------------------

  function card(v, opened) {
    var thumb = v.thumb_url ? '<img loading="lazy" src="' + esc(v.thumb_url) + '" alt="">' : "";
    var badge = v.restricted ? '<span class="yt-badge">restricted</span>' : "";
    var dur = v.duration_sec ? '<span class="yt-dur">' + fmtDur(v.duration_sec) + "</span>" : "";
    var chanImg = v.channel_avatar ? '<img src="' + esc(v.channel_avatar) + '" alt="">' : "";
    var openedTag = opened ? '<span class="yt-opened-tag">opened</span>' : "";
    return '' +
      '<article class="yt-card' + (opened ? " yt-card--opened" : "") + '" data-act="open" data-id="' + esc(v.id) + '">' +
        '<div class="yt-thumb-wrap">' + thumb + badge + dur +
          '<span class="yt-play-hint">Watch on YouTube ↗</span>' + "</div>" +
        '<div class="yt-meta">' +
          '<div class="yt-title">' + esc(v.title) + "</div>" +
          '<div class="yt-chan">' + chanImg + "<span>" + esc(v.channel_title || "") + "</span>" +
            "<span>· " + esc(ago(v.published_at)) + "</span>" + openedTag + "</div>" +
          '<div class="yt-card-actions" data-stop="1">' +
            '<button class="yt-btn yt-btn--sm" data-act="mark-watched" data-id="' + esc(v.id) + '">Mark watched</button>' +
          "</div>" +
        "</div>" +
      "</article>";
  }

  function render() {
    var el = mount();
    if (!el) return;
    var adminPrev = el.querySelector(".yt-admin");
    var adminWasOpen = !!(adminPrev && adminPrev.open);
    var count = state.videos.length;
    var failing = (state.status && state.status.failing_count) || 0;
    var opened = openedSet();

    var grid = count
      ? '<div class="yt-grid">' + state.videos.map(function (v) { return card(v, opened.has(v.id)); }).join("") + "</div>"
      : '<div class="yt-empty">You’re all caught up. Nothing new from your channels in the last ' +
        esc(state.newWindowDays) + " days.</div>";

    el.innerHTML = '' +
      '<div class="yt-bar">' +
        "<h2>YouTube</h2>" +
        '<span class="yt-count">' + count + " new</span>" +
        (failing ? '<span class="yt-warn">⚠ ' + failing + " channel(s) failing</span>" : "") +
        '<span class="yt-spacer"></span>' +
        '<button class="yt-btn yt-btn--ghost yt-btn--sm" data-act="refresh">Refresh</button>' +
      "</div>" +
      grid +
      adminHtml();

    if (adminWasOpen) {
      var adminNow = el.querySelector(".yt-admin");
      if (adminNow) adminNow.open = true;
    }
  }

  function adminHtml() {
    var chanRows = state.channels.map(function (c) {
      var warn = c.consecutive_failures >= 3
        ? ' <span class="yt-warn">⚠ ' + c.consecutive_failures + " failed polls</span>" : "";
      return '<div class="yt-row yt-chanline"><span>' + esc(c.title || c.channel_id) + warn +
        '</span><button class="yt-btn yt-btn--sm" data-act="remove-channel" data-id="' +
        esc(c.channel_id) + '">Remove</button></div>';
    }).join("");

    return '<details class="yt-admin">' +
      "<summary>Manage channels</summary>" +
      "<h3>Add a channel</h3>" +
      '<div class="yt-row"><input type="text" placeholder="channel URL or @handle" data-role="add-input">' +
        '<button class="yt-btn yt-btn--sm" data-act="add-channel">Add</button></div>' +
      '<div class="yt-note" data-role="add-msg"></div>' +
      "<h3>Channels (" + state.channels.length + ")</h3>" +
      (chanRows || '<div class="yt-note">None yet.</div>') +
    "</details>";
  }

  // ---- actions ------------------------------------------------------

  function sendWatched(id) {
    api("/watched", { method: "POST", body: { id: id } }).then(function () {
      state.videos = state.videos.filter(function (v) { return v.id !== id; });
      render();
    });
  }

  function onClick(e) {
    var el = root();
    if (!el || !el.contains(e.target)) return;
    var t = e.target.closest("[data-act]");
    if (!t) return;
    var act = t.getAttribute("data-act");
    var id = t.getAttribute("data-id");

    switch (act) {
      case "open": {
        if (e.target.closest("[data-stop]")) return; // a card-action button was hit
        openVideo(id);
        markOpened(id);
        var cardEl = t;
        cardEl.classList.add("yt-card--opened");
        if (!cardEl.querySelector(".yt-opened-tag")) {
          var chan = cardEl.querySelector(".yt-chan");
          if (chan) chan.insertAdjacentHTML("beforeend", '<span class="yt-opened-tag">opened</span>');
        }
        break;
      }
      case "mark-watched": e.stopPropagation(); sendWatched(id); break;
      case "refresh": refresh(); break;
      case "add-channel": {
        var input = el.querySelector('[data-role="add-input"]');
        var msg = el.querySelector('[data-role="add-msg"]');
        var url = (input && input.value || "").trim();
        if (!url) return;
        msg.textContent = "Adding…";
        api("/channels", { method: "POST", body: { url: url } }).then(function (d) {
          if (d.ok) { input.value = ""; msg.textContent = "Added " + (d.channel && d.channel.title || ""); refresh(); }
          else { msg.innerHTML = '<span class="yt-warn">' + esc(d.error || "failed") + "</span>"; }
        });
        break;
      }
      case "remove-channel":
        if (!window.confirm("Remove this channel and its watched history?")) return;
        api("/channels/" + encodeURIComponent(id), { method: "DELETE" }).then(refresh);
        break;
    }
  }

  // ---- init --------------------------------------------------------

  function init() {
    if (!root()) return;
    readIsland();
    render();
  }

  document.addEventListener("click", onClick);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.YTBlock = { _init: true, refresh: refresh, render: render };
})();

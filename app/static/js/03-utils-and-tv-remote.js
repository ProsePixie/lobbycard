let state = { query: '', type: 'all', genre: 'all', dateFilter: 'all', sourceFilter: 'digital', sort: 'title-asc', view: (typeof DEV !== 'undefined' && DEV === 'phone') ? 'list' : 'grid', showRuntimeMismatchOnly: false, franchiseOnly: false, weeklyTvOnly: false, shortEpisodesOnly: false, onlyPartnerNoInterest: false, onlyYouNoInterest: false, statusFilter: new Set() };

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatBytes(b) {
  if (!b) return '';
  const gb = b / 1e9;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${(b / 1e6).toFixed(0)} MB`;
}

function formatRuntime(mins) {
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60), m = mins % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function extraPathsSet(show) {
  const set = new Set();
  (show.duplicates || []).forEach(d => (d.extra_paths || []).forEach(p => set.add(p)));
  return set;
}

function matchesQuery(show, q) {
  if (!q) return true;
  const genres = (show.tmdb_genres || [show.genre]).join(' ');
  const hay = (show.title + ' ' + genres + ' ' + (show.tmdb_overview || '') + ' ' + (show.years||[]).join(' ')).toLowerCase();
  const terms = q.split(/\s+/).filter(Boolean);
  return terms.every(term => hay.includes(term));
}

function matchesDateFilter(show, filter) {
  if (filter === 'all') return true;
  if (filter === 'watched-7d' || filter === 'watched-30d') {
    // e.watched_at is the last time this entry's watched *state* changed,
    // in either direction (see resolve_watched()/api_unwatch_show) - a
    // show you just restarted (Restart show, all unwatched) would have a
    // recent watched_at but watched:false, and shouldn't match "watched
    // recently". Must check both.
    const days = filter === 'watched-7d' ? 7 : 30;
    const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
    return show.entries.some(e => e.watched && e.watched_at && new Date(e.watched_at).getTime() >= cutoff);
  }
  if (!show.latest_added) return false;
  const days = filter === '7d' ? 7 : 30;
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  return new Date(show.latest_added).getTime() >= cutoff;
}

function showToast(msg) {
  let t = document.getElementById('toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast';
    t.className = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2500);
}

// "Play on the TV" - tells the server to start VLC on the machine it runs
// on (the one wired to the screen). This is the Play button in every
// server-mode page now, the PC's own window included (2026-09-07): the
// server always launches a controllable VLC, so playback started from the
// PC shows the transport card on phones/tablets too. Offline exports fall
// back to the raw openvlc:// link (no server to ask). `control` in the
// body is a leftover hint the server no longer needs.
// "Location" icon: on the machine running the server (any OS) the server opens
// the file manager itself; from another device the openfolder:// link in the
// href is left to do its thing (it needs the Windows handler in extras/).
window.openLocation = function(ev, a) {
  ev.stopPropagation();
  if (!SERVER_MODE || IS_REMOTE || !a.dataset.path) return true;
  fetch('/api/open-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Auth-Token': AUTH_TOKEN },
    body: JSON.stringify({ path: a.dataset.path }),
  })
    .then(r => r.json())
    .then(d => { if (!(d && d.ok)) showToast('Could not open the folder' + (d && d.error ? ': ' + d.error : '')); })
    .catch(() => showToast('Could not reach the server to open the folder'));
  return false;
};

window.playOnServer = function(btn) {
  const path = btn && btn.dataset ? btn.dataset.path : null;
  if (!path) return;
  btn.disabled = true;
  fetch('/api/play', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Auth-Token': AUTH_TOKEN },
    body: JSON.stringify({ path, control: true }),
  })
    .then(r => r.json())
    .then(d => {
      if (d && d.ok) {
        showToast('▶ Playing on the TV');
        if (d.control) { window._tvControlsDismissed = false; showTvControls(); }
      } else {
        showToast('Could not start playback' + (d && d.error ? ': ' + d.error : ''));
      }
    })
    .catch(() => showToast('Could not reach the server to start playback'))
    .finally(() => { btn.disabled = false; });
};

// The "Play on the TV" transport card - built lazily on first show, then
// kept in sync with the one shared VLC by pollVlcState() below. Scrubber,
// pause, +/-15 & 30, volume, mute and stop; every button drives
// /api/vlc/<cmd> which proxies to that VLC's own HTTP interface.
function tvFmtClock(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(ss).padStart(2, '0');
}
window._tvSeeking = false;      // user is dragging the scrubber right now
window._tvLastPoll = null;      // {at, time, playing, length} - for between-poll interpolation
window._tvCurVol = null;        // last volume VLC reported (256 = 100%)
window._tvVolBeforeMute = 256;
window._tvWakeLock = null;      // Screen Wake Lock sentinel while the card is up (remote-only)
window._tvDimTimer = null;      // auto-dim-after-inactivity timer (remote-only)
// Vibrates once on a real tap (mobile only - most desktop browsers have no
// Vibration API at all, and the try/catch means an unsupported browser
// just silently does nothing rather than erroring). Deliberately short and
// uniform for every button - this is a "felt that" tick, not a signal
// that varies by which button.
function tvHaptic() {
  try { if (navigator.vibrate) navigator.vibrate(12); } catch (e) {}
}
// Keep-awake (2026-09-29, fixed 2026-09-30) - only worth acquiring on the
// dedicated remote page (REMOTE_ONLY): that's the one screen meant to sit
// out and be looked at/tapped over a long viewing session, unlike the
// small floating card on the main app.
//
// The real Screen Wake Lock API (tried first, free when it works) only
// holds on an https:// page or the exact hostname "localhost" - confirmed
// real, 2026-09-30: the phone kept locking because this remote is served
// as http://mediabox.local, a plain-http LAN hostname, which browsers never
// treat as a secure context, so navigator.wakeLock.request() was silently
// failing on every real phone the whole time. Layered underneath it (not
// a replacement - still attempted, works for free if this ever moves to
// https) is the actual fix: a hidden, looping, muted, silent video
// (/nosleep.mp4, generated once server-side by _ensure_nosleep_video() in
// web_server.py) - the long-established "NoSleep.js" technique. Mobile
// Safari/Chrome don't dim the screen while a video element is actively
// playing, regardless of secure-context rules, so this works today over
// plain http with no certificate.
function _tvGetNoSleepVideo() {
  let v = document.getElementById('tv-nosleep-video');
  if (!v) {
    v = document.createElement('video');
    v.id = 'tv-nosleep-video';
    v.muted = true;
    v.setAttribute('muted', '');
    v.setAttribute('playsinline', '');   // never let iOS force real fullscreen playback
    v.setAttribute('webkit-playsinline', '');
    v.loop = true;
    v.src = '/nosleep.mp4';
    v.style.cssText = 'position:fixed; left:-9999px; top:-9999px; width:1px; height:1px; opacity:0; pointer-events:none;';
    // Extra safety net alongside tvAcquireWakeLock()'s own retry loop - if
    // the element genuinely wasn't ready for any of those attempts, this
    // fires one more once the browser confirms it actually can play.
    v.addEventListener('canplay', function() { if (v.paused) v.play().catch(function() {}); });
    document.body.appendChild(v);
  }
  return v;
}
async function tvAcquireWakeLock() {
  if (!(typeof REMOTE_ONLY !== 'undefined' && REMOTE_ONLY)) return;
  if ('wakeLock' in navigator) {
    try { window._tvWakeLock = await navigator.wakeLock.request('screen'); } catch (e) {}
  }
  // A play() call made the same tick a <video> is first created/given its
  // src can silently lose out to the browser's own initial load sequence
  // (confirmed real, 2026-09-30: the very first attempt right after
  // _tvGetNoSleepVideo() creates the element left it paused with no
  // thrown error, even though calling play() again moments later worked
  // fine) - retrying a couple of times a beat later is cheap and closes
  // that race regardless of its exact cause.
  const video = _tvGetNoSleepVideo();
  for (let attempt = 0; attempt < 4 && video.paused; attempt++) {
    try { await video.play(); } catch (e) {}
    if (video.paused) await new Promise(r => setTimeout(r, 250));
  }
  // Actively supervises playback for the rest of the session instead of
  // trusting that one play() call holds forever - confirmed real,
  // 2026-09-30: a real phone stayed awake for ~6 minutes, then the video
  // silently stopped (no error, nothing to catch) and the screen resumed
  // its normal dim-then-lock schedule. A 4s check-and-restart is cheap and
  // self-heals whatever caused that, without needing to pin down the
  // exact mobile-browser quirk responsible. Guarded so re-showing the
  // card (pollVlcState calls this on every poll while playing) doesn't
  // stack a second interval on top of the first.
  if (!window._tvNoSleepWatchdog) {
    window._tvNoSleepWatchdog = setInterval(function() {
      const v = document.getElementById('tv-nosleep-video');
      if (v && (v.paused || v.ended)) v.play().catch(function() {});
    }, 4000);
  }
}
function tvReleaseWakeLock() {
  if (window._tvWakeLock) {
    try { window._tvWakeLock.release(); } catch (e) {}
    window._tvWakeLock = null;
  }
  if (window._tvNoSleepWatchdog) {
    clearInterval(window._tvNoSleepWatchdog);
    window._tvNoSleepWatchdog = null;
  }
  const v = document.getElementById('tv-nosleep-video');
  if (v) { try { v.pause(); } catch (e) {} }
}
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', function() {
    const bar = document.getElementById('tv-controls');
    if (document.visibilityState === 'visible' && bar && bar.classList.contains('show')) tvAcquireWakeLock();
  });
}
// Auto-dim after inactivity (remote-only only - see the .tvc-dim CSS rule's
// own comment). Any tap/touch/pointer move on the card resets the timer;
// the dim itself is opacity-only, so the same tap that wakes it still
// lands on whatever button is underneath.
function resetTvAutoDim() {
  const bar = document.getElementById('tv-controls');
  if (!bar) return;
  bar.classList.remove('tvc-dim');
  if (window._tvDimTimer) clearTimeout(window._tvDimTimer);
  if (!(typeof REMOTE_ONLY !== 'undefined' && REMOTE_ONLY)) return;
  window._tvDimTimer = setTimeout(function() { bar.classList.add('tvc-dim'); }, 12000);
}
window.showTvControls = function() {
  let bar = document.getElementById('tv-controls');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'tv-controls';
    bar.innerHTML =
      '<div class="tvc-head">'
        + '<img class="tvc-poster" alt="" hidden>'
        + '<div class="tvc-headtext">'
          + '<span class="tvc-eyebrow">&#9654; Now playing on the TV</span>'
          + '<div class="tvc-label">Loading&hellip;</div>'
        + '</div>'
        + '<a class="tvc-tmdb" href="#" target="_blank" rel="noopener" hidden>TMDB &#8599;</a>'
        + '<button type="button" class="tvc-close" data-cmd="_close" title="Hide">&times;</button>'
      + '</div>'
      + '<div class="tvc-body">'
        + '<div class="tvc-scrub">'
          + '<span class="tvc-t cur">0:00</span>'
          + '<input type="range" class="tvc-seek" min="0" max="1000" step="1" value="0" aria-label="Seek">'
          + '<span class="tvc-t dur">--:--</span>'
        + '</div>'
        + '<div class="tvc-row transport">'
          + '<button type="button" class="tvc-btn tvc-nav" data-cmd="back30" title="Back 30 seconds">&#8630;30</button>'
          + '<button type="button" class="tvc-btn tvc-nav" data-cmd="back15" title="Back 15 seconds">&#8630;15</button>'
          + '<button type="button" class="tvc-btn tvc-pause" data-cmd="pause" title="Pause / resume">&#10073;&#10073;</button>'
          + '<button type="button" class="tvc-btn tvc-nav" data-cmd="fwd15" title="Forward 15 seconds">15&#8631;</button>'
          + '<button type="button" class="tvc-btn tvc-nav" data-cmd="fwd30" title="Forward 30 seconds">30&#8631;</button>'
        + '</div>'
        + '<div class="tvc-row utility">'
          + '<div class="tvc-cardgroup tvc-chaptergroup" hidden>'
            + '<span class="tvc-cardlabel">CHAPTER</span>'
            + '<div class="tvc-cardbtns">'
              + '<button type="button" class="tvc-btn tvc-chapter" data-cmd="chapter_prev" title="Previous chapter">&#9198;</button>'
              + '<button type="button" class="tvc-btn tvc-chapter" data-cmd="chapter_next" title="Next chapter">&#9197;</button>'
            + '</div>'
          + '</div>'
          + '<div class="tvc-cardgroup tvc-volgroup2">'
            + '<span class="tvc-cardlabel">VOLUME</span>'
            + '<div class="tvc-cardbtns tvc-volrow">'
              + '<button type="button" class="tvc-btn tvc-vol" data-cmd="vol_down" title="Volume down">&#128265;</button>'
              + '<div class="tvc-volbar"><div class="tvc-volfill"></div></div>'
              + '<button type="button" class="tvc-btn tvc-vol" data-cmd="vol_up" title="Volume up">&#128266;</button>'
              + '<span class="tvc-volpct">--%</span>'
              + '<span class="tvc-voldivider"></span>'
              + '<button type="button" class="tvc-btn tvc-vol tvc-mute" data-cmd="_mute" title="Mute / unmute">&#128264;</button>'
            + '</div>'
          + '</div>'
          + '<button type="button" class="tvc-btn tvc-stop" data-cmd="_stop" title="Stop playback">&#9632;<span>Stop</span></button>'
        + '</div>'
      + '</div>';
    bar.addEventListener('click', function(e) {
      const b = e.target.closest('button[data-cmd]');
      if (!b) return;
      tvHaptic();
      const c = b.dataset.cmd;
      if (c === '_close') { window._tvControlsDismissed = true; hideTvControls(); return; }
      if (c === '_stop') { window._tvControlsDismissed = true; vlcCmd('stop', b); hideTvControls(); return; }
      if (c === '_mute') { tvToggleMute(b); return; }
      vlcCmd(c, b);
    });
    bar.addEventListener('pointerdown', resetTvAutoDim);
    const seek = bar.querySelector('.tvc-seek');
    seek.addEventListener('input', function() {
      window._tvSeeking = true;
      const dur = +seek.dataset.dur || 0;
      const cur = bar.querySelector('.tvc-t.cur');
      if (dur && cur) cur.textContent = tvFmtClock(dur * (seek.value / 1000));
    });
    seek.addEventListener('change', function() {
      const dur = +seek.dataset.dur || 0;
      window._tvSeeking = false;
      if (!dur) return;
      const to = Math.round(dur * (seek.value / 1000));
      fetch('/api/vlc/seek_to', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Auth-Token': AUTH_TOKEN },
        body: JSON.stringify({ to: to }),
      }).then(() => setTimeout(pollVlcState, 400))
        .catch(() => showToast('Could not reach the server'));
    });
    document.body.appendChild(bar);
  }
  bar.classList.add('show');
  tvAcquireWakeLock();
  // Only kick off the auto-dim countdown the first time the card appears
  // for this viewing session - confirmed real, 2026-09-29 live test: this
  // used to call resetTvAutoDim() unconditionally, and pollVlcState() (see
  // below) calls showTvControls() again on every single poll tick (every
  // 1-3s) while something's playing - each of THOSE calls was also
  // resetting the inactivity timer, so it could never actually elapse.
  // Real inactivity is tracked by the 'pointerdown' listener below instead;
  // this just needs to start the clock once.
  if (!window._tvDimStarted) { window._tvDimStarted = true; resetTvAutoDim(); }
};
window.hideTvControls = function() {
  const bar = document.getElementById('tv-controls');
  if (bar) bar.classList.remove('show');
  tvReleaseWakeLock();
  if (window._tvDimTimer) clearTimeout(window._tvDimTimer);
  window._tvDimStarted = false;
};
function tvToggleMute(btn) {
  const cur = (window._tvCurVol == null) ? 256 : window._tvCurVol;
  let target;
  if (cur > 0) { window._tvVolBeforeMute = cur; target = 0; }
  else { target = window._tvVolBeforeMute || 256; }
  if (btn) btn.disabled = true;
  fetch('/api/vlc/set_vol', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Auth-Token': AUTH_TOKEN },
    body: JSON.stringify({ vol: target }),
  }).then(() => setTimeout(pollVlcState, 300))
    .catch(() => showToast('Could not reach the server'))
    .finally(() => { if (btn) btn.disabled = false; });
}
window.vlcCmd = function(cmd, btn) {
  if (btn) btn.disabled = true;
  fetch('/api/vlc/' + cmd, { method: 'POST', headers: { 'X-Auth-Token': AUTH_TOKEN } })
    .then(r => r.json())
    .then(d => {
      if (!d || !d.ok) { showToast((d && d.error) || 'VLC did not respond'); return; }
      if (cmd === 'pause') {
        // optimistic flip for instant feedback; the re-poll below (and
        // every other device's poll) reconciles it with VLC's real state
        const p = document.querySelector('#tv-controls .tvc-pause');
        if (p) syncTvPauseGlyph(p.dataset.paused !== '1');
      }
      if (typeof SERVER_MODE !== 'undefined' && SERVER_MODE) setTimeout(pollVlcState, 500);
    })
    .catch(() => showToast('Could not reach the server'))
    .finally(() => { if (btn) btn.disabled = false; });
};

// --- Multi-device sync (2026-09-06) ------------------------------------
// One TV, one VLC (see /api/play + /api/vlc/state). Every server-mode
// page polls the shared VLC's real state, so a device that didn't start
// playback still shows the transport bar, and every device's pause glyph
// follows VLC itself rather than that device's own last tap. This
// includes the PC's own app window: a movie launched
// from the PC is now a controllable VLC too, so the card should appear
// on the phone the same as if the phone had started it. Offline / no
// server (not SERVER_MODE) never polls.
window._tvControlsDismissed = window._tvControlsDismissed || false;
function syncTvPauseGlyph(isPaused) {
  const p = document.querySelector('#tv-controls .tvc-pause');
  if (!p) return;
  p.innerHTML = isPaused ? '&#9654;' : '&#10073;&#10073;';
  p.dataset.paused = isPaused ? '1' : '0';
}
function tvCleanTitle(name) {
  return String(name || '')
    .replace(/\.[A-Za-z0-9]{2,4}$/, '')             // drop extension
    .replace(/\.(2160p|1080p|720p|480p|x26[45]|h26[45]|web-?dl|bluray|hdr)\b.*$/i, '') // + release cruft
    .replace(/[._]+/g, ' ').trim();
}
function tvApplyState(d) {
  const bar = document.getElementById('tv-controls');
  if (!bar) return;
  const lbl = bar.querySelector('.tvc-label');
  if (lbl) {
    // Real show/episode title when the server captured it at /api/play
    // time (web_server.py's last_show_title/last_episode_title, set from
    // the actual library match, not the release filename) - falls back to
    // a cleaned-up filename for anything /api/play couldn't identify.
    let label = null;
    if (d.last_show_title) {
      const parts = [d.last_show_title];
      if (d.last_season != null && d.last_episode != null) {
        parts.push('S' + String(d.last_season).padStart(2, '0') + 'E' + String(d.last_episode).padStart(2, '0'));
      }
      if (d.last_episode_title) parts.push(d.last_episode_title);
      label = parts.join(' · ');
    }
    lbl.textContent = label || tvCleanTitle(d.title) || 'Playing on the TV';
  }
  const poster = bar.querySelector('.tvc-poster');
  if (poster) {
    if (d.last_poster) { poster.src = tmdbImageUrl('poster', d.last_poster); poster.hidden = false; }
    else { poster.hidden = true; }
  }
  const tmdbLink = bar.querySelector('.tvc-tmdb');
  if (tmdbLink) {
    if (d.last_tmdb_id && d.last_tmdb_type) {
      const kind = d.last_tmdb_type === 'Movie' ? 'movie' : 'tv';
      let url = `https://www.themoviedb.org/${kind}/${d.last_tmdb_id}`;
      if (kind === 'tv' && d.last_season != null && d.last_episode != null) {
        url += `/season/${d.last_season}/episode/${d.last_episode}`;
      }
      tmdbLink.href = url;
      tmdbLink.hidden = false;
    } else {
      tmdbLink.hidden = true;
    }
  }
  // Chapter skip - only shown when the playing file genuinely has more
  // than one chapter (see web_server.py's _vlc_state_snapshot() for how
  // chapters_count is derived, and why it may simply never be available
  // for a given file/VLC build - that's a hidden button, not a broken
  // one). Boundary episodes disable the button that would go nowhere
  // instead of letting a tap silently no-op.
  const chapterGroup = bar.querySelector('.tvc-chaptergroup');
  if (chapterGroup) {
    const hasChapters = typeof d.chapters_count === 'number' && d.chapters_count > 1;
    chapterGroup.hidden = !hasChapters;
    if (hasChapters) {
      const cur = typeof d.chapter === 'number' ? d.chapter : 0;
      const prevBtn = chapterGroup.querySelector('[data-cmd="chapter_prev"]');
      const nextBtn = chapterGroup.querySelector('[data-cmd="chapter_next"]');
      if (prevBtn) prevBtn.disabled = cur <= 0;
      if (nextBtn) nextBtn.disabled = cur >= d.chapters_count - 1;
    }
  }
  if (d.volume != null) {
    window._tvCurVol = d.volume;
    const pct = Math.max(0, Math.min(100, Math.round((d.volume / 256) * 100)));
    const mb = bar.querySelector('.tvc-mute');
    if (mb) {
      mb.innerHTML = (d.volume === 0) ? '&#128263;' : '&#128264;';
      mb.classList.toggle('is-muted', d.volume === 0);
    }
    const fill = bar.querySelector('.tvc-volfill');
    if (fill) fill.style.width = pct + '%';
    const pctEl = bar.querySelector('.tvc-volpct');
    if (pctEl) pctEl.textContent = pct + '%';
  }
  const length = d.length || 0;
  const seek = bar.querySelector('.tvc-seek');
  const durEl = bar.querySelector('.tvc-t.dur');
  const curEl = bar.querySelector('.tvc-t.cur');
  if (seek) { seek.dataset.dur = length; seek.disabled = !length; }
  if (durEl) durEl.textContent = length ? tvFmtClock(length) : '--:--';
  if (!window._tvSeeking) {
    if (curEl) curEl.textContent = tvFmtClock(d.time || 0);
    if (seek && length) seek.value = Math.round(1000 * Math.min(1, (d.time || 0) / length));
  }
  window._tvLastPoll = { at: performance.now(), time: d.time || 0, playing: d.state === 'playing', length: length };
}
// Smooth the scrubber between polls: while playing and not being dragged,
// advance the readout by wall-clock so it ticks every second even though
// the server is only polled every ~1s.
function tvInterpTick() {
  if (window._tvSeeking) return;
  const lp = window._tvLastPoll;
  const bar = document.getElementById('tv-controls');
  if (!lp || !lp.playing || !bar || !bar.classList.contains('show')) return;
  const t = lp.time + (performance.now() - lp.at) / 1000;
  const curEl = bar.querySelector('.tvc-t.cur');
  const seek = bar.querySelector('.tvc-seek');
  if (curEl) curEl.textContent = tvFmtClock(t);
  if (seek && lp.length) seek.value = Math.round(1000 * Math.min(1, t / lp.length));
}
// "Reopen on the TV" pill - only on the phone/tablet remote (IS_REMOTE),
// only when VLC is closed but the server still remembers what /api/play
// last started. Tapping it re-runs that play.
window._tvLastPath = null;
function showTvReopen(path, title) {
  window._tvLastPath = path;
  let el = document.getElementById('tv-reopen');
  if (!el) {
    el = document.createElement('button');
    el.id = 'tv-reopen';
    el.type = 'button';
    el.innerHTML = '<span class="tvr-main">&#9654; Reopen on the TV</span><span class="tvr-sub"></span>';
    el.addEventListener('click', reopenOnTv);
    document.body.appendChild(el);
  }
  el.querySelector('.tvr-sub').textContent = tvCleanTitle(title) || '';
  el.disabled = false;
  el.classList.add('show');
}
function hideTvReopen() {
  const el = document.getElementById('tv-reopen');
  if (el) el.classList.remove('show');
}
function reopenOnTv() {
  if (!window._tvLastPath) return;
  const el = document.getElementById('tv-reopen');
  if (el) el.disabled = true;
  fetch('/api/play', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Auth-Token': AUTH_TOKEN },
    body: JSON.stringify({ path: window._tvLastPath }),
  })
    .then(r => r.json())
    .then(d => {
      if (d && d.ok) {
        showToast('▶ Reopening on the TV');
        window._tvControlsDismissed = false;
        hideTvReopen();
        setTimeout(pollVlcState, 700);
      } else {
        showToast('Could not reopen' + (d && d.error ? ': ' + d.error : ''));
        if (el) el.disabled = false;
      }
    })
    .catch(() => { showToast('Could not reach the server'); if (el) el.disabled = false; });
}
function pollVlcState() {
  fetch('/api/vlc/state', { headers: { 'X-Auth-Token': AUTH_TOKEN } })
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) { noteRemoteLink(false); return; }
      noteRemoteLink(true);
      const remote = (typeof IS_REMOTE !== 'undefined' && IS_REMOTE);
      let showingSomething = false;
      if (d.active && d.state !== 'stopped') {
        if (!window._tvControlsDismissed) showTvControls();
        syncTvPauseGlyph(d.state === 'paused');
        tvApplyState(d);
        hideTvReopen();
        showingSomething = !window._tvControlsDismissed;
      } else {
        window._tvControlsDismissed = false;  // reset for the next session
        window._tvLastPoll = null;
        hideTvControls();
        // Only offer "Reopen on the TV" for something stopped partway -
        // an episode played to the end (or nearly) has nothing to resume.
        const frac = (typeof d.last_fraction === 'number') ? d.last_fraction : 0;
        if (remote && d.last_path && frac > 0.03 && frac < 0.9) {
          showTvReopen(d.last_path, d.last_title);
          showingSomething = true;
        } else {
          hideTvReopen();
        }
      }
      // Idle screen (see #remote-idle's own comment) - only the point of
      // this whole second port, so it's the one thing shown when there's
      // genuinely nothing else to show, never on the main port.
      if (typeof REMOTE_ONLY !== 'undefined' && REMOTE_ONLY) {
        if (showingSomething) hideRemoteIdle(); else showRemoteIdle();
      }
    })
    .catch(() => { noteRemoteLink(false); });
}
// Self-scheduling poll: 1s while something is actively playing and the
// card is up (so the scrubber stays live), 3s otherwise.
function scheduleVlcPoll() {
  const bar = document.getElementById('tv-controls');
  const lp = window._tvLastPoll;
  const fast = lp && lp.playing && bar && bar.classList.contains('show');
  window._tvPollTimer = setTimeout(function() {
    pollVlcState();
    scheduleVlcPoll();
  }, fast ? 1000 : 3000);
}
if (typeof SERVER_MODE !== 'undefined' && SERVER_MODE) {
  pollVlcState();
  scheduleVlcPoll();
  setInterval(tvInterpTick, 500);
}

async function postJSON(url, body) {
  if (!SERVER_MODE) {
    showToast('Start the local server (start_server) to save changes');
    return null;
  }
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Auth-Token': AUTH_TOKEN },
      body: JSON.stringify(body),
    });
    // Every route in this app that can reject a request (busy, invalid
    // input, etc.) returns a real {ok: false, message: "..."} JSON body
    // alongside a 4xx/409 status, not just a bare error code - parse it
    // even when resp.ok is false, so callers checking result.message
    // actually see it instead of a generic fallback.
    let data = null;
    try { data = await resp.json(); } catch (e) {}
    if (!resp.ok) {
      return data || { ok: false, message: 'request failed (' + resp.status + ')' };
    }
    return data;
  } catch (err) {
    showErrorDialog('Request failed', err.message);
    return null;
  }
}

// Pulls in whatever the server currently has - a background/nightly scan
// that ran since this tab was opened, a watched-state change made from
// another tab or device, a brand new show that's never been in this tab's
// copy of `shows` at all. Same data a full browser reload would show,
// without the reload - server_mode=False (a laptop with no server) never
// reaches here since SERVER_MODE already gates the button that calls this.
// Does NOT trigger a real library scan - if a file was added moments ago
// and nothing has scanned it in yet, it genuinely won't show up here
// either, the same way a browser refresh wouldn't. Returns how many
// existing shows changed and how many were genuinely new.
window._lastSyncAt = 0;
async function syncShowsFromServer() {
  const resp = await fetch('/api/shows-data', { headers: { 'X-Auth-Token': AUTH_TOKEN } });
  if (!resp.ok) return null;
  const freshShows = await resp.json();
  window._lastSyncAt = Date.now();
  const freshByKey = new Map(freshShows.map(s => [s.show_key, s]));
  let changed = 0;
  for (const s of shows) {
    const fresh = freshByKey.get(s.show_key);
    if (fresh) { Object.assign(s, fresh); changed++; }
  }
  // Drop shows that left the library since this tab loaded - a fully
  // purged title, a queue item that got filed and re-keyed, a removed
  // duplicate. Without this a silent sync (no full page reload) would
  // leave a dead card in the grid. Splice in place so the shared `shows`
  // reference every renderer closes over stays the same array.
  let removed = 0;
  for (let i = shows.length - 1; i >= 0; i--) {
    if (!freshByKey.has(shows[i].show_key)) { shows.splice(i, 1); removed++; }
  }
  const knownKeys = new Set(shows.map(s => s.show_key));
  let added = 0;
  for (const fresh of freshShows) {
    if (!knownKeys.has(fresh.show_key)) { shows.push(fresh); knownKeys.add(fresh.show_key); added++; }
  }
  // If a modal is open on a show that just vanished, close it rather than
  // leave it showing a title that's no longer in the library.
  const overlay = document.getElementById('modal-overlay');
  if (removed && overlay && overlay.classList.contains('open')) {
    const openIdx = parseInt(overlay.dataset.showIdx || '-1', 10);
    if (openIdx >= 0 && !shows[openIdx]) closeModal();
  }
  return { changed, added, removed };
}

// Re-pull the library in place (no full browser reload) and redraw - the
// shared completion path for every mutating action that isn't the big
// "Refresh library" button, plus the tab-refocus catch-up below.
async function silentResync(reason) {
  let r = null;
  try { r = await syncShowsFromServer(); } catch (e) { return; }
  if (!r) return;
  renderFrontPanels();
  applyFilters();
  if (typeof renderFranchiseWhatsNext === 'function'
      && document.documentElement.classList.contains('franchise-mode')) renderFranchiseWhatsNext();
  if (reason === 'action') {
    const parts = [];
    if (r.added) parts.push(`${r.added} new`);
    if (r.removed) parts.push(`${r.removed} removed`);
    if (r.changed && !r.added && !r.removed) parts.push('updated');
    showToast(parts.length ? `Library ${parts.join(', ')}.` : 'Library up to date.');
  } else if ((r.added || r.removed) && reason === 'focus') {
    showToast(`Library synced - ${[r.added && r.added + ' new', r.removed && r.removed + ' removed'].filter(Boolean).join(', ')}.`);
  }
}

window.onPanelRefresh = async function(panelKey, btn) {
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = '⏳';

  let sync = null;
  try { sync = await syncShowsFromServer(); } catch (e) { /* fall through to the air-date recheck below regardless */ }

  const keys = (panelRefreshState[panelKey] || []).filter(Boolean);
  let airDatesChecked = 0;
  if (keys.length) {
    const result = await postJSON('/api/refresh-next-episode', { show_keys: keys });
    if (result && result.ok) {
      for (const [key, fields] of Object.entries(result.updated || {})) {
        const s = shows.find(sh => sh.show_key === key);
        if (s) { Object.assign(s, fields); airDatesChecked++; }
      }
    }
  }

  btn.disabled = false;
  btn.textContent = originalText;
  const parts = [];
  if (sync && sync.added) parts.push(`${sync.added} new`);
  if (sync && sync.changed) parts.push(`${sync.changed} updated`);
  if (airDatesChecked) parts.push(`${airDatesChecked} air date(s) checked`);
  showToast(parts.length ? `Refreshed - ${parts.join(', ')}.` : 'Refreshed - nothing new.');
  renderFrontPanels();
  applyFilters();
};

window.onClearRecentlyWatched = async function() {
  if (TRAVEL_MODE) {
    // No server on the road - just clear the local MRU list (SPEC 7.3).
    travelRecentClear();
    renderFrontPanels();
    return;
  }
  const result = await postJSON('/api/clear-recently-watched', {});
  if (result && result.ok) {
    RECENTLY_WATCHED_KEYS = [];
    renderFrontPanels();
  } else if (result && result.message) {
    showToast(result.message);
  }
};

// The franchise's ordered title list comes from the pack's franchise.json
// (see pipeline/branding.py): each entry has an id (the TMDB id), a type
// ('Movie' or 'TV'), a release position and a chrono (story-order) position. `type` is part of the match because TMDB's movie and TV id spaces
// are independent (a Movie id and a TV id can collide numerically). An entry
// for something you don't own is completely inert - matching only ever runs
// against shows in the library, so unreleased or not-yet-owned titles can be
// listed ahead of time. A show with several seasons gets a single position;
// there is no season-level tracking.
const FRANCHISE_TITLES = FRANCHISE.titles || [];

function franchiseEntry(show) {
  return FRANCHISE_TITLES.find(m => m.id === show.tmdb_id && m.type === show.type) || null;
}

// Weekly TV mode (2026-08-26) - "currently active AND has a real
// next-episode date" per the user's own definition, built entirely from
// computeShowStatusKeys()'s existing 'active'/'airing_soon' keys rather
// than a separate check, so this can never drift from what the status-dot
// legend elsewhere already means by those same words.
function isWeeklyTvCandidate(show) {
  if (show.type !== 'TV') return false;
  const keys = computeShowStatusKeys(show);
  return keys.includes('active') && keys.includes('airing_soon');
}

function isFranchiseChronoOrder() {
  return localStorage.getItem('franchiseChronoOrder') === '1';
}

function updateFranchiseOrderToggleBtn() {
  const btn = document.getElementById('franchise-order-toggle-btn');
  btn.textContent = isFranchiseChronoOrder() ? '📅 Switch to release order' : '🌌 Switch to chronological order';
}

window.toggleFranchiseOrder = function(idx) {
  localStorage.setItem('franchiseChronoOrder', isFranchiseChronoOrder() ? '0' : '1');
  openModal(idx);
  updateFranchiseOrderToggleBtn();
  if (document.documentElement.classList.contains('franchise-mode')) renderFranchiseWhatsNext();
};

window.toggleFranchiseOrderGlobal = function() {
  localStorage.setItem('franchiseChronoOrder', isFranchiseChronoOrder() ? '0' : '1');
  updateFranchiseOrderToggleBtn();
  renderFrontPanels();
};

function renderFranchiseTimeline(show, idx) {
  const m = franchiseEntry(show);
  if (!m) return '';
  const chrono = isFranchiseChronoOrder();
  const pos = chrono ? m.chrono : m.release;
  const sorted = FRANCHISE_TITLES.slice().sort((a, b) => (chrono ? a.chrono - b.chrono : a.release - b.release));
  const myPos = sorted.findIndex(mm => mm.id === m.id && mm.type === m.type);
  // Keyed by "type:id", not just id - a Movie tmdb_id and a TV tmdb_id can
  // collide numerically (independent TMDB namespaces), so id alone would
  // silently drop one of them from this lookup.
  const idxByKey = new Map(shows.map((s, i) => [`${s.type}:${s.tmdb_id}`, i]));
  let prevIdx = null, nextIdx = null;
  for (let i = myPos - 1; i >= 0; i--) {
    const key = `${sorted[i].type}:${sorted[i].id}`;
    if (idxByKey.has(key)) { prevIdx = idxByKey.get(key); break; }
  }
  for (let i = myPos + 1; i < sorted.length; i++) {
    const key = `${sorted[i].type}:${sorted[i].id}`;
    if (idxByKey.has(key)) { nextIdx = idxByKey.get(key); break; }
  }
  const prevBtn = prevIdx !== null
    ? `<button type="button" class="star-btn" onclick="event.stopPropagation();modalOpenFrom(${prevIdx})">&larr; Previous in timeline</button>` : '';
  const nextBtn = nextIdx !== null
    ? `<button type="button" class="star-btn" onclick="event.stopPropagation();modalOpenFrom(${nextIdx})">Next in timeline &rarr;</button>` : '';
  return `<div class="franchise-timeline">
    <div class="franchise-timeline-row">
      <span>🧭 ${chrono ? 'Chronological' : 'Release order'} #${pos} of ${FRANCHISE_TITLES.length}</span>
      <button type="button" class="star-btn" onclick="event.stopPropagation();toggleFranchiseOrder(${idx})">${chrono ? '&#128197; Switch to release order' : '&#127756; Switch to chronological order'}</button>
    </div>
    <div class="franchise-timeline-nav">${prevBtn}${nextBtn}</div>
  </div>`;
}

// --- TV modal-hero backdrop carousel (SPEC 7.1) ----------------------
// The big hero image on a TV show's popup: hover it for ‹ › arrows to
// cycle through every backdrop TMDB has, and a "use this" toggle to pin
// one. Works in both the home app (SERVER_MODE) and a travel export:
//   * fetch: home -> /api/tmdb-backdrops (server-proxied, no key on the
//     page); travel -> TMDB directly with the inline key.
//   * image: home -> tmdbImageUrl('backdrop', ...) (server downloads +
//     caches it); travel -> the live TMDB CDN.
//   * pin: home -> POST /api/override field:'backdrop' -> a permanent
//     backdrop_override in user_state.json (survives refreshes, exactly
//     like a poster override); travel -> localStorage['tvBackdrop:<key>']
//     (the travel-changes.json mirror is a later item).
//   * < 2 backdrops, or the fetch fails (offline) -> controls hidden,
//     default backdrop shown unchanged.
const BD_CAROUSEL_ENABLED = (typeof SERVER_MODE !== 'undefined' && SERVER_MODE) || TRAVEL_MODE;
let _bdLists = {};  // show_key -> [file_path, ...] (in-memory)
function savedBackdrop(key) {
  try { return localStorage.getItem('tvBackdrop:' + key) || null; } catch (e) { return null; }
}
function _bdImgUrl(show, filePath) {
  return TRAVEL_MODE
    ? `https://image.tmdb.org/t/p/${TMDB_CDN_SIZE.backdrop}${filePath}`
    : tmdbImageUrl('backdrop', filePath);
}
function backdropCss(show) {
  // travel keeps its localStorage pick; the home app's pin is already
  // baked into show.tmdb_backdrop at load (backdrop_override), so the
  // default path covers it.
  if (TRAVEL_MODE) {
    const picked = savedBackdrop(show.show_key);
    if (picked) return `https://image.tmdb.org/t/p/${TMDB_CDN_SIZE.backdrop}${picked}`;
  }
  return tmdbImageUrl('backdrop', show.tmdb_backdrop);
}
async function setupBackdropCarousel(show) {
  const hero = document.querySelector('#modal-box .modal-hero');
  if (!hero || !BD_CAROUSEL_ENABLED || show.type !== 'TV' || !show.tmdb_id) return;
  const prev = hero.querySelector('.bd-prev'), next = hero.querySelector('.bd-next');
  const countEl = hero.querySelector('.bd-count'), setEl = hero.querySelector('.bd-set');
  if (!prev || !next || !countEl || !setEl) return;

  let list = _bdLists[show.show_key];
  if (!list && TRAVEL_MODE) {
    try {
      const cached = JSON.parse(localStorage.getItem('tvBackdropList:' + show.show_key) || 'null');
      if (Array.isArray(cached) && cached.length) list = cached;
    } catch (e) {}
  }
  if (!list) {
    try {
      let data;
      if (TRAVEL_MODE) {
        const resp = await fetch(`https://api.themoviedb.org/3/tv/${show.tmdb_id}/images?api_key=${encodeURIComponent(TRAVEL_TMDB_API_KEY)}&include_image_language=en,null`);
        data = await resp.json();
        list = (data.backdrops || []).map(b => b.file_path).filter(Boolean);
      } else {
        const resp = await fetch(`/api/tmdb-backdrops/${show.tmdb_id}?type=TV`, { headers: { 'X-Auth-Token': AUTH_TOKEN } });
        data = await resp.json();
        list = (data.backdrops || []).filter(Boolean);
      }
    } catch (e) { list = []; }
  }
  _bdLists[show.show_key] = list;
  if (TRAVEL_MODE) {
    try { if (list.length) localStorage.setItem('tvBackdropList:' + show.show_key, JSON.stringify(list)); } catch (e) {}
  }

  if (list.length < 2) {
    for (const el of [prev, next, countEl, setEl]) el.style.display = 'none';
    return;
  }

  // The pinned pick: travel -> the localStorage key; home -> the
  // backdrop_override, flagged as show.backdrop_pinned at load (the show
  // always has *a* tmdb_backdrop, so the flag is what distinguishes a real
  // pin from just the TMDB default).
  const pinnedPath = () => TRAVEL_MODE
    ? savedBackdrop(show.show_key)
    : (show.backdrop_pinned ? (show.tmdb_backdrop || null) : null);
  const savedIdx = pinnedPath() ? list.indexOf(pinnedPath()) : -1;
  let i = Math.max(0, savedIdx);
  const isPinned = () => !!pinnedPath() && list[i] === pinnedPath();
  const label = () => {
    countEl.textContent = `${i + 1} / ${list.length}`;
    setEl.textContent = isPinned() ? '★ saved' : '☆ use this';
    setEl.classList.toggle('saved', isPinned());
  };
  const paint = () => {
    hero.style.backgroundImage = `url('${_bdImgUrl(show, list[i])}')`;
    label();
  };
  // On open, only repaint if there's a pinned pick to show - otherwise
  // leave the default backdrop (offline-safe local file) untouched until
  // the user actually cycles.
  if (savedIdx >= 0) paint(); else label();
  prev.onclick = () => { i = (i - 1 + list.length) % list.length; paint(); };
  next.onclick = () => { i = (i + 1) % list.length; paint(); };
  setEl.onclick = async () => {
    const clearing = isPinned();
    if (TRAVEL_MODE) {
      try {
        if (clearing) localStorage.removeItem('tvBackdrop:' + show.show_key);
        else localStorage.setItem('tvBackdrop:' + show.show_key, list[i]);
      } catch (e) {}
    } else {
      const r = await postJSON('/api/override', { show_key: show.show_key, field: 'backdrop', value: clearing ? '' : list[i] });
      if (!r || r.ok === false) { if (typeof showToast === 'function') showToast('Could not save the backdrop.'); return; }
      // reflect it live so backdropCss()/reopen use the new pin
      show.tmdb_backdrop = clearing ? (r.default_backdrop || show.tmdb_backdrop) : list[i];
      show.backdrop_pinned = !clearing;
    }
    paint();
    if (typeof showToast === 'function') showToast(clearing ? 'Backdrop reset to default' : 'Backdrop pinned for this show');
  };
}

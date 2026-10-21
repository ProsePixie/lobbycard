// --- Add a title (physical disc or Amazon purchase) ---

window.onAdminAddSourceChange = function() {
  const source = document.getElementById('admin-add-source').value;
  document.getElementById('admin-add-streaming-link').style.display =
    (source === 'netflix' || source === 'prime') ? 'block' : 'none';
};

// "Paste the real page" resolver (2026-09-20) - a typed guess into TMDB
// search is exactly what goes wrong for a brand-new or ambiguously-titled
// release ("Monster: The Lizzie Borden Story" was the real report this
// was built for). /api/streaming/resolve-link reads the actual on-site
// title off the pasted Netflix/Prime page (both serve that publicly, no
// login needed - confirmed live) and hands it back here; this only
// pre-fills + auto-runs the SAME search-and-pick flow every other "Add a
// title" path already uses, it doesn't skip confirming the real TMDB
// match. The resolved deep link is remembered (adminState.resolvedDeepLink)
// so it can pre-fill the "Watch link" field automatically once a match is
// picked, instead of making you paste the same URL a second time.
window.onAdminResolveStreamingLink = async function() {
  const urlInput = document.getElementById('admin-add-streaming-url');
  const url = urlInput.value.trim();
  if (!url) return;
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = 'Resolving...';
  try {
    const resp = await fetch('/api/streaming/resolve-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Auth-Token': AUTH_TOKEN },
      body: JSON.stringify({ url }),
    });
    const result = await resp.json().catch(() => null);
    if (!result || !result.ok) {
      showToast((result && result.message) || 'Could not read a title off that page.');
      if (result && result.deep_link) adminState.resolvedDeepLink = result.deep_link;
      return;
    }
    document.getElementById('admin-add-query').value = result.title;
    showToast(`Found "${result.title}" - searching TMDB...`);
    await onAdminAddSearch();
    // Set AFTER the search, not before - onAdminAddSearch() clears this on
    // every run (including this one) specifically so a stale resolved link
    // can never attach itself to some other, later, manually-searched title.
    adminState.resolvedDeepLink = result.deep_link;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Resolve title';
  }
};

window.onAdminAddSearch = async function() {
  const q = document.getElementById('admin-add-query').value.trim();
  const type = document.getElementById('admin-add-type').value;
  const resultsEl = document.getElementById('admin-add-results');
  if (!q) return;
  // Cleared on every search (including one about to be re-set by the
  // resolver right after this call returns) - see onAdminResolveStreamingLink's
  // own comment on why this ordering matters.
  adminState.resolvedDeepLink = null;
  resultsEl.innerHTML = '<div class="admin-note">Searching...</div>';
  const resp = await fetch(`/api/tmdb-search?q=${encodeURIComponent(q)}&type=${encodeURIComponent(type)}`);
  const data = await resp.json().catch(() => null);
  const results = (data && data.results) || [];
  adminState.addResults = results;
  adminState.addResultsType = type;
  if (!results.length) { resultsEl.innerHTML = '<div class="admin-note">No results.</div>'; return; }
  // Index-based onclick, not embedded title/poster strings - a title
  // with an apostrophe (Ocean's Eleven, Schindler's List, ...) would
  // otherwise break out of the onclick attribute's own quoting.
  resultsEl.innerHTML = results.map((r, i) => `
    <div class="admin-pick-card" onclick="onAdminAddSelect(${i})">
      ${adminPosterThumb(r.poster_path, r.title)}
      <div class="admin-pick-title">${escapeHtml(r.title)}${r.year ? ` (${r.year})` : ''}</div>
    </div>`).join('');
};

window.onAdminAddSelect = function(i) {
  const r = adminState.addResults[i];
  const type = adminState.addResultsType;
  const source = document.getElementById('admin-add-source').value;
  adminState.addPick = { tmdbId: r.tmdb_id, title: r.title, posterPath: r.poster_path, type, source };
  const { title, posterPath } = adminState.addPick;
  document.getElementById('admin-add-results').innerHTML = '';
  const el = document.getElementById('admin-add-selected');
  const thumb = posterPath
    ? `<img src="${tmdbImageUrl('poster', posterPath)}" alt="" onerror="onTmdbImageError(this)">`
    : `<div class="admin-pick-placeholder" style="width:70px; aspect-ratio:2/3;">${escapeHtml(title)}</div>`;
  const isStreaming = source === 'netflix' || source === 'prime';
  const titleFieldLabel = source === 'physical' ? 'Spine title (as it reads on the box)' : 'Title';
  const saveLabel = {
    physical: 'Save to physical collection', amazon: 'Save to Amazon purchases',
    netflix: 'Follow on Netflix', prime: 'Follow on Prime Video',
  }[source];
  el.innerHTML = `
    <div class="admin-current">
      ${thumb}
      <div>
        <div style="font-weight:700;">${escapeHtml(title)}</div>
        <div class="admin-note">${type} &middot; TMDB id ${adminState.addPick.tmdbId}</div>
      </div>
    </div>
    <div class="admin-field">
      <label class="admin-label">${titleFieldLabel}</label>
      <input class="admin-input" type="text" id="admin-add-spine" value="${escapeHtml(title)}">
    </div>
    ${type === 'TV' && !isStreaming ? `
    <div class="admin-field" style="max-width:160px;">
      <label class="admin-label">Owned seasons (1-N)</label>
      <input class="admin-input" type="number" id="admin-add-seasons" min="1" max="50" value="1">
    </div>` : ''}
    ${isStreaming ? `
    <div class="admin-field">
      <label class="admin-label">Watch link (optional)</label>
      <input class="admin-input" type="text" id="admin-add-deeplink" value="${escapeHtml(adminState.resolvedDeepLink || '')}" placeholder="Paste the real Netflix/Prime page if you have it - a search link is used otherwise">
      <div class="admin-note">${adminState.resolvedDeepLink
        ? 'Pre-filled from the page you resolved above - change it if this isn’t the right match.'
        : 'Tracks the whole show going forward - new seasons/episodes get picked up automatically, same as anything else in your library. Unfollow any time from the title’s own popup.'
      }</div>
    </div>` : ''}
    <button class="header-btn" type="button" onclick="onAdminAddSave()">💾 ${saveLabel}</button>
  `;
};

window.onAdminAddSave = async function() {
  const pick = adminState.addPick;
  if (!pick) return;
  const titleValue = document.getElementById('admin-add-spine').value.trim();
  const isPhysical = pick.source === 'physical';
  const isStreaming = pick.source === 'netflix' || pick.source === 'prime';

  if (isStreaming) {
    const deepLink = document.getElementById('admin-add-deeplink').value.trim();
    const body = {
      type: pick.type === 'Movie' ? 'movie' : 'tv',
      tmdb_id: pick.tmdbId,
      provider: pick.source === 'netflix' ? 'netflix' : 'prime',
      title: titleValue || pick.title,
      deep_link: deepLink || undefined,
    };
    const result = await postJSON('/api/streaming/follow', body);
    if (!result || !result.ok) { showToast((result && result.message) || 'Failed to follow.'); return; }
    showToast(result.message || 'Following.');
    document.getElementById('admin-add-selected').innerHTML = '';
    document.getElementById('admin-add-query').value = '';
    adminState.addPick = null;
    if (typeof showBootOverlay === 'function') showBootOverlay('Saving...');
    if (typeof pollRefreshStatus === 'function') setTimeout(() => pollRefreshStatus('sync'), 800);
    return;
  }

  const body = {
    type: pick.type === 'Movie' ? 'movie' : 'tv',
    tmdb_id: pick.tmdbId,
  };
  body[isPhysical ? 'spine_title' : 'title'] = titleValue || pick.title;
  if (pick.type === 'TV') {
    body.owned_seasons = parseInt(document.getElementById('admin-add-seasons').value, 10) || 1;
  }
  const result = await postJSON(isPhysical ? '/api/physical/add' : '/api/amazon/add', body);
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to save.'); return; }
  showToast(result.message || 'Saved.');
  document.getElementById('admin-add-selected').innerHTML = '';
  document.getElementById('admin-add-query').value = '';
  adminState.addPick = null;
  if (typeof showBootOverlay === 'function') showBootOverlay('Saving...');
  if (typeof pollRefreshStatus === 'function') setTimeout(() => pollRefreshStatus('sync'), 800);
};

window._watchListType = window._watchListType || 'all';
window.setWatchListType = function(t) {
  window._watchListType = t;
  openTop10View();
};

window.openTop10View = function() {
  _modalCurrent = function() { openTop10View(); };
  // Always newest-added-to-the-library first (same field the grid's
  // "recent" sort uses), never alphabetical - shows[] itself is title-sorted.
  const byAdded = (a, b) => (b.latest_added || '').localeCompare(a.latest_added || '');
  const type = window._watchListType;
  const typeOk = (s) => type === 'all' || s.type === type;
  const listed = shows.filter(s => s.top10_you || s.top10_partner);
  const typeCounts = {
    all: listed.length,
    TV: listed.filter(s => s.type === 'TV').length,
    Movie: listed.filter(s => s.type === 'Movie').length,
  };
  const you = shows.filter(s => s.top10_you && typeOk(s)).sort(byAdded);
  const partner = shows.filter(s => s.top10_partner && typeOk(s)).sort(byAdded);
  const both = you.filter(s => s.top10_partner);
  const partnerOnly = partner.filter(s => !s.top10_you);
  const youOnly = you.filter(s => !s.top10_partner);
  const emptyMsg = type === 'all'
    ? "Nothing here yet — star a favorite from any show's detail view."
    : `No ${type === 'TV' ? 'TV shows' : 'movies'} here yet.`;
  const chip = (t, label) =>
    `<button type="button" class="watchlist-chip${type === t ? ' active' : ''}" onclick="setWatchListType('${t}')">${label} (${typeCounts[t]})</button>`;

  const gridOf = (list) => list.length
    ? `<div class="grid watchlist-grid">${list.map(s => renderGridCard(s, shows.indexOf(s))).join('')}</div>`
    : '';

  const shelf = (title, list, emptyMsg, extraClass) => `
    <div class="watchlist-shelf${extraClass ? ' ' + extraClass : ''}">
      <div class="watchlist-shelf-title">${title} <span class="watchlist-count">${list.length}</span></div>
      ${list.length ? gridOf(list) : `<p class="watchlist-empty">${emptyMsg}</p>`}
    </div>`;

  document.getElementById('modal-box').className = 'modal-box';
  document.getElementById('modal-box').innerHTML = `
    <div class="modal-header${modalStack.length ? ' with-back' : ''}">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      ${modalBackBtnHtml()}
      <div class="title">Watch List</div>
    </div>
    <div class="entries" style="display:block; max-height: 75vh; overflow-y: auto; padding: 16px;">
      <div class="watchlist-filter">${chip('all', 'All')}${chip('TV', 'TV')}${chip('Movie', 'Movies')}</div>
      ${both.length ? shelf("🤝 Both of You Love These", both, "", "watchlist-shared") : ""}
      <div class="watchlist-columns">
        ${shelf("💗 " + PEOPLE.partner.name + "'s Watch List", partnerOnly, emptyMsg)}
        ${shelf("🎬 " + PEOPLE.you.name + "'s Watch List", youOnly, emptyMsg)}
      </div>
    </div>
  `;
  document.getElementById('modal-overlay').classList.add('open');
};

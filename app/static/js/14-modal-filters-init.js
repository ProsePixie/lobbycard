// --- Modal back-stack (2026-09-07) -----------------------------------
// The show detail, the Watch List and the Missing Episodes view all
// render into the same #modal-box. Drilling from one into another used
// to be a dead end - close, reopen the list, find your place again.
// modalStack remembers how to re-render each view you came through;
// modalOpenFrom() pushes the current view before switching; the "< Back"
// button (modalBackBtnHtml / modalBack) pops one level. closeModal()
// clears it. Views that AREN'T list-like (admin, help, dialogs) never
// register, so Back never appears there.
window.modalStack = window.modalStack || [];
window._modalCurrent = null;
function modalBackBtnHtml() {
  return modalStack.length
    ? '<button type="button" class="modal-back" onclick="modalBack()" title="Back to the previous view">&#8249; Back</button>'
    : '';
}
window.modalOpenFrom = function(idx) {
  // Called instead of openModal() for clicks that happen INSIDE an
  // already-open modal view (a Watch List card, a recommendation, an
  // actor's other title). Only then is there something to go back to.
  const overlay = document.getElementById('modal-overlay');
  if (overlay && overlay.classList.contains('open') && typeof _modalCurrent === 'function') {
    modalStack.push(_modalCurrent);
  }
  openModal(idx);
};
window.modalBack = function() {
  const restore = modalStack.pop();
  if (typeof restore === 'function') restore();
  else closeModal();
};

window.openModal = function(idx) {
  const show = shows[idx];
  _modalCurrent = function() { openModal(idx); };
  const yearLabel = show.years && show.years.length
    ? (show.years.length > 1 ? `${show.years[0]}–${show.years[show.years.length-1]}` : show.years[0])
    : '';
  const badgesHtml = `
    <span class="badge type-${show.type}">${show.type}</span>
    ${renderGenreBadges(show)}
    ${renderStatusBadges(show, true)}
    ${renderCategoryBadges(show)}
    ${renderExternalLinks(show)}
  `;
  // No backdrop image on the phone remote - plain text header instead.
  const hasHero = !!show.tmdb_backdrop && !show.runtime_mismatch;
  // TV backdrop carousel controls (SPEC 7.1) - wired up by
  // setupBackdropCarousel() after the modal renders; hidden by it when
  // offline or the show has < 2 backdrops.
  const bdControls = (hasHero && BD_CAROUSEL_ENABLED && show.type === 'TV') ? `
      <div class="bd-nav bd-prev" title="Previous backdrop">&#8249;</div>
      <div class="bd-nav bd-next" title="Next backdrop">&#8250;</div>
      <div class="bd-count"></div>
      <span class="bd-set">&#9734; use this</span>` : '';
  const headerHtml = hasHero ? `
    <div class="modal-hero" style="background-image:url('${backdropCss(show)}');">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      ${modalBackBtnHtml()}
      ${bdControls}
      <div class="modal-hero-content">
        ${show.tmdb_poster ? `<img class="modal-hero-poster" src="${tmdbImageUrl('poster', show.tmdb_poster)}" alt="" loading="lazy" decoding="async" onerror="onTmdbImageError(this)">` : ''}
        <div class="modal-hero-text">
          <div class="title">${escapeHtml(show.title)} <span class="years">${yearLabel}</span></div>
          <div class="modal-hero-badges">${badgesHtml}</div>
        </div>
      </div>
    </div>
  ` : `
    <div class="modal-header${modalStack.length ? ' with-back' : ''}">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      ${modalBackBtnHtml()}
      <div class="title">${escapeHtml(show.title)} <span class="years">${yearLabel}</span></div>
      <div style="margin-top:6px; display:flex; gap:5px; flex-wrap:wrap; align-items:center;">${badgesHtml}</div>
    </div>
  `;
  document.getElementById('modal-box').className = 'modal-box';
  document.getElementById('modal-box').innerHTML = `
    ${headerHtml}
    <div class="entries" style="display:block; max-height: 75vh; overflow-y: auto;">
      ${renderRetentionControl(show)}
      ${renderDetail(show, hasHero)}
      ${renderFranchiseTimeline(show, idx)}
      ${renderCastCrew(show)}
      ${renderRecommendations(show)}
      ${renderMissingEpisodes(show)}
      ${renderEpisodeSortToggle(show, idx)}
      ${renderExtrasToggle(show, idx)}
      ${renderEntriesList(show, idx)}
    </div>
  `;
  const _ov = document.getElementById('modal-overlay');
  _ov.classList.add('open');
  _ov.dataset.showIdx = idx;  // read by syncShowsFromServer() to close a modal whose show just left the library
  if (hasHero && BD_CAROUSEL_ENABLED && show.type === 'TV') setupBackdropCarousel(show);
};

window.closeModal = function() {
  const _ov = document.getElementById('modal-overlay');
  _ov.classList.remove('open');
  delete _ov.dataset.showIdx;
  modalStack.length = 0;
  _modalCurrent = null;
};

window.openActorPopup = function(personId) {
  // Reverse index built on demand from the already-loaded shows array -
  // no persisted state, no extra API call. CAST_LIMIT (pipeline\
  // enrich_tmdb.py) only keeps the top-billed cast per show, so a guest
  // star or supporting actor billed lower won't show up here even if
  // they're really in a show - a real, known limitation, not a bug.
  const matches = [];
  let name = null, imdbId = null, profilePath = null;
  shows.forEach(s => {
    const c = (s.tmdb_cast || []).find(c => c.id === personId);
    if (c) {
      matches.push(s);
      if (name === null) { name = c.name; imdbId = c.imdb_id || null; profilePath = c.profile_path || null; }
    }
  });
  const rows = matches.length
    ? matches.map(s => `<div class="actor-popup-show" onclick="closeActorPopup();modalOpenFrom(${shows.indexOf(s)})">${escapeHtml(s.title)}</div>`).join('')
    : '<div class="actor-popup-empty">No other titles in your library have this actor in the top-billed cast.</div>';
  // The photo needs no fetch at all - it's the exact same already-cached
  // cast-row image (see /tmdb-image/cast/... - downloaded once, forever
  // offline after that). Only the bio is genuinely new: fetched lazily,
  // once per actor ever (see /api/actor/<id> - cached in
  // tmdb_person_cache.json), so opening the same actor again later is
  // instant with no network call.
  const photo = profilePath
    ? `<img class="actor-popup-photo" src="${tmdbImageUrl('cast', profilePath)}" alt="" onerror="onTmdbImageError(this)">`
    : `<div class="actor-popup-photo actor-popup-photo-placeholder">${escapeHtml((name || '?').charAt(0))}</div>`;
  document.getElementById('actor-popup').innerHTML = `
    <button class="modal-close" onclick="closeActorPopup()">&times;</button>
    <div class="actor-popup-top">
      ${photo}
      <div>
        <div class="actor-popup-name">${escapeHtml(name || 'Actor')}</div>
        <div class="actor-popup-links">
          <a href="https://www.themoviedb.org/person/${personId}" target="_blank" rel="noopener">TMDB</a>
          ${imdbId ? `<a href="https://www.imdb.com/name/${imdbId}/" target="_blank" rel="noopener">IMDB</a>` : ''}
        </div>
        <div class="actor-popup-bio" id="actor-popup-bio">Loading bio...</div>
      </div>
    </div>
    <div class="actor-popup-label">In your library</div>
    <div class="actor-popup-shows">${rows}</div>
  `;
  document.getElementById('actor-popup-overlay').classList.add('open');

  const renderBio = (data) => {
    const bioEl = document.getElementById('actor-popup-bio');
    if (!bioEl) return; // popup closed (or a different actor opened) before this landed
    const parts = [];
    if (data.birthday) {
      const age = data.birthday ? Math.floor((Date.now() - new Date(data.birthday)) / 3.15576e10) : null;
      parts.push(`Born ${formatDate(data.birthday)}${age ? ` (age ${age})` : ''}${data.place_of_birth ? ` in ${escapeHtml(data.place_of_birth)}` : ''}`);
    }
    if (data.biography) {
      parts.push(escapeHtml(data.biography) + (data.biography.length >= 600 ? '…' : ''));
    }
    bioEl.innerHTML = parts.length
      ? parts.map(p => `<p>${p}</p>`).join('')
      : '<p class="actor-popup-empty">No biography available.</p>';
  };
  const clearBio = () => {
    const bioEl = document.getElementById('actor-popup-bio');
    if (bioEl) bioEl.textContent = '';
  };

  if (SERVER_MODE) {
    // Server-side cache (tmdb_person_cache.json) does the "fetched once,
    // ever" work here - see /api/actor/<id>.
    fetch(`/api/actor/${personId}`).then(r => r.json()).then(renderBio).catch(clearBio);
  } else if (TRAVEL_MODE && TRAVEL_TMDB_API_KEY) {
    // No server to hold a cache here, so this is a live TMDB call every
    // time - same direct-fetch pattern liveTmdbSearch() already
    // established for Scan for New Files (2026-08-23), same embedded key,
    // same "the popup should actually work on the road, not just when the
    // exact right data happened to get baked in at export time" reasoning
    // (2026-08-27, at the user's request). Silently leaves the bio blank
    // on failure - same as no internet at all, never an error dialog for
    // what's genuinely just "you're offline right now."
    fetch(`https://api.themoviedb.org/3/person/${personId}?api_key=${encodeURIComponent(TRAVEL_TMDB_API_KEY)}`)
      .then(r => r.json())
      .then(data => renderBio({ birthday: data.birthday, place_of_birth: data.place_of_birth, biography: data.biography }))
      .catch(clearBio);
  } else {
    clearBio();
  }
};

window.closeActorPopup = function() {
  document.getElementById('actor-popup-overlay').classList.remove('open');
};

window.toggleCard = function(idx) {
  // Tracked in window._expandedCards (not just the DOM class) because
  // applyFilters() rebuilds #list from scratch on every watched/retention/
  // travel toggle - it needs a real, non-DOM source of truth to know which
  // cards to re-expand, otherwise the card a user is actively checking off
  // episodes in collapses out from under them on the very first click.
  if (window._expandedCards.has(idx)) {
    window._expandedCards.delete(idx);
  } else {
    window._expandedCards.add(idx);
  }
  const card = document.querySelector(`.card[data-idx="${idx}"]`);
  if (card) card.classList.toggle('expanded');
};

document.addEventListener('click', (ev) => {
  // data-path specifically, not just the copy-btn class - the error
  // dialog's "Copy error message" button reuses copy-btn for styling only
  // (its own onclick="copyErrorMessage(this)" handles the actual copy) and
  // has no data-path, so without this check both handlers fired on the
  // same click and raced to reset the button's label afterward.
  // .closest(), not ev.target directly - an icon button's click can land on
  // its inner <svg>/<path>, which has no data-path or copy-btn class of
  // its own.
  const btn = ev.target.closest('.copy-btn[data-path]');
  if (btn) {
    const path = btn.getAttribute('data-path');
    const originalHtml = btn.innerHTML;
    const isIcon = btn.classList.contains('icon-btn');
    const done = () => {
      btn.innerHTML = isIcon ? ICON_CHECK : 'Copied!';
      btn.classList.add('copied');
      setTimeout(() => { btn.innerHTML = originalHtml; btn.classList.remove('copied'); }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(path).then(done).catch(() => fallbackCopy(path, done));
    } else {
      fallbackCopy(path, done);
    }
  }
});

function fallbackCopy(text, cb) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (e) {}
  document.body.removeChild(ta);
  cb();
}

// A real backend failure (a job's status.error, a network-level postJSON
// failure) deserves more than a 2.5s toast - the whole reason for this is
// someone not having time to read, let alone copy, an error before it
// vanished. Stays open (same modal-overlay/close-button every other popup
// already uses) until explicitly dismissed, with a one-click copy.
window.showErrorDialog = function(title, message) {
  document.getElementById('modal-box').className = 'modal-box';
  document.getElementById('modal-box').innerHTML = `
    <div class="modal-header">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      <div class="title">⚠️ ${escapeHtml(title)}</div>
    </div>
    <div class="entries" style="display:block; padding: 16px;">
      <pre class="error-message-box" id="error-message-text">${escapeHtml(message)}</pre>
      <button class="copy-btn" type="button" onclick="copyErrorMessage(this)">Copy error message</button>
    </div>
  `;
  document.getElementById('modal-overlay').classList.add('open');
};

// Same "no need to go dig in logs\" reasoning as showErrorDialog - Auto-
// File already knows exactly why each item didn't get filed (never a
// guess, always a specific TMDB/naming-spec reason), so show it right
// here instead of pointing at a dated file in logs\ the user has to find
// and open by hand (2026-08-28, reported: "I don't see anything in the
// logs" - the file DID have the answer, it just wasn't surfaced anywhere
// in the app itself).
window.showFlaggedDialog = function(flagged) {
  const rows = flagged.map(f => `${f.reason}\n  ${f.path}`).join('\n\n');
  document.getElementById('modal-box').className = 'modal-box';
  document.getElementById('modal-box').innerHTML = `
    <div class="modal-header">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      <div class="title">Not filed - flagged for review (${flagged.length})</div>
    </div>
    <div class="entries" style="display:block; padding: 16px;">
      <pre class="error-message-box" id="error-message-text">${escapeHtml(rows)}</pre>
      <button class="copy-btn" type="button" onclick="copyErrorMessage(this)">Copy</button>
    </div>
  `;
  document.getElementById('modal-overlay').classList.add('open');
};

window.copyErrorMessage = function(btn) {
  const text = document.getElementById('error-message-text').textContent;
  const originalLabel = btn.textContent;
  const done = () => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = originalLabel; btn.classList.remove('copied'); }, 1200);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
};

// Every applyFilters() clause except genre itself - shared with
// updateGenreCounts() below so a genre's displayed count and what
// selecting it would actually show can never drift apart again (they're
// now computed by the literal same predicate, not two independently-
// maintained copies).
function matchesSourceFilter(s, sf) {
  if (sf === 'both') return true;
  if (sf === 'physical') return !!s.physical_collection;
  if (sf === 'netflix') return s.streaming_provider === 'netflix';
  if (sf === 'prime') return s.streaming_provider === 'prime';
  return !s.physical_collection;  // 'digital' (default) - includes Amazon + streaming, same as before
}

// TV only - a movie has no "episode" runtime to speak of, and
// tmdb_episode_run_time is never set for one anyway. TMDB's own
// last_episode_to_air/next_episode_to_air runtime (enrich_tmdb.py), not a
// guess - a show with no runtime data at all (never aired, or TMDB just
// doesn't have it) doesn't match either way, same "don't flag on unknown
// data" treatment used everywhere else in this app.
function isShortEpisodeSeries(s) {
  return s.type === 'TV' && typeof s.tmdb_episode_run_time === 'number' && s.tmdb_episode_run_time > 0 && s.tmdb_episode_run_time < 30;
}

function passesFiltersExceptGenre(s, q, skipType) {
  return matchesQuery(s, q) &&
    (skipType || state.type === 'all' || s.type === state.type) &&
    matchesDateFilter(s, state.dateFilter) &&
    matchesSourceFilter(s, state.sourceFilter) &&
    (state.showRuntimeMismatchOnly ? s.runtime_mismatch === true : s.runtime_mismatch !== true) &&
    (!state.franchiseOnly || !!franchiseEntry(s)) &&
    (!state.weeklyTvOnly || isWeeklyTvCandidate(s)) &&
    (!state.shortEpisodesOnly || isShortEpisodeSeries(s)) &&
    // Inverted from a "hide" filter (2026-09-20, at the user's request) -
    // an audit view ("show me what she's ruled out") is more useful than
    // suppressing it, since the whole point is knowing what the other
    // person doesn't want to watch, not browsing around it.
    (!state.onlyPartnerNoInterest || s.no_interest_partner) &&
    (!state.onlyYouNoInterest || s.no_interest_you) &&
    // Compounding/restrictive, not additive - a show must carry EVERY
    // selected status, not just any one of them (each status is an
    // independent flag a show can hold several of at once, e.g. "still
    // airing" + "partway through" - see computeShowStatusKeys()), so
    // stacking filters narrows results instead of broadening them.
    // Confirmed intentional: picking two mutually-exclusive statuses
    // (Unwatched + Watched) always yields zero results - that's correct,
    // not a bug, per the user's explicit call.
    (state.statusFilter.size === 0 || [...state.statusFilter].every(k => computeShowStatusKeys(s).includes(k)));
}

// Recomputes "TV (N)"/"Movies (N)" to reflect what picking THAT tab would
// actually show given every other currently-active filter (genre, date,
// source, the person-specific toggles, etc.) - confirmed real, 2026-09-30:
// these were a frozen server-side whole-library snapshot from page load,
// so they never changed no matter what filters were applied, and the
// footer's "X of Y" compared against the raw whole-library row count
// (both types plus physical-collection placeholders) instead of anything
// related to what was actually on screen - a real user report ("the math
// isn't mathing"). Same "except this one dimension" shape as
// updateGenreCounts() right below. Returns the count for whichever tab is
// CURRENTLY selected (TV + Movie combined for "all"), so applyFilters()
// can reuse this exact number as the footer's own denominator - the two
// can no longer say different things about the same view.
function updateTypeCounts(q) {
  let tv = 0, movie = 0;
  for (const s of shows) {
    if (!passesFiltersExceptGenre(s, q, true)) continue;
    if (state.genre !== 'all' && !(s.tmdb_genres || [s.genre]).includes(state.genre)) continue;
    if (s.type === 'TV') tv++;
    else if (s.type === 'Movie') movie++;
  }
  const tvChip = document.querySelector('.type-chip[data-type="TV"]');
  const movieChip = document.querySelector('.type-chip[data-type="Movie"]');
  if (tvChip) tvChip.textContent = `TV (${tv})`;
  if (movieChip) movieChip.textContent = `Movies (${movie})`;
  if (state.type === 'TV') return tv;
  if (state.type === 'Movie') return movie;
  return tv + movie;
}

// Recomputes the (N) next to every genre option to reflect what picking
// THAT genre would actually show given every other currently-active
// filter - not a frozen whole-library snapshot (see genreCounts above).
// Called on every applyFilters() run, so it's always as current as the
// results themselves.
function updateGenreCounts(q) {
  const counts = {};
  for (const s of shows) {
    if (!passesFiltersExceptGenre(s, q)) continue;
    for (const g of (s.tmdb_genres || [s.genre])) {
      counts[g] = (counts[g] || 0) + 1;
    }
  }
  document.querySelectorAll('#genre-filter option').forEach(opt => {
    if (opt.value === 'all') return;
    opt.textContent = `${opt.value} (${counts[opt.value] || 0})`;
  });
}

// Render dirty-check (2026-09-04, folded in from the travel build). Every
// applyFilters() call used to blow away #list / #grid with a fresh
// innerHTML = filtered.map(renderCard).join('') - ~180 cards rebuilt even
// when nothing about the result actually changed (a debounced keystroke
// that didn't alter the set, a re-filter fired by a side panel, etc).
// _lastRenderSig fingerprints everything that affects the rendered cards -
// the filtered set + order, the view/sort, and the per-show state that
// card content is derived from (watched counts, readiness, retention,
// runtime-mismatch, travel-select, autofile). If it's byte-identical to
// the last render, skip the rebuild. Because the fingerprint includes the
// mutable per-show fields, callers that change card content in place
// (onWatchedToggle, onRetentionChange, onRestartShow, ...) do NOT need to
// manually invalidate it - the next applyFilters() sees the changed
// fingerprint and redraws.
let _lastRenderSig = null;
function _renderSig(filtered) {
  // JSON.stringify rather than a delimiter join - show titles / keys can
  // contain anything, so there's no separator char that's guaranteed safe.
  return JSON.stringify([
    state.view, state.sort, !!state.weeklyTvOnly, shows.length, filtered.length,
    filtered.map(s => [
      s.show_key || s.title,
      s.entries.filter(e => e.watched).length, s.entries.length,
      !!s.all_watched,
      s.ready_to_delete_count || 0,
      s.retention || '',
      !!s.runtime_mismatch,
      s.entries.filter(e => e.travel_selected).length,
      s.entries.filter(e => e.retention_override).length,
      s.autofile_ready_count || 0, s.autofile_needs_review_count || 0,
    ]),
  ]);
}
function applyFilters() {
  const q = state.query.trim().toLowerCase();
  let filtered = shows.filter(s =>
    passesFiltersExceptGenre(s, q) &&
    (state.genre === 'all' || (s.tmdb_genres || [s.genre]).includes(state.genre))
  );
  updateGenreCounts(q);
  const typeScopedTotal = updateTypeCounts(q);

  filtered.sort((a, b) => {
    // Weekly TV mode always sorts soonest-airing first, overriding the
    // normal sort dropdown - same "this mode has its own fixed order"
    // precedent Franchise Mode's chronological toggle already sets.
    if (state.weeklyTvOnly) {
      return (a.tmdb_next_episode.air_date).localeCompare(b.tmdb_next_episode.air_date);
    }
    switch (state.sort) {
      case 'title-desc': return b.title.localeCompare(a.title);
      case 'recent': return (b.latest_added||'').localeCompare(a.latest_added||'');
      case 'release-desc': return (b.latest_release_date||'').localeCompare(a.latest_release_date||'');
      case 'count-desc': return (b.version_count||0) - (a.version_count||0);
      case 'size-desc': return (b.total_size_bytes||0) - (a.total_size_bytes||0);
      case 'wasted-desc': return (b.wasted_bytes||0) - (a.wasted_bytes||0);
      default: return a.title.localeCompare(b.title);
    }
  });

  const listEl = document.getElementById('list');
  const gridEl = document.getElementById('grid');
  const countEl = document.getElementById('result-count');
  const emptyEl = document.getElementById('empty-state');
  countEl.textContent = `${filtered.length} of ${typeScopedTotal} titles`;

  // Nothing that affects the cards changed since the last render - the
  // count text + genre counts above are already refreshed, so bail before
  // the expensive innerHTML rebuild. (Front panels, purge/autofile buttons
  // and the retention row are updated by their own callers, not here.)
  const _sig = _renderSig(filtered);
  if (_sig === _lastRenderSig) return;
  _lastRenderSig = _sig;

  if (filtered.length === 0) {
    listEl.innerHTML = '';
    gridEl.innerHTML = '';
    listEl.style.display = 'none';
    gridEl.style.display = 'none';
    emptyEl.style.display = 'block';
    return;
  }

  emptyEl.style.display = 'none';
  if (state.view === 'grid') {
    listEl.style.display = 'none';
    listEl.innerHTML = '';
    gridEl.style.display = 'grid';
    gridEl.innerHTML = filtered.map((s) => renderGridCard(s, shows.indexOf(s), null, state.weeklyTvOnly ? weeklyTvBadge(s) : '')).join('');
  } else {
    gridEl.style.display = 'none';
    gridEl.innerHTML = '';
    listEl.style.display = 'block';
    listEl.innerHTML = filtered.map((s) => renderCard(s, shows.indexOf(s))).join('');
  }
}

// Search runs on a 140 ms debounce (2026-09-04, folded in from the travel
// build): the #search 'input' event fires per keystroke and applyFilters()
// rebuilds the entire card list (innerHTML = filtered.map(renderCard)), so
// typing a multi-word query used to trigger a full re-render on every
// letter. state.query updates immediately (so nothing feels laggy); the
// actual filter/re-render is coalesced. The other filter controls below are
// 'change' events (one fire per interaction), so they stay direct.
let _searchDebounce = null;
document.getElementById('search').addEventListener('input', (e) => {
  state.query = e.target.value;
  clearTimeout(_searchDebounce);
  _searchDebounce = setTimeout(applyFilters, 140);
});
document.getElementById('genre-filter').addEventListener('change', (e) => { state.genre = e.target.value; applyFilters(); });
document.getElementById('date-filter').addEventListener('change', (e) => { state.dateFilter = e.target.value; applyFilters(); });
document.getElementById('source-filter').addEventListener('change', (e) => { state.sourceFilter = e.target.value; applyFilters(); });
document.getElementById('sort-by').addEventListener('change', (e) => { state.sort = e.target.value; applyFilters(); });
function wireFilterToggle(id, stateKey) {
  const btn = document.getElementById(id);
  btn.addEventListener('click', () => {
    state[stateKey] = !state[stateKey];
    btn.classList.toggle('active', state[stateKey]);
    applyFilters();
  });
}
wireFilterToggle('runtime-mismatch-filter', 'showRuntimeMismatchOnly');
wireFilterToggle('franchise-only-filter', 'franchiseOnly');
wireFilterToggle('weekly-tv-toggle', 'weeklyTvOnly');
wireFilterToggle('short-episodes-filter', 'shortEpisodesOnly');
wireFilterToggle('only-partner-no-interest-filter', 'onlyPartnerNoInterest');
wireFilterToggle('only-you-no-interest-filter', 'onlyYouNoInterest');
document.querySelectorAll('.status-legend-item').forEach(item => {
  item.addEventListener('click', () => {
    const status = item.getAttribute('data-status');
    if (state.statusFilter.has(status)) {
      state.statusFilter.delete(status);
      item.classList.remove('active');
    } else {
      state.statusFilter.add(status);
      item.classList.add('active');
    }
    applyFilters();
  });
});
document.querySelectorAll('.type-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('.type-chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    state.type = chip.getAttribute('data-type');
    applyFilters();
  });
});
// Reflect the starting view (list by default on the phone remote) on the
// toggle before wiring clicks - the markup hard-codes Grid as active.
document.querySelectorAll('.view-toggle button').forEach(b => b.classList.toggle('active', b.dataset.view === state.view));
document.querySelectorAll('.view-toggle button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.view-toggle button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.view = btn.getAttribute('data-view');
    applyFilters();
  });
});
// Dashboard is a real popup now (2026-08-23), not a toggled inline drawer -
// same modal-overlay/.open pattern every other popup already uses, just in
// its own dedicated overlay (not the shared #modal-overlay, since that one
// gets its innerHTML replaced per-popup and the Dashboard's content is
// server-rendered once at page load, not rebuilt in JS).
window.openDashboardPopup = function() {
  document.getElementById('dashboard-overlay').classList.add('open');
  document.getElementById('dashboard-toggle').classList.add('active');
};
window.closeDashboardPopup = function() {
  document.getElementById('dashboard-overlay').classList.remove('open');
  document.getElementById('dashboard-toggle').classList.remove('active');
};
document.getElementById('dashboard-overlay').addEventListener('click', (e) => {
  if (e.target.id === 'dashboard-overlay') closeDashboardPopup();
});

window.openTravelPanel = function() {
  document.getElementById('travel-overlay').classList.add('open');
  document.getElementById('travel-mode-btn').classList.add('active');
  // Import-watched-log only makes sense at home (needs the local server to
  // write into user_state.json); Scan-for-new-files only makes sense on the
  // road (it's reading THIS drive's own New Downloads folder). Never both
  // at once, so each is gated to the mode it can actually do something in.
  document.getElementById('travel-import-section').style.display = SERVER_MODE ? '' : 'none';
  document.getElementById('travel-scan-section').style.display = TRAVEL_MODE ? '' : 'none';
};
window.closeTravelPanel = function() {
  document.getElementById('travel-overlay').classList.remove('open');
  document.getElementById('travel-mode-btn').classList.remove('active');
};
document.getElementById('travel-overlay').addEventListener('click', (e) => {
  if (e.target.id === 'travel-overlay') closeTravelPanel();
});

// Server-side native folder dialog (2026-08-23) - see /api/browse-folder's
// own comment in web_server.py for why this is possible at all (only
// because the server and browser are always the same machine here).
window.browseTravelDest = async function() {
  if (!SERVER_MODE) { showToast('Start the local server to use this'); return; }
  const btn = event.target;
  const originalText = btn.textContent;
  btn.textContent = 'Waiting for folder picker...';
  btn.disabled = true;
  try {
    const resp = await fetch('/api/browse-folder', { method: 'POST', headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const data = await resp.json().catch(() => null);
    if (data && data.ok && data.path) {
      document.getElementById('travel-dest-input').value = data.path;
    }
  } catch (e) {
    showToast('Could not reach the server for the folder picker.');
  }
  btn.textContent = originalText;
  btn.disabled = false;
};

window.exportDuplicatesList = async function() {
  const result = await postJSON('/api/export-duplicates', {});
  if (!result || !result.ok) { showToast((result && result.message) || 'Nothing to export.'); return; }
  showToast('Duplicate Files.txt exported to your Desktop.');
};

// Hub menus (File Management / Admin / Help) - each header-btn toggles its
// own popover; opening one closes any other that's open, a click outside
// or Escape closes whichever is open, and picking an item closes it too
// (see closeHubMenus() calls inline on each menu item's onclick).
const HUB_NAMES = ['filemgmt', 'admin', 'help'];
window.toggleHubMenu = function(name) {
  const menu = document.getElementById(`${name}-hub-menu`);
  const btn = document.getElementById(`${name}-hub-btn`);
  const willOpen = !menu.classList.contains('open');
  closeHubMenus();
  if (willOpen) {
    menu.classList.add('open');
    btn.classList.add('active');
  }
};
window.closeHubMenus = function() {
  HUB_NAMES.forEach(name => {
    const menu = document.getElementById(`${name}-hub-menu`);
    const btn = document.getElementById(`${name}-hub-btn`);
    if (menu) menu.classList.remove('open');
    if (btn) btn.classList.remove('active');
  });
};
document.addEventListener('click', (e) => {
  if (!e.target.closest('.hub-wrap')) closeHubMenus();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeHubMenus();
});

// "Logs" (Admin hub) - the existing Admin panel already has a "Refresh &
// Server Log" tab; this just opens straight to it instead of adding a
// second, separate log view to maintain.
window.openAdminLogs = function() {
  openAdminPanel();
  switchAdminTab('log');
};

// Franchise Mode and YouTube Mode both reskin the whole app (banner, front
// panels/grid) via an html class, so having both on at once doesn't mean
// anything coherent - each one turning on must force the other off. Each
// init below exposes its own setter on window so the OTHER one can call it
// without the two IIFEs needing to know about each other's internals. If
// both were somehow left '1' in localStorage from before this existed,
// YouTube Mode loses the tie-break (arbitrary, just needs to be one of them).
if (localStorage.getItem('franchiseMode') === '1' && localStorage.getItem('youtubeMode') === '1') {
  localStorage.setItem('youtubeMode', '0');
}

(function initFranchiseMode() {
  // Franchise mode drives the same state.franchiseOnly the filter-row button
  // sets, so turning the mode on/off keeps that button in sync rather than
  // leaving two controls silently disagreeing. The class on <html> also swaps
  // the banner and palette (see html.franchise-mode in the CSS).
  const btn = document.getElementById('franchise-mode-toggle');
  const filterCheckbox = document.getElementById('franchise-only-filter');
  const orderBtn = document.getElementById('franchise-order-toggle-btn');
  // No franchise in the pack, or none of its titles owned: the controls are
  // rendered hidden and there is nothing to wire up.
  if (!btn || btn.style.display === 'none' || !FRANCHISE_TITLES.length) return;
  const apply = (on) => {
    document.documentElement.classList.toggle('franchise-mode', on);
    btn.classList.toggle('active', on);
    state.franchiseOnly = on;
    filterCheckbox.classList.toggle('active', on);
    orderBtn.style.display = on ? '' : 'none';
    updateFranchiseOrderToggleBtn();
    applyFilters();
    renderFrontPanels();
    localStorage.setItem('franchiseMode', on ? '1' : '0');
  };
  window._setFranchiseMode = apply;
  apply(localStorage.getItem('franchiseMode') === '1');
  btn.addEventListener('click', () => {
    const on = !document.documentElement.classList.contains('franchise-mode');
    if (on && typeof window._setYoutubeMode === 'function') window._setYoutubeMode(false);
    apply(on);
  });
})();

(function initYoutubeMode() {
  // Header chip -> html.youtube-mode, persisted in localStorage, same shape
  // as Franchise Mode above. The chip is only in the DOM when the YouTube block
  // mounted server-side (render_engine's _yt_on), so bail cleanly otherwise.
  const btn = document.getElementById('youtube-mode-toggle');
  if (!btn) return;
  const apply = (on) => {
    document.documentElement.classList.toggle('youtube-mode', on);
    btn.classList.toggle('active', on);
    // Let the block know it's visible now (it can defer its first fetch /
    // resume polling). Guarded - the block may not have parsed yet.
    try { if (on && window.YTBlock && window.YTBlock.onShown) window.YTBlock.onShown(); } catch (e) {}
    localStorage.setItem('youtubeMode', on ? '1' : '0');
  };
  window._setYoutubeMode = apply;
  apply(localStorage.getItem('youtubeMode') === '1');
  btn.addEventListener('click', () => {
    const on = !document.documentElement.classList.contains('youtube-mode');
    if (on && typeof window._setFranchiseMode === 'function') window._setFranchiseMode(false);
    apply(on);
  });
})();

(function initTravelZoom() {
  // Travel-mode UI zoom (2026-09-04, folded in from the travel build /
  // hunk 6). The home-tuned UI renders small on a ~14" 1080p laptop; CSS
  // `zoom` on <html> scales text, images and spacing together so the
  // layout is unchanged, just larger. Ctrl+Alt+= / Ctrl+Alt+- step by
  // 0.05, Ctrl+Alt+0 resets to the default; persisted in
  // localStorage.travelUiZoom. Independent of tv-mode's --tv-scale. A real
  // slider for this lands with the travel Settings panel (SPEC 7.6); the
  // chord stays either way.
  if (!TRAVEL_MODE) return;
  const KEY = 'travelUiZoom', DEF = 1.18, MIN = 0.8, MAX = 2.0;
  const clamp = z => Math.min(MAX, Math.max(MIN, Math.round(z * 100) / 100));
  const get = () => {
    const v = parseFloat(localStorage.getItem(KEY));
    return (v >= MIN && v <= MAX) ? v : DEF;
  };
  const apply = z => { document.documentElement.style.zoom = z; };
  apply(get());
  document.addEventListener('keydown', (e) => {
    if (!e.ctrlKey || !e.altKey) return;
    let z = get();
    if (e.key === '=' || e.key === '+') z = clamp(z + 0.05);
    else if (e.key === '-' || e.key === '_') z = clamp(z - 0.05);
    else if (e.key === '0') z = DEF;
    else return;
    e.preventDefault();
    localStorage.setItem(KEY, String(z));
    apply(z);
    if (typeof showToast === 'function') showToast(`UI zoom ${Math.round(z * 100)}%`);
  });
})();
document.getElementById('refresh-btn').addEventListener('click', triggerRefresh);
if (!SERVER_MODE) {
  document.getElementById('refresh-btn').title = 'Start the local server to enable refresh';
}
if (TRAVEL_MODE) {
  document.getElementById('refresh-btn').style.display = 'none';
  document.getElementById('top10-btn').style.display = 'none';
  document.getElementById('export-log-btn').style.display = '';
  document.getElementById('purge-btn').style.display = 'none';
  document.getElementById('autofile-btn').style.display = 'none';
  document.getElementById('file-queue-btn').style.display = 'none';
  // 2026-09-13: those three used to BE the header buttons - now they're
  // nested inside the File Management hub's popover, so hiding them
  // individually (still needed - see closeHubMenus()/renderAdminFixSelected()
  // etc, nothing else knows they're irrelevant here) left the OUTER hub
  // button fully visible, opening what was now a completely empty popover.
  // Confirmed real via a code trace, not hypothetical. Hide the whole
  // wrapper too - nothing inside it applies in Travel Mode at all.
  const filemgmtHubWrap = document.getElementById('filemgmt-hub-wrap');
  if (filemgmtHubWrap) filemgmtHubWrap.style.display = 'none';
  // Person-specific "not interested" filters have no meaning on a shared
  // portable copy (SPEC 7.5) - hide them; the ⭐🙈 per-show buttons are
  // already gated out of renderRetentionControl() in travel mode.
  document.getElementById('only-partner-no-interest-filter').style.display = 'none';
  document.getElementById('only-you-no-interest-filter').style.display = 'none';
  // Admin panel is server-only and does nothing here - shareable copies
  // get a real Branding editor instead (plan item 17); a personal export
  // has nothing to edit (baked & locked at build time), so the gear just
  // disappears. admin-btn ("Fix / Add a Title") lives inside the Admin
  // hub's popover now, alongside Dashboard/Missing Episodes/Library
  // History/Logs - those three still apply in Travel Mode (they're just
  // display, no server needed), so the hub button itself STAYS visible;
  // only admin-btn and the new Logs shortcut (server-only, added after
  // this travel-mode logic was written) get individually hidden/repurposed.
  const adminBtn = document.getElementById('admin-btn');
  if (TRAVEL_SHARE_MODE) {
    // Also closes the Admin hub popover on click, same as every other
    // item in it (its own inline onclick did this before being replaced
    // wholesale here) - otherwise the popover stays open behind the
    // Branding editor that just opened.
    adminBtn.onclick = () => { closeHubMenus(); openTravelBranding(); };
    adminBtn.title = 'Branding & Display';
    // The label text itself still reads "Fix / Add a Title" from the
    // server template - repoint it too, not just the click handler.
    const labelEl = adminBtn.lastChild;
    if (labelEl && labelEl.nodeType === Node.TEXT_NODE) labelEl.textContent = 'Branding & Display';
  } else {
    adminBtn.style.display = 'none';
  }
  const adminLogsBtn = document.getElementById('admin-logs-btn');
  if (adminLogsBtn) adminLogsBtn.style.display = 'none';
  applyTravelBranding();
  // First-run discoverability (plan item 18) - a one-time dismissible
  // pointer at Setup.cmd/START HERE.html, not shown again on this
  // browser/computer once dismissed. Never blocks anything - Open Folder/
  // Play links just silently do nothing until Setup.cmd runs, which is
  // exactly the "found by accident, not explained" gap this closes.
  let alreadyDismissed = false;
  try { alreadyDismissed = !!localStorage.getItem('travelFirstRunDismissed'); } catch (e) {}
  if (!alreadyDismissed) {
    document.getElementById('travel-first-run-banner').style.display = 'flex';
  }
}
window.dismissTravelFirstRunBanner = function() {
  document.getElementById('travel-first-run-banner').style.display = 'none';
  try { localStorage.setItem('travelFirstRunDismissed', '1'); } catch (e) {}
};
if (typeof updateTravelPanel === 'function') updateTravelPanel();

document.getElementById('modal-overlay').addEventListener('click', (ev) => {
  if (ev.target.id === 'modal-overlay') closeModal();
});
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') closeModal();
});

applyFilters();
renderFrontPanels();
updatePurgeButton();
updateAutofileButton();

// Tab-refocus catch-up. Anything that changed the library server-side
// while this tab was in the background - a VORTEX rename, the nightly
// scan, a purge/file done from the PC while you were on your phone - is
// picked up silently the next time the tab is looked at, so you never
// have to know to hit Refresh. Server mode only, debounced so flicking
// back and forth doesn't hammer /api/shows-data, and skipped while a
// refresh is already in flight or the Refresh button is mid-run.
if (typeof SERVER_MODE !== 'undefined' && SERVER_MODE) {
  const _focusResync = () => {
    if (document.visibilityState === 'hidden') return;
    if (Date.now() - (window._lastSyncAt || 0) < 20000) return;
    const rb = document.getElementById('refresh-btn');
    if (rb && rb.classList.contains('is-busy')) return;
    silentResync('focus');
  };
  document.addEventListener('visibilitychange', _focusResync);
  window.addEventListener('focus', _focusResync);
}

// Boot overlay - this page loaded while a refresh (startup quick-scan or a
// nightly run) was still in progress. VORTEX's starting.html only waits for
// the server to answer at all, not for that refresh to finish, so without
// this the very first paint could show a half-populated grid / posters not
// cached yet - the thing the user has to reload a few times to get past.
// Blocks the view until pollRefreshStatus sees it's done, then does a real
// reload (same completion path the header Refresh button uses) so the next
// paint is the actual finished result, not a patched-up copy.
if (typeof SERVER_MODE !== 'undefined' && SERVER_MODE && REFRESH_STATUS && REFRESH_STATUS.running) {
  const el = document.getElementById('boot-overlay');
  if (el) {
    el.hidden = false;
    startLoadingHint();
    bootEnsureSpinner(); bootFxStart();
    if (REFRESH_STATUS.steps && REFRESH_STATUS.steps.length) updateBootOverlayFromStatus(REFRESH_STATUS);
    else updateBootOverlay(true, (REFRESH_STATUS.log || []).length, REFRESH_STATUS.total || 8);
  }
  pollRefreshStatus('reload');
}
// A refresh just finished and the page reloaded: the overlay is already up (inline script
// near the top), so lift it a moment after the library has actually painted.
try {
  if (sessionStorage.getItem('appDrawing') === '1' && !(REFRESH_STATUS && REFRESH_STATUS.running)) {
    sessionStorage.removeItem('appDrawing');
    bootEnsureSpinner(); bootFxStart();
    requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(hideBootOverlay, 350)));
  }
} catch (e) {}

// The Settings page needs the server, and only answers requests from the machine running it.
if (!SERVER_MODE || IS_REMOTE) {
  const settingsItem = document.getElementById('settings-btn');
  if (settingsItem) settingsItem.style.display = 'none';
}

const ICON_FOLDER ='<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"></path></svg>';
const ICON_PLAY = '<svg width="20" height="20" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"></circle><path d="M10 8l6 4-6 4z" fill="currentColor"></path></svg>';
const ICON_CAST = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="14" rx="2"></rect><path d="M8 21h8M12 18v3"></path><path d="M10.5 9.3l4.2 2.7-4.2 2.7z" fill="currentColor" stroke="none"></path></svg>';
// A title ending in "(YYYY)" was already year-disambiguated server-side
// (e.g. three "Doctor Who" series) - don't append the year a second time.
const TITLE_HAS_YEAR_RE = /\(\d{4}\)\s*$/;
const ICON_COPY = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"></rect><path d="M5 15V5a2 2 0 0 1 2-2h10"></path></svg>';
const ICON_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"></path></svg>';
// Retro TV (rabbit-ear antennas) with a checkmark on the screen - used only
// as the "Watched" column header icon. Deliberately a different silhouette
// than the flat monitor+stand icon on the TV Mode button (line 1065) so the
// two aren't confused at a glance.
const ICON_TV_WATCHED = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7L3 1M17 7l4-6"></path><rect x="4" y="7" width="16" height="12" rx="2"></rect><path d="M9 13l2 2 4-4"></path></svg>';
const ICON_SUITCASE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="18" height="13" rx="2"></rect><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><path d="M3 12h18"></path></svg>';
const ICON_LOCK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="9" rx="2"></rect><path d="M8 11V7a4 4 0 0 1 8 0v4"></path></svg>';

// Entry rows and their header (see renderEntryHeader below) are each an
// independent CSS grid using this SAME template string, rather than one
// grid spanning every row - simpler than restructuring the DOM hierarchy,
// and columns still line up perfectly as long as every row for a given
// show uses the identical template. Travel/Keep are conditional per show
// (TRAVEL_UI_ENABLED is page-global; retention is uniform across one
// show's entries), never per-row, so the template only needs to vary by
// show, not by entry.
function entryRowGridTemplate(showRetention) {
  const cols = ['30px'];
  if (TRAVEL_UI_ENABLED && SERVER_MODE && !TRAVEL_MODE) cols.push('30px');
  if (showRetention === 'delete_after_watch') cols.push('30px');
  cols.push('16px', 'minmax(0, 1fr)', '70px', '110px', '132px');
  return cols.join(' ');
}

function renderEntryHeader(showRetention) {
  // Icons, not text, for these three columns - they're sized to fit a
  // checkbox (30px), and header labels like "Watched"/"Travel" overflowed
  // past that width into neighboring cells since nothing clips grid content.
  const cells = [`<span class="header-icon" title="Watched">${ICON_TV_WATCHED}</span>`];
  if (TRAVEL_UI_ENABLED && SERVER_MODE && !TRAVEL_MODE) cells.push(`<span class="header-icon" title="Travel">${ICON_SUITCASE}</span>`);
  if (showRetention === 'delete_after_watch') cells.push(`<span class="header-icon" title="Keep (protect from delete-after-watch)">${ICON_LOCK}</span>`);
  cells.push('<span></span>', '<span>Episode</span>', '<span>Size</span>', '<span>Release date</span>', '<span style="text-align:right;">Options</span>');
  return `<div class="entry-header-row" style="grid-template-columns:${entryRowGridTemplate(showRetention)}">${cells.join('')}</div>`;
}

// Same phrase list ALT_VERSION_KEYWORDS uses in pipeline\scan_media.py -
// kept in sync deliberately (like consolidate.py's own copy) since it's
// the same real-world signal (a release group tagging a deliberately
// different cut), just consumed here for the 🏳️ flag instead of
// duplicate-detection.
const ALT_VERSION_KEYWORDS = ['alternative cut', 'extended cut', 'extended edition', 'extended version', 'directors cut', 'theatrical cut', 'unrated cut', 'uncut'];

function normalizeKey(s) {
  return (s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

// One flag icon, one reason - generalizes the original "season 0" special
// flag to cover everything "outside the normal numbered run," each with
// its own explanatory tooltip rather than a second icon, so the UI stays
// simple. Checked in this order because a season-0 special is the most
// certain signal (deliberate, not inferred), down to "unidentified" which
// is a real gap, not a positive detection.
function computeFlagReason(e, showType) {
  if (e.season === 0) {
    return 'Special - a full episode/long-form release outside the numbered seasons, sorted by release date';
  }
  if (e.is_extra) {
    return 'Extra - behind-the-scenes/bonus content bundled alongside the numbered episodes, not a real episode';
  }
  const altMatch = ALT_VERSION_KEYWORDS.find(kw => normalizeKey(e.name).includes(kw));
  if (altMatch) {
    return `Alternate version - release tagged "${altMatch}", a different cut of the same episode/movie`;
  }
  // A prior "aired near a holiday date range" heuristic used to live here
  // and flagged normal episodes as special purely from air_date, with no
  // TMDB signal backing it (unlike every other check in this function) -
  // removed 2026-08-26, confirmed actively wrong: 15 completely ordinary
  // Doctor Who episodes (Series 8/9/11/12/13 openers and finales, classic-
  // era stories spanning New Year's Eve) were getting flagged solely
  // because the show's real BBC broadcast pattern happens to land in
  // those calendar windows. A genuine holiday special is already caught
  // above via season === 0 (real TMDB data), which is the "never guess"
  // bar everything else in this app holds itself to.
  if (showType === 'TV' && e.season == null) {
    return "Unidentified - season/episode couldn't be determined from the filename";
  }
  return null;
}

// Real chronological slot for a season-0 special, among the show's own
// numbered seasons - positions it by real air_date instead of season 0
// always sorting/grouping before Season 1 regardless of when it actually
// aired (2026-08-26, confirmed real problem: a show's 2013 anniversary
// special was sorting ahead of its own 1963 Season 1). Every show with
// any season-0 entries gets this - general fix, not a per-show exception,
// since it's just sorting data TMDB already provides for every show by a
// field (air_date) already present, not inventing a cross-title
// relationship the way the MOVIE_TO_TV_MERGE sentinel below has to.
// Cached on the show object (invalidated implicitly - the show list is
// rebuilt fresh on every full reload, and entries never change season
// numbers between renders) since it's the same computation every time
// renderEntriesList() re-runs for this card.
function effectiveSortSeason(show, entry) {
  if (entry.season !== 0) return entry.season;
  if (!entry.air_date) return 0;  // no date to go on - safest fallback, groups at the front like before

  if (!show._seasonStartCache) {
    const starts = new Map();
    for (const e of show.entries) {
      if (e.season == null || e.season === 0 || !Number.isInteger(e.season) || !e.air_date) continue;
      const cur = starts.get(e.season);
      if (!cur || e.air_date < cur) starts.set(e.season, e.air_date);
    }
    show._seasonStartCache = starts;
  }
  const starts = show._seasonStartCache;
  const numbered = [...starts.keys()].sort((a, b) => a - b);
  let placedAfter = null;
  for (const s of numbered) {
    if (starts.get(s) <= entry.air_date) placedAfter = s;
    else break;
  }
  return placedAfter === null ? -0.5 : placedAfter + 0.5;
}

function seasonGroupKey(season) {
  return (season === null || season === undefined) ? 'none' : String(season);
}

function seasonGroupLabel(season) {
  if (season === 0) return 'Specials';
  // web_server.py's MOVIE_TO_TV_MERGE sentinel - a movie folded into a TV
  // show's own season list at a specific chronological slot (Futurama's 4
  // direct-to-video movies, Twin Peaks: Fire Walk with Me). Always
  // fractional there by design, and real TMDB season numbers are always
  // non-negative integers, so this can never misfire on real season data
  // for this or any other show.
  if (typeof season === 'number' && !Number.isInteger(season)) return '🎬 Movies';
  if (season === null || season === undefined) return 'Unidentified';
  return `S${String(season).padStart(2, '0')}`;
}

// Per-show, lazily-seeded collapse state for season groups - deliberately
// NOT persisted (same reasoning as window._extrasVisible above): resets on
// reload. Seeded once, the first time a show's entries render, from
// defaultCollapsedSeasons() below - after that, only toggleSeasonGroup()
// mutates it, so a season the user manually expands/collapses doesn't keep
// snapping back to its computed default on every watched-toggle re-render.
window._collapsedSeasons = window._collapsedSeasons || {};

function defaultCollapsedSeasons(show, entries) {
  const bySeasons = new Map();
  for (const e of entries) {
    const k = seasonGroupKey(effectiveSortSeason(show, e));
    if (!bySeasons.has(k)) bySeasons.set(k, []);
    bySeasons.get(k).push(e);
  }
  const collapsed = new Set();
  for (const [k, group] of bySeasons) {
    // A fully-watched season starts collapsed - the point of grouping is
    // surfacing where you actually need to pick up on a 15-season show,
    // not 15 flat walls of checkmarks you already know are done.
    if (group.every(e => e.watched)) collapsed.add(k);
  }
  return collapsed;
}

window.toggleSeasonGroup = function(idx, seasonKey) {
  const collapsed = window._collapsedSeasons[idx];
  if (!collapsed) return;
  if (collapsed.has(seasonKey)) collapsed.delete(seasonKey); else collapsed.add(seasonKey);
  refreshEntriesBlock(idx);
};

// Bulk-marks every currently-displayed entry in one season watched/unwatched
// in a single action - mirrors onWatchedToggle's per-episode side effects
// (readiness/autofile recompute, front-panel refresh, retention row) but
// applied to a whole season at once, plus a refreshEntriesBlock() so the
// season header's own rollup checkbox and count immediately reflect it.
window.toggleSeasonWatched = async function(idx, seasonKey, newWatched) {
  const show = shows[idx];
  // Matches by the same grouping key the header/card actually rendered
  // with (seasonGroupKey(effectiveSortSeason(...))), not raw e.season ===
  // season - a show can now have several distinct season-0-derived
  // groups (specials interleaved at different chronological points), all
  // sharing e.season === 0, so a plain season-number match would wrongly
  // sweep in every special across every slot instead of just this group's.
  const paths = getDisplayEntries(show, idx)
    .filter(e => seasonGroupKey(effectiveSortSeason(show, e)) === seasonKey)
    .map(e => e.path);
  if (!paths.length) return;
  const pathSet = new Set(paths);

  if (TRAVEL_MODE) {
    const travelWatched = loadTravelWatched();
    const travelWatchedAt = loadTravelWatchedAt();
    const at = new Date().toISOString();
    for (const path of paths) {
      travelWatched[path] = newWatched;
      if (newWatched) travelWatchedAt[path] = at; else delete travelWatchedAt[path];
    }
    saveTravelWatched(travelWatched);
    saveTravelWatchedAt(travelWatchedAt);
    for (const e of show.entries) {
      if (pathSet.has(e.path)) { e.watched = newWatched; e.watched_at = at; }
    }
    recomputeShowReadiness(show);
    recomputeAutofileEligibility(show);
    applyFilters();
    renderFrontPanels();
    updatePurgeButton();
    updateAutofileButton();
    refreshRetentionRow(show);
    refreshEntriesBlock(idx);
    return;
  }

  const result = await postJSON('/api/watched-batch', { paths, watched: newWatched });
  if (result && result.ok) {
    for (const e of show.entries) {
      if (pathSet.has(e.path)) { e.watched = newWatched; e.watched_at = result.at || e.watched_at; }
    }
    recomputeShowReadiness(show);
    recomputeAutofileEligibility(show);
    applyFilters();
    renderFrontPanels();
    updatePurgeButton();
    updateAutofileButton();
    refreshRetentionRow(show);
  }
  refreshEntriesBlock(idx);
};

function renderSeasonGroupHeader(idx, seasonKey, season, groupEntries) {
  const isExpanded = !window._collapsedSeasons[idx].has(seasonKey);
  const watchedCount = groupEntries.filter(e => e.watched).length;
  const total = groupEntries.length;
  const allWatched = total > 0 && watchedCount === total;
  return `<div class="season-group-header${isExpanded ? ' expanded' : ''}" onclick="toggleSeasonGroup(${idx}, '${seasonKey}')">
    <span class="season-chevron">&#9656;</span>
    <span class="season-group-label">${escapeHtml(seasonGroupLabel(season))}</span>
    <span class="season-group-count">${watchedCount}/${total} watched</span>
    <label class="watched-check" title="${allWatched ? 'Whole season watched' : 'Mark every episode in this season watched'}" onclick="event.stopPropagation()">
      <input type="checkbox" ${allWatched ? 'checked' : ''} onchange="toggleSeasonWatched(${idx}, '${seasonKey}', this.checked)">
    </label>
  </div>`;
}

function renderEntriesList(show, idx) {
  const extras = extraPathsSet(show);
  const entries = getDisplayEntries(show, idx);
  if (!entries.length) return '';

  // Only worth grouping/collapsing when there's actually more than one
  // distinct sort-season to sort through - a single-season show (the
  // vast majority) renders exactly as it always has, no extra chrome.
  // Grouped by effectiveSortSeason, not raw e.season, so a show with one
  // real season plus specials (season 0) still gets split/interleaved
  // rather than silently skipping the grouping path entirely.
  const sortSeasonValues = new Set(entries.map(e => effectiveSortSeason(show, e)));
  if (show.type !== 'TV' || sortSeasonValues.size <= 1) {
    return renderEntryHeader(show.retention) + entries.map(e => renderEntryRow(e, extras.has(e.path), show.retention, show.type, show.show_key)).join('');
  }

  if (!window._collapsedSeasons[idx]) {
    window._collapsedSeasons[idx] = defaultCollapsedSeasons(show, entries);
  }

  const groups = new Map();
  for (const e of entries) {
    const sortSeason = effectiveSortSeason(show, e);
    const k = seasonGroupKey(sortSeason);
    if (!groups.has(k)) groups.set(k, { season: e.season, sortSeason, entries: [] });
    groups.get(k).entries.push(e);
  }
  // Entries arrive pre-sorted by (season, episode) - fine for real season
  // numbers, but a season-0 special's sortSeason can now land anywhere
  // (e.g. 7.5, between Season 7 and 8) while its raw season (0) still put
  // it first in that original order - re-sort the groups themselves by
  // sortSeason so they actually render in the right chronological place.
  const orderedGroups = [...groups.entries()].sort((a, b) => {
    const sa = a[1].sortSeason, sb = b[1].sortSeason;
    if (sa == null) return sb == null ? 0 : 1;
    if (sb == null) return -1;
    return sa - sb;
  });

  const header = renderEntryHeader(show.retention);
  let html = '';
  for (const [seasonKey, group] of orderedGroups) {
    html += renderSeasonGroupHeader(idx, seasonKey, group.season, group.entries);
    if (!window._collapsedSeasons[idx].has(seasonKey)) {
      html += header + group.entries.map(e => renderEntryRow(e, extras.has(e.path), show.retention, show.type, show.show_key)).join('');
    }
  }
  return html;
}

function renderEntryRow(e, isDup, showRetention, showType, showKey) {
  const label = e.season != null && e.episode != null
    ? `S${String(e.season).padStart(2,'0')}E${String(e.episode).padStart(2,'0')} &middot; ${escapeHtml(e.name)}`
    : escapeHtml(e.name);
  const dupTag = isDup ? ` <span class="badge warn" title="Not the largest copy of this episode/title">extra copy</span>` : '';
  const flagReason = computeFlagReason(e, showType);
  const specialFlag = flagReason ? ` <span title="${escapeHtml(flagReason)}">🏳️</span>` : '';
  // A visible note, not just a hover tooltip on the action icon - the
  // whole point of merging sources into one card (see
  // web_server.py's merge_source_into_digital()) is that a mixed-source
  // show still reads clearly at a glance, not just on hover.
  const streamLbl = e.root === 'Netflix' ? 'Netflix' : (e.root === 'Prime' ? 'Prime Video' : '');
  const sourceNote = e.streaming_link
    ? ` <span class="badge streaming" title="Streaming on your subscription - the Play button opens ${streamLbl}">↗ ${streamLbl}</span>`
    : (e.amazon_link
      ? ` <span class="badge on-disc" title="Not a local file - the Play button opens Amazon Prime Video instead">↗ Amazon</span>`
      : (e.no_real_file ? ` <span class="badge on-disc" title="Owned physically, not digitized">on disc</span>` : ''));
  const travelCheck = (TRAVEL_UI_ENABLED && SERVER_MODE && !TRAVEL_MODE) ? `<label class="watched-check" title="Select for travel drive" onclick="event.stopPropagation()">
      <input type="checkbox" ${e.travel_selected ? 'checked' : ''} data-path="${escapeHtml(e.path)}" onchange="onTravelSelectToggle(this)">
    </label>` : '';
  const keepCheck = showRetention === 'delete_after_watch' ? `<label class="watched-check" title="Protect this one from the show's delete-after-watching policy" onclick="event.stopPropagation()">
      <input type="checkbox" ${e.retention_override ? 'checked' : ''} data-path="${escapeHtml(e.path)}" onchange="onRetentionOverrideToggle(this)" ${(SERVER_MODE || TRAVEL_MODE) ? '' : 'disabled'}>
    </label>` : '';
  // "Added" is a fallback, not a confirmed release date (see render_page()'s
  // e["release_date"] comment) - visually distinguished so it doesn't read
  // as equally authoritative.
  const dateCell = e.release_date
    ? `<span class="entry-date" title="Release date">${formatDate(e.release_date)}</span>`
    : `<span class="entry-date entry-date-fallback" title="Release date unknown - file added date shown instead">${formatDate(e.ctime || e.mtime)}</span>`;
  return `<div class="entry-row${isDup ? ' is-dup' : ''}${e.watched ? ' is-watched' : ''}" data-path="${escapeHtml(e.path)}" style="grid-template-columns:${entryRowGridTemplate(showRetention)}">
    <label class="watched-check" title="Mark watched" onclick="event.stopPropagation()">
      <input type="checkbox" ${e.watched ? 'checked' : ''} data-path="${escapeHtml(e.path)}" onchange="onWatchedToggle(this)" ${(SERVER_MODE || TRAVEL_MODE) ? '' : 'disabled'}>
    </label>
    ${travelCheck}
    ${keepCheck}
    <span class="root-dot r${ROOT_INDEX[e.root]}" title="${escapeHtml(friendlyRootTitle(e.root))}"></span>
    <span class="entry-name">${label}${specialFlag}${dupTag}${sourceNote}</span>
    <span class="entry-size">${formatBytes(e.size_bytes)}</span>
    ${dateCell}
    <span class="entry-actions">
      ${e.file_url ? (SERVER_MODE
        ? `<button class="icon-btn icon-btn-play" data-path="${escapeHtml(e.path)}" onclick="event.stopPropagation();playOnServer(this)" title="Play on the TV — controllable from any phone/tablet on the network">${ICON_PLAY}</button>`
        : `<a class="icon-btn icon-btn-play" href="${e.file_url}" title="Play">${ICON_PLAY}</a>`) : ''}
      ${e.url ? `<a class="icon-btn" href="${e.url}" data-path="${escapeHtml(e.path)}" onclick="return openLocation(event, this)" title="Location">${ICON_FOLDER}</a>` : ''}
      ${e.amazon_link ? `<a class="icon-btn icon-btn-play" href="${e.amazon_link}" data-show-key="${escapeHtml(showKey || '')}" data-entry-path="${escapeHtml(e.path)}" target="_blank" rel="noopener" onclick="return openStreamingLink(event, this)" title="Not a local file - opens Amazon Prime Video to watch this (you'll need to be signed in)">${ICON_PLAY}</a>` : ''}
      ${e.streaming_link ? `<a class="icon-btn icon-btn-play" href="${escapeHtml(e.streaming_link)}" data-show-key="${escapeHtml(showKey || '')}" data-entry-path="${escapeHtml(e.path)}" target="_blank" rel="noopener" onclick="return openStreamingLink(event, this)" title="Not a local file - opens ${streamLbl} to watch this (you'll need to be signed in)">${ICON_PLAY}</a>` : ''}
      <button class="copy-btn icon-btn" data-path="${escapeHtml(e.path)}" title="Copy Path">${ICON_COPY}</button>
    </span>
  </div>`;
}

function renderRetentionControl(show) {
  const val = show.retention || 'archive';
  const readyCount = show.ready_to_delete_count || 0;
  const readyBadge = readyCount > 0
    ? `<span class="badge ready" title="Watched and set to delete-after-watching, not protected by an override">✅ ${readyCount} of ${show.entries.length} ready to move</span>`
    : '';
  const key = escapeHtml(show.show_key);
  const disabledAttr = SERVER_MODE ? '' : 'disabled';
  // Restart show works offline in travel mode (localStorage-backed, see
  // onRestartShow's TRAVEL_MODE branch); the person-specific ⭐🙈 controls
  // are removed entirely in travel (SPEC 7.5) - shared portable copy, no
  // per-person watchlists.
  const restartAttr = (SERVER_MODE || TRAVEL_MODE) ? '' : 'disabled';
  if (show.is_streaming) {
    const lbl = show.streaming_provider_label || (show.streaming_provider === 'netflix' ? 'Netflix' : 'Prime Video');
    const lapsed = show.streaming_lapsed
      ? `<span class="badge streaming-lapsed" title="The companion tool's last check no longer found this on ${lbl}">⚠ no longer on ${lbl}</span>`
      : (show.streaming_arriving ? `<span class="badge streaming" title="Announced, not dropped yet">arriving ${escapeHtml(show.streaming_arriving)}</span>` : '');
    return `<div class="retention-row" onclick="event.stopPropagation()">
      <span class="badge streaming">↗ Following on ${lbl}</span>
      ${lapsed}
      <button class="star-btn" data-key="${key}" data-tmdb="${show.tmdb_id}" data-type="${show.type === 'Movie' ? 'movie' : 'tv'}" onclick="onStreamingRelease(this)" ${SERVER_MODE ? '' : 'disabled'} title="Stop following - remove it from the library. Streaming licences come and go; re-follow from Lighthouse any time.">✕ Release</button>
      ${TRAVEL_MODE ? '' : `<button class="star-btn${show.top10_partner ? ' active' : ''}" data-key="${key}" data-person="partner" onclick="onTop10Toggle(this)" ${disabledAttr} title="${escapeHtml(PEOPLE.partner.name)}'s Watch List">⭐ ${escapeHtml(PEOPLE.partner.initial)}</button>
      <button class="star-btn${show.top10_you ? ' active' : ''}" data-key="${key}" data-person="you" onclick="onTop10Toggle(this)" ${disabledAttr} title="${escapeHtml(PEOPLE.you.name)}'s Watch List">⭐ ${escapeHtml(PEOPLE.you.initial)}</button>`}
      ${show.entries.some(e => e.watched) ? `<button class="star-btn" data-key="${key}" onclick="onRestartShow(this)" ${restartAttr} title="Mark every episode as not watched">🔄 Restart show</button>` : ''}
    </div>`;
  }
  return `<div class="retention-row" onclick="event.stopPropagation()">
    <span class="retention-label">📌 Retention:</span>
    <select class="retention-select" data-key="${key}" onchange="onRetentionChange(this)" ${(SERVER_MODE || TRAVEL_MODE) ? '' : 'disabled title="Start the local server to change this"'}>
      <option value="archive" ${val === 'archive' ? 'selected' : ''}>Keep forever</option>
      <option value="delete_after_watch" ${val === 'delete_after_watch' ? 'selected' : ''}>Delete after watching</option>
    </select>
    ${readyBadge}
    ${TRAVEL_MODE ? '' : `<button class="star-btn${show.top10_partner ? ' active' : ''}" data-key="${key}" data-person="partner" onclick="onTop10Toggle(this)" ${disabledAttr} title="${escapeHtml(PEOPLE.partner.name)}'s Watch List">⭐ ${escapeHtml(PEOPLE.partner.initial)}</button>
    <button class="star-btn${show.top10_you ? ' active' : ''}" data-key="${key}" data-person="you" onclick="onTop10Toggle(this)" ${disabledAttr} title="${escapeHtml(PEOPLE.you.name)}'s Watch List">⭐ ${escapeHtml(PEOPLE.you.initial)}</button>
    <button class="star-btn${show.no_interest_partner ? ' active' : ''}" data-key="${key}" data-person="partner" onclick="onNoInterestToggle(this)" ${disabledAttr} title="${escapeHtml(PEOPLE.partner.name)} isn't interested in this - independent of their star, doesn't affect ${escapeHtml(PEOPLE.you.name)}'s view">🙈 ${escapeHtml(PEOPLE.partner.initial)}</button>
    <button class="star-btn${show.no_interest_you ? ' active' : ''}" data-key="${key}" data-person="you" onclick="onNoInterestToggle(this)" ${disabledAttr} title="${escapeHtml(PEOPLE.you.name)} isn't interested in this - independent of their star, doesn't affect ${escapeHtml(PEOPLE.partner.name)}'s view">🙈 ${escapeHtml(PEOPLE.you.initial)}</button>`}
    ${show.entries.some(e => e.watched) ? `<button class="star-btn" data-key="${key}" onclick="onRestartShow(this)" ${restartAttr} title="Mark every episode/file as not watched, to track your place on a rewatch">🔄 Restart show</button>` : ''}
    ${(TRAVEL_UI_ENABLED && SERVER_MODE && !TRAVEL_MODE) ? `<button class="star-btn${show.entries.every(e => e.travel_selected) ? ' active' : ''}" data-key="${key}" onclick="onTravelSelectShow(this)" title="Select/deselect all episodes for travel">🧳 Select whole show</button>` : ''}
  </div>`;
}

window.toggleMissingDetail = function(btn) {
  const block = btn.closest('.missing-block');
  block.querySelector('.chev').classList.toggle('open');
  block.querySelector('.missing-detail').classList.toggle('open');
};

function renderMissingEpisodes(show) {
  const missing = show.missing_episodes || [];
  const missingSeasons = show.missing_seasons || [];
  if (missing.length || missingSeasons.length) {
    // A season with zero digital episodes isn't always a real gap - see
    // pipeline/enrich_tv_coverage.py's physical/Amazon cross-reference.
    // Split so the headline count/color reflects genuine gaps only;
    // covered-elsewhere seasons still show, just visually distinct
    // (brown chip, never red) instead of silently vanishing.
    const trulyMissingSeasons = missingSeasons.filter(m => !isSeasonCoveredElsewhere(m));
    const elsewhereSeasons = missingSeasons.filter(isSeasonCoveredElsewhere);
    const seasonChips = trulyMissingSeasons.map(m =>
      `<span class="missing-chip season-chip" title="${m.aired_episode_count} aired episode(s) since ${escapeHtml(m.first_air_date)}, not yet in your library">Season ${m.season} missing</span>`
    ).join('');
    const elsewhereChips = elsewhereSeasons.map(m =>
      `<span class="missing-chip on-disc" title="Not digital, but owned ${coveredElsewhereLabel(m)}">Season ${m.season} — ${coveredElsewhereLabel(m)}</span>`
    ).join('');
    const shown = missing.slice(0, 20);
    const epChips = shown.map(m =>
      `<span class="missing-chip" title="${escapeHtml(m.name)} (aired ${m.air_date})">S${String(m.season).padStart(2,'0')}E${String(m.episode).padStart(2,'0')}</span>`
    ).join('');
    const more = missing.length > 20 ? ` <span class="missing-chip">+${missing.length - 20} more</span>` : '';

    const parts = [];
    if (trulyMissingSeasons.length) parts.push(`${trulyMissingSeasons.length} whole season${trulyMissingSeasons.length === 1 ? '' : 's'}`);
    if (missing.length) parts.push(`${missing.length} episode${missing.length === 1 ? '' : 's'}`);
    let summary = parts.length ? parts.join(' + ') + ' missing' : 'Nothing genuinely missing';
    if (elsewhereSeasons.length) summary += ` (+${elsewhereSeasons.length} season${elsewhereSeasons.length === 1 ? '' : 's'} owned elsewhere)`;

    return `<div class="missing-block">
      <div class="missing-toggle" onclick="event.stopPropagation(); toggleMissingDetail(this)">
        <span class="chev">&#9656;</span>
        <span class="label">${summary}</span>
      </div>
      <div class="missing-detail">
        <div class="missing-chips">${seasonChips}${elsewhereChips}${epChips}${more}</div>
      </div>
    </div>`;
  }
  if (show.type === 'TV' && show.tracking_start_date) {
    // Cooking/reality/documentary/talk/news shows are no longer a hard
    // exclusion (2026-08-21) - each gets tracked for real gaps from its
    // own start date forward, with everything aired before that date
    // permanently exempt (no backlog). Reaching this line means nothing
    // has come up missing SINCE that date yet - worth saying explicitly,
    // since "nothing genuinely missing" alone would look identical to a
    // show that's always been fully tracked, when this one might just not
    // have had a chance to fall behind yet.
    return `<div class="physical-note">Tracked for missing episodes since ${escapeHtml(show.tracking_start_date)} (cooking/reality/documentary/talk/news - nothing missing since then). Anything aired before that date is never flagged.</div>`;
  }
  if (show.type === 'TV' && show.completeness_skip_reason === 'delete_after_watch') {
    return `<div class="physical-note">Not tracked to completion (set to Delete after watching).</div>`;
  }
  if (show.type === 'TV' && show.completeness_skip_reason === 'queue_only_minor') {
    return `<div class="physical-note">Not enough collected yet to track completeness - only sitting in the transient download queue.</div>`;
  }
  if (show.type === 'TV' && show.completeness_skip_reason === 'not_collected_to_completion') {
    return `<div class="physical-note">Not tracked to completion - what's owned is whatever sounded interesting or was available, not meant to be a full collection.</div>`;
  }
  return '';
}

function renderGenreBadges(show) {
  if (show.runtime_mismatch) {
    return `<span class="badge warn" title="TMDB match found but file runtime doesn't line up - not trusting its genre data">⚠️ Unknown</span>`;
  }
  const genres = show.tmdb_genres || [show.genre];
  const shown = genres.slice(0, 2);
  const rest = genres.length > 2 ? ` +${genres.length - 2}` : '';
  return `<span class="badge genre" title="${escapeHtml(genres.join(', '))}">${escapeHtml(shown.join(', '))}${rest}</span>`;
}

function renderExternalLinks(show) {
  // Every link here goes as directly as we honestly can to the real page:
  // - IMDB: a real imdb_id comes free off the TMDB detail call we already
  //   make (movie: native field; TV: append_to_response=external_ids) - a
  //   direct https://www.imdb.com/title/{id}/ link when we have it, only
  //   falling back to IMDB's own search when we don't (unmatched title).
  // - epguides (TV): matched against their published show index
  //   (pipeline/enrich_epguides.py, cached like TMDB matching) - a direct
  //   https://epguides.com/{slug}/ link when confidently matched, else
  //   falls back to a Google site: search (what epguides' own search box
  //   itself uses under the hood).
  // - Rotten Tomatoes: no free ID exists anywhere for this (TMDB doesn't
  //   have one, RT has no public search API) - RT's own on-site search
  //   results page is the closest we can get to "the actual review" without
  //   scraping/guessing a URL slug.
  // - Trailer: a real official YouTube trailer key comes free off the same
  //   TMDB detail call (append_to_response=videos) - links straight to the
  //   video when we have one, only falling back to a YouTube search when
  //   TMDB genuinely doesn't have one on file.
  // Never a guessed/fabricated URL - every fallback is a real search page.
  const siteSearch = (site) => `https://www.google.com/search?q=site:${site}+${encodeURIComponent('"' + show.title + '"')}`;
  const imdbLink = show.tmdb_imdb_id
    ? `https://www.imdb.com/title/${show.tmdb_imdb_id}/`
    : `https://www.imdb.com/find/?q=${encodeURIComponent(show.title)}`;
  const trailerLink = show.tmdb_trailer_key
    ? `https://www.youtube.com/watch?v=${show.tmdb_trailer_key}`
    : `https://www.youtube.com/results?search_query=${encodeURIComponent(show.title + ' trailer')}`;
  const links = [];
  if (show.type === 'TV') {
    const epguidesLink = show.epguides_url || siteSearch('epguides.com');
    links.push(`<a class="open-link" target="_blank" rel="noopener" href="${epguidesLink}">📺 epguides</a>`);
    if (show.tmdb_matched) {
      links.push(`<a class="open-link" target="_blank" rel="noopener" href="${imdbLink}">🎞️ IMDB</a>`);
    }
    if (show.tmdb_matched && show.tmdb_id) {
      links.push(`<a class="open-link" target="_blank" rel="noopener" href="https://www.themoviedb.org/tv/${show.tmdb_id}">🎬 TMDB</a>`);
    }
  } else if (show.type === 'Movie') {
    links.push(`<a class="open-link" target="_blank" rel="noopener" href="https://www.rottentomatoes.com/search?search=${encodeURIComponent(show.title)}">🍅 Rotten Tomatoes</a>`);
    links.push(`<a class="open-link" target="_blank" rel="noopener" href="${imdbLink}">🎞️ IMDB</a>`);
    if (show.tmdb_matched && show.tmdb_id) {
      links.push(`<a class="open-link" target="_blank" rel="noopener" href="https://www.themoviedb.org/movie/${show.tmdb_id}">🎬 TMDB</a>`);
    }
  }
  links.push(`<a class="open-link" target="_blank" rel="noopener" href="${trailerLink}">▶️ Trailer</a>`);
  return links.join('');
}

function renderDetail(show, skipPoster) {
  if (show.runtime_mismatch) {
    // Usually a wrong match - but a real extended/alternate cut (a
    // director's cut, a shorter broadcast edit) legitimately runs a
    // different length than TMDB's one cached runtime number too, and
    // looks identical to a wrong match from this check's point of view.
    // "Confirm" lets the user say which one this actually is, once,
    // rather than it silently staying flagged forever with no way to fix
    // it themselves (real gap closed 2026-08-21 - see /api/confirm-runtime).
    // data-key + this.dataset, not the key embedded straight into the
    // onclick string - a title with an apostrophe (plenty of real ones in
    // this library) would otherwise break out of the attribute's own
    // quoting, same class of bug already fixed elsewhere in this file.
    const confirmBtn = SERVER_MODE
      ? `<button class="header-btn" type="button" data-key="${escapeHtml(show.show_key)}" onclick="onConfirmRuntime(this)" style="margin-top:8px;">✅ This is the right match - just a different cut/edit</button>`
      : '';
    return `<div class="detail"><div class="overview">⚠️ Unknown - a TMDB match was found, but the file's real runtime (~${show.file_runtime_minutes} min) doesn't match what TMDB reports (${show.tmdb_runtime} min) for that title. Probably the wrong match, so details aren't shown.${confirmBtn}</div></div>`;
  }
  if (show.tmdb_matched) {
    const poster = (show.tmdb_poster && !skipPoster)
      ? `<img class="poster" src="${tmdbImageUrl('poster', show.tmdb_poster)}" alt="" loading="lazy" decoding="async">`
      : '';
    const overview = show.tmdb_overview
      ? escapeHtml(show.tmdb_overview)
      : 'No synopsis available.';
    const rating = show.tmdb_vote
      ? `<div class="rating">⭐ ${show.tmdb_vote.toFixed(1)}/10</div>`
      : '';
    const runtimeText = show.type === 'Movie' && show.tmdb_runtime
      ? formatRuntime(show.tmdb_runtime)
      : show.type === 'TV' && show.tmdb_episode_run_time
      ? `~${formatRuntime(show.tmdb_episode_run_time)}/episode`
      : '';
    const runtime = runtimeText ? `<div class="rating">⏱️ ${runtimeText}</div>` : '';
    return `<div class="detail">${poster}<div class="overview">${overview}</div>${rating}${runtime}</div>`;
  }
  if (show.wiki_matched) {
    // Fallback for titles TMDB never matched at all - a real find, but a
    // lower-confidence source, so it's visually labeled as such rather
    // than presented like a verified TMDB match (this app's established
    // "never present unverified as verified" habit). Server mode serves
    // the locally-downloaded image via /wiki-image/; travel mode gets its
    // own local copy (see build_travel_plan.py) since that export is a
    // real folder, not a single file, so the bloat concern below doesn't
    // apply to it. The plain static single-file snapshot (server_mode and
    // travel_mode both false) still skips the image rather than embedding
    // it - these ~130 fallback images would meaningfully bloat every copy
    // of that one self-contained HTML file.
    const imgFile = show.wiki_image_path ? show.wiki_image_path.replace('wiki_images/', '') : null;
    const poster = (imgFile && (SERVER_MODE || TRAVEL_MODE) && !skipPoster)
      ? `<img class="poster" src="${wikiImageUrl(imgFile)}" alt="" loading="lazy" decoding="async">`
      : '';
    const extract = show.wiki_extract ? escapeHtml(show.wiki_extract) : 'No summary available.';
    // wiki_source defaults to "Wikipedia" for every real match this
    // pipeline itself finds, but a cache entry can be hand-written for a
    // specific non-Wikipedia source article when Wikipedia genuinely has
    // nothing (see pipeline/enrich_wikipedia.py's apply_cached_fields()) -
    // the label follows whatever source is actually true, not a hardcoded
    // guess.
    const sourceName = show.wiki_source || 'Wikipedia';
    const sourceLink = show.wiki_page_url
      ? `<a class="open-link" target="_blank" rel="noopener" href="${show.wiki_page_url}">via ${escapeHtml(sourceName)} ↗</a>`
      : `via ${escapeHtml(sourceName)}`;
    return `<div class="detail">${poster}<div class="overview">${extract}<div style="margin-top:6px; font-size:calc(11px * var(--tv-scale)); color:var(--text-dim);">${sourceLink} - not a TMDB match, take with a grain of salt</div></div></div>`;
  }
  if (show.physical_manual_cover_url || show.physical_collection) {
    // Physical-collection disc TMDB has no entry for at all (see
    // pipeline/enrich_physical.py's manual_no_tmdb_match) - a real cover
    // found by hand, not box art, plus whatever note was recorded about it.
    const poster = (show.physical_manual_cover_url && !skipPoster)
      ? `<img class="poster" src="${show.physical_manual_cover_url}" alt="" loading="lazy" decoding="async">`
      : '';
    const note = show.tmdb_overview ? escapeHtml(show.tmdb_overview) : 'No TMDB match exists for this disc.';
    return `<div class="detail">${poster}<div class="overview">${note}<div style="margin-top:6px; font-size:calc(11px * var(--tv-scale)); color:var(--text-dim);">Not on TMDB - part of the physical collection, identified by hand</div></div></div>`;
  }
  return '';
}

function renderCastCrew(show) {
  if (!show.tmdb_matched) return '';
  const directorLine = show.tmdb_director
    ? `<div class="crew-line"><span class="crew-label">Director</span>${escapeHtml(show.tmdb_director)}</div>`
    : (show.tmdb_creators && show.tmdb_creators.length)
    ? `<div class="crew-line"><span class="crew-label">Created by</span>${escapeHtml(show.tmdb_creators.join(', '))}</div>`
    : '';
  const cast = show.tmdb_cast || [];
  if (!directorLine && !cast.length) return '';
  const castHtml = cast.length ? `<div class="cast-row">${cast.map(c => {
    const photo = c.profile_path
      ? `<img class="cast-photo" src="${tmdbImageUrl('cast', c.profile_path)}" alt="" loading="lazy" decoding="async" onerror="onTmdbImageError(this)">`
      : `<div class="cast-photo cast-photo-placeholder">${escapeHtml((c.name || '?').charAt(0))}</div>`;
    // c.id (TMDB person id) is missing for cast entries cached before this
    // feature existed - card stays plain (no click) until the next refresh
    // backfills it, rather than opening a popup with nothing useful in it.
    const clickable = c.id ? ` clickable" onclick="openActorPopup(${c.id})` : '';
    return `<div class="cast-card${clickable}" title="${escapeHtml(c.character || '')}">${photo}<div class="cast-name">${escapeHtml(c.name)}</div>${c.character ? `<div class="cast-character">${escapeHtml(c.character)}</div>` : ''}</div>`;
  }).join('')}</div>` : '';
  return `<div class="cast-crew">${directorLine}${castHtml}</div>`;
}

function renderRecommendations(show) {
  const recs = show.tmdb_recommendations || [];
  if (!recs.length) return '';
  const cards = recs.map(r => {
    const owned = shows.find(s => s.tmdb_id === r.tmdb_id && s.type === r.type);
    const poster = r.poster_path
      ? `<img class="rec-poster" src="${tmdbImageUrl('rec_poster', r.poster_path)}" alt="" loading="lazy" decoding="async" onerror="onTmdbImageError(this)">`
      : `<div class="rec-poster"></div>`;
    // data-* attributes, not a native title="" tooltip - the hover-preview
    // below replaces that entirely, and having both would show two
    // tooltips stacked on top of each other. Escaped here same as any
    // other HTML output; showRecHoverTip() escapes AGAIN when it reads
    // these back before touching innerHTML, since dataset access decodes
    // entities back to raw text - one or the other alone would either
    // break real titles with an apostrophe/ampersand or reopen the exact
    // HTML-injection risk already fixed elsewhere in this file.
    const hoverAttrs = `data-title="${escapeHtml(r.title)}" data-overview="${escapeHtml(r.overview || '')}" data-rating="${r.vote_average != null ? r.vote_average.toFixed(1) : ''}" onmouseenter="showRecHoverTip(event, this)" onmousemove="moveRecHoverTip(event)" onmouseleave="hideRecHoverTip()"`;
    if (owned) {
      const ownedIdx = shows.indexOf(owned);
      return `<div class="rec-card" ${hoverAttrs} onclick="modalOpenFrom(${ownedIdx})">${poster}<div class="rec-title">${escapeHtml(r.title)}</div><div class="rec-owned">&#10003; In your library</div></div>`;
    }
    const tmdbUrl = `https://www.themoviedb.org/${r.type === 'Movie' ? 'movie' : 'tv'}/${r.tmdb_id}`;
    return `<a class="rec-card" ${hoverAttrs} href="${tmdbUrl}" target="_blank" rel="noopener">${poster}<div class="rec-title">${escapeHtml(r.title)}</div></a>`;
  }).join('');
  return `<div class="recommendations"><div class="recommendations-label">More like this</div><div class="rec-row">${cards}</div></div>`;
}

window.showRecHoverTip = function(event, el) {
  const tip = document.getElementById('rec-hover-tip');
  const title = el.dataset.title || '';
  const rating = el.dataset.rating;
  const overview = el.dataset.overview || '';
  tip.innerHTML = `
    <div class="rec-hover-tip-title">${escapeHtml(title)}</div>
    ${rating ? `<div class="rec-hover-tip-rating">⭐ ${escapeHtml(rating)}/10</div>` : ''}
    <div class="rec-hover-tip-overview">${overview ? escapeHtml(overview) : '<span style="opacity:0.6;">No synopsis available.</span>'}</div>
  `;
  tip.classList.add('visible');
  // Anchored to the recommendations row itself (2026-08-22), not the
  // cursor and not a raw viewport-left pin either - the latter caused the
  // tip to land on top of other posters in the same row whenever its
  // height crossed into the row's own vertical span (real, confirmed bug:
  // a "Quantum Leap" tooltip covering the "Doctor Who"/"Andromeda" cards
  // next to it). Sits just below the row, left-aligned to it; flips above
  // if there's not enough room below - it can never cover the row it's
  // describing either way.
  const margin = 16;
  const row = el.closest('.rec-row') || el;
  const rowRect = row.getBoundingClientRect();
  const tipRect = tip.getBoundingClientRect();
  let left = Math.max(margin, Math.min(rowRect.left, window.innerWidth - tipRect.width - margin));
  let top = rowRect.bottom + 10;
  if (top + tipRect.height > window.innerHeight - margin) {
    top = rowRect.top - tipRect.height - 10;
  }
  top = Math.max(margin, top);
  tip.style.left = left + 'px';
  tip.style.top = top + 'px';
};

window.moveRecHoverTip = function(event) {
  // Position is anchored to the row, not the cursor - see
  // showRecHoverTip(). Kept as a no-op so the onmousemove wiring already
  // on each card doesn't need to change.
};

window.hideRecHoverTip = function() {
  document.getElementById('rec-hover-tip').classList.remove('visible');
};

function renderStatusBadges(show, skipReady) {
  // skipReady: the modal header shows this badge and renderRetentionControl's
  // own readyBadge right below it at the same time - pure duplication there
  // (grid cards and the collapsed list-view row have no retention-row
  // visible, so they still need it). Passing true from the modal isn't just
  // tidier - it also means a watched-toggle only has one copy of this count
  // to keep fresh, not two.
  let html = '';
  // Deleted-after-watch, currently owns nothing (pipeline/
  // track_purged_shows.py) - missing_seasons here is every aired season,
  // by design, not a real gap to fill, so the generic "N seasons missing"
  // badge below would read as a to-do list rather than what it actually
  // is: deliberate, ongoing tracking so the next-episode badge keeps
  // working without keeping files around.
  if (show.purged_tracked) {
    return `<span class="badge on-disc" title="You've watched this before and it was deleted per its retention policy - the app keeps checking for new episodes/seasons">🗑 Deleted after watching - tracking for new episodes</span>`;
  }
  if (show.wasted_bytes > 0) {
    html += `<span class="badge warn" title="Extra duplicate copies found">${formatBytes(show.wasted_bytes)} extra copies</span>`;
  }
  const epCount = (show.missing_episodes || []).length;
  const missingSeasons = show.missing_seasons || [];
  const seasonCount = missingSeasons.filter(m => !isSeasonCoveredElsewhere(m)).length;
  const elsewhereCount = missingSeasons.length - seasonCount;
  if (epCount || seasonCount) {
    const parts = [];
    if (seasonCount) parts.push(`${seasonCount} season${seasonCount === 1 ? '' : 's'}`);
    if (epCount) parts.push(`${epCount} ep${epCount === 1 ? '' : 's'}`);
    html += `<span class="badge missing" title="Aired content not found anywhere in your library - digital, physical, or Amazon">${parts.join(' + ')} missing</span>`;
  }
  if (elsewhereCount > 0) {
    html += `<span class="badge on-disc" title="Not digital, but owned some other way (physical disc and/or Amazon)">${elsewhereCount} season${elsewhereCount === 1 ? '' : 's'} owned elsewhere</span>`;
  }
  if (!skipReady && show.ready_to_delete_count > 0) {
    html += `<span class="badge ready" title="Watched and set to delete-after-watching, not protected by an override">✅ ${show.ready_to_delete_count} ready to move</span>`;
  }
  return html;
}

function renderCategoryBadges(show) {
  // Archive-folder classification (Series/Movies/etc) - a different concept
  // from genre. The old pipeline-stage text badge ("In Your Library" etc,
  // formerly rendered here too) was dropped in favor of leading with genre
  // (renderGenreBadges) on cards; workflow status is still fully
  // discoverable via the color-coded root-dots (title= tooltip on hover)
  // and the "All Statuses" filter, just no longer a card-level text badge.
  if (show.archive_categories && show.archive_categories.length) {
    return show.archive_categories.map(c => `<span class="badge category">${escapeHtml(c)}</span>`).join('');
  }
  return '';
}

function isEpisodeSortDesc() {
  return localStorage.getItem('episodeSortDesc') === '1';
}

// Per-show "show extras" state - deliberately NOT persisted (unlike the
// episode-sort preference): extras stay hidden by default every time a
// show is opened, which is the whole point of hiding them, and resets
// cleanly on a page reload rather than needing to be un-toggled everywhere.
window._extrasVisible = window._extrasVisible || {};
// Same non-DOM-backed reasoning as above, for which list-view cards are
// expanded - see toggleCard() and renderCard() below.
window._expandedCards = window._expandedCards || new Set();

function getDisplayEntries(show, idx) {
  // show.entries is server-sorted ascending (season/episode). Extras
  // (bonus/behind-the-scenes content merged in by consolidate.py's
  // prefix_merge(), flagged is_extra - never real episodes) are filtered
  // out unless explicitly revealed for this show; sort-order reversal
  // never mutates the original array (other code, e.g. extraPathsSet/
  // Continue Watching, assumes ascending order).
  const showExtras = !!window._extrasVisible[idx];
  const filtered = showExtras ? show.entries : show.entries.filter(e => !e.is_extra);
  return isEpisodeSortDesc() ? filtered.slice().reverse() : filtered;
}

function renderEpisodeSortToggle(show, idx) {
  if (show.type !== 'TV' || show.entries.length <= 1) return '';
  const desc = isEpisodeSortDesc();
  return `<button type="button" class="star-btn" onclick="event.stopPropagation(); toggleEpisodeSort(${idx})" title="Change episode order">${desc ? '↓ Newest first' : '↑ Oldest first'}</button>`;
}

function renderExtrasToggle(show, idx) {
  const extraCount = show.entries.filter(e => e.is_extra).length;
  if (extraCount === 0) return '';
  const checked = !!window._extrasVisible[idx];
  return `<label class="watched-check" style="margin-left:8px;" title="Bonus/behind-the-scenes content merged in from this show's extras - never real episodes" onclick="event.stopPropagation()">
    <input type="checkbox" ${checked ? 'checked' : ''} onchange="toggleShowExtras(${idx}, this.checked)">
    <span class="check-label">Show extras (${extraCount})</span>
  </label>`;
}

function refreshEntriesBlock(idx) {
  // Shared by both the episode-sort and show-extras toggles: a surgical
  // re-render of just this show's entries, so an already-expanded
  // list-view card doesn't collapse (a full applyFilters() redraw would
  // rebuild every card from scratch and lose the .expanded state, which
  // isn't tracked anywhere except the live DOM class).
  const show = shows[idx];
  if (document.getElementById('modal-overlay').classList.contains('open')) {
    // Surgical update of the already-open modal's own .entries block, not
    // a full openModal(idx) rebuild - that used to destroy and recreate
    // this scrollable div itself on every toggle (collapse a season, mark
    // one watched), snapping the scroll position back to the top. Same
    // "don't lose live state" reasoning as the list-view card branch
    // below, just scoped to scrollTop instead of a CSS class.
    const modalEntriesDiv = document.querySelector('#modal-box .entries');
    if (modalEntriesDiv) {
      const scrollTop = modalEntriesDiv.scrollTop;
      const hasHero = !!show.tmdb_backdrop && !show.runtime_mismatch;
      modalEntriesDiv.innerHTML = `
        ${renderRetentionControl(show)}
        ${renderDetail(show, hasHero)}
        ${renderFranchiseTimeline(show, idx)}
        ${renderCastCrew(show)}
        ${renderRecommendations(show)}
        ${renderMissingEpisodes(show)}
        ${renderEpisodeSortToggle(show, idx)}
        ${renderExtrasToggle(show, idx)}
        ${renderEntriesList(show, idx)}
      `;
      modalEntriesDiv.scrollTop = scrollTop;
    }
    return;
  }
  const card = document.querySelector(`.card[data-idx="${idx}"]`);
  if (!card) return;
  const entriesDiv = card.querySelector('.entries');
  if (entriesDiv) {
    entriesDiv.innerHTML = `${renderRetentionControl(show)}${renderDetail(show)}${renderCastCrew(show)}${renderRecommendations(show)}${renderMissingEpisodes(show)}${renderEpisodeSortToggle(show, idx)}${renderExtrasToggle(show, idx)}${renderEntriesList(show, idx)}`;
  }
}

window.toggleEpisodeSort = function(idx) {
  localStorage.setItem('episodeSortDesc', isEpisodeSortDesc() ? '0' : '1');
  refreshEntriesBlock(idx);
};

window.toggleShowExtras = function(idx, checked) {
  window._extrasVisible[idx] = checked;
  refreshEntriesBlock(idx);
};

function renderCard(show, idx) {
  const rootsDots = statusDotHtml(show);
  const countLabel = show.type === 'TV'
    ? `${show.episode_count} ep${show.episode_count===1?'':'s'}${show.version_count!==show.episode_count ? ' / '+show.version_count+' folders' : ''}`
    : (show.version_count > 1 ? `${show.version_count} copies` : '');
  const yearLabel = show.years && show.years.length
    ? (show.years.length > 1 ? `${show.years[0]}–${show.years[show.years.length-1]}` : show.years[0])
    : '';

  const singleEntry = show.entries.length === 1 ? show.entries[0] : null;
  const expandedClass = window._expandedCards.has(idx) ? ' expanded' : '';

  return `<div class="card${expandedClass}" data-idx="${idx}">
    <div class="card-row" onclick="toggleCard(${idx})">
      <span class="chevron">&#9656;</span>
      ${DEV === 'phone' && show.tmdb_poster ? `<img class="card-thumb" src="${tmdbImageUrl('poster', show.tmdb_poster)}" alt="" loading="lazy" decoding="async" onerror="onTmdbImageError(this)">` : ''}
      <span class="title">${escapeHtml(show.title)}<span class="years">${yearLabel}</span></span>
      <span class="badge type-${show.type}">${show.type}</span>
      ${renderGenreBadges(show)}
      ${renderStatusBadges(show)}
      ${renderCategoryBadges(show)}
      <span class="meta">${countLabel}</span>
      <span class="roots">${rootsDots}</span>
      ${singleEntry ? `<span class="entry-actions" onclick="event.stopPropagation()">
        ${singleEntry.file_url ? (SERVER_MODE
          ? `<button class="icon-btn icon-btn-play" data-path="${escapeHtml(singleEntry.path)}" onclick="event.stopPropagation();playOnServer(this)" title="Play on the TV — controllable from any phone/tablet on the network">${ICON_PLAY}</button>`
          : `<a class="icon-btn icon-btn-play" href="${singleEntry.file_url}" title="Play">${ICON_PLAY}</a>`) : ''}
        ${singleEntry.url ? `<a class="icon-btn" href="${singleEntry.url}" data-path="${escapeHtml(singleEntry.path)}" onclick="return openLocation(event, this)" title="Location">${ICON_FOLDER}</a>` : ''}
        ${singleEntry.amazon_link ? `<a class="icon-btn icon-btn-play" href="${singleEntry.amazon_link}" data-show-key="${escapeHtml(show.show_key)}" target="_blank" rel="noopener" onclick="return openStreamingLink(event, this)" title="Not a local file - opens Amazon Prime Video to watch this (you'll need to be signed in)">${ICON_PLAY}</a>` : ''}
        ${singleEntry.streaming_link ? `<a class="icon-btn icon-btn-play" href="${escapeHtml(singleEntry.streaming_link)}" data-show-key="${escapeHtml(show.show_key)}" target="_blank" rel="noopener" onclick="return openStreamingLink(event, this)" title="Not a local file - opens ${show.streaming_provider_label || 'the provider'} to watch this">${ICON_PLAY}</a>` : ''}
      </span>` : ''}
    </div>
    <div class="entries">${renderRetentionControl(show)}${renderDetail(show)}${renderMissingEpisodes(show)}${renderEpisodeSortToggle(show, idx)}${renderExtrasToggle(show, idx)}${renderEntriesList(show, idx)}</div>
  </div>`;
}

function renderGridCard(show, idx, progressLabel, extraBadgeHtml) {
  let poster;
  if (show.tmdb_poster && !show.runtime_mismatch) {
    poster = `<img class="poster-img" src="${tmdbImageUrl('poster', show.tmdb_poster)}" alt="" loading="lazy" decoding="async" onerror="onTmdbImageError(this)">`;
  } else if (show.wiki_image_path && (SERVER_MODE || TRAVEL_MODE)) {
    const imgFile = show.wiki_image_path.replace('wiki_images/', '');
    poster = `<img class="poster-img" src="${wikiImageUrl(imgFile)}" alt="" loading="lazy" decoding="async">`;
  } else if (show.physical_manual_cover_url) {
    // No TMDB match at all (see pipeline/enrich_physical.py) - a real but
    // non-TMDB cover, not box art, for the couple of discs TMDB has never
    // heard of.
    poster = `<img class="poster-img" src="${show.physical_manual_cover_url}" alt="" loading="lazy" decoding="async">`;
  } else {
    poster = `<div class="poster-placeholder">${escapeHtml(show.title)}</div>`;
  }
  // Skip the year suffix when the title already carries one - a
  // same-title show (three "Doctor Who" series) is disambiguated to
  // "Doctor Who (2005)" server-side, and appending again gave
  // "Doctor Who (2005) (2005)".
  const yearLabel = (show.years && show.years.length && !TITLE_HAS_YEAR_RE.test(show.title)) ? show.years[0] : '';
  const progressBadge = progressLabel ? `<span class="badge progress">${escapeHtml(progressLabel)}</span>` : '';
  const rootsDots = statusDotHtml(show);
  return `<div class="poster-card" data-idx="${idx}" onclick="modalOpenFrom(${idx})">
    ${poster}
    <div class="poster-meta">
      <div class="poster-title">${escapeHtml(show.title)}${yearLabel ? ` (${yearLabel})` : ''}</div>
      <div class="poster-badges">
        ${progressBadge}
        <span class="badge type-${show.type}">${show.type}</span>
        ${renderGenreBadges(show)}
        ${renderStatusBadges(show)}
        ${renderCategoryBadges(show)}
        ${extraBadgeHtml || ''}
      </div>
      <div class="roots" style="margin-top:4px;">${rootsDots}</div>
    </div>
  </div>`;
}

function nextEpisodeBadge(show) {
  const ne = show.tmdb_next_episode;
  if (!ne || !ne.air_date) return '';
  const today = new Date().toISOString().slice(0, 10);
  if (ne.air_date < today) return '';
  const label = ne.air_date === today ? 'Airs today' : `Airs ${formatDate(ne.air_date)}`;
  const epLabel = (ne.season != null && ne.episode != null) ? `S${String(ne.season).padStart(2,'0')}E${String(ne.episode).padStart(2,'0')}` : '';
  const titleAttr = [epLabel, ne.name].filter(Boolean).join(' - ');
  return `<span class="next-episode-badge" title="${escapeHtml(titleAttr)}">&#128197; ${label}</span>`;
}

// Weekly TV mode's own card badge (2026-08-26) - same shape as
// nextEpisodeBadge() above, but spells out the day of week too (e.g.
// "Airs Tuesday, Sep 2") per the user's explicit ask for this specific
// view, since a weekly-schedule mindset cares which day more than
// anywhere else in the app does. Deliberately a separate function rather
// than an option bolted onto nextEpisodeBadge() - every other call site
// of that one (Continue Watching, What's New, Recently Watched) keeps its
// existing short format unchanged.
function weeklyTvBadge(show) {
  const ne = show.tmdb_next_episode;
  if (!ne || !ne.air_date) return '';
  const today = new Date().toISOString().slice(0, 10);
  if (ne.air_date < today) return '';
  const label = ne.air_date === today
    ? 'Airs today'
    : `Airs ${new Date(ne.air_date + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })}`;
  const epLabel = (ne.season != null && ne.episode != null) ? `S${String(ne.season).padStart(2,'0')}E${String(ne.episode).padStart(2,'0')}` : '';
  const titleAttr = [epLabel, ne.name].filter(Boolean).join(' - ');
  return `<span class="next-episode-badge" title="${escapeHtml(titleAttr)}">&#128197; ${label}</span>`;
}

function computeContinueWatching() {
  const candidates = [];   // real, currently-owned watched entries to sort by
  const resuming = [];     // owned unwatched episodes exist, but the watched
                            // ones are already gone (see below) - sorted by
                            // Recently Watched's own order instead
  const tracked = [];
  for (const s of shows) {
    if (s.type !== 'TV') continue;
    // Deleted-after-watch shows (pipeline/track_purged_shows.py) own no
    // files right now by design, so they can never satisfy the "some
    // watched, some not" check below - but the user still wants to know
    // when the next episode airs without keeping every file around.
    // Surfaced separately, soonest-airing first, appended after real
    // in-progress shows.
    if (s.purged_tracked) {
      const ne = s.tmdb_next_episode;
      if (ne && ne.air_date) tracked.push({ show: s, watchedCount: 0, total: 0, purgedTracked: true, nextAirDate: ne.air_date });
      continue;
    }
    const entries = s.entries;
    if (!entries.length || entries.every(e => e.watched)) continue;  // nothing left, or empty
    const watchedCount = entries.filter(e => e.watched).length;
    if (watchedCount > 0) {
      const lastAt = entries.reduce((max, e) => (e.watched_at && e.watched_at > (max || '')) ? e.watched_at : max, null);
      candidates.push({ show: s, watchedCount, total: entries.length, lastAt: lastAt || '' });
      continue;
    }
    // watchedCount is 0 here but there's still at least one UNWATCHED entry
    // owned (the length/every check above already ruled out "nothing owned
    // at all" and "fully watched"). The one rule this whole function runs
    // on: recently watched + still has something unwatched = Continue
    // Watching, regardless of WHY the currently-owned entries show zero
    // watched right now - a Delete After Watching show purges each episode
    // the moment it's watched (e.ready_to_delete - per-entry, not gated on
    // the whole show being done), so its owned entries can show zero
    // watched even mid-series purely because the watched ones are already
    // gone from the folder, never evidence the show wasn't started. Recently
    // Watched survives that purge (state_store.promote_recently_watched
    // fires on every watched=true, independent of what happens to the file
    // afterward), so it's used here as the one durable "have I actually
    // started this" signal - deliberately NOT gated to delete_after_watch
    // shows only (an earlier version of this fix was, wrongly - the same
    // "recently watched, something left, so it belongs here" logic applies
    // no matter the retention policy; 2026-09-18).
    const rwIndex = RECENTLY_WATCHED_KEYS.indexOf(s.show_key);
    if (rwIndex !== -1) {
      resuming.push({ show: s, watchedCount, total: entries.length, rwIndex, viaRecentlyWatched: true });
    }
  }
  candidates.sort((a, b) => b.lastAt.localeCompare(a.lastAt));
  resuming.sort((a, b) => a.rwIndex - b.rwIndex);  // RECENTLY_WATCHED_KEYS is already MRU-first
  tracked.sort((a, b) => a.nextAirDate.localeCompare(b.nextAirDate));
  return candidates.concat(resuming).concat(tracked).slice(0, 12);
}

function computeWhatsNew() {
  const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const candidates = shows.filter(s =>
    s.latest_added && new Date(s.latest_added).getTime() >= cutoff
    // ...and not already fully watched. "Added in the last 7 days" alone
    // kept a title here even after you'd watched every episode of it, so a
    // freshly-added show you binged still sat in What's New for days
    // (2026-09-04, folded in from the travel build). A partly-watched
    // title stays - it's still genuinely new-to-you. renderFrontPanels()
    // recomputes this on every watched toggle, so it leaves the moment the
    // last unwatched entry is checked.
    && !(s.entries.length > 0 && s.entries.every(e => e.watched))
  );
  candidates.sort((a, b) => (b.latest_added || '').localeCompare(a.latest_added || ''));
  return candidates.slice(0, 12);
}

function computeRecentlyWatched() {
  // RECENTLY_WATCHED_KEYS is a real persisted list (data\user_state.json,
  // MRU order via promote_recently_watched() in app\state_store.py) -
  // unlike Continue Watching/What's New, which are fully recomputed from
  // live state every render. A key with no matching show anymore (deleted
  // from the library since) is skipped here defensively, though
  // prune_state() already removes those on the next real refresh.
  // In travel mode the baked list is a stale home snapshot - read the
  // localStorage MRU the trip actually built instead (SPEC 7.3).
  const src = TRAVEL_MODE ? travelRecentGet() : RECENTLY_WATCHED_KEYS;
  return src.map(k => shows.find(s => s.show_key === k)).filter(Boolean);
}

function computeFranchiseWhatsNext() {
  // Walks the Franchise manifest (FRANCHISE_TITLES - 53 released + 3 announced-
  // but-unreleased) in whichever order is currently selected (chronological
  // or release, same toggle the
  // per-title timeline badge uses) and returns the first title that's
  // both owned and not fully watched - "what's next" in the strict sense
  // of the next thing in sequence you haven't finished, not a recency- or
  // popularity-based guess. A show with some but not all episodes watched
  // still counts as "not yet done" (all_watched, computed server-side in
  // render_page(), covers both cases in one check).
  const chrono = isFranchiseChronoOrder();
  const sorted = FRANCHISE_TITLES.slice().sort((a, b) => (chrono ? a.chrono - b.chrono : a.release - b.release));
  for (const m of sorted) {
    const s = shows.find(sh => sh.tmdb_id === m.id && sh.type === m.type);
    if (s && !s.all_watched) return { show: s, entry: m };
  }
  return null;
}

function renderFranchiseWhatsNext() {
  const next = computeFranchiseWhatsNext();
  const body = document.getElementById('franchise-whats-next-body');
  if (!next) {
    body.innerHTML = `<p style="color:var(--text-dim); font-size:13px;">You're caught up on every ${escapeHtml(FRANCHISE.name)} title in your library!</p>`;
    return;
  }
  const { show, entry } = next;
  const idx = shows.indexOf(show);
  const chrono = isFranchiseChronoOrder();
  const pos = chrono ? entry.chrono : entry.release;
  const poster = show.tmdb_poster
    ? `<img class="fwn-poster" src="${tmdbImageUrl('poster', show.tmdb_poster)}" alt="" loading="lazy" decoding="async" onerror="onTmdbImageError(this)">`
    : `<div class="fwn-poster"></div>`;
  const yearLabel = show.years && show.years.length ? ` &middot; ${show.years[0]}` : '';
  body.innerHTML = `<div class="franchise-whats-next-card" onclick="openModal(${idx})">
    ${poster}
    <div>
      <div class="fwn-label">${chrono ? 'Chronological' : 'Release'} #${pos} of ${FRANCHISE_TITLES.length}</div>
      <div class="fwn-title">${escapeHtml(show.title)}</div>
      <div class="fwn-sub">${escapeHtml(show.type)}${yearLabel}</div>
    </div>
  </div>`;
}

function renderFrontPanels() {
  const franchiseMode = document.documentElement.classList.contains('franchise-mode');
  document.querySelector('.front-panels').style.display = franchiseMode ? 'none' : 'grid';
  document.getElementById('franchise-whats-next-panel').style.display = franchiseMode ? 'block' : 'none';
  if (franchiseMode) {
    renderFranchiseWhatsNext();
    return;
  }

  const cw = computeContinueWatching();
  document.getElementById('continue-watching-body').innerHTML = cw.length
    ? `<div class="grid grid-compact">${cw.map(c => renderGridCard(c.show, shows.indexOf(c.show), c.purgedTracked ? 'deleted after watching' : c.viaRecentlyWatched ? (c.show.retention === 'delete_after_watch' ? `${c.total} left · earlier eps watched & deleted` : `${c.total} left · resuming`) : `${c.watchedCount}/${c.total} eps`, nextEpisodeBadge(c.show))).join('')}</div>`
    : `<p style="color:var(--text-dim); font-size:13px;">Nothing in progress right now.</p>`;
  panelRefreshState.cw = cw.map(c => c.show.show_key);

  const wn = computeWhatsNew();
  document.getElementById('whats-new-body').innerHTML = wn.length
    ? `<div class="grid grid-compact">${wn.map(s => renderGridCard(s, shows.indexOf(s), null, nextEpisodeBadge(s))).join('')}</div>`
    : `<p style="color:var(--text-dim); font-size:13px;">Nothing new in the last 7 days.</p>`;
  panelRefreshState.wn = wn.map(s => s.show_key);

  const rw = computeRecentlyWatched();
  document.getElementById('recently-watched-body').innerHTML = rw.length
    ? `<div class="grid grid-compact">${rw.map(s => renderGridCard(s, shows.indexOf(s), null, nextEpisodeBadge(s))).join('')}</div>`
    : `<p style="color:var(--text-dim); font-size:13px;">Nothing marked watched yet.</p>`;
  document.getElementById('clear-recently-watched-btn').style.display = rw.length ? '' : 'none';
  panelRefreshState.rw = rw.map(s => s.show_key);
}

// Keyed to the 3 front panels - populated fresh every renderFrontPanels()
// call, read by onPanelRefresh() so each panel's own small refresh button
// only re-checks the handful of shows actually visible in IT, not the
// whole library.
const panelRefreshState = { cw: [], wn: [], rw: [] };

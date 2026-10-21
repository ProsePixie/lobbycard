// --- Fix a title ---

window.onAdminFixLibrarySearch = function(q) {
  const resultsEl = document.getElementById('admin-fix-library-results');
  q = q.trim().toLowerCase();
  if (q.length < 2) { resultsEl.innerHTML = ''; return; }
  const matches = shows.map((s, idx) => ({ s, idx })).filter(({s}) => s.title.toLowerCase().includes(q)).slice(0, 12);
  resultsEl.innerHTML = matches.map(({s, idx}) => `
    <div class="admin-pick-card" onclick="onAdminFixSelectShow(${idx})">
      ${adminPosterThumb(s.tmdb_poster, s.title)}
      <div class="admin-pick-title">${escapeHtml(s.title)}${s.years && s.years.length ? ` (${s.years[0]})` : ''}</div>
    </div>`).join('');
};

window.onAdminFixSelectShow = function(idx) {
  adminState.fixShowIdx = idx;
  document.getElementById('admin-fix-library-results').innerHTML = '';
  document.getElementById('admin-fix-search').value = '';
  renderAdminFixSelected();
};

function renderAdminFixSelected() {
  const s = shows[adminState.fixShowIdx];
  const el = document.getElementById('admin-fix-selected');
  if (!s) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <div class="admin-current">
      ${s.tmdb_poster ? `<img src="${tmdbImageUrl('poster', s.tmdb_poster)}" alt="" onerror="onTmdbImageError(this)">` : `<div class="admin-pick-placeholder" style="width:70px; aspect-ratio:2/3;">${escapeHtml(s.title)}</div>`}
      <div>
        <div style="font-weight:700;">${escapeHtml(s.title)}</div>
        <div class="admin-note">${s.type}${s.tmdb_id ? ` &middot; TMDB id ${s.tmdb_id}` : ' &middot; not matched'}${s.has_override ? ' &middot; has a saved correction' : ''}</div>
      </div>
    </div>
    <div class="admin-row admin-field">
      <div class="admin-field">
        <label class="admin-label">Re-search TMDB for the correct match</label>
        <input class="admin-input" type="text" id="admin-fix-tmdb-query" value="${escapeHtml(s.title)}">
      </div>
      <button class="header-btn" type="button" onclick="onAdminFixTmdbSearch()">Search</button>
    </div>
    <div id="admin-fix-tmdb-results" class="admin-pick-grid"></div>
    <div class="admin-field" style="margin-top:10px;">
      <button class="header-btn" type="button" onclick="onAdminFixFallbackSearch()">🔎 Try Epguides / IMDB / Wikipedia instead</button>
      <div class="admin-note">TMDB isn't always the authority - this cross-references epguides/TVmaze/IMDb and Wikipedia/Wikidata to find a match TMDB's own search can't.</div>
    </div>
    <div id="admin-fix-fallback-results" class="admin-pick-grid"></div>
    <div class="admin-field" style="margin-top:10px;">
      <button class="header-btn" type="button" onclick="onAdminFixPosterSearch()" ${s.tmdb_id ? '' : 'disabled title="Needs a TMDB match first"'}>🖼️ Pick a different poster for this match</button>
    </div>
    <div id="admin-fix-poster-results" class="admin-pick-grid"></div>
    <div class="admin-field" style="margin-top:10px;">
      <label class="admin-label">Or upload your own image</label>
      <input type="file" id="admin-fix-upload-input" accept="image/*" onchange="onAdminFixUploadPoster(this)">
      <div class="admin-note">Saved locally, no TMDB match needed - used as the poster. If it's wide/large enough (1280px+ wide, landscape-ish), it's also used as the popup's banner image.</div>
    </div>
    ${s.type === 'TV' ? `
    <div class="admin-field" style="margin-top:10px;">
      <label class="admin-label">Season numbering offset</label>
      <div class="admin-row">
        <input class="admin-input" type="number" min="1" step="1" id="admin-season-offset" style="max-width:100px;" value="${s.season_offset || ''}" placeholder="e.g. 9">
        <button class="header-btn" type="button" onclick="onAdminSeasonOffsetSave()">Save</button>
        ${s.season_offset ? `<button class="header-btn" type="button" onclick="onAdminSeasonOffsetClear()">Clear</button>` : ''}
      </div>
      <div class="admin-note">For a rebooted show whose TMDB entry restarts at Season 1 while your files keep the
      original's season numbering (e.g. files call it S10 but TMDB's reboot entry is Season 1) - set how many
      seasons to subtract so episode titles/air dates match up. Only affects this title; re-scan the library
      afterward to apply it to files already scanned.</div>
    </div>` : ''}
    <div class="admin-field" style="margin-top:10px;">
      <label class="admin-label">Different productions, same title</label>
      <div class="admin-row">
        ${s.split_by_year ? `<span class="admin-note" style="margin:0;">Split by year.</span> <button class="header-btn" type="button" onclick="onAdminSplitByYearClear()">Undo</button>` : `<button class="header-btn" type="button" onclick="onAdminSplitByYearSave()">These are different, unrelated productions</button>`}
      </div>
    </div>
    <div class="admin-field" style="margin-top:10px;">
      <label class="admin-label">Category / genre</label>
      <div class="admin-row">
        <select class="admin-select" id="admin-genre-override" style="max-width:220px;">
          <option value="">${escapeHtml((s.tmdb_genres && s.tmdb_genres[0]) || s.genre || 'Uncategorized')} (current)</option>
          ${adminKnownGenres().map(g => `<option value="${escapeHtml(g)}">${escapeHtml(g)}</option>`).join('')}
        </select>
        <input class="admin-input" type="text" id="admin-genre-override-custom" placeholder="or type a new one..." style="max-width:160px;">
        <button class="header-btn" type="button" onclick="onAdminGenreOverrideSave()">Save</button>
        ${s.genre_overridden ? `<button class="header-btn" type="button" onclick="onAdminGenreOverrideClear()">Clear</button>` : ''}
      </div>
      <div class="admin-note">Forces this show's genre badge/filter, regardless of what TMDB has (or doesn't) -
      pick one already used elsewhere in your library, or type your own.</div>
    </div>
    ${s.type === 'TV' ? `
    <div class="admin-field" style="margin-top:10px;">
      <label class="admin-label">Missing Episodes</label>
      <div class="admin-note">${
        s.completeness_skip_reason === 'manually_marked_complete'
          ? 'Manually marked complete - never flagged as missing, regardless of what TMDB reports.'
          : ((s.missing_episodes && s.missing_episodes.length) || (s.missing_seasons && s.missing_seasons.length))
            ? `Currently flagged: ${(s.missing_episodes || []).length} episode(s), ${(s.missing_seasons || []).length} whole season(s).`
            : 'Nothing currently flagged as missing.'
      }</div>
      <button class="header-btn" type="button" onclick="onAdminMissingCompleteToggle()">${
        s.completeness_skip_reason === 'manually_marked_complete' ? 'Clear override (go back to automatic tracking)' : 'Mark complete (never flag as missing)'
      }</button>
    </div>` : ''}
    ${s.type === 'TV' && s.retention === 'delete_after_watch' ? `
    <div class="admin-field" style="margin-top:10px;">
      <label class="admin-label">Missing-episode backlog (Delete After Watching)</label>
      <div class="admin-note" id="admin-purged-backlog-note">${adminPurgedBacklogNote(s)}</div>
      <button class="header-btn" type="button" onclick="onAdminPurgedBacklogToggle()">${
        adminPurgedBacklogOn(s) ? 'Switch to new-episodes-only (default)' : 'Track the full historical backlog too'
      }</button>
    </div>` : ''}
    <div class="admin-field" style="margin-top:16px; padding-top:14px; border-top:1px solid var(--border);">
      <label class="admin-label" style="color:var(--btn-purge);">Remove Forever</label>
      <div class="admin-note">Moves every file for this title to Pending Deletion and stops tracking it
      entirely - not a blocklist, so a fresh copy later starts clean. Doesn't touch watched history, Watch List,
      or Not Interested.</div>
      <button class="header-btn" type="button" style="border-color:var(--btn-purge); color:var(--btn-purge);" onclick="onAdminRemoveForever()">Remove Forever</button>
    </div>
  `;
}

// Every genre already showing up somewhere in the library (from the same
// counts the genre filter dropdown uses), "Uncategorized" excluded - the
// whole point of this control is fixing away from that, not selecting it.
function adminKnownGenres() {
  return Object.keys(genreCounts).filter(g => g !== 'Uncategorized').sort();
}

window.onAdminGenreOverrideSave = async function() {
  const s = shows[adminState.fixShowIdx];
  const custom = document.getElementById('admin-genre-override-custom').value.trim();
  const picked = document.getElementById('admin-genre-override').value;
  const genre = custom || picked;
  if (!genre) { showToast('Pick a genre from the list, or type a new one.'); return; }
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'genre', value: genre });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to save the genre.'); return; }
  s.tmdb_genres = [genre];
  s.genre_overridden = true;
  s.has_override = true;
  showToast('Genre saved.');
  renderAdminFixSelected();
  applyFilters();
};

window.onAdminGenreOverrideClear = async function() {
  const s = shows[adminState.fixShowIdx];
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'genre', value: '' });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to clear the genre.'); return; }
  s.tmdb_genres = result.default_genres;
  s.genre_overridden = false;
  showToast('Genre override cleared - back to whatever TMDB provides.');
  renderAdminFixSelected();
  applyFilters();
};

// Missing-episode grace period (2026-09-13) - one app-wide setting, not
// tied to any selected show, so it's read once when the Admin panel opens
// (see openAdminPanel()) rather than per-show.
window.onAdminMissingGraceDaysChange = async function(value) {
  const result = await postJSON('/api/settings', { missing_episode_grace_days: parseInt(value, 10) });
  if (!result || !result.ok) { showToast('Failed to save.'); return; }
  showToast(`Grace period set to ${value} days - takes effect on the next refresh.`);
};

async function loadAdminMissingGraceDays() {
  const sel = document.getElementById('admin-missing-grace-days');
  if (!sel || !SERVER_MODE) return;
  try {
    const resp = await fetch('/api/settings', { headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const data = await resp.json();
    if (data && data.settings) sel.value = String(data.settings.missing_episode_grace_days || 7);
  } catch (e) {}
}

async function loadAdminImageCacheStatus() {
  const el = document.getElementById('admin-image-cache-status');
  if (!el || !SERVER_MODE) return;
  try {
    const resp = await fetch('/api/image-cache/status', { headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const data = await resp.json();
    if (data && data.ok) {
      const mb = (data.total_bytes / 1e6).toFixed(1);
      el.textContent = `${data.file_count} image${data.file_count === 1 ? '' : 's'} cached locally (${mb} MB).`;
    } else {
      el.textContent = 'Unable to read cache status.';
    }
  } catch (e) { el.textContent = 'Unable to read cache status.'; }
}

// Deliberately no automatic eviction anywhere in this app - a cached
// poster/backdrop/cast photo stays on disk forever until this explicit
// Admin action is used (2026-09-13, "every poster should be cached. Until
// I say delete in the Admin."). Nothing runs this on a schedule or as part
// of a refresh.
window.onAdminClearImageCache = async function() {
  if (!confirm('Delete every locally cached TMDB poster/backdrop/cast photo? They will simply re-download the next time each is viewed. Your own uploaded covers and the manual disc covers are not affected.')) return;
  const result = await postJSON('/api/image-cache/clear', {});
  if (!result || !result.ok) { showToast('Failed to clear image cache.'); return; }
  showToast(`Cleared ${result.cleared} cached image${result.cleared === 1 ? '' : 's'}.`);
  loadAdminImageCacheStatus();
};

// Manual "mark complete" override - highest-priority branch in
// enrich_tv_coverage.py's skip-completeness chain (see /api/override's
// 'missing_complete' field and that script's own comment).
window.onAdminMissingCompleteToggle = async function() {
  const s = shows[adminState.fixShowIdx];
  const nowComplete = s.completeness_skip_reason !== 'manually_marked_complete';
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'missing_complete', value: nowComplete });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to save.'); return; }
  showToast(nowComplete ? 'Marked complete - takes effect on the next refresh.' : 'Override cleared - takes effect on the next refresh.');
  // Real effect needs enrich_tv_coverage.py to actually re-run (a full
  // refresh, not the quick-scan) - reflect the pending choice immediately
  // in the UI so it doesn't look like nothing happened, without pretending
  // missing_episodes/missing_seasons were already recomputed.
  s.completeness_skip_reason = nowComplete ? 'manually_marked_complete' : null;
  renderAdminFixSelected();
};

// Per-show opt-IN to unbounded missing-episode backlog tracking for a
// Delete After Watching show (2026-09-17, correcting a wrong default -
// see /api/override's 'purged_full_backlog' field and pipeline/
// track_purged_shows.py's build_tv() for what this actually changes).
// s.purged_backlog_tracking_pref is a CLIENT-side tracked preference, not
// a field the pipeline writes back for every show - a not-yet-purged show
// has nothing on the record to read the real state from yet (state.
// purged_backlog_tracking still controls it either way, this is purely so
// the button's own label stays correct immediately after a click, on a
// show whose record may never get a tracking_start_date stamped at all
// until it's actually purged). Initialized once, lazily, from whatever the
// pipeline DID already stamp (a purged_tracked record with no
// tracking_start_date means it was already opted in).
function adminPurgedBacklogOn(s) {
  if (s.purged_backlog_tracking_pref === undefined) {
    s.purged_backlog_tracking_pref = !!(s.purged_tracked && !s.tracking_start_date);
  }
  return s.purged_backlog_tracking_pref;
}
function adminPurgedBacklogNote(s) {
  if (adminPurgedBacklogOn(s)) {
    return 'Full backlog tracking is ON for this show - every aired episode not currently owned is flagged missing, same as a normal kept-forever show.';
  }
  if (s.purged_tracked && s.tracking_start_date) {
    return `Bounded (default): only episodes airing after ${escapeHtml(s.tracking_start_date)} - when this show first went zero-owned - are ever flagged missing. Everything before that was already watched and deleted on purpose, not re-chased.`;
  }
  return 'Bounded (default). Once this show is fully purged, only new episodes airing from that point forward will ever get flagged missing - the backlog it had before then is treated as already watched and deleted on purpose, not re-chased.';
}
window.onAdminPurgedBacklogToggle = async function() {
  const s = shows[adminState.fixShowIdx];
  const nowOn = !adminPurgedBacklogOn(s);
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'purged_full_backlog', value: nowOn });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to save.'); return; }
  showToast(nowOn ? 'Full backlog tracking on - takes effect on the next refresh.' : 'Back to new-episodes-only - takes effect on the next refresh.');
  s.purged_backlog_tracking_pref = nowOn;
  renderAdminFixSelected();
};

// Remove Forever (2026-09-13) - moves every file for this title to
// Pending Deletion and clears its retention (see run_remove_forever()'s
// own comment in web_server.py for why clearing retention is what makes
// this "stop tracking," not a separate blocklist). Same background-job +
// poll shape as Purge, since a full series can be many GB.
let removeForeverPollTimer = null;
window.onAdminRemoveForever = async function() {
  const s = shows[adminState.fixShowIdx];
  if (!s) return;
  let fileCount = 0, totalBytes = 0;
  for (const e of s.entries || []) { fileCount++; totalBytes += (e.size_bytes || 0); }
  const ok = confirm(
    `Move ${fileCount} file(s) (${formatBytes(totalBytes)}) for "${s.title}" to Pending Deletion and stop tracking it?\n\n` +
    `Nothing is deleted outright - only moved for you to review. Not a blocklist: a fresh copy added later starts clean. ` +
    `Watched history, Watch List, and Not Interested are left untouched.`
  );
  if (!ok) return;
  showBootOverlay('Starting Remove Forever...');
  const result = await postJSON('/api/remove-forever', { show_key: s.show_key });
  if (!result || !result.ok) { hideBootOverlay(); showToast((result && result.message) || 'Could not start.'); return; }
  pollRemoveForeverStatus();
};

async function pollRemoveForeverStatus() {
  clearTimeout(removeForeverPollTimer);
  try {
    const resp = await fetch('/api/remove-forever/status', { headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const status = await resp.json();
    if (status.running) {
      setBootOverlayProgress(`Removing "${status.title || ''}"... (${status.moved_count} of ${status.total_count})`, status.moved_count, status.total_count);
      removeForeverPollTimer = setTimeout(pollRemoveForeverStatus, 1500);
    } else if (status.error) {
      hideBootOverlay();
      showErrorDialog('Remove Forever failed', status.error);
    } else {
      showToast(`Removed - ${status.moved_count} file(s) moved to Pending Deletion. Refreshing library...`);
      closeModal();
      pollRefreshStatus('sync');
    }
  } catch (err) {
    hideBootOverlay();
  }
}

window.onAdminSeasonOffsetSave = async function() {
  const s = shows[adminState.fixShowIdx];
  const raw = document.getElementById('admin-season-offset').value.trim();
  const offset = parseInt(raw, 10);
  if (!raw || isNaN(offset) || offset <= 0) { showToast('Enter a positive number of seasons to subtract.'); return; }
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'season_offset', value: offset });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to save the offset.'); return; }
  s.season_offset = offset;
  showToast('Season offset saved - refresh the library to apply it to already-scanned files.');
  renderAdminFixSelected();
};

window.onAdminSeasonOffsetClear = async function() {
  const s = shows[adminState.fixShowIdx];
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'season_offset', value: 0 });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to clear the offset.'); return; }
  delete s.season_offset;
  showToast('Season offset cleared.');
  renderAdminFixSelected();
};

window.onAdminSplitByYearSave = async function() {
  const s = shows[adminState.fixShowIdx];
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'split_by_year', value: true });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to save.'); return; }
  s.split_by_year = true;
  showToast('Saved - refresh the library to split them apart.');
  renderAdminFixSelected();
};

window.onAdminSplitByYearClear = async function() {
  const s = shows[adminState.fixShowIdx];
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'split_by_year', value: false });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to clear.'); return; }
  delete s.split_by_year;
  showToast('Cleared - refresh the library to merge them back.');
  renderAdminFixSelected();
};

window.onAdminFixTmdbSearch = async function() {
  const s = shows[adminState.fixShowIdx];
  const q = document.getElementById('admin-fix-tmdb-query').value.trim();
  const resultsEl = document.getElementById('admin-fix-tmdb-results');
  if (!q) return;
  resultsEl.innerHTML = '<div class="admin-note">Searching...</div>';
  const resp = await fetch(`/api/tmdb-search?q=${encodeURIComponent(q)}&type=${encodeURIComponent(s.type)}`);
  const data = await resp.json().catch(() => null);
  const results = (data && data.results) || [];
  if (!results.length) { resultsEl.innerHTML = '<div class="admin-note">No results.</div>'; return; }
  resultsEl.innerHTML = results.map(r => `
    <div class="admin-pick-card" onclick="onAdminFixApplyMatch(${r.tmdb_id})">
      ${adminPosterThumb(r.poster_path, r.title)}
      <div class="admin-pick-title">${escapeHtml(r.title)}${r.year ? ` (${r.year})` : ''}</div>
    </div>`).join('');
};

window.onAdminFixApplyMatch = async function(tmdbId) {
  const s = shows[adminState.fixShowIdx];
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'tmdb_id', value: tmdbId });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to save the correction.'); return; }
  Object.assign(s, result.fields);
  s.has_override = true;
  showToast('Match corrected.');
  renderAdminFixSelected();
  applyFilters();
};

window.onAdminFixFallbackSearch = async function() {
  const s = shows[adminState.fixShowIdx];
  const resultsEl = document.getElementById('admin-fix-fallback-results');
  resultsEl.innerHTML = '<div class="admin-note">Checking epguides/TVmaze/IMDb and Wikipedia - this can take a few seconds...</div>';
  const resp = await fetch(`/api/fallback-search?show_key=${encodeURIComponent(s.show_key)}`);
  const data = await resp.json().catch(() => null);
  const result = data && data.result;
  if (!result) { resultsEl.innerHTML = '<div class="admin-note">No match found via epguides or Wikipedia either.</div>'; return; }
  adminState.fallbackResult = result;
  const sourceLabel = result.source === 'epguides' ? 'epguides / TVmaze / IMDb' : 'Wikipedia / Wikidata';
  resultsEl.innerHTML = `
    <div class="admin-pick-card" onclick="onAdminFixApplyFallbackMatch()">
      ${adminPosterThumb(result.poster_path, result.title)}
      <div class="admin-pick-title">${escapeHtml(result.title)}${result.year ? ` (${result.year})` : ''}</div>
      <div class="admin-note">via ${sourceLabel}</div>
    </div>`;
};

window.onAdminFixApplyFallbackMatch = async function() {
  const s = shows[adminState.fixShowIdx];
  const r = adminState.fallbackResult;
  if (!r) return;
  const body = { show_key: s.show_key, field: 'tmdb_id', value: r.tmdb_id, media_type: r.media_type };
  if (r.media_type === 'tv_episode') { body.season = r.season; body.episode = r.episode; }
  const result = await postJSON('/api/override', body);
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to save the correction.'); return; }
  Object.assign(s, result.fields);
  s.has_override = true;
  showToast('Match corrected.');
  renderAdminFixSelected();
  applyFilters();
};

window.onAdminFixPosterSearch = async function() {
  const s = shows[adminState.fixShowIdx];
  const resultsEl = document.getElementById('admin-fix-poster-results');
  resultsEl.innerHTML = '<div class="admin-note">Loading posters...</div>';
  const resp = await fetch(`/api/tmdb-posters/${s.tmdb_id}?type=${encodeURIComponent(s.type)}`);
  const data = await resp.json().catch(() => null);
  const posters = (data && data.posters) || [];
  if (!posters.length) { resultsEl.innerHTML = '<div class="admin-note">No alternate posters found.</div>'; return; }
  resultsEl.innerHTML = posters.map(p => `
    <div class="admin-pick-card" onclick="onAdminFixApplyPoster('${p.file_path}')">
      ${adminPosterThumb(p.file_path, '')}
    </div>`).join('');
};

window.onAdminFixApplyPoster = async function(posterPath) {
  const s = shows[adminState.fixShowIdx];
  const result = await postJSON('/api/override', { show_key: s.show_key, field: 'poster', value: posterPath });
  if (!result || !result.ok) { showToast((result && result.message) || 'Failed to save the poster.'); return; }
  s.tmdb_poster = posterPath;
  s.has_override = true;
  showToast('Poster updated.');
  renderAdminFixSelected();
  applyFilters();
};

window.onAdminFixUploadPoster = async function(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  if (!SERVER_MODE) { showToast('Start the local server (start_server) to save changes'); input.value = ''; return; }
  const s = shows[adminState.fixShowIdx];
  const formData = new FormData();
  formData.append('show_key', s.show_key);
  formData.append('image', file);
  showToast('Uploading...');
  let data = null;
  try {
    const resp = await fetch('/api/override/upload-poster', {
      method: 'POST',
      headers: { 'X-Auth-Token': AUTH_TOKEN },
      body: formData,
    });
    data = await resp.json().catch(() => null);
  } catch (e) {
    showToast('Upload failed - could not reach the server.');
    input.value = '';
    return;
  }
  input.value = '';
  if (!data || !data.ok) { showToast((data && data.message) || 'Failed to save the image.'); return; }
  s.tmdb_poster = data.poster;
  if (data.backdrop) { s.tmdb_backdrop = data.backdrop; }
  s.has_override = true;
  showToast(data.backdrop ? 'Poster and banner updated.' : 'Poster updated (image was too small/narrow for a banner too).');
  renderAdminFixSelected();
  applyFilters();
};

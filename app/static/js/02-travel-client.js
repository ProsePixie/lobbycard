let RECENTLY_WATCHED_KEYS = @@recently_watched_json@@;
const RECENTLY_WATCHED_CAP = @@recently_watched_cap@@;
const TRAVEL_WATCHED_KEY = 'travelWatched';
// Travel-mode Recently Watched (2026-09-04, folded in from the travel
// build / SPEC 7.3). The baked RECENTLY_WATCHED_KEYS is a frozen home
// snapshot - in travel mode nothing on the trip ever updated it, so the
// panel just sat there stale. These keep a real MRU list in localStorage,
// same shape and cap as promote_recently_watched() in state_store.py
// (newest first, de-dupe-then-unshift, RECENTLY_WATCHED_CAP). Only
// consulted when TRAVEL_MODE; the home/server path is unchanged.
const TRAVEL_RECENT_KEY = 'travelRecentKeys';
function travelRecentGet() {
  try {
    const v = JSON.parse(localStorage.getItem(TRAVEL_RECENT_KEY) || '[]');
    return Array.isArray(v) ? v : [];
  } catch (e) { return []; }
}
function travelRecentAdd(key) {
  if (!key) return;
  const next = [key, ...travelRecentGet().filter(k => k !== key)].slice(0, RECENTLY_WATCHED_CAP);
  try { localStorage.setItem(TRAVEL_RECENT_KEY, JSON.stringify(next)); } catch (e) {}
}
function travelRecentClear() {
  try { localStorage.removeItem(TRAVEL_RECENT_KEY); } catch (e) {}
}
// Re-enabled 2026-08-17 at the user's request, after being parked
// 2026-08-16 (selection UI felt confusing without consistent per-season
// file naming) - the API endpoints and build_travel_plan.py were never
// touched, only this UI was hidden.
const TRAVEL_UI_ENABLED = @@travel_ui_enabled_json@@;

function travelUrl(relPath, scheme) {
  if (!relPath) return null;
  const dirHref = location.href.replace(/[^/]*$/, '');
  const base = dirHref.replace(/^file:/, scheme + ':');
  return base + relPath.split('/').map(encodeURIComponent).join('/');
}

function loadTravelWatched() {
  try { return JSON.parse(localStorage.getItem(TRAVEL_WATCHED_KEY) || '{}'); } catch (e) { return {}; }
}
function saveTravelWatched(obj) {
  localStorage.setItem(TRAVEL_WATCHED_KEY, JSON.stringify(obj));
}
// Parallel to travelWatched (2026-09-04, item 11) - {path: ISO timestamp}
// for when a travel watch happened. Kept as its own key rather than folded
// into travelWatched's {path: bool} shape, so nothing that already reads
// travelWatched needs to change. Only ever has entries for paths currently
// marked watched - a caller sets it alongside travelWatched[path]=true and
// deletes it alongside travelWatched[path]=false/removal.
const TRAVEL_WATCHED_AT_KEY = 'travelWatchedAt';
function loadTravelWatchedAt() {
  try { return JSON.parse(localStorage.getItem(TRAVEL_WATCHED_AT_KEY) || '{}'); } catch (e) { return {}; }
}
function saveTravelWatchedAt(obj) {
  localStorage.setItem(TRAVEL_WATCHED_AT_KEY, JSON.stringify(obj));
}

// Offline retention control (2026-09-04, plan item 15 / SPEC 7.2). The
// travel <select> is the SAME control as home (not a separate two-button
// design - the user's explicit call), just enabled instead of disabled.
// Both maps store only DELTAS from the home-baked value, same shape as
// promote_recently_watched()'s "overrides only" convention elsewhere in
// this file:
//   travelRetention:          { show_key: 'archive' | 'delete_after_watch' }
//   travelRetentionOverrides: { path: true }  (per-episode "keep this one")
// Never moves or deletes a file - matches the schema's decided "record
// intent only" (Travel_Changes_Schema.md §13.2); the actual move-to-
// Pending-Deletion mechanism on the travel machine is a later item.
const TRAVEL_RETENTION_KEY = 'travelRetention';
function loadTravelRetention() {
  try { return JSON.parse(localStorage.getItem(TRAVEL_RETENTION_KEY) || '{}'); } catch (e) { return {}; }
}
function saveTravelRetention(obj) {
  localStorage.setItem(TRAVEL_RETENTION_KEY, JSON.stringify(obj));
}
const TRAVEL_RETENTION_OVERRIDE_KEY = 'travelRetentionOverrides';
function loadTravelRetentionOverrides() {
  try { return JSON.parse(localStorage.getItem(TRAVEL_RETENTION_OVERRIDE_KEY) || '{}'); } catch (e) { return {}; }
}
function saveTravelRetentionOverrides(obj) {
  localStorage.setItem(TRAVEL_RETENTION_OVERRIDE_KEY, JSON.stringify(obj));
}

// Branding (plan item 17 / SPEC 7.6) - shareable-mode only editor for
// title/subtitle/hero image + the UI-zoom slider promised back in
// initTravelZoom()'s own comment ("a real slider lands with the travel
// Settings panel"). heroDataUrl is a localStorage data: URL for now, not
// a real file on the drive under assets/ the way the plan doc describes -
// that needs the bundle's own file-write mechanism (plan item 18, not
// built yet); this is a working stand-in that still round-trips through
// travel-changes.json.branding so a real assets/branding.json can replace
// it later without changing this shape.
const TRAVEL_BRANDING_KEY = 'travelBranding';
function loadTravelBranding() {
  try { return JSON.parse(localStorage.getItem(TRAVEL_BRANDING_KEY) || '{}'); } catch (e) { return {}; }
}
function saveTravelBranding(obj) {
  localStorage.setItem(TRAVEL_BRANDING_KEY, JSON.stringify(obj));
}
function applyTravelBranding() {
  if (!TRAVEL_MODE) return;
  const b = loadTravelBranding();
  const titleEl = document.querySelector('.app-title');
  const taglineEl = document.querySelector('.app-tagline');
  const heroEl = document.querySelector('.app-hero');
  if (titleEl) titleEl.textContent = b.title || APP_NAME;
  if (taglineEl) taglineEl.textContent = b.subtitle || APP_TAGLINE;
  if (heroEl) heroEl.style.backgroundImage = b.heroDataUrl ? `url('${b.heroDataUrl}')` : '';
}

// Path C server detection (plan item 19 / SPEC 2) - travel/travel_server.py
// is an OPTIONAL live-sync layer on top of everything items 15/16 already
// built, not a replacement: localStorage stays the record of truth this
// page reads from on every load (so a trip with no server running, the
// file opened straight from file://, is unaffected and unchanged), but
// when a probe to the same origin's /api/ping succeeds, watched/retention
// changes are ALSO best-effort mirrored live so a second device on the
// same network (travelServerSync's whole reason to exist) sees them
// without waiting for the end-of-trip export. Starts false; flips true
// only after a real successful probe. file:// pages never even attempt
// this (fetch to a file:// origin has nothing to probe).
let TRAVEL_SERVER_MODE = false;
if (TRAVEL_MODE && location.protocol !== 'file:') {
  fetch('/api/ping', { signal: AbortSignal.timeout(1500) })
    .then(r => r.ok ? r.json() : null)
    .then(data => { if (data && data.ok) TRAVEL_SERVER_MODE = true; })
    .catch(() => {});
}
function travelServerSync(path, body) {
  if (!TRAVEL_SERVER_MODE) return;
  fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).catch(() => {});  // best-effort only - localStorage already has it either way
}

if (TRAVEL_MODE) {
  const travelWatched = loadTravelWatched();
  const travelWatchedAt = loadTravelWatchedAt();
  const travelRetention = loadTravelRetention();
  const travelRetentionOverrides = loadTravelRetentionOverrides();
  for (const s of shows) {
    // Stash the baked-in value BEFORE any override is applied - this is
    // what onRetentionChange() diffs against to decide whether a pick is
    // a real delta worth persisting, and what exportTravelLog() reports
    // as home_choice.
    s._homeRetention = s.retention || 'archive';
    if (s.show_key in travelRetention) s.retention = travelRetention[s.show_key];
    for (const e of s.entries) {
      e.url = travelUrl(e.rel_path, 'openfolder');
      e.file_url = travelUrl(e.rel_path, 'openvlc');
      // keyed by the original home path (still stored in e.path even though
      // the actual copy lives elsewhere on this drive) so the exported log
      // can be reconciled directly against the home library, no id mapping needed
      if (e.path in travelWatched) {
        e.watched = !!travelWatched[e.path];
        if (e.watched && travelWatchedAt[e.path]) e.watched_at = travelWatchedAt[e.path];
      }
      if (e.path in travelRetentionOverrides) e.retention_override = !!travelRetentionOverrides[e.path];
    }
    const watchedCount = s.entries.filter(e => e.watched).length;
    s.all_watched = s.entries.length > 0 && watchedCount === s.entries.length;
    // Applied AFTER retention/overrides are overlaid, so ready_to_delete_count
    // / the ✅ badge / completeness-skip are correct on first render.
    recomputeShowReadiness(s);
  }
}

// travel-changes.json (schema 2, plan item 11 / Travel_Changes_Schema.md) -
// the single round-trip file, replacing the old travel_watched_log_*.json.
// Clean break (2026-09-04): only this is written now; the home importer
// still reads a legacy travel_watched_log_*.json if that's what it finds
// (schema §11). Rewritten in full every export - no partial-update state
// to get out of sync (§13.4).
window.exportTravelLog = async function() {
  const travelWatched = loadTravelWatched();
  const travelWatchedAt = loadTravelWatchedAt();
  const watchedArr = Object.keys(travelWatched).map(path => {
    const rec = { key: path, watched: !!travelWatched[path], source: 'checkbox' };
    if (travelWatchedAt[path]) rec.at = travelWatchedAt[path];
    return rec;
  });

  // Files added on this trip (entry.root === 'Travel'). watched here is
  // the file's own flag - the sole record of a road-added file's watched
  // state (§13.1); it has no home e.path yet for a watched[] row to key on.
  const additions = [];
  for (const s of shows) {
    for (const e of s.entries) {
      if (e.root !== 'Travel') continue;
      const relPath = e.rel_path || '';
      additions.push({
        tmdb_id: s.tmdb_id || null,
        type: s.type,
        season: (e.season != null ? e.season : null),
        episode: (e.episode != null ? e.episode : null),
        episode_title: e.episode_title || '',
        air_date: e.air_date || '',
        travel_rel_path: relPath,
        house_name: relPath.split('/').pop() || '',
        size_bytes: e.size_bytes || 0,
        added_at: s.latest_added || '',
        watched: !!e.watched,
      });
    }
  }

  // Pinned TV backdrops (SPEC 7.1) - localStorage['tvBackdrop:<show_key>'].
  const backdropOverrides = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (!k || !k.startsWith('tvBackdrop:')) continue;
    const s = shows.find(sh => sh.show_key === k.slice('tvBackdrop:'.length));
    if (!s) continue;
    backdropOverrides.push({
      tmdb_id: s.tmdb_id || null, type: s.type,
      file_path: localStorage.getItem(k), chosen_at: new Date().toISOString(),
    });
  }

  // Retention deltas (SPEC 7.2, plan item 15) - a show-level record for
  // anything with a show-level override AND/OR at least one per-episode
  // "keep this one" override, so a per-episode-only change (show never
  // touched at the top level) still gets exported.
  const travelRetentionMap = loadTravelRetention();
  const travelRetentionOverridesMap = loadTravelRetentionOverrides();
  const retentionShowKeys = new Set(Object.keys(travelRetentionMap));
  for (const s of shows) {
    if (s.entries.some(e => e.path in travelRetentionOverridesMap)) retentionShowKeys.add(s.show_key);
  }
  const retention = [...retentionShowKeys].map(showKey => {
    const s = shows.find(sh => sh.show_key === showKey);
    if (!s) return null;
    const episodeOverrides = s.entries
      .filter(e => e.path in travelRetentionOverridesMap)
      .map(e => ({ key: e.path, keep: !!travelRetentionOverridesMap[e.path] }));
    return {
      key: showKey, show_key: showKey, type: s.type,
      choice: travelRetentionMap[showKey] || s._homeRetention || s.retention || 'archive',
      home_choice: s._homeRetention || 'archive',
      at: new Date().toISOString(), committed: false,
      episode_overrides: episodeOverrides,
    };
  }).filter(Boolean);

  // Best-effort folder name for root_hint - the importer auto-detects the
  // drive regardless (§1), this is just a convenience hint.
  let rootHint = '';
  try { rootHint = decodeURIComponent(location.pathname.split('/').filter(Boolean).slice(-2, -1)[0] || ''); } catch (e) {}

  const payload = {
    schema: 2,
    generated_at: new Date().toISOString(),
    mode: TRAVEL_SHARE_MODE ? 'shareable' : 'personal',
    root_hint: rootHint,
    watched: watchedArr,
    additions: additions,
    retention: retention,
    deletions: [],          // discard-intent flow not built yet (schema §13.2 - record intent only, this stays empty until a move mechanism exists)
    backdrop_overrides: backdropOverrides,
    // Only ever set on a shareable copy (plan item 17) - a personal
    // export's branding is baked & locked, nothing to mirror back.
    branding: TRAVEL_SHARE_MODE ? loadTravelBranding() : null,
  };
  const json = JSON.stringify(payload, null, 2);
  const filename = 'travel-changes.json';
  const total = watchedArr.length + additions.length + backdropOverrides.length + retention.length;

  // showSaveFilePicker() (2026-08-23, at the user's request - "the Export
  // should be in the same location as the Travel drive, not default to
  // Desktop") opens a real native Save-As dialog, letting the choice
  // actually be the drive's travel-state\ folder - a plain <a download>
  // (the old approach) always goes straight to Chrome's own configured
  // downloads folder with no way to redirect it; no web page can dictate
  // an arbitrary filesystem save location, this is the one real exception
  // Chromium offers, via an explicit user-driven picker. Falls back to
  // the old download-link approach on anything that doesn't support it.
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: 'Travel changes', accept: { 'application/json': ['.json'] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(json);
      await writable.close();
      showToast(`Exported ${total} change(s) - save it into travel-state\\ on the drive.`);
      return;
    } catch (e) {
      if (e && e.name === 'AbortError') return;  // user cancelled the picker - not an error
      // fall through to the download-link fallback below
    }
  }
  const blob = new Blob([json], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  showToast(`Exported ${total} change(s) to your downloads folder - move it into travel-state\\ on the drive.`);
};

// Bring-it-home import (2026-09-05, plan item 13 follow-up) - one folder
// picker + one button, superseding the old per-file travel_watched_log_*/
// new_files_manifest.json upload pair now that exportTravelLog() writes
// everything to a single travel-changes.json. import_travel_changes.py's
// own --source already finds that file under <folder>\travel-state\, or
// falls back to the legacy pair at the folder root if that's all an older
// export left behind (schema §11) - one control covers both.
window.browseTravelImportSource = async function() {
  if (!SERVER_MODE) { showToast('Start the local server to use this'); return; }
  const btn = event.target;
  const originalText = btn.textContent;
  btn.textContent = 'Waiting for folder picker...';
  btn.disabled = true;
  try {
    const resp = await fetch('/api/browse-folder', { method: 'POST', headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const data = await resp.json().catch(() => null);
    if (data && data.ok && data.path) {
      document.getElementById('travel-import-source-input').value = data.path;
    }
  } catch (e) {
    showToast('Could not reach the server for the folder picker.');
  }
  btn.textContent = originalText;
  btn.disabled = false;
};

window.importTravelChanges = async function() {
  if (!SERVER_MODE) { showToast('Start the local server to import trip changes'); return; }
  const source = document.getElementById('travel-import-source-input').value.trim();
  const resultEl = document.getElementById('travel-import-result');
  if (!source) { resultEl.textContent = 'Browse to the travel drive/export folder first.'; return; }
  const btn = document.getElementById('travel-import-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Importing...';
  resultEl.textContent = 'Importing...';
  let data = null;
  try {
    const resp = await fetch('/api/import-travel-changes', {
      method: 'POST', headers: { 'X-Auth-Token': AUTH_TOKEN, 'Content-Type': 'application/json' },
      body: JSON.stringify({ source }),
    });
    data = await resp.json().catch(() => null);
  } catch (e) {
    resultEl.textContent = "Couldn't reach the server.";
    btn.disabled = false;
    btn.textContent = '📥 Import Trip Changes';
    return;
  }
  btn.disabled = false;
  btn.textContent = '📥 Import Trip Changes';
  if (!data || !data.ok) { resultEl.textContent = (data && data.message) || 'Import failed.'; return; }
  resultEl.textContent = data.message;
  showToast('Trip changes imported - refreshing...');
  syncShowsFromServer();
};

// --- "Scan for New Files" (2026-08-23) - the on-the-road half of the new-
// episodes-while-traveling feature. Only ever active when TRAVEL_MODE is
// true (gated in openTravelPanel()) since it reads the travel drive's own
// New Downloads folder via showDirectoryPicker() - the File System Access
// API this depends on never exposes a real absolute filesystem path to JS
// (a hard browser constraint, not a choice made here), so all this can do
// is a best-effort guess (filename parsing + an optional live TMDB search)
// good enough to browse/watch on the road. The REAL, TMDB-verified match
// happens back home in travel\import_new_travel_files.py, which re-derives
// everything from the manifest this writes plus the real New Downloads
// path the user gives it directly - this client-side guess is a hint, not
// a source of truth, same "TMDB is the sole source of truth" rule as
// everywhere else in this project.
const NEW_FILE_EXTENSIONS = ['.mkv', '.mp4', '.avi', '.ts', '.m2ts', '.wmv', '.mov', '.m4v', '.flv', '.webm', '.iso', '.mpg', '.mpeg', '.vob'];

// Small, deliberately-simplified JS port of pipeline\scan_media.py's
// parse_folder_name() - good enough for a best-effort on-the-road guess,
// not trying to replicate every edge case (anime fansub prefixes, edition
// descriptors, etc.) that the real Python version handles for the
// authoritative home-side pass.
function parseNewFileName(filename) {
  const stem = filename.replace(/\.[^.]+$/, '');
  const cleanTitle = (raw) => raw
    .replace(/[._]+/g, ' ')
    .replace(/\[[^\]]*\]|\([^)]*\)/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^[-\s]+|[-\s]+$/g, '');

  const seMatch = stem.match(/[Ss](\d{1,3})[EeXx](\d{1,3})/);
  if (seMatch) {
    const titleRegion = stem.slice(0, seMatch.index);
    return {
      type: 'TV',
      title: cleanTitle(titleRegion) || cleanTitle(stem),
      year: null,
      season: parseInt(seMatch[1], 10),
      episode: parseInt(seMatch[2], 10),
    };
  }

  const yearMatches = stem.match(/(19\d{2}|20\d{2})/g);
  if (yearMatches) {
    const year = yearMatches[yearMatches.length - 1];
    const idx = stem.lastIndexOf(year);
    return { type: 'Movie', title: cleanTitle(stem.slice(0, idx)) || cleanTitle(stem), year: parseInt(year, 10), season: null, episode: null };
  }

  return { type: null, title: cleanTitle(stem), year: null, season: null, episode: null };
}

async function liveTmdbSearch(mediaType, title, year) {
  const kind = mediaType === 'tv' ? 'tv' : 'movie';
  const yearParam = year ? (kind === 'tv' ? `&first_air_date_year=${year}` : `&year=${year}`) : '';
  const url = `https://api.themoviedb.org/3/search/${kind}?api_key=${encodeURIComponent(TRAVEL_TMDB_API_KEY)}&query=${encodeURIComponent(title)}${yearParam}`;
  const resp = await fetch(url);
  if (!resp.ok) return null;
  const data = await resp.json();
  return (data.results && data.results[0]) || null;
}

window.scanForNewTravelFiles = async function() {
  const resultEl = document.getElementById('travel-scan-result');
  if (!window.showDirectoryPicker) {
    resultEl.textContent = "This browser can't scan a folder directly - open this page in Chrome or Edge.";
    return;
  }
  let dirHandle;
  try {
    dirHandle = await window.showDirectoryPicker();
  } catch (e) {
    return;  // cancelled the picker - not an error
  }

  resultEl.textContent = 'Scanning...';
  const filenames = [];
  for await (const [name, handle] of dirHandle.values()) {
    if (handle.kind !== 'file') continue;
    const ext = name.slice(name.lastIndexOf('.')).toLowerCase();
    if (NEW_FILE_EXTENSIONS.includes(ext)) filenames.push(name);
  }
  if (!filenames.length) {
    resultEl.textContent = 'No video files found in that folder.';
    return;
  }

  // Re-scanning replaces the previous batch rather than piling up
  // duplicate cards - the manifest/grid always reflects the folder's
  // current real contents.
  for (let i = shows.length - 1; i >= 0; i--) {
    if (shows[i].is_new_travel_file) shows.splice(i, 1);
  }
  window._newTravelManifest = [];

  let matchedCount = 0;
  for (const filename of filenames) {
    const guess = parseNewFileName(filename);
    const mediaType = guess.type === 'TV' ? 'tv' : guess.type === 'Movie' ? 'movie' : null;
    let match = null;
    if (TRAVEL_TMDB_API_KEY && mediaType && navigator.onLine) {
      try { match = await liveTmdbSearch(mediaType, guess.title, guess.year); } catch (e) { /* offline/failed - falls back to a placeholder card below */ }
    }
    if (match) matchedCount++;

    const title = (match && (match.title || match.name)) || guess.title || filename;
    const yearStr = match ? (match.release_date || match.first_air_date || '').slice(0, 4) : '';
    const year = yearStr ? parseInt(yearStr, 10) : guess.year;

    shows.push({
      show_key: 'newfile:' + filename,
      title,
      type: guess.type === 'TV' ? 'TV' : 'Movie',
      genre: '🆕 New Download',
      years: year ? [year] : [],
      // root:'Travel' + rel_path (2026-09-05, plan item 11/13 follow-up) -
      // the ONLY thing exportTravelLog()'s additions[] loop actually keys
      // on ("if (e.root !== 'Travel') continue") - without these two
      // fields a New Download never made it into travel-changes.json at
      // all, despite the schema promising it would; the separate "Save
      // Import Manifest" button was the only way home before this.
      entries: [{
        path: 'newfile:' + filename, watched: false, size_bytes: 0,
        root: 'Travel', rel_path: 'New Downloads/' + filename,
        season: guess.season != null ? guess.season : null,
        episode: guess.episode != null ? guess.episode : null,
        episode_title: '', air_date: '',
      }],
      version_count: 1,
      episode_count: guess.type === 'TV' ? 1 : undefined,
      retention: 'archive',
      missing_episodes: [],
      missing_seasons: [],
      tmdb_status: '',
      wasted_bytes: 0,
      ready_to_delete_count: 0,
      runtime_mismatch: false,
      tmdb_matched: !!match,
      tmdb_id: match ? match.id : null,
      tmdb_poster: match ? match.poster_path : null,
      tmdb_overview: match ? match.overview : 'Found in New Downloads on this trip - not yet imported into the home library. Confirmed for real once you\'re home.',
      tmdb_vote: match ? match.vote_average : null,
      is_new_travel_file: true,
      latest_added: new Date().toISOString(),
    });
    const pushedShow = shows[shows.length - 1];
    // Plan item 12's remaining piece - physically file this into the
    // drive's Media\ mirror when a live-sync server is actually running
    // to do it (TRAVEL_SERVER_MODE); best-effort only, and only for a
    // confident match - an unmatched/guessed file stays in New Downloads
    // untouched either way, same as before this existed.
    if (match && TRAVEL_SERVER_MODE) {
      const isDocumentary = (match.genre_ids || []).includes(99);  // TMDB's Documentary genre id, same for movie/TV
      const entry = pushedShow.entries[0];
      fetch('/api/place-new-download', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename, type: mediaType, title,
          season: entry.season, episode: entry.episode,
          episode_title: entry.episode_title, year, is_documentary: isDocumentary,
        }),
      }).then(r => r.json()).then(result => {
        if (result && result.ok && result.rel_path) {
          entry.rel_path = result.rel_path;
          entry.url = travelUrl(entry.rel_path, 'openfolder');
          entry.file_url = travelUrl(entry.rel_path, 'openvlc');
        }
      }).catch(() => {});
    }
    window._newTravelManifest.push({
      filename,
      media_type: mediaType,
      tmdb_id: match ? match.id : null,
      guessed_title: guess.title,
      guessed_year: guess.year,
      guessed_season: guess.season,
      guessed_episode: guess.episode,
    });
  }

  applyFilters();
  document.getElementById('travel-save-manifest-btn').style.display = '';
  resultEl.textContent = `Found ${filenames.length} file(s), ${matchedCount} matched live to TMDB - added to your library grid as "🆕 New Download". Review them there, then Save Import Manifest and bring it home with this drive.`;
  showToast(`${filenames.length} new file(s) added to the grid`);
};

window.saveNewTravelFilesManifest = async function() {
  const manifest = window._newTravelManifest || [];
  if (!manifest.length) { showToast('Nothing to save - scan a folder first.'); return; }
  const json = JSON.stringify({ created_at: new Date().toISOString(), files: manifest }, null, 2);
  const filename = 'new_files_manifest.json';
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: 'New travel files manifest', accept: { 'application/json': ['.json'] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(json);
      await writable.close();
      showToast('Manifest saved - bring it home along with the New Downloads folder.');
      return;
    } catch (e) {
      if (e && e.name === 'AbortError') return;
    }
  }
  const blob = new Blob([json], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  showToast('Manifest downloaded - bring it home along with the New Downloads folder.');
};

// The server-baked genre_counts_json is used ONLY to know which genre
// values exist at all (the dropdown's option list, built once) - the
// actual (N) shown next to each one is computed fresh by
// updateGenreCounts() below, every time any filter changes. It used to
// just be printed straight from this frozen snapshot, which counted the
// WHOLE library regardless of the active source/type/date/status/search
// filters (even titles hidden by the default "Digital only" source
// filter) - confirmed real, not a guess: "Comedy (12)" would still say
// 12 even once other filters had already ruled out everything but 3 of
// them, 2026-08-21.
const genreCounts = @@genre_counts_json@@;
const genreSelect = document.getElementById('genre-filter');
Object.keys(genreCounts).sort().forEach(g => {
  const opt = document.createElement('option');
  opt.value = g;
  opt.textContent = g;
  genreSelect.appendChild(opt);
});

document.querySelectorAll('.legend [data-root]').forEach(el => {
  const friendly = ROOT_LABELS_FRIENDLY[el.getAttribute('data-root')];
  if (!friendly) return;
  el.lastChild.textContent = ' ' + friendly.label;
  el.title = friendly.hint;
});

document.querySelectorAll('#status-legend [data-status]').forEach(el => {
  const def = SHOW_STATUS_DEFS[el.getAttribute('data-status')];
  if (!def) return;
  el.appendChild(document.createTextNode(' ' + def.label));
  el.title = def.hint;
});

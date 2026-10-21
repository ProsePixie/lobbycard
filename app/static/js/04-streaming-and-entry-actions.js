// Netflix/Prime "Play" links - see /api/open-external-link's own comment in
// web_server.py for why this exists at all: this app normally runs inside
// its own dedicated, isolated Chrome profile, so a plain link click would
// just open a new window in THAT profile (no logins, no extensions) instead
// of the browser/profile the user actually uses. Routes through the server
// (same machine) to ask Windows itself to open the link in the real default
// browser instead. Falls back to a plain new-tab open (still inside this
// isolated profile, but at least it opens something) if the server call
// fails, and to normal <a href target="_blank"> behavior entirely when
// there's no server at all (a Travel Library offline copy).
window.openStreamingLink = function(ev, el) {
  if (!SERVER_MODE) {
    // The Travel App has no server to ask - open the same kind of window
    // (filling this screen) straight from the browser. Anything else
    // offline keeps the plain link.
    if (!TRAVEL_MODE) return true;
    ev.preventDefault();
    openPopupWindow(el.href, 'maximize');
    return false;
  }
  ev.preventDefault();
  const url = el.href;
  const showKey = el.dataset.showKey || null;
  const entryPath = el.dataset.entryPath || null;
  postJSON('/api/open-external-link', { url }).then(r => {
    if (!r || r.ok === false) {
      showToast((r && r.message) || 'Could not reach your default browser - opening here instead.');
      openPopupWindow(url, 'maximize');
      return;
    }
    // Poll for the popup closing, then jump back to where you were
    // (2026-09-21: "go straight to the episode if possible, if not the
    // show card") - only possible when we know which show/entry this
    // link came from and the server handed back a launch_id to track.
    if (r.launch_id && showKey) pollStreamingPopupClose(r.launch_id, showKey, entryPath);
  });
  return false;
};

// Every OTHER outbound link (TMDB, IMDb, Rotten Tomatoes, epguides, Wikipedia,
// the Google site search, the YouTube trailer link) takes the same route as
// the streaming links above - a real-Chrome popup the server sizes by host
// (Netflix/Amazon/YouTube maximized, lookups on the left half of the screen;
// see _EXTERNAL_FULLSCREEN_HOSTS in web_server.py). One delegated handler
// instead of an onclick on each of the ~15 anchors, so links added later get
// it for free. Only when this page is on the SAME machine as the server: a
// phone/tablet remote must open its own links on itself, not on the PC.
// Skips anything a more specific handler already took (defaultPrevented).
const _SAME_MACHINE_PAGE = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);

// The browser-side twin of that server logic, for places the home server's
// Win32 window placement doesn't exist: the Travel App (a static Travel
// Library.html on a drive, or travel_server.py - neither has the home
// server's routes) and as the fallback when the server call fails. Sized from
// the screen the page is ACTUALLY on (screen.avail* - the usable area, so
// the taskbar/dock is respected; availLeft/availTop = which monitor on a
// multi-screen desk), so any resolution, scaling or laptop panel works.
// Keep the two host lists in step with _EXTERNAL_FULLSCREEN_HOSTS in
// web_server.py: watch-here sites fill the screen, everything else is a
// lookup on the left half.
const POPUP_FULL_HOSTS = ['netflix.com', @@amazon_domain_json@@, 'amazon.com', 'youtube.com', 'youtu.be'];
const POPUP_HALF_MIN_WIDTH = 800;   // css px: below this a half-screen page stops being readable
function popupModeForUrl(url) {
  let host = '';
  try { host = new URL(url).hostname; } catch (e) {}
  return POPUP_FULL_HOSTS.some(h => host === h || host.endsWith('.' + h)) ? 'maximize' : 'half-left';
}
function popupFeatures(mode) {
  const s = window.screen;
  const W = s.availWidth, H = s.availHeight;
  const left = s.availLeft || 0, top = s.availTop || 0;
  // Phones/tablets and tiny windows: a sized popup is meaningless there, let
  // the browser do its normal thing (a tab).
  if (!W || !H || W < 700 || (window.matchMedia && matchMedia('(pointer: coarse)').matches)) return null;
  let w = W;
  if (mode === 'half-left') w = Math.min(W, Math.max(Math.floor(W / 2), POPUP_HALF_MIN_WIDTH));
  // width/height here are the page area; the browser adds its own frame and
  // clamps the whole window into the screen, so shave the side borders only.
  w = Math.max(320, w - 16);
  return `popup=yes,noopener,left=${left},top=${top},width=${w},height=${H}`;
}
window.openPopupWindow = function(url, mode) {
  const feat = popupFeatures(mode || popupModeForUrl(url));
  const w = feat ? window.open(url, '_blank', feat) : window.open(url, '_blank', 'noopener');
  return w;
};

document.addEventListener('click', function(ev) {
  if (ev.defaultPrevented || ev.button !== 0 || ev.ctrlKey || ev.shiftKey || ev.metaKey || ev.altKey) return;
  const a = ev.target.closest && ev.target.closest('a[href]');
  if (!a || a.target !== '_blank') return;
  let u;
  try { u = new URL(a.href); } catch (e) { return; }
  if ((u.protocol !== 'http:' && u.protocol !== 'https:') || u.origin === location.origin) return;
  // Home PC, page on the same machine as the server -> real Chrome via the
  // server. Travel App -> straight to the browser-side popup (the travel
  // server has no such route). Anything else (a phone/tablet remote) keeps
  // the browser's default new tab.
  const viaServer = SERVER_MODE && _SAME_MACHINE_PAGE && !TRAVEL_MODE;
  if (!viaServer && !TRAVEL_MODE) return;
  ev.preventDefault();
  if (!viaServer) { openPopupWindow(a.href); return; }
  postJSON('/api/open-external-link', { url: a.href }).then(r => {
    if (!r || r.ok === false) openPopupWindow(a.href);  // host not allowlisted / server unreachable
  });
});

function pollStreamingPopupClose(launchId, showKey, entryPath) {
  const poll = () => {
    fetch(`/api/streaming/popup-status?launch_id=${encodeURIComponent(launchId)}`, { headers: { 'X-Auth-Token': AUTH_TOKEN } })
      .then(r => r.json())
      .then(data => {
        if (data && data.closed) {
          returnToShowAfterStreaming(showKey, entryPath);
        } else {
          setTimeout(poll, 1500);
        }
      })
      .catch(() => {});  // a network blip here isn't worth retrying forever over
  };
  setTimeout(poll, 1500);
}

function returnToShowAfterStreaming(showKey, entryPath) {
  const idx = shows.findIndex(sh => sh.show_key === showKey);
  if (idx < 0) return;  // show no longer in the current filtered set - nothing to jump to
  openModal(idx);
  if (!entryPath) return;
  // Wait a tick for openModal()'s innerHTML write to actually land before
  // querying for the row.
  setTimeout(() => {
    for (const row of document.querySelectorAll('#modal-box .entry-row')) {
      if (row.dataset.path === entryPath) {
        row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        row.classList.add('just-returned');
        setTimeout(() => row.classList.remove('just-returned'), 2000);
        break;
      }
    }
  }, 50);
}

function recomputeShowReadiness(s) {
  s.entries.forEach(e => {
    e.ready_to_delete = s.retention === 'delete_after_watch' && e.watched && !e.retention_override;
  });
  s.all_watched = s.entries.length > 0 && s.entries.every(e => e.watched);
  s.ready_to_delete_count = s.entries.filter(e => e.ready_to_delete).length;
}

// Keeps the always-visible header "🗑️ Purge" button in sync - highlighted
// with a live count when anything's eligible, disabled/plain otherwise.
// Call after anything that can change eligibility (watched, restart-show,
// retention policy, retention override) - see recomputeShowReadiness() call
// sites - and once on page load.
function updatePurgeButton() {
  const btn = document.getElementById('purge-btn');
  if (!btn) return;
  let count = 0, bytes = 0;
  for (const s of shows) {
    for (const e of s.entries) {
      if (e.ready_to_delete) { count++; bytes += (e.size_bytes || 0); }
    }
  }
  const enabled = SERVER_MODE && count > 0;
  btn.disabled = !enabled;
  btn.classList.toggle('active', enabled);
  btn.title = !SERVER_MODE
    ? 'Start the local server to enable this'
    : (count > 0
      ? `Purge — ${count} file(s), ${formatBytes(bytes)} ready to move to Pending Deletion`
      : 'Purge — nothing eligible right now');
}

const AUTOFILE_QUEUE_ROOTS = new Set([@@queue_label_json@@]);
// See AUTOFILE_TV_EXCLUDED_ROOTS/autofile_in_scope() in render_engine.py's
// own Python side - the companion tool now owns TV filing out of this root
// end-to-end. Kept separate from "confidently matched" on purpose: an
// out-of-scope entry should vanish from Auto-File entirely, not show up
// under "needs review" (that's for a genuinely unconfident match still
// worth surfacing - not the app's business anymore is a different thing).
const AUTOFILE_TV_EXCLUDED_ROOTS = new Set([]);
function autofileConfidentlyMatched(s, e) {
  if (!s.tmdb_matched) return false;
  if (s.type === 'TV') return e.season != null && e.episode != null;
  return s.type === 'Movie';
}
function autofileInScope(s, e) {
  return !(s.type === 'TV' && AUTOFILE_TV_EXCLUDED_ROOTS.has(e.root));
}
function recomputeAutofileEligibility(s) {
  s.entries.forEach(e => {
    const queueEligible = AUTOFILE_QUEUE_ROOTS.has(e.root) && e.watched && s.retention !== 'delete_after_watch' && autofileInScope(s, e);
    const confident = autofileConfidentlyMatched(s, e);
    e.autofile_eligible = queueEligible && confident;
    e.autofile_needs_review = queueEligible && !confident;
  });
  s.autofile_ready_count = s.entries.filter(e => e.autofile_eligible).length;
}

// Keeps the always-visible header "📦 File to Library" button in sync -
// same shape as updatePurgeButton(). Call after anything that can change
// autofile eligibility (watched, restart-show, retention policy) and once
// on page load.
function updateAutofileButton() {
  const btn = document.getElementById('autofile-btn');
  if (!btn) return;
  let count = 0, bytes = 0;
  for (const s of shows) {
    for (const e of s.entries) {
      if (e.autofile_eligible) { count++; bytes += (e.size_bytes || 0); }
    }
  }
  const enabled = SERVER_MODE && count > 0;
  btn.disabled = !enabled;
  btn.classList.toggle('active', enabled);
  btn.title = !SERVER_MODE
    ? 'Start the local server to enable this'
    : (count > 0
      ? `File to Library — ${count} file(s), ${formatBytes(bytes)} watched, kept forever, queued for renaming into Video\\`
      : 'File to Library — nothing eligible right now');
}

// The modal is a one-shot innerHTML dump, not reactively bound to `shows` -
// call this after any change so an already-open detail view (e.g. "Keep
// this one" checkboxes appearing after switching retention to
// delete-after-watch) reflects it immediately instead of needing a
// close/reopen. Safe to call unconditionally: the modal overlay blocks
// interaction with anything else, so if it's open it can only be showing
// the show whose control was just clicked.
// Two tiers of refresh for a change made via a control inside an open show
// display (modal, or an expanded list-view card - both are real, separate
// surfaces a user can be looking at, and only the open modal case was ever
// covered here before, which is exactly the shape of the "Select whole
// show did nothing" bug: the backend + shows[] data updated correctly, but
// nothing on screen redrew to show it.
//
// refreshShowDisplay(key): full rebuild (via refreshEntriesBlock) - use
// when the change affects more than one row's markup (e.g. every entry
// row's checkboxes, or whether the "Keep this one" checkbox exists at all).
// refreshRetentionRow(show): swaps in just a fresh renderRetentionControl()
// - use for changes whose only visible effect is that row's own ready-to-
// move count, so a high-frequency action (checking off episodes one by
// one) doesn't flicker/rebuild everything else in the display each time.
function refreshShowDisplay(key) {
  const idx = shows.findIndex(sh => sh.show_key === key);
  if (idx >= 0) refreshEntriesBlock(idx);
}

function refreshRetentionRow(show) {
  document.querySelectorAll(`.retention-select[data-key="${CSS.escape(show.show_key)}"]`).forEach(sel => {
    const row = sel.closest('.retention-row');
    if (row) row.outerHTML = renderRetentionControl(show);
  });
}

window.onWatchedToggle = async function(input) {
  const path = input.getAttribute('data-path');
  const watched = input.checked;

  if (TRAVEL_MODE) {
    const travelWatched = loadTravelWatched();
    travelWatched[path] = watched;
    saveTravelWatched(travelWatched);
    const at = new Date().toISOString();
    const travelWatchedAt = loadTravelWatchedAt();
    if (watched) travelWatchedAt[path] = at; else delete travelWatchedAt[path];
    saveTravelWatchedAt(travelWatchedAt);
    let affectedShow = null;
    for (const s of shows) {
      const e = s.entries.find(en => en.path === path);
      if (e) { e.watched = watched; e.watched_at = at; affectedShow = s; }
      if (s.entries.some(en => en.path === path)) { recomputeShowReadiness(s); recomputeAutofileEligibility(s); }
    }
    // MRU history, mirroring promote_recently_watched()'s "only on
    // watched=true" rule (SPEC 7.3) - must run before renderFrontPanels().
    if (watched && affectedShow) travelRecentAdd(affectedShow.show_key);
    applyFilters();
    renderFrontPanels();
    updatePurgeButton();
    updateAutofileButton();
    if (affectedShow) refreshRetentionRow(affectedShow);
    travelServerSync('/api/watched', { path, watched, at });
    return;
  }

  const result = await postJSON('/api/watched', { path, watched });
  if (result && result.ok) {
    let affectedShow = null;
    for (const s of shows) {
      const e = s.entries.find(en => en.path === path);
      if (e) { e.watched = watched; e.watched_at = result.at || e.watched_at; affectedShow = s; }
      if (s.entries.some(en => en.path === path)) { recomputeShowReadiness(s); recomputeAutofileEligibility(s); }
    }
    applyFilters();
    renderFrontPanels();
    updatePurgeButton();
    updateAutofileButton();
    if (affectedShow) refreshRetentionRow(affectedShow);
  } else if (SERVER_MODE) {
    input.checked = !watched;
    if (result && result.message) showToast(result.message);
  }
};

window.onStreamingRelease = async function(btn) {
  if (!SERVER_MODE) return;
  const tmdb = parseInt(btn.getAttribute('data-tmdb'), 10);
  const type = btn.getAttribute('data-type') || 'tv';
  const key = btn.getAttribute('data-key');
  const s = shows.find(sh => sh.show_key === key);
  if (!confirm(`Stop following "${s ? s.title : 'this title'}"? It'll be removed from the library. You can follow it again any time.`)) return;
  btn.disabled = true;
  if (typeof showBootOverlay === 'function') showBootOverlay('Releasing...');
  const r = await postJSON('/api/streaming/release', { tmdb_id: tmdb, type: type });
  if (r && r.ok) {
    if (typeof closeModal === 'function') closeModal();
    showToast('Released - rebuilding the shelf...');
    // enrich_streaming re-run was kicked off server-side; catch the result.
    if (typeof pollRefreshStatus === 'function') pollRefreshStatus('sync');
    else if (typeof silentResync === 'function') setTimeout(() => silentResync('action'), 1500);
  } else {
    btn.disabled = false;
    if (typeof hideBootOverlay === 'function') hideBootOverlay();
    showToast((r && r.message) || 'Could not release');
  }
};

window.onRestartShow = async function(btn) {
  const key = btn.getAttribute('data-key');
  const s = shows.find(sh => sh.show_key === key);
  if (!s) return;
  if (TRAVEL_MODE) {
    // Offline restart (SPEC 7.5) - clear this show's keys from the local
    // travelWatched store, same mechanism as onWatchedToggle's travel
    // branch. Not a "watch" event, so the Recently Watched MRU is left
    // alone (mirrors promote_recently_watched only firing on watched=true).
    const travelWatched = loadTravelWatched();
    const travelWatchedAt = loadTravelWatchedAt();
    for (const e of s.entries) {
      delete travelWatched[e.path]; delete travelWatchedAt[e.path];
      e.watched = false; e.watched_at = null;
    }
    saveTravelWatched(travelWatched);
    saveTravelWatchedAt(travelWatchedAt);
    recomputeShowReadiness(s);
    recomputeAutofileEligibility(s);
    applyFilters();
    renderFrontPanels();
    updatePurgeButton();
    updateAutofileButton();
    showToast(`Marked "${s.title}" as not watched.`);
    refreshShowDisplay(key);
    return;
  }
  const result = await postJSON('/api/unwatch-show', { show_key: key });
  if (result && result.ok) {
    s.entries.forEach(e => { e.watched = false; e.watched_at = result.at || e.watched_at; });
    recomputeShowReadiness(s);
    recomputeAutofileEligibility(s);
    applyFilters();
    renderFrontPanels();
    updatePurgeButton();
    updateAutofileButton();
    showToast(`Marked "${s.title}" as not watched.`);
    refreshShowDisplay(key);
  } else if (result && result.message) {
    showToast(result.message);
  }
};

window.onConfirmRuntime = async function(btn) {
  const key = btn.getAttribute('data-key');
  const s = shows.find(sh => sh.show_key === key);
  if (!s) return;
  const result = await postJSON('/api/confirm-runtime', { show_key: key });
  if (result && result.ok) {
    s.runtime_mismatch = false;
    showToast(`Confirmed - "${s.title}" won't be flagged again.`);
    applyFilters();
    refreshShowDisplay(key);
  } else if (result && result.message) {
    showToast(result.message);
  }
};

window.onRetentionOverrideToggle = async function(input) {
  const path = input.getAttribute('data-path');
  const override = input.checked;
  if (TRAVEL_MODE) {
    const overrides = loadTravelRetentionOverrides();
    if (override) overrides[path] = true; else delete overrides[path];
    saveTravelRetentionOverrides(overrides);
    let affectedShow = null;
    for (const s of shows) {
      const e = s.entries.find(en => en.path === path);
      if (e) e.retention_override = override;
      if (s.entries.some(en => en.path === path)) {
        recomputeShowReadiness(s);
        affectedShow = s;
      }
    }
    applyFilters();
    updatePurgeButton();
    if (affectedShow) refreshRetentionRow(affectedShow);
    travelServerSync('/api/retention-override', { path, override });
    return;
  }
  const result = await postJSON('/api/retention-override', { path, override });
  if (result && result.ok) {
    let affectedShow = null;
    for (const s of shows) {
      const e = s.entries.find(en => en.path === path);
      if (e) e.retention_override = override;
      if (s.entries.some(en => en.path === path)) {
        recomputeShowReadiness(s);
        affectedShow = s;
      }
    }
    applyFilters();
    updatePurgeButton();
    if (affectedShow) refreshRetentionRow(affectedShow);
  } else {
    input.checked = !override;
    if (result && result.message) showToast(result.message);
  }
};

window.onTravelSelectToggle = async function(input) {
  const path = input.getAttribute('data-path');
  const selected = input.checked;
  const result = await postJSON('/api/travel-select', { path, selected });
  if (result && result.ok) {
    for (const s of shows) {
      const e = s.entries.find(en => en.path === path);
      if (e) e.travel_selected = selected;
    }
    updateTravelPanel();
  } else {
    input.checked = !selected;
    if (result && result.message) showToast(result.message);
  }
};

window.onTravelSelectShow = async function(btn) {
  const key = btn.getAttribute('data-key');
  const s = shows.find(sh => sh.show_key === key);
  if (!s) return;
  const allSelected = s.entries.every(e => e.travel_selected);
  const selected = !allSelected;
  const result = await postJSON('/api/travel-select-show', { show_key: key, selected });
  if (result && result.ok) {
    s.entries.forEach(e => e.travel_selected = selected);
    applyFilters();
    updateTravelPanel();
    refreshShowDisplay(key);
    showToast(selected ? `Selected all of "${s.title}" for travel.` : `Deselected all of "${s.title}" from travel.`);
  } else if (result && result.message) {
    showToast(result.message);
  }
};

function updateTravelPanel() {
  const countEl = document.getElementById('travel-count');
  // travel-panel-body was the old element id from when this lived inside
  // the Dashboard drawer, before the Travel popup became its own modal
  // (#travel-overlay/#travel-modal-box, 2026-08-23) - that id was never
  // updated here, so this bailed out silently on every call ever since,
  // leaving "0 items selected, 0 MB total" stuck regardless of what was
  // actually selected (the selection itself was always saving correctly -
  // confirmed via a direct check of data\user_state.json's
  // travel_selection - only this readout was broken).
  if (!countEl) return;
  let count = 0, bytes = 0;
  for (const s of shows) {
    for (const e of s.entries) {
      if (e.travel_selected) { count++; bytes += (e.size_bytes || 0); }
    }
  }
  countEl.textContent = count;
  document.getElementById('travel-size').textContent = formatBytes(bytes) || '0 MB';
}

window.prepareTravelExport = async function() {
  const dest = document.getElementById('travel-dest-input').value.trim();
  if (!dest) { showToast('Enter a destination path first, e.g. F:\\\\TravelMedia'); return; }
  const modeInput = document.querySelector('input[name="travel-mode-kind"]:checked');
  const mode = modeInput ? modeInput.value : 'personal';
  const btn = document.getElementById('travel-export-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Generating...';
  const result = await postJSON('/api/prepare-travel-export', { destination: dest, mode: mode });
  btn.disabled = false;
  btn.textContent = '🧳 Generate Export Script';
  if (result && result.ok) {
    showToast('Travel Export.ps1 generated - a copy + a "Run Travel Export.vbs" are on your Desktop. Review the .ps1, then double-click the .vbs to run it hidden with a small progress window.');
  } else {
    showToast((result && result.message) || 'Failed to generate export script.');
  }
};

window.onRetentionChange = async function(select) {
  const key = select.getAttribute('data-key');
  const policy = select.value;
  if (TRAVEL_MODE) {
    const s = shows.find(sh => sh.show_key === key);
    if (!s) return;
    s.retention = policy;
    const overrides = loadTravelRetention();
    // Only store it if it's actually a delta from what the export baked
    // in - so a show nobody touched never shows up in travel-changes.json.
    if (policy === s._homeRetention) delete overrides[key]; else overrides[key] = policy;
    saveTravelRetention(overrides);
    recomputeShowReadiness(s);
    recomputeAutofileEligibility(s);
    applyFilters();
    updatePurgeButton();
    updateAutofileButton();
    showToast(policy === 'delete_after_watch' ? 'Set to delete after watching.' : 'Set to keep forever.');
    refreshShowDisplay(key);
    travelServerSync('/api/retention', { show_key: key, policy });
    return;
  }
  const result = await postJSON('/api/retention', { show_key: key, policy });
  if (result && result.ok) {
    const s = shows.find(sh => sh.show_key === key);
    if (s) {
      s.retention = policy;
      recomputeShowReadiness(s);
      recomputeAutofileEligibility(s);
    }
    applyFilters();
    updatePurgeButton();
    updateAutofileButton();
    showToast(policy === 'delete_after_watch' ? 'Set to delete after watching.' : 'Set to keep forever.');
    if (s) refreshShowDisplay(key);
  } else if (result && result.message) {
    showToast(result.message);
  }
};

window.onTop10Toggle = async function(btn) {
  const key = btn.getAttribute('data-key');
  const person = btn.getAttribute('data-person');
  const s = shows.find(sh => sh.show_key === key);
  const field = person === 'you' ? 'top10_you' : 'top10_partner';
  const isActive = s ? s[field] : btn.classList.contains('active');
  const action = isActive ? 'remove' : 'add';
  const result = await postJSON('/api/top10', { show_key: key, person, action });
  if (result && result.ok) {
    if (s) s[field] = !isActive;
    btn.classList.toggle('active', !isActive);
  } else if (result && result.message) {
    showToast(result.message);
  }
};

window.onNoInterestToggle = async function(btn) {
  const key = btn.getAttribute('data-key');
  const person = btn.getAttribute('data-person');
  const s = shows.find(sh => sh.show_key === key);
  const field = person === 'you' ? 'no_interest_you' : 'no_interest_partner';
  const isActive = s ? s[field] : btn.classList.contains('active');
  const action = isActive ? 'remove' : 'add';
  const result = await postJSON('/api/no-interest', { show_key: key, person, action });
  if (result && result.ok) {
    if (s) s[field] = !isActive;
    btn.classList.toggle('active', !isActive);
  } else if (result && result.message) {
    showToast(result.message);
  }
};

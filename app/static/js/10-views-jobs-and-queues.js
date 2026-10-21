window.openMissingEpisodesView = function() {
  _modalCurrent = function() { openMissingEpisodesView(); };
  // A season covered elsewhere (physical disc or Amazon - see
  // isSeasonCoveredElsewhere()) isn't something to go download, just
  // something to go get from where it already is. This whole view is a
  // download shopping list, so those seasons are excluded from
  // candidacy/counts here entirely (they still show, differently, on
  // the show's own detail view).
  const candidates = shows.filter(s =>
    (s.missing_episodes && s.missing_episodes.length) ||
    (s.missing_seasons && s.missing_seasons.some(m => !isSeasonCoveredElsewhere(m)))
  );
  candidates.sort((a, b) => a.title.localeCompare(b.title));

  const rows = candidates.map(s => {
    const trulyMissingSeasons = (s.missing_seasons || []).filter(m => !isSeasonCoveredElsewhere(m));
    const elsewhereCount = s.missing_seasons.length - trulyMissingSeasons.length;
    const parts = [];
    if (trulyMissingSeasons.length) parts.push(`${trulyMissingSeasons.length} season${trulyMissingSeasons.length === 1 ? '' : 's'}`);
    if (s.missing_episodes.length) parts.push(`${s.missing_episodes.length} ep${s.missing_episodes.length === 1 ? '' : 's'}`);
    const elsewhereNote = elsewhereCount > 0 ? ` <span style="color:#8a5a2e;">(+${elsewhereCount} owned elsewhere, not listed)</span>` : '';
    return `<label class="missing-digest-row">
      <input type="checkbox" class="missing-digest-check" data-key="${escapeHtml(s.show_key)}" checked>
      <span class="missing-digest-title">${escapeHtml(s.title)}</span>
      <span class="missing-digest-summary">${parts.join(' + ')} missing${elsewhereNote}</span>
    </label>`;
  }).join('');

  const body = candidates.length === 0
    ? `<p style="color:var(--text-dim);">Nothing missing right now.</p>`
    : `<div style="display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:12px; flex-wrap:wrap;">
        <div style="display:flex; gap:8px;">
          <button class="header-btn" type="button" onclick="setAllMissingDigestChecks(true)">Select all</button>
          <button class="header-btn" type="button" onclick="setAllMissingDigestChecks(false)">Select none</button>
        </div>
        <button class="header-btn" id="missing-digest-export-btn" type="button" onclick="exportMissingDigest()">💾 Export to Desktop</button>
      </div>
      <div class="missing-digest-list">${rows}</div>`;

  document.getElementById('modal-box').className = 'modal-box modal-box-roomy';
  document.getElementById('modal-box').innerHTML = `
    <div class="modal-header${modalStack.length ? ' with-back' : ''}">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      ${modalBackBtnHtml()}
      <div class="title">📋 Missing Episodes — ${candidates.length} show${candidates.length === 1 ? '' : 's'}</div>
    </div>
    <div class="entries" style="display:block; max-height: 75vh; overflow-y: auto; padding: 16px;">
      ${body}
    </div>
  `;
  document.getElementById('modal-overlay').classList.add('open');
};

window.setAllMissingDigestChecks = function(checked) {
  document.querySelectorAll('.missing-digest-check').forEach(cb => { cb.checked = checked; });
};

// Library History - the persistent add/removed ledger + tombstones,
// written by plans/build_library_ledger.py. Read-only view.
const LEDGER_LABELS = {
  show_added: { icon: '＋', cls: 'lh-add', text: 'Added' },
  show_returned: { icon: '↩', cls: 'lh-return', text: 'Returned' },
  show_removed: { icon: '－', cls: 'lh-remove', text: 'Removed' },
  episodes_added: { icon: '＋', cls: 'lh-add', text: 'Episodes added' },
  episodes_removed: { icon: '－', cls: 'lh-remove', text: 'Episodes removed' },
  ledger_initialized: { icon: '•', cls: 'lh-info', text: 'Ledger started' },
  scan_shrank: { icon: '⚠', cls: 'lh-warn', text: 'Scan came back short — skipped' },
};
window.openLibraryHistoryView = function() {
  _modalCurrent = function() { openLibraryHistoryView(); };
  document.getElementById('modal-box').className = 'modal-box modal-box-roomy';
  document.getElementById('modal-box').innerHTML = `
    <div class="modal-header${modalStack.length ? ' with-back' : ''}">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      ${modalBackBtnHtml()}
      <div class="title">🗂 Library History</div>
    </div>
    <div class="entries" style="display:block; max-height:75vh; overflow-y:auto; padding:16px;" id="lh-body">
      <p style="color:var(--text-dim);">Loading…</p>
    </div>`;
  document.getElementById('modal-overlay').classList.add('open');
  fetch('/api/library-history', { headers: { 'X-Auth-Token': AUTH_TOKEN } })
    .then(r => r.ok ? r.json() : null)
    .then(d => renderLibraryHistory(d))
    .catch(() => {
      const b = document.getElementById('lh-body');
      if (b) b.innerHTML = '<p style="color:var(--text-dim);">Could not load the ledger.</p>';
    });
};
function renderLibraryHistory(d) {
  const b = document.getElementById('lh-body');
  if (!b) return;
  if (!d) { b.innerHTML = '<p style="color:var(--text-dim);">Could not load the ledger.</p>'; return; }
  const evs = d.events || [];
  const tombs = d.tombstones || [];

  const evRow = (e) => {
    const L = LEDGER_LABELS[e.event] || { icon: '•', cls: 'lh-info', text: e.event };
    let detail = escapeHtml(e.title || e.identity || '');
    if (e.type) detail += ` <span class="lh-type">${escapeHtml(e.type)}</span>`;
    if (e.episodes && e.episodes.length) {
      const eps = e.episodes.slice(0, 8).map(([s, ep]) => `S${String(s).padStart(2,'0')}E${String(ep).padStart(2,'0')}`).join(', ');
      detail += ` <span class="lh-eps">${eps}${e.episodes.length > 8 ? ` +${e.episodes.length - 8}` : ''}</span>`;
    } else if (e.episode_count != null && (e.event === 'show_added' || e.event === 'show_removed' || e.event === 'show_returned')) {
      detail += ` <span class="lh-eps">${e.episode_count} file${e.episode_count === 1 ? '' : 's'}</span>`;
    }
    if (e.reason) detail += ` <span class="lh-reason lh-reason-${e.reason}">${e.reason === 'purged_after_watch' ? 'purged after watching' : escapeHtml(e.reason)}</span>`;
    if (e.event === 'scan_shrank') detail = `${e.old_count} → ${e.new_count} shows`;
    const time = e.at ? new Date(e.at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '';
    return `<div class="lh-row"><span class="lh-badge ${L.cls}" title="${escapeHtml(L.text)}">${L.icon}</span>`
      + `<span class="lh-detail">${detail}</span><span class="lh-time">${time}</span></div>`;
  };

  // group by calendar day
  const byDay = {};
  for (const e of evs) {
    const day = e.at ? new Date(e.at).toISOString().slice(0, 10) : 'unknown';
    (byDay[day] = byDay[day] || []).push(e);
  }
  const dayHtml = Object.keys(byDay).sort().reverse().map(day => {
    const label = day === 'unknown' ? 'unknown' : formatDate(day);
    return `<div class="lh-day">${escapeHtml(label)}</div>` + byDay[day].map(evRow).join('');
  }).join('');

  const tombHtml = tombs.length ? `
    <div class="lh-section">Removed titles — still tracked (${tombs.length})</div>
    ${tombs.map(t => `<div class="lh-row lh-tomb">
      <span class="lh-badge lh-remove">－</span>
      <span class="lh-detail">${escapeHtml(t.title || t.identity)} <span class="lh-type">${escapeHtml(t.type || '')}</span>
        <span class="lh-eps">${t.last_known_episode_count || 0} file${(t.last_known_episode_count || 0) === 1 ? '' : 's'} when removed</span>
        <span class="lh-reason lh-reason-${escapeHtml(t.reason || '')}">${t.reason === 'purged_after_watch' ? 'purged after watching' : escapeHtml(t.reason || '')}</span></span>
      <span class="lh-time">${t.removed_at ? formatDate(t.removed_at.slice(0,10)) : ''}</span>
    </div>`).join('')}` : '';

  b.innerHTML = (evs.length ? `<div class="lh-timeline">${dayHtml}</div>` :
    `<p style="color:var(--text-dim);">No history yet — the ledger records changes from the next library refresh on.</p>`)
    + tombHtml;
}

function buildMissingDigestText(selectedKeys) {
  const selected = shows.filter(s => selectedKeys.has(s.show_key));
  selected.sort((a, b) => a.title.localeCompare(b.title));
  const blocks = selected.map(s => {
    // A season can't appear in both lists at once (missing_seasons = zero
    // owned episodes that season; missing_episodes = gaps within a season
    // that IS owned) - safe to merge and sort by season number together,
    // so e.g. a whole missing Season 3 doesn't get listed ahead of a
    // Season 1 episode gap just because it's a season-level entry.
    const items = [];
    for (const m of (s.missing_seasons || [])) {
      if (isSeasonCoveredElsewhere(m)) continue; // owned on disc/Amazon already - not a download task, skip from the shopping list
      items.push({ season: m.season, episode: -1,
        text: `  Season ${m.season} - entirely missing (${m.aired_episode_count} episode${m.aired_episode_count === 1 ? '' : 's'})` });
    }
    for (const m of (s.missing_episodes || [])) {
      const epLabel = `S${String(m.season).padStart(2, '0')}E${String(m.episode).padStart(2, '0')}`;
      const name = m.name ? ` - ${m.name}` : '';
      const air = m.air_date ? ` (aired ${m.air_date})` : '';
      items.push({ season: m.season, episode: m.episode, text: `  ${epLabel}${name}${air}` });
    }
    items.sort((a, b) => (a.season - b.season) || (a.episode - b.episode));
    const lines = [s.title, ...items.map(i => i.text)];
    return lines.join('\n');
  });
  return blocks.join('\n\n') + '\n';
}

window.exportMissingDigest = async function() {
  const selectedKeys = new Set(Array.from(document.querySelectorAll('.missing-digest-check:checked')).map(cb => cb.getAttribute('data-key')));
  if (selectedKeys.size === 0) { showToast('Select at least one show first.'); return; }
  const content = buildMissingDigestText(selectedKeys);
  const btn = document.getElementById('missing-digest-export-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Exporting...';
  const result = await postJSON('/api/export-missing-digest', { content });
  btn.disabled = false;
  btn.textContent = '💾 Export to Desktop';
  if (result && result.ok) {
    showToast('Missing Episodes.txt exported to your Desktop.');
  } else {
    showToast((result && result.message) || 'Failed to export digest.');
  }
};

let refreshPollTimer = null;
window.triggerRefresh = async function() {
  if (!SERVER_MODE) { showToast('Start the local server to refresh'); return; }
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.classList.add('is-busy');
  btn.title = 'Starting...';
  showBootOverlay('Starting...');
  const result = await postJSON('/api/refresh', {});
  if (!result || result.ok === false) {
    btn.disabled = false;
    btn.classList.remove('is-busy');
    btn.title = 'Refresh library';
    hideBootOverlay();
    if (result && result.message) showToast(result.message);
    return;
  }
  pollRefreshStatus('reload');
};

// mode: 'reload' (the Refresh-library button - a full pipeline can change
// posters, counts and the server-rendered side panels, so a real reload is
// right) or 'sync' (purge / auto-file / dup-resolve / file-queue - those
// only touch the shows list + a couple of buttons, so re-pull the data and
// redraw in place, no jarring reload, scroll and filters kept).
async function pollRefreshStatus(mode) {
  mode = mode || 'reload';
  // pollRefreshStatus() has four call sites (the refresh button itself,
  // plus purge/autofile/resolve-duplicate success handlers that each
  // trigger a background refresh) - without this, two of them firing
  // close together starts two independent self-rescheduling chains that
  // never cancel each other, doubling the /api/refresh/status traffic
  // for as long as both keep running. Clearing any pending timer from a
  // still-live chain before doing this check keeps at most one in flight.
  clearTimeout(refreshPollTimer);
  try {
    const resp = await fetch('/api/refresh/status', { headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const status = await resp.json();
    const btn = document.getElementById('refresh-btn');
    const total = status.total || 8;
    const done = status.log ? status.log.length : 0;
    if (status.steps && status.steps.length) updateBootOverlayFromStatus(status);
    else updateBootOverlay(status.running, done, total);
    if (status.running) {
      btn.classList.add('is-busy');
      btn.title = `Refreshing... (${done}/${total} steps)`;
      refreshPollTimer = setTimeout(() => pollRefreshStatus(mode), 1000);
    } else {
      btn.disabled = false;
      btn.classList.remove('is-busy');
      if (status.error) {
        btn.title = 'Refresh failed - retry';
        hideBootOverlay();
        // The bare error string alone ("script exited with code 1") never
        // said WHY - status.log already carries each script's real
        // stdout/stderr (see app/web_server.py's run_refresh_pipeline()),
        // it just wasn't being shown. Same detail is also always
        // reviewable later via the Admin panel's "Refresh & Server Log"
        // tab, not just at the moment of failure.
        const detail = (status.log || [])
          .map(e => e.returncode !== 0
            ? `${e.script} (exit ${e.returncode})\n  STDOUT: ${(e.stdout || '(empty)').trim()}\n  STDERR: ${(e.stderr || '(empty)').trim()}`
            : `${e.script} (exit 0) - OK`)
          .join('\n\n');
        showErrorDialog('Refresh failed', `${status.error}\n\n${detail}`);
      } else if (mode === 'sync') {
        btn.title = 'Refresh library';
        // Keep the overlay up through the resync itself (not just the
        // pipeline run that preceded it) - hiding it the instant
        // status.running flips false would open exactly the click-through
        // window this whole mechanism exists to prevent.
        setBootOverlayProgress('Syncing changes...', 1, 1);
        await silentResync('action');
        hideBootOverlay();
      } else {
        btn.title = 'Refresh library';
        // A full reload (not a silent re-render) whenever ANYTHING was
        // waiting on this refresh finishing - the boot overlay included -
        // so the fresh page load picks up everything the pipeline just
        // wrote (posters now cached, new titles, corrected counts) instead
        // of patching the in-memory copy that was possibly taken mid-write.
        // No explicit hideBootOverlay() here - the page reload a moment
        // later clears it along with everything else.
        // The reload below redraws the whole library; keep the overlay up through it
        // (the next page picks the flag up and lifts the overlay once it has painted).
        try { sessionStorage.setItem('appDrawing', '1'); } catch (e) {}
        bootShowDrawing();
        setTimeout(() => window.location.reload(), 700);
      }
    }
  } catch (err) {
    const btn = document.getElementById('refresh-btn');
    btn.disabled = false;
    btn.classList.remove('is-busy');
    // A transient fetch failure shouldn't leave the user stuck behind a
    // blocking overlay with no way out - better to let them see the page
    // (and hit Refresh again) than trap them on a network hiccup.
    hideBootOverlay();
  }
}

// The blocking overlay - originally shown only when the page happened to
// load mid-refresh (see REFRESH_STATUS / the init block near the end of
// the script), now reused (2026-09-12, at the user's request) for every
// action that changes the library out from under you: Refresh library,
// Delete, File to Library, File Download Queue, Admin add/fix saves,
// streaming release, duplicate resolution. One blocking mechanism, one
// place to look, instead of each action having its own "disable this
// button and hope nobody clicks anything else" logic.
//
// showBootOverlay(label) shows it and starts the rotating hint text.
// setBootOverlayProgress(label, done, total) updates the bar/label while
// it's already up - a no-op if the overlay isn't showing, so callers
// (pollRefreshStatus's own tick, purge/autofile/file-queue polls) can call
// it unconditionally without tracking their own "is it up" flag.
// hideBootOverlay() hides it and stops the hint rotation.
@@boot_overlay_js@@

// Random loading-screen filler text (2026-09-12, at the user's request) -
// one random line from the whole flattened pool (LOADING_HINTS_POOL),
// for as long as the blocking overlay above is up (see _hintSeconds for how long each stays). Fades out,
// swaps text, fades in - see .boot-hint/.boot-hint.fading. Avoids
// repeating the immediately-previous line so a short wait doesn't show
// the same one twice in a row.
let loadingHintTimer = null;
let loadingHintLastText = null;
function _pickLoadingHint() {
  if (!LOADING_HINTS_POOL.length) return null;
  if (LOADING_HINTS_POOL.length === 1) return LOADING_HINTS_POOL[0];
  let pick;
  do { pick = LOADING_HINTS_POOL[Math.floor(Math.random() * LOADING_HINTS_POOL.length)]; }
  while (pick.text === loadingHintLastText);
  return pick;
}
function _showLoadingHint() {
  const el = document.getElementById('boot-hint');
  if (!el) return null;
  const pick = _pickLoadingHint();
  if (!pick) return null;
  el.classList.add('fading');
  setTimeout(() => {
    loadingHintLastText = pick.text;
    const label = LOADING_HINT_LABELS[pick.cat] || '';
    el.innerHTML = (label ? `<span class="boot-hint-label">${escapeHtml(label)}</span>` : '')
      + escapeHtml(pick.text);
    el.classList.remove('fading');
  }, 400);
  return pick;
}
// Time on screen scales with the line's length (2s + 1s per 14 characters, 7s to 24s) so a
// long one is not swapped out before it can be read; it used to be a fixed 10s for every line.
function _hintSeconds(text) {
  return Math.max(7, Math.min(24, 2 + (text || '').length / 14));
}
function startLoadingHint() {
  if (loadingHintTimer) return;  // already running - don't stack timers
  const step = () => {
    const pick = _showLoadingHint();
    loadingHintTimer = setTimeout(step, (pick ? _hintSeconds(pick.text) : 10) * 1000);
  };
  step();
}
function stopLoadingHint() {
  clearTimeout(loadingHintTimer);
  loadingHintTimer = null;
  loadingHintLastText = null;
  const el = document.getElementById('boot-hint');
  if (el) el.innerHTML = '';
}

// Remote-only port's idle screen (see #remote-idle's own comment above and
// REMOTE_ONLY's, and pollVlcState() below for what shows/hides it) - same
// rotate-a-random-line-every-10s-with-a-fade approach as the boot overlay's
// own loading hints, just a dedicated small pool instead of reusing
// LOADING_HINTS_POOL (those are "please wait," these need to actually say
// nothing's playing - not just be generic filler).
const REMOTE_IDLE_QUIPS = @@idle_quips_json@@;
let remoteIdleTimer = null;
let remoteIdleLastText = null;
function _pickRemoteIdleQuip() {
  if (REMOTE_IDLE_QUIPS.length === 1) return REMOTE_IDLE_QUIPS[0];
  let pick;
  do { pick = REMOTE_IDLE_QUIPS[Math.floor(Math.random() * REMOTE_IDLE_QUIPS.length)]; }
  while (pick === remoteIdleLastText);
  return pick;
}
function _showRemoteIdleQuip() {
  const el = document.getElementById('remote-idle-hint');
  if (!el) return;
  const pick = _pickRemoteIdleQuip();
  el.classList.add('fading');
  setTimeout(() => {
    remoteIdleLastText = pick;
    el.textContent = pick;
    el.classList.remove('fading');
  }, 400);
}
function showRemoteIdle() {
  const el = document.getElementById('remote-idle');
  if (!el) return;
  el.hidden = false;
  if (remoteIdleTimer) return;  // rotation already running - don't stack timers
  _showRemoteIdleQuip();
  remoteIdleTimer = setInterval(_showRemoteIdleQuip, 10000);
}
function hideRemoteIdle() {
  const el = document.getElementById('remote-idle');
  if (el) el.hidden = true;
  clearInterval(remoteIdleTimer);
  remoteIdleTimer = null;
  remoteIdleLastText = null;
}

let purgePollTimer = null;
window.purgePendingDeletion = async function() {
  if (!SERVER_MODE) { showToast('Start the local server to purge'); return; }
  let eligibleCount = 0, eligibleBytes = 0;
  for (const s of shows) {
    for (const e of s.entries) {
      if (e.ready_to_delete) { eligibleCount++; eligibleBytes += (e.size_bytes || 0); }
    }
  }
  const ok = confirm(
    `Move ${eligibleCount} file(s) (${formatBytes(eligibleBytes)}) to Pending Deletion for you to review?\n\n` +
    `Nothing will be deleted - only moved out of your library into that folder.`
  );
  if (!ok) return;
  const btn = document.getElementById('purge-btn');
  btn.disabled = true;
  btn.classList.add('is-busy');
  showBootOverlay('Starting purge...');
  const result = await postJSON('/api/purge-pending-deletion', {});
  if (!result || result.ok === false) {
    updatePurgeButton();
    btn.classList.remove('is-busy');
    hideBootOverlay();
    if (result && result.message) showToast(result.message);
    return;
  }
  pollPurgeStatus();
};

async function pollPurgeStatus() {
  clearTimeout(purgePollTimer);  // see pollRefreshStatus()'s comment - same dedup, one chain at most
  try {
    const resp = await fetch('/api/purge-pending-deletion/status', { headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const status = await resp.json();
    const btn = document.getElementById('purge-btn');
    if (!btn) return;
    if (status.running) {
      setBootOverlayProgress(`Deleting... (${status.moved_count} of ${status.total_count})`, status.moved_count, status.total_count);
      purgePollTimer = setTimeout(pollPurgeStatus, 2000);
    } else if (status.error) {
      btn.disabled = false;
      btn.classList.remove('is-busy');
      hideBootOverlay();
      showErrorDialog('Purge failed', status.error);
    } else if (status.moved_count > 0) {
      btn.classList.remove('is-busy');
      const skippedNote = status.skipped_count > 0 ? ` ${status.skipped_count} skipped (already gone, or a name collision at the destination) - see logs\\.` : '';
      showToast(`Moved ${status.moved_count} file(s) (${formatBytes(status.moved_bytes)}) to Pending Deletion.${skippedNote} Refreshing library...`);
      pollRefreshStatus('sync');
    } else if (status.skipped_count > 0) {
      btn.classList.remove('is-busy');
      updatePurgeButton();
      hideBootOverlay();
      showToast(`Nothing moved - all ${status.skipped_count} eligible file(s) were skipped (already gone, or a name collision at the destination). See logs\\ for why.`);
    } else {
      btn.classList.remove('is-busy');
      updatePurgeButton();
      hideBootOverlay();
      showToast('Nothing moved - try refreshing the library first.');
    }
  } catch (err) {
    const btn = document.getElementById('purge-btn');
    if (btn) { btn.disabled = false; btn.classList.remove('is-busy'); }
    hideBootOverlay();
  }
}

// Batch duplicate resolution (2026-08-27, at the user's request) - was one
// refresh-triggering call per group before this, disruptive when working
// through a big batch (the Doctor Who case: 49 groups at once). Now a
// checkbox per group (opts it into the batch) plus a radio per candidate
// (picks the keeper, independent of the checkbox - changing your mind
// about which file wins never silently queues a group you hadn't meant to
// touch) feed one combined call and exactly one refresh at the end.
window.updateDupProcessButton = function() {
  const btn = document.getElementById('dup-process-btn');
  if (!btn) return;
  const n = document.querySelectorAll('.dup-group-select:checked').length;
  btn.textContent = `Process selected (${n})`;
  btn.disabled = n === 0;
};

window.onProcessSelectedDuplicates = async function() {
  if (!SERVER_MODE) { showToast('Start the local server to resolve duplicates'); return; }
  const checked = Array.from(document.querySelectorAll('.dup-group-select:checked'));
  if (!checked.length) return;
  const resolutions = [];
  const groupEls = [];
  let totalDiscard = 0;
  for (const cb of checked) {
    const groupEl = cb.closest('.dup-group');
    const selectedRadio = groupEl.querySelector('.dup-keep-radio:checked');
    if (!selectedRadio) continue;
    const allPaths = JSON.parse(groupEl.dataset.paths);
    const discardPaths = allPaths.filter(p => p !== selectedRadio.value);
    if (!discardPaths.length) continue;
    resolutions.push({ show_title: groupEl.dataset.show, discard_paths: discardPaths });
    groupEls.push(groupEl);
    totalDiscard += discardPaths.length;
  }
  if (!resolutions.length) return;
  const ok = confirm(
    `Resolve ${resolutions.length} group(s), moving ${totalDiscard} file(s) to Pending Deletion?\n\n` +
    `Nothing will be deleted - only moved out of your library into that folder.`
  );
  if (!ok) return;
  const btn = document.getElementById('dup-process-btn');
  btn.disabled = true;
  btn.textContent = 'Processing...';
  // Background job (not a synchronous call) - a big batch across many
  // groups can genuinely take minutes (cross-drive moves, tens of GB),
  // confirmed real via a client-side timeout during testing even though
  // the server kept working the whole time. Same start+poll shape as
  // purge/autofile.
  dupResolvePendingGroupEls = groupEls;
  showBootOverlay('Starting duplicate resolve...');
  const startResult = await postJSON('/api/resolve-duplicates-batch', { resolutions });
  if (!startResult || startResult.ok === false) {
    updateDupProcessButton();
    hideBootOverlay();
    showToast(startResult && startResult.message ? startResult.message : 'Could not start resolving the selected duplicates.');
    return;
  }
  pollDupResolveStatus();
};

let dupResolvePendingGroupEls = [];
let dupResolvePollTimer = null;
async function pollDupResolveStatus() {
  clearTimeout(dupResolvePollTimer);  // see pollRefreshStatus()'s comment - same dedup, one chain at most
  try {
    const resp = await fetch('/api/resolve-duplicates-batch/status', { headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const status = await resp.json();
    const btn = document.getElementById('dup-process-btn');
    if (!btn) return;
    if (status.running) {
      setBootOverlayProgress(`Resolving duplicates... (${status.moved_count} of ${status.total_count})`, status.moved_count, status.total_count);
      dupResolvePollTimer = setTimeout(pollDupResolveStatus, 2000);
    } else if (status.error) {
      updateDupProcessButton();
      hideBootOverlay();
      showErrorDialog('Duplicate resolve failed', status.error);
    } else {
      const skippedNote = status.skipped_count > 0 ? ` ${status.skipped_count} could not be moved (already gone, or a name collision at the destination).` : '';
      showToast(`Moved ${status.moved_count} file(s) to Pending Deletion.${skippedNote} Refreshing library...`);
      dupResolvePendingGroupEls.forEach(g => g.remove());
      dupResolvePendingGroupEls = [];
      pollRefreshStatus('sync');
    }
  } catch (err) {
    updateDupProcessButton();
    hideBootOverlay();
  }
}

let autofilePollTimer = null;
window.autofileQueue = async function() {
  if (!SERVER_MODE) { showToast('Start the local server to file'); return; }
  let eligibleCount = 0, eligibleBytes = 0;
  for (const s of shows) {
    for (const e of s.entries) {
      if (e.autofile_eligible) { eligibleCount++; eligibleBytes += (e.size_bytes || 0); }
    }
  }
  const ok = confirm(
    `Rename and move ${eligibleCount} file(s) (${formatBytes(eligibleBytes)}) from the download queue into Video\\?\n\n` +
    `Creates a new show/season folder if needed. Nothing not confidently identified will be touched.`
  );
  if (!ok) return;
  const btn = document.getElementById('autofile-btn');
  btn.disabled = true;
  btn.classList.add('is-busy');
  showBootOverlay('Starting file-to-library...');
  const result = await postJSON('/api/autofile-queue', {});
  if (!result || result.ok === false) {
    updateAutofileButton();
    btn.classList.remove('is-busy');
    hideBootOverlay();
    if (result && result.message) showToast(result.message);
    return;
  }
  pollAutofileStatus();
};

async function pollAutofileStatus() {
  clearTimeout(autofilePollTimer);  // see pollRefreshStatus()'s comment - same dedup, one chain at most
  try {
    const resp = await fetch('/api/autofile-queue/status', { headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const status = await resp.json();
    const btn = document.getElementById('autofile-btn');
    if (!btn) return;
    if (status.running) {
      setBootOverlayProgress(`Filing... (${status.filed_count + status.skipped_count} of ${status.total_count})`, status.filed_count + status.skipped_count, status.total_count);
      autofilePollTimer = setTimeout(pollAutofileStatus, 2000);
    } else if (status.error) {
      btn.disabled = false;
      btn.classList.remove('is-busy');
      hideBootOverlay();
      showErrorDialog('Auto-file failed', status.error);
    } else if (status.filed_count > 0) {
      btn.classList.remove('is-busy');
      const flaggedNote = status.skipped_count > 0 ? ` ${status.skipped_count} flagged for review.` : '';
      showToast(`Filed ${status.filed_count} file(s) (${formatBytes(status.filed_bytes)}) into Video\\.${flaggedNote} Refreshing library...`);
      // The flagged-items dialog needs to actually be seen/dismissed, so
      // it can't sit behind the blocking overlay (which sits on top of
      // everything else, modals included) - hide first, same as any
      // other legitimate interruption of the block.
      if (status.flagged && status.flagged.length) { hideBootOverlay(); showFlaggedDialog(status.flagged); }
      pollRefreshStatus('sync');
    } else if (status.skipped_count > 0) {
      btn.classList.remove('is-busy');
      updateAutofileButton();
      hideBootOverlay();
      showToast(`Nothing filed - all ${status.skipped_count} queued item(s) were flagged for review.`);
      showFlaggedDialog(status.flagged || []);
    } else {
      btn.classList.remove('is-busy');
      updateAutofileButton();
      hideBootOverlay();
      showToast('Nothing filed - try refreshing the library first.');
    }
  } catch (err) {
    const btn = document.getElementById('autofile-btn');
    if (btn) { btn.disabled = false; btn.classList.remove('is-busy'); }
    hideBootOverlay();
  }
}

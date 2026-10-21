// --- File download queue -> Video\ (pipeline/file_queue.py) --------------
// Deliberate, explicit filing: pick exactly what to move out of the downloader
// queue into its archive folder. Not watched-gated; TV + movies;
// delete-after-watch shows are listed but unchecked by default. Filing
// something here does NOT mark it watched and does NOT change its
// "Recently Downloaded" date.
const _fqPad = n => String(n).padStart(2, '0');
let _fqPollTimer = null;

window.openFileQueue = async function() {
  if (!SERVER_MODE) { showToast('Start the local server to file the queue'); return; }
  showToast('Scanning the download queue...');
  const r = await postJSON('/api/file-queue/plan', {});
  if (!r || r.ok === false) { showToast((r && r.message) || 'Could not build the plan.'); return; }
  renderFileQueueModal(r.plan);
};

function renderFileQueueModal(plan) {
  const items = plan.items || [];
  const review = plan.unmatched || [];
  const stale = plan.stale_count || 0;
  const junk = plan.junk || [];
  const rows = items.map(it => {
    const label = it.type === 'TV'
      ? `S${_fqPad(it.season)}E${_fqPad(it.episode)}` + (it.episode_title ? ' ' + escapeHtml(it.episode_title) : '')
      : '(movie)';
    let tags = '';
    if (it.retention === 'delete_after_watch') tags += '<span class="badge">🗑 delete after watching</span> ';
    if (it.collision) tags += it.collision.action === 'replace'
      ? '<span class="badge ready">↑ replaces lower-quality existing</span> '
      : '<span class="badge">= keeps existing copy</span> ';
    if (it.companion_pending) tags += '<span class="badge" title="A companion tool has this queued to file on its side">🔎 Companion pending</span> ';
    return `<label class="fq-row" style="display:grid; grid-template-columns:20px 1fr auto; gap:8px; align-items:start; padding:6px 0; border-bottom:1px solid var(--border); font-size:12.5px;">
      <input type="checkbox" class="fq-item" data-src="${escapeHtml(it.src_video)}" ${it.default_selected ? 'checked' : ''} onchange="fqRecount()">
      <span><strong>${escapeHtml(it.show_title)}</strong> ${label}
        <br><span style="color:var(--text-dim);">&rarr; ${escapeHtml(it.dest_full)}</span></span>
      <span style="text-align:right; white-space:nowrap;">${tags}<span style="color:var(--text-dim);">${formatBytes(it.size_bytes || 0)}</span></span>
    </label>`;
  }).join('');
  const reviewHtml = review.length ? `<div style="margin-top:12px; font-size:12px; color:var(--text-dim);">
      <strong>${review.length} left in place</strong> (not confidently identified):<br>
      ${review.map(u => `&bull; ${escapeHtml(u.show_title)} — ${escapeHtml(u.reason)}`).join('<br>')}</div>` : '';
  const staleHtml = stale ? `<div style="margin-top:8px; font-size:12px; color:var(--text-dim);">
      ${stale} stale queue folder(s) with no video (already filed elsewhere) — a library refresh clears those from this list.</div>` : '';
  // Empty / junk-only / sample-only release folders - default-on, same "move never delete"
  // convention as Purge.
  const junkHtml = junk.length ? `<label style="display:flex; gap:6px; align-items:flex-start; margin-top:14px; padding-top:10px; border-top:1px solid var(--border); font-size:12.5px;">
      <input type="checkbox" id="fq-sweep-junk" checked>
      <span>Also move <strong>${junk.length} empty/junk/sample-only</strong> queue folder(s) to Pending Deletion
        <br><span style="color:var(--text-dim); font-size:11px;">${junk.map(j => escapeHtml(j.name)).join(', ')}</span></span>
    </label>` : '';
  document.getElementById('modal-box').className = 'modal-box modal-box-roomy';
  document.getElementById('modal-box').innerHTML = `
    <div class="modal-header">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      <div class="title">File download queue &rarr; Video\\</div>
      <div style="margin-top:6px; font-size:13px; color:var(--text-dim);">${items.length} ready &middot; ${review.length} need review &middot; ${stale} stale &middot; ${junk.length} junk</div>
    </div>
    <div class="entries" style="display:block; max-height:68vh; overflow-y:auto; padding:12px 16px;">
      ${items.length ? `<label style="display:flex; gap:6px; align-items:center; font-size:12px; color:var(--text-dim); margin-bottom:6px;">
        <input type="checkbox" id="fq-all" checked onchange="fqToggleAll(this)"> select / deselect all</label>
      <div id="fq-list">${rows}</div>` : '<p style="color:var(--text-dim);">Nothing in the queue is ready to file. Try a library refresh first.</p>'}
      ${staleHtml}
      ${reviewHtml}
      ${junkHtml}
      ${(items.length || junk.length) ? `<div style="margin-top:16px; display:flex; gap:10px; align-items:center;">
        <button class="header-btn" id="fq-go" type="button" onclick="submitFileQueue()">Move selected (<span id="fq-count">0</span>)</button>
        <span id="fq-status" style="font-size:12px; color:var(--text-dim);"></span></div>` : ''}
    </div>`;
  document.getElementById('modal-overlay').classList.add('open');
  fqRecount();
}

window.fqToggleAll = function(cb) {
  document.querySelectorAll('.fq-item').forEach(c => { c.checked = cb.checked; });
  fqRecount();
};

window.fqRecount = function() {
  const n = document.querySelectorAll('.fq-item:checked').length;
  const el = document.getElementById('fq-count');
  if (el) el.textContent = n;
  const all = document.getElementById('fq-all');
  if (all) all.checked = n > 0 && n === document.querySelectorAll('.fq-item').length;
};

window.submitFileQueue = async function() {
  const selected = [...document.querySelectorAll('.fq-item:checked')].map(c => c.getAttribute('data-src'));
  const sweepBox = document.getElementById('fq-sweep-junk');
  const sweepJunk = !!(sweepBox && sweepBox.checked);
  if (!selected.length && !sweepJunk) { showToast('Nothing selected.'); return; }
  const parts = [];
  if (selected.length) parts.push(`move ${selected.length} file(s) into Video\\`);
  if (sweepJunk) parts.push('sweep the junk queue folders to Pending Deletion');
  if (!confirm(`${parts.join(' and ')}?\n\nCopies are verified against TMDB. A destination that already exists is only replaced when the new copy is higher quality — the old one goes to Pending Deletion.`)) return;
  const go = document.getElementById('fq-go');
  if (go) go.disabled = true;
  showBootOverlay('Starting...');
  const r = await postJSON('/api/file-queue/execute', { selected, sweep_junk: sweepJunk });
  if (!r || r.ok === false) { if (go) go.disabled = false; hideBootOverlay(); showToast((r && r.message) || 'Could not start.'); return; }
  pollFileQueueStatus();
};

async function pollFileQueueStatus() {
  clearTimeout(_fqPollTimer);
  try {
    const resp = await fetch('/api/file-queue/status', { headers: { 'X-Auth-Token': AUTH_TOKEN } });
    const st = await resp.json();
    const line = document.getElementById('fq-status');
    if (st.running) {
      const done = st.filed + st.replaced + st.skipped + st.failed;
      if (line) line.textContent = `Filing... (${done} of ${st.total})`;
      setBootOverlayProgress(`Filing... (${done} of ${st.total})`, done, st.total);
      _fqPollTimer = setTimeout(pollFileQueueStatus, 1500);
      return;
    }
    if (st.error) { hideBootOverlay(); showErrorDialog('File queue failed', st.error); return; }
    const parts = [`${st.filed} filed`];
    if (st.replaced) parts.push(`${st.replaced} replaced`);
    if (st.skipped) parts.push(`${st.skipped} skipped`);
    if (st.swept) parts.push(`${st.swept} junk folder(s) swept`);
    if (st.failed) parts.push(`${st.failed} failed`);
    showToast(parts.join(', ') + (st.filed || st.replaced ? '. Refreshing library...' : '.'));
    closeModal();
    if (st.filed || st.replaced) pollRefreshStatus('sync');
    else hideBootOverlay();
  } catch (e) {
    const go = document.getElementById('fq-go');
    if (go) go.disabled = false;
    hideBootOverlay();
  }
}

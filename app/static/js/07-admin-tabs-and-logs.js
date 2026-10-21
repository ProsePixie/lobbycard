window.switchAdminTab = function(tab) {
  document.querySelectorAll('.admin-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.getElementById('admin-tab-fix').style.display = tab === 'fix' ? 'block' : 'none';
  document.getElementById('admin-tab-add').style.display = tab === 'add' ? 'block' : 'none';
  document.getElementById('admin-tab-log').style.display = tab === 'log' ? 'block' : 'none';
  if (tab === 'log') loadAdminLogTab();
};

// Plain-English names for the pipeline scripts - matches PIPELINE_SCRIPTS
// in web_server.py (kept as a display-only lookup, not imported, since a
// mismatch here just means a script falls back to its filename, not a
// real bug). Anything not listed gets its filename title-cased instead of
// a blank line, so a future script addition never looks broken.
const SCRIPT_FRIENDLY_NAMES = {
  'pipeline/heal_reorg_state.py': 'Reconciling reorg-side file moves',
  'pipeline/scan_media.py': 'Scanning your library for new/changed files',
  'pipeline/enrich_tmdb.py': 'Matching titles against TMDB',
  'pipeline/consolidate.py': 'Merging digital/physical/Amazon copies',
  'pipeline/enrich_wikipedia.py': 'Wikipedia fallback for unmatched titles',
  'pipeline/enrich_epguides.py': 'Epguides cross-reference',
  'pipeline/enrich_physical.py': 'Updating the physical collection',
  'pipeline/enrich_amazon.py': 'Updating Amazon purchases',
  'pipeline/enrich_tv_coverage.py': 'Checking for missing episodes/seasons',
  'pipeline/detect_runtime_mismatch.py': 'Checking for mismatched runtimes',
  'plans/build_quarantine_plan.py': 'Planning quarantine moves',
};
function friendlyScriptName(script) {
  if (SCRIPT_FRIENDLY_NAMES[script]) return SCRIPT_FRIENDLY_NAMES[script];
  const base = (script || '').split('/').pop().replace(/\.py$/, '').replace(/_/g, ' ');
  return base ? base.charAt(0).toUpperCase() + base.slice(1) : script;
}
// The last non-blank line of a script's own stdout - most of this
// project's pipeline scripts already end with a one-line summary print
// (counts matched/skipped/updated), so this surfaces that same line as a
// human-readable highlight instead of parsing script-specific output
// formats one by one.
function lastSummaryLine(stdout) {
  const lines = (stdout || '').split('\n').map(l => l.trim()).filter(Boolean);
  return lines.length ? lines[lines.length - 1] : '';
}

// Refreshes both halves every time the tab opens (not cached) - the whole
// point is "what's happening / what just happened," which is only useful
// if it's current. Two independent fetches, rendered together: the
// structured per-script refresh outcome (_refresh_status, already
// captured server-side, just never surfaced beyond a bare error string
// before this) and the raw server.log tail (new - previously nowhere to
// read this without opening the file by hand).
async function loadAdminLogTab() {
  const container = document.getElementById('admin-log-content');
  if (!SERVER_MODE) { container.innerHTML = '<p class="admin-note">Start the local server to view this.</p>'; return; }
  container.innerHTML = '<p class="admin-note">Loading...</p>';

  const [statusResp, logResp] = await Promise.all([
    fetch('/api/refresh/status', { headers: { 'X-Auth-Token': AUTH_TOKEN } }).then(r => r.json()).catch(() => null),
    fetch('/api/server-log', { headers: { 'X-Auth-Token': AUTH_TOKEN } }).then(r => r.json()).catch(() => null),
  ]);

  let html = '<div class="admin-field"><label class="admin-label">Last refresh</label>';
  if (!statusResp) {
    html += "<p class=\"admin-note\">Couldn't reach the server.</p>";
  } else if (statusResp.running) {
    html += `<p class="admin-note">Running now - step ${(statusResp.log || []).length}.</p>`;
  } else if (!statusResp.started_at) {
    html += '<p class="admin-note">No refresh has run yet this session.</p>';
  } else {
    if ((statusResp.log || []).length) {
      const total = statusResp.log.length;
      const failCount = statusResp.log.filter(e => e.returncode !== 0).length;
      const headline = failCount === 0
        ? `✅ All ${total} step${total === 1 ? '' : 's'} completed cleanly`
        : `⚠️ ${failCount} of ${total} step${total === 1 ? '' : 's'} had a problem`;
      const highlights = statusResp.log.map(entry => {
        const bad = entry.returncode !== 0;
        const line = lastSummaryLine(entry.stdout);
        return `<div class="admin-log-highlight${bad ? ' admin-log-bad' : ''}">${bad ? '⚠️' : '✅'} <strong>${escapeHtml(friendlyScriptName(entry.script))}</strong>${line ? ' — ' + escapeHtml(line) : ''}</div>`;
      }).join('');
      html += `<div class="admin-log-summary"><p class="admin-log-headline">${headline}</p>` +
        `<p class="admin-note">Started ${escapeHtml(statusResp.started_at)}, finished ${escapeHtml(statusResp.finished_at || '-')}.</p>` +
        highlights + '</div>';
    } else {
      const outcome = statusResp.error ? `⚠️ Failed: ${escapeHtml(statusResp.error)}` : '✅ Completed successfully';
      html += `<p class="admin-note">${outcome}<br>Started ${escapeHtml(statusResp.started_at)}, finished ${escapeHtml(statusResp.finished_at || '-')}.</p>`;
    }
    if ((statusResp.log || []).length) {
      html += '<label class="admin-label" style="margin-top:10px; display:block;">Full detail per step</label>';
      html += '<div class="admin-refresh-log">' + statusResp.log.map(entry => {
        const bad = entry.returncode !== 0;
        const body = bad ? `STDOUT:\\n${entry.stdout || '(empty)'}\\n\\nSTDERR:\\n${entry.stderr || '(empty)'}` : (entry.stdout || '').slice(-300);
        return `<details${bad ? ' open' : ''}><summary class="${bad ? 'admin-log-bad' : ''}">${bad ? '⚠️' : '✅'} ${escapeHtml(entry.script)} (exit ${entry.returncode})</summary><pre class="error-message-box">${escapeHtml(body)}</pre></details>`;
      }).join('') + '</div>';
    }
  }
  html += '</div>';

  html += '<div class="admin-field"><label class="admin-label">Server log (last ~50KB)</label>';
  if (!logResp || logResp.ok === false) {
    html += `<p class="admin-note">${escapeHtml((logResp && logResp.message) || "Couldn't load server.log.")}</p>`;
  } else {
    const text = logResp.text || '(empty)';
    html += (logResp.truncated ? '<p class="admin-note">Showing the end of the file - earlier entries are still on disk at logs\\server.log.</p>' : '')
      + `<pre class="error-message-box admin-server-log">${escapeHtml(text)}</pre>`;
  }
  html += '</div>';

  container.innerHTML = html;
}

function adminPosterThumb(posterPath, title) {
  return posterPath
    ? `<img src="${tmdbImageUrl('poster', posterPath)}" alt="" loading="lazy" decoding="async" onerror="onTmdbImageError(this)">`
    : `<div class="admin-pick-placeholder">${escapeHtml(title || 'No poster')}</div>`;
}

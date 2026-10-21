// Loading overlay behaviour (2026-10): constellation spinner, drifting starfield +
// nebula behind the card, step-by-step detail with a weighted hairline progress
// bar, and a "taking longer than usual" escape hatch. Injected into the page by
// render_engine.py as plain JS (no template braces); keeps the global names the
// rest of the page already calls: showBootOverlay, hideBootOverlay,
// setBootOverlayProgress, updateBootOverlay.

const BOOT_NODES = [[18, 66], [34, 34], [56, 50], [72, 20], [84, 62], [52, 84]];
const BOOT_COLORS = ['#66ccff', '#a78bfa', '#f2b134', '#4ade80', '#fb7185', '#f472b6'];
const BOOT_CYCLE = 3.6;
const BOOT_STUCK_MS = 6 * 60 * 1000;
const BOOT_STAR_PATH = 'M0 -5 L1.3 -1.3 L5 0 L1.3 1.3 L0 5 L-1.3 1.3 L-5 0 L-1.3 -1.3Z';

let _bootSpinReady = false;
let _bootStuck = { sig: '', changedAt: 0, snoozeUntil: 0 };

function _bootTwinkle(x, y, delay, scale) {
  return '<g transform="translate(' + x + ' ' + y + ') scale(' + scale + ')"><path class="bo-tw" style="animation-delay:' + delay + 's" d="' + BOOT_STAR_PATH + '" fill="#fff"/></g>';
}

function bootSpinnerSvg() {
  const N = BOOT_NODES, C = BOOT_COLORS;
  const cum = [0];
  for (let i = 1; i < N.length; i++) cum.push(cum[i - 1] + Math.hypot(N[i][0] - N[i - 1][0], N[i][1] - N[i - 1][1]));
  const total = cum[cum.length - 1];
  const at = cum.map(c => (BOOT_CYCLE * c / total).toFixed(2));
  const path = 'M' + N.map(p => p.join(' ')).join(' L');
  let s = '<svg viewBox="0 0 100 100"><path d="' + path + '" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="1"/>';
  for (let k = 0; k < N.length - 1; k++) {
    s += '<line class="bo-seg" pathLength="1" x1="' + N[k][0] + '" y1="' + N[k][1] + '" x2="' + N[k + 1][0] + '" y2="' + N[k + 1][1] + '" stroke="' + C[k + 1] + '" style="animation-delay:' + at[k] + 's"/>';
  }
  for (let i = 0; i < N.length; i++) {
    s += '<circle class="bo-node" cx="' + N[i][0] + '" cy="' + N[i][1] + '" r="2.2" fill="' + C[i] + '" style="animation-delay:' + at[i] + 's"/>';
    s += '<g transform="translate(' + N[i][0] + ' ' + N[i][1] + ')"><circle class="bo-ring" r="6" fill="none" stroke="' + C[i] + '" stroke-width="1.4" style="animation-delay:' + at[i] + 's"/>'
       + '<path class="bo-flash" d="' + BOOT_STAR_PATH + '" fill="' + C[i] + '" style="animation-delay:' + at[i] + 's"/></g>';
  }
  s += '<circle class="bo-trav" r="2.4" fill="#fff" style="offset-path:path(\'' + path + '\')"/>';
  s += _bootTwinkle(10, 18, 0.2, 0.8) + _bootTwinkle(90, 86, 1.4, 0.8) + _bootTwinkle(90, 12, 2.2, 0.6);
  return s + '</svg>';
}

function bootEnsureSpinner() {
  if (_bootSpinReady) return;
  const el = document.getElementById('boot-spin');
  if (el) { el.innerHTML = bootSpinnerSvg(); _bootSpinReady = true; }
}

// ---- background: fixed twinkling stars + slow nebula (canvas, ~30fps, only while shown) ----
let _bootFxTimer = null, _bootStars = null, _bootFxT0 = 0;
const _bootReduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function _bootFxSize(cv) { cv.width = window.innerWidth; cv.height = window.innerHeight; }
function _bootFxDraw(cv, t, dt) {
  const cx = cv.getContext('2d'), W = cv.width, H = cv.height;
  cx.clearRect(0, 0, W, H);
  const blobs = [['102,204,255', 0.0, 0.55, 0.55, 0.14], ['167,139,250', 2.1, 0.3, 0.7, 0.12], ['74,222,128', 4.2, 0.7, 0.3, 0.07]];
  blobs.forEach(function (o) {
    const x = W * (o[2] + 0.07 * Math.sin(t / 9000 + o[1])), y = H * (o[3] + 0.08 * Math.cos(t / 11000 + o[1])), r = Math.max(W, H) * 0.5;
    const g = cx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, 'rgba(' + o[0] + ',' + o[4] + ')'); g.addColorStop(1, 'rgba(' + o[0] + ',0)');
    cx.fillStyle = g; cx.fillRect(0, 0, W, H);
  });
  // Stars never change position (sideways drift made some people queasy); they only swell and
  // fade. The brighter ones get a faint halo and a short glint at the top of their swell.
  for (const s of _bootStars) {
    const w = 0.5 + 0.5 * Math.sin(t / 1000 * s.sp + s.p), a = 0.2 + 0.7 * w;
    const X = s.x * W, Y = s.y * H, r = s.r * (0.85 + 0.3 * a);
    if (s.b && a > 0.4) {
      const hg = cx.createRadialGradient(X, Y, 0, X, Y, r * 4.5);
      hg.addColorStop(0, 'rgba(' + s.c + ',' + (0.24 * a).toFixed(2) + ')'); hg.addColorStop(1, 'rgba(' + s.c + ',0)');
      cx.fillStyle = hg; cx.beginPath(); cx.arc(X, Y, r * 4.5, 0, 6.283); cx.fill();
    }
    cx.fillStyle = 'rgba(' + s.c + ',' + a.toFixed(2) + ')';
    cx.beginPath(); cx.arc(X, Y, r, 0, 6.283); cx.fill();
    if (s.b && a > 0.62) {
      const L = (a - 0.62) / 0.38 * 12;
      cx.strokeStyle = 'rgba(' + s.c + ',' + (0.65 * (a - 0.62) / 0.38).toFixed(2) + ')'; cx.lineWidth = 1;
      cx.beginPath(); cx.moveTo(X - L, Y); cx.lineTo(X + L, Y); cx.moveTo(X, Y - L); cx.lineTo(X, Y + L); cx.stroke();
    }
  }
}
function bootFxStart() {
  const cv = document.getElementById('boot-fx');
  if (!cv || _bootFxTimer) return;
  if (!_bootStars) {
    _bootStars = [];
    // Density follows the screen so a phone and a 4K monitor both look like the same sky.
    const n = Math.max(90, Math.min(380, Math.round(window.innerWidth * window.innerHeight / 6500)));
    const hues = ['255,175,95', '255,125,150', '110,215,255', '190,155,255', '125,235,175'];
    for (let k = 0; k < n; k++) {
      const big = Math.random() < 0.11, tint = Math.random() < 0.13;
      _bootStars.push({
        x: Math.random(), y: Math.random(), r: (Math.random() * 1.25 + 0.4) * (big ? 1.45 : 1),
        sp: 0.4 + Math.random() * 0.9, p: Math.random() * 6.28,
        b: big || (tint && Math.random() < 0.5),
        c: tint ? hues[Math.floor(Math.random() * hues.length)] : '255,255,255',
      });
    }
  }
  _bootFxSize(cv);
  _bootFxT0 = performance.now();
  let last = _bootFxT0;
  const tick = function () {
    const now = performance.now(), dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    _bootFxDraw(cv, now, dt);
    _bootFxTimer = _bootReduce ? null : setTimeout(tick, 33);
  };
  _bootFxTimer = 1; tick();
}
function bootFxStop() {
  clearTimeout(_bootFxTimer); _bootFxTimer = null;
  const cv = document.getElementById('boot-fx');
  if (cv) cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
}
window.addEventListener('resize', function () { const cv = document.getElementById('boot-fx'); if (cv && _bootFxTimer) _bootFxSize(cv); });

// ---- text + progress ----
// reserve = keep the detail/explanation lines' space even when empty (a refresh, where steps
// with and without counts alternate, so the card never jumps); a plain "Saving..." collapses them.
function _bootSet(step, title, detail, what, pct, reserve) {
  const q = function (id) { return document.getElementById(id); };
  q('boot-step').textContent = step || '';
  q('boot-title').textContent = title || '';
  q('boot-detail').innerHTML = detail ? escapeHtml(detail) : '&nbsp;';
  q('boot-detail').hidden = !detail && !reserve;
  q('boot-what').textContent = what || '';
  q('boot-what').hidden = !what && !reserve;
  q('boot-hair-fill').style.width = Math.max(2, Math.min(100, pct)) + '%';
}
function _bootStuckCheck(sig) {
  const now = Date.now(), box = document.getElementById('boot-stuck');
  if (!box) return;
  if (sig !== _bootStuck.sig) { _bootStuck.sig = sig; _bootStuck.changedAt = now; box.hidden = true; return; }
  if (now - _bootStuck.changedAt > BOOT_STUCK_MS && now > _bootStuck.snoozeUntil) box.hidden = false;
}
function bootStuckReset() {
  _bootStuck = { sig: '', changedAt: Date.now(), snoozeUntil: 0 };
  const box = document.getElementById('boot-stuck'); if (box) box.hidden = true;
}
document.addEventListener('click', function (ev) {
  if (ev.target && ev.target.id === 'boot-keep') { ev.preventDefault(); _bootStuck.snoozeUntil = Date.now() + BOOT_STUCK_MS; document.getElementById('boot-stuck').hidden = true; }
  if (ev.target && ev.target.id === 'boot-hide') { ev.preventDefault(); hideBootOverlay(); }
});

// Generic actions (Saving..., Filing... (3 of 10)): title + bar, no step detail.
function setBootOverlayProgress(label, done, total) {
  const el = document.getElementById('boot-overlay');
  if (!el || el.hidden) return;
  const pct = total ? Math.round((done / total) * 100) : 6;
  _bootSet('', label, '', '', pct);
}
let _bootHideT = null;
function showBootOverlay(label) {
  const el = document.getElementById('boot-overlay');
  if (!el) return;
  clearTimeout(_bootHideT);
  el.classList.remove('bo-out');
  if (el.hidden) { el.classList.add('bo-in'); setTimeout(function () { el.classList.remove('bo-in'); }, 450); }
  el.hidden = false;
  bootEnsureSpinner(); bootFxStart(); bootStuckReset();
  _bootSet('', label || 'Working...', '', '', 6);
  startLoadingHint();
}
function hideBootOverlay() {
  const el = document.getElementById('boot-overlay');
  if (!el || el.hidden) { stopLoadingHint(); bootFxStop(); return; }
  stopLoadingHint();
  el.classList.add('bo-out');                       // fade out, then actually hide
  clearTimeout(_bootHideT);
  _bootHideT = setTimeout(function () { el.hidden = true; el.classList.remove('bo-out'); bootFxStop(); }, 380);
}
// Older server without a step plan: the original "N of M steps done" line.
function updateBootOverlay(running, done, total) {
  setBootOverlayProgress(running ? done + ' of ' + total + ' steps done' : 'Finishing up...', done, total);
}
// A refresh in progress: which step, what it is doing, live counts, weighted bar.
function updateBootOverlayFromStatus(status) {
  const el = document.getElementById('boot-overlay');
  if (!el || el.hidden) return;
  bootEnsureSpinner();
  const steps = status.steps || [];
  if (!steps.length) { updateBootOverlay(status.running, (status.log || []).length, status.total || 8); return; }
  const done = (status.log || []).length;
  const idx = Math.min(done, steps.length - 1), st = steps[idx], pg = status.progress;
  let sub = 0, detail = '';
  if (pg && pg.total > 0) {
    sub = Math.min(1, pg.done / pg.total);
    detail = pg.done.toLocaleString() + ' of ' + pg.total.toLocaleString() + ' ' + (pg.noun || 'items') + (pg.note ? ' · ' + pg.note : '');
  }
  let wTotal = 0, wDone = 0;
  steps.forEach(function (s, i) { const w = s.weight || 1; wTotal += w; if (i < idx) wDone += w; });
  const frac = (wDone + (st.weight || 1) * sub) / wTotal;
  const secs = status.started_at ? Math.max(0, Math.floor((Date.now() - Date.parse(status.started_at)) / 1000)) : 0;
  const clock = Math.floor(secs / 60) + ':' + String(secs % 60).padStart(2, '0');
  _bootSet('Step ' + (idx + 1) + ' of ' + steps.length + ' · ' + clock, st.name, detail, st.what, frac * 100, true);
  _bootStuckCheck(done + '|' + (pg ? pg.done + '|' + pg.note : ''));
}
// The wait after the last step: the page reloads and draws itself.
function bootShowDrawing() {
  _bootSet('Almost there', 'Drawing your library', 'Reloading the page', 'Putting your posters and cards on screen.', 100, true);
  const box = document.getElementById('boot-stuck'); if (box) box.hidden = true;
}

// ---- phone remote: can it still reach the app? ----
// Two failed polls in a row (about 6 seconds) show the "Can't reach the app" card; the first
// good poll clears it. After 30 seconds it adds a hint about the PC being asleep or off Wi-Fi.
let _linkFails = 0, _linkDownSince = 0;
function noteRemoteLink(ok) {
  if (typeof REMOTE_ONLY === 'undefined' || !REMOTE_ONLY) return;
  const el = document.getElementById('remote-offline');
  if (!el) return;
  if (ok) { _linkFails = 0; _linkDownSince = 0; el.hidden = true; return; }
  _linkFails++;
  if (_linkFails < 2) return;
  if (!_linkDownSince) _linkDownSince = Date.now();
  el.hidden = false;
  document.getElementById('remote-offline-more').hidden = (Date.now() - _linkDownSince) < 30000;
}

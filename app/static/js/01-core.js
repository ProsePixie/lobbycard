const ROOT_INDEX = {
  [@@queue_label_json@@]: 0, "Archive": 1, "Physical": 2, "Amazon": 3, "Netflix": 4, "Prime": 5,
};
// Display-layer only - the keys here are the real internal root values
// (used for root-dot indexing, the legend, etc. everywhere else in this
// file) and must never change. Only .label/.hint, shown via native title=
// tooltips, are meant to be edited.
const ROOT_LABELS_FRIENDLY = {
  [@@queue_label_json@@]: { label: "New Downloads", hint: "Freshly finished downloads, not yet reviewed" },
  "Archive": { label: "My Library", hint: "Your permanent, organized collection" },
  "Physical": { label: "Physical Media", hint: "DVD/Blu-ray you own on the shelf - not a digital file" },
  "Amazon": { label: "Amazon Purchase", hint: "Bought on Prime Video - not downloaded, opens Amazon to watch" },
  "Netflix": { label: "Netflix", hint: "Streaming on your Netflix subscription - not a file, opens Netflix to watch" },
  "Prime": { label: "Prime Video", hint: "Streaming on your Prime subscription - not a file, opens Prime Video to watch" },
};
function friendlyRootTitle(root) {
  const f = ROOT_LABELS_FRIENDLY[root];
  return f ? `${f.label} — ${f.hint}` : root;
}
// Card-level watch/lifecycle status dot - what the front-page legend and
// every card's colored dot actually mean now. Deliberately a separate
// system from ROOT_LABELS_FRIENDLY/root-dot above (which stays reserved for
// per-episode physical file location, still shown inside each expanded
// show) - see the .status-dot CSS comment for why the colors don't overlap.
const SHOW_STATUS_DEFS = {
  unwatched: { css: 's-unwatched', label: 'Unwatched', hint: 'Still has unwatched content' },
  progress:  { css: 's-progress', label: 'In Progress', hint: 'Partially watched' },
  active:    { css: 's-active', label: 'Active', hint: 'Still airing - TMDB lists this as a returning/in-production series' },
  ended:     { css: 's-ended', label: 'Ended', hint: 'No longer airing' },
  watched:   { css: 's-watched', label: 'Watched', hint: 'Fully caught up' },
  airing_soon: { css: 's-airing-soon', label: 'Airs Soon', hint: 'Has a real next-episode air date on TMDB' },
};
const TV_ACTIVE_STATUSES = new Set(['Returning Series', 'In Production']);
const TV_ENDED_STATUSES = new Set(['Ended', 'Canceled']);
// Independent boolean flags, not a mutually-exclusive bucket - a show
// commonly matches more than one at once (still airing AND partway through,
// canceled AND never started, etc), so every applicable dot renders, not
// just one "winning" status.
function computeShowStatusKeys(show) {
  const total = show.entries.length;
  const watchedCount = show.entries.filter(e => e.watched).length;
  const keys = [];
  if (total > 0 && watchedCount < total) keys.push('unwatched');
  if (watchedCount > 0 && watchedCount < total) keys.push('progress');
  if (total > 0 && watchedCount === total) keys.push('watched');
  if (show.type === 'TV' && TV_ACTIVE_STATUSES.has(show.tmdb_status)) keys.push('active');
  if (show.type === 'TV' && TV_ENDED_STATUSES.has(show.tmdb_status)) keys.push('ended');
  const ne = show.tmdb_next_episode;
  if (ne && ne.air_date && ne.air_date >= new Date().toISOString().slice(0, 10)) keys.push('airing_soon');
  return keys;
}
function statusDotHtml(show) {
  return computeShowStatusKeys(show).map(key => {
    const def = SHOW_STATUS_DEFS[key];
    const hint = (key === 'ended' && show.tmdb_status === 'Canceled')
      ? 'Canceled - no more episodes are coming'
      : def.hint;
    return `<span class="root-dot status-dot ${def.css}" title="${escapeHtml(def.label + ' — ' + hint)}"></span>`;
  }).join('');
}
const shows = JSON.parse(document.getElementById('media-data').textContent);
const SERVER_MODE = @@server_mode_json@@;
const AUTH_TOKEN = @@auth_token_json@@;
// Snapshot of the startup/nightly refresh, taken the instant this page was
// rendered - see the boot-overlay init below. {running:false} on every
// normal load; only real when this page happened to load mid-refresh.
const REFRESH_STATUS = @@refresh_status_json@@;
// Loading-screen filler text pool (see app/assets/loading_hints.json) -
// flattened into one {cat, text} array once, here, rather than
// re-flattening on every rotation tick. Category is kept (not just the
// text) purely to pick a little label prefix for it - see
// LOADING_HINT_LABELS / startLoadingHint() below.
const LOADING_HINTS = @@loading_hints_json@@;
const LOADING_HINTS_POOL = Object.entries(LOADING_HINTS).flatMap(
  ([cat, lines]) => lines.map(text => ({ cat, text }))
);
// Intentionally impish on "insults" - the whole point of that category is
// the joke of a scathing line labeled like a compliment.
const LOADING_HINT_LABELS = @@hint_labels_json@@;
// Names and household members from the active pack (see pipeline/branding.py).
const APP_NAME = @@app_name_json@@;
// Non-empty in a static build: images are plain files under this relative folder.
const IMAGE_BASE = @@image_base_json@@;
const APP_TAGLINE = @@app_tagline_json@@;
const PEOPLE = @@people_json@@;
const FRANCHISE = @@franchise_json@@;
// "Am I the phone remote?" - the page was opened over a LAN IP / hostname,
// not localhost. That's the deliberate split: at the desktop you open
// 127.0.0.1, the phone connects to the machine's LAN address. Only then
// does the "Play on the TV" (cast to server) button show.
const IS_REMOTE = SERVER_MODE && !['localhost', '127.0.0.1', '[::1]', '::1', ''].includes(location.hostname);
if (IS_REMOTE) document.documentElement.classList.add('is-remote');
// True only when this exact page load arrived on REMOTE_PORT (see that
// constant's own comment in app/web_server.py) - a genuinely different
// port, not a path, so a phone bookmarked straight to it can never land on
// the full library grid by accident. Orthogonal to IS_REMOTE above (that's
// "is this a LAN device," this is "which door did it come through") -
// same page, same script, same everything; the <html class="remote-only">
// that actually hides .wrap is rendered server-side (see render_page()'s
// own comment) so it applies from first paint, no JS/flash involved - this
// constant is just for any JS that wants to branch on it too.
const REMOTE_ONLY = @@remote_only_json@@;
// What is this being shown on? Only a real remote device (IS_REMOTE)
// gets a density adjustment - localhost is always the full desktop.
// The screen's shorter side is orientation-independent: iPhone ~390,
// iPad ~810, iPad Pro 12.9" 1024, laptop 800+.
const DEV = (() => {
  if (!IS_REMOTE) return 'desktop';
  // innerWidth/innerHeight are always reliable; screen.* is a fallback
  // (some embedded webviews report 0). min() of the two is orientation-
  // independent: iPhone ~390, iPad portrait/landscape ~768-1024.
  const w = window.innerWidth || screen.width || 0;
  const h = window.innerHeight || screen.height || w;
  const shortSide = Math.min(w || 9999, h || 9999);
  if (shortSide < 500) return 'phone';
  if (shortSide < 1100) return 'tablet';
  const touch = (navigator.maxTouchPoints || 0) > 0 || 'ontouchstart' in window;
  return touch ? 'tablet' : 'desktop';
})();
if (DEV === 'phone') document.documentElement.classList.add('dev-phone');
else if (DEV === 'tablet') document.documentElement.classList.add('dev-tablet');
const TRAVEL_MODE = @@travel_mode_json@@;
// personal (default): branding is baked & locked at export time, gear
// hidden - shareable: a generic-defaults copy meant for someone else's
// machine, branding editable behind the gear (plan item 17 / SPEC 7.6).
const TRAVEL_SHARE_MODE = @@travel_share_mode_json@@;
// Only ever non-empty when travel_mode=True at generation time (see
// render_page()'s own guard) - a live TMDB search from the "Scan for New
// Files" feature needs a key available client-side since the portable app
// has no server of its own to proxy the request through. Never embedded
// in the main server-mode app or the static generate_html.py snapshot -
// both call render_page() without this argument at all, so it defaults
// to "".
const TRAVEL_TMDB_API_KEY = @@travel_tmdb_api_key_json@@;
// Local cache only exists behind the Flask server (see app/web_server.py's
// /tmdb-image/ route and pipeline/tmdb_image_cache.py) - the static,
// server-less HTML snapshot (app/generate_html.py, server_mode=False) has
// nothing to serve that route, so it falls back to the same live TMDB CDN
// URLs used before the local cache existed. Same reasoning as
// wiki_image_path's existing `&& SERVER_MODE` gate elsewhere in this file.
const TMDB_CDN_SIZE = { poster: 'w500', backdrop: 'w1280', cast: 'w185', rec_poster: 'w185' };
// A "missing" season isn't always missing everywhere - see
// pipeline/enrich_tv_coverage.py's physical/Amazon cross-reference.
// Shared so every missing-seasons display (badges, chips, the download
// digest) agrees on what "covered elsewhere" means.
function isSeasonCoveredElsewhere(m) {
  return !!(m.covered_physically || m.covered_by_amazon);
}
function coveredElsewhereLabel(m) {
  if (m.covered_physically && m.covered_by_amazon) return 'on disc + Amazon';
  if (m.covered_physically) return 'on disc';
  if (m.covered_by_amazon) return 'on Amazon';
  return '';
}
function tmdbImageUrl(kind, path) {
  if (!path) return '';
  // rec_poster is deliberately never locally cached, even by the main
  // app's own data\tmdb_images\ (see pipeline\tmdb_image_cache.py's own
  // comment - recommendations are for titles you don't own, caching those
  // defeated the whole "offline copy of your collection" point). So
  // build_travel_plan.py never had one to copy onto the travel drive
  // either - a local Images/rec_poster/ path here would always 404. Go
  // straight to the live CDN instead (2026-08-27, at the user's request -
  // "More like this" should actually show real posters when there's a
  // connection, not always fall back to a placeholder for a file that
  // structurally never exists).
  if (TRAVEL_MODE && kind === 'rec_poster') {
    return `https://image.tmdb.org/t/p/${TMDB_CDN_SIZE[kind]}${path}`;
  }
  if (TRAVEL_MODE) {
    // Copied onto the travel drive under Images/{kind}/{filename} by
    // build_travel_plan.py, mirroring data/tmdb_images/'s own layout - a
    // plain relative path resolves fine since this is a real local file
    // next to Travel Library.html, not launching an external OS handler
    // the way openvlc/openfolder links need a custom protocol for.
    return `Images/${kind}/${encodeURIComponent(path.replace(/^\//, ''))}`;
  }
  if (IMAGE_BASE) return `${IMAGE_BASE}/${kind}${path}`;
  return SERVER_MODE ? `/tmdb-image/${kind}${path}` : `https://image.tmdb.org/t/p/${TMDB_CDN_SIZE[kind]}${path}`;
}
function wikiImageUrl(imgFile) {
  if (!imgFile) return '';
  if (TRAVEL_MODE) return `Images/wiki/${encodeURIComponent(imgFile)}`;
  return `/wiki-image/${encodeURIComponent(imgFile)}`;
}
// Reacts to a real failed image load - offline, TMDB down, a corrupted
// local file, doesn't matter which - rather than trying to detect
// "offline" in advance (unreliable). Swaps the <img> for a same-sized,
// same-shaped div (keeps its original class for identical width/height/
// aspect-ratio/border-radius) showing a plain "Feature Offline" label
// instead of a broken-image icon. Cast photos get a small icon instead
// of the full phrase (56px circle has no room for text) with the same
// message as a title= tooltip.
// Real, reproducible bug (2026-08-22): a genuinely transient hiccup - the
// image cache's own circuit breaker fail-fasting for a short cooldown
// after a few real network errors, or just an ordinary blip - used to
// permanently kill that one <img> for the rest of the page's life, since
// this function swapped it for a static placeholder on the very FIRST
// failure with no retry at all. Scrolling through hundreds of lazy-loaded
// images made that look like "everything past a certain point is broken
// forever," even though the underlying server/cache was fine seconds
// later. Every real website retries a failed image at least once before
// giving up - this now does the same: a few attempts with increasing
// delay (long enough to outlast the circuit breaker's own cooldown -
// see pipeline/tmdb_image_cache.py's _CIRCUIT_COOLDOWN_SECONDS, kept in
// sync with the delays below), THEN the permanent placeholder, not before.
const IMG_RETRY_DELAYS_MS = [1000, 3000, 6000];

function onTmdbImageError(img) {
  if (img.dataset.offlineFallback) return; // already gave up, don't loop
  const attempt = parseInt(img.dataset.retryAttempt || '0', 10);
  if (attempt < IMG_RETRY_DELAYS_MS.length) {
    img.dataset.retryAttempt = String(attempt + 1);
    const baseSrc = img.dataset.retryBaseSrc || img.src;
    img.dataset.retryBaseSrc = baseSrc; // set once, before any cache-busting param gets added
    setTimeout(() => {
      if (!img.isConnected) return; // scrolled out and removed/replaced by the time this fires
      const sep = baseSrc.includes('?') ? '&' : '?';
      img.src = `${baseSrc}${sep}_retry=${Date.now()}`;
    }, IMG_RETRY_DELAYS_MS[attempt]);
    return;
  }
  const div = document.createElement('div');
  div.className = img.className + ' img-offline-fallback';
  div.title = 'Feature Offline';
  div.dataset.offlineFallback = '1';
  div.textContent = img.classList.contains('cast-photo') ? '📡' : 'Feature Offline';
  img.replaceWith(div);
}
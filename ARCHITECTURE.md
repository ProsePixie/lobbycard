# Architecture

## The shape of it

```
run.py                     starts app/web_server.py, restarts it when the setup page asks
app/
  web_server.py            Flask app: pages, JSON endpoints, refresh orchestration, VLC control
  render_engine.py         render_page(): fills the page template with data and per-install values
  templates/page.html      the page skeleton
  static/app.css           the stylesheet
  static/js/NN-*.js        the application script, split by feature; the files run in name order in one scope
  setup_wizard.py          the first-run setup page and its endpoints
  state_store.py           user_state.json: the only sanctioned way to touch it
  autofile_naming.py       naming rules and TMDB-validated episode checks for filing
  generate_html.py         writes a static, read-only snapshot of the library page
pipeline/                  the refresh steps, each a small script reading/writing data/*.json
plans/                     "plan, review, then run" tools and two data builders (status, ledger)
packs/default, demo/pack   built-in look and the demo's own pack (see CONFIG.md)
extras/                    optional: YouTube block, VORTEX launcher, Windows protocol handlers
demo/make_demo.py          builds the invented sample library
tools/                     art generator, screenshots, demo recording, the static demo build
tests/                     pytest suite (unit, security, pipeline end to end, real browser)
```

Configuration is read in one place, [`pipeline/app_config.py`](pipeline/app_config.py): environment, then
`config.json`, then defaults. Names, art and flavour text come from
[`pipeline/branding.py`](pipeline/branding.py) (packs). Nothing else carries a folder, a name or a region.

## Data flow

The unit of the whole system is `data/media_db.json`: a list of *shows* (TV series or movies), each with its
files ("entries") and, once matched, its TMDB fields. A refresh rebuilds it from the disk up:

| Step | Script | What it does |
| --- | --- | --- |
| 1 | `heal_reorg_state` | carries watched state forward for files a reorg renamed |
| 2 | `scan_media` | walks the library and incoming folders, parses names into title / year / season / episode |
| 3 | `enrich_tmdb` | matches each show to TMDB (see *Matching* below) and fetches details, cast, images |
| 4 | `consolidate` | merges duplicates, flags reclaimable space |
| 5 | `enrich_wikipedia`, `enrich_epguides` | fallback identification for what TMDB can't place |
| 6 | `enrich_physical`, `enrich_amazon`, `enrich_streaming` | adds titles you own as discs, purchases or follows, with no file on disk |
| 7 | `enrich_tv_coverage` | compares owned episodes with TMDB's lists: missing episodes and seasons |
| 8 | `detect_runtime_mismatch` | movies whose file length disagrees with TMDB's (usually a wrong match) |
| 9 | `enrich_wanted`, `track_purged_shows` | shows you want but don't own, and shows you watched and deleted but still follow |
| 10 | `build_quarantine_plan`, `build_system_status`, `build_library_ledger` | duplicate plan, a status file for other tools, an add/remove ledger |

Each step is a subprocess. `web_server.run_refresh_pipeline` runs them in order and **aborts on the first
failure**, so the status file is only ever written after a clean run. A quick-scan subset
(`pipeline/refresh_scripts.py`) runs at startup.

The server never decides what is in the library at request time: `load_shows()` reads `media_db.json` plus the
optional seed-driven sources (physical, Amazon, streaming), merges them, and `render_page()` joins that with
`user_state.json` (watched marks, retention, lists, overrides) into the page.

## The page

`render_page()` produces one HTML document with the CSS, JS and the data embedded, so it works from the server,
as a static snapshot, and offline once saved. The three source files are `app/templates/page.html`,
`app/static/app.css` and `app/static/js/*.js`; per-render values (the data, the name, art, accent, household names,
franchise) are `@@name@@` placeholders filled in one pass by `_fill()`. A value that itself contains `@@x@@` is not
filled twice, and every JSON value is escaped (`_js_json`) so file names and titles can't close their own
`<script>` tag. The files were split out of a single 9,000-line Python string; the rendered page is byte-identical
to what the string produced, which is how the split was verified.

No build step, no bundler: the page is the three files. `tests/test_frontend.py` renders it, checks every
placeholder is filled, runs each inline script through `node --check`, and parses the data block.

**Scale.** The page embeds the whole library (about 600 bytes per file). Measured on an invented 2,400-title,
~28,000-file library: a 41 MB page, rendered in about one second, first grid on screen in under a second from
local disk with a 52 MB JS heap. Fine on a LAN; it is the thing to change first (lazy-loading per-title details)
if a library is much larger.

The page talks back through JSON endpoints in `web_server.py`. Every mutating endpoint calls `check_auth()`.

## Matching

`best_match` in `pipeline/enrich_tmdb.py` scores each search result as title similarity + year bonus (+0.25
exact, +0.10 off by one, -0.30 more than three years out) + a log-scaled popularity nudge capped at 0.20, drops
anything under 0.45 similarity, and returns the top. If the top two are both >= 0.85 similar and within 0.05 of
each other it also returns the runner-up; the in-place rename flow turns that into a "which one?" choice instead
of a guess. Manual corrections made in the Admin panel are stored as overrides and win over the matcher on the
next run.

## Filing and renaming safety

`pipeline/file_queue.py` builds a **plan** first (what would move where, and why anything is skipped), shows it,
and only then executes the items you select. Execution is conservative by construction:

* a file is moved, never copied-then-deleted, and never over an existing file at the destination;
* if the library already has an equal or better copy, the incoming one is skipped;
* a copy that is replaced goes to `Pending Deletion` first, so it is recoverable;
* only subtitle files that share the video's exact stem travel with it; nothing else is touched;
* on any `OSError` the source stays where it was;
* watched marks and retention choices are re-keyed to the new path in the same step.

`pipeline/rename_queue_inplace.py` renames inside the release folder with the same rules (no overwrite, no two
files claiming one name, folder renamed only when it holds a single release, and a reboot or a near-tied title
match is flagged for review rather than renamed on a guess). `plans/build_reorg_manifest.py` and its movie
sibling go further: they write a reviewable PowerShell script (PowerShell also runs on macOS and Linux) and
touch nothing themselves.

## State and atomic writes

Every shared JSON file is written the same way: temp file, then `os.replace` via
[`pipeline/atomic_write.py`](pipeline/atomic_write.py), which retries a transient lock (a Windows reader holding
the file open at the instant of the swap) and then raises. `state_store.update_state(mutator)` is the single
lock-guarded read-modify-write for `user_state.json`: HTTP handlers, the VLC watcher thread and filing all go
through it, so concurrent writers can't drop each other's changes. `user_state.json` is the one file that cannot
be rebuilt, which is why nothing else is allowed to write it.

## Process model and ports

* `run.py` is a tiny supervisor: it starts `app/web_server.py` and relaunches it when it exits with code 75 (the
  setup page's "restart").
* `web_server.py` serves the library on `MEDIABOX_PORT` and, from a second listening socket in the same Flask
  app, the remote-only page on `MEDIABOX_REMOTE_PORT`, so a phone can be bookmarked straight to the controller.
* Work that must not block a request (refresh, purge, filing, the VLC watcher, the YouTube poller) runs in
  daemon threads or subprocesses, each behind its own lock; at most one "job" runs at a time.

## Security model

It is a local app, but it can move files, so it is built as if the network were hostile:

* binds to `127.0.0.1` unless you opt in with `--lan`;
* refuses any request whose `Host` header is not `localhost`, an IP literal, or (in `--lan` mode only) a `.local` or
  machine name, which closes DNS-rebinding, where another site points its own hostname at `127.0.0.1` to read the
  page, token included;
* a random token is generated per run, embedded in the page, and required on every mutating endpoint, along with
  an `Origin` check;
* the setup routes additionally refuse any request that does not come from the same machine;
* `/api/play` and `/api/open-folder` only act on paths that are real entries in the current library, never on a
  path from the wire;
* data embedded in the page (file names, titles) is escaped so it can't close its `<script>` tag;
* a restrictive `Content-Security-Policy` names the few image hosts it needs (TMDB, Wikimedia, YouTube thumbnails
  for the optional block);
* the TMDB key lives in a file with owner-only permissions and is never sent to the page or logged.

## Platform notes

Portable: the scanner, matcher, filing, the page, the server, setup, demo mode, VLC control. Windows-only and
gated on `app_config.IS_WINDOWS`: popup window placement (Win32 / per-monitor-DPI) and stale-port cleanup.
`/api/open-folder` uses Explorer, Finder or `xdg-open`. The C# extras only build on Windows.

## How it is tested

* `tests/test_pipeline_end_to_end.py` runs `scan_media -> enrich_tmdb -> consolidate -> enrich_tv_coverage` as the
  real scripts in subprocesses against `tests/fake_tmdb.py`, a small fake TMDB server. The code reads its TMDB hosts
  from `app_config` (`MEDIABOX_TMDB_API_BASE`, `MEDIABOX_TMDB_IMAGE_BASE`), which is the only seam needed.
* `tests/test_first_run_real_library.py` is the README promise as a test: the setup page, key test, save, restart,
  the startup refresh, and the library on screen with its TMDB data.
* `tests/e2e/` drives a real browser (Playwright) against the demo server and the static build.
* `tests/test_security.py` enumerates every POST route from the URL map and requires a token on each.
* `tests/test_repo_hygiene.py` keeps the repository publishable.

## Known limits

* The script is 14 files by feature but still one global scope (they are concatenated in name order), not ES modules.
  Turning them into modules means giving the shared state (`shows`, `state`) an owner; that is the next refactor.
* The page embeds the whole library (see *Scale* above).
* Some modules still carry comments from the project's history (incident write-ups), kept because they explain why.
* The scanner is tuned to the common release-name shapes; anything stranger goes through `scan_overrides.json`.
* Python is DPI-unaware and the popup placement is Windows-only; other systems just open the page.

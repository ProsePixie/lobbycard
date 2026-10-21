"""
Local Media Library server.

Security model (personal tool for a trusted home LAN, not the open internet):
- Binds to 127.0.0.1 (this computer only) by default. Set MEDIABOX_HOST=0.0.0.0
  to let a phone/tablet on the same Wi-Fi use it as a play-on-the-TV remote
  (see /api/play). Either way, every mutating request is gated by:
- the X-Auth-Token header, matched
  against a random token generated fresh each time this process starts and
  embedded only in the page this server itself serves. A malicious page open
  in another tab cannot read that token (browser same-origin policy blocks
  it from reading our response), so it cannot forge a valid request even
  though the browser would happily let it try (this is the standard defense
  against "localhost CSRF" / browser-based attacks on local dev servers).
- Origin header is also checked on mutating requests as a second layer.
- All file-path-referencing requests are validated against the current,
  freshly-loaded dataset - arbitrary paths are never accepted or acted on.
- The refresh endpoint only ever runs a fixed, hardcoded list of scripts in
  this folder - no user input ever reaches a shell/subprocess command line.
- Nothing here can ever delete files - that's a hard invariant, not just a
  convention. Filing/quarantine stay explicit, chat-gated, review-the-script-
  first actions. There are two deliberate exceptions, both one-click actions
  gated by an in-browser confirm() showing exactly what's about to happen
  first: /api/purge-pending-deletion only ever *moves* watched, delete-
  after-watch-eligible files into a Desktop review folder outside every
  scanned location (never deletes - still sitting right there for the user
  to delete themselves, whenever they choose); /api/autofile-queue only
  ever *moves+renames* watched, "keep forever" queue files into Video\\,
  against the full naming spec in autofile_naming.py - real per-episode
  TMDB validation, never a guess (see validate_tv_episode() there and
  README roadmap item 24). Anything it can't independently confirm is
  left exactly where it is and reported, never renamed on a hunch. Once a
  video's been filed out of a release folder, whatever sidecar-only
  wreckage is left behind (nfo, thumbnail art, etc - never anything else)
  also gets moved into the same Desktop review folder /api/purge-pending-
  deletion uses (see _autofile_relocate_leftover(), roadmap item 26) - so a
  video-less leftover never lingers in a scanned queue root as a stray
  library entry.
"""
import os as _boot_os
import time as _boot_time

# Absolute-first-line diagnostics for a real, recurring, previously
# unsolved symptom: the launcher's 90s "never came up" timeout firing with
# ZERO trace anywhere, including logs/server.log - because that log only
# starts capturing once every top-level import below has already finished
# (see the stdout/stderr redirect further down, inside
# `if __name__ == "__main__":`). If something in THIS file's own import
# chain is what's actually slow on a cold boot (empty disk cache,
# antivirus doing on-access scanning of newly-loaded binaries for the
# first time this boot, a spun-down drive), there was never any evidence
# of where the time went - confirmed real 2026-08-21, three separate
# incidents, all with an identical "nothing written at all" signature.
# Uses only the two most basic stdlib modules, imported before anything
# else in this file, so the checkpoint mechanism itself can never be the
# thing that hangs. Appends to its own small file (not server.log, which
# isn't safe to touch yet) - safe to delete any time, just loses history.
_BOOT_LOG = _boot_os.path.join(_boot_os.path.dirname(_boot_os.path.abspath(__file__)), "..", "logs", "startup_checkpoints.log")


def _boot_checkpoint(label):
    try:
        _boot_os.makedirs(_boot_os.path.dirname(_BOOT_LOG), exist_ok=True)
        with open(_BOOT_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{_boot_time.strftime('%Y-%m-%d %H:%M:%S')}] PID {_boot_os.getpid()}: {label}\n")
    except OSError:
        pass  # diagnostics logging must never itself become a new failure mode


_boot_checkpoint("process started - beginning top-level imports")

import bisect
import json
import os
import base64
import html
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from xml.etree import ElementTree
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory

from render_engine import (
    AUTOFILE_QUEUE_ROOTS, autofile_confidently_matched, autofile_in_scope, fmt_size, playlist_uri,
    render_page, resolve_watched, suggest_category,
)
from state_store import TOP10_PEOPLE, load_state, promote_recently_watched, prune_state, show_key, update_state
from autofile_naming import (
    named_season_to_tmdb,
    SEASON_DIR_RE, extract_resolution_tag, find_matching_sidecars, looks_multi_episode,
    movie_dest_dir, movie_filename, pick_best_duplicate, tv_dest_dir, tv_filename,
    validate_tv_episode,
)
import file_queue  # pipeline/file_queue.py - "File download queue -> the library"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "pipeline"))
import app_config  # noqa: E402
import branding  # noqa: E402
import library_rules  # noqa: E402
DATA_DIR = app_config.DATA_DIR
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
# Hand-maintained DVD/Blu-ray collection (pipeline/enrich_physical.py),
# entirely separate from the scanned digital library - concatenated onto
# it in load_shows() so every existing consumer (index page, state
# pruning, wiki-image lookup) sees physical entries for free. Optional:
# missing file just means the physical collection hasn't been built yet.
PHYSICAL_DB_PATH = os.path.join(DATA_DIR, "physical_db.json")
# Amazon Prime Video purchases (pipeline/enrich_amazon.py) - unlike
# physical, these are treated as DIGITAL (no physical_collection flag),
# so they show up by default and count toward the normal stats. Same
# optional-file reasoning as PHYSICAL_DB_PATH above.
AMAZON_DB_PATH = os.path.join(DATA_DIR, "amazon_db.json")
# Followed streaming titles (pipeline/enrich_streaming.py) - Netflix /
# Prime Video shows the user "follows" so they render in the library as if
# owned (a live subscription is a live licence). Same DIGITAL treatment as
# Amazon; same optional-file reasoning.
STREAMING_DB_PATH = os.path.join(DATA_DIR, "streaming_db.json")
STREAMING_SEED_PATH = os.path.join(DATA_DIR, "streaming_follows_seed.json")
SEASON_CACHE_PATH = os.path.join(DATA_DIR, "tmdb_season_cache.json")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
PORT = int(os.environ.get("MEDIABOX_PORT", "5183"))
# A second, genuinely separate port serving ONLY the "Now playing on the TV"
# transport card - no library grid, nothing to scroll past - so a phone used
# purely as a couch remote can be bookmarked/home-screened straight to the
# controller with zero chance of accidentally loading the full app. Same
# Flask `app` object, same routes, same auth - just a second listening socket;
# index() below branches purely on which port a request arrived on (see
# _is_remote_port_request()). Override with MEDIABOX_REMOTE_PORT, same
# convention as MEDIABOX_HOST below.
REMOTE_PORT = int(os.environ.get("MEDIABOX_REMOTE_PORT", "5184"))
# 127.0.0.1 = this computer only. 0.0.0.0 = reachable from other devices on
# the LAN (phone-as-remote, /api/play); every mutating route is still
# token-gated.
HOST = os.environ.get("MEDIABOX_HOST", "127.0.0.1")  # set MEDIABOX_HOST=0.0.0.0 to reach it from a phone on your network
OPENVLC_HANDLER = os.path.join(PROJECT_ROOT, "extras", "windows", "handlers", "OpenVlcHandler.exe")
APP_ASSETS_DIR = os.path.join(SCRIPT_DIR, "assets")
# The most recent VLC launched with a controllable HTTP interface (from
# /api/play with control=true) - {"port": int, "pw": str}. Only the latest
# is remembered, which matches "control the thing I just started".
_vlc_ctl = {}

PHYSICAL_SEED_PATH = os.path.join(DATA_DIR, "physical_media_seed.json")
AMAZON_SEED_PATH = os.path.join(DATA_DIR, "amazon_purchases_seed.json")

# sys.path collision note: pipeline/, plans/ and the optional YouTube dir all
# carry generic module names (config, core, state, atomic_write). This works
# because each insert(0, ...) puts the wanted dir first at the moment its
# imports run. If a new bolt-on dir is added, insert it right before the
# imports that need it, not once at module top.
sys.path.insert(0, os.path.join(PROJECT_ROOT, "pipeline"))
from atomic_write import atomic_replace  # noqa: E402
from prune_logs import prune_logs  # noqa: E402
from split_by_year_titles import is_split_by_year_title, reset_cache as reset_split_by_year_cache  # noqa: E402
from tmdb_image_cache import (  # noqa: E402
    CUSTOM_TOKEN_PREFIX, IMAGES_ROOT as TMDB_IMAGES_ROOT, MAX_UPLOAD_BYTES, SIZE_BY_KIND,
    cache_show_images, ensure_cached, save_custom_image,
)
from enrich_tmdb import (  # noqa: E402
    api_get as tmdb_api_get, extract_cast, extract_recommendations, extract_trailer_key,
    load_person_cache, save_person_cache,
)
# fallback_identify and migrate_state_paths are deliberately NOT imported
# here at module level - both are only ever used from one narrow endpoint
# each (see their own call sites below), but fallback_identify transitively
# pulls in enrich_epguides/enrich_wikipedia too, adding real weight to
# every single server startup for a feature (the Admin panel's epguides/
# Wikipedia fallback search) that's used rarely. Real, recurring symptom
# this is part of fixing: launcher timeouts with zero diagnostic trace,
# 2026-08-21 - see _boot_checkpoint() at the top of this file. Imported
# lazily instead, right inside the functions that actually use them.
sys.path.insert(0, os.path.join(PROJECT_ROOT, "plans"))

# Outside every scanned root (incoming folders, the library) on purpose - a
# single destination that works no matter which drive/folder an eligible file
# currently lives in, and needs no scan-exclusion wiring since scan_media.py's
# roots never point anywhere near the project folder.
PENDING_DELETION_DIR = os.path.join(PROJECT_ROOT, "Pending Deletion")

# Small app-wide config knobs, not per-user/per-show state (that's
# user_state.json's job) - currently just the missing-episode grace
# period, see pipeline/missing_episode_grace.py's own comment for why it
# exists. Admin-adjustable via /api/settings below. MISSING_EPISODE_GRACE_CHOICES
# imported from there (2026-09-14) rather than kept as a fourth independent
# copy of the same tuple - enrich_tv_coverage.py/track_purged_shows.py/
# enrich_wanted.py all import it from the same place now.
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
from missing_episode_grace import MISSING_EPISODE_GRACE_CHOICES  # noqa: E402


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("missing_episode_grace_days", 7)
    if data["missing_episode_grace_days"] not in MISSING_EPISODE_GRACE_CHOICES:
        data["missing_episode_grace_days"] = 7
    return data

# Sidecar/junk types Auto-File deliberately never touches when filing a
# video (nfo, thumbnail art, release-tracking/checksum files) - a release
# folder containing only these, after its video (+ subtitle sidecars) has
# already been filed, has nothing left worth keeping in a scanned queue
# root. Extended 2026-08-18 (Feature_Requests.md's "Done" item 2) to also
# cover .gif/.exe/.md5/.par2 - deliberately excludes .srt/.sub/.ass, which
# are real content, not junk. NOT kept in sync with
# generated\cleanup_orphaned_folders.ps1's own copy of this list (that's
# the paused reorg project's generated output, out of scope per the user's
# own decision).
DISPOSABLE_SIDECAR_EXTS = {
    ".nfo", ".txt", ".sfv", ".jpg", ".jpeg", ".png", ".gif", ".url", ".diz",
    ".exe", ".md5", ".par2",
}

# Mirrors enrich_tv_coverage.py's own ONGOING_STATUSES - used only to decide
# whether a live-fetched season should be cached as permanently "complete"
# (see autofile_naming.validate_tv_episode).
ONGOING_STATUSES = {"Returning Series", "In Production"}

AUTH_TOKEN = secrets.token_urlsafe(32)
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

PIPELINE_SCRIPTS = [
    # MUST run first, before scan_media.py - it carries watched/retention/
    # travel state forward for anything the reorg tooling renamed since the
    # last refresh, and run_refresh_pipeline() prunes any watched entry
    # whose path isn't in THIS run's fresh scan the moment it finishes, so
    # an unmigrated old path is unrecoverable the instant scan_media.py's
    # results get pruned against. See pipeline/heal_reorg_state.py.
    "pipeline/heal_reorg_state.py",
    "pipeline/scan_media.py",
    "pipeline/enrich_tmdb.py",
    "pipeline/consolidate.py",
    "pipeline/enrich_wikipedia.py",
    "pipeline/enrich_epguides.py",
    # After consolidate.py (needs the freshest possible digital season
    # data for its per-season overlap check) and before
    # enrich_tv_coverage.py (which reads physical_db.json for the
    # covered_physically cross-reference - physical needs to be current
    # first). Also listed in automation/daily_refresh.ps1's own $Scripts -
    # kept in sync deliberately, same duplication daily_refresh.ps1
    # already has with this list for every other script here.
    "pipeline/enrich_physical.py",
    # Reads media_db.json for its own (simpler, whole-title) overlap
    # check - same "after consolidate.py" reasoning as enrich_physical.py
    # above. Order relative to enrich_physical.py doesn't matter (neither
    # reads the other's output).
    "pipeline/enrich_amazon.py",
    # Followed Netflix / Prime Video titles - synthetic entries merged into
    # the library by load_shows(). Cached TMDB fetches, so a re-run with an
    # unchanged seed is cheap. Also in QUICK_SCAN_SCRIPTS.
    "pipeline/enrich_streaming.py",
    "pipeline/enrich_tv_coverage.py",
    "pipeline/detect_runtime_mismatch.py",
    # After the two above (they reset+recompute missing_* for owned shows
    # and skip zero-owned ones) - injects "wanted" titles from
    # data\wanted_seed.json into media_db.json with missing_seasons
    # pre-populated, so the companion tool picks them up via the existing
    # missing-episode contract. Also in daily_refresh.ps1.
    "pipeline/enrich_wanted.py",
    # After enrich_wanted.py (so "wanted" and "purged_tracked" don't both
    # claim the same tmdb_id) - re-injects any delete_after_watch show/movie
    # that currently owns nothing digital, so Recently Watched / Continue
    # Watching / the companion tool's missing-episode sweep keep working for it
    # after Purge removes its last file. See the module docstring.
    "pipeline/track_purged_shows.py",
    "plans/build_quarantine_plan.py",
    # Deliberately LAST (a status file for external tools such as the VORTEX
    # launcher) - this
    # pipeline aborts on the first failing script, so build_system_status.py
    # only ever runs once every prior step has genuinely succeeded. Also
    # listed in automation/daily_refresh.ps1's own $Scripts, same
    # kept-in-sync duplication as enrich_physical.py above - both refresh
    # paths need to write the same status file for it to mean anything.
    "plans/build_system_status.py",
    # Dead last - diffs this clean scan against the previous snapshot and
    # appends add/removed events to data\library_ledger.jsonl (+ permanent
    # tombstones for removed shows). Same "only runs after a genuinely
    # successful refresh" reasoning as build_system_status.py. Also in
    # automation\daily_refresh.ps1's $Scripts.
    "plans/build_library_ledger.py",
]

# Fast subset run once automatically whenever the server starts (e.g. a fresh
# boot, or the wife's shortcut) - skips the slow per-season TMDB completeness
# checks and the plan-builders, which aren't needed just to pick up new
# downloads quickly. Lives in pipeline/refresh_scripts.py now (2026-09-05) so
# pipeline/file_queue.py --refresh can run the identical subset for VORTEX
# without importing this whole module - one list, two callers.
from refresh_scripts import QUICK_SCAN_SCRIPTS as STARTUP_SCRIPTS  # noqa: E402

app = Flask(__name__)
# Applies to every request body, not just the upload route - but this app
# has no other route that expects anything remotely close to this size, so
# one global cap is simpler than a per-route limit. Some headroom above
# MAX_UPLOAD_BYTES for multipart boundaries/the other form field.
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024 * 1024

_refresh_lock = threading.Lock()
_refresh_status = {"running": False, "started_at": None, "finished_at": None, "error": None, "log": []}

# What each refresh step is doing, in plain English - shown on the loading
# screen (name = the heading, what = the one-line explanation under it).
# Anything not listed falls back to its filename, so a new script never
# shows a blank. The third value only seeds the progress bar's weighting
# (rough seconds) until a step has actually been timed on this machine.
STEP_INFO = {
    "pipeline/heal_reorg_state.py": ("Reconciling file moves", "Carrying watched marks over for anything renamed since last time.", 2),
    "pipeline/scan_media.py": ("Scanning your library", "Looking through your video folders, the download queue and your drives for new or changed files.", 18),
    "pipeline/enrich_tmdb.py": ("Matching titles against TMDB", "Looking each title up on TMDB so posters, genres, cast and air dates stay right.", 52),
    "pipeline/consolidate.py": ("Merging copies", "Combining digital, physical and Amazon copies of the same show into one entry.", 4),
    "pipeline/reconcile_missing_episodes.py": ("Updating missing episodes", "Clearing the missing flag on episodes that just arrived.", 3),
    "pipeline/enrich_wikipedia.py": ("Wikipedia fallback", "Trying Wikipedia for titles TMDB could not match.", 4),
    "pipeline/enrich_epguides.py": ("Epguides cross-reference", "Cross-checking episode lists against epguides.", 4),
    "pipeline/enrich_physical.py": ("Updating the physical collection", "Rebuilding your discs and box sets.", 4),
    "pipeline/enrich_amazon.py": ("Updating Amazon purchases", "Rebuilding your Amazon Prime purchases.", 2),
    "pipeline/enrich_streaming.py": ("Updating Netflix and Prime follows", "Refreshing the Netflix and Prime titles you follow.", 5),
    "pipeline/enrich_tv_coverage.py": ("Checking for missing episodes", "Comparing what you own with every aired episode on TMDB.", 28),
    "pipeline/detect_runtime_mismatch.py": ("Checking for mismatched runtimes", "Flagging files whose length does not match the title.", 5),
    "pipeline/enrich_wanted.py": ("Adding wanted titles", "Adding the titles you asked to chase.", 3),
    "pipeline/track_purged_shows.py": ("Deleted-after-watching shows", "Keeping shows you watch and delete visible in Continue Watching.", 4),
    "plans/build_quarantine_plan.py": ("Planning quarantine moves", "Checking for files that should not be in the library.", 3),
    "plans/build_system_status.py": ("Writing system status", "Saving a status file VORTEX reads.", 2),
    "plans/build_library_ledger.py": ("Updating the library ledger", "Recording what was added or removed since last time.", 3),
}
REFRESH_STEP_TIMINGS_PATH = os.path.join(DATA_DIR, "refresh_step_timings.json")
REFRESH_STEP_PROGRESS_PATH = os.path.join(DATA_DIR, "refresh_step_progress.json")


def _load_step_timings():
    try:
        with open(REFRESH_STEP_TIMINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_step_timing(script, seconds):
    """Last 5 durations per step, so the loading bar can weight each step by
    how long it really takes here. Best-effort - never affects the refresh."""
    try:
        timings = _load_step_timings()
        timings[script] = (timings.get(script, []) + [round(seconds, 1)])[-5:]
        tmp = REFRESH_STEP_TIMINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(timings, f, indent=1)
        atomic_replace(tmp, REFRESH_STEP_TIMINGS_PATH)
    except OSError:
        pass


def _refresh_step_plan(scripts):
    timings = _load_step_timings()
    plan = []
    for script in scripts:
        info = STEP_INFO.get(script)
        base = os.path.basename(script).replace(".py", "").replace("_", " ")
        seen = timings.get(script) or []
        weight = sum(seen) / len(seen) if seen else (info[2] if info else 5)
        plan.append({
            "script": script,
            "name": info[0] if info else base.capitalize(),
            "what": info[1] if info else "Working through this step.",
            "weight": round(max(weight, 0.5), 1),
        })
    return plan


def _live_step_progress():
    """The running step's own "N of M <noun>" report (pipeline/step_progress.py),
    only if it belongs to the step running right now and was written after
    that step began - never a stale leftover from an earlier run."""
    current = _refresh_status.get("current")
    if not current or not _refresh_status.get("running"):
        return None
    try:
        with open(REFRESH_STEP_PROGRESS_PATH, encoding="utf-8") as f:
            pg = json.load(f)
    except (OSError, ValueError):
        return None
    if pg.get("script") != os.path.basename(current) or pg.get("at", 0) < _refresh_status.get("current_started_ts", 0):
        return None
    return {k: pg.get(k) for k in ("done", "total", "noun", "note")}


def _refresh_status_view():
    """_refresh_status plus the live sub-progress of the current step - what
    the page's loading screen polls."""
    view = dict(_refresh_status)
    view["progress"] = _live_step_progress()
    return view

_purge_lock = threading.Lock()
_purge_status = {
    "running": False, "started_at": None, "finished_at": None, "error": None,
    "moved_count": 0, "moved_bytes": 0, "skipped_count": 0, "total_count": 0,
}

_autofile_lock = threading.Lock()
_autofile_status = {
    "running": False, "started_at": None, "finished_at": None, "error": None,
    "filed_count": 0, "filed_bytes": 0, "skipped_count": 0, "total_count": 0,
    # Per-file reason for every flagged/skipped entry (2026-08-28) - the UI
    # used to just say "see logs\ for why," which meant genuinely opening a
    # dated log file by hand to see something this endpoint already knows.
    # Now the status response IS the log for this purpose - see
    # showFlaggedDialog() in render_engine.py.
    "flagged": [],
}

_dup_resolve_lock = threading.Lock()
_dup_resolve_status = {
    "running": False, "started_at": None, "finished_at": None, "error": None,
    "moved_count": 0, "moved_bytes": 0, "skipped_count": 0, "total_count": 0,
}

# "Remove Forever" (Admin -> Fix a title, 2026-09-13) - moves every real
# file for one title to Pending Deletion (same safe never-hard-delete
# convention as Purge) and clears its retention entry so
# pipeline/track_purged_shows.py doesn't reinject a placeholder for it -
# NOT a blocklist, just "stop tracking this one, right now." Deliberately
# does not touch watched/top10/no_interest/recently_watched (the user's
# own explicit scope: main library only, leave history/recommendations
# alone). Same background-job shape as Purge/Autofile since a full series
# can be many GB.
_remove_forever_lock = threading.Lock()
_remove_forever_status = {
    "running": False, "started_at": None, "finished_at": None, "error": None,
    "moved_count": 0, "moved_bytes": 0, "skipped_count": 0, "total_count": 0,
    "title": None,
}

# "File download queue -> the library" (pipeline/file_queue.py). Deliberate,
# explicit filing - NOT watched-gated, TV + movies - triggered from the
# app's File-Queue modal or, standalone, by VORTEX. See file_queue.py.
_file_queue_lock = threading.Lock()
_file_queue_status = {
    "running": False, "started_at": None, "finished_at": None, "error": None,
    "filed": 0, "replaced": 0, "skipped": 0, "failed": 0, "swept": 0, "total": 0, "log_path": None,
}


def merge_source_into_digital(digital_show, source_show):
    """Splices a Physical/Amazon TV show's entries into the matching
    digital show's own entries[], so a title owned partway across
    sources (Legion: digital has season 3, Amazon has seasons 1-2;
    Futurama: digital has 10-11, physical has 1-5) renders as ONE card
    with every entry - real file or synthetic - instead of a separate
    card per source. Safe by construction, not by luck: enrich_physical.py
    /enrich_amazon.py already exclude any season that's also digital
    before either ever writes physical_db.json/amazon_db.json, so there's
    no season-number collision to resolve here, just append and re-sort.
    Movies never reach this function (see the caller) - a movie's overlap
    check is whole-title at generation time, so one that made it into
    physical_db.json/amazon_db.json never has a digital counterpart to
    merge into in the first place."""
    digital_show["entries"] = sorted(
        digital_show["entries"] + source_show["entries"],
        key=lambda e: (e.get("season") or 0, e.get("episode") or 0),
    )
    digital_show["episode_count"] = len(digital_show["entries"])
    digital_show["version_count"] = len(digital_show["entries"])


# --- Movie-into-TV-show exceptions (explicit, per-library; never automatic) ---
# A handful of movies logically belong inside a specific TV show's own
# season list, in chronological watch order, rather than as their own
# disconnected standalone card - TMDB has no concept of this (a movie and a
# TV show are always two completely separate catalog entries there), so it
# has to be spelled out explicitly, one real TMDB movie id at a time, in
# <data dir>/library_rules.json ("movie_to_tv_merge", see
# pipeline/library_rules.py). Each entry: movie tmdb_id -> (owning TV show's
# tmdb_id, sentinel "season" value placing it in the right chronological
# slot). The sentinel is always fractional - a real TMDB season number is
# never anything but a non-negative integer, so this can't collide with real
# season data for this or any other show, and seasonGroupLabel() in
# render_engine.py recognizes any non-integer season as "this is one of
# these inserts." A movie owned on physical media only reaches this table
# via the extra_sources loop below, never written back to any .json file,
# so pipeline/enrich_tv_coverage.py's missing-episode tracking (reads the
# raw, unmerged media_db.json) is unaffected and still correctly treats it
# as owned, not missing. A movie owned digitally reaches it via the separate
# digital-side pass below.
MOVIE_TO_TV_MERGE = library_rules.movie_to_tv_merge()


def merge_movie_into_tv_show(tv_show, movie_show, season_sentinel):
    """Splices one owned movie into a TV show's own entries[] as a
    synthetic pseudo-episode, instead of leaving it as a standalone Movie
    card - see MOVIE_TO_TV_MERGE above for why. Strips the TV show's own
    name from the front of the movie's title where present (a movie entry
    carries its full TMDB title, which sometimes repeats the show's name -
    "Futurama: Bender's Big Score" - and sometimes doesn't - "Twin Peaks:
    Fire Walk with Me" only has a colon-space, this handles both) and
    leaves episode as None so it displays as a bare title in the UI
    (renderEntryRow's own season != null && episode != null check), same
    as any other single-entry Movie show today - never a fabricated
    "S05E01"-style label for something that was never really an episode."""
    entry = dict(movie_show["entries"][0])
    entry["name"] = re.sub(rf"^{re.escape(tv_show['title'])}:?\s*", "", movie_show["title"])
    entry["season"] = season_sentinel
    entry["episode"] = None
    entry["air_date"] = movie_show.get("tmdb_release_date")
    # air_date as a 3rd sort key, not just relying on insertion order + a
    # stable sort - confirmed real, not hypothetical for Futurama:
    # physical_db.json's own list order for those 4 movies is Big Score,
    # Bender's Game, Into the Wild Green Yonder, Beast with a Billion Backs
    # (enrich_physical.py doesn't preserve physical_media_seed.json's
    # order), which produced the wrong watch order until this was added.
    # Harmless for every real TV episode too - season+episode is already a
    # unique key there, so air_date never actually gets consulted as a
    # tiebreaker.
    tv_show["entries"] = sorted(
        tv_show["entries"] + [entry],
        key=lambda e: (e.get("season") if e.get("season") is not None else 0, e.get("episode") or 0, e.get("air_date") or ""),
    )
    tv_show["episode_count"] = len(tv_show["entries"])
    tv_show["version_count"] = len(tv_show["entries"])


def _dedupe_show_records(shows):
    """Drop repeat show records that share a (tmdb_id, type) identity, or -
    for still-unmatched shows - a normalized title. Keeps the first (richest
    by entry count) occurrence, merges the others' entries into it by path.

    consolidate.py already collapses same-identity shows, but only when it
    runs, and it runs at pipeline position 3 - any later writer
    (enrich_*, detect_runtime_mismatch, or api_refresh_next_episode) that
    races with, or runs after a half-finished, pipeline can leave a
    duplicated record sitting in media_db.json until the next full refresh.
    That is exactly how "Doctor Who" once showed up three times in What's
    New (2026-09-05). This read-time guard means a corrupt file never
    *renders* wrong - the next refresh still does the real cleanup."""
    seen = {}
    out = []
    for s in shows:
        if s.get("tmdb_matched") and s.get("tmdb_id"):
            ident = ("id", s["tmdb_id"], s.get("type"))
        else:
            ident = ("title", (s.get("title") or "").strip().lower(), s.get("type"))
        first = seen.get(ident)
        if first is None:
            seen[ident] = s
            out.append(s)
            continue
        have = {e.get("path") for e in first.get("entries", [])}
        for e in s.get("entries", []):
            if e.get("path") not in have:
                first.setdefault("entries", []).append(e)
                have.add(e.get("path"))
    return out


def load_shows():
    if not os.path.exists(DB_PATH):
        return []
    with open(DB_PATH, encoding="utf-8") as f:
        shows = _dedupe_show_records(json.load(f))

    extra_sources = []
    if os.path.exists(PHYSICAL_DB_PATH):
        with open(PHYSICAL_DB_PATH, encoding="utf-8") as f:
            extra_sources.append(json.load(f))
    if os.path.exists(AMAZON_DB_PATH):
        with open(AMAZON_DB_PATH, encoding="utf-8") as f:
            extra_sources.append(json.load(f))
    if os.path.exists(STREAMING_DB_PATH):
        with open(STREAMING_DB_PATH, encoding="utf-8") as f:
            extra_sources.append(json.load(f))

    # {(tmdb_id, type): show} for the real digital library only - built
    # once, referenced (not copied) by every merge below, so mutating a
    # matched show here mutates the actual object already sitting in
    # `shows`, no separate reassembly step needed.
    digital_by_key = {
        (s["tmdb_id"], s["type"]): s for s in shows if s.get("tmdb_matched") and s.get("tmdb_id")
    }

    # Digital-side half of MOVIE_TO_TV_MERGE (Twin Peaks: Fire Walk with
    # Me) - the movie is already sitting in `shows` as its own entry
    # (media_db.json, not a separate physical/Amazon source), so it's
    # pulled out of the top-level list here and merged in, rather than
    # going through the extra_sources loop below like Futurama's
    # physical-only movies do.
    for tmdb_id, (tv_tmdb_id, season_sentinel) in MOVIE_TO_TV_MERGE.items():
        movie_show = digital_by_key.get((tmdb_id, "Movie"))
        tv_show = digital_by_key.get((tv_tmdb_id, "TV"))
        if movie_show and tv_show:
            merge_movie_into_tv_show(tv_show, movie_show, season_sentinel)
            shows.remove(movie_show)

    standalone = []
    for source_shows in extra_sources:
        for s in source_shows:
            tmdb_id = s.get("tmdb_id")
            merge_target = MOVIE_TO_TV_MERGE.get(tmdb_id) if s["type"] == "Movie" else None
            if merge_target:
                tv_tmdb_id, season_sentinel = merge_target
                tv_show = digital_by_key.get((tv_tmdb_id, "TV"))
                if tv_show:
                    merge_movie_into_tv_show(tv_show, s, season_sentinel)
                    continue
            digital_match = digital_by_key.get((tmdb_id, s.get("type"))) if tmdb_id else None
            if digital_match and s["type"] == "TV":
                merge_source_into_digital(digital_match, s)
            else:
                standalone.append(s)
    return shows + standalone


def safe_folder_name(name):
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip()


def _lan_ips():
    """Private-range IPv4 addresses of this machine, best guess first, for
    the startup banner only (so the user knows what to type on their phone).

    The old single-address "UDP-connect to 8.8.8.8 and read the local end"
    trick returns the VPN tunnel address when a VPN is up (NordLynx etc.) -
    which no phone on the real Wi-Fi can reach. So: collect every local
    IPv4, keep the RFC-1918 ones, and list 192.168.* first (the ordinary
    home-router range, and the least likely to be a VPN tunnel)."""
    found = set()
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(res[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            found.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass

    # NordLynx (NordVPN's WireGuard) always pins the local tunnel end to
    # this exact address - it's never a real LAN host, and no phone on the
    # home Wi-Fi can reach it, so keep it out of the banner entirely
    # (this is the "10.5.0.2" the user kept mis-typing before).
    _VPN_TUNNEL_IPS = {"10.5.0.2"}

    def _is_private(ip):
        if ip in _VPN_TUNNEL_IPS:
            return False
        if ip.startswith(("192.168.", "10.")):
            return True
        if ip.startswith("172."):
            try:
                return 16 <= int(ip.split(".")[1]) <= 31
            except (IndexError, ValueError):
                return False
        return False

    ips = sorted((ip for ip in found if _is_private(ip)),
                 key=lambda ip: (not ip.startswith("192.168."), ip))
    return ips


def _lan_hostnames():
    """Names this machine can also be reached by, for the startup banner -
    friendlier than a bare IP and, unlike one, they keep working even if
    the IP ever changes (the IP is normally pinned by a router DHCP
    reservation now, but a name is belt-and-braces).

    - <host>.local is always offered: mDNS/Bonjour, which an iPhone/iPad
      resolves with zero setup (Android is hit or miss). <host> is this
      PC's own name - rename the PC to change it (e.g. to "mediabox").
    - <host>.fritz.box only if it actually resolves from here - that needs
      the router (a FRITZ!Box auto-registers every device's name) to be
      this machine's DNS server, which a VPN on this box can quietly
      break."""
    short = socket.gethostname().split(".")[0].strip().lower()
    if not short:
        return []
    names = [f"{short}.local"]
    fritz = f"{short}.fritz.box"
    try:
        socket.getaddrinfo(fritz, None, socket.AF_INET)
        names.append(fritz)
    except OSError:
        pass
    return names


def _find_vlc():
    return app_config.vlc_path() or None


def _find_ffprobe():
    return app_config.ffprobe_path() or None


def _find_ffmpeg():
    return app_config.ffmpeg_path() or None


NOSLEEP_VIDEO_PATH = os.path.join(DATA_DIR, "nosleep.mp4")


def _ensure_nosleep_video():
    """Generates a ~5s, 2x2px, silent black MP4 once and caches it to disk
    (self-healing lazy-cache, same "ensure it exists, generate once" shape
    as pipeline/tmdb_image_cache.py's ensure_cached()) - the phone remote's
    fallback for staying awake (2026-09-30: the real Screen Wake Lock API
    only works on https:// or exactly "localhost", confirmed against a real
    matching GitHub issue - mediabox.local over plain http never qualifies,
    so navigator.wakeLock silently never holds on a real phone). A looping,
    invisible, muted <video> element playing is the standard, long-
    established workaround (the "NoSleep.js" technique) - mobile Safari/
    Chrome don't dim the screen while a video is actively playing,
    regardless of secure-context rules. Generated locally via ffmpeg
    (already a project dependency) rather than embedding a random
    third-party base64 blob, so there's nothing here to trust but this
    project's own ffmpeg call. Returns True if the file exists/was made.

    5s, not the original 1s (2026-09-30, real device report: kept the
    screen awake for ~6 minutes, then it started dimming anyway) - a video
    this short means the browser crosses the loop boundary (end -> restart)
    roughly 360 times over 6 minutes, and that boundary is exactly where a
    mobile browser's media pipeline is most likely to silently stall
    without throwing an error. Fewer loop boundaries per minute lowers how
    often that risk is hit; render_engine.py's own watchdog (see
    tvAcquireWakeLock's setInterval) is the other, complementary half of
    this fix - it actively checks the video is still really playing and
    restarts it if not, rather than trusting a single play() call to hold
    for an entire viewing session."""
    if os.path.isfile(NOSLEEP_VIDEO_PATH):
        return True
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return False
    try:
        tmp = NOSLEEP_VIDEO_PATH + ".tmp"
        subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=black:s=2x2:d=5:r=10",
             "-f", "lavfi", "-i", "anullsrc", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", "-movflags", "+faststart",
             # -f mp4 explicit: ffmpeg infers output format from the file
             # EXTENSION by default, and the atomic-write ".tmp" suffix
             # (same pattern as everywhere else in this project) means the
             # real extension isn't ".mp4" until the atomic_replace() below
             # renames it - confirmed real, without this it fails with
             # "Unable to choose an output format".
             "-f", "mp4", tmp],
            capture_output=True, timeout=15,
        )
        if not os.path.isfile(tmp):
            return False
        atomic_replace(tmp, NOSLEEP_VIDEO_PATH)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _ffprobe_stream_langs(ffprobe, video_path, stream_type):
    """Language tags of a file's audio ('a') or subtitle ('s') streams,
    newline-joined, as ffprobe prints them. '' on any failure."""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", stream_type,
             "-show_entries", "stream_tags=language",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=8,
        )
        return out.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _ffprobe_chapters(ffprobe, video_path):
    """Sorted list of real chapter start times (seconds, float) for the
    file, or [] if it has none / ffprobe fails. Confirmed real, 2026-09-29:
    the installed VLC (3.0.23) has NO chapter fields anywhere in its HTTP
    status.xml output at all (verified against its raw XML directly) - the
    'chapter'/'chapters_count' status fields only exist in a later VLC
    version this project isn't running. Real files DO carry real chapters
    though (confirmed on a Strange New Worlds episode: Recap/Episode Start/
    Intro/Main Content/Credits), so chapter-skip is implemented as a
    seek-to-the-next-stored-timestamp instead of trusting VLC's own
    (unavailable) chapter command - works regardless of VLC's version,
    using the same ffprobe dependency already relied on for audio/subtitle
    detection just above."""
    if not ffprobe or not video_path:
        return []
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_chapters", "-print_format", "json", video_path],
            capture_output=True, text=True, timeout=8,
        )
        data = json.loads(out.stdout or "{}")
        starts = sorted(float(c["start_time"]) for c in data.get("chapters", []) if "start_time" in c)
        return starts
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError):
        return []


def _ffprobe_duration(video_path):
    """Runtime of a video file in seconds (0.0 on any failure). Same
    `format=duration` probe handlers\\OpenVlcHandler.cs's GetDurationSeconds
    uses - lets _track_vlc_watched() do the real 80%-of-runtime check
    instead of only a flat wall-clock floor."""
    ffprobe = _find_ffprobe()
    if not ffprobe or not video_path:
        return 0.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", video_path],
            capture_output=True, text=True, timeout=8,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _vlc_language_args(video_path):
    """VLC audio/subtitle args matching handlers\\OpenVlcHandler.cs exactly
    (the desktop launcher) so the phone remote behaves the same:

      * English audio track present  -> force English audio, subtitles
        fully off (including a same-named external .srt sidecar).
      * No English audio (foreign film) -> leave audio alone (don't guess
        which foreign track), turn ON English subtitles if the file has
        any subtitle track.
      * ffprobe missing / untagged file / any error -> [] (VLC's own
        defaults, unchanged).
    """
    ffprobe = _find_ffprobe()
    if not ffprobe:
        return []
    try:
        audio = _ffprobe_stream_langs(ffprobe, video_path, "a")
        if any(ln.strip().lower() in ("eng", "en") for ln in audio.splitlines()):
            return ["--audio-language=eng", "--no-spu", "--no-sub-autodetect-file"]
        subs = _ffprobe_stream_langs(ffprobe, video_path, "s")
        return ["--sub-language=eng"] if subs.strip() else []
    except Exception:
        return []


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _track_vlc_watched(proc, path, video_path=None):
    """Wait for a VLC launched by /api/play to close, then mark the
    episode/movie watched if it ran long enough. Mirrors
    handlers\\OpenVlcHandler.cs exactly: 80% of the file's real runtime
    (ffprobe) when that's known, else a flat 40-minute wall-clock floor.
    Best-effort: any failure just means it isn't auto-marked, same as
    quitting VLC early."""
    started = time.time()
    try:
        proc.wait()
    except Exception:
        return
    elapsed = time.time() - started
    duration = _ffprobe_duration(video_path) if video_path else 0.0
    if duration >= 60:
        if elapsed / duration < 0.80:
            return
    elif elapsed < 40 * 60:
        return
    try:
        shows = load_shows()
        owning = next((s for s in shows if any(e["path"] == path for e in s["entries"])), None)
        if owning is None:
            return
        at = datetime.now(timezone.utc).isoformat()
        key = show_key(owning)

        def mutator(state):
            state["watched"][path] = {"watched": True, "at": at}
            promote_recently_watched(state, key)

        update_state(mutator)
    except Exception:
        pass


def _host_allowed(host_header):
    """DNS-rebinding guard. A web page on another site can point its own hostname at
    127.0.0.1 and then read this server as if it were same-origin, token and all. The
    browser still sends that attacker hostname in the Host header, so refuse any Host
    that is not one this server is meant to be reached by: localhost and IP literals
    always, plus (only when listening on the network) .local names and this machine's
    own hostname."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):  # [::1]:5183
        host = host.split("]")[0].lstrip("[")
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    if host == "localhost" or host.endswith(".localhost"):
        return True
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if HOST == "127.0.0.1":
        return False
    return host.endswith(".local") or host == socket.gethostname().lower()


@app.before_request
def _refuse_foreign_host():
    if not _host_allowed(request.host):
        abort(403)


def _is_loopback_client():
    import ipaddress
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def check_auth():
    token = request.headers.get("X-Auth-Token", "")
    if not token or not secrets.compare_digest(token, AUTH_TOKEN):
        abort(403)
    origin = request.headers.get("Origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        # Also accept an Origin that is same-origin with THIS request's own
        # host, so the page keeps working when it's loaded over a LAN IP or
        # hostname (phone-as-remote), not only over 127.0.0.1. Still strictly
        # same-origin - the token check above is the real gate.
        if urlparse(origin).netloc != request.host:
            abort(403)


@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        # upload.wikimedia.org: cover images for physical-collection discs with
        # no TMDB match at all - see physical_manual_cover_url in
        # pipeline/enrich_physical.py. Named hosts only, not a wildcard.
        # *.ytimg.com (video thumbs), yt3.googleusercontent.com (current
        # channel avatars) + yt3.ggpht.com (older avatar host): the YouTube
        # block's card images. The block plays videos by opening the user's
        # real browser (window.open -> youtube.com/watch), so no iframe and no
        # frame-src line - just these image hosts.
        "img-src 'self' https://image.tmdb.org https://upload.wikimedia.org "
        "https://*.ytimg.com https://yt3.ggpht.com https://yt3.googleusercontent.com data:; "
        "font-src 'self' data:; "  # the app-name wordmark's embedded base64 woff2 (Orbitron, OFL-licensed)
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'"
    )
    return resp


def _server_log(msg):
    """One timestamped line to logs/server.log.

    Under a real launch stdout/stderr are redirected to logs/server.log
    inside ``if __name__ == "__main__"`` (see the bottom of this file), so a
    plain print() lands there. Before that redirect (module import time) it
    goes to the console instead - fine for the one-time mount message below.
    Handed to bolt-on blocks (the YouTube block) as their ``log`` callable so
    everything ends up in the same file.
    """
    try:
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)
    except Exception:
        pass


# --- YouTube block (optional, independently owned) --------------------------
# The block's folder (config key youtube_dir; see extras/youtube/) owns 100%
# of the YouTube logic + the block's markup/CSS/JS; this app owns only the
# page it renders on, the header toggle, and this mount. Same
# sys.path.insert mechanism as pipeline/ and plans/ above. The whole thing is
# guarded: a missing dir or any import/registration error just leaves
# youtube_available False, the toggle chip hidden, and the app boots exactly
# as before.
YOUTUBE_DIR = app_config.YOUTUBE_DIR
youtube = None
youtube_available = False
try:
    if YOUTUBE_DIR and os.path.isdir(YOUTUBE_DIR):
        # Keep the block's channel list and watch history with the rest of the data.
        os.environ.setdefault("YOUTUBE_DATA_DIR", os.path.join(DATA_DIR, "youtube"))
        sys.path.insert(0, YOUTUBE_DIR)
        import youtube  # noqa: E402  (guarded optional dependency)

        app.register_blueprint(
            youtube.make_youtube_bp(
                {
                    "check_auth": check_auth,
                    "auth_token": AUTH_TOKEN,
                    "log": _server_log,
                }
            ),
            url_prefix="/youtube",
        )
        youtube_available = True
        _server_log("youtube block mounted at /youtube")
    else:
        _server_log(f"youtube block dir not present ({YOUTUBE_DIR}) - continuing without it")
except Exception as ex:  # noqa: BLE001 - never let the block break the server
    youtube = None
    youtube_available = False
    _server_log(f"youtube block unavailable, continuing without it: {ex}")


def _youtube_block_html():
    """The self-contained ``<section id="youtube-block">`` inner HTML, or ""
    when the block isn't available. ``render_youtube_block`` is contracted to
    never raise, but stay defensive anyway."""
    if not youtube_available:
        return ""
    try:
        return youtube.render_youtube_block({"auth_token": AUTH_TOKEN, "log": _server_log})
    except Exception as ex:  # noqa: BLE001
        _server_log(f"youtube render_youtube_block failed: {ex}")
        return ""


def _is_remote_port_request():
    """True when this request arrived on REMOTE_PORT rather than the main
    PORT - the one and only thing that distinguishes the two. request.host
    is the Host header the browser actually sent (e.g. "mediabox.local:5184"
    or "192.168.1.20:5184"); since this app always runs on an explicit
    non-default port, a real browser navigation always includes it. Not a
    security boundary - check_auth()'s own host-based Origin check is
    unaffected either way (see its own comment) - this only decides which
    HTML index() hands back."""
    try:
        port = int(request.host.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return False
    return port == REMOTE_PORT


import setup_wizard  # noqa: E402
setup_wizard.register(app, check_auth, lambda: AUTH_TOKEN)


@app.route("/")
def index():
    if not app_config.CONFIGURED:
        return redirect("/setup")  # first run: no library yet
    shows = load_shows()
    state = load_state()
    html = render_page(
        shows,
        state,
        server_mode=True,
        auth_token=AUTH_TOKEN,
        youtube_available=youtube_available,
        youtube_block_html=_youtube_block_html(),
        # Snapshot of the refresh in progress, if any - lets a page load that
        # lands mid-refresh (VORTEX's starting.html redirects the instant the
        # port answers, not once the startup scan is actually done) show a
        # blocking "still finishing up" overlay instead of a half-populated
        # grid, rather than needing several manual reloads to catch up.
        refresh_status=_refresh_status_view(),
        remote_only=_is_remote_port_request(),
    )
    return Response(html, mimetype="text/html")


@app.route("/guide")
def field_guide():
    """An optional longer guide: a field_guide.html in the active pack (see
    pipeline/branding.py). No auth: it's static help content, same as the
    main page itself."""
    path = branding.pack_file("field_guide.html")
    if not path:
        abort(404)
    return send_from_directory(os.path.dirname(path), os.path.basename(path), mimetype="text/html")


@app.route("/manifest.webmanifest")
def web_manifest():
    """Lets iOS/Android "Add to Home Screen" give the app a real app icon
    and open it chrome-free (display: standalone), instead of a screenshot
    of the page. No auth - it's just metadata."""
    return jsonify({
        "name": branding.app_name(),
        "short_name": branding.app_name()[:12],
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#14161a",
        "theme_color": "#14161a",
        "icons": [
            {"src": "/app-icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/app-icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    })


@app.route("/remote-manifest.webmanifest")
def remote_web_manifest():
    """Same idea as /manifest.webmanifest above, for the couch-remote-only
    page (REMOTE_PORT) - a distinct manifest so home-screening it doesn't
    look/behave like the real app. orientation: landscape + display:
    fullscreen only actually take effect on Android/Chrome (iOS Safari
    ignores both fields entirely, confirmed) but cost nothing
    to declare correctly for anyone on this network who isn't on an
    iPhone. The real "force landscape on iPhone too" mechanism is CSS-only
    (app/render_engine.py's html.remote-only orientation media queries),
    since no web API can lock orientation in iOS Safari at all."""
    return jsonify({
        "name": branding.app_name() + " Remote",
        "short_name": "TV Remote",
        "start_url": "/",
        "scope": "/",
        "display": "fullscreen",
        "orientation": "landscape",
        "background_color": "#05070c",
        "theme_color": "#05070c",
        "icons": [
            {"src": "/app-icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/app-icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    })


@app.route("/app-icon-<int:size>.png")
def app_icon(size):
    fn = f"app-icon-{size}.png"
    path = branding.pack_file(fn) if size in (180, 192, 512) else ""
    if not path:
        abort(404)
    return send_from_directory(os.path.dirname(path), fn, mimetype="image/png")


@app.route("/nosleep.mp4")
def nosleep_video():
    """The phone remote's keep-awake video (see _ensure_nosleep_video()'s
    own docstring for why this exists instead of the real Wake Lock API).
    No auth gate - it's a ~couple-KB silent black clip, same trust level as
    app-icon-*.png above."""
    if not _ensure_nosleep_video():
        abort(404)
    return send_from_directory(DATA_DIR, "nosleep.mp4", mimetype="video/mp4")


@app.route("/api/play", methods=["POST"])
def api_play():
    """Start VLC on THE SERVER for a known library file - turns any browser
    on the LAN (a phone on the couch) into a play-on-the-TV remote.

    Only ever plays a path that is a real entry in the current library -
    the same allowlist the watched/retention routes use - never an
    arbitrary path off the wire.

    Whenever vlc.exe is found (the normal case) this launches it directly
    with its HTTP interface on a fresh loopback port and records it in
    _vlc_ctl, so the transport card works from every device on the LAN -
    including when playback was started from the PC's own app window,
    not just from a phone. Fullscreen + on top, the same ffprobe
    audio/subtitle selection as handlers\\OpenVlcHandler.cs, and a
    background thread that marks the item watched at 80% of runtime.
    The `control` flag in the request body is now only a hint (kept for
    back-compat); OpenVlcHandler.exe is used only as a fallback when
    vlc.exe can't be located at all."""
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path")
    control = bool(data.get("control"))
    if not path or not isinstance(path, str):
        abort(400)

    owning_show, entry = next(
        ((s, e) for s in load_shows() for e in s["entries"]
         if e.get("path") == path or e.get("video_path") == path),
        (None, None),
    )
    if entry is None or not entry.get("video_path"):
        abort(404)

    parts = [entry["video_path"]] + list(entry.get("playlist_extra_paths") or [])
    missing = [p for p in parts if not os.path.isfile(p)]
    if missing:
        return jsonify({"ok": False, "error": "file not found on the server"}), 200

    vlc = _find_vlc()
    try:
        if vlc:
            # A fresh Play replaces whatever /api/play last started - one
            # video at a time, no stacking windows. Only ever the VLC WE
            # launched (tracked in _vlc_ctl), never a VLC the user opened
            # by hand.
            old = _vlc_ctl.get("proc")
            if old and old.poll() is None:
                try:
                    old.terminate()
                except OSError:
                    pass
            port, pw = _free_port(), secrets.token_urlsafe(9)
            # --fullscreen + --video-on-top: always come up fullscreen and
            # over everything else on the TV/PC. --play-and-exit closes VLC
            # when the video ends (clean for a remote). --no-one-instance so
            # this really is its own window. Audio/subtitle selection is
            # the exact same conditional logic as the desktop launcher
            # (handlers\OpenVlcHandler.cs) - English audio + no subs, or,
            # for a foreign film, English subs on.
            lang_args = _vlc_language_args(entry["video_path"])
            chapters = _ffprobe_chapters(_find_ffprobe(), entry["video_path"])
            proc = subprocess.Popen([
                vlc, "--fullscreen", "--video-on-top", "--no-video-title-show",
                "--no-one-instance", "--play-and-exit",
                *lang_args,
                "--extraintf", "http", "--http-host", "127.0.0.1",
                "--http-port", str(port), "--http-password", pw,
                *parts,
            ])
            _vlc_ctl.clear()
            _vlc_ctl.update(
                port=port, pw=pw, proc=proc,
                # kept even after VLC exits, so the phone remote can offer a
                # "Reopen on the TV" button (see _vlc_state_snapshot).
                last_path=entry["path"],
                last_title=os.path.basename(entry["video_path"]),
                # How far the last session got, 0..1 - the remote only offers
                # "Reopen on the TV" for something stopped partway, never for
                # an episode that actually finished. Reset here so a fresh
                # start never inherits the previous file's progress.
                last_fraction=0.0,
                # Show/episode identity (2026-09-29, remote redesign) - lets
                # the remote show a real "Show Name S01E02 - Episode Title"
                # label instead of the raw release filename, a poster
                # thumbnail, and a "View on TMDB" link straight to the
                # episode airing right now. None for anything unmatched -
                # the remote just falls back to the filename/no-poster/no-
                # link the same as before this existed.
                last_show_title=owning_show.get("title") if owning_show else None,
                last_tmdb_id=(owning_show.get("tmdb_id") if owning_show and owning_show.get("tmdb_matched") else None),
                last_tmdb_type=owning_show.get("type") if owning_show else None,
                last_season=entry.get("season"),
                last_episode=entry.get("episode"),
                # Real TV episode title only when this genuinely IS a TV
                # episode (season/episode both known) - confirmed real,
                # 2026-09-29 live test: falling back to entry["name"] (the
                # release filename) unconditionally meant a MOVIE's label
                # showed its own filename as a redundant third line under
                # the already-shown movie title. None here is correct for
                # a movie; the remote's label-building just omits it.
                last_episode_title=(
                    entry.get("episode_title") or entry.get("name")
                    if entry.get("season") is not None and entry.get("episode") is not None
                    else None
                ),
                last_poster=owning_show.get("tmdb_poster") if owning_show else None,
                # Real chapter start times (seconds) from ffprobe, not VLC -
                # see _ffprobe_chapters()'s own docstring. Empty list for a
                # file with no chapters; the remote correctly hides the
                # chapter buttons either way.
                chapters=chapters,
            )
            threading.Thread(
                target=_track_vlc_watched,
                args=(proc, entry["path"], entry["video_path"]),
                daemon=True,
            ).start()
        elif os.path.isfile(OPENVLC_HANDLER):
            # vlc.exe not found at a known path - fall back to the protocol
            # handler (no HTTP interface, so no transport card for this one).
            subprocess.Popen([OPENVLC_HANDLER, playlist_uri(parts, "openvlc")])
        else:
            return jsonify({"ok": False, "error": "VLC is not installed on the server"}), 200
    except OSError as ex:
        return jsonify({"ok": False, "error": str(ex)}), 200

    return jsonify({"ok": True, "control": bool(vlc)})


@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    """Show a library file in the server machine's own file manager - only for
    a path that is a real entry in the current library (same allowlist as
    /api/play), never an arbitrary path off the wire. Explorer (selecting the
    file) on Windows, Finder on macOS, the default file manager elsewhere."""
    check_auth()
    if not _is_loopback_client():
        return jsonify({"ok": False, "error": "only from the machine running the server"}), 200
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path")
    if not path or not isinstance(path, str):
        abort(400)
    entry = next((e for s in load_shows() for e in s["entries"] if e.get("path") == path), None)
    if entry is None:
        abort(404)
    target = entry.get("video_path") or path
    if not os.path.exists(target):
        target = path
    if not os.path.exists(target):
        return jsonify({"ok": False, "error": "not found on disk"}), 200
    try:
        if app_config.IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(target)], creationflags=0x08000000)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target])
        else:
            subprocess.Popen(["xdg-open", target if os.path.isdir(target) else os.path.dirname(target)])
    except OSError as ex:
        return jsonify({"ok": False, "error": str(ex)}), 200
    return jsonify({"ok": True})


@app.route("/api/vlc/<cmd>", methods=["POST"])
def api_vlc(cmd):
    """Pause / skip the VLC most recently started by /api/play with
    control=true (the phone remote's transport bar). Proxies to that VLC's
    own HTTP interface on loopback - the browser never talks to VLC
    directly. Unknown command -> 400; nothing controllable -> a soft
    'not reachable' so the UI can say so without erroring."""
    check_auth()
    seek = {"back15": "-15S", "fwd15": "+15S", "back30": "-30S", "fwd30": "+30S"}
    vol_step = {"vol_up": "%2B13", "vol_down": "-13"}  # ~5% of VLC's 256 = 100%
    if cmd in ("pause", "play", "stop"):
        query = "command=pl_" + cmd
    elif cmd in seek:
        query = "command=seek&val=" + seek[cmd]
    elif cmd in vol_step:
        query = "command=volume&val=" + vol_step[cmd]
    elif cmd == "seek_to":
        body = request.get_json(force=True, silent=True) or {}
        try:
            query = "command=seek&val=" + str(max(0, int(body.get("to"))))
        except (TypeError, ValueError):
            abort(400)
    elif cmd == "set_vol":
        body = request.get_json(force=True, silent=True) or {}
        try:
            query = "command=volume&val=" + str(min(512, max(0, int(body.get("vol")))))
        except (TypeError, ValueError):
            abort(400)
    elif cmd in ("chapter_next", "chapter_prev"):
        # Seeks to the next/previous ffprobe-derived chapter start time
        # (see _ffprobe_chapters()) rather than VLC's own chapter command -
        # confirmed real, 2026-09-29: the installed VLC (3.0.23) has no
        # chapter support in its HTTP status output at all, verified
        # against its raw XML directly, so there is no VLC-side "current
        # chapter" to even ask it to move relative to. Reusing plain
        # command=seek (the same absolute-seek command the scrubber drag
        # already uses) sidesteps VLC's chapter support entirely - this
        # works on any VLC version, since it's really just "seek to this
        # timestamp" with the timestamp picked by us, not VLC.
        chapters = _vlc_ctl.get("chapters") or []
        if not chapters:
            return jsonify({"ok": False, "error": "no chapter data for this file"}), 200
        current_time = _vlc_state_snapshot().get("time", 0)
        if cmd == "chapter_next":
            target = next((c for c in chapters if c > current_time + 1), None)
            if target is None:
                return jsonify({"ok": False, "error": "already at the last chapter"}), 200
        else:
            candidates = [c for c in chapters if c < current_time - 2]
            if not candidates:
                return jsonify({"ok": False, "error": "already at the first chapter"}), 200
            target = candidates[-1]
        query = "command=seek&val=" + str(max(0, int(target)))
    else:
        abort(400)
    if not _vlc_ctl.get("port"):
        return jsonify({"ok": False, "error": "nothing is playing"}), 200
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{_vlc_ctl['port']}/requests/status.xml?{query}"
        )
        req.add_header("Authorization", "Basic " + base64.b64encode(
            f":{_vlc_ctl['pw']}".encode()).decode())
        urllib.request.urlopen(req, timeout=3).read()
        _vlc_state_cache["data"] = None  # force the next /api/vlc/state to re-read
        return jsonify({"ok": True})
    except (urllib.error.URLError, OSError):
        return jsonify({"ok": False, "error": "VLC isn't reachable - is it still open?"}), 200


# Short cache so a room full of polling phones (mine + my wife's, every few
# seconds) collapses to at most one real status.xml hit per couple of
# seconds. {"at": monotonic_seconds, "data": snapshot_dict}.
_vlc_state_cache = {"at": 0.0, "data": None}


def _vlc_state_snapshot():
    """Current state of the one shared VLC that /api/play (control mode)
    launched:
        {"active": bool, "state": "playing"|"paused"|"stopped",
         "time": int secs elapsed, "length": int secs total,
         "volume": int (256 = 100%), "title": current filename}
    When inactive it also carries "last_path"/"last_title" (the item
    /api/play last started) if there was one this session, so the remote
    can show a "Reopen on the TV" button.
    Every LAN remote polls this (see /api/vlc/state) so a device that
    didn't start playback still shows the transport bar, its scrubber and
    volume track VLC itself, and the pause glyph follows VLC rather than
    that device's own last tap."""
    def _inactive():
        s = {"active": False, "state": "stopped"}
        if _vlc_ctl.get("last_path"):
            s["last_path"] = _vlc_ctl["last_path"]
            s["last_title"] = _vlc_ctl.get("last_title")
            # How far the last session got (0..1). The remote uses this to
            # only offer "Reopen on the TV" for something stopped partway -
            # an episode played to the end reports ~1.0 and gets no button.
            s["last_fraction"] = _vlc_ctl.get("last_fraction", 0.0)
        return s

    proc = _vlc_ctl.get("proc")
    port = _vlc_ctl.get("port")
    if not port or (proc is not None and proc.poll() is not None):
        return _inactive()

    now = time.monotonic()
    if _vlc_state_cache["data"] is not None and now - _vlc_state_cache["at"] < 1.0:
        return _vlc_state_cache["data"]

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/requests/status.xml"
        )
        req.add_header("Authorization", "Basic " + base64.b64encode(
            f":{_vlc_ctl['pw']}".encode()).decode())
        raw = urllib.request.urlopen(req, timeout=2).read()
        root = ElementTree.fromstring(raw)
        state = (root.findtext("state") or "").strip() or "playing"

        def _int(tag, default=0):
            try:
                return int(float(root.findtext(tag)))
            except (TypeError, ValueError):
                return default

        snap = {
            "active": state != "stopped",
            "state": state,
            "time": max(0, _int("time")),
            "length": max(0, _int("length")),
            "volume": _int("volume", 256),
        }
        for node in root.iter("info"):
            if node.get("name") == "filename" and (node.text or "").strip():
                # VLC's HTTP interface double-encodes entities in status.xml
                # (a filename apostrophe comes back as the literal "&#39;"),
                # so unescape once more after ElementTree's own decode.
                snap["title"] = html.unescape(node.text.strip())
                break
        # Chapter position/count, derived from ffprobe's own chapter list
        # (see _ffprobe_chapters(), captured at /api/play time into
        # _vlc_ctl["chapters"]) against the current playback time - NOT
        # from VLC's status.xml, confirmed real 2026-09-29 by reading it
        # raw: the installed VLC (3.0.23) has no chapter fields in it at
        # all. bisect finds which chapter the current time falls into;
        # empty chapters list -> both stay None, remote hides the buttons.
        chapters = _vlc_ctl.get("chapters") or []
        if chapters:
            snap["chapters_count"] = len(chapters)
            snap["chapter"] = max(0, bisect.bisect_right(chapters, snap["time"]) - 1)
        else:
            snap["chapters_count"] = None
            snap["chapter"] = None
        # Show/episode identity captured at /api/play time (see there) -
        # carried through every poll so the remote can show a real title,
        # poster and TMDB link without re-deriving them itself.
        for k in ("last_show_title", "last_tmdb_id", "last_tmdb_type",
                  "last_season", "last_episode", "last_episode_title", "last_poster"):
            snap[k] = _vlc_ctl.get(k)
        # Remember how far this session has got so _inactive() can tell a
        # finished episode from one stopped partway (see there).
        if snap["length"] > 0 and state != "stopped":
            _vlc_ctl["last_fraction"] = min(1.0, snap["time"] / snap["length"])
    except (urllib.error.URLError, OSError, ElementTree.ParseError):
        # VLC gone / not answering yet / malformed - treat as nothing playing
        snap = _inactive()

    _vlc_state_cache.update(at=now, data=snap)
    return snap


@app.route("/api/vlc/state")
def api_vlc_state():
    """Read-only poll target for the phone/tablet remotes - see
    _vlc_state_snapshot(). Token-gated like the rest of /api/vlc/*."""
    check_auth()
    return jsonify(_vlc_state_snapshot())


@app.route("/api/shows-data")
def api_shows_data():
    check_auth()
    # The exact same computed shows list a full page load embeds, without
    # the multi-MB HTML wrapper around it - lets a panel's own refresh
    # button pull in anything the server already knows about (a
    # background/nightly scan that ran since this tab was opened, a
    # watched-state change made from another tab or device) without a full
    # browser reload. Reuses render_page() completely unchanged rather
    # than a second, parallel computation that could drift from what a
    # real page load actually shows - just extracts the exact same
    # embedded JSON a browser reload would parse out of it.
    shows = load_shows()
    state = load_state()
    html = render_page(shows, state, server_mode=True, auth_token=AUTH_TOKEN)
    match = re.search(r'<script id="media-data" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not match:
        abort(500)
    return Response(match.group(1), mimetype="application/json")


@app.route("/api/library-history")
def api_library_history():
    """The add/removed ledger (plans\\build_library_ledger.py) + the
    tombstone list of shows that have left the library. Newest events
    first; `limit` (default 300) caps how many are returned."""
    check_auth()
    try:
        limit = max(1, min(2000, int(request.args.get("limit", 300))))
    except (TypeError, ValueError):
        limit = 300
    events = []
    ledger = os.path.join(DATA_DIR, "library_ledger.jsonl")
    if os.path.isfile(ledger):
        with open(ledger, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    events.sort(key=lambda e: e.get("at", ""), reverse=True)  # newest first
    tomb_path = os.path.join(DATA_DIR, "library_tombstones.json")
    tombstones = []
    try:
        with open(tomb_path, encoding="utf-8") as f:
            tombstones = sorted(json.load(f).values(),
                                key=lambda t: t.get("removed_at", ""), reverse=True)
    except (OSError, ValueError):
        pass
    return jsonify({"events": events, "tombstones": tombstones,
                    "total_events": len(events)})


WIKI_IMAGES_DIR = os.path.join(DATA_DIR, "wiki_images")


@app.route("/wiki-image/<path:filename>")
def wiki_image(filename):
    # No auth check deliberately - a plain <img src="..."> tag can't send
    # the X-Auth-Token header, same reason TMDB posters load as direct
    # unauthenticated <img> requests. Still validated against the current
    # dataset (not an arbitrary filesystem read) - only ever serves a file
    # some show's wiki_image_path actually points at, matching this file's
    # documented security model; send_from_directory also independently
    # guards against path traversal outside WIKI_IMAGES_DIR.
    shows = load_shows()
    known = {s["wiki_image_path"] for s in shows if s.get("wiki_image_path")}
    if f"wiki_images/{filename}" not in known:
        abort(404)
    return send_from_directory(WIKI_IMAGES_DIR, filename)


TMDB_IMAGE_FILENAME_RE = re.compile(r"^[A-Za-z0-9]+\.(jpg|jpeg|png)$")


@app.route("/tmdb-image/<kind>/<filename>")
def tmdb_image(kind, filename):
    # Same no-auth-check reasoning as /wiki-image above (a plain <img src>
    # can't send the auth header). kind is checked against the exact 4
    # known values and filename against TMDB's real naming pattern before
    # it ever touches a filesystem path or an outbound URL - deliberately
    # stricter than /wiki-image's "check against the known set" approach,
    # since this route can also *write* a new file (the self-heal fetch
    # below), not just read one.
    if kind not in SIZE_BY_KIND or not TMDB_IMAGE_FILENAME_RE.match(filename):
        abort(404)
    local_dir = os.path.join(TMDB_IMAGES_ROOT, kind)
    local_path = os.path.join(local_dir, filename)
    if not os.path.exists(local_path):
        # Not cached yet (a title matched since the last backfill run, or
        # the very first request after enrich_tmdb.py added it) - fetch it
        # once now via the same helper the pipeline scripts use, so every
        # request after this one is served locally without needing anyone
        # to remember to re-run a script.
        ensure_cached(kind, f"/{filename}")
        if not os.path.exists(local_path):
            abort(404)  # TMDB genuinely doesn't have this image (404/network failure) - nothing to serve
    return send_from_directory(local_dir, filename)


def build_override_tmdb_fields(kind, tmdb_id):
    """Full replacement tmdb_* fields for a corrected match, fetched once
    right when the Admin panel saves the override (never at render time -
    render_page() does no network I/O, it runs on every page load). Same
    field set enrich_tmdb.py itself would produce for this id, reusing its
    own extract_cast()/extract_recommendations()/extract_trailer_key()
    rather than re-deriving that logic here. kind: 'Movie' or 'TV'."""
    endpoint = "movie" if kind == "Movie" else "tv"
    # aggregate_credits kept in sync with enrich_tmdb.py's own TV fetch
    # (2026-09-13) - without it, extract_cast() silently falls back to
    # TMDB's near-empty top-level credits.cast for TV, reproducing the
    # exact sparse-cast bug (Twin Peaks: 1 person) the pipeline fetch was
    # fixed for, but only for a title corrected through this Admin path.
    append = "videos,credits,recommendations" if kind == "Movie" else "external_ids,videos,credits,aggregate_credits,recommendations"
    detail = tmdb_api_get(f"/{endpoint}/{tmdb_id}", {"append_to_response": append})
    if not detail or detail.get("success") is False:
        return None

    genre_map = {g["id"]: g["name"] for g in (tmdb_api_get(f"/genre/{endpoint}/list", {}) or {}).get("genres", [])}
    genre_ids = [g["id"] for g in (detail.get("genres") or [])]
    genres = [genre_map.get(gid) for gid in genre_ids if genre_map.get(gid)]
    name = detail.get("title") or detail.get("name")
    release_date = detail.get("release_date") or detail.get("first_air_date") or ""
    cast = extract_cast(detail)

    fields = {
        "tmdb_id": tmdb_id, "tmdb_matched": True, "tmdb_name": name,
        "tmdb_genres": genres or None, "tmdb_overview": detail.get("overview", ""),
        "tmdb_poster": detail.get("poster_path"), "tmdb_backdrop": detail.get("backdrop_path"),
        "tmdb_vote": detail.get("vote_average"), "tmdb_release_date": release_date or None,
        "tmdb_trailer_key": extract_trailer_key(detail), "tmdb_cast": cast,
        "tmdb_recommendations": extract_recommendations(detail, kind),
    }
    if kind == "Movie":
        fields["tmdb_runtime"] = detail.get("runtime") or None
        fields["tmdb_imdb_id"] = detail.get("imdb_id") or None
        crew = (detail.get("credits") or {}).get("crew") or []
        fields["tmdb_director"] = next((c.get("name") for c in crew if c.get("job") == "Director"), None)
    else:
        fields["tmdb_status"] = detail.get("status")
        fields["tmdb_tv_type"] = detail.get("type")
        fields["tmdb_number_of_seasons"] = detail.get("number_of_seasons")
        fields["tmdb_imdb_id"] = (detail.get("external_ids") or {}).get("imdb_id") or None
        fields["tmdb_creators"] = [c.get("name") for c in (detail.get("created_by") or []) if c.get("name")] or None
        # Same fields enrich_tmdb.py itself sets for a fresh TV match (see
        # its own matched_via == "tv" branch) - missing here until
        # 2026-08-26 was a real gap, not by design: a wrong-match fix
        # through the Admin panel (exactly the tool for a same-title-
        # different-production collision, confirmed real case: Kitchen
        # Nightmares' 2007-2014 original vs its 2023 revival, both titled
        # identically) would correct tmdb_status immediately but silently
        # leave tmdb_next_episode stale/empty until the next full
        # pipeline refresh - which meant Weekly TV mode still wouldn't
        # surface a just-fixed show until then either.
        runtime = (detail.get("last_episode_to_air") or {}).get("runtime") \
            or (detail.get("next_episode_to_air") or {}).get("runtime")
        fields["tmdb_episode_run_time"] = runtime or None
        next_ep = detail.get("next_episode_to_air")
        fields["tmdb_next_episode"] = {
            "season": next_ep.get("season_number"), "episode": next_ep.get("episode_number"),
            "name": next_ep.get("name"), "air_date": next_ep.get("air_date"),
        } if next_ep and next_ep.get("air_date") else None

    # Cache the new images locally right away - the whole point of a
    # correction is an immediately-right, offline-capable result, not
    # something that only becomes fully local after the next refresh.
    cache_show_images({"tmdb_poster": fields["tmdb_poster"], "tmdb_backdrop": fields["tmdb_backdrop"], "tmdb_cast": cast})
    return fields


def build_override_tmdb_episode_fields(series_id, season, episode):
    """Same tmdb_*-keyed shape as build_override_tmdb_fields(), for a
    library entry that TMDB only lists as a single episode of an existing
    series - never a standalone listing of its own. Real, not theoretical:
    "SNL50: The Anniversary Special" has no movie/tv entry, only shows up as
    S50E13 of Saturday Night Live's own show id (see fallback_identify.py).
    render_page()'s override merge needs no special case for this - it's the
    same flat tmdb_* dict either way."""
    detail = tmdb_api_get(f"/tv/{series_id}/season/{season}/episode/{episode}", {"append_to_response": "credits"})
    if not detail or detail.get("success") is False:
        return None
    series = tmdb_api_get(f"/tv/{series_id}", {}) or {}
    cast = extract_cast({"credits": {"cast": (detail.get("credits") or {}).get("guest_stars") or []}})

    fields = {
        "tmdb_id": detail.get("id"), "tmdb_matched": True,
        "tmdb_name": detail.get("name") or series.get("name"),
        "tmdb_genres": [g["name"] for g in (series.get("genres") or []) if g.get("name")] or None,
        "tmdb_overview": detail.get("overview") or series.get("overview") or "",
        "tmdb_poster": detail.get("still_path") or series.get("poster_path"),
        "tmdb_backdrop": series.get("backdrop_path"),
        "tmdb_vote": detail.get("vote_average"), "tmdb_release_date": detail.get("air_date") or None,
        "tmdb_trailer_key": None, "tmdb_cast": cast, "tmdb_recommendations": None,
        "tmdb_runtime": detail.get("runtime") or None, "tmdb_imdb_id": None,
    }
    cache_show_images({"tmdb_poster": fields["tmdb_poster"], "tmdb_backdrop": fields["tmdb_backdrop"], "tmdb_cast": cast})
    return fields


@app.route("/api/fallback-search")
def api_fallback_search():
    # Read-only, same no-auth-check convention as /api/tmdb-search - only
    # ever reaches epguides/TVmaze/Wikipedia/Wikidata/TMDB's own public
    # APIs, never touches the filesystem or user_state.json.
    key = request.args.get("show_key", "")
    shows = load_shows()
    owning_show = next((s for s in shows if show_key(s) == key), None)
    if owning_show is None:
        abort(400)

    title = owning_show.get("tmdb_name") or owning_show["title"]
    year = (owning_show.get("years") or [None])[0]
    from fallback_identify import resolve as fallback_resolve  # lazy - see the module-level comment near the other imports
    result = fallback_resolve(title, year, owning_show["type"], tmdb_api_get)
    if not result:
        return jsonify({"ok": True, "result": None})
    tmdb_id, media_type, season, episode, source = result

    if media_type == "tv_episode":
        detail = tmdb_api_get(f"/tv/{tmdb_id}/season/{season}/episode/{episode}", {})
        series = tmdb_api_get(f"/tv/{tmdb_id}", {}) or {}
        if not detail:
            return jsonify({"ok": True, "result": None})
        ep_label = f"S{season:02d}E{episode:02d}"
        preview = {
            "media_type": "tv_episode", "tmdb_id": tmdb_id, "season": season, "episode": episode,
            "title": f"{series.get('name', '')} – {ep_label} – {detail.get('name', '')}",
            "year": (detail.get("air_date") or "")[:4],
            "poster_path": detail.get("still_path") or series.get("poster_path"),
            "source": source,
        }
    else:
        endpoint = "movie" if media_type == "movie" else "tv"
        detail = tmdb_api_get(f"/{endpoint}/{tmdb_id}", {})
        if not detail:
            return jsonify({"ok": True, "result": None})
        preview = {
            "media_type": media_type, "tmdb_id": tmdb_id, "season": None, "episode": None,
            "title": detail.get("title") or detail.get("name"),
            "year": (detail.get("release_date") or detail.get("first_air_date") or "")[:4],
            "poster_path": detail.get("poster_path"),
            "source": source,
        }
    return jsonify({"ok": True, "result": preview})


@app.route("/api/tmdb-search")
def api_tmdb_search():
    # Read-only - no auth check needed (matches this file's existing
    # pattern of only gating state-changing requests), but still only
    # ever reaches the real TMDB API, never anything path/filesystem-based.
    query = (request.args.get("q") or "").strip()
    kind = request.args.get("type", "Movie")
    if not query:
        return jsonify({"results": []})
    endpoint = "movie" if kind == "Movie" else "tv"
    data = tmdb_api_get(f"/search/{endpoint}", {"query": query, "include_adult": "true"})
    results = (data or {}).get("results") or []
    out = [{
        "tmdb_id": r.get("id"),
        "title": r.get("title") or r.get("name"),
        "year": (r.get("release_date") or r.get("first_air_date") or "")[:4],
        "poster_path": r.get("poster_path"),
        "overview": (r.get("overview") or "")[:200],
    } for r in results[:12] if r.get("id")]
    return jsonify({"results": out})


@app.route("/api/tmdb-posters/<int:tmdb_id>")
def api_tmdb_posters(tmdb_id):
    kind = request.args.get("type", "Movie")
    endpoint = "movie" if kind == "Movie" else "tv"
    data = tmdb_api_get(f"/{endpoint}/{tmdb_id}/images", {})
    posters = (data or {}).get("posters") or []
    out = [{"file_path": p.get("file_path"), "vote_average": p.get("vote_average")} for p in posters[:24] if p.get("file_path")]
    return jsonify({"posters": out})


@app.route("/api/tmdb-backdrops/<int:tmdb_id>")
def api_tmdb_backdrops(tmdb_id):
    """Backdrop file_paths for a title - feeds the modal-hero backdrop
    carousel (render_engine setupBackdropCarousel). Same read-only, cache-
    fronted TMDB call /api/tmdb-posters uses; the travel build hits TMDB
    directly instead (no server)."""
    kind = request.args.get("type", "TV")
    endpoint = "movie" if kind == "Movie" else "tv"
    data = tmdb_api_get(f"/{endpoint}/{tmdb_id}/images", {})
    backdrops = (data or {}).get("backdrops") or []
    out = [b.get("file_path") for b in backdrops[:30] if b.get("file_path")]
    return jsonify({"backdrops": out})


@app.route("/api/actor/<int:person_id>")
def api_actor(person_id):
    # Read-only, same no-auth-check convention as /api/tmdb-search - only
    # ever reaches the real TMDB API or this file's own cache, never
    # touches user_state.json. Reuses tmdb_person_cache.json (already used
    # for backfilling actor IMDb ids) rather than a separate cache file -
    # same id, same file, one more field group added: bio/birthday/place
    # of birth. First click on any given actor is a real live TMDB call;
    # every click after that (by anyone, on any title) is free forever.
    cache = load_person_cache()
    key = str(person_id)
    entry = cache.get(key, {})
    if "bio_fetched" not in entry:
        detail = tmdb_api_get(f"/person/{person_id}", {})
        if detail:
            entry = dict(entry)
            entry["bio_fetched"] = True
            entry["biography"] = (detail.get("biography") or "")[:600]
            entry["birthday"] = detail.get("birthday")
            entry["place_of_birth"] = detail.get("place_of_birth")
            cache[key] = entry
            save_person_cache(cache)
    return jsonify({
        "ok": True,
        "biography": entry.get("biography") or "",
        "birthday": entry.get("birthday"),
        "place_of_birth": entry.get("place_of_birth"),
    })


@app.route("/api/override", methods=["POST"])
def api_override():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("show_key")
    field = data.get("field")
    value = data.get("value")
    if not key or not isinstance(key, str) or field not in ("poster", "tmdb_id", "backdrop", "season_offset", "genre", "missing_complete", "purged_full_backlog", "split_by_year"):
        abort(400)

    # Validated against the real current dataset, not trusted from the
    # client - same discipline every other mutating route in this file
    # already holds itself to (see /api/watched).
    shows = load_shows()
    owning_show = next((s for s in shows if show_key(s) == key), None)
    if owning_show is None:
        abort(400)

    if field == "poster":
        if not isinstance(value, str) or not value.startswith("/"):
            abort(400)
        ensure_cached("poster", value)

        def mutator(state):
            state["overrides"].setdefault(key, {})["poster_override"] = value

        update_state(mutator)
        return jsonify({"ok": True})

    if field == "backdrop":
        # A chosen TMDB backdrop file_path from the modal-hero carousel, or
        # "" to clear the pin and fall back to the default. Persisted the
        # same way as a poster override (render_engine applies
        # backdrop_override at load); download it so it's cache-served like
        # every other backdrop.
        if value in (None, ""):
            def mutator(state):
                ent = state["overrides"].get(key)
                if ent:
                    ent.pop("backdrop_override", None)
                    if not ent:
                        state["overrides"].pop(key, None)

            update_state(mutator)
            # Hand back the real TMDB default so the client can repaint the
            # hero live without a full reload.
            endpoint = "movie" if owning_show["type"] == "Movie" else "tv"
            detail = tmdb_api_get(f"/{endpoint}/{owning_show.get('tmdb_id')}", {}) if owning_show.get("tmdb_id") else None
            default_backdrop = (detail or {}).get("backdrop_path")
            return jsonify({"ok": True, "cleared": True, "default_backdrop": default_backdrop})
        if not isinstance(value, str) or not value.startswith("/"):
            abort(400)
        ensure_cached("backdrop", value)

        def mutator(state):
            state["overrides"].setdefault(key, {})["backdrop_override"] = value

        update_state(mutator)
        return jsonify({"ok": True})

    if field == "season_offset":
        # Plan item 20 - "scene season offset": a rebooted show's TMDB
        # entry restarts numbering at season 1 while scene release groups
        # keep counting forward from the original's seasons (Kitchen
        # Nightmares' 2023 reboot, tmdb:235884, is TMDB season 1 even
        # though scene files call it S10, continuing the 2007 original's
        # own 9 seasons - real_season = scene_season - offset, so 9 here).
        # TV only - a movie has no season numbering to remap. Read by
        # pipeline/scan_media.py's apply_season_offset(), which runs
        # BEFORE this show has ever been TMDB-matched, so - same reason
        # tmdb_id_override already gets a title-keyed twin below - only
        # the title-keyed entry actually gets read; this is also written
        # onto the id-keyed entry so it round-trips symmetrically with
        # the other override fields when re-fetched here.
        if owning_show["type"] != "TV":
            abort(400)
        # Year-qualified for a split-by-year title, same reasoning and same
        # 2026-09-26 incident as tmdb_id's own title-keyed twin below - an
        # unqualified key here would be just as capable of silently
        # bleeding across a split title's separate eras, even though no
        # title currently needs both season_offset AND split_by_year at
        # once. Cheap to close off symmetrically rather than leave a second
        # copy of the same footgun sitting unused.
        title_key = f"title:TV:{owning_show['title']}"
        if is_split_by_year_title(owning_show["title"]) and owning_show.get("years"):
            title_key = f"{title_key}:{owning_show['years'][0]}"

        if value in (None, "", 0):
            def mutator(state):
                ent = state["overrides"].get(key)
                if ent:
                    ent.pop("season_offset", None)
                    if not ent:
                        state["overrides"].pop(key, None)
                title_ent = state["overrides"].get(title_key)
                if title_ent:
                    title_ent.pop("season_offset", None)
                    if not title_ent:
                        state["overrides"].pop(title_key, None)

            update_state(mutator)
            return jsonify({"ok": True, "cleared": True})

        try:
            offset = int(value)
        except (TypeError, ValueError):
            abort(400)
        if offset <= 0:
            return jsonify({"ok": False, "message": "The offset should be a positive number of seasons to subtract (e.g. 9, not -9 or 0)."}), 400

        def mutator(state):
            state["overrides"].setdefault(key, {})["season_offset"] = offset
            state["overrides"].setdefault(title_key, {})["season_offset"] = offset

        update_state(mutator)
        return jsonify({"ok": True, "offset": offset})

    if field == "split_by_year":
        # 2026-09-23, real user report: two genuinely different, unrelated
        # productions sharing an exact title (1968's "The Producers" film
        # vs. the 2005 musical remake) got merged into one media_db.json
        # entry and the 2005 copy flagged as a "duplicate" to be deleted -
        # pipeline/scan_media.py's SPLIT_BY_YEAR_TITLES allowlist exists
        # for exactly this, but used to be code-only (a title only got
        # added there when someone happened to notice and edit the file).
        # This is that same mechanism, now settable through the Admin
        # panel - see scan_media.py's _load_split_by_year_titles() for the
        # read side. Movie AND TV both allowed (unlike season_offset,
        # which is TV-only by definition) - a title collision like this
        # can happen to either. Same title-keyed-twin-write as
        # season_offset above and for the identical reason: scan_media.py
        # groups files into a show BEFORE any TMDB match exists for this
        # run, so only a title-keyed lookup can ever be found at that
        # point - written onto the id-keyed entry too so it round-trips
        # symmetrically with the other override fields when re-fetched
        # here.
        split = bool(value)
        title_key = f"title:{owning_show['type']}:{owning_show['title']}"

        def mutator(state):
            if split:
                state["overrides"].setdefault(key, {})["split_by_year"] = True
                state["overrides"].setdefault(title_key, {})["split_by_year"] = True
            else:
                ent = state["overrides"].get(key)
                if ent:
                    ent.pop("split_by_year", None)
                    if not ent:
                        state["overrides"].pop(key, None)
                title_ent = state["overrides"].get(title_key)
                if title_ent:
                    title_ent.pop("split_by_year", None)
                    if not title_ent:
                        state["overrides"].pop(title_key, None)

        update_state(mutator)
        # This process is long-lived - is_split_by_year_title()'s own cache
        # (populated the first time anything in this run called it) would
        # otherwise still say "no" for a title just newly opted in here.
        reset_split_by_year_cache()
        return jsonify({"ok": True, "split_by_year": split})

    if field == "genre":
        # Manual "select its category" fix for a show TMDB never gave a
        # genre to (unmatched entirely, or matched with no genre data) -
        # applied at render time (render_engine.py's override loop) by
        # replacing tmdb_genres with a single-element list, so every
        # existing genre-badge/filter/count consumer just works unchanged,
        # same "override forces the one field, nothing else pretends to
        # know better" shape as poster/backdrop above. Movies included, not
        # just TV - "Uncategorized" isn't a TV-only problem.
        if value in (None, ""):
            def mutator(state):
                ent = state["overrides"].get(key)
                if ent:
                    ent.pop("genre_override", None)
                    if not ent:
                        state["overrides"].pop(key, None)

            update_state(mutator)
            # owning_show here is load_shows()'s output from the TOP of this
            # request, before any override gets applied (that only happens
            # in render_page()) - so its own tmdb_genres/genre are the real,
            # un-overridden TMDB (or "Uncategorized" fallback) value, handed
            # back so the client can repaint immediately instead of a
            # stale/blank badge until the next full reload.
            real_genres = owning_show.get("tmdb_genres") or [owning_show.get("genre", "Uncategorized")]
            return jsonify({"ok": True, "cleared": True, "default_genres": real_genres})
        if not isinstance(value, str) or not value.strip():
            abort(400)
        genre = value.strip()

        def mutator(state):
            state["overrides"].setdefault(key, {})["genre_override"] = genre

        update_state(mutator)
        return jsonify({"ok": True, "genre": genre})

    if field == "missing_complete":
        # Manual "mark complete" override (2026-09-13, at the user's
        # request) - the highest-priority branch in enrich_tv_coverage.py's
        # skip-completeness chain, above even the hardcoded exclusion list.
        # A plain show_key -> bool map in user_state.json, not folded into
        # `overrides` (which is TMDB-field-shaped) since this isn't a data
        # correction, it's a policy choice, same category as retention.
        mark_complete = bool(value)
        # Same title-keyed-twin fix as tmdb_id_override's own (2026-08-26)
        # and for the identical reason, confirmed real 2026-09-26 while
        # auditing after the Doctor Who incident: this was id-keyed only,
        # so an ongoing show's forced re-search landing on a different id
        # would silently drop a real "mark complete" decision with no sign
        # it happened. Year-qualified for a split-by-year title, same
        # cross-era-bleed reasoning as everywhere else that needed it.
        title_key = None
        if owning_show["type"] == "TV":
            title_key = f"title:TV:{owning_show['title']}"
            if is_split_by_year_title(owning_show["title"]) and owning_show.get("years"):
                title_key = f"{title_key}:{owning_show['years'][0]}"

        def mutator(state):
            if mark_complete:
                state.setdefault("missing_episode_overrides", {})[key] = True
                if title_key:
                    state["missing_episode_overrides"][title_key] = True
            else:
                state.get("missing_episode_overrides", {}).pop(key, None)
                if title_key:
                    state.get("missing_episode_overrides", {}).pop(title_key, None)

        update_state(mutator)
        return jsonify({"ok": True, "mark_complete": mark_complete})

    if field == "purged_full_backlog":
        # Opt-IN to unbounded backlog tracking for one delete_after_watch
        # show (2026-09-17, correcting a wrong default) - see
        # app/state_store.py's purged_backlog_tracking and pipeline/
        # track_purged_shows.py's build_tv() for what this actually
        # changes. Absence/False = bounded (new default): only episodes
        # airing after this show first went zero-owned ever count as
        # missing, since delete_after_watch means the pre-purge backlog was
        # already watched and deliberately discarded, not a gap to chase.
        full_backlog = bool(value)

        def mutator(state):
            if full_backlog:
                state.setdefault("purged_backlog_tracking", {})[key] = True
            else:
                state.get("purged_backlog_tracking", {}).pop(key, None)

        update_state(mutator)
        return jsonify({"ok": True, "full_backlog": full_backlog})

    try:
        tmdb_id = int(value)
    except (TypeError, ValueError):
        abort(400)

    media_type = data.get("media_type")
    if media_type == "tv_episode":
        try:
            season = int(data.get("season"))
            episode = int(data.get("episode"))
        except (TypeError, ValueError):
            abort(400)
        fields = build_override_tmdb_episode_fields(tmdb_id, season, episode)
        if not fields:
            return jsonify({"ok": False, "message": "Couldn't fetch that episode from TMDB - the series/season/episode numbers may no longer be current."}), 400
    else:
        kind = "Movie" if media_type == "movie" else ("TV" if media_type == "tv" else owning_show["type"])
        fields = build_override_tmdb_fields(kind, tmdb_id)
        if not fields:
            return jsonify({"ok": False, "message": "Couldn't fetch that TMDB id - check it's a real id for the right type (Movie vs TV)."}), 400

    def mutator(state):
        state["overrides"][key] = {"tmdb_id_override": tmdb_id, "tmdb_fields_override": fields}
        # Also save under the show's own title-based key, for TV specifically
        # (2026-08-26 - same real bug class already documented above at
        # enrich_tmdb.py's override_entry lookup, MasterChef US 2026-08-20,
        # now confirmed a second time on Kitchen Nightmares): this endpoint
        # only ever wrote the id-based key (show_key() of the show as it
        # existed when the fix was made). For an ONGOING show, every refresh
        # forces a fresh needs_backfill re-search regardless of the cache -
        # the moment that search re-derives the id-based key from the newly-
        # corrected tmdb_id, the id-keyed override no longer matches what
        # get looked up under the OLD id, and an unguided title search can
        # land right back on the wrong same-titled show. enrich_tmdb.py's
        # own lookup already falls back to this exact title-based key for
        # this reason - it just never had anything to find here before.
        # Movies don't get this: a movie has no "ongoing status" to force a
        # perpetual re-search once matched, so the id-keyed override alone
        # is already stable for them.
        if owning_show["type"] == "TV" and media_type != "tv_episode":
            # Year-qualified for a split-by-year title (Doctor Who, The
            # Stand) - confirmed real, 2026-09-26: an unqualified title key
            # here doesn't distinguish between a split title's separate
            # eras, so fixing one era's wrong match silently reapplied the
            # same id to the OTHER eras sharing that title on the next
            # refresh, and they got merged into one garbled show. Only
            # split-by-year titles need this - everything else still gets
            # the plain key, unchanged.
            title_key = f"title:TV:{owning_show['title']}"
            if is_split_by_year_title(owning_show["title"]) and owning_show.get("years"):
                title_key = f"{title_key}:{owning_show['years'][0]}"
            state["overrides"][title_key] = {
                "tmdb_id_override": tmdb_id, "tmdb_fields_override": fields, "title_keyed": True,
            }

    update_state(mutator)
    return jsonify({"ok": True, "fields": fields})


@app.route("/api/override/upload-poster", methods=["POST"])
def api_override_upload_poster():
    # multipart/form-data (a real file), not JSON - check_auth() itself is
    # request-shape-agnostic (header + Origin only), so it still applies
    # unchanged here, same as every other mutating route in this file.
    check_auth()
    key = request.form.get("show_key")
    if not key:
        abort(400)
    shows = load_shows()
    owning_show = next((s for s in shows if show_key(s) == key), None)
    if owning_show is None:
        abort(400)

    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"ok": False, "message": "No image file received."}), 400
    image_bytes = file.read(MAX_UPLOAD_BYTES + 1)
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False, "message": f"Image is too large ({MAX_UPLOAD_BYTES // (1024 * 1024)}MB max)."}), 400

    try:
        poster_rel, backdrop_rel = save_custom_image(image_bytes)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400

    def mutator(state):
        entry = state["overrides"].setdefault(key, {})
        entry["poster_override"] = poster_rel
        if backdrop_rel:
            entry["backdrop_override"] = backdrop_rel
        elif "backdrop_override" in entry:
            del entry["backdrop_override"]

    update_state(mutator)
    return jsonify({"ok": True, "poster": poster_rel, "backdrop": backdrop_rel})


WANTED_SEED_PATH = os.path.join(DATA_DIR, "wanted_seed.json")


@app.route("/api/wanted/add", methods=["POST"])
def api_wanted_add():
    """Add a title to data\\wanted_seed.json - a show/movie the user owns
    nothing of but wants. pipeline\\enrich_wanted.py injects it into
    media_db.json with missing_seasons pre-populated so the companion tool's
    existing sweep picks it up. `want`: "all" or a list of season numbers
    (TV only)."""
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    kind = (data.get("type") or "").lower()
    tmdb_id = data.get("tmdb_id")
    want = data.get("want", "all")
    requested_by = data.get("requested_by")
    if kind not in ("movie", "tv") or not isinstance(tmdb_id, int):
        abort(400)
    if want != "all":
        if not isinstance(want, list) or not all(isinstance(n, int) and n > 0 for n in want):
            abort(400)
        want = sorted(set(want))
    if requested_by not in (None, "you", "partner"):
        requested_by = None

    seed = {"shows": []}
    if os.path.exists(WANTED_SEED_PATH):
        try:
            with open(WANTED_SEED_PATH, encoding="utf-8") as f:
                seed = json.load(f)
        except ValueError:
            pass
    seed.setdefault("shows", [])
    typ = "movie" if kind == "movie" else "tv"
    if any(e.get("tmdb_id") == tmdb_id and (e.get("type") or "tv") == typ for e in seed["shows"]):
        return jsonify({"ok": False, "message": "Already on the wanted list."}), 400
    seed["shows"].append({
        "tmdb_id": tmdb_id, "type": typ, "want": want,
        "requested_by": requested_by,
        "added_at": datetime.now(timezone.utc).isoformat(),
    })
    tmp = WANTED_SEED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2, ensure_ascii=False)
    os.replace(tmp, WANTED_SEED_PATH)
    if not start_refresh(["pipeline/enrich_wanted.py"]):
        return jsonify({"ok": True, "message": "Added - a refresh is already running; it'll show up once that finishes."})
    return jsonify({"ok": True, "message": "Added to the wanted list - fetching it now."})


@app.route("/api/wanted/remove", methods=["POST"])
def api_wanted_remove():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    tmdb_id = data.get("tmdb_id")
    typ = (data.get("type") or "tv").lower()
    typ = "movie" if typ == "movie" else "tv"
    if not isinstance(tmdb_id, int):
        abort(400)
    if not os.path.exists(WANTED_SEED_PATH):
        return jsonify({"ok": True, "message": "Nothing to remove."})
    with open(WANTED_SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    before = len(seed.get("shows", []))
    seed["shows"] = [e for e in seed.get("shows", [])
                     if not (e.get("tmdb_id") == tmdb_id and (e.get("type") or "tv") == typ)]
    tmp = WANTED_SEED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2, ensure_ascii=False)
    os.replace(tmp, WANTED_SEED_PATH)
    removed = before - len(seed["shows"])
    start_refresh(["pipeline/scan_media.py", "pipeline/enrich_tmdb.py",
                   "pipeline/consolidate.py", "pipeline/enrich_tv_coverage.py",
                   "pipeline/enrich_wanted.py"])
    return jsonify({"ok": True, "message": f"Removed {removed} from the wanted list; rebuilding."})


def _read_streaming_seed():
    seed = {"shows": []}
    if os.path.exists(STREAMING_SEED_PATH):
        try:
            with open(STREAMING_SEED_PATH, encoding="utf-8") as f:
                seed = json.load(f)
        except ValueError:
            pass
    seed.setdefault("shows", [])
    return seed


def _write_streaming_seed(seed):
    tmp = STREAMING_SEED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STREAMING_SEED_PATH)


@app.route("/api/streaming/follow", methods=["POST"])
def api_streaming_follow():
    """Follow a Netflix / Prime Video title so it renders in the library as
    if owned (a live subscription is a live licence). Normally called by
    the companion tool's "Add to library" button; also usable directly.
    {tmdb_id:int, type:"tv"|"movie", provider:"netflix"|"prime",
     title?:str, deep_link?:str}."""
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    tmdb_id = data.get("tmdb_id")
    typ = (data.get("type") or "tv").lower()
    typ = "movie" if typ == "movie" else "tv"
    provider = (data.get("provider") or "").lower()
    if not isinstance(tmdb_id, int) or provider not in ("netflix", "prime"):
        abort(400)
    seed = _read_streaming_seed()
    if any(e.get("tmdb_id") == tmdb_id and (e.get("type") or "tv") == typ for e in seed["shows"]):
        return jsonify({"ok": False, "message": "Already following this title."}), 400
    seed["shows"].append({
        "tmdb_id": tmdb_id, "type": typ, "provider": provider,
        "title": data.get("title") or None,
        "deep_link": data.get("deep_link") or None,
        "added_at": datetime.now(timezone.utc).isoformat(),
    })
    _write_streaming_seed(seed)
    start_refresh(["pipeline/enrich_streaming.py"])
    return jsonify({"ok": True, "message": "Following - fetching it now."})


@app.route("/api/streaming/release", methods=["POST"])
def api_streaming_release():
    """Unfollow / release a streaming title (licence lapsed, lost interest).
    Drops it from the seed, logs a `streaming_released` event to the library
    ledger, rebuilds. {tmdb_id:int, type:"tv"|"movie", provider?:str}."""
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    tmdb_id = data.get("tmdb_id")
    typ = (data.get("type") or "tv").lower()
    typ = "movie" if typ == "movie" else "tv"
    if not isinstance(tmdb_id, int):
        abort(400)
    seed = _read_streaming_seed()
    gone = [e for e in seed["shows"] if e.get("tmdb_id") == tmdb_id and (e.get("type") or "tv") == typ]
    if not gone:
        return jsonify({"ok": True, "message": "Not following that title."})
    seed["shows"] = [e for e in seed["shows"] if e not in gone]
    _write_streaming_seed(seed)
    try:
        ledger = os.path.join(DATA_DIR, "library_ledger.jsonl")
        with open(ledger, "a", encoding="utf-8") as f:
            for e in gone:
                f.write(json.dumps({
                    "at": datetime.now(timezone.utc).isoformat(),
                    "event": "streaming_released",
                    "identity": f"tmdb:{'TV' if typ == 'tv' else 'Movie'}:{tmdb_id}",
                    "tmdb_id": tmdb_id, "type": "TV" if typ == "tv" else "Movie",
                    "title": e.get("title") or "",
                    "provider": e.get("provider"),
                    "reason": "released_streaming",
                }) + "\n")
    except OSError:
        pass
    start_refresh(["pipeline/enrich_streaming.py"])
    return jsonify({"ok": True, "message": "Released - it'll drop off the shelf on the next render."})


@app.route("/api/physical/add", methods=["POST"])
def api_physical_add():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    kind = data.get("type")
    tmdb_id = data.get("tmdb_id")
    spine_title = (data.get("spine_title") or "").strip()
    owned_seasons = data.get("owned_seasons")

    if kind not in ("movie", "tv") or not isinstance(tmdb_id, int) or not spine_title:
        abort(400)
    if kind == "tv" and (not isinstance(owned_seasons, int) or owned_seasons < 1):
        abort(400)

    if not os.path.exists(PHYSICAL_SEED_PATH):
        abort(500)
    with open(PHYSICAL_SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    bucket = "movies" if kind == "movie" else "tv"
    if any(item.get("tmdb_id") == tmdb_id for item in seed.get(bucket, [])):
        return jsonify({"ok": False, "message": "That title is already in the physical collection."}), 400

    entry = {"tmdb_id": tmdb_id, "spine_title": spine_title}
    if kind == "tv":
        entry["owned_seasons"] = owned_seasons
    seed.setdefault(bucket, []).append(entry)

    tmp = PHYSICAL_SEED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PHYSICAL_SEED_PATH)

    if not start_refresh(["pipeline/enrich_physical.py"]):
        return jsonify({"ok": True, "message": "Saved - a refresh is already running, the new disc will appear once it finishes."})
    return jsonify({"ok": True, "message": "Saved - regenerating the physical collection now."})


@app.route("/api/amazon/add", methods=["POST"])
def api_amazon_add():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    kind = data.get("type")
    tmdb_id = data.get("tmdb_id")
    title = (data.get("title") or "").strip()
    owned_seasons = data.get("owned_seasons")

    if kind not in ("movie", "tv") or not isinstance(tmdb_id, int) or not title:
        abort(400)
    if kind == "tv" and owned_seasons is not None and not isinstance(owned_seasons, int):
        abort(400)

    if not os.path.exists(AMAZON_SEED_PATH):
        abort(500)
    with open(AMAZON_SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    bucket = "movies" if kind == "movie" else "tv"
    if any(item.get("tmdb_id") == tmdb_id for item in seed.get(bucket, [])):
        return jsonify({"ok": False, "message": "That title is already in your Amazon purchases."}), 400

    entry = {"tmdb_id": tmdb_id, "title": title, "added_at": datetime.now(timezone.utc).isoformat()}
    if kind == "tv" and owned_seasons:
        entry["owned_seasons"] = owned_seasons
    seed.setdefault(bucket, []).append(entry)

    tmp = AMAZON_SEED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2, ensure_ascii=False)
    os.replace(tmp, AMAZON_SEED_PATH)

    if not start_refresh(["pipeline/enrich_amazon.py"]):
        return jsonify({"ok": True, "message": "Saved - a refresh is already running, this will appear once it finishes."})
    return jsonify({"ok": True, "message": "Saved - regenerating your Amazon purchases now."})


@app.route("/api/watched", methods=["POST"])
def api_watched():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path")
    watched = bool(data.get("watched"))
    if not path or not isinstance(path, str):
        abort(400)

    shows = load_shows()
    owning_show = next((s for s in shows if any(e["path"] == path for e in s["entries"])), None)
    if owning_show is None:
        abort(400)

    at = datetime.now(timezone.utc).isoformat()
    key = show_key(owning_show)

    def mutator(state):
        state["watched"][path] = {"watched": watched, "at": at}
        if watched:
            promote_recently_watched(state, key)

    update_state(mutator)
    return jsonify({"ok": True, "at": at})


@app.route("/api/watched-batch", methods=["POST"])
def api_watched_batch():
    # Backs the season-group "mark whole season watched" checkbox
    # (app/render_engine.py's toggleSeasonWatched) - same shape as
    # /api/watched, just N paths in one request/one state write instead of
    # N round-trips for a 20+ episode season.
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    paths = data.get("paths")
    watched = bool(data.get("watched"))
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        abort(400)

    shows = load_shows()
    known_paths = {e["path"] for s in shows for e in s["entries"]}
    if not all(p in known_paths for p in paths):
        abort(400)
    owning_show = next((s for s in shows if any(e["path"] in paths for e in s["entries"])), None)

    at = datetime.now(timezone.utc).isoformat()
    key = show_key(owning_show) if owning_show else None

    def mutator(state):
        for path in paths:
            state["watched"][path] = {"watched": watched, "at": at}
        if watched and key:
            promote_recently_watched(state, key)

    update_state(mutator)
    return jsonify({"ok": True, "at": at})


@app.route("/api/unwatch-show", methods=["POST"])
def api_unwatch_show():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("show_key")
    if not isinstance(key, str):
        abort(400)

    shows = load_shows()
    target = next((s for s in shows if show_key(s) == key), None)
    if target is None:
        abort(400)
    paths = [e["path"] for e in target["entries"]]

    now = datetime.now(timezone.utc).isoformat()

    def mutator(state):
        for path in paths:
            state["watched"][path] = {"watched": False, "at": now}

    update_state(mutator)
    return jsonify({"ok": True, "at": now})


@app.route("/api/refresh-next-episode", methods=["POST"])
def api_refresh_next_episode():
    # Per-panel refresh for What's New/Continue Watching/Recently Watched's
    # next-episode-air badges - a live TMDB re-check for just the handful
    # of TV shows visible in one panel (up to 12), not the full 13-script
    # pipeline. Same tmdb_next_episode shape enrich_tmdb.py's own main()
    # loop builds, so a real full refresh later recomputes the identical
    # thing - this just doesn't make you wait for one to see today's date.
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    keys = data.get("show_keys")
    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
        abort(400)

    shows = load_shows()
    by_key = {show_key(s): s for s in shows}
    updated = {}
    for k in keys:
        s = by_key.get(k)
        if not s or s["type"] != "TV" or not s.get("tmdb_matched") or not s.get("tmdb_id"):
            continue
        detail = tmdb_api_get(f"/tv/{s['tmdb_id']}", {})
        if not detail:
            continue
        next_ep = detail.get("next_episode_to_air")
        next_episode = {
            "season": next_ep.get("season_number"), "episode": next_ep.get("episode_number"),
            "name": next_ep.get("name"), "air_date": next_ep.get("air_date"),
        } if next_ep and next_ep.get("air_date") else None
        updated[k] = {"tmdb_next_episode": next_episode, "tmdb_status": detail.get("status")}

    if updated and not _any_job_running():
        # Re-read DB_PATH fresh rather than reusing `shows` above - load_shows()
        # merges in physical_db.json/amazon_db.json entries too, and writing
        # that merged list back to DB_PATH would corrupt it with content that
        # doesn't belong there. Only the real digital media_db.json gets
        # touched, same atomic write every pipeline script now uses.
        #
        # Skipped entirely while any pipeline/refresh job is running: this
        # endpoint holds no lock, and racing a background consolidate/enrich
        # step on the same file silently loses one side's write (the
        # 2026-08-27 PermissionError on media_db.json.tmp is the same fault).
        # The next full refresh recomputes tmdb_next_episode anyway, so
        # skipping here only costs a few minutes' staleness on the air-date
        # badge, never correctness. _dedupe_show_records() + atomic_replace()
        # match what every pipeline writer does.
        with open(DB_PATH, encoding="utf-8") as f:
            fresh_shows = _dedupe_show_records(json.load(f))
        by_key_fresh = {show_key(s): s for s in fresh_shows}
        for k, fields in updated.items():
            target = by_key_fresh.get(k)
            if target:
                target.update(fields)
        tmp = DB_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(fresh_shows, f, indent=2)
        atomic_replace(tmp, DB_PATH)

    return jsonify({"ok": True, "updated": updated})


@app.route("/api/confirm-runtime", methods=["POST"])
def api_confirm_runtime():
    # The other half of pipeline/detect_runtime_mismatch.py's own
    # runtime_confirmed mechanism - that script already reads this and
    # state_store.py's prune_state() already protects it from being
    # pruned, but until now nothing ever WROTE it - the only way to clear
    # a false-positive "Unknown" flag (a legitimate extended/alternate cut
    # running longer or shorter than TMDB's single cached runtime number)
    # was hand-editing user_state.json directly. Real user-reported bug,
    # 2026-08-21 (Waterworld's Ulysses Cut, Radiohead's In Rainbows - From
    # the Basement) - both real, correct TMDB matches, just genuinely
    # different cuts than TMDB's one tracked runtime.
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("show_key")
    if not isinstance(key, str):
        abort(400)

    shows = load_shows()
    owning_show = next((s for s in shows if show_key(s) == key), None)
    if owning_show is None:
        abort(400)

    # Deliberately title-keyed, not show_key()-keyed - matches
    # detect_runtime_mismatch.py's own load_confirmed_runtime_titles() and
    # state_store.py's prune-exemption for this exact field, both of which
    # already use this shape. Movies only (same scope the detection script
    # itself is limited to - TV runtimes are too noisy to trust).
    if owning_show["type"] != "Movie":
        abort(400)
    title_key = f"title:{owning_show['type']}:{owning_show['title']}"

    def mutator(state):
        state["overrides"].setdefault(title_key, {})["runtime_confirmed"] = True

    update_state(mutator)
    return jsonify({"ok": True})


@app.route("/api/retention", methods=["POST"])
def api_retention():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("show_key")
    policy = data.get("policy")
    if policy not in ("archive", "delete_after_watch", None) or not isinstance(key, str):
        abort(400)

    shows = load_shows()
    known_keys = {show_key(s) for s in shows}
    if key not in known_keys:
        abort(400)

    def mutator(state):
        if policy is None or policy == "archive":
            state["retention"].pop(key, None)
        else:
            state["retention"][key] = policy

    update_state(mutator)
    return jsonify({"ok": True})


@app.route("/api/retention-override", methods=["POST"])
def api_retention_override():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path")
    override = bool(data.get("override"))
    if not path or not isinstance(path, str):
        abort(400)

    shows = load_shows()
    known_paths = {e["path"] for s in shows for e in s["entries"]}
    if path not in known_paths:
        abort(400)

    def mutator(state):
        if override:
            state["retention_overrides"][path] = True
        else:
            state["retention_overrides"].pop(path, None)

    update_state(mutator)
    return jsonify({"ok": True})


@app.route("/api/resolve-duplicate", methods=["POST"])
def api_resolve_duplicate():
    """The Dashboard's duplicate picker - moves every path in discard_paths
    to Pending Deletion\\<show title>\\, same as run_purge()'s move (never
    deletes). Whichever copy the user didn't click "Keep" is what shows up
    here - could be the originally-computed keep_path too, if the user
    overrides scan_media.py's own pick."""
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    show_title = data.get("show_title")
    discard_paths = data.get("discard_paths")
    if not show_title or not isinstance(discard_paths, list) or not discard_paths:
        abort(400)
    if _any_job_running():
        return jsonify({"ok": False, "message": _busy_message()}), 409

    shows = load_shows()
    show = next((s for s in shows if s["title"] == show_title), None)
    if not show:
        abort(400)
    moved, _moved_bytes, skipped = _move_duplicate_discards(show, discard_paths)

    if moved:
        start_refresh(STARTUP_SCRIPTS)
    return jsonify({"ok": True, "moved": moved, "skipped": skipped})


def _any_job_running():
    return (
        _refresh_status["running"] or _purge_status["running"]
        or _autofile_status["running"] or _dup_resolve_status["running"]
        or _file_queue_status["running"] or _remove_forever_status["running"]
    )


def _busy_message():
    if _refresh_status["running"]:
        return "A refresh is currently running - try again once it finishes."
    if _purge_status["running"]:
        return "A purge is currently running - try again once it finishes."
    if _autofile_status["running"]:
        return "Auto-filing is currently running - try again once it finishes."
    if _dup_resolve_status["running"]:
        return "A duplicate resolve batch is currently running - try again once it finishes."
    if _file_queue_status["running"]:
        return "The download queue is being filed - try again once it finishes."
    if _remove_forever_status["running"]:
        return "Remove Forever is currently running - try again once it finishes."
    return "Something else is already running."


def _move_duplicate_discards(show, discard_paths):
    """Shared by /api/resolve-duplicate and the batch job below - moves
    every path in discard_paths to Pending Deletion\\<show title>\\, only
    ever a path this show's own duplicate-detection actually flagged
    (never a bare client-supplied path). Returns (moved, moved_bytes,
    skipped) - never raises on an individual file (a stale/already-gone
    path or a destination collision just gets skipped, same as run_purge()'s
    own per-file tolerance), so one bad path in a big batch can't abort
    everything after it."""
    valid_paths = {d["keep_path"] for d in show.get("duplicates", [])}
    valid_paths |= {p for d in show.get("duplicates", []) for p in d["extra_paths"]}

    moved, skipped = [], []
    moved_bytes = 0
    for path in discard_paths:
        if path not in valid_paths or not os.path.exists(path):
            skipped.append(path)
            continue
        dest_dir = os.path.join(PENDING_DELETION_DIR, safe_folder_name(show["title"]))
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, os.path.basename(path.rstrip("\\/")))
        if os.path.exists(dest_path):
            skipped.append(path)
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        shutil.move(path, dest_path)
        moved.append(path)
        moved_bytes += size
    return moved, moved_bytes, skipped


def start_dup_resolve_batch(resolutions):
    """Primes _dup_resolve_status and kicks off run_dup_resolve_batch in a
    background thread - same reasoning as start_purge()/start_autofile():
    a big batch (the Doctor Who case this was built for - 49 groups, tens
    of GB, cross-drive) can genuinely take minutes, confirmed real by a
    client-side timeout during testing even though the move itself kept
    working server-side the whole time. Returns False if anything that
    could race with it reading/moving the same files is already running."""
    with _dup_resolve_lock:
        if _any_job_running():
            return False
        _dup_resolve_status.update({
            "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "error": None,
            "moved_count": 0, "moved_bytes": 0, "skipped_count": 0,
            "total_count": sum(len(r.get("discard_paths") or []) for r in resolutions),
        })
        threading.Thread(target=run_dup_resolve_batch, args=(resolutions,), daemon=True).start()
    return True


@app.route("/api/resolve-duplicates-batch", methods=["POST"])
def api_resolve_duplicates_batch():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    resolutions = data.get("resolutions")
    if not isinstance(resolutions, list) or not resolutions:
        abort(400)
    for r in resolutions:
        if not isinstance(r, dict) or not r.get("show_title") or not isinstance(r.get("discard_paths"), list):
            abort(400)
    if not start_dup_resolve_batch(resolutions):
        return jsonify({"ok": False, "message": _busy_message()}), 409
    return jsonify({"ok": True})


@app.route("/api/resolve-duplicates-batch/status")
def api_resolve_duplicates_batch_status():
    check_auth()
    return jsonify(_dup_resolve_status)


def run_dup_resolve_batch(resolutions):
    """Runs every {show_title, discard_paths} resolution from the
    Dashboard's "Process selected" button, one show at a time, in a
    background thread - see start_dup_resolve_batch(). Exactly one
    start_refresh() at the end regardless of how many groups/shows were
    in the batch, not one per group - that's the whole point of batching
    this instead of the old one-call-per-click flow. Writes a durable
    per-run audit log to logs\\, same convention as run_purge()/
    run_autofile()."""
    log_lines = [f"Duplicate resolve batch started {datetime.now(timezone.utc).isoformat()}"]
    try:
        shows = load_shows()
        shows_by_title = {s["title"]: s for s in shows}

        for res in resolutions:
            show_title = res["show_title"]
            discard_paths = res["discard_paths"]
            show = shows_by_title.get(show_title)
            if not show:
                _dup_resolve_status["skipped_count"] += len(discard_paths)
                log_lines.append(f"SKIPPED (show not found - {show_title}): {len(discard_paths)} path(s)")
                continue
            moved, moved_bytes, skipped = _move_duplicate_discards(show, discard_paths)
            _dup_resolve_status["moved_count"] += len(moved)
            _dup_resolve_status["moved_bytes"] += moved_bytes
            _dup_resolve_status["skipped_count"] += len(skipped)
            for path in moved:
                log_lines.append(f"MOVED ({show_title}): {path}")
            for path in skipped:
                log_lines.append(f"SKIPPED (already gone, or a name collision at the destination - {show_title}): {path}")

        log_lines.append(
            f"Done: {_dup_resolve_status['moved_count']} moved, {_dup_resolve_status['skipped_count']} skipped."
        )
    except Exception as ex:
        _dup_resolve_status["error"] = str(ex)
        log_lines.append(f"ERROR: {ex}")
    finally:
        _dup_resolve_status["running"] = False
        _dup_resolve_status["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, "dup_resolve_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines))
            prune_logs(LOG_DIR, "dup_resolve")
        except OSError:
            pass
        if _dup_resolve_status["moved_count"] > 0:
            start_refresh(STARTUP_SCRIPTS)


def start_purge():
    """Primes _purge_status and kicks off run_purge in a background thread -
    moving potentially many GB across drives can take a while, so this must
    never block the request/response cycle. Returns False if a purge (or
    anything else that could race with it reading/moving the same files) is
    already running."""
    with _purge_lock:
        if _any_job_running():
            return False
        _purge_status.update({
            "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "error": None,
            "moved_count": 0, "moved_bytes": 0, "skipped_count": 0, "total_count": 0,
        })
        threading.Thread(target=run_purge, daemon=True).start()
    return True


@app.route("/api/purge-pending-deletion", methods=["POST"])
def api_purge_pending_deletion():
    check_auth()
    if not start_purge():
        return jsonify({"ok": False, "message": _busy_message()}), 409
    return jsonify({"ok": True})


@app.route("/api/purge-pending-deletion/status")
def api_purge_pending_deletion_status():
    check_auth()
    return jsonify(_purge_status)


@app.route("/api/remove-forever", methods=["POST"])
def api_remove_forever():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("show_key")
    if not key or not isinstance(key, str):
        abort(400)
    # Confirm it's a real, current show before even starting the
    # background job - same discipline as every other mutating route here
    # (see /api/override's own comment).
    if next((s for s in load_shows() if show_key(s) == key), None) is None:
        return jsonify({"ok": False, "message": "That title is no longer in the library."}), 200
    if not start_remove_forever(key):
        return jsonify({"ok": False, "message": _busy_message()}), 409
    return jsonify({"ok": True})


@app.route("/api/remove-forever/status")
def api_remove_forever_status():
    check_auth()
    return jsonify(_remove_forever_status)


def run_purge():
    """Moves (never deletes) every watched, delete-after-watch-eligible,
    non-overridden file into PENDING_DELETION_DIR, regardless of which
    scanned root/drive it currently lives on. Runs in a background thread -
    see start_purge(). Writes a durable per-run audit log to logs\\ so
    there's always a record of exactly what moved from where."""
    log_lines = [f"Purge to Pending Deletion started {datetime.now(timezone.utc).isoformat()}"]
    try:
        shows = load_shows()
        state = load_state()
        retention = state.get("retention", {})
        retention_overrides = state.get("retention_overrides", {})
        watched_state = state.get("watched", {})

        eligible = []
        for s in shows:
            if s["type"] == "Other":
                continue  # not confidently identified - handled manually
            if retention.get(show_key(s)) != "delete_after_watch":
                continue
            for e in s["entries"]:
                watched, _ = resolve_watched(e["path"], watched_state)
                if not watched or retention_overrides.get(e["path"]):
                    continue
                eligible.append((s, e))
        _purge_status["total_count"] = len(eligible)

        for s, e in eligible:
            src = e["path"]
            if not os.path.exists(src):
                _purge_status["skipped_count"] += 1
                log_lines.append(f"SKIPPED (already gone - moved by a previous run, or gone some other way): {src}")
                continue
            dest_dir = os.path.join(PENDING_DELETION_DIR, safe_folder_name(s["title"]))
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, os.path.basename(src.rstrip("\\/")))
            if os.path.exists(dest_path):
                _purge_status["skipped_count"] += 1
                log_lines.append(f"SKIPPED (already exists at destination): {src}")
                continue
            shutil.move(src, dest_path)
            _purge_status["moved_count"] += 1
            _purge_status["moved_bytes"] += e.get("size_bytes", 0)
            log_lines.append(f"MOVED: {src} -> {dest_path}")

        log_lines.append(f"Done: {_purge_status['moved_count']} moved, {_purge_status['skipped_count']} skipped.")
    except Exception as ex:
        _purge_status["error"] = str(ex)
        log_lines.append(f"ERROR: {ex}")
    finally:
        _purge_status["running"] = False
        _purge_status["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, "purge_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines))
            prune_logs(LOG_DIR, "purge")
        except OSError:
            pass
        if _purge_status["moved_count"] > 0:
            start_refresh(STARTUP_SCRIPTS)


def start_remove_forever(key):
    """Primes _remove_forever_status and kicks off run_remove_forever in a
    background thread - same reasoning as start_purge(): a full series can
    be many GB, no reason to block the request thread on it."""
    with _remove_forever_lock:
        if _any_job_running():
            return False
        _remove_forever_status.update({
            "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "error": None,
            "moved_count": 0, "moved_bytes": 0, "skipped_count": 0, "total_count": 0,
            "title": None,
        })
        threading.Thread(target=run_remove_forever, args=(key,), daemon=True).start()
    return True


def run_remove_forever(key):
    """Moves every real file for the show matching `key` to Pending
    Deletion (never a hard delete - same convention as Purge), then clears
    ONLY its retention entry - not watched/top10/no_interest/
    recently_watched, per the user's explicit scope (2026-09-13). Clearing
    retention is what actually makes this "stop tracking," not a
    separate blocklist: pipeline/track_purged_shows.py only ever reinjects
    a placeholder for a retention key still set to delete_after_watch, so
    once that key is gone, nothing re-adds this title until a real new
    file for it shows up on its own (a fresh download, a manual re-add) -
    exactly the "not a permanent block" behavior asked for."""
    log_lines = [f"Remove Forever started {datetime.now(timezone.utc).isoformat()} for show_key={key}"]
    try:
        shows = load_shows()
        show = next((s for s in shows if show_key(s) == key), None)
        if show is None:
            _remove_forever_status["error"] = "That title is no longer in the library."
            return
        _remove_forever_status["title"] = show["title"]
        entries = show.get("entries", [])
        _remove_forever_status["total_count"] = len(entries)

        left_behind = []
        for e in entries:
            src = e.get("path")
            if not src or not os.path.exists(src):
                _remove_forever_status["skipped_count"] += 1
                log_lines.append(f"SKIPPED (already gone): {src}")
                continue
            dest_dir = os.path.join(PENDING_DELETION_DIR, safe_folder_name(show["title"]))
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, os.path.basename(src.rstrip("\\/")))
            if os.path.exists(dest_path):
                # A same-named file already sitting in Pending Deletion
                # (e.g. from an earlier regular Purge, not yet cleared by
                # hand) - src is left in place, NOT silently counted as
                # "handled" the way skipped_count elsewhere in this file
                # implies. Tracked separately so retention below only
                # clears once every real file is actually gone - clearing
                # it unconditionally would have stopped tracking a show
                # while this file was still sitting untouched in the real
                # library (confirmed real gap, 2026-09-14).
                _remove_forever_status["skipped_count"] += 1
                left_behind.append(src)
                log_lines.append(f"LEFT IN PLACE (destination already exists - clear Pending Deletion\\{safe_folder_name(show['title'])}\\ by hand first): {src}")
                continue
            shutil.move(src, dest_path)
            _remove_forever_status["moved_count"] += 1
            _remove_forever_status["moved_bytes"] += e.get("size_bytes", 0)
            log_lines.append(f"MOVED: {src} -> {dest_path}")

        if left_behind:
            _remove_forever_status["error"] = (
                f"{len(left_behind)} file(s) couldn't be moved - a same-named file already sits in "
                f"Pending Deletion. Retention was left as-is (still tracked) so nothing was silently "
                f"dropped. Clear Pending Deletion\\{safe_folder_name(show['title'])}\\ by hand, then try again."
            )
            log_lines.append(
                f"Stopped short: {_remove_forever_status['moved_count']} moved, "
                f"{len(left_behind)} left in place, retention NOT cleared."
            )
        else:
            def mutator(state):
                state.get("retention", {}).pop(key, None)

            update_state(mutator)
            log_lines.append(
                f"Done: {_remove_forever_status['moved_count']} moved, "
                f"{_remove_forever_status['skipped_count']} skipped, retention cleared."
            )
    except Exception as ex:
        _remove_forever_status["error"] = str(ex)
        log_lines.append(f"ERROR: {ex}")
    finally:
        _remove_forever_status["running"] = False
        _remove_forever_status["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, "remove_forever_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines))
            prune_logs(LOG_DIR, "remove_forever")
        except OSError:
            pass
        # Always resync, even if nothing was moved (an already-purged_tracked
        # placeholder has zero files but still needs this run to drop its
        # retention-driven reinjection) - unlike Purge, which only bothers
        # refreshing when it actually moved something real.
        if not _remove_forever_status["error"]:
            start_refresh(STARTUP_SCRIPTS)


def start_autofile():
    """Primes _autofile_status and kicks off run_autofile in a background
    thread - see start_purge() for why this can't run inline. Returns False
    if autofiling (or anything else that could race with it) is already
    running."""
    with _autofile_lock:
        if _any_job_running():
            return False
        _autofile_status.update({
            "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "error": None,
            "filed_count": 0, "filed_bytes": 0, "skipped_count": 0, "total_count": 0,
            "flagged": [],
        })
        threading.Thread(target=run_autofile, daemon=True).start()
    return True


@app.route("/api/autofile-queue", methods=["POST"])
def api_autofile_queue():
    check_auth()
    if not start_autofile():
        return jsonify({"ok": False, "message": _busy_message()}), 409
    return jsonify({"ok": True})


@app.route("/api/autofile-queue/status")
def api_autofile_queue_status():
    check_auth()
    return jsonify(_autofile_status)


@app.route("/api/file-queue/plan", methods=["POST"])
def api_file_queue_plan():
    """Scan + TMDB-verify the incoming queue and return the plan
    (destinations, collisions, default selections, stale count) for the
    File-Queue modal. Read-only - moves nothing. See pipeline/file_queue.py.

    Optional body field "auto_refresh": true - if the plan comes back
    data_stale (something in the queue is newer than media_db.json, see
    file_queue.build_plan()'s own note), kicks off the same background
    refresh a manual "Refresh library" click does, rather than just
    reporting the staleness and leaving the caller to notice and act on
    it.
    Non-blocking either way - this request still returns the plan it just
    built (still behind the data), a refresh_triggered flag says whether
    a catch-up refresh was actually started; the caller re-polls
    /api/refresh/status and calls this route again once it finishes."""
    check_auth()
    if _any_job_running():
        return jsonify({"ok": False, "message": _busy_message()}), 409
    data = request.get_json(force=True, silent=True) or {}
    try:
        plan = file_queue.build_plan()
    except Exception as ex:  # noqa: BLE001 - surface any planning failure to the UI
        return jsonify({"ok": False, "message": f"Couldn't build the plan: {ex}"}), 500
    refresh_triggered = False
    if plan.get("data_stale") and data.get("auto_refresh"):
        refresh_triggered = start_refresh()
    return jsonify({"ok": True, "plan": plan, "refresh_triggered": refresh_triggered})


def _run_file_queue(selected, sweep_junk):
    log = []
    try:
        plan = file_queue.build_plan()
        wanted = set(selected)
        chosen = [it for it in plan["items"] if it["src_video"] in wanted]
        _file_queue_status["total"] = len(chosen)
        filed, replaced, skipped, failed = file_queue.execute(chosen, log)
        swept = sweep_failed = 0
        if sweep_junk:
            swept, sweep_failed = file_queue.sweep_junk_folders(
                log, exclude_dirs=file_queue.load_companion_ready_dirs(),
            )
        _file_queue_status.update(
            filed=filed, replaced=replaced, skipped=skipped, failed=failed + sweep_failed,
            swept=swept,
        )
        log.append(
            f"Done: {filed} filed, {replaced} replaced, {skipped} skipped, {failed} failed"
            + (f"; {swept} junk folder(s) swept, {sweep_failed} failed" if sweep_junk else "") + "."
        )
    except Exception as ex:  # noqa: BLE001
        _file_queue_status["error"] = str(ex)
        log.append(f"ERROR: {ex}")
    finally:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, "file_queue_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log))
            prune_logs(LOG_DIR, "file_queue")
            _file_queue_status["log_path"] = log_path
        except OSError:
            pass
        _file_queue_status["running"] = False
        _file_queue_status["finished_at"] = datetime.now(timezone.utc).isoformat()
        if _file_queue_status["filed"] or _file_queue_status["replaced"]:
            start_refresh(STARTUP_SCRIPTS)


@app.route("/api/file-queue/execute", methods=["POST"])
def api_file_queue_execute():
    """Move the selected queue items into the archive. Body:
    {"selected": ["<src_video path>", ...], "sweep_junk": bool}. Runs in a
    background thread; poll /api/file-queue/status. A refresh is kicked off
    afterward if anything moved (so media_db.json catches the new Archive
    locations). sweep_junk moves empty/junk/sample-only queue folders to
    Pending Deletion alongside the filing - see pipeline/file_queue.py."""
    check_auth()
    body = request.get_json(silent=True) or {}
    selected = body.get("selected") or []
    sweep_junk = bool(body.get("sweep_junk"))
    if not selected and not sweep_junk:
        return jsonify({"ok": False, "message": "Nothing selected."}), 400
    with _file_queue_lock:
        if _any_job_running():
            return jsonify({"ok": False, "message": _busy_message()}), 409
        _file_queue_status.update({
            "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "error": None,
            "filed": 0, "replaced": 0, "skipped": 0, "failed": 0, "swept": 0,
            "total": len(selected), "log_path": None,
        })
        threading.Thread(target=_run_file_queue, args=(list(selected), sweep_junk), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/file-queue/status")
def api_file_queue_status():
    check_auth()
    return jsonify(_file_queue_status)


def _autofile_collect_candidates(shows, retention, watched_state):
    """Phase 1: every watched, "keep forever," queue-root entry whose show
    TMDB has matched at all. Groups TV candidates by (show, season, episode)
    so same-pass duplicates can be resolved in phase 2 - one release can
    exist in the queue at more than one quality/source at once. Returns
    (episode_groups, movie_candidates, flagged) where flagged is a list of
    (path, reason) for anything that fails an outright, per-entry check
    (multi-episode shape, no season/episode, nothing left to file)."""
    episode_groups = {}
    movie_candidates = []
    flagged = []

    for s in shows:
        if s["type"] == "Other":
            continue  # not confidently identified at all - handled manually
        if retention.get(show_key(s)) == "delete_after_watch":
            continue  # only "keep forever" (explicit archive, or unset/default) qualifies
        if not s.get("tmdb_matched"):
            continue  # show itself isn't confidently identified - never guess
        for e in s["entries"]:
            if e["root"] not in AUTOFILE_QUEUE_ROOTS:
                continue
            if not autofile_in_scope(s, e):
                continue  # the companion tool's territory now (TV in AUTOFILE_TV_EXCLUDED_ROOTS) - not flagged, just not the app's business
            watched, _ = resolve_watched(e["path"], watched_state)
            if not watched:
                continue
            video_path = e.get("video_path")
            if not video_path or not os.path.exists(video_path):
                continue  # nothing playable found, or already gone (e.g. a previous run)

            original_names = (os.path.basename(e["path"].rstrip("\\/")), os.path.basename(video_path))
            if s["type"] == "TV":
                if e.get("season") is None or e.get("episode") is None:
                    flagged.append((video_path, "no season/episode could be parsed from the filename"))
                    continue
                if looks_multi_episode(*original_names):
                    flagged.append((video_path, "looks like a multi-episode file - never auto-renamed"))
                    continue
                key = (show_key(s), e["season"], e["episode"])
                episode_groups.setdefault(key, []).append({"entry": e, "show": s})
            elif s["type"] == "Movie":
                movie_candidates.append({"entry": e, "show": s})

    return episode_groups, movie_candidates, flagged


def _autofile_resolve_duplicates(episode_groups, flagged):
    """Phase 2: within each (show, season, episode) group, keep the highest
    quality_score() (resolution, then source, then size) candidate; the
    rest are flagged as superseded - never deleted, left exactly in place."""
    winners = []
    for group in episode_groups.values():
        if len(group) == 1:
            winners.append(group[0])
            continue
        candidates = [
            {"idx": i, "original_name": os.path.basename(g["entry"]["video_path"]), "size_bytes": g["entry"].get("size_bytes", 0)}
            for i, g in enumerate(group)
        ]
        winner_c, loser_cs = pick_best_duplicate(candidates)
        winners.append(group[winner_c["idx"]])
        for lc in loser_cs:
            flagged.append((group[lc["idx"]]["entry"]["video_path"], "superseded by a higher-quality copy of the same episode this run - left in place"))
    return winners


def _autofile_existing_location(show, other_root="Archive"):
    """This show's existing archive folder + category, if any - so a
    partially-archived show doesn't get its category/folder re-guessed
    from scratch. Mirrors pipeline/file_queue.py's own copy of this lookup."""
    archived = [e for e in show["entries"] if e["root"] == other_root]
    if not archived:
        return None, None
    sample_dir = os.path.dirname(archived[0]["path"].rstrip("\\/"))
    category = archived[0].get("category")
    if show["type"] == "TV":
        show_dir = os.path.dirname(sample_dir) if SEASON_DIR_RE.search(sample_dir) else sample_dir
        return show_dir, category
    return sample_dir, category


def _autofile_plan_tv(show, entry, resolution):
    ep_name = validate_tv_episode(
        show["tmdb_id"], entry["season"], entry["episode"], SEASON_CACHE_PATH, DATA_DIR,
        is_current_airing_season=(show.get("tmdb_status") in ONGOING_STATUSES and show.get("tmdb_number_of_seasons") == named_season_to_tmdb(show["tmdb_id"], entry["season"], DATA_DIR)),
    )
    if ep_name is None:
        return None, None, f"TMDB has no S{entry['season']:02d}E{entry['episode']:02d} for this show - never renamed on a guess"

    existing_show_dir, existing_category = _autofile_existing_location(show)
    category = existing_category or "Series"
    if not existing_show_dir:
        cat, _confidence, reason = suggest_category(show)
        if cat is None:
            return None, None, f"no confident category ({reason})"
        category = cat

    show_name = show.get("tmdb_name") or show["title"]
    dest_dir = tv_dest_dir(category, show_name, entry["season"], existing_show_dir)
    ext = os.path.splitext(entry["video_path"])[1]
    dest_name = tv_filename(show_name, entry["season"], entry["episode"], ep_name, resolution, ext)
    return dest_dir, dest_name, None


def _autofile_plan_movie(show, entry, resolution):
    existing_dir, existing_category = _autofile_existing_location(show)
    category = existing_category or "Movies"
    if not existing_dir:
        cat, _confidence, reason = suggest_category(show)
        if cat is None:
            return None, None, f"no confident category ({reason})"
        category = cat

    title = show.get("tmdb_name") or show["title"]
    year = (show.get("years") or [None])[0]
    dest_dir = movie_dest_dir(category, title, year, existing_dir)
    ext = os.path.splitext(entry["video_path"])[1]
    dest_name = movie_filename(title, year, resolution, ext)
    return dest_dir, dest_name, None


def _autofile_move_one(entry, dest_dir, dest_name, log_lines):
    video_path = entry["video_path"]
    dest_path = os.path.join(dest_dir, dest_name)
    if os.path.exists(dest_path):
        return False, "destination already exists - possible collision, left in place"

    sidecars = find_matching_sidecars(video_path)
    os.makedirs(dest_dir, exist_ok=True)
    shutil.move(video_path, dest_path)
    dest_stem = os.path.splitext(dest_name)[0]
    moved_sidecars = 0
    for sc in sidecars:
        sc_dest = os.path.join(dest_dir, dest_stem + os.path.splitext(sc)[1])
        if not os.path.exists(sc_dest):
            shutil.move(sc, sc_dest)
            moved_sidecars += 1
    suffix = f" (+{moved_sidecars} subtitle sidecar file(s))" if moved_sidecars else ""
    log_lines.append(f"FILED: {video_path} -> {dest_path}{suffix}")
    return True, None


def _is_orphaned_leftover(folder):
    """True if `folder` now contains nothing but disposable sidecar files, or
    is fully empty - mirrors generated\\cleanup_orphaned_folders.ps1's own
    (non-recursive, re-verified-at-use-time) check. A subfolder or an
    unrecognized extension means something real might still be in there, so
    it's left alone rather than forced."""
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for name in names:
        full = os.path.join(folder, name)
        if os.path.isdir(full) or os.path.splitext(name)[1].lower() not in DISPOSABLE_SIDECAR_EXTS:
            return False
    return True


def _autofile_relocate_leftover(show, entry, log_lines):
    """After a video (+ sidecars) has just been filed out of a release
    folder, move whatever sidecar-only wreckage is left behind into
    PENDING_DELETION_DIR - same destination and same never-delete,
    review-it-yourself pattern as run_purge() - instead of leaving it behind
    in a scanned queue root to show up as a stray, video-less library entry.
    Never touches anything if the entry was a loose file (no containing
    release folder) or if anything besides disposable sidecars remains -
    that's left exactly where it is, same as always. Failure here (e.g. a
    permissions error) is logged and swallowed, never allowed to undo the
    filing that already succeeded."""
    try:
        folder = entry["path"].rstrip("\\/")
        video_path = entry["video_path"]
        if os.path.normcase(folder) == os.path.normcase(video_path):
            return  # entry WAS the loose video file itself - no folder left behind
        if not os.path.isdir(folder) or not _is_orphaned_leftover(folder):
            return
        dest_dir = os.path.join(PENDING_DELETION_DIR, safe_folder_name(show["title"]))
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, os.path.basename(folder))
        if os.path.exists(dest_path):
            dest_path = os.path.join(dest_dir, f"{os.path.basename(folder)} ({datetime.now().strftime('%Y%m%d_%H%M%S')})")
        shutil.move(folder, dest_path)
        log_lines.append(f"LEFTOVER MOVED: {folder} -> {dest_path}")
    except OSError as ex:
        log_lines.append(f"LEFTOVER MOVE FAILED (left in place): {entry['path']} - {ex}")


def run_autofile():
    """Renames+moves (never deletes) every watched, "keep forever,"
    confidently-TMDB-matched entry still sitting in a queue root into
    Video\\ - full spec in README roadmap item 24 (TMDB is the sole source
    of truth for names/numbers, original filenames are used only to locate
    quality tags and to identify which episode a file represents; multi-
    episode files, unvalidatable season/episode numbers, same-pass
    duplicates, and destination collisions are all flagged rather than
    guessed at - see the phase-1/2 helpers above). Only the video file
    itself (plus exact-stem subtitle sidecars) is renamed and moved out of
    its release folder; after that, if nothing but disposable sidecar junk
    is left in that folder, it's relocated whole into PENDING_DELETION_DIR
    (_autofile_relocate_leftover()) rather than left behind as a stray,
    video-less entry in a scanned queue root. Runs in a background thread -
    see start_autofile(). Writes a durable per-run audit log to logs\\,
    same as run_purge()."""
    log_lines = [f"Auto-file to Library started {datetime.now(timezone.utc).isoformat()}"]
    try:
        shows = load_shows()
        state = load_state()
        retention = state.get("retention", {})
        watched_state = state.get("watched", {})

        episode_groups, movie_candidates, flagged = _autofile_collect_candidates(shows, retention, watched_state)
        tv_winners = _autofile_resolve_duplicates(episode_groups, flagged)
        _autofile_status["total_count"] = len(tv_winners) + len(movie_candidates) + len(flagged)
        # Watched/retention_override/travel_selection state is keyed by
        # e["path"] - for a folder-wrapped release that's the folder, not
        # video_path. Auto-File renames/moves the video out of that folder,
        # so without this, that state is orphaned under a path that no
        # longer exists. resolve_watched() no longer defaults anything to
        # watched=True on a missing record (removed 2026-08-21, see its own
        # docstring) - so without this carry-forward, an orphaned record
        # wouldn't just lose its sort-order timestamp quietly, the checkbox
        # itself would visibly and wrongly revert to unwatched. Even more
        # reason this mapping has to be right, not less. Uses the same
        # migrate() plans/migrate_state_paths.py already provides for reorg
        # renames (all three state dicts, not just watched - this used to
        # be a narrower hand-rolled watched-only version, real gap closed
        # 2026-08-21) since Auto-File knows exactly what it renamed, no
        # size/mtime guessing needed the way scan_media.py's generic
        # fallback has to for renames from unknown sources.
        watched_path_migrations = []

        for item in tv_winners:
            e, s = item["entry"], item["show"]
            resolution = extract_resolution_tag(os.path.basename(e["path"].rstrip("\\/")), os.path.basename(e["video_path"]))
            dest_dir, dest_name, reason = _autofile_plan_tv(s, e, resolution)
            if dest_dir is None:
                flagged.append((e["video_path"], reason))
                continue
            ok, reason = _autofile_move_one(e, dest_dir, dest_name, log_lines)
            if not ok:
                flagged.append((e["video_path"], reason))
                continue
            watched_path_migrations.append((e["path"], os.path.join(dest_dir, dest_name)))
            _autofile_relocate_leftover(s, e, log_lines)
            _autofile_status["filed_count"] += 1
            _autofile_status["filed_bytes"] += e.get("size_bytes", 0)

        for item in movie_candidates:
            e, s = item["entry"], item["show"]
            resolution = extract_resolution_tag(os.path.basename(e["path"].rstrip("\\/")), os.path.basename(e["video_path"]))
            dest_dir, dest_name, reason = _autofile_plan_movie(s, e, resolution)
            if dest_dir is None:
                flagged.append((e["video_path"], reason))
                continue
            ok, reason = _autofile_move_one(e, dest_dir, dest_name, log_lines)
            if not ok:
                flagged.append((e["video_path"], reason))
                continue
            watched_path_migrations.append((e["path"], os.path.join(dest_dir, dest_name)))
            _autofile_relocate_leftover(s, e, log_lines)
            _autofile_status["filed_count"] += 1
            _autofile_status["filed_bytes"] += e.get("size_bytes", 0)

        if watched_path_migrations:
            from migrate_state_paths import migrate as migrate_state_paths  # lazy - see the module-level comment near the other imports
            migrate_state_paths(watched_path_migrations)
            log_lines.append(f"Carried watched/retention/travel state across rename for {len(watched_path_migrations)} file(s).")

        for path, reason in flagged:
            _autofile_status["skipped_count"] += 1
            _autofile_status["flagged"].append({"path": path, "reason": reason})
            log_lines.append(f"FLAGGED ({reason}): {path}")

        log_lines.append(f"Done: {_autofile_status['filed_count']} filed, {_autofile_status['skipped_count']} flagged/skipped.")
    except Exception as ex:
        _autofile_status["error"] = str(ex)
        log_lines.append(f"ERROR: {ex}")
    finally:
        _autofile_status["running"] = False
        _autofile_status["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, "autofile_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines))
            prune_logs(LOG_DIR, "autofile")
        except OSError:
            pass
        if _autofile_status["filed_count"] > 0:
            start_refresh(STARTUP_SCRIPTS)


@app.route("/api/top10", methods=["POST"])
def api_top10():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("show_key")
    person = data.get("person")
    action = data.get("action")
    if person not in TOP10_PEOPLE or action not in ("add", "remove") or not isinstance(key, str):
        abort(400)

    shows = load_shows()
    known_keys = {show_key(s) for s in shows}
    if key not in known_keys:
        abort(400)

    result = {"ok": True}

    def mutator(state):
        current = state["top10"][person]
        if action == "remove":
            state["top10"][person] = [k for k in current if k != key]
            return
        if key in current:
            return  # already in the list, no-op
        state["top10"][person] = current + [key]

    update_state(mutator)
    return jsonify(result), (200 if result["ok"] else 409)


@app.route("/api/clear-recently-watched", methods=["POST"])
def api_clear_recently_watched():
    check_auth()

    def mutator(state):
        state["recently_watched"] = []

    update_state(mutator)
    return jsonify({"ok": True})


@app.route("/api/no-interest", methods=["POST"])
def api_no_interest():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("show_key")
    person = data.get("person")
    action = data.get("action")
    if person not in TOP10_PEOPLE or action not in ("add", "remove") or not isinstance(key, str):
        abort(400)

    shows = load_shows()
    known_keys = {show_key(s) for s in shows}
    if key not in known_keys:
        abort(400)

    result = {"ok": True}

    def mutator(state):
        current = state["no_interest"][person]
        if action == "remove":
            state["no_interest"][person] = [k for k in current if k != key]
            return
        if key in current:
            return  # already in the list, no-op
        state["no_interest"][person] = current + [key]

    update_state(mutator)
    return jsonify(result), (200 if result["ok"] else 409)


@app.route("/api/export-missing-digest", methods=["POST"])
def api_export_missing_digest():
    """Writes the missing-episodes digest text (built client-side from data
    already in the page - shows[].missing_episodes/missing_seasons, no
    server-side regeneration needed) straight to the user's Desktop, same
    fixed-filename-always-overwritten convention as build_travel_plan.py's
    Desktop copy of Travel Export.ps1."""
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        abort(400)
    if len(content) > 2_000_000:
        return jsonify({"ok": False, "message": "That digest is too large to export."}), 400

    dest_path = os.path.join(os.path.expanduser("~"), "Desktop", "Missing Episodes.txt")
    try:
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as ex:
        return jsonify({"ok": False, "message": f"Couldn't write to Desktop: {ex}"}), 500
    return jsonify({"ok": True})


@app.route("/api/export-duplicates", methods=["POST"])
def api_export_duplicates():
    """Same fixed-filename-to-Desktop convention as export-missing-digest
    above, but built server-side (not client-side) since show['duplicates']
    - kept_path/extra_paths/wasted_bytes per episode, see
    pipeline/detect_runtime_mismatch.py-adjacent dedup logic - is real
    filesystem detail never sent to the browser in the first place, unlike
    the missing-episodes digest which is built from data already in the
    page. 2026-08-23, at the user's request after asking "where" the
    Dashboard's Wasted Space number actually pointed to."""
    check_auth()
    shows = load_shows()
    lines = []
    total_bytes = 0
    for s in shows:
        dups = s.get("duplicates") or []
        if not dups:
            continue
        lines.append(f"{s['title']} ({s.get('type', '?')})")
        for d in dups:
            ep_label = f"S{d['season']:02d}E{d['episode']:02d}" if d.get("season") is not None else ""
            wasted = fmt_size(d.get("wasted_bytes", 0))
            lines.append(f"  {ep_label}".rstrip() if ep_label else "  ")
            lines.append(f"    Keeping:  {d.get('keep_path', '?')}")
            for extra in d.get("extra_paths", []):
                lines.append(f"    Wasted:   {extra}  ({wasted})")
            total_bytes += d.get("wasted_bytes", 0)
        lines.append("")
    if not lines:
        return jsonify({"ok": False, "message": "No duplicates to export right now."})

    header = [
        f"Duplicate files - {fmt_size(total_bytes)} total wasted space",
        f"Generated {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
        "Nothing has been touched - this is a list only. Use the app's own",
        "Purge/Pending Deletion workflow, or delete these yourself, once",
        "you've reviewed it.",
        "",
    ]
    dest_path = os.path.join(os.path.expanduser("~"), "Desktop", "Duplicate Files.txt")
    try:
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write("\n".join(header + lines))
    except OSError as ex:
        return jsonify({"ok": False, "message": f"Couldn't write to Desktop: {ex}"}), 500
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    check_auth()
    return jsonify({"ok": True, "settings": load_settings()})


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    """Admin -> Fix a title's missing-episode grace-period selector
    (2026-09-13). Only this one setting exists so far - deliberately a
    small key/value dict rather than one field apiece, so a future second
    setting doesn't need its own new route."""
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    if "missing_episode_grace_days" in data:
        try:
            days = int(data["missing_episode_grace_days"])
        except (TypeError, ValueError):
            abort(400)
        if days not in MISSING_EPISODE_GRACE_CHOICES:
            abort(400)
        settings = load_settings()
        settings["missing_episode_grace_days"] = days
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        atomic_replace(tmp, SETTINGS_PATH)
    return jsonify({"ok": True, "settings": load_settings()})


def _is_clearable_image_file(dirpath, filename):
    """Real TMDB downloads only - excludes the two things that live in this
    same tree but AREN'T a re-fetchable cache: user-uploaded custom covers
    (save_custom_image() writes 'custom<hex>.jpg' into these same poster/
    backdrop folders - see CUSTOM_TOKEN_PREFIX) and the manual/ subfolder
    (the 2 non-TMDB disc covers backfill_tmdb_images.py downloads by hand).
    Neither has a TMDB URL to re-fetch from, so wiping them the way a real
    cache entry gets wiped would just permanently delete the only copy
    (caught 2026-09-13, before this route had ever actually been used)."""
    if os.path.basename(dirpath) == "manual":
        return False
    return not filename.startswith(CUSTOM_TOKEN_PREFIX)


@app.route("/api/image-cache/status", methods=["GET"])
def api_image_cache_status():
    """Real on-disk size, not an estimate - same walk backfill_tmdb_images.py
    prints at the end of a manual run, surfaced here so Admin can see what a
    "clear" is actually about to delete before confirming (2026-09-13,
    "every poster should be cached... until I say delete in the Admin").
    Reports only what Clear would actually remove - see
    _is_clearable_image_file() for what's deliberately excluded."""
    check_auth()
    file_count = 0
    total_bytes = 0
    if os.path.isdir(TMDB_IMAGES_ROOT):
        for root, _dirs, files in os.walk(TMDB_IMAGES_ROOT):
            for fn in files:
                if not _is_clearable_image_file(root, fn):
                    continue
                try:
                    total_bytes += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    continue
                file_count += 1
    return jsonify({"ok": True, "file_count": file_count, "total_bytes": total_bytes})


@app.route("/api/image-cache/clear", methods=["POST"])
def api_image_cache_clear():
    """Deletes every locally cached TMDB image (posters/backdrops/cast) so
    they're re-downloaded fresh on next view - this is the ONLY place
    anything ever deletes from data/tmdb_images/. Nothing in the pipeline or
    the /tmdb-image/ self-heal route removes a cached file on its own; a
    poster stays cached forever until this explicit Admin action runs
    (2026-09-13, "every poster should be cached. Until I say delete in the
    Admin."). Deliberately requires no library refresh afterward - the next
    page load's /tmdb-image/ requests just re-populate the cache lazily,
    same self-heal that's always handled a cold/empty cache.

    Skips anything _is_clearable_image_file() excludes (custom uploads,
    manual/ covers) - those aren't re-fetchable from TMDB, so "clear" must
    never touch them."""
    check_auth()
    if not os.path.isdir(TMDB_IMAGES_ROOT):
        return jsonify({"ok": True, "cleared": 0})
    cleared = 0
    for root, _dirs, files in os.walk(TMDB_IMAGES_ROOT):
        for fn in files:
            if not _is_clearable_image_file(root, fn):
                continue
            try:
                os.remove(os.path.join(root, fn))
                cleared += 1
            except OSError:
                continue
    return jsonify({"ok": True, "cleared": cleared})


# Netflix/Prime "Play" links open here instead of a plain <a target="_blank">
# because this app itself normally runs inside its own dedicated, isolated
# Chrome profile (automation\MediaLibraryLauncher.cs's --app --user-data-dir
# window - deliberately separate so its own localStorage/session never mixes
# with the user's real browsing). A plain link click just opens a new window
# in that SAME isolated profile - no logins, no extensions, not the browser
# the user actually uses day to day. os.startfile() instead asks Windows
# itself to open the URL, which goes through the user's real default browser
# and real default profile, exactly like double-clicking a link anywhere
# else on the desktop would. Only possible at all because the server and the
# browser hitting it are always the same machine (see /api/browse-folder's
# own comment, same reasoning). Domain-allowlisted (not just "any URL the
# client sends") since this shells out on the server - only the two
# providers streaming links are ever actually built from
# (pipeline\enrich_streaming.py's _search_link / a real deep_link).
#
# Window size is decided by host, so every link in the app behaves the same
# way no matter which part of the UI (or which sibling app) opened it:
#   * somewhere you WATCH (Netflix, Amazon, YouTube) -> maximized
#   * a lookup you read beside the app (TMDB, IMDb, Rotten Tomatoes, search
#     engines, Wikipedia, epguides) -> the left half of the screen
_EXTERNAL_FULLSCREEN_HOSTS = tuple(dict.fromkeys(("netflix.com", app_config.AMAZON_DOMAIN, "amazon.com", "youtube.com", "youtu.be")))
_EXTERNAL_HALF_LEFT_HOSTS = (
    "themoviedb.org", "imdb.com", "rottentomatoes.com", "epguides.com", "wikipedia.org",
    "google.com", "bing.com", "duckduckgo.com", "justwatch.com",
)
_EXTERNAL_LINK_ALLOWED_HOSTS = _EXTERNAL_FULLSCREEN_HOSTS + _EXTERNAL_HALF_LEFT_HOSTS


def _host_in(host, hosts):
    return any(host == h or host.endswith("." + h) for h in hosts)


def _window_mode_for_host(host):
    return "maximize" if _host_in(host, _EXTERNAL_FULLSCREEN_HOSTS) else "half-left"


def _focus_window(hwnd, maximize=False):
    """Forces hwnd to the foreground. Windows deliberately blocks a
    background process from stealing focus (the "foreground lock" anti-
    annoyance feature) - AttachThreadInput to the CURRENT foreground
    window's input queue before calling SetForegroundWindow is the
    standard, well-known way around that for a legitimate same-machine
    action like this one.

    maximize=True calls ShowWindow(SW_MAXIMIZE) directly instead of
    relying on the --start-maximized launch flag actually taking effect -
    confirmed real (2026-09-21) that it doesn't when Chrome's singleton
    IPC folds the launch into an already-running process (the same fold-in
    quirk _launch_chrome_app_window's own docstring already covers for
    --app itself): the flag opened a normal small window instead. This
    Win32 call acts directly on the window we already found, so it works
    regardless of whether Chrome honored the launch flag."""
    import ctypes
    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(target_thread, fg_thread, True)
    try:
        user32.ShowWindow(hwnd, 3 if maximize else 9)  # SW_MAXIMIZE : SW_RESTORE (undoes a minimize)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        user32.AttachThreadInput(target_thread, fg_thread, False)


def _left_half_rect(ref_hwnd):
    """(x, y, w, h) of the left half of the work area (screen minus taskbar)
    of the monitor `ref_hwnd` is on - the app window the click came from,
    so on a multi-monitor desk the popup lands beside the app, not on
    whichever screen happens to be primary."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    mon = user32.MonitorFromWindow(ref_hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(info))
    work = info.rcWork
    work_w = work.right - work.left
    # Half the screen, but never narrower than ~800 css px: on a small laptop
    # panel (1366 wide = a 683px half) a page like TMDB's gets too cramped to
    # read. In real pixels, so scale by the window's DPI (this thread is
    # per-monitor aware - see _launch_chrome_app_window).
    try:
        scale = (user32.GetDpiForWindow(ref_hwnd) or 96) / 96.0
    except (AttributeError, OSError):
        scale = 1.0
    width = min(work_w, max(work_w // 2, int(800 * scale)))
    return work.left, work.top, width, work.bottom - work.top


def _place_window_left_half(hwnd, rect):
    """Un-maximizes hwnd if needed and fits its VISIBLE frame to `rect`.
    Windows 10 top-level windows carry an invisible ~7px resize border, so
    a window positioned at x=0 leaves a visible gap and overlaps its
    neighbour; DWM's extended-frame bounds tell us the real visible edge
    and the inset is compensated for."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    x, y, w, h = rect
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE - a maximized window ignores SetWindowPos sizing
    win = wintypes.RECT()
    ext = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(win))
    il = it = ir = ib = 0
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(ext), ctypes.sizeof(ext)) == 0:  # DWMWA_EXTENDED_FRAME_BOUNDS
        il, it = ext.left - win.left, ext.top - win.top
        ir, ib = win.right - ext.right, win.bottom - ext.bottom
    # SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
    user32.SetWindowPos(hwnd, 0, x - il, y - it, w + il + ir, h + it + ib, 0x0004 | 0x0010 | 0x0040)


def _visible_chrome_windows():
    """Every visible top-level window of Chromium's window class
    (Chrome_WidgetWin_1 - shared by Chrome, Edge, and any other Chromium-
    based app, so this alone can't tell them apart; see
    _launch_chrome_app_window()'s before/after diff, which is what
    actually disambiguates). Pure ctypes - no pywin32 dependency to add
    just for this."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    hwnds = []

    def _cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if cls_buf.value == "Chrome_WidgetWin_1":
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return set(hwnds)


# Same hardcoded-path + fallback pattern automation/MediaLibraryLauncher.cs
# already uses for the app's own window - a real install location, not a
# guess, with a graceful "just open something" fallback if Chrome's been
# moved/uninstalled.
_CHROME_PATH_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
)


def _find_chrome_exe():
    configured = app_config.browser_path()
    if configured:
        return configured
    for path in _CHROME_PATH_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return None


# Tracks each popup's lifecycle so the page can poll /api/streaming/
# popup-status and know when to jump back to the episode/show it came from
# (2026-09-21: "go straight to the episode if possible, if not the show
# card"). launch_id -> {"closed": bool, "started_at": float}. In-memory
# only - losing this on a server restart just means an in-flight poll gets
# "closed" back immediately (see the route below), never an error.
_popup_launches = {}
_popup_launches_lock = threading.Lock()
_POPUP_LAUNCH_MAX_AGE_SECONDS = 2 * 3600  # a popup left open for 2h+ isn't worth still tracking


def _prune_popup_launches_locked():
    """Caller must already hold _popup_launches_lock. Cheap - only runs
    once per new launch, same pattern as this file's other small in-memory
    caches with no eviction schedule of their own."""
    cutoff = time.time() - _POPUP_LAUNCH_MAX_AGE_SECONDS
    for launch_id in [k for k, v in _popup_launches.items() if v["started_at"] < cutoff]:
        del _popup_launches[launch_id]


def _mark_popup_closed(launch_id):
    if not launch_id:
        return
    with _popup_launches_lock:
        if launch_id in _popup_launches:
            _popup_launches[launch_id]["closed"] = True


def _launch_chrome_app_window(url, launch_id=None, mode="maximize"):
    """Runs the real launch with THIS THREAD made per-monitor-DPI-aware.
    python.exe is DPI-unaware, so on a scaled display (this machine: 4K at
    200%) its GetMonitorInfo/GetWindowRect report virtualized coordinates -
    a 1920x1040 work area for a 3840x2080 screen - while Chrome (DPI-aware)
    positions in real pixels; a half-screen computed from the virtualized
    numbers landed at (31,6)-(1035,1843) instead of the left half (caught
    2026-10-10 by measuring a test window). Thread-scoped, restored after,
    and never touches the rest of the server."""
    if not app_config.IS_WINDOWS:
        # Window placement is Win32-only; elsewhere hand the link to the
        # default browser and tell the page the "popup" is already done.
        import webbrowser
        webbrowser.open(url)
        _mark_popup_closed(launch_id)
        return
    import ctypes
    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    prev = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    try:
        _launch_chrome_app_window_inner(url, launch_id, mode)
    finally:
        if prev:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))


def _launch_chrome_app_window_inner(url, launch_id=None, mode="maximize"):
    """`mode` is "maximize" (Netflix/Amazon/YouTube - see
    _EXTERNAL_FULLSCREEN_HOSTS) or "half-left" (TMDB, search engines, ...).
    Everything below describes the maximize case; half-left runs the same
    launch / find-the-new-window / reassert-for-a-couple-seconds sequence
    but fits the window to the left half of the work area instead.

    Opens url in a chromeless Chrome "app mode" window using the user's
    REAL default Chrome profile (deliberately no --user-data-dir override)
    - already logged into Netflix/Amazon, with their real extensions and
    language prefs, but with no tabs/bookmarks/address bar, so it reads as
    part of the app rather than a separate browser window popping up
    (2026-09-20: "a popup that is my active browser... but masquerades as
    the app"). The exact opposite of the app's own window, which
    deliberately uses an ISOLATED profile (MediaLibraryLauncher.cs's
    ChromeAppProfile) so its own localStorage never mixes with real
    browsing - here the user explicitly wants the real profile.

    --start-maximized (2026-09-21, replacing an earlier 75%-of-screen-
    centered size): fills almost the whole screen but keeps a real native
    title bar with an always-visible close (X) button - the user explicitly
    wants a genuine close control, not Chrome's own transient "press Esc"
    fullscreen banner, which --start-fullscreen would have given instead.

    Chrome's own already-running-instance behavior means a new --app
    request can get folded into whatever Chrome process is already running
    instead of a distinct new process (the exact quirk
    MediaLibraryLauncher.cs's own comment documents, worked around there
    with an isolated profile - not an option here, since sharing the real
    profile is the whole point). A before/after snapshot of every visible
    Chrome-class window sidesteps it either way: cold launch or folded into
    an existing process, whatever window wasn't there a moment ago is the
    one just opened, and the site's own native fullscreen control
    (Netflix/Amazon/YouTube's own button) works normally from there - app-
    mode windows fully support the Fullscreen API, nothing extra needed.

    When launch_id is given, keeps running after focusing the new window -
    polling until that exact hwnd stops existing - and marks it closed in
    _popup_launches so the page's poll loop knows to navigate back. Not the
    whole Chrome process's lifetime (see the fold-in note above: that
    process may own the user's other, unrelated Chrome windows too) -
    specifically this one window."""
    chrome = _find_chrome_exe()
    if not chrome or not app_config.IS_WINDOWS:
        # Window placement is Win32-only; elsewhere (or with no Chrome) just open the page.
        import webbrowser
        webbrowser.open(url)  # still open something rather than silently fail
        _mark_popup_closed(launch_id)
        return
    import ctypes
    # Captured BEFORE launching: right after, Chrome owns the foreground, and
    # the half-left rect should follow the monitor the app itself is on.
    ref_hwnd = ctypes.windll.user32.GetForegroundWindow()
    before = _visible_chrome_windows()
    if mode == "half-left":
        # No --window-size/--window-position: Chrome reads those in device-
        # independent units, not the real pixels computed here, and the
        # Win32 placement below is what actually decides the geometry.
        args = [chrome, f"--app={url}"]
    else:
        args = [chrome, f"--app={url}", "--start-maximized"]
    subprocess.Popen(args, close_fds=True)
    hwnd = None
    for _ in range(25):
        time.sleep(0.2)
        new = _visible_chrome_windows() - before
        if new:
            hwnd = new.pop()
            break
    if hwnd is None:
        _mark_popup_closed(launch_id)  # never found it - don't leave the page polling forever
        return
    # One maximize call right after the window is first found loses a race
    # against Chrome's own post-launch layout pass (confirmed real,
    # 2026-09-21: it silently reverted to a small remembered size a moment
    # after being maximized). Reasserting for a couple seconds outlasts
    # whatever Chrome is still doing to the window on its own.
    for i in range(8):
        if mode == "half-left":
            _place_window_left_half(hwnd, _left_half_rect(ref_hwnd))
            if i == 0:
                _focus_window(hwnd)  # raise it once; re-raising every pass would fight the user
        else:
            _focus_window(hwnd, maximize=True)
        time.sleep(0.25)
    if launch_id:
        user32 = ctypes.windll.user32
        while user32.IsWindow(hwnd):
            time.sleep(1)
        _mark_popup_closed(launch_id)


@app.route("/api/open-external-link", methods=["POST"])
def api_open_external_link():
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")
    if not url or not isinstance(url, str):
        abort(400)
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or not _host_in(host, _EXTERNAL_LINK_ALLOWED_HOSTS):
        abort(400)
    mode = _window_mode_for_host(host)
    # Only a window you WATCH something in needs tracking (the page jumps
    # back to the show once it closes); a lookup popup is fire-and-forget.
    launch_id = uuid.uuid4().hex if mode == "maximize" else None
    if launch_id:
        with _popup_launches_lock:
            _popup_launches[launch_id] = {"closed": False, "started_at": time.time()}
            _prune_popup_launches_locked()
    # Off the request thread - launch + the focus-snapshot retry loop can
    # take up to ~5s on a cold Chrome launch, no reason to make the click
    # wait on it.
    threading.Thread(target=_launch_chrome_app_window, args=(url,),
                     kwargs={"launch_id": launch_id, "mode": mode}, daemon=True).start()
    return jsonify({"ok": True, "launch_id": launch_id, "mode": mode})


@app.route("/api/streaming/popup-status", methods=["GET"])
def api_streaming_popup_status():
    """Polled by the page after openStreamingLink() while the Chrome
    app-mode popup is open, so it knows when to jump back to the episode/
    show it came from (2026-09-21). An unknown/expired launch_id reports
    closed:true rather than 404ing - the page should stop waiting on it
    either way, not poll forever."""
    check_auth()
    launch_id = request.args.get("launch_id", "")
    with _popup_launches_lock:
        info = _popup_launches.get(launch_id)
    return jsonify({"ok": True, "closed": True if info is None else info["closed"]})


# Netflix's per-title page (/title/<id>, what a "Copy link" or the
# ?jbv=<id> modal-deep-link both resolve to - both carry the SAME numeric
# id) is real, public SEO content: full <title>/og:title, no login
# required. Confirmed live 2026-09-20 - this is a genuinely different URL
# shape than netflix.com/search, which requires a logged-in session -
# that finding doesn't apply here. Amazon's own
# /dp/<ASIN> and /gp/video/detail/<ASIN> product pages are ordinary public
# retail pages, same deal. Used by /api/streaming/resolve-link below to
# turn "I pasted the real page" into an accurate title, instead of the
# user having to guess what TMDB's search wants.
_NETFLIX_TITLE_RE = re.compile(r"netflix\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:title/(\d+)|browse\?jbv=(\d+))", re.I)
_PRIME_TITLE_RE = re.compile(r"amazon\.[a-z.]+/(?:gp/video/detail/|dp/)([A-Z0-9]{10})", re.I)
_HTML_TITLE_RE = re.compile(r"<title[^>]*>([^<]*)</title>", re.I)
# Netflix/Amazon's own SEO-facing pages serve meaningfully different (or no)
# content to an obvious non-browser client - a real desktop-Chrome UA string
# is what the live verification this session confirmed against, not a
# stand-in identifying this as a script the way tmdb_image_cache.py's own
# USER_AGENT deliberately does for TMDB's API.
_STREAMING_FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _clean_netflix_title(raw):
    t = re.sub(r"^\s*Watch\s+", "", raw)
    t = re.sub(r"\s*\|\s*Netflix.*$", "", t, flags=re.I)
    return t.strip()


def _clean_prime_title(raw):
    t = re.sub(r"^\s*Amazon\.\w+:\s*", "", raw, flags=re.I)
    t = re.sub(r"\s*\|\s*Prime Video.*$", "", t, flags=re.I)
    t = re.sub(r"\s*-\s*(Staffel|Season)\s*\d+.*$", "", t, flags=re.I)  # localized season suffix (German/English)
    t = re.sub(r"\s+(ansehen|watch)\s*$", "", t, flags=re.I)  # trailing verb on a movie page
    return t.strip()


def _resolve_streaming_link(url):
    """(provider, canonical_deep_link, clean_title) for a real Netflix/
    Prime title page URL, or (None, None, None) if it isn't one or the
    fetch/parse failed - never raises, same "best-effort, fail quietly"
    posture as every other optional cross-app/external lookup in this
    file. Host-allowlisted via the same _EXTERNAL_LINK_ALLOWED_HOSTS this
    file already trusts for /api/open-external-link, not a fresh check -
    one allowlist, two uses."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or not any(
        host == h or host.endswith("." + h) for h in _EXTERNAL_LINK_ALLOWED_HOSTS
    ):
        return None, None, None

    m = _NETFLIX_TITLE_RE.search(url)
    if m:
        title_id = m.group(1) or m.group(2)
        provider, fetch_url, cleaner = "netflix", f"https://www.netflix.com/title/{title_id}", _clean_netflix_title
    else:
        m = _PRIME_TITLE_RE.search(url)
        if not m:
            return None, None, None
        asin = m.group(1)
        provider, fetch_url, cleaner = "prime", f"https://www.{app_config.AMAZON_DOMAIN}/dp/{asin}", _clean_prime_title

    try:
        req = urllib.request.Request(fetch_url, headers={"User-Agent": _STREAMING_FETCH_USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            page_html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return provider, fetch_url, None

    m = _HTML_TITLE_RE.search(page_html)
    if not m:
        return provider, fetch_url, None
    title = cleaner(html.unescape(m.group(1)))
    return provider, fetch_url, (title or None)


@app.route("/api/streaming/resolve-link", methods=["POST"])
def api_streaming_resolve_link():
    """Admin -> Add a title -> Netflix/Prime: paste the real page link
    instead of guessing what to type into TMDB search. Resolves to the
    real on-site title (see _resolve_streaming_link's own comment), which
    the client then runs through the SAME /api/tmdb-search + confirm-pick
    flow every other "Add a title" path already uses - this only replaces
    "guess a search query" with "start from a confirmed-accurate one," it
    doesn't skip picking/confirming the real TMDB match."""
    check_auth()
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")
    if not url or not isinstance(url, str):
        abort(400)
    provider, deep_link, title = _resolve_streaming_link(url)
    if provider is None:
        return jsonify({"ok": False, "message": "That doesn't look like a Netflix or Prime Video title link - paste the real show/movie page, not a search or the homepage."})
    if not title:
        return jsonify({"ok": False, "message": "Found the page but couldn't read a title off it - paste the title into the search box by hand instead.", "deep_link": deep_link, "provider": provider})
    return jsonify({"ok": True, "provider": provider, "title": title, "deep_link": deep_link})


def start_refresh(scripts=None):
    """Primes _refresh_status and kicks off run_refresh_pipeline in a
    background thread. Returns False if a refresh (or a purge/autofile,
    either of which could race with it moving/reading the same files) is
    already running."""
    with _refresh_lock:
        if _any_job_running():
            return False
        if app_config.DEMO:
            # The sample library is a fixed set of files: nothing to scan,
            # nothing to look up. Report an instantly finished refresh.
            _refresh_status.update({
                "running": False, "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(), "error": None,
                "log": ["Demo library: nothing to refresh."], "total": 0, "steps": [],
                "current": None, "current_started_ts": 0,
            })
            _write_refresh_status_file()
            return True
        _refresh_status.update({
            "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "error": None, "log": [],
            # Real step count for whichever list is actually running (the
            # startup quick-scan and a full "Refresh library" are different
            # lengths) - read by the boot overlay / refresh-button title for
            # an honest "X of Y steps" instead of a hardcoded guess.
            "total": len(scripts) if scripts is not None else len(PIPELINE_SCRIPTS),
            "steps": _refresh_step_plan(scripts if scripts is not None else PIPELINE_SCRIPTS),
            "current": None, "current_started_ts": 0,
        })
        _write_refresh_status_file()
        threading.Thread(target=run_refresh_pipeline, kwargs={"scripts": scripts}, daemon=True).start()
    return True


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    check_auth()
    if not start_refresh():
        return jsonify({"ok": False, "message": "A refresh is already running."}), 409
    return jsonify({"ok": True})


@app.route("/api/refresh/status")
def api_refresh_status():
    check_auth()
    return jsonify(_refresh_status_view())


@app.route("/api/server-log")
def api_server_log():
    # Same "point-in-time diagnosis you can actually get to without me"
    # motivation as ensure_port_ready()'s own log redirection below - a
    # server-process crash used to leave zero trace anywhere; this is the
    # other half of closing that gap, an in-app way to actually read it
    # instead of needing someone to open logs\server.log by hand. Read-only,
    # but still behind check_auth() like every other status endpoint here
    # (/api/refresh/status), not because this is sensitive on a localhost-
    # only tool, just for consistency.
    check_auth()
    log_path = os.path.join(LOG_DIR, "server.log")
    if not os.path.exists(log_path):
        return jsonify({"ok": True, "text": "", "message": "No server.log yet - nothing's been written since this feature was added."})
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            # Last ~50KB is plenty for "what just happened" without
            # streaming a potentially large, ever-growing file to the
            # browser on every open.
            seek_to = max(0, size - 50_000)
            f.seek(seek_to)
            if seek_to > 0:
                f.readline()  # drop a possibly-truncated partial first line
            text = f.read()
    except OSError as ex:
        return jsonify({"ok": False, "message": str(ex)}), 500
    return jsonify({"ok": True, "text": text, "truncated": seek_to > 0})


REFRESH_STATUS_FILE_PATH = os.path.join(DATA_DIR, "refresh_status.json")


def _write_refresh_status_file():
    """Small, cheap, no-auth-needed mirror of _refresh_status for the other
    tools that read this app's data files directly rather than over HTTP
    (a companion tool) - so they have a way to check "is a refresh in
    progress right now" before trusting media_db.json, instead of guessing
    from its shape.

    Real problem this answers (2026-09-11): scan_media.py runs FIRST in
    every refresh - quick-scan or full - and writes media_db.json fresh
    from a pure filesystem walk, with zero tmdb_matched/tmdb_id/etc. fields;
    enrich_tmdb.py (the very next step, seconds later) is what re-populates
    them. That means media_db.json genuinely has 0 tmdb_matched entries for
    a few seconds on EVERY refresh, quick-scan included - not rare, not
    corruption, just an expected mid-pipeline shape. An out-of-band reader
    that happens to land in that window with no way to know it's mid-write
    reads it as the library having collapsed. Two separate companion tools
    independently caught this exact window.

    Excludes the log's stdout/stderr (large, irrelevant to "is it safe to
    read yet") - just the shape a freshness check actually needs."""
    try:
        tmp = REFRESH_STATUS_FILE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "running": _refresh_status["running"],
                "started_at": _refresh_status["started_at"],
                "finished_at": _refresh_status["finished_at"],
                "error": _refresh_status["error"],
                "steps_done": len(_refresh_status["log"]),
                "steps_total": _refresh_status.get("total"),
            }, f)
        atomic_replace(tmp, REFRESH_STATUS_FILE_PATH)
    except OSError:
        pass  # best-effort - never let this block the real refresh


def run_refresh_pipeline(scripts=None):
    scripts = scripts if scripts is not None else PIPELINE_SCRIPTS
    _write_refresh_status_file()
    try:
        for script in scripts:
            script_path = os.path.join(PROJECT_ROOT, script)
            _refresh_status["current"] = script
            _refresh_status["current_started_ts"] = time.time()
            _step_t0 = time.time()
            proc = subprocess.run(
                [sys.executable, script_path],
                # Bumped from 1800s (2026-08-19): the actor photo links/IMDB
                # feature's one-time backfill (every already-matched show's
                # cast needs one re-fetch for the new per-actor TMDB person
                # id, plus one new API call per unique actor for their IMDB
                # id) genuinely timed out a 604-show library at 30 minutes,
                # confirmed live - not a hang, just real one-time work. Cast
                # ids are cached forever once fetched, so this is a one-time
                # cost per show (and a much smaller one if the library grows
                # by a normal amount later), not a permanently slower
                # refresh - the higher ceiling is a safety margin for that
                # same shape of one-time spike recurring (a large batch of
                # new titles added at once), not a sign 30 minutes was
                # generally too tight for routine refreshes.
                cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=3600,
            )
            _refresh_status["log"].append({
                "script": script, "returncode": proc.returncode,
                "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:],
            })
            if proc.returncode == 0:
                _record_step_timing(script, time.time() - _step_t0)
            _write_refresh_status_file()
            if proc.returncode != 0:
                _refresh_status["error"] = f"{script} exited with code {proc.returncode}"
                break

        if not _refresh_status["error"]:
            shows = load_shows()
            known_paths = {e["path"] for s in shows for e in s["entries"]}
            known_keys = {show_key(s) for s in shows}
            prune_state(known_paths, known_keys)
    except Exception as e:
        _refresh_status["error"] = str(e)
    finally:
        _refresh_status["running"] = False
        _refresh_status["current"] = None
        _refresh_status["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_refresh_status_file()


def _find_pid_listening_on_port(port):
    """PID bound to `port` in LISTENING state, via `netstat -ano` (no
    extra dependency - psutil isn't installed anywhere in this project).
    None if nothing is."""
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    needle = f":{port}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            local_addr = parts[1]
            if local_addr.endswith(needle) and parts[4].isdigit():
                return int(parts[4])
    return None


def _process_name(pid):
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        return line.split('","')[0].strip('"') if line else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def ensure_port_ready(port, max_wait_s=10):
    """Self-healing startup step, so a stuck prior instance never needs a
    human to notice and kill it by hand before this one can bind.

    Real bug this closes, not a hypothetical: app.run() below has zero
    error handling around it - if the port's already taken, it throws an
    unhandled OSError and this process dies within ~1s of starting, with
    NO trace anywhere (confirmed 2026-08-21: no Windows crash dump, no
    Application Error event - a plain Python exception isn't a system-
    level fault). Meanwhile automation\\MediaLibraryLauncher.cs's/
    media_launcher.ps1's own IsServerRunning()/Test-ServerRunning() checks
    only run ONCE, before spawning - neither ever notices or cleans up a
    stuck PREVIOUS instance still squatting the port, so every subsequent
    launch attempt just silently dies the same way, forever, until
    someone manually kills python.exe first. That manual kill-then-
    relaunch is the entire reason "restart it by hand" always worked here
    while the shortcut never did - this makes that cleanup automatic and
    built into the one entrypoint both launch paths funnel through,
    instead of a step only a human doing it manually remembered.

    Never kills anything it can't positively identify as safe to kill:
    confirms via a real HTTP request that whatever's on the port ISN'T
    actually a healthy running server first (a slow-but-fine server
    caught mid-request by the launcher's own brief connect-timeout check
    is a real possible false alarm, not just stale/dead), and only ever
    terminates a process literally named python.exe - anything else
    sitting on this port is left alone and reported, never guessed at."""
    if not app_config.IS_WINDOWS:
        return  # the stale-instance cleanup below relies on netstat/tasklist/taskkill output formats
    pid = _find_pid_listening_on_port(port)
    if pid is None or pid == os.getpid():
        return  # port free, or somehow already us - nothing to do

    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3)
        print(f"Port {port} is already served by a healthy instance (PID {pid}) - not starting a second one.", file=sys.stderr)
        sys.exit(0)
    except Exception:
        pass  # didn't answer HTTP within 3s (any reason) - treat as stuck, not healthy

    name = _process_name(pid)
    if not name or name.lower() != "python.exe":
        print(
            f"Port {port} is held by PID {pid} ({name or 'unknown process'}), not python.exe - "
            "leaving it alone. Can't bind until that's freed some other way.",
            file=sys.stderr,
        )
        return

    print(f"Port {port} is held by an unresponsive python.exe (PID {pid}) - stopping it before starting fresh.", file=sys.stderr)
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass

    waited = 0.0
    while _find_pid_listening_on_port(port) is not None and waited < max_wait_s:
        time.sleep(0.5)
        waited += 0.5


_boot_checkpoint("all top-level imports and module setup finished")

if __name__ == "__main__":
    # Redirect stdout/stderr to a real log file before anything else runs -
    # every launch path (the .exe shortcut, the auto-start scheduled task,
    # a manual run) starts this hidden/detached with nowhere for print()
    # output or an unhandled-exception traceback to go otherwise, which is
    # exactly why past crashes here left zero diagnostic trail. Appended,
    # not overwritten, so a history of restarts survives; a clear
    # timestamped separator marks where each run begins.
    os.makedirs(LOG_DIR, exist_ok=True)
    _log_path = os.path.join(LOG_DIR, "server.log")
    _log_file = open(_log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file
    print(f"\n=== Server starting {datetime.now().isoformat()} (PID {os.getpid()}) ===")
    _boot_checkpoint("stdout/stderr redirected to server.log - from here on, see that file")

    ensure_port_ready(PORT)
    ensure_port_ready(REMOTE_PORT)

    print(f"Media Library server starting at http://127.0.0.1:{PORT}")
    if HOST == "127.0.0.1":
        print("Bound to localhost only - not reachable from other devices.")
    else:
        _ips = _lan_ips()
        _names = _lan_hostnames()
        if _ips or _names:
            print("On this network (phone/tablet, same Wi-Fi) - type the whole thing incl. http:// :")
            for _name in _names:
                print(f"    http://{_name}:{PORT}")
            for _ip in _ips:
                print(f"    http://{_ip}:{PORT}")
            if _names:
                print("  (the .local name keeps working from an iPhone/iPad even if the IP ever changes)")
            print(f"Couch-remote only (no library grid - bookmark this on a phone used just as a remote):")
            for _name in _names:
                print(f"    http://{_name}:{REMOTE_PORT}")
            for _ip in _ips:
                print(f"    http://{_ip}:{REMOTE_PORT}")
        print(f"Bound to {HOST} - reachable on the LAN. Changes still require the auth token.")
    print(f"Session auth token (regenerates each restart): {AUTH_TOKEN[:12]}...")

    # data/session_auth.json (plan item 16 / SPEC 5) - the ONLY way a
    # separate OS process (handlers/OpenVlcHandler.exe, launched by the
    # openvlc:// protocol dispatch, not by this server) can learn the
    # current AUTH_TOKEN to call /api/watched itself after a VLC watch
    # completes. Same trust boundary as every other file under data/ -
    # local user account only, this doesn't widen it. Overwritten (not
    # appended) every restart so a stale token from a previous session
    # never lingers.
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "session_auth.json"), "w", encoding="utf-8") as f:
            json.dump({"token": AUTH_TOKEN, "port": PORT}, f)
    except OSError as e:
        print(f"Could not write data/session_auth.json - VLC auto-watch tracking won't reach this server: {e}")
    if app_config.CONFIGURED:
        start_refresh(STARTUP_SCRIPTS)
        print("Quick scan (the incoming folder + TMDB match + consolidate) started in the background.")
    else:
        print("No library configured yet - serving the setup page at /setup.")

    # YouTube block's hourly channel poller - one daemon thread, idempotent,
    # every cycle internally try/excepted. Guarded here too so a failure
    # never touches the main server start below.
    if youtube_available:
        try:
            youtube.start_poller(_server_log)
            print("YouTube block poller started in the background.")
        except Exception as ex:  # noqa: BLE001
            _server_log(f"youtube poller failed to start: {ex}")
    # Second listening socket for REMOTE_PORT, same Flask `app` object - no
    # second app, no duplicated routes, just a second werkzeug dev server
    # bound to a different port in a daemon thread alongside the main one
    # below (2026-09-17, see REMOTE_PORT's own comment above). A daemon
    # thread dies with the process automatically, same as every other
    # background thread this file starts (the YouTube poller above) - no
    # separate shutdown path needed.
    threading.Thread(
        target=lambda: app.run(host=HOST, port=REMOTE_PORT, debug=False, use_reloader=False, threaded=True),
        daemon=True,
    ).start()

    # threaded=True (2026-08-22) - a real page load fires off hundreds of
    # near-simultaneous image requests (every poster/backdrop/cast photo
    # currently in view), and the single-threaded default serialized every
    # one of them, one at a time, on top of whatever else the server was
    # doing (a background quick-scan, another request). state-changing
    # routes already go through update_state()'s own lock (app/
    # state_store.py) or one of the dedicated _refresh_lock/_purge_lock/
    # _autofile_lock locks below, so real concurrent requests don't corrupt
    # shared state; pipeline/tmdb_image_cache.py's ensure_cached() got its
    # own fix the same day (a unique temp filename per call, not a shared
    # one) for the one place real concurrency could have caused a genuine
    # write race.
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)

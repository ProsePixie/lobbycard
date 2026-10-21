r"""File the download queue into the real archive.

Takes every video sitting in the incoming queue that TMDB can
confirm, works out its correct ``<library>/<Category>\<Show>\Season NN\``
destination (canonical name, same rules as Auto-File / autofile_naming.py),
and moves it there.

Unlike web_server.py's ``run_autofile()`` this is a deliberate, explicit
"file it now" operation, so:

* **not gated on watched** - filing a file doesn't mean you've watched it,
  it just means you want it in the archive rather than the queue.
* **TV + movies** - queue TV is filed here too (was Auto-File-excluded as
  the companion tool's territory). Coordinate with the companion tool
  before running if it's mid-flow on the same downloads.
* **delete-after-watching shows are still handled** - a show whose
  retention is ``delete_after_watch`` is planned but defaults to
  *unselected* (no point archiving something you'll delete after watching).

**Collisions:** if the destination file already exists, the two are ranked
by ``scan_media.quality_score`` (resolution, then source, then size). The
better copy ends up in the archive; the loser is moved to
``Pending Deletion\<show>\`` - never deleted here, never silently
overwritten.

**"What's New" is untouched** - this only moves files on disk (mtime
preserved) and never writes ``media_db.json``. ``latest_added`` and the
7-day What's New / Recently Downloaded list are recomputed unchanged by the
next refresh.

Per-file state (watched / retention override / travel selection) is
carried forward from the old queue path to the new archive path, same as
``run_autofile()`` / ``rename_queue_inplace.py`` do via
``migrate_state_paths.py``.

Modes::

  python file_queue.py --plan
      Scan + verify + write generated\file_queue_plan.json. Moves nothing.

  python file_queue.py --execute [selection.json]
      Move the items named in selection.json ({"selected": ["<src_video>", ...]}).
      With no selection file: move every item the plan marked default_selected.

  python file_queue.py --go
      --plan then --execute of the default_selected set, one shot (for VORTEX).

Exit codes: 0 = clean, 2 = finished but something was skipped/flagged,
1 = hard error (bad args, lock held, unreadable db).
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "pipeline"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "plans"))

from autofile_naming import (  # noqa: E402
    named_season_to_tmdb,
    extract_resolution_tag, find_matching_sidecars, looks_multi_episode,
    movie_dest_dir, movie_filename, movie_name_year, release_episode_title,
    resolve_between_seasons_special, tv_dest_dir, tv_filename, validate_tv_episode,
)
from build_movie_reorg_manifest import BUCKET_CATEGORIES, bucket_for  # noqa: E402
from scan_media import ROOTS, VIDEO_EXTENSIONS, is_ignored_dir, quality_score  # noqa: E402
from state_store import load_state, show_key, update_state  # noqa: E402

from app_config import DATA_DIR  # noqa: E402
MEDIA_DB_PATH = os.path.join(DATA_DIR, "media_db.json")
SEASON_CACHE_PATH = os.path.join(DATA_DIR, "tmdb_season_cache.json")
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
PLAN_PATH = os.path.join(GENERATED_DIR, "file_queue_plan.json")
LOCK_PATH = os.path.join(DATA_DIR, "file_queue.lock")
PENDING_DELETION_DIR = os.path.join(PROJECT_ROOT, "Pending Deletion")
# A companion tool's status file - a best-effort sibling-project
# path, never required: missing/unreadable just means an empty exclude set,
# not a failure. Not our data, never written here, only ever read.
from app_config import companion_file  # noqa: E402
COMPANION_STATUS_PATH = companion_file("companion_status.json")

from app_config import ARCHIVE_ROOT, QUEUE_LABEL as QUEUE_ROOT, QUEUE_PATH as QUEUE_ROOT_PATH  # noqa: E402
ONGOING_STATUSES = {"Returning Series", "In Production", "Planned"}
LOCK_STALE_SECONDS = 2 * 60 * 60
# Same set web_server.py's _is_orphaned_leftover() treats as "safe to sweep".
DISPOSABLE_SIDECAR_EXTS = {
    ".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt", ".nfo", ".txt",
    ".jpg", ".jpeg", ".png", ".sfv", ".md5", ".par2", ".exe", ".nzb",
}
# Junk-sweep policy: the sample-vs-real threshold is a flat size floor, not a
# runtime probe - every title actually owned in this library is comfortably
# multiple GB; the smallest real thing file_queue has ever planned against
# is in the hundreds of MB. 150 MB give samples/previews (typically well
# under 100 MB) a wide margin while still catching a genuine, if small,
# real file. Adjust here if a real edge case surfaces.
MIN_REAL_VIDEO_BYTES = 150 * 1024 * 1024
SAMPLE_NAME_RE = re.compile(r"\bsample\b", re.IGNORECASE)


def safe_folder_name(name):
    for ch in '<>:"/\\|?*':
        name = (name or "Other").replace(ch, "_")
    return name.strip()




def load_companion_ready_dirs():
    """Normcased set of incoming folder paths the companion tool currently
    has tracked as ready to file (its ready_to_file_source_dirs). Planned items
    under one of these are
    left unselected by default so a one-click `--go` from VORTEX doesn't
    race the companion tool's own filing. Missing file / bad JSON / no such key ->
    empty set, never an error - this is a courtesy read of another
    project's file, not a dependency file_queue can require."""
    try:
        with open(COMPANION_STATUS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    return {os.path.normcase(os.path.normpath(p)) for p in (data.get("ready_to_file_source_dirs") or [])}


def _top_level_queue_folder(path):
    """The direct child of QUEUE_ROOT_PATH that contains `path` (normcased,
    normpath'd - same format load_companion_ready_dirs() already uses) -
    find_junk_folders() only ever considers folders at that exact level,
    so this is what a planned item's own path needs to collapse to before
    it can be excluded from the junk scan. None if `path` isn't under the
    queue root at all (shouldn't happen for a real queue entry, but never
    guess a match here over silently including nothing)."""
    try:
        rel = os.path.relpath(path, QUEUE_ROOT_PATH)
    except ValueError:
        return None
    if rel == os.curdir or rel.startswith(os.pardir):
        return None
    first_segment = rel.split(os.sep)[0]
    return os.path.normcase(os.path.normpath(os.path.join(QUEUE_ROOT_PATH, first_segment)))


# ------------------------------------------------------------ junk sweep --
def _has_real_video(folder):
    """True if `folder` contains at least one video file that isn't a
    sample/preview - see MIN_REAL_VIDEO_BYTES's own comment for the
    threshold reasoning. Recurses one level into subfolders (a season-pack-
    style wrapper), same tolerance _is_orphaned_leftover() below does not
    need since that one only ever looks at what's left AFTER a real video
    was already pulled out."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return False
    for entry in entries:
        if entry.is_dir(follow_symlinks=False):
            if _has_real_video(entry.path):
                return True
            continue
        if os.path.splitext(entry.name)[1].lower() not in VIDEO_EXTENSIONS:
            continue
        if SAMPLE_NAME_RE.search(entry.name):
            continue
        try:
            if entry.stat().st_size >= MIN_REAL_VIDEO_BYTES:
                return True
        except OSError:
            continue
    return False


def _folder_size(folder):
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def find_junk_folders(exclude_dirs=None):
    """Every top-level incoming release folder with no real video in
    it - empty, junk-only (nfo/jpg/par2/...), or sample-only
    (an explicit user rule). Only whole release folders - a loose file sitting
    directly in the queue root is left alone, same folder-level convention
    Pending Deletion already uses everywhere else. `exclude_dirs` (normcased
    paths) is for the companion tool's ready_to_file_source_dirs - never sweep
    something they're actively about to file."""
    exclude_dirs = exclude_dirs or set()
    out = []
    if not os.path.isdir(QUEUE_ROOT_PATH):
        return out
    try:
        candidates = [e for e in os.scandir(QUEUE_ROOT_PATH) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return out
    for entry in candidates:
        if os.path.normcase(os.path.normpath(entry.path)) in exclude_dirs:
            continue
        if is_ignored_dir(entry.path):
            continue  # opted out of the app entirely - never sweep it either
        if _has_real_video(entry.path):
            continue
        out.append({"path": entry.path, "name": entry.name, "size_bytes": _folder_size(entry.path)})
    return out


def sweep_junk_folders(log, exclude_dirs=None):
    """Moves every junk folder (find_junk_folders(), freshly re-scanned -
    never trusts a plan's stale junk[] list) to Pending Deletion\\ directly
    (not nested under a show - these aren't matched to any title). Always a
    move, never a delete, same as everywhere else. Returns (swept, failed)."""
    swept = failed = 0
    for j in find_junk_folders(exclude_dirs):
        try:
            os.makedirs(PENDING_DELETION_DIR, exist_ok=True)
            dest = os.path.join(PENDING_DELETION_DIR, j["name"])
            if os.path.exists(dest):
                dest = f"{dest} ({datetime.now().strftime('%Y%m%d_%H%M%S')})"
            shutil.move(j["path"], dest)
            log.append(f"JUNK -> Pending Deletion: {j['path']} -> {dest}")
            swept += 1
        except OSError as ex:
            log.append(f"JUNK SWEEP FAILED (left in place): {j['path']} - {ex}")
            failed += 1
    return swept, failed


# --------------------------------------------------------------- locking --
def acquire_lock():
    if os.path.exists(LOCK_PATH):
        try:
            info = json.load(open(LOCK_PATH, encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        if time.time() - info.get("ts", 0) < LOCK_STALE_SECONDS:
            print(
                f"file_queue is already running (pid {info.get('pid')}, since "
                f"{info.get('started')}). Aborting.", file=sys.stderr,
            )
            sys.exit(1)
        # stale lock - fall through and overwrite
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "ts": time.time(),
                   "started": datetime.now(timezone.utc).isoformat()}, f)


def release_lock():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


# ------------------------------------------------------------- planning ---
def _existing_location(show):
    """This show's existing archive folder + category, if any - so a
    partially-archived show keeps its category/folder instead of re-guessing.
    Mirrors web_server._autofile_existing_location()."""
    archived = [e for e in show["entries"] if e.get("root") == "Archive"]
    if not archived:
        return None, None
    sample_dir = os.path.dirname(archived[0]["path"].rstrip("\\/"))
    category = archived[0].get("category")
    if show["type"] == "TV":
        parts = sample_dir.split(os.sep)
        # strip the season subfolder ("Season 03" or "Specials") so
        # tv_dest_dir() can re-append the right one - without the Specials
        # case a season-0 file lands in ...\Specials\Specials\ (Doctor Who
        # S00E47, 2026-09).
        if parts and (parts[-1].lower().startswith("season") or parts[-1].lower() == "specials"):
            sample_dir = os.sep.join(parts[:-1])
    return sample_dir, category


def _category_for(show, existing_category):
    if existing_category:
        return existing_category
    genres = show.get("tmdb_genres") or []
    if show["type"] == "TV":
        tv_type = show.get("tmdb_tv_type")
        if tv_type == "Miniseries":
            return "Mini-Series"
        if tv_type == "Documentary" or "Documentary" in genres:
            return "Documentaries"
        return "Series"
    if "Documentary" in genres:
        return "Documentaries"
    if "Music" in genres:
        return "Music"
    return "Movies"


STALE = "stale"  # media_db entry whose video file is gone/moved - not a filing candidate, a refresh clears it


def _plan_entry(show, entry, retention):
    """Returns (item_dict, None) or (None, reason_str). reason_str == STALE
    means "the db is out of date here", surfaced as a count + a refresh
    nudge rather than a per-item failure."""
    video_path = entry.get("video_path")
    if (not video_path or not os.path.isfile(video_path)
            or os.path.splitext(video_path)[1].lower() not in VIDEO_EXTENSIONS):
        return None, STALE
    if not show.get("tmdb_matched") or not show.get("tmdb_id"):
        return None, "show is not confidently matched to TMDB - never filed on a guess"

    original_names = (os.path.basename(entry["path"].rstrip("\\/")), os.path.basename(video_path))
    ext = os.path.splitext(video_path)[1]
    resolution = extract_resolution_tag(*original_names)
    existing_dir, existing_cat = _existing_location(show)
    category = _category_for(show, existing_cat)
    tmdb_name = show.get("tmdb_name") or show["title"]

    if show["type"] == "TV":
        season, episode = entry.get("season"), entry.get("episode")
        if season is None or episode is None:
            return None, "no season/episode could be parsed from the filename"
        if looks_multi_episode(*original_names):
            return None, "looks like a multi-episode file - never auto-renamed"
        # A returning series routinely has its newest TMDB season created
        # (often future-dated) while the PRIOR season is still gaining late
        # episodes - so "current airing season" can't be just
        # number_of_seasons == season (that missed Diners S56E06 while S57
        # already existed, 2026-09). Anything within one of the latest.
        ep_name = validate_tv_episode(
            show["tmdb_id"], season, episode, SEASON_CACHE_PATH, DATA_DIR,
            is_current_airing_season=(
                show.get("tmdb_status") in ONGOING_STATUSES
                and (show.get("tmdb_number_of_seasons") or 0)
                - named_season_to_tmdb(show["tmdb_id"], season, DATA_DIR) <= 1
            ),
        )
        if ep_name is None and episode == 0:
            # SxxE00 by scene convention = a between-seasons special; TMDB
            # has no such slot, it lives under Season 0. Resolve it there
            # by an exact title match before giving up, and re-home the
            # season/episode so it files into Specials\ with real numbering.
            special = resolve_between_seasons_special(
                show["tmdb_id"], release_episode_title(*original_names), DATA_DIR
            )
            if special:
                season, episode, ep_name = special
        if ep_name is None:
            if episode == 0:
                return None, ("looks like a between-seasons special (SxxE00) - TMDB files these "
                              "under Season 0 and it couldn't be matched there by title")
            return None, f"TMDB has no S{season:02d}E{episode:02d} for this show - never filed on a guess"
        dest_dir = tv_dest_dir(category, tmdb_name, season, existing_dir)
        dest_name = tv_filename(tmdb_name, season, episode, ep_name, resolution, ext)
    else:
        season = episode = ep_name = None
        year = (show.get("tmdb_release_date") or "")[:4] or ((show.get("years") or [None])[0])
        if not existing_dir and category in BUCKET_CATEGORIES:
            dest_dir = os.path.join(ARCHIVE_ROOT, category, bucket_for(movie_name_year(tmdb_name, year)))
        else:
            dest_dir = movie_dest_dir(category, tmdb_name, year, existing_dir)
        dest_name = movie_filename(tmdb_name, year, resolution, ext)

    dest_full = os.path.join(dest_dir, dest_name)

    # Collision = the SAME episode / movie is already archived here, even if
    # under a different quality tag in the filename (Show.S01E01.1080p.mkv
    # vs .2160p.mkv). Match on SxxExx for TV, on the "<Title (Year)>" stem
    # for movies - not on an exact filename, or an upgrade would just land
    # a second copy beside the old one.
    existing = None
    if os.path.isdir(dest_dir):
        if show["type"] == "TV":
            token = f"s{season:02d}e{episode:02d}"
            match = lambda fn: token in fn.lower()  # noqa: E731
        else:
            stem = movie_name_year(tmdb_name, year).lower()
            match = lambda fn: fn.lower().startswith(stem)  # noqa: E731
        for fn in sorted(os.listdir(dest_dir)):
            if os.path.splitext(fn)[1].lower() in VIDEO_EXTENSIONS and match(fn):
                existing = os.path.join(dest_dir, fn)
                break

    collision = None
    if existing:
        new_q = quality_score(os.path.basename(video_path))
        old_q = quality_score(os.path.basename(existing))
        try:
            new_bigger = os.path.getsize(video_path) > os.path.getsize(existing)
        except OSError:
            new_bigger = False
        replace = new_q > old_q or (new_q == old_q and new_bigger)
        collision = {
            "existing": existing,
            "existing_name": os.path.basename(existing),
            "new_quality": list(new_q),
            "existing_quality": list(old_q),
            "action": "replace" if replace else "keep_existing",
        }

    default_selected = retention != "delete_after_watch"
    if collision and collision["action"] == "keep_existing":
        default_selected = False

    return {
        "src_video": video_path,
        "src_entry_path": entry["path"],
        "show_title": show["title"],
        "show_key": show.get("show_key"),
        "tmdb_id": show["tmdb_id"],
        "type": show["type"],
        "season": season,
        "episode": episode,
        "episode_title": ep_name,
        "category": category,
        "dest_dir": dest_dir,
        "dest_name": dest_name,
        "dest_full": dest_full,
        "retention": retention,
        "collision": collision,
        "default_selected": default_selected,
        "companion_pending": False,  # build_plan() sets this + re-forces default_selected
        "size_bytes": entry.get("size_bytes") or 0,
    }, None


def _newest_queue_folder_mtime():
    """Newest mtime of any folder directly under the queue root - a cheap
    (single os.scandir, no recursion, no TMDB) signal for "something's
    arrived since the last refresh". Used only to flag staleness below,
    never to decide what to actually file - build_plan() still trusts
    media_db.json alone for that (real case: a file renamed via
    rename_queue_inplace.py's live folder scan seconds after downloading
    was invisible to --plan, which only knows what the last refresh
    recorded, with no way to tell the difference between "genuinely
    nothing to file" and "my data just hasn't caught up yet")."""
    newest = None
    try:
        with os.scandir(QUEUE_ROOT_PATH) as it:
            for entry in it:
                if not entry.is_dir():
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if newest is None or mtime > newest:
                    newest = mtime
    except OSError:
        return None
    return newest


def build_plan():
    with open(MEDIA_DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)
    retention_map = load_state().get("retention", {})
    mf_ready = load_companion_ready_dirs()

    items, unmatched, stale = [], [], 0
    for s in shows:
        if s.get("type") == "Other":
            continue
        retention = retention_map.get(show_key(s)) or "archive"
        for e in s["entries"]:
            if e.get("root") != QUEUE_ROOT:
                continue
            item, reason = _plan_entry(s, e, retention)
            if item:
                # The companion tool is actively tracking this queue folder as
                # ready to file on their side - never default it selected,
                # so a one-click --go can't race them.
                if os.path.normcase(os.path.normpath(item["src_entry_path"])) in mf_ready:
                    item["companion_pending"] = True
                    item["default_selected"] = False
                items.append(item)
            elif reason == STALE:
                stale += 1
            else:
                unmatched.append({
                    "src": e.get("video_path") or e["path"],
                    "show_title": s["title"],
                    "reason": reason,
                })

    # Same-destination dedupe: the queue can hold the same episode twice at
    # different quality. Keep the better one, send the rest to "unmatched".
    def _real(p):
        try:
            return os.path.normcase(os.path.realpath(p))
        except OSError:
            return os.path.normcase(os.path.normpath(p))

    by_dest = {}
    for it in items:
        by_dest.setdefault(os.path.normcase(it["dest_full"]), []).append(it)
    deduped = []
    for group in by_dest.values():
        # First collapse rows that point at the SAME physical file - right
        # after a --refresh, media_db.json can briefly carry both a stale
        # and a fresh record for one file (a rename that just happened),
        # and without this the file gets compared against itself and one
        # copy surfaces as a phantom "superseded" row while the other is
        # filed (VORTEX, 2026-09-05). Keep the first, drop the rest silently.
        seen_files, unique = set(), []
        for it in group:
            rk = _real(it["src_video"])
            if rk in seen_files:
                continue
            seen_files.add(rk)
            unique.append(it)
        group = unique
        if len(group) == 1:
            deduped.append(group[0])
            continue
        group.sort(key=lambda it: (quality_score(os.path.basename(it["src_video"])), it["size_bytes"]), reverse=True)
        deduped.append(group[0])
        for loser in group[1:]:
            unmatched.append({
                "src": loser["src_video"], "show_title": loser["show_title"],
                "reason": "superseded by a higher-quality copy of the same episode in the queue",
            })

    # A folder that TMDB-confirmed matched into items[] above is, by
    # definition, not "no real video" - even if the episode itself is
    # small enough to trip _has_real_video()'s size floor (real case: a
    # genuine ~57MB matched episode showed up in BOTH items[] - as a
    # collision-skip, "keep_existing" - AND junk[] at once, offering the
    # user "here's an episode, skip it" and "here's junk, sweep it" for
    # the identical folder). Excluding every matched folder from the junk
    # scan removes the double-classification regardless of which side of
    # a collision it landed on - a skipped-as-already-archived item is
    # still a real matched episode, just as much as a selected one.
    matched_dirs = {d for d in (_top_level_queue_folder(it["src_entry_path"]) for it in deduped) if d}
    junk = find_junk_folders(exclude_dirs=mf_ready | matched_dirs)

    # Staleness signal - separate from stale_count above (which counts entries
    # whose file already vanished). data_stale means the OPPOSITE data
    # problem: something's arrived in the queue since media_db.json was
    # last written, so this plan can't know about it at all - a silent
    # empty result would otherwise look identical to "genuinely nothing to
    # file". Comparison only, never changes what gets planned/filed.
    # De-dupe needs-review rows by source path - items[] is already
    # dest-deduped above, but two media_db records for the same show (a
    # duplication the next consolidate pass will clean up, but which can
    # sit in the file meanwhile - see app/web_server._dedupe_show_records)
    # produced the same unfileable queue file as three byte-identical
    # unmatched rows (Doctor Who, 2026-09-05, reported by VORTEX).
    seen_unmatched = set()
    deduped_unmatched = []
    for u in unmatched:
        sig = os.path.normcase(os.path.normpath(u["src"]))
        if sig in seen_unmatched:
            continue
        seen_unmatched.add(sig)
        deduped_unmatched.append(u)
    unmatched = deduped_unmatched

    media_db_mtime = os.path.getmtime(MEDIA_DB_PATH)
    newest_queue_mtime = _newest_queue_folder_mtime()
    data_stale = bool(newest_queue_mtime and newest_queue_mtime > media_db_mtime)

    plan = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "archive_root": ARCHIVE_ROOT,
        "queue_root": QUEUE_ROOT,
        "items": deduped,
        "unmatched": unmatched,
        "stale_count": stale,
        "junk": junk,
        "junk_count": len(junk),
        "data_stale": data_stale,
        "media_db_refreshed_at": datetime.fromtimestamp(media_db_mtime, timezone.utc).isoformat(),
        "newest_queue_mtime": (
            datetime.fromtimestamp(newest_queue_mtime, timezone.utc).isoformat() if newest_queue_mtime else None
        ),
        "note": "Built from media_db.json's queue entries. stale_count = entries whose video "
                "file is already gone (filed elsewhere, or moved) - a library refresh clears "
                "those. data_stale = true means something in the queue folder is NEWER than "
                "this data (media_db_refreshed_at vs. newest_queue_mtime) - a library refresh "
                "will pick it up; this plan simply can't see it yet, that's not the same as "
                "there being nothing to file. junk[] = incoming release folders with no "
                "real (non-sample) video in them - empty, junk-only, or sample-only; "
                "default-on candidates for a Pending Deletion sweep, see sweep_junk_folders(). "
                "A folder that matched into items[] (even as a collision-skip) never also "
                "appears in junk[], regardless of its size - it's a real matched episode, not "
                "junk, even if smaller than the usual real-video floor.",
    }
    os.makedirs(GENERATED_DIR, exist_ok=True)
    # ensure_ascii=True on purpose: this file is consumed by VORTEX, which
    # hit mojibake (`Â·` for `·`) reading it back with a non-UTF-8 default
    # codec. Pure-ASCII \uXXXX escapes are codec-proof for any JSON reader.
    with open(PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=True)
    return plan


# ------------------------------------------------------------ execution ---
def _is_orphaned_leftover(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for name in names:
        full = os.path.join(folder, name)
        if os.path.isdir(full) or os.path.splitext(name)[1].lower() not in DISPOSABLE_SIDECAR_EXTS:
            return False
    return True


def _move_to_pending(path, show_title, log):
    dest_dir = os.path.join(PENDING_DELETION_DIR, safe_folder_name(show_title))
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path.rstrip("\\/")))
    if os.path.exists(dest):
        dest = f"{dest} ({datetime.now().strftime('%Y%m%d_%H%M%S')})"
    shutil.move(path, dest)
    log.append(f"  -> Pending Deletion: {path} -> {dest}")
    return dest


def execute(items, log):
    filed = replaced = skipped = failed = 0
    state_moves = []  # (old_entry_path, new_video_path)

    for it in items:
        src = it["src_video"]
        dest_full = it["dest_full"]
        try:
            if not os.path.exists(src):
                log.append(f"SKIP (source gone): {src}")
                skipped += 1
                continue

            coll = it.get("collision") or {}
            existing = coll.get("existing")
            if existing and os.path.exists(existing):
                if coll.get("action") != "replace":
                    log.append(f"SKIP (kept the existing equal/better copy): {src}  ==  {existing}")
                    skipped += 1
                    continue
                _move_to_pending(existing, it["show_title"], log)
                replaced += 1

            # Never silently overwrite whatever is sitting exactly at the
            # destination path (a same-name file the collision scan didn't
            # flag - e.g. it was added since the plan was built).
            if os.path.exists(dest_full):
                log.append(f"SKIP (a file is already at the destination): {src} -> {dest_full}")
                skipped += 1
                continue

            os.makedirs(it["dest_dir"], exist_ok=True)
            sidecars = find_matching_sidecars(src)
            shutil.move(src, dest_full)
            stem = os.path.splitext(it["dest_name"])[0]
            moved_sc = 0
            for sc in sidecars:
                sc_dest = os.path.join(it["dest_dir"], stem + os.path.splitext(sc)[1])
                if not os.path.exists(sc_dest):
                    shutil.move(sc, sc_dest)
                    moved_sc += 1
            log.append(
                f"FILED: {src} -> {dest_full}"
                + (f"  (+{moved_sc} sidecar file(s))" if moved_sc else "")
            )
            filed += 1
            state_moves.append((it["src_entry_path"], dest_full))

            # sweep the now-empty release folder
            folder = it["src_entry_path"].rstrip("\\/")
            if (os.path.normcase(folder) != os.path.normcase(src)
                    and os.path.isdir(folder) and _is_orphaned_leftover(folder)):
                _move_to_pending(folder, it["show_title"], log)
        except OSError as ex:
            log.append(f"FAILED (left in place): {src} - {ex}")
            failed += 1

    if state_moves:
        _carry_state_forward(state_moves, log)

    return filed, replaced, skipped, failed


def _carry_state_forward(moves, log):
    """watched / retention_overrides / travel_selection keyed by the old
    queue path -> the new archive path, so a filed-but-unwatched episode
    doesn't get flipped to watched by resolve_watched()'s Archive default
    (the exact bug migrate_state_paths.py fixes for reorg renames)."""
    counts = {"watched": 0, "retention_overrides": 0, "travel_selection": 0}

    def mutator(state):
        watched = state.setdefault("watched", {})
        overrides = state.setdefault("retention_overrides", {})
        travel = state.setdefault("travel_selection", [])
        for old_path, new_path in moves:
            if old_path == new_path:
                continue
            if old_path in watched:
                watched[new_path] = watched.pop(old_path)
                counts["watched"] += 1
            if old_path in overrides:
                overrides[new_path] = overrides.pop(old_path)
                counts["retention_overrides"] += 1
            if old_path in travel:
                travel[travel.index(old_path)] = new_path
                counts["travel_selection"] += 1

    update_state(mutator)
    if any(counts.values()):
        log.append(
            f"State carried forward: {counts['watched']} watched, "
            f"{counts['retention_overrides']} retention override(s), "
            f"{counts['travel_selection']} travel selection(s)."
        )


# ---------------------------------------------------------------- CLI ----
def _select(plan, selection_path):
    """Returns (chosen_items, sweep_junk_requested). sweep_junk can only
    come from the selection file ({"selected": [...], "sweep_junk": true}
    shape) - there's
    nothing to read it from when falling back to plan defaults."""
    if selection_path:
        with open(selection_path, encoding="utf-8") as f:
            sel = json.load(f)
        wanted = set(sel.get("selected", []))
        chosen = [it for it in plan["items"] if it["src_video"] in wanted]
        return chosen, bool(sel.get("sweep_junk"))
    return [it for it in plan["items"] if it["default_selected"]], False


def _summarise(plan):
    n = len(plan["items"])
    d = sum(1 for it in plan["items"] if it["default_selected"])
    coll = sum(1 for it in plan["items"] if it["collision"])
    mf = sum(1 for it in plan["items"] if it.get("companion_pending"))
    print(f"Queue plan: {n} verified item(s) ({d} selected by default), "
          f"{coll} destination collision(s), {len(plan['unmatched'])} needs-review, "
          f"{plan.get('stale_count', 0)} stale (refresh clears), "
          f"{plan.get('junk_count', 0)} junk folder(s), {mf} held for the companion tool.")
    for it in plan["items"]:
        tag = "[x]" if it["default_selected"] else "[ ]"
        c = ""
        if it["collision"]:
            c = "  REPLACES lower-quality existing" if it["collision"]["action"] == "replace" \
                else "  keeps existing (equal/better)"
        if it.get("companion_pending"):
            c += "  the companion tool has this queued to file"
        print(f"  {tag} {it['show_title']}"
              + (f" S{it['season']:02d}E{it['episode']:02d}" if it["type"] == "TV" else " (movie)")
              + f" -> {it['dest_full']}{c}")
    for u in plan["unmatched"]:
        print(f"   -  {u['show_title']}: {u['reason']}  ({os.path.basename(u['src'])})")
    for j in plan.get("junk", []):
        print(f"   junk: {j['name']}  ({j['size_bytes']/1e6:.0f} MB, no real video)")


def _run_execute(chosen, log, sweep_junk=False, mf_exclude=None):
    started = datetime.now(timezone.utc).isoformat()
    log.append(f"=== file_queue execute started {started} ===")
    filed, replaced, skipped, failed = execute(chosen, log)
    swept = sweep_failed = 0
    if sweep_junk:
        swept, sweep_failed = sweep_junk_folders(log, exclude_dirs=mf_exclude)
    log.append(
        f"Done: {filed} filed, {replaced} replaced, {skipped} skipped, {failed} failed"
        + (f"; {swept} junk folder(s) swept, {sweep_failed} failed" if sweep_junk else "") + "."
    )
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "file_queue_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("\n".join(log))
    print(f"\nLog: {log_path}")
    if failed or sweep_failed:
        return 1
    return 2 if skipped else 0


def main():
    parser = argparse.ArgumentParser(description="File the download queue into the library.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plan", action="store_true", help="Scan + verify only; write the plan JSON.")
    group.add_argument("--execute", nargs="?", const="", metavar="selection.json",
                       help="Move the selected items (or every default_selected item with no file).")
    group.add_argument("--go", action="store_true", help="Plan then execute the default set - one shot.")
    parser.add_argument(
        "--sweep-junk", action="store_true",
        help="Also move empty/junk/sample-only queue folders to Pending Deletion. "
             "Combine with --execute, or use alone for just the sweep. --go always sweeps.",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Run the fast rescan (scan_media -> enrich_tmdb -> consolidate, "
             "skipping the slow nightly-only steps) BEFORE planning, so a file "
             "downloaded since the last full refresh is actually seen. Same subset "
             "the server runs on startup. Combine with --plan or --go.",
    )
    args = parser.parse_args()

    acquire_lock()
    try:
        if args.refresh:
            # a sanctioned CLI entry point to the same fast rescan the server
            # already runs on startup, so "File" right after downloading
            # doesn't just show stale data. Guarded by acquire_lock() above
            # against a second file_queue run; a concurrent in-app "Refresh
            # library" isn't lock-shared, but the worst case there is
            # media_db.json getting rewritten twice in quick succession
            # (last complete scan wins) - wasted work, never corruption.
            from refresh_scripts import run_quick_scan
            rlog = []
            print("--refresh: running the fast rescan first...", file=sys.stderr)
            ok, failing = run_quick_scan(log=rlog)
            for ln in rlog:
                print("  " + ln, file=sys.stderr)
            if not ok:
                print(f"--refresh: {failing} failed - planning against the data as it was.", file=sys.stderr)

        if args.execute is not None:
            if not os.path.exists(PLAN_PATH):
                print("No plan found - run --plan first.", file=sys.stderr)
                return 1
            with open(PLAN_PATH, encoding="utf-8") as f:
                plan = json.load(f)
            chosen, sel_sweep_junk = _select(plan, args.execute or None)
            do_sweep = args.sweep_junk or sel_sweep_junk
            if not chosen and not do_sweep:
                print("Nothing selected to file.")
                return 0
            return _run_execute(chosen, [], sweep_junk=do_sweep, mf_exclude=load_companion_ready_dirs())

        if not args.plan and not args.go and args.sweep_junk:
            # standalone sweep - no TMDB plan needed for this
            log = [f"=== file_queue junk sweep started {datetime.now(timezone.utc).isoformat()} ==="]
            swept, sweep_failed = sweep_junk_folders(log, exclude_dirs=load_companion_ready_dirs())
            log.append(f"Done: {swept} junk folder(s) swept, {sweep_failed} failed.")
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, "file_queue_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log))
            print("\n".join(log))
            print(f"\nLog: {log_path}")
            return 1 if sweep_failed else 0

        plan = build_plan()
        _summarise(plan)
        print(f"\nPlan written: {PLAN_PATH}")
        if args.go:
            chosen = [it for it in plan["items"] if it["default_selected"]]
            if not chosen and not plan.get("junk_count"):
                print("\nNothing to file.")
                return 0
            print(f"\n--go: filing {len(chosen)} default-selected item(s) and sweeping "
                  f"{plan.get('junk_count', 0)} junk folder(s)...\n")
            return _run_execute(chosen, [], sweep_junk=True, mf_exclude=load_companion_ready_dirs())
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())

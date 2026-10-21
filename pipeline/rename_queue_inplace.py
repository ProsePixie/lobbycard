"""One-off utility (2026-08-23, at the user's request via the Vortex hub's
"Rename Downloads" button): applies the exact same TMDB-verified naming
convention Auto-File uses (app/autofile_naming.py) to files still sitting
in the download queue, WITHOUT moving them into the archive - purely a
filename/foldername cleanup, so a queue folder full of scene-release names
becomes readable at a glance in Explorer before you've even watched
anything, and before the real Auto-File pass ever runs.

Renames both the video file AND its wrapping release folder (2026-08-23 -
the first version only renamed the file, leaving the folder itself still
showing the messy original scene-release name in Explorer, which defeated
half the point). The folder only gets renamed when it's safe to treat as
a single release: it must actually contain this one video (not be a queue
root itself) and must not contain any OTHER video file (a season-pack-
style folder with multiple videos is left alone - renaming it based on
just one entry would be misleading for the others still inside).

Deliberately simpler than web_server.py's real run_autofile(): no
watched/retention gating (queue items get cleaned up regardless of watch
status - that's the whole point of running this before you've watched
anything), no destination-folder computation, no same-pass duplicate
resolution (two files that would land on an identical name are flagged
and skipped, not resolved - a rare edge case not worth the complexity
here, unlike the real archive-filing pass where it matters a lot more).
Still shares the same "never guess" backbone: a TV episode is only
renamed once validate_tv_episode() confirms it against real TMDB data,
exactly like the real Auto-File does - a queue file that can't be
confirmed is left exactly as-is, never touched on a guess. Reboot /
dual-entry shows (a parsed season well past the matched TMDB entry's
season count - Kitchen Nightmares' 2023 revival is TMDB S01-03 while scene
files call the same episodes S08-10) are treated as ambiguous and skipped
with a distinct reason rather than mis-filed.

Three run modes (VORTEX's Rename button uses plan -> pick-the-right-one
popup -> apply, mirroring the File flow):
  (default, no args)  plan + immediately apply every confident rename, as
                      it always has. Ambiguous/unconfirmed items untouched.
  --plan              write generated/rename_plan.json (`ready` +
                      `needs_review`, each with `flags` and, for
                      reboot-ambiguous ones, `alternatives` - the
                      counted-straight-through readings that validate
                      against TMDB). Changes nothing on disk.
  --apply SEL.json    execute ONLY the renames named in a selection file
                      ({"selected": [{"src_path", "name"}]}); `name` is
                      authoritative, so the popup can pick an alternative.

Carries forward watched/retention_override/travel_selection state across
the folder rename the same way run_autofile() does for its own renames
(plans/migrate_state_paths.py) - state is keyed by the release folder
path, so renaming it without this would silently orphan any state a user
already set on something in the queue (a preview watch, a travel-export
pick) before running this.

Deliberately does NOT force a fresh scan_media.py rescan of the WHOLE
library to fix a stale cached video_path (running this script twice back
to back is exactly when that would be stale, since the first run just
renamed it) - tried that 2026-08-23 and it caused a worse problem:
scan_media.py re-derives each show's raw grouping title from whatever's on
disk, and a title parsed from a freshly-cleaned filename doesn't always
match the exact string its own carry-forward logic keys off of, silently
resetting tmdb_matched to None for every show this script had just
renamed. resolve_actual_video_path() below checks the release folder's
real contents directly instead when the cached path is gone.

Scans the queue roots directly every run (scan_media.scan_root(), just the
3 small queue folders - cheap, not a full-library pass) rather than only
looking at whatever media_db.json already happens to have (2026-08-26, the
user's explicit ask: "it should be able to run ... on its own", not only
after opening the app and refreshing first). For anything media_db.json
already has a confident match for, that cached data is reused outright -
zero extra API calls, identical accuracy to before. For anything it
doesn't (a file that landed since the last refresh, or before the app has
ever been opened at all), this does its own real, independent TMDB
search+validate - the exact same "never guess" matching
travel\\import_new_travel_files.py already does for files scan_media.py
has never seen either. See find_or_match_show() below.
"""
import argparse
import json
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "plans"))
from autofile_naming import (  # noqa: E402
    extract_resolution_tag, find_matching_sidecars, movie_filename,
    release_episode_title, resolve_between_seasons_special, tv_filename,
    named_season_to_tmdb, validate_tv_episode,
)
from render_engine import AUTOFILE_QUEUE_ROOTS, autofile_confidently_matched  # noqa: E402
from scan_media import ROOTS, VIDEO_EXTENSIONS, normalize_key, scan_root  # noqa: E402
from migrate_state_paths import migrate as migrate_state_paths  # noqa: E402
from enrich_tmdb import api_get, best_match  # noqa: E402
from prune_logs import prune_logs  # noqa: E402 - same dir, no sys.path.insert needed

from app_config import DATA_DIR  # noqa: E402
MEDIA_DB_PATH = os.path.join(DATA_DIR, "media_db.json")
SEASON_CACHE_PATH = os.path.join(DATA_DIR, "tmdb_season_cache.json")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated")
RENAME_PLAN_PATH = os.path.join(GENERATED_DIR, "rename_plan.json")

# Same "still airing, current season isn't final yet" signal
# _autofile_plan_tv (web_server.py) uses - keeps validate_tv_episode() from
# permanently caching a still-airing season as complete before it is.
ONGOING_STATUSES = {"Returning Series", "In Production", "Planned"}

# A parsed season only 1 past the matched TMDB entry's season count is just
# a freshly-aired season TMDB hasn't ticked number_of_seasons for yet -
# allowed (for an ongoing show). Anything further past is the reboot /
# dual-entry trap: scene numbering counts straight through from the
# original series while the modern TMDB entry restarts at S01 - Kitchen
# Nightmares' 2023 revival is TMDB S01-03 but scene files call the same
# episodes S08-10. Renaming on that guess silently mis-files, so it's
# skipped with a distinct reason and left for the Admin season-offset field
# (or the --plan picker) to resolve.
REBOOT_SEASON_GRACE = 1


def reboot_ambiguous(show, season):
    """True when `season` overshoots the matched TMDB entry's known season
    count past the airing-season grace - the classic reboot / dual-entry
    ambiguity. TV only; needs a real matched entry whose
    tmdb_number_of_seasons is known (a fresh find_or_match_show() match
    leaves it None, so this stays False there and validate_tv_episode()'s
    own "no such season" skip still catches a wild number)."""
    n = show.get("tmdb_number_of_seasons") or 0
    if not n or season is None:
        return False
    grace = REBOOT_SEASON_GRACE if show.get("tmdb_status") in ONGOING_STATUSES else 0
    return season > n + grace


def reboot_alternatives(show, entry, video_path):
    """For a reboot-ambiguous item, offer the "counted straight through from
    the original" reading(s): try subtracting a plausible offset so the
    season lands inside the matched entry, and keep any that validate
    against real TMDB data. Only called for flagged items in --plan mode,
    so the extra validate_tv_episode() lookups are off the hot path."""
    n = show.get("tmdb_number_of_seasons") or 0
    season, episode = entry.get("season"), entry.get("episode")
    if not n or season is None or episode is None:
        return []
    ext = os.path.splitext(video_path)[1]
    resolution = extract_resolution_tag(
        os.path.basename(entry["path"].rstrip("\\/")), os.path.basename(video_path)
    )
    show_name = show.get("tmdb_name") or show["title"]
    out = []
    # Candidate offsets: the one that maps onto the entry's LAST season
    # first (most common - the revival's newest season), then a couple
    # either side, capped so a garbage season number can't spray lookups.
    primary = season - n
    for off in dict.fromkeys([primary, primary - 1, primary + 1, season - 1]):
        if off <= 0 or season - off <= 0 or season - off > n + REBOOT_SEASON_GRACE:
            continue
        alt_season = season - off
        ep_name = validate_tv_episode(
            show["tmdb_id"], alt_season, episode, SEASON_CACHE_PATH, DATA_DIR,
            is_current_airing_season=(
                show.get("tmdb_status") in ONGOING_STATUSES
                and n - alt_season <= 1
            ),
        )
        if ep_name is None:
            continue
        out.append({
            "tmdb_id": show["tmdb_id"],
            "season": alt_season,
            "episode": episode,
            "ep_name": ep_name,
            "proposed_name": tv_filename(show_name, alt_season, episode, ep_name, resolution, ext),
            "source": f"season_offset {off}",
        })
    return out


def title_candidate_alternatives(show, entry, video_path):
    """For a multiple_title_candidates item - a near-tied TMDB title match,
    e.g. MasterChef US vs UK, each a SEPARATE show with its own valid
    season/episode numbering (a different shape than reboot_ambiguous's
    single-entry season overrun) - offers both the
    picked candidate and the near-tied runner-up, each validated
    independently against its own TMDB data. TV: the parsed (season,
    episode) validated per-candidate, same never-guess discipline as
    reboot_alternatives(); an unvalidated candidate is silently dropped, not
    offered. Movies: no season/episode to validate - both candidates are
    always offered, named against their own title/year."""
    ext = os.path.splitext(video_path)[1]
    resolution = extract_resolution_tag(
        os.path.basename(entry["path"].rstrip("\\/")), os.path.basename(video_path)
    )
    runner = show.get("_ambiguous_runner") or {}
    candidates = [
        {"id": show["tmdb_id"], "name": show.get("tmdb_name") or show.get("title"),
         "first_air_date": None, "release_date": None},
        runner,
    ]
    season, episode = entry.get("season"), entry.get("episode")
    out = []
    for cand in candidates:
        tmdb_id = cand.get("id")
        if not tmdb_id:
            continue
        name = cand.get("name") or cand.get("title") or show.get("title") or "?"
        if show["type"] == "Movie":
            date_str = cand.get("release_date") or ""
            year = int(date_str[:4]) if date_str[:4].isdigit() else None
            out.append({
                "tmdb_id": tmdb_id, "season": None, "episode": None, "ep_name": None,
                "proposed_name": movie_filename(name, year, resolution, ext),
                "source": "title_candidate",
            })
            continue
        if season is None or episode is None:
            continue
        ep_name = validate_tv_episode(tmdb_id, season, episode, SEASON_CACHE_PATH, DATA_DIR)
        if ep_name is None:
            continue  # this candidate doesn't actually have this episode - never offered on a guess
        out.append({
            "tmdb_id": tmdb_id, "season": season, "episode": episode, "ep_name": ep_name,
            "proposed_name": tv_filename(name, season, episode, ep_name, resolution, ext),
            "source": "title_candidate",
        })
    return out

QUEUE_ROOT_PATHS = {
    os.path.normcase(os.path.abspath(path))
    for _label, (path, kind) in ROOTS.items() if kind == "queue"
}


def resolve_actual_video_path(entry):
    """entry['video_path'] goes stale the instant this script (or a
    previous run of it) renames the file - re-scanning the whole library
    to fix that is worse than the problem it solves (confirmed real,
    2026-08-23: a fresh scan_media.py pass re-derives each show's raw
    grouping title from the files on disk, and a title parsed from a
    freshly-cleaned filename doesn't always land on the exact same string
    the previous scan's carry-forward logic keys off of - it silently
    reset tmdb_matched to None for every show this script had already
    renamed, requiring a full enrich_tmdb.py re-run to fix). Falls back to
    checking what's actually sitting in the release folder right now
    instead - the show/season/episode/tmdb match data in the (otherwise
    stale) media_db.json is still completely valid either way; only the
    exact filename could be out of date."""
    cached = entry.get("video_path")
    if cached and os.path.exists(cached):
        return cached
    folder = (entry.get("path") or "").rstrip("\\/")
    if not folder or not os.path.isdir(folder):
        return None
    try:
        videos = [fn for fn in os.listdir(folder) if os.path.splitext(fn)[1].lower() in VIDEO_EXTENSIONS]
    except OSError:
        return None
    if len(videos) == 1:
        return os.path.join(folder, videos[0])
    return None  # none found, or more than one - ambiguous, don't guess


def plan_rename(show, entry):
    """Returns (new_filename_or_None, reason_if_skipped, actual_video_path_or_None)."""
    video_path = resolve_actual_video_path(entry)
    if not video_path:
        return None, "no playable file found", None
    if not autofile_confidently_matched(show, entry):
        return None, "not confidently matched", None
    if show.get("_ambiguous_runner"):
        # A near-tied TMDB title match at the search step (MasterChef US vs
        # UK, each its own separate, independently-valid show - see
        # title_candidate_alternatives()'s own docstring). Checked here, not
        # just in build_plan(), so run_default()'s apply-immediately path
        # (which calls plan_rename() directly) can't silently rename the
        # wrong country's version just because it happens to validate fine
        # against ITSELF - that's exactly how this class of bug looks like
        # "worked" until someone notices the wrong show.
        return None, "multiple TMDB titles matched this closely - not renamed without a choice", None

    ext = os.path.splitext(video_path)[1]
    resolution = extract_resolution_tag(
        os.path.basename(entry["path"].rstrip("\\/")), os.path.basename(video_path)
    )

    if show["type"] == "TV":
        if entry.get("season") is None or entry.get("episode") is None:
            return None, "no season/episode could be parsed", None
        season, episode = entry["season"], entry["episode"]
        # TMDB season behind this file's season: same unless TMDB names a
        # season after the real series (Bake Off: "Series 17" = TMDB 10).
        tmdb_season = named_season_to_tmdb(show["tmdb_id"], season, DATA_DIR)
        if reboot_ambiguous(show, tmdb_season):
            n = show.get("tmdb_number_of_seasons")
            return None, (
                f"S{season:02d} is past this TMDB entry's {n} season(s) - looks like a "
                f"reboot/dual-entry (scene numbering counting straight through from the "
                f"original). Not renamed on a guess - set a season offset in Admin, or use "
                f"the rename picker."
            ), None
        ep_name = validate_tv_episode(
            show["tmdb_id"], season, episode, SEASON_CACHE_PATH, DATA_DIR,
            is_current_airing_season=(
                show.get("tmdb_status") in ONGOING_STATUSES
                and (show.get("tmdb_number_of_seasons") or 0) - tmdb_season <= 1
            ),
        )
        if ep_name is None and episode == 0:
            # SxxE00 = between-seasons special; TMDB files these under
            # Season 0. Resolve by title and rename to real S00Enn instead
            # of leaving the junk scene name in place. Same helper file_queue
            # uses.
            special = resolve_between_seasons_special(
                show["tmdb_id"],
                release_episode_title(os.path.basename(entry["path"].rstrip("\\/")), os.path.basename(video_path)),
                DATA_DIR,
            )
            if special:
                season, episode, ep_name = special
        if ep_name is None:
            return None, f"TMDB has no S{season:02d}E{episode:02d} for this show - never renamed on a guess", None
        show_name = show.get("tmdb_name") or show["title"]
        return tv_filename(show_name, season, episode, ep_name, resolution, ext), None, video_path

    if show["type"] == "Movie":
        title = show.get("tmdb_name") or show["title"]
        year = (show.get("years") or [None])[0]
        return movie_filename(title, year, resolution, ext), None, video_path

    return None, "not a TV/Movie entry", None


def folder_safe_to_rename(video_dir):
    """True if video_dir is a normal single-release folder: not a queue
    root itself, and holding at most one video file (this one)."""
    if os.path.normcase(os.path.abspath(video_dir)) in QUEUE_ROOT_PATHS:
        return False
    try:
        entries = os.listdir(video_dir)
    except OSError:
        return False
    video_count = sum(1 for fn in entries if os.path.splitext(fn)[1].lower() in VIDEO_EXTENSIONS)
    return video_count <= 1


def build_by_path_index(shows):
    """Fast-path lookup: {normalized queue-item path: show dict} for
    anything the app has already scanned+matched in a prior refresh -
    avoids a redundant live TMDB call for files this app already knows
    about. Only queue-root entries matter here; anything already archived
    was never a rename-queue candidate in the first place."""
    index = {}
    for s in shows:
        if s.get("type") not in ("TV", "Movie"):
            continue
        for e in s.get("entries", []):
            if e.get("root") not in AUTOFILE_QUEUE_ROOTS:
                continue
            index[os.path.normcase(os.path.abspath(e["path"]))] = s
        # A TV show already in the archive under a real TMDB match: a new
        # queue file with the same title is that show, not a fresh
        # near-tied search (2026-09-24: The Great British Bake Off's 2010
        # vs 2017 TMDB entries tied, and the queue file sat on "needs
        # review" with nothing to pick, despite the archive being 87012).
        # TV only - a same-title movie is often a different film.
        if s.get("type") == "TV" and s.get("tmdb_matched") and s.get("tmdb_id")                 and any(e.get("root") not in AUTOFILE_QUEUE_ROOTS for e in s.get("entries", [])):
            index.setdefault(("title", normalize_key(s.get("title") or ""), "TV"), s)
    return index


def find_or_match_show(leaf, by_path):
    """Returns a show-shaped dict with the fields plan_rename()/
    autofile_confidently_matched() need (type, tmdb_matched, tmdb_id,
    tmdb_name, title, tmdb_status, tmdb_number_of_seasons/years), for one
    freshly-scanned queue item - not read from media_db.json's own
    entries[] the way this always used to require. Two paths: (1) fast -
    the app has already scanned+matched this exact path in some prior
    refresh (however old) - reuses that real data outright, zero extra API
    calls, identical accuracy to before this change. (2) fresh - a file
    that landed in the queue since the last refresh, or before the app has
    ever been opened at all - does a real, independent TMDB search+
    validate, matching travel\\import_new_travel_files.py's own precedent
    for files scan_media.py has never seen. Returns None if neither path
    finds a confident match - left exactly as-is downstream, never a
    guess. is_current_airing_season isn't knowable for a fresh match (no
    prior tmdb_status to check), so it's left unset/False - the same safe
    simplification import_new_travel_files.py already accepts: worst case
    a still-airing season's cache goes stale one refresh cycle early,
    never a wrong rename."""
    existing = by_path.get(os.path.normcase(os.path.abspath(leaf["path"])))
    if existing:
        return existing
    if leaf["type"] == "TV":
        owned = by_path.get(("title", normalize_key(leaf["title"] or ""), "TV"))
        if owned:
            return owned

    if leaf["type"] == "TV":
        match, runner = _search_tv_with_ambiguity(leaf["title"], leaf["year"])
        if not match:
            return None
        show = {
            "type": "TV", "tmdb_matched": True, "tmdb_id": match["id"],
            "tmdb_name": match.get("name") or leaf["title"], "title": leaf["title"],
            "tmdb_status": None, "tmdb_number_of_seasons": None,
        }
        if runner:
            # Near-tied title match (e.g. MasterChef US vs UK - same title,
            # different origin_country, each independently valid) - see
            # multiple_title_candidates below. Stashed on the dict itself
            # rather than a separate return value so this keeps working as
            # a plain show-shaped dict for every other reader.
            show["_ambiguous_runner"] = runner
        return show
    if leaf["type"] == "Movie":
        match, runner = _search_movie_with_ambiguity(leaf["title"], leaf["year"])
        if not match:
            return None
        year = (match.get("release_date") or "")[:4] or leaf["year"]
        show = {
            "type": "Movie", "tmdb_matched": True, "tmdb_id": match["id"],
            "tmdb_name": match.get("title") or leaf["title"], "title": leaf["title"],
            "years": [int(year)] if year else [],
        }
        if runner:
            show["_ambiguous_runner"] = runner
        return show
    return None


def _search_tv_with_ambiguity(title, year):
    """Same query search_tv() runs, but via best_match(return_ambiguous=True)
    so a near-tied runner-up (see multiple_title_candidates below) comes
    back too instead of only ever being logged."""
    params = {"query": title, "include_adult": "true"}
    if year:
        params["first_air_date_year"] = year
    data = api_get("/search/tv", params)
    results = data.get("results") if data else None
    match, runner = best_match(results, title, year, "first_air_date", return_ambiguous=True)
    if not match and year:
        data = api_get("/search/tv", {"query": title, "include_adult": "true"})
        results = data.get("results") if data else None
        match, runner = best_match(results, title, year, "first_air_date", return_ambiguous=True)
    return match, runner


def _search_movie_with_ambiguity(title, year):
    """Movie equivalent of _search_tv_with_ambiguity() above."""
    params = {"query": title, "include_adult": "true"}
    if year:
        params["year"] = year
    data = api_get("/search/movie", params)
    results = data.get("results") if data else None
    match, runner = best_match(results, title, year, "release_date", return_ambiguous=True)
    if not match and year:
        data = api_get("/search/movie", {"query": title, "include_adult": "true"})
        results = data.get("results") if data else None
        match, runner = best_match(results, title, year, "release_date", return_ambiguous=True)
    return match, runner


def gather_candidates():
    """(show-shaped match dict, freshly-scanned leaf entry) pairs for every
    TV/Movie file currently sitting in the queue roots. Scans the 3 small
    queue folders directly (cheap, not a full-library walk) and reuses
    media_db.json's confident matches where it has them - see
    find_or_match_show()."""
    media_db_shows = []
    if os.path.exists(MEDIA_DB_PATH):
        with open(MEDIA_DB_PATH, encoding="utf-8") as f:
            media_db_shows = json.load(f)
    by_path = build_by_path_index(media_db_shows)

    candidates = []
    for label, (path, kind) in ROOTS.items():
        if kind != "queue":
            continue
        for leaf in scan_root(label, path):
            if leaf["type"] not in ("TV", "Movie"):
                continue
            show = find_or_match_show(leaf, by_path)
            if show is None:
                continue  # no confident TMDB match - left exactly as-is, never guessed
            candidates.append((show, leaf))
    return candidates


def _skip_flags(reason):
    """Turn plan_rename()'s free-text skip reason into stable flag slugs the
    picker UI can branch on without string-matching."""
    r = (reason or "").lower()
    flags = []
    if "reboot/dual-entry" in r:
        flags.append("reboot_ambiguous")
    if "no season/episode" in r:
        flags.append("no_season_episode")
    if "no playable file" in r:
        flags.append("no_file")
    if "not confidently matched" in r:
        flags.append("unmatched")
    if "tmdb has no s" in r:
        flags.append("episode_not_in_tmdb")
    return flags


def build_plan(candidates, with_alternatives=True):
    """One dict per candidate: the proposed clean name (or None + a reason +
    flags + alternatives when it can't be placed on a guess). This is the
    payload --plan writes to generated/rename_plan.json for VORTEX's
    pick-the-right-one popup; it mutates nothing."""
    items = []
    for show, leaf in candidates:
        new_name, reason, video_path = plan_rename(show, leaf)
        # plan_rename() already refused to rename on show["_ambiguous_runner"]
        # (protects run_default()'s apply-immediately path too, not just
        # this one) - here that just needs a real video_path for
        # title_candidate_alternatives() below, same as reboot_ambiguous
        # already re-resolves it via src= just after.
        if show.get("_ambiguous_runner") and not video_path:
            video_path = resolve_actual_video_path(leaf)
        src = video_path or resolve_actual_video_path(leaf) or (leaf.get("path") or "").rstrip("\\/")
        item = {
            "src_path": src,
            "release_folder": (leaf.get("path") or "").rstrip("\\/"),
            "show_title": show.get("tmdb_name") or show.get("title"),
            "tmdb_id": show.get("tmdb_id"),
            "type": show.get("type"),
            "season": leaf.get("season"),
            "episode": leaf.get("episode"),
            "ep_name": None,
            "proposed_name": new_name,
            "proposed_folder": os.path.splitext(new_name)[0] if new_name else None,
            "flags": [] if new_name else _skip_flags(reason),
            "reason": None if new_name else reason,
            "alternatives": [],
        }
        if show.get("_ambiguous_runner") and "multiple_title_candidates" not in item["flags"]:
            item["flags"].append("multiple_title_candidates")
        if new_name and show.get("type") == "TV" and leaf.get("season") is not None and leaf.get("episode") is not None:
            # Cache hit - plan_rename() just validated this same (s, e).
            item["ep_name"] = validate_tv_episode(
                show["tmdb_id"], leaf["season"], leaf["episode"], SEASON_CACHE_PATH, DATA_DIR,
            )
        if (not new_name) and with_alternatives and video_path:
            if "reboot_ambiguous" in item["flags"]:
                item["alternatives"] = reboot_alternatives(show, leaf, video_path)
            elif "multiple_title_candidates" in item["flags"]:
                item["alternatives"] = title_candidate_alternatives(show, leaf, video_path)
        items.append(item)
    return items


def _rename_one(video_path, target_name, release_folder, log_lines, seen_targets, seen_folder_targets):
    """Rename one video file (+ its sidecars) to target_name, then fold its
    release folder to the matching clean name when that folder is a safe
    single-release folder. Returns (file_renamed, folder_move_or_None,
    skipped) - skipped is True only for a real collision/OSError, not for a
    file that was already correctly named. Shared by run_default() and
    run_apply()."""
    video_dir = os.path.dirname(video_path)
    new_path = os.path.join(video_dir, target_name)
    file_renamed = False
    skipped = False

    if os.path.abspath(new_path) != os.path.abspath(video_path):
        if new_path in seen_targets or os.path.exists(new_path):
            log_lines.append(f"SKIPPED (name collision): {video_path}")
            return False, None, True
        seen_targets.add(new_path)
        try:
            os.rename(video_path, new_path)
            for sidecar in find_matching_sidecars(video_path):
                sidecar_ext = os.path.splitext(sidecar)[1]
                new_sidecar = os.path.splitext(new_path)[0] + sidecar_ext
                if not os.path.exists(new_sidecar):
                    os.rename(sidecar, new_sidecar)
            log_lines.append(f"RENAMED FILE: {os.path.basename(video_path)} -> {target_name}")
            file_renamed = True
        except OSError as ex:
            log_lines.append(f"FAILED: {video_path} ({ex})")
            return False, None, True

    folder_move = None
    if folder_safe_to_rename(video_dir):
        new_folder_name = os.path.splitext(target_name)[0]
        parent_dir = os.path.dirname(video_dir)
        new_folder_path = os.path.join(parent_dir, new_folder_name)
        if os.path.abspath(new_folder_path) != os.path.abspath(video_dir):
            if new_folder_path in seen_folder_targets or os.path.exists(new_folder_path):
                log_lines.append(f"SKIPPED FOLDER (name collision): {video_dir}")
            else:
                seen_folder_targets.add(new_folder_path)
                try:
                    os.rename(video_dir, new_folder_path)
                    log_lines.append(f"RENAMED FOLDER: {os.path.basename(video_dir)} -> {new_folder_name}")
                    folder_move = ((release_folder or video_dir).rstrip("\\/"), new_folder_path)
                except OSError as ex:
                    log_lines.append(f"FAILED FOLDER RENAME: {video_dir} ({ex})")
    elif file_renamed:
        log_lines.append(f"  (left folder as-is: {os.path.basename(video_dir)} - not a single-release folder)")
    return file_renamed, folder_move, skipped


def _write_log(log_lines, renamed, folders_renamed, skipped):
    log_lines.append(f"Done: {renamed} file(s) renamed, {folders_renamed} folder(s) renamed, {skipped} skipped.")
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "rename_queue_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    prune_logs(LOG_DIR, "rename_queue")
    return log_path


def run_plan():
    """--plan: write generated/rename_plan.json, change nothing on disk."""
    items = build_plan(gather_candidates())
    os.makedirs(GENERATED_DIR, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "queue_roots": [p for _l, (p, k) in ROOTS.items() if k == "queue"],
        "ready": [i for i in items if i["proposed_name"]],
        "needs_review": [i for i in items if not i["proposed_name"]],
    }
    tmp = RENAME_PLAN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RENAME_PLAN_PATH)
    print(f"Rename plan: {len(payload['ready'])} ready, {len(payload['needs_review'])} need review.")
    print(f"Wrote {RENAME_PLAN_PATH}")


def run_apply(selection_path):
    """--apply <selection.json>: execute ONLY the chosen renames.
    selection.json = {"selected": [{"src_path": "...", "name": "..."}], ...}
    `name` is authoritative - it can be an alternative the user picked in
    the popup, not just the plan's default proposed_name."""
    with open(selection_path, encoding="utf-8") as f:
        selection = json.load(f)
    wanted = {
        os.path.normcase(os.path.abspath(s["src_path"])): s["name"]
        for s in selection.get("selected", [])
        if s.get("src_path") and s.get("name")
    }
    if not wanted:
        print("Nothing selected in the selection file - nothing to do.")
        return

    # Re-scan so a resolved video_path and the leaf entry (for state
    # carry-forward) are current; match selections by src_path.
    by_src = {}
    for show, leaf in gather_candidates():
        vp = resolve_actual_video_path(leaf)
        if vp:
            by_src[os.path.normcase(os.path.abspath(vp))] = (leaf, vp)

    log_lines = [f"Rename queue --apply {os.path.basename(selection_path)} - started {datetime.now().isoformat()}"]
    renamed = folders_renamed = skipped = 0
    seen_targets, seen_folder_targets, state_path_moves = set(), set(), []

    for norm_src, target_name in wanted.items():
        hit = by_src.get(norm_src)
        if not hit:
            log_lines.append(f"SKIPPED (no longer in queue / already renamed): {norm_src}")
            skipped += 1
            continue
        leaf, video_path = hit
        file_renamed, folder_move, was_skipped = _rename_one(
            video_path, target_name, leaf.get("path"),
            log_lines, seen_targets, seen_folder_targets,
        )
        if file_renamed:
            renamed += 1
        if folder_move:
            folders_renamed += 1
            state_path_moves.append(folder_move)
        if was_skipped:
            skipped += 1
        elif not file_renamed and not folder_move:
            log_lines.append(f"  (already correctly named: {os.path.basename(video_path)})")

    if state_path_moves:
        migrate_state_paths(state_path_moves)
        log_lines.append(f"Carried watched/retention/travel state across folder rename for {len(state_path_moves)} item(s).")
    log_path = _write_log(log_lines, renamed, folders_renamed, skipped)
    print(f"Done: {renamed} file(s) renamed, {folders_renamed} folder(s) renamed, {skipped} skipped.")
    print(f"Log: {log_path}")


def run_default():
    """No args: plan + immediately apply every confident rename (the
    original one-click behaviour). Reboot-ambiguous / unconfirmed items are
    left exactly as-is - use --plan then --apply to resolve those."""
    candidates = gather_candidates()
    log_lines = [f"Rename queue in place - started {datetime.now().isoformat()}"]
    renamed = folders_renamed = skipped = 0
    seen_targets, seen_folder_targets, state_path_moves = set(), set(), []

    for show, leaf in candidates:
        new_name, _reason, video_path = plan_rename(show, leaf)
        if new_name is None:
            continue  # not eligible or couldn't be confirmed - left exactly as-is
        file_renamed, folder_move, was_skipped = _rename_one(
            video_path, new_name, leaf.get("path"),
            log_lines, seen_targets, seen_folder_targets,
        )
        if file_renamed:
            renamed += 1
        if folder_move:
            folders_renamed += 1
            state_path_moves.append(folder_move)
        if was_skipped:
            skipped += 1

    if state_path_moves:
        migrate_state_paths(state_path_moves)
        log_lines.append(f"Carried watched/retention/travel state across folder rename for {len(state_path_moves)} item(s).")
    log_path = _write_log(log_lines, renamed, folders_renamed, skipped)

    if renamed == 0 and folders_renamed == 0:
        print("Nothing to rename - everything in the queue already has a clean name (or there's nothing in the queue right now).")
    else:
        print(f"Done: {renamed} file(s) renamed, {folders_renamed} folder(s) renamed, {skipped} skipped.")
    print(f"Log: {log_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--plan", action="store_true",
        help="write generated/rename_plan.json (ready + needs_review, with flags/alternatives) and change nothing",
    )
    group.add_argument(
        "--apply", metavar="SELECTION.json",
        help='execute only the renames in a selection file: {"selected": [{"src_path": "...", "name": "..."}]}',
    )
    args = parser.parse_args()
    if args.plan:
        run_plan()
    elif args.apply:
        run_apply(args.apply)
    else:
        run_default()


if __name__ == "__main__":
    main()

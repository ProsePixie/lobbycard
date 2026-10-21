"""
Filename/season reorganization - manifest generator (pilot, one show at a time).

Reads media_db.json for a single show, cross-checks every file's parsed
season/episode against TMDB's real episode list (data\\tmdb_season_cache.json,
fetching live if not cached), and produces THREE outputs under generated\\ -
nothing on the library is touched by this script:

  reorg_manifest_<show>.json  - full structured manifest (moves + flagged + collisions)
  reorg_manifest_<show>.txt   - human-readable summary for review
  reorg_plan_<show>.ps1       - the actual Move-Item commands, review-then-run
                                 (writes an audit line to logs\\reorg\\ for every
                                 successful move, and verifies file counts after)

Never trusts the original filename beyond identifying which episode a file
represents - the episode NUMBER is validated against TMDB, and the episode
TITLE used in the new filename always comes from TMDB, never from the
original release name. A file whose season/episode can't be confidently
resolved to exactly one TMDB episode is left out of the manifest entirely
and listed under "flagged" instead - never guessed.

Usage:
  python build_reorg_manifest.py "Des"
  python build_reorg_manifest.py "Doctor Who" --tmdb-id 57243   # disambiguate
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
from app_config import DATA_DIR  # noqa: E402
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs", "reorg")
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
SEASON_CACHE_PATH = os.path.join(DATA_DIR, "tmdb_season_cache.json")

sys.path.insert(0, os.path.join(PROJECT_ROOT, "pipeline"))
from scan_media import VIDEO_EXTENSIONS, RES_RANK, quality_score  # reuse, don't duplicate

from app_config import ARCHIVE_ROOT  # noqa: E402
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".sub", ".idx", ".vtt"}
ILLEGAL_WINDOWS_CHARS = '<>:"/\\|?*'

# "Movies" is the only category a TV-typed show can never legitimately be
# filed under - everything else (Comedy, Documentaries, Mini-Series,
# Series, and even Music - a music documentary mini-series is real TV
# content that belongs staying in Music, confirmed for "Foo Fighters:
# Sonic Highways", 2026-08-21) is fair game, wherever the show is already
# archived. media_db.json's archive_categories can legitimately include
# "Movies" for a TV show (a straggler episode landed there before reorg
# ever ran) - picking archive_categories[0] blindly used to route the
# WHOLE show's manifest into <library>/Movies instead of Series, confirmed
# real for "Revival" (tmdb_id 254013, 2026-08-20): its archive_categories
# was ["Movies", "Series"], so every one of its 9 episodes got moved into
# Movies\Revival\Season 01 instead of Series\Revival. An earlier version of
# this fix used a fixed allowlist instead of excluding "Movies" - too
# narrow, it would have wrongly pulled Sonic Highways OUT of Music into
# Series the same way the original bug pulled Revival into Movies.
NEVER_TV_CATEGORY = "Movies"

# This script always derives a show's destination folder from its TMDB
# name alone - fine for the whole library except a franchise with multiple
# separate TMDB ids sharing the exact same title, where every one of them
# would collide onto the identical folder name with no way to tell them
# apart. Doctor Who is the confirmed real case (tmdb_id 121 classic-era,
# 57243 2005-era, 239770 2023-era - all three literally named "Doctor Who"
# in TMDB) - already organized into separate "Doctor Who (1963)"/"(2005)"/
# "(2023)" folders per an earlier explicit user decision. Running this
# generic script for 121 or 239770 without this override silently proposed
# moving all their files BACK into a plain "Doctor Who" folder, undoing
# that decision - confirmed as the real mechanism behind a self-inflicted
# mix-up on 2026-08-20 (an earlier autonomous batch run had executed those
# two tmdb_ids through this same generic path, which is what caused the
# collision in the first place). Add an entry here for any other
# multi-tmdb-id-same-title franchise if one turns up.
SHOW_FOLDER_NAME_OVERRIDES = {
    121: "Doctor Who (1963)",
    57243: "Doctor Who (2005)",
    239770: "Doctor Who (2023)",
}

from app_config import tmdb_api_key, TMDB_KEY_HELP  # noqa: E402
API_KEY = tmdb_api_key()
if not API_KEY and __name__ == "__main__":
    print(TMDB_KEY_HELP, file=sys.stderr)
    sys.exit(1)

from app_config import TMDB_API_BASE as BASE  # noqa: E402
SLEEP_BETWEEN_CALLS = 0.12


def api_get(path, params):
    params = dict(params)
    params["api_key"] = API_KEY
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1.0)
            continue
    return None


def load_season_cache():
    if not os.path.exists(SEASON_CACHE_PATH):
        return {}
    try:
        with open(SEASON_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_season_cache(cache):
    tmp = SEASON_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    os.replace(tmp, SEASON_CACHE_PATH)


def get_season_episodes(cache, tmdb_id, season_num):
    """{episode_number(str): {air_date, name}}. Reuses/extends the same cache
    enrich_tv_coverage.py writes, keyed identically ("{tmdb_id}:{season}")."""
    key = f"{tmdb_id}:{season_num}"
    entry = cache.get(key)
    if entry is not None and entry.get("complete", True):
        return entry["episodes"]

    data = api_get(f"/tv/{tmdb_id}/season/{season_num}", {})
    time.sleep(SLEEP_BETWEEN_CALLS)
    episodes = {}
    if data and "episodes" in data:
        for ep in data["episodes"]:
            episodes[str(ep["episode_number"])] = {
                "air_date": ep.get("air_date"),
                "name": ep.get("name") or f"Episode {ep['episode_number']}",
            }
    cache[key] = {
        "episodes": episodes,
        "complete": bool(episodes),
        "fetched_at": datetime.now().isoformat(),
    }
    return episodes


def sanitize_folder_name(name):
    for ch in ILLEGAL_WINDOWS_CHARS:
        name = name.replace(ch, "")
    return name.strip().rstrip(".")


def to_dot_token(text):
    """Plex/dot-style filename token: illegal chars stripped, any run of
    whitespace/separators collapsed to a single '.', no leading/trailing dots."""
    for ch in ILLEGAL_WINDOWS_CHARS:
        text = text.replace(ch, "")
    text = re.sub(r"[\s_]+", ".", text.strip())
    text = re.sub(r"\.{2,}", ".", text)
    return text.strip(".")


# Windows reserves these names as legacy DOS devices - when a path's first
# dot-separated segment is EXACTLY one of these (case-insensitive), Windows
# treats it as a device reference instead of a real file, so os.path.exists()
# on a path like "Con.Man.S01E01....mkv" returns True with garbage/zeroed
# stat data instead of doing a real file check - silently breaking this
# script's collision-detection for every single episode of a show whose
# to_dot_token()'d name starts with one of these (confirmed real, "Con Man",
# 2026-08-20 - "ConMan..." and "Con-Man..." don't trigger it, only the exact
# "Con." first segment does).
RESERVED_WINDOWS_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def dodge_reserved_device_name(dot_token):
    """Given an already-to_dot_token()'d string, swap the first '.' for a
    '-' if the segment before it is a bare Windows-reserved device name -
    keeps the same visual token everywhere else, just breaks the exact
    device-name match."""
    parts = dot_token.split(".", 1)
    if len(parts) == 2 and parts[0].upper() in RESERVED_WINDOWS_DEVICE_NAMES:
        return f"{parts[0]}-{parts[1]}"
    return dot_token


def extract_resolution_tag(original_name):
    low = original_name.lower()
    best = None
    best_rank = -1
    for token, rank in RES_RANK.items():
        if token in low and rank > best_rank:
            best, best_rank = token, rank
    return best


MULTI_EP_PATTERN = re.compile(r"[Ss](\d{1,2})[EeXx](\d{1,3})((?:-\d{1,3})|(?:[-_.]?[Ee]\d{1,3}))?")


def looks_like_multi_episode(original_name, season, parsed_episode):
    """scan_media.py's SEASON_EP regex only ever captures the FIRST episode
    number of a combined-episode release (e.g. S06E16-17.avi parses as just
    E16) - it silently can't see the second one. Detect the shape here so we
    flag instead of confidently mis-mapping a two-episode file as one.
    Deliberately narrow: only a hyphen+digits or a second E+digits
    *immediately* following the matched SxxExx token counts - anything else
    following (a dot into a resolution/year/source tag, etc.) does not."""
    for m in MULTI_EP_PATTERN.finditer(original_name):
        if int(m.group(1)) == season and int(m.group(2)) == parsed_episode and m.group(3):
            return True
    return False


def find_video_file(entry):
    vp = entry.get("video_path")
    if vp and os.path.splitext(vp)[1].lower() in VIDEO_EXTENSIONS:
        return vp
    p = entry["path"]
    if os.path.splitext(p)[1].lower() in VIDEO_EXTENSIONS:
        return p
    return None


def find_sidecars(video_path):
    """Subtitle files sitting next to the video, matched by identical stem,
    to be carried along under the new name. Other sidecars (nfo, sample,
    images) are deliberately left behind, untouched."""
    folder = os.path.dirname(video_path)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    out = []
    if not os.path.isdir(folder):
        return out
    for fn in os.listdir(folder):
        full = os.path.join(folder, fn)
        if full == video_path or not os.path.isfile(full):
            continue
        base, ext = os.path.splitext(fn)
        if ext.lower() in SUBTITLE_EXTENSIONS and base == stem:
            out.append(full)
    return out


def find_show(shows, title_query, tmdb_id_override):
    if tmdb_id_override:
        matches = [s for s in shows if s.get("tmdb_id") == tmdb_id_override]
        if not matches:
            print(f"No show in media_db.json has tmdb_id {tmdb_id_override}.", file=sys.stderr)
            sys.exit(1)
        return matches[0]

    exact = [s for s in shows if s["title"].strip().lower() == title_query.strip().lower()]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        print(f"Multiple titles match '{title_query}' exactly - disambiguate with --tmdb-id:", file=sys.stderr)
        for s in exact:
            print(f"  tmdb_id={s.get('tmdb_id')}  type={s['type']}  categories={s.get('archive_categories')}", file=sys.stderr)
        sys.exit(1)

    partial = [s for s in shows if title_query.strip().lower() in s["title"].strip().lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        print(f"No exact title match for '{title_query}'; multiple partial matches - be more specific or use --tmdb-id:", file=sys.stderr)
        for s in partial:
            print(f"  '{s['title']}'  tmdb_id={s.get('tmdb_id')}  type={s['type']}", file=sys.stderr)
        sys.exit(1)

    print(f"No title in media_db.json matches '{title_query}'.", file=sys.stderr)
    sys.exit(1)


def find_related_unprocessed_entries(all_shows, show):
    """Fansub/scene-release-group-prefixed titles (e.g. "[Erai-raws] Shingeki
    no Kyojin S2 - 05...") sometimes never get consolidated with the show's
    main identity in media_db.json - title-text matching just doesn't catch
    them, so they sit as their own separate, usually-unmatched "show" entries
    forever, physically living in the very same folder. A per-title lookup
    like this whole script does would silently never see them. Catch that
    here: any OTHER title-group with a file physically inside this show's
    original archive folder is surfaced for manual attention - never
    auto-merged or auto-moved, since the numbering conventions those groups
    use are frequently inconsistent even with each other (confirmed: some
    use season-relative numbers, some use absolute, with identical-looking
    filename shapes)."""
    own_paths = {e["path"] for e in show["entries"]}
    original_folders = set()
    for e in show["entries"]:
        if e["root"] != "Archive":
            continue
        parts = e["path"].split(os.sep)
        try:
            cat_idx = parts.index(e["category"])
            original_folders.add(os.sep.join(parts[: cat_idx + 2]))  # .../<Category>/<TopFolder>
        except ValueError:
            continue

    related = []
    for other in all_shows:
        if other is show:
            continue
        for e in other.get("entries", []):
            if e.get("path") in own_paths:
                continue
            if any(e.get("path", "").startswith(folder + os.sep) or e.get("path", "") == folder for folder in original_folders):
                related.append({
                    "other_title": other["title"], "other_type": other["type"],
                    "other_tmdb_matched": other.get("tmdb_matched", False),
                    "path": e["path"], "season": e.get("season"), "episode": e.get("episode"),
                    "size_bytes": e.get("size_bytes"),
                })
    return related


def build_manifest(show, season_cache, all_shows=None):
    if show["type"] != "TV":
        print(f"'{show['title']}' is type '{show['type']}', not TV - this script only handles season/episode content.", file=sys.stderr)
        sys.exit(1)
    if not show.get("tmdb_matched") or not show.get("tmdb_id"):
        print(f"'{show['title']}' has no confident TMDB match - resolve that first (pipeline\\enrich_tmdb.py), can't build a manifest without it.", file=sys.stderr)
        sys.exit(1)

    tmdb_id = show["tmdb_id"]
    tmdb_name = show.get("tmdb_name") or show["title"]
    tv_valid_categories = [c for c in (show.get("archive_categories") or []) if c != NEVER_TV_CATEGORY]
    category = tv_valid_categories[0] if tv_valid_categories else "Series"
    show_folder_name = SHOW_FOLDER_NAME_OVERRIDES.get(tmdb_id) or sanitize_folder_name(tmdb_name)
    show_root = os.path.join(ARCHIVE_ROOT, category, show_folder_name)

    archived_entries = [e for e in show["entries"] if e["root"] == "Archive"]

    moves = []
    flagged = []
    already_correct = []
    by_episode_key = {}

    for e in archived_entries:
        original_name = e["name"]
        season = e.get("season")
        episode = e.get("episode")

        video_path = find_video_file(e)
        if video_path is None or not os.path.isfile(video_path):
            flagged.append({
                "old_path": e["path"], "reason": "no video file found at this entry's location (moved/deleted since last scan?)",
            })
            continue

        if season is None or episode is None:
            flagged.append({
                "old_path": video_path, "reason": "no season/episode parsed from the original filename - can't identify which TMDB episode this is",
            })
            continue

        if looks_like_multi_episode(original_name, season, episode):
            flagged.append({
                "old_path": video_path,
                "reason": f"filename looks like it may bundle multiple episodes (e.g. a range like E{episode}-##) - only episode {episode} was parsed, the rest would be silently lost. Needs manual mapping.",
            })
            continue

        episodes = get_season_episodes(season_cache, tmdb_id, season)
        ep_data = episodes.get(str(episode))
        if ep_data is None:
            flagged.append({
                "old_path": video_path,
                "reason": f"TMDB season {season} has no episode {episode} for '{tmdb_name}' (tmdb_id {tmdb_id}) - parsed number doesn't correspond to a real episode",
            })
            continue

        by_episode_key.setdefault((season, episode), []).append({
            "entry": e, "video_path": video_path, "ep_data": ep_data, "original_name": original_name,
        })

    def compute_new_path(candidate, season, episode, ep_data):
        video_path = candidate["video_path"]
        ext = os.path.splitext(video_path)[1]
        res_tag = extract_resolution_tag(candidate["original_name"])
        filename_parts = [dodge_reserved_device_name(to_dot_token(tmdb_name)), f"S{season:02d}E{episode:02d}", to_dot_token(ep_data["name"])]
        if res_tag:
            filename_parts.append(res_tag)
        new_filename = ".".join(filename_parts) + ext
        season_dir_name = "Specials" if season == 0 else f"Season {season:02d}"
        return os.path.join(show_root, season_dir_name, new_filename)

    def same_path(a, b):
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))

    collisions = []
    for (season, episode), candidates in by_episode_key.items():
        ep_data = candidates[0]["ep_data"]

        # Never overwrite a file that's already sitting at the destination -
        # not from this run's candidates, and not from anything else. Check
        # this BEFORE any quality-based ranking, since a file already placed
        # by an earlier run may have had its source/quality tags stripped by
        # the rename itself, making a fresh quality_score() comparison against
        # it meaningless (and dangerous to trust for an overwrite decision).
        already_placed = None
        for c in candidates:
            candidate_dest = compute_new_path(c, season, episode, ep_data)
            if same_path(c["video_path"], candidate_dest):
                already_placed = c
                break

        if already_placed is not None:
            already_correct.append({
                "path": already_placed["video_path"], "season": season, "episode": episode,
                "tmdb_episode_name": ep_data["name"],
            })
            for c in candidates:
                if c is already_placed:
                    continue
                flagged.append({
                    "old_path": c["video_path"],
                    "reason": f"S{season:02d}E{episode:02d} is already correctly filed at the destination (from an earlier run) - this is a leftover duplicate, left in place untouched. Never overwriting an already-placed file.",
                })
            continue  # already done, nothing to add to moves for this episode

        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            ranked = sorted(candidates, key=lambda c: (quality_score(c["original_name"]), c["entry"]["size_bytes"]), reverse=True)
            chosen = ranked[0]
            losers = ranked[1:]
            collisions.append({
                "season": season, "episode": episode,
                "kept": chosen["video_path"], "kept_quality": quality_score(chosen["original_name"]),
                "superseded": [
                    {"path": c["video_path"], "quality": quality_score(c["original_name"])} for c in losers
                ],
            })
            for c in losers:
                flagged.append({
                    "old_path": c["video_path"],
                    "reason": f"duplicate of S{season:02d}E{episode:02d} - lower quality ({quality_score(c['original_name'])}) than the kept copy, not moved (left in place, not deleted)",
                })

        new_path = compute_new_path(chosen, season, episode, ep_data)

        # Destination occupied by something other than any of today's
        # candidates (e.g. a different show's file collided on name, or stale
        # data) - don't guess which should win, flag everything instead.
        if os.path.exists(new_path) and not same_path(chosen["video_path"], new_path):
            flagged.append({
                "old_path": chosen["video_path"],
                "reason": f"S{season:02d}E{episode:02d}'s computed destination ({new_path}) already exists and is a DIFFERENT file than any candidate found for this episode - needs manual resolution, not touched.",
            })
            continue

        video_path = chosen["video_path"]
        new_dir = os.path.dirname(new_path)
        new_stem = os.path.splitext(os.path.basename(new_path))[0]

        sidecars = []
        for sc_path in find_sidecars(video_path):
            sc_ext = os.path.splitext(sc_path)[1]
            sidecars.append({"old_path": sc_path, "new_path": os.path.join(new_dir, new_stem + sc_ext)})

        moves.append({
            "old_path": video_path,
            "new_path": new_path,
            "season": season, "episode": episode,
            "tmdb_episode_name": ep_data["name"],
            "tmdb_air_date": ep_data.get("air_date"),
            "size_bytes": chosen["entry"]["size_bytes"],
            "sidecars": sidecars,
            "source_release_folder": chosen["entry"]["path"] if chosen["entry"]["path"] != video_path else None,
        })

    moves.sort(key=lambda m: (m["season"], m["episode"]))

    related_unprocessed = find_related_unprocessed_entries(all_shows, show) if all_shows is not None else []

    manifest = {
        "show_title": show["title"],
        "tmdb_id": tmdb_id,
        "tmdb_name": tmdb_name,
        "category": category,
        "show_root": show_root,
        "generated_at": datetime.now().isoformat(),
        "source_file_count": len(archived_entries),
        "moves": moves,
        "flagged": flagged,
        "already_correct": already_correct,
        "collisions": collisions,
        "related_unprocessed": related_unprocessed,
    }
    return manifest


def write_outputs(manifest):
    safe_title = sanitize_folder_name(manifest["show_title"]).replace(" ", "_")
    json_path = os.path.join(GENERATED_DIR, f"reorg_manifest_{safe_title}.json")
    txt_path = os.path.join(GENERATED_DIR, f"reorg_manifest_{safe_title}.txt")
    ps1_path = os.path.join(GENERATED_DIR, f"reorg_plan_{safe_title}.ps1")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    lines = [
        f"Reorg manifest: {manifest['show_title']}  (tmdb_id {manifest['tmdb_id']}, '{manifest['tmdb_name']}')",
        f"Category: {manifest['category']}   Show folder: {manifest['show_root']}",
        f"Generated: {manifest['generated_at']}",
        f"Source files found (archived): {manifest['source_file_count']}",
        f"Confident moves: {len(manifest['moves'])}    Already correctly filed: {len(manifest['already_correct'])}    Flagged (not touched): {len(manifest['flagged'])}    Collisions resolved: {len(manifest['collisions'])}",
        "",
        "=== MOVES (will be renamed/relocated if you approve) ===",
    ]
    for m in manifest["moves"]:
        lines.append(f"  S{m['season']:02d}E{m['episode']:02d}  \"{m['tmdb_episode_name']}\"")
        lines.append(f"    FROM: {m['old_path']}")
        lines.append(f"    TO:   {m['new_path']}")
        for sc in m["sidecars"]:
            lines.append(f"    + sidecar: {os.path.basename(sc['old_path'])} -> {os.path.basename(sc['new_path'])}")
        lines.append("")

    if manifest["already_correct"]:
        eps = sorted({(a["season"], a["episode"]) for a in manifest["already_correct"]})
        ep_ranges = ", ".join(f"S{s:02d}E{e:02d}" for s, e in eps[:10]) + (f" (+{len(eps) - 10} more)" if len(eps) > 10 else "")
        lines.append(f"=== ALREADY CORRECTLY FILED - {len(manifest['already_correct'])} episode(s), no action needed: {ep_ranges} ===")
        lines.append("")

    if manifest["collisions"]:
        lines.append("=== COLLISIONS (same TMDB episode, multiple files - lower quality left in place, NOT deleted) ===")
        for c in manifest["collisions"]:
            lines.append(f"  S{c['season']:02d}E{c['episode']:02d}: kept {c['kept']} (quality {c['kept_quality']})")
            for s in c["superseded"]:
                lines.append(f"    superseded, left alone: {s['path']} (quality {s['quality']})")
        lines.append("")

    if manifest["flagged"]:
        lines.append("=== FLAGGED - left untouched, needs your eyes ===")
        for item in manifest["flagged"]:
            lines.append(f"  {item['old_path']}")
            lines.append(f"    -> {item['reason']}")
        lines.append("")

    if manifest["related_unprocessed"]:
        lines.append("=== RELATED FILES NOT COVERED BY THIS MANIFEST ===")
        lines.append("# These live in the same original folder as this show but are filed under a")
        lines.append("# DIFFERENT, separate title in media_db.json (usually a fansub/release-group")
        lines.append("# prefix that never got consolidated with the main title) - this manifest")
        lines.append("# never saw them at all. NOT touched, NOT included in the moves above.")
        lines.append("# Numbering conventions between groups are not reliable or consistent enough")
        lines.append("# to auto-resolve - review manually, or tell me the show and I'll take a")
        lines.append("# closer look episode-by-episode.")
        by_title = {}
        for r in manifest["related_unprocessed"]:
            by_title.setdefault(r["other_title"], []).append(r)
        for title, items in by_title.items():
            lines.append(f"  \"{title}\"  ({len(items)} file(s))")
            for it in items:
                se = f"S{it['season']:02d}E{it['episode']:02d}" if it["season"] is not None else "(no season/episode parsed)"
                lines.append(f"    {se}  {it['path']}")
        lines.append("")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_log = os.path.join(LOG_DIR, f"reorg_audit_{run_ts}.jsonl")

    ps1_lines = [
        f"# Reorg execution plan - generated by build_reorg_manifest.py",
        f"# Show: {manifest['show_title']}  (tmdb_id {manifest['tmdb_id']})",
        f"# Generated: {manifest['generated_at']}",
        f"# REVIEW EVERY LINE BEFORE RUNNING - this moves real files on the library.",
        f"# Every successful move is appended to: {audit_log}",
        "",
        "$ErrorActionPreference = 'Stop'",
        f'$AuditLog = "{audit_log}"',
        'New-Item -ItemType Directory -Force -Path (Split-Path $AuditLog) | Out-Null',
        "",
        "function Write-AuditEntry {",
        "    param($OldPath, $NewPath, $Bytes)",
        "    $entry = [ordered]@{",
        "        timestamp = (Get-Date).ToString('o')",
        f"        show = \"{manifest['show_title']}\"",
        f"        tmdb_id = {manifest['tmdb_id']}",
        "        old_path = $OldPath",
        "        new_path = $NewPath",
        "        size_bytes = $Bytes",
        "    }",
        "    ($entry | ConvertTo-Json -Compress) | Add-Content -LiteralPath $AuditLog -Encoding utf8",
        "}",
        "",
    ]

    for m in manifest["moves"]:
        new_dir = os.path.dirname(m["new_path"])
        ps1_lines.append(f"# --- S{m['season']:02d}E{m['episode']:02d}  {m['tmdb_episode_name']} ---")
        ps1_lines.append(f'New-Item -ItemType Directory -Force -Path "{new_dir}" | Out-Null')
        ps1_lines.append(f'Move-Item -LiteralPath "{m["old_path"]}" -Destination "{m["new_path"]}" -Force')
        ps1_lines.append(f'Write-AuditEntry -OldPath "{m["old_path"]}" -NewPath "{m["new_path"]}" -Bytes {m["size_bytes"]}')
        for sc in m["sidecars"]:
            ps1_lines.append(f'Move-Item -LiteralPath "{sc["old_path"]}" -Destination "{sc["new_path"]}" -Force')
            ps1_lines.append(f'Write-AuditEntry -OldPath "{sc["old_path"]}" -NewPath "{sc["new_path"]}" -Bytes 0')
        ps1_lines.append("")

    expected = len(manifest["moves"])
    orphan_list_path = os.path.join(GENERATED_DIR, "orphaned_folders.txt")
    source_folders = sorted({m["source_release_folder"] for m in manifest["moves"] if m["source_release_folder"]})

    ps1_lines += [
        "# --- Post-move verification ---",
        f'$Expected = {expected}',
        f'$Actual = (Get-ChildItem -LiteralPath "{manifest["show_root"]}" -Recurse -File | Where-Object {{ $_.Extension -notin @(".nfo") }} | Measure-Object).Count',
        'if ($Actual -ge $Expected) { Write-Host "VERIFIED: $Actual file(s) present under the new show folder (expected at least $Expected)." -ForegroundColor Green }',
        'else { Write-Host "MISMATCH: expected at least $Expected files, found $Actual - STOP, do not proceed to another show, investigate first." -ForegroundColor Red }',
        "",
        "# --- Carry watched/retention/travel state forward to the new paths ---",
        "# Without this, resolve_watched() (app\\render_engine.py) falls back to",
        '# root=="Archive" defaulting to watched=True for any renamed file whose',
        "# old-path state record just went orphaned - see migrate_state_paths.py.",
        f'python "{os.path.join(SCRIPT_DIR, "migrate_state_paths.py")}" --audit-log $AuditLog',
        "",
        "# --- Orphan-candidate detection (NOT deleted here) ---",
        "# Source release folders left behind after their video was moved out.",
        "# A folder is only appended to orphaned_folders.txt if, right now, it",
        "# contains nothing but disposable sidecar files (.nfo/.txt/.sfv/.jpg/",
        "# .jpeg/.png/.url/.diz) or is fully empty - no subfolders, no video,",
        "# no unexpected file types. Anything else is left alone, unlisted.",
        "# Actual removal happens later, in one pass, via generated\\cleanup_orphaned_folders.ps1",
        "# (which re-verifies each folder again immediately before removing it).",
        f'$DisposableExt = @(".nfo",".txt",".sfv",".jpg",".jpeg",".png",".url",".diz")',
        f'$OrphanList = "{orphan_list_path}"',
        "$SourceFolders = @(",
    ]
    for i, f in enumerate(source_folders):
        trailing_comma = "," if i < len(source_folders) - 1 else ""
        ps1_lines.append(f'    "{f}"{trailing_comma}')
    ps1_lines += [
        ")",
        "foreach ($f in $SourceFolders) {",
        "    if (-not (Test-Path -LiteralPath $f)) { continue }",
        "    $items = Get-ChildItem -LiteralPath $f -Force",
        "    $isOrphan = ($items | Where-Object { $_.PSIsContainer -or ($DisposableExt -notcontains $_.Extension.ToLower()) }).Count -eq 0",
        "    if ($isOrphan) {",
        "        Add-Content -LiteralPath $OrphanList -Value $f -Encoding utf8",
        '        Write-Host "Orphan candidate appended: $f"',
        "    } else {",
        '        Write-Host "Left alone (contains more than disposable sidecars): $f" -ForegroundColor Yellow',
        "    }",
        "}",
    ]

    # utf-8-sig (BOM), not plain utf-8: Windows PowerShell 5.1 reads a
    # BOM-less .ps1 using the system ANSI codepage, mangling any non-ASCII
    # character in a move path (confirmed real corruption of the episode
    # title "Aesahaettr" on His Dark Materials S02E07, 2026-08-20). The BOM
    # makes PowerShell decode the file as UTF-8 correctly.
    with open(ps1_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(ps1_lines))

    return json_path, txt_path, ps1_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("title", help="Show title as it appears in media_db.json (or a substring)")
    parser.add_argument("--tmdb-id", type=int, default=None, help="Disambiguate by exact tmdb_id instead of title matching")
    args = parser.parse_args()

    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    show = find_show(shows, args.title, args.tmdb_id)
    season_cache = load_season_cache()
    manifest = build_manifest(show, season_cache, all_shows=shows)
    save_season_cache(season_cache)

    json_path, txt_path, ps1_path = write_outputs(manifest)

    print(f"Manifest for '{manifest['show_title']}' (tmdb_id {manifest['tmdb_id']}, '{manifest['tmdb_name']}'):")
    print(f"  Source files (archived): {manifest['source_file_count']}")
    print(f"  Confident moves:         {len(manifest['moves'])}")
    print(f"  Already correctly filed: {len(manifest['already_correct'])}")
    print(f"  Collisions resolved:     {len(manifest['collisions'])}")
    print(f"  Flagged / untouched:     {len(manifest['flagged'])}")
    print()
    accounted_for = len(manifest["moves"]) + len(manifest["flagged"]) + len(manifest["already_correct"])
    if accounted_for != manifest["source_file_count"]:
        print(f"  NOTE: {manifest['source_file_count']} source files but only {accounted_for} accounted for in moves+flagged - investigate before trusting this manifest.", file=sys.stderr)
    print(f"Wrote:\n  {json_path}\n  {txt_path}\n  {ps1_path}")
    print("\nNothing on the library has been touched. Review the .txt summary, then the .ps1, before running anything.")


if __name__ == "__main__":
    main()

"""Flags a movie as runtime_mismatch when its real file duration (ffprobe)
is way off from what TMDB says it should run - a strong signal the TMDB
match is actually wrong (title-only matching occasionally picks the wrong
entry entirely, e.g. a short film sharing a name with a feature). Movies
only for now - TV's per-episode runtime data is much noisier (multi-
episode files, extended cuts, TMDB's own averaged numbers), not trusted
yet. Never flags on missing TMDB or file duration data - never guess.

Nightly/manual-refresh only (not the fast startup quick-scan) - real
file-duration probing is local and fast once cached, but this is
enrichment, not core data integrity, same placement as
enrich_tv_coverage.py.
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
RUNTIME_CACHE_PATH = os.path.join(DATA_DIR, "runtime_cache.json")

sys.path.insert(0, SCRIPT_DIR)
from probe_runtime import get_duration_seconds  # noqa: E402
sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))
from state_store import load_state  # noqa: E402
from atomic_write import atomic_replace


def load_confirmed_runtime_titles():
    """Titles the user has explicitly confirmed are the right TMDB match
    despite the runtime not lining up (a broadcast/streaming cut shorter
    than TMDB's cached theatrical runtime, or TMDB just having bad data
    for a niche title - confirmed real for both: Chris Hardwick:
    Funcomfortable, Ghostheads, Taylor Hawkins Tribute Concert). Without
    this, the flag keeps coming back every run even after confirmation,
    and the app's own UI (render_engine.py) treats runtime_mismatch=True
    as "probably the wrong match" and hides the title's detail data behind
    an (Unknown) badge - so a confirmed-correct match still looked broken
    to the user. {title:{type}:{title}: True}, same key shape as the
    overrides dict in enrich_tmdb.py."""
    state = load_state()
    return {
        key for key, override in state.get("overrides", {}).items()
        if override.get("runtime_confirmed")
    }

# Both must hold together: a 25% swing on a 20-minute short is only 5
# minutes (not actually suspicious), and a 15-minute swing on a 3-hour
# epic is only ~8% (normal cut-to-cut variance) - requiring both avoids
# flagging either edge on its own.
MISMATCH_PCT_THRESHOLD = 0.25
MISMATCH_MIN_MINUTES = 15


def load_cache():
    if os.path.exists(RUNTIME_CACHE_PATH):
        try:
            with open(RUNTIME_CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(cache):
    tmp = RUNTIME_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    atomic_replace(tmp, RUNTIME_CACHE_PATH)


def is_mismatch(file_minutes, tmdb_minutes):
    diff = abs(file_minutes - tmdb_minutes)
    pct = diff / tmdb_minutes if tmdb_minutes else 0
    return diff >= MISMATCH_MIN_MINUTES and pct >= MISMATCH_PCT_THRESHOLD


def main():
    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    cache = load_cache()
    confirmed_titles = load_confirmed_runtime_titles()
    checked = 0
    flagged = 0
    probed_new = 0
    flagged_titles = []

    for s in shows:
        s["runtime_mismatch"] = False
        s["file_runtime_minutes"] = None
        if s["type"] != "Movie" or not s.get("tmdb_matched"):
            continue
        is_confirmed = f"title:{s['type']}:{s['title']}" in confirmed_titles
        tmdb_runtime = s.get("tmdb_runtime")
        if not tmdb_runtime:
            continue  # no TMDB runtime data - never guess

        video_entries = [e for e in s["entries"] if e.get("video_path")]
        if not video_entries:
            continue
        entry = max(video_entries, key=lambda e: e.get("size_bytes", 0))
        # A multi-part disc title (pipeline/scan_media.py's
        # process_video_ts_folder()) has its REAL duration spread across
        # video_path plus every playlist_extra_paths sibling - probing
        # video_path alone measured only the first ~15-20 minute part
        # against TMDB's full-runtime number, guaranteeing a false
        # mismatch flag on every multi-part title. Confirmed real: both
        # Nine Inch Nails DVDs flagged this way, 2026-08-21. Each part is
        # still cached individually by its own path, so a re-run only
        # re-probes whichever part actually changed.
        parts = [entry["video_path"]] + (entry.get("playlist_extra_paths") or [])

        duration_sec = 0.0
        probe_failed = False
        for part_path in parts:
            if part_path in cache:
                part_duration = cache[part_path]
            else:
                part_duration = get_duration_seconds(part_path)
                cache[part_path] = part_duration
                probed_new += 1
            if not part_duration:
                probe_failed = True
                break
            duration_sec += part_duration
        if probe_failed:
            continue  # ffprobe failed on at least one part - never guess

        file_minutes = duration_sec / 60
        s["file_runtime_minutes"] = round(file_minutes)
        checked += 1
        if is_mismatch(file_minutes, tmdb_runtime) and not is_confirmed:
            s["runtime_mismatch"] = True
            flagged += 1
            flagged_titles.append((s["title"], round(file_minutes), tmdb_runtime))

    # Atomic write - see scan_media.py's own copy of this comment
    # (2026-08-21): web_server.py reads this file on every page load, so a
    # direct write here can hand a concurrent reader a torn/truncated file.
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2)
    atomic_replace(tmp, DB_PATH)
    save_cache(cache)

    print(f"Runtime mismatch check: {checked} movies compared ({probed_new} newly probed via ffprobe), {flagged} flagged as possible mismatches.")
    for title, file_min, tmdb_min in flagged_titles:
        print(f"  - {title}: file ~{file_min} min vs TMDB {tmdb_min} min")


if __name__ == "__main__":
    main()

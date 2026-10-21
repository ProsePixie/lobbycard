"""Writes data\\system_status.json - a small, purpose-built status summary
for the VORTEX launcher (or any other local reader) to consume without
parsing the full media_db.json or the nightly-only refresh_latest.log.

Deliberately the LAST script in both PIPELINE_SCRIPTS (web_server.py's
in-app "Refresh library" path) and daily_refresh.ps1's own $Scripts array
(the nightly scheduled-task path) - both pipelines abort on the first
failing script, so this only ever runs after every prior step has
genuinely succeeded. That gives a reader a clean invariant for free: an
unchanged last_refresh timestamp means the most recent refresh attempt
failed somewhere before reaching here, not that everything's fine - no
separate pass/fail field needed, and nothing to keep in sync between two
code paths the way a hand-written "did it work" flag would need.

Written with the same tmp-file + os.replace() atomic-write pattern every
other data file in this project uses (see scan_media.py, state_store.py,
etc.) - a concurrent reader (VORTEX, polling on its own schedule) either
sees the old complete file or the new one, never a partial write."""
import json
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
OUT_PATH = os.path.join(DATA_DIR, "system_status.json")

# atomic_replace() lives in pipeline\, not duplicated here - see that
# module's own docstring for why. Especially worth it for this file
# specifically: it exists BECAUSE VORTEX reads it concurrently, so it's
# exactly as exposed to the same collision class as media_db.json is.
sys.path.insert(0, os.path.join(PROJECT_ROOT, "pipeline"))
from atomic_write import atomic_replace  # noqa: E402

# Mirrors render_engine.py's TV_ACTIVE_STATUSES / computeShowStatusKeys()
# exactly (airing_soon = a real tmdb_next_episode air_date that hasn't
# passed yet) - kept in sync deliberately, not reinvented, so this number
# can never quietly drift from what the app's own status dots mean by the
# same words. See that file's own comment for why 'active' + 'airing_soon'
# together is exactly the Weekly TV filter's definition.
TV_ACTIVE_STATUSES = {"Returning Series", "In Production"}


def main():
    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    today = datetime.now(timezone.utc).date().isoformat()
    show_count = sum(1 for s in shows if s["type"] in ("TV", "Movie"))
    missing_total = sum(1 for s in shows if s.get("missing_seasons") or s.get("missing_episodes"))
    # Item-level breakdown of missing_total, added at a companion tool's
    # request: it wants a freshness + missing-count signal to show the user
    # before a sweep runs, and asked for a new data/media_db_meta.json - but
    # last_refresh + result here already ARE that "a finished scan wrote this,
    # not a partial one" signal (this script is deliberately last in both
    # refresh paths), so these two counts are all that was actually missing.
    # Raw totals across every show - NOT filtered by covered_physically /
    # covered_by_amazon on missing_seasons[] (a reader wanting a "go find
    # this" list should apply that filter itself; enrich_tv_coverage.py sets
    # those flags). missing_total above stays a show count for backwards
    # compatibility; these are the episode/season item counts.
    missing_episode_count = sum(len(s.get("missing_episodes") or []) for s in shows)
    missing_season_count = sum(len(s.get("missing_seasons") or []) for s in shows)
    duplicate_count = sum(len(d["extra_paths"]) for s in shows for d in s.get("duplicates", []))
    airing_soon_count = sum(
        1 for s in shows
        if s["type"] == "TV"
        and s.get("tmdb_status") in TV_ACTIVE_STATUSES
        and (s.get("tmdb_next_episode") or {}).get("air_date", "") >= today
    )

    # Same instant as last_refresh today (this script always runs as the
    # very last pipeline step, so "the refresh happened" and "this file
    # was written" are simultaneous in practice) - kept as a genuinely
    # separate field anyway:
    # last_refresh is meant to answer "when did the library data last get
    # refreshed," written_at "when was this summary file itself last
    # produced." They can only ever drift apart if this script's own
    # timestamp source ever changes to something other than "now" (e.g.
    # sourcing last_refresh from an earlier pipeline stage) - having the
    # separate field already in place means that future change wouldn't
    # need a reader-side schema update. Mirrors the companion tool's own
    # written_at/last_sweep split - one convention across the ecosystem,
    # not two different names for the same idea.
    now = datetime.now(timezone.utc).isoformat()
    status = {
        "last_refresh": now,
        "written_at": now,
        "result": "ok",
        "show_count": show_count,
        "missing_total": missing_total,
        "missing_episode_count": missing_episode_count,
        "missing_season_count": missing_season_count,
        "duplicate_count": duplicate_count,
        "airing_soon_count": airing_soon_count,
    }

    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    atomic_replace(tmp, OUT_PATH)
    print(
        f"Wrote {OUT_PATH}: {show_count} shows/movies, {missing_total} incomplete "
        f"({missing_episode_count} eps + {missing_season_count} seasons), "
        f"{duplicate_count} duplicate files, {airing_soon_count} airing soon."
    )


if __name__ == "__main__":
    main()

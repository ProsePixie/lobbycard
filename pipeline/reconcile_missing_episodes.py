"""Fast local reconciliation of missing_episodes / missing_seasons against
what's actually on disk - no TMDB calls, so it's cheap enough for the
startup quick-scan (heal_reorg_state -> scan_media -> enrich_tmdb ->
consolidate -> THIS), unlike enrich_tv_coverage.py which does a per-season
TMDB fetch and only runs in the full/nightly pipeline.

The problem it fixes: you download a missing episode, the quick-scan picks
the file up into media_db.json's entries[], but missing_episodes still
carries that episode (it was written by the last full enrich_tv_coverage.py
run and consolidate.py just carries it forward untouched) - so the card
keeps showing "1 ep missing" until the next full refresh.

This does the obvious thing: for every TV show, drop any missing_episodes
entry whose (season, episode) now has a matching file, and any
missing_seasons entry whose season now has any owned file. It trusts
scan_media.py's filename parse - a mis-parsed file could clear a flag it
shouldn't, but the next full enrich_tv_coverage.py run re-derives both
lists authoritatively and puts it back. Never ADDS anything to either list
- only removes - so it can't invent a gap.

Only touches shows that actually change; writes media_db.json atomically
(web_server.py reads it on every page load).
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")

sys.path.insert(0, SCRIPT_DIR)
from atomic_write import atomic_replace  # noqa: E402


def main():
    if not os.path.exists(DB_PATH):
        print("reconcile_missing_episodes: no media_db.json yet - nothing to do.")
        return

    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    changed = cleared_eps = cleared_seasons = 0
    touched = []

    for s in shows:
        if s.get("type") != "TV":
            continue
        missing_eps = s.get("missing_episodes") or []
        missing_seasons = s.get("missing_seasons") or []
        if not missing_eps and not missing_seasons:
            continue

        owned_se = set()
        owned_seasons = set()
        for e in s.get("entries", []):
            season = e.get("season")
            if season is None:
                continue
            owned_seasons.add(season)
            episode = e.get("episode")
            if episode is not None:
                owned_se.add((season, episode))

        new_eps = [m for m in missing_eps if (m.get("season"), m.get("episode")) not in owned_se]
        new_seasons = [m for m in missing_seasons if m.get("season") not in owned_seasons]

        if len(new_eps) != len(missing_eps) or len(new_seasons) != len(missing_seasons):
            de = len(missing_eps) - len(new_eps)
            ds = len(missing_seasons) - len(new_seasons)
            s["missing_episodes"] = new_eps
            s["missing_seasons"] = new_seasons
            changed += 1
            cleared_eps += de
            cleared_seasons += ds
            touched.append((s.get("title", "?"), de, ds))

    if not changed:
        print("reconcile_missing_episodes: nothing on disk that was still flagged as missing.")
        return

    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2)
    atomic_replace(tmp, DB_PATH)

    print(f"reconcile_missing_episodes: {changed} show(s) updated - "
          f"{cleared_eps} episode(s) and {cleared_seasons} season(s) no longer missing (file now present).")
    for title, de, ds in touched:
        bits = []
        if de:
            bits.append(f"{de} ep")
        if ds:
            bits.append(f"{ds} season")
        print(f"  - {title}: {', '.join(bits)} cleared")


if __name__ == "__main__":
    main()

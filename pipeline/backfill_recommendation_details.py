"""One-time backfill: adds overview/vote_average to every already-stored
tmdb_recommendations entry, for the recommendation hover-preview (added
2026-08-22). extract_recommendations() itself was already fixed to capture
these fields going forward (no extra API call - they're already in the
same response TMDB sends), but a show that was matched/enriched before
that fix has recommendations saved without them. This re-fetches just the
lightweight /recommendations endpoint (not full show detail) for every
show that already has a recommendations list, and rebuilds it through the
same extract_recommendations() the live pipeline uses - so this can never
drift from what a normal refresh would produce.

Covers both data/media_db.json and data/physical_db.json - physical items
went through the exact same extract_recommendations() gap.

Safe to interrupt and re-run: writes after each file finishes, and only
touches shows that already have a tmdb_recommendations list (nothing here
is used to discover NEW recommendations, only to enrich existing ones).
"""
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from enrich_tmdb import api_get, extract_recommendations, SLEEP_BETWEEN_CALLS  # noqa: E402

from app_config import DATA_DIR  # noqa: E402


def backfill_file(path):
    with open(path, encoding="utf-8") as f:
        shows = json.load(f)

    targets = [
        s for s in shows
        if s.get("tmdb_recommendations") and s.get("tmdb_matched") and s.get("tmdb_id")
    ]
    print(f"{os.path.basename(path)}: {len(targets)} show(s) to backfill")

    updated = 0
    failed = 0
    for i, s in enumerate(targets):
        endpoint = "movie" if s["type"] == "Movie" else "tv"
        data = api_get(f"/{endpoint}/{s['tmdb_id']}/recommendations", {})
        time.sleep(SLEEP_BETWEEN_CALLS)
        if not data:
            failed += 1
            continue
        s["tmdb_recommendations"] = extract_recommendations({"recommendations": data}, s["type"])
        updated += 1
        if (i + 1) % 50 == 0:
            print(f"  ...{i + 1}/{len(targets)}")

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    print(f"  Done: {updated} updated, {failed} failed (left as-is, will retry on the next normal refresh)")


def main():
    backfill_file(os.path.join(DATA_DIR, "media_db.json"))
    backfill_file(os.path.join(DATA_DIR, "physical_db.json"))


if __name__ == "__main__":
    main()

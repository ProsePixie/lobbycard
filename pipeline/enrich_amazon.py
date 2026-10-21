"""Builds data\\amazon_db.json from the hand-maintained
data\\amazon_purchases_seed.json (titles bought on Amazon Prime Video -
added so they don't get forgotten, see Feature_Requests.md item 3).
Reuses enrich_physical.py's TMDB helpers (api_get, extract_cast/
recommendations/trailer, synthetic_entry) rather than duplicating them -
same fetch-by-confirmed-id, cache-forever approach, own cache file
(data\\tmdb_amazon_cache.json).

Unlike the physical collection, these are treated as DIGITAL, not a
separate filtered-out category: no `physical_collection` flag, so they
show up in the default view and count toward the normal library stats
like any other title (the whole point is not forgetting they exist, not
hiding them behind a toggle). The only things that mark an entry as an
Amazon purchase: `is_amazon_purchase: true` and an `amazon_link` (a
generated Prime Video search URL - https://www.<your amazon domain>/s?k=<title>&
i=instant-video - not a precise per-title product link, since there's no
Amazon API/credentials involved anywhere in this project; the user is
assumed already logged into Amazon in their own browser when they click
it). `no_real_file: true` on each entry, same as physical, so the Play/
Location icons null-guard correctly and the entry-actions row shows an
"Open on Amazon" link instead.

Movie overlap is whole-title (a movie is atomic, same reasoning
enrich_physical.py uses). TV overlap is per-season, same model and same
`load_digital_tv_seasons()` enrich_physical.py already has - confirmed
real, not hypothetical: Legion is a genuine case, season 3 already
digital, seasons 1-2 Amazon-only - a whole-title skip would have thrown
those away entirely.

In the regular refresh pipeline (app/web_server.py's PIPELINE_SCRIPTS,
automation/daily_refresh.ps1) alongside enrich_physical.py - adding a
title via the Admin panel needs no script run by hand."""
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from enrich_physical import (
    api_get, extract_cast, extract_recommendations, extract_trailer_key, synthetic_entry,
    load_digital_tv_seasons,
)
from atomic_write import atomic_replace
from added_at import local_naive_iso

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from app_config import AMAZON_DOMAIN, DATA_DIR  # noqa: E402
SEED_PATH = os.path.join(DATA_DIR, "amazon_purchases_seed.json")
OUT_PATH = os.path.join(DATA_DIR, "amazon_db.json")
DIGITAL_DB_PATH = os.path.join(DATA_DIR, "media_db.json")
CACHE_PATH = os.path.join(DATA_DIR, "tmdb_amazon_cache.json")


def amazon_search_link(title, season=None):
    # Per-season query for TV ("Game of Thrones Season 2") - a season-
    # aware search gets meaningfully closer to the actual purchased item
    # than a bare show-title search would for anything with multiple
    # separately-bought seasons, confirmed real (the user's own Prime
    # Video screenshot showed several titles - Game of Thrones, Legion -
    # with more than one purchase tile, implying separately-bought
    # seasons, not one lump purchase).
    # The user's own storefront (amazon_domain / streaming_region in the
    # config): i=instant-video resolves to the same "Prime Video: Search"
    # department on every regional marketplace checked, but it is not
    # assumed to carry over unchanged to one that was not.
    query = f"{title} Season {season}" if season else title
    return f"https://www.{AMAZON_DOMAIN}/s?k={urllib.parse.quote(query)}&i=instant-video"


def load_owned_movies_locally():
    """{tmdb_id} for every locally-owned digital movie - whole-title check,
    correct for movies (atomic, no season nuance)."""
    if not os.path.exists(DIGITAL_DB_PATH):
        return set()
    with open(DIGITAL_DB_PATH, encoding="utf-8") as f:
        digital = json.load(f)
    return {s["tmdb_id"] for s in digital if s.get("type") == "Movie" and s.get("tmdb_matched") and s.get("tmdb_id")}


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    atomic_replace(tmp, CACHE_PATH)


def fetch_detail(cache, kind, tmdb_id):
    key = f"{kind}:{tmdb_id}"
    if key in cache:
        return cache[key]
    endpoint = "movie" if kind == "movie" else "tv"
    append = "videos,credits,recommendations" if kind == "movie" else "external_ids,videos,credits,recommendations"
    detail = api_get(f"/{endpoint}/{tmdb_id}", {"append_to_response": append})
    time.sleep(0.12)
    if not detail or detail.get("success") is False:
        return None
    cache[key] = detail
    return detail


def build_show(seed_item, kind, genre_map, cache, digital_tv_seasons=None):
    tmdb_id = seed_item["tmdb_id"]

    amazon_only_seasons = None
    if kind == "tv":
        owned_seasons = seed_item.get("owned_seasons") or 1
        already_digital = (digital_tv_seasons or {}).get(tmdb_id, set())
        amazon_only_seasons = [s for s in range(1, owned_seasons + 1) if s not in already_digital]
        if not amazon_only_seasons:
            print(f"  SKIPPED: TV id {tmdb_id} ({seed_item.get('title', '?')}) - every listed season "
                  f"(1-{owned_seasons}) is already digital", file=sys.stderr)
            return None
        dropped = sorted(set(range(1, owned_seasons + 1)) - set(amazon_only_seasons))
        if dropped:
            print(f"  note: TV id {tmdb_id} ({seed_item.get('title', '?')}) - season(s) {dropped} already "
                  f"digital, dropped; {amazon_only_seasons} kept as Amazon-only", file=sys.stderr)

    detail = fetch_detail(cache, kind, tmdb_id)
    if not detail:
        print(f"  WARNING: {kind} id {tmdb_id} ({seed_item.get('title', '?')}) failed to fetch, skipped", file=sys.stderr)
        return None

    show_type = "Movie" if kind == "movie" else "TV"
    name = detail.get("title") or detail.get("name") or seed_item.get("title", "?")
    genre_ids = [g["id"] for g in (detail.get("genres") or [])]
    genres = [genre_map.get(gid) for gid in genre_ids if genre_map.get(gid)]
    date_str = detail.get("release_date") or detail.get("first_air_date") or ""
    year = int(date_str[:4]) if date_str[:4].isdigit() else None
    cast = extract_cast(detail)

    if kind == "movie":
        link = amazon_search_link(name)
        entries = [synthetic_entry(f"amazon://movie/{tmdb_id}", name, root="Amazon")]
        episode_count, version_count = None, 1
        for e in entries:
            e["amazon_link"] = link
    else:
        link = amazon_search_link(name)  # show-level fallback (used on the collapsed card view)
        entries = [
            synthetic_entry(f"amazon://tv/{tmdb_id}/season/{s}", f"{name} - Season {s} (Amazon)", season=s, episode=1, root="Amazon")
            for s in amazon_only_seasons
        ]
        for e in entries:
            e["amazon_link"] = amazon_search_link(name, season=e["season"])
        episode_count, version_count = len(entries), len(entries)

    show = {
        "title": name, "type": show_type, "genre": genres[0] if genres else "Uncategorized",
        "years": [year] if year else [],
        "episode_count": episode_count, "version_count": version_count,
        "roots": ["Amazon"], "archive_categories": ["Amazon"], "pipeline_stage": "Archived",
        "entries": entries,
        # Set by /api/amazon/add (seed entries added before that stamped
        # anything have no date and stay None - no invented dates).
        "latest_added": local_naive_iso(seed_item.get("added_at")), "total_size_bytes": 0, "wasted_bytes": 0,
        "duplicates": [], "missing_episodes": [], "missing_seasons": [],
        "episodes_expected_so_far": None, "assumed_physical_media": False,
        "completeness_skip_reason": None, "runtime_mismatch": False, "file_runtime_minutes": None,
        "tmdb_matched": True, "tmdb_genres": genres or None, "tmdb_id": tmdb_id, "tmdb_name": name,
        "tmdb_overview": detail.get("overview", ""), "tmdb_poster": detail.get("poster_path"),
        "tmdb_backdrop": detail.get("backdrop_path"), "tmdb_vote": detail.get("vote_average"),
        "tmdb_release_date": date_str or None,
        "tmdb_cast": cast, "tmdb_trailer_key": extract_trailer_key(detail),
        "tmdb_recommendations": extract_recommendations(detail, show_type),
        "is_amazon_purchase": True, "amazon_link": link,
    }
    if kind == "movie":
        crew = (detail.get("credits") or {}).get("crew") or []
        show["tmdb_runtime"] = detail.get("runtime") or None
        show["tmdb_imdb_id"] = detail.get("imdb_id") or None
        show["tmdb_director"] = next((c.get("name") for c in crew if c.get("job") == "Director"), None)
    else:
        show["tmdb_status"] = detail.get("status")
        show["tmdb_tv_type"] = detail.get("type")
        show["tmdb_number_of_seasons"] = detail.get("number_of_seasons")
        show["tmdb_imdb_id"] = (detail.get("external_ids") or {}).get("imdb_id") or None
        show["tmdb_creators"] = [c.get("name") for c in (detail.get("created_by") or []) if c.get("name")] or None
    return show


def main():
    with open(SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)

    movie_genre_map = {g["id"]: g["name"] for g in (api_get("/genre/movie/list", {}) or {}).get("genres", [])}
    tv_genre_map = {g["id"]: g["name"] for g in (api_get("/genre/tv/list", {}) or {}).get("genres", [])}
    owned_movies_locally = load_owned_movies_locally()
    digital_tv_seasons = load_digital_tv_seasons()
    cache = load_cache()

    shows = []
    fetched, failed, skipped_owned = 0, 0, 0

    for item in seed.get("movies", []):
        if item["tmdb_id"] in owned_movies_locally:
            print(f"  SKIPPED: movie id {item['tmdb_id']} - already owned locally", file=sys.stderr)
            skipped_owned += 1
            continue
        show = build_show(item, "movie", movie_genre_map, cache)
        if show:
            shows.append(show)
            fetched += 1
        else:
            failed += 1

    for item in seed.get("tv", []):
        show = build_show(item, "tv", tv_genre_map, cache, digital_tv_seasons)
        if show:
            shows.append(show)
            fetched += 1
        elif item["tmdb_id"] in digital_tv_seasons:
            skipped_owned += 1
        else:
            failed += 1

    shows.sort(key=lambda s: s["title"].lower())

    # Atomic write - see pipeline/scan_media.py's own copy of this comment
    # (2026-08-21): web_server.py reads this file on every page load, so a
    # direct write here can hand a concurrent reader a torn/truncated file.
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2, ensure_ascii=False)
    atomic_replace(tmp, OUT_PATH)
    save_cache(cache)

    print(f"\nWrote {len(shows)} Amazon-purchase entries to {OUT_PATH}")
    print(f"TMDB-fetched: {fetched}, failed: {failed}, skipped (already owned locally): {skipped_owned}")


if __name__ == "__main__":
    main()

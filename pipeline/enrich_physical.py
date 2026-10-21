"""Builds data\\physical_db.json from the hand-maintained
data\\physical_media_seed.json (the DVD/Blu-ray shelf inventory - see
generated\\Physical_Collection_Inventory.md for the full identification
trail). Every tmdb_id in the seed file was already verified against a live
TMDB search during that identification pass, so this script fetches by id
directly (append_to_response=videos,credits,recommendations, same as
enrich_tmdb.py) rather than re-searching - no guessing, no risk of a
second search drifting from the confirmed match.

Output shows use the same field shape as media_db.json (title, type,
entries[], tmdb_* fields, ...) so they can later be concatenated with the
scanned digital library at render time with minimal special-casing. Two
things distinguish a physical entry from there on:
  - physical_collection: true on the show itself
  - no_real_file: true on each entry (no path on the library exists - the
    Location/Play icons need a null-guard for this, added in render_engine.py)

TV overlap handling is per-season, not per-show (revised 2026-08-20 after
Futurama surfaced a real case where whole-show exclusion was wrong -
digital only had seasons 10-11, physical had 1-7, completely disjoint,
so skipping the whole physical entry threw away 7 real seasons). This
script READS data\\media_db.json (read-only - never written here) to find
which seasons of a TV show are already digital, and only builds physical
entries for the seasons that aren't. A season owned both ways is dropped
from the physical entries[] (still redundant - digital wins for that one
season); if every physical season turns out to already be digital, the
whole show is skipped, same full-duplicate behavior as before. Movies
stay binary (owned digitally or not, no per-season nuance) - a confirmed
digital movie duplicate is still fully skipped, see
physical_media_seed.json's overlaps_excluded.movies.

Never writes media_db.json (read-only reference for the per-season
overlap check above) - but as of 2026-08-20 this script IS part of the
regular refresh pipeline (app/web_server.py's PIPELINE_SCRIPTS,
automation/daily_refresh.ps1's $Scripts - both need this script listed,
kept in sync deliberately), positioned after enrich_tmdb.py/consolidate.py
(needs fresh digital season data) and before enrich_tv_coverage.py (which
needs a fresh physical_db.json for its own covered_physically cross-
reference). Every seed entry's full TMDB detail is cached forever in
data\\tmdb_physical_cache.json (see fetch_detail()) specifically so running
this nightly doesn't mean re-fetching the whole collection's cast/videos/
recommendations every night - required before this could safely join an
automatic schedule. The practical effect: adding a new physical disc is
now just "add one line to physical_media_seed.json, then click Refresh
library (or wait for the nightly run)" - no script needs to be run by
hand, by you or by a future session, ever again for this step. Can still
be run by hand any time for an immediate rebuild."""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

from tmdb_image_cache import cache_show_images
from atomic_write import atomic_replace

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from app_config import DATA_DIR  # noqa: E402
SEED_PATH = os.path.join(DATA_DIR, "physical_media_seed.json")
OUT_PATH = os.path.join(DATA_DIR, "physical_db.json")
DIGITAL_DB_PATH = os.path.join(DATA_DIR, "media_db.json")
CACHE_PATH = os.path.join(DATA_DIR, "tmdb_physical_cache.json")


def load_digital_tv_seasons():
    """{tmdb_id: {season numbers already owned digitally}} - read-only
    reference so TV overlap can be resolved per-season instead of
    per-show. Missing/unreadable media_db.json just means no overlap
    data - every physical season gets included, never a crash."""
    if not os.path.exists(DIGITAL_DB_PATH):
        return {}
    with open(DIGITAL_DB_PATH, encoding="utf-8") as f:
        digital = json.load(f)
    out = {}
    for s in digital:
        if s.get("type") != "TV" or not s.get("tmdb_id"):
            continue
        seasons = {e["season"] for e in s["entries"] if e.get("season") is not None}
        out.setdefault(s["tmdb_id"], set()).update(seasons)
    return out

from app_config import tmdb_api_key  # noqa: E402
API_KEY = tmdb_api_key()
if not API_KEY and __name__ == "__main__":
    print("No TMDB API key found.", file=sys.stderr)
    sys.exit(1)

from app_config import TMDB_API_BASE as BASE  # noqa: E402
SLEEP_BETWEEN_CALLS = 0.12
CAST_LIMIT = 15
RECOMMENDATIONS_LIMIT = 6


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
            print(f"  TMDB error {e.code} on {path}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1.0)
            continue
    return None


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
    """Full TMDB detail response for one seed entry, cached forever by
    kind+id once fetched - same "confirmed match, never re-searched, never
    re-fetched" reasoning the seed file's own tmdb_id already relies on
    (see this module's docstring). Required before this script is safe to
    run automatically every night instead of by hand - without it, a
    nightly run would re-fetch full cast/video/recommendation data for
    the entire physical collection every single time, for data that's
    essentially never going to change once a title is released."""
    key = f"{kind}:{tmdb_id}"
    if key in cache:
        return cache[key]
    endpoint = "movie" if kind == "movie" else "tv"
    append = "videos,credits,recommendations" if kind == "movie" else "external_ids,videos,credits,recommendations"
    detail = api_get(f"/{endpoint}/{tmdb_id}", {"append_to_response": append})
    time.sleep(SLEEP_BETWEEN_CALLS)
    if not detail or detail.get("success") is False:
        return None
    cache[key] = detail
    return detail


def extract_trailer_key(detail):
    videos = ((detail.get("videos") or {}).get("results")) or []
    trailers = [v for v in videos if v.get("type") == "Trailer" and v.get("site") == "YouTube"]
    if not trailers:
        return None
    trailers.sort(key=lambda v: (not v.get("official", False), v.get("iso_639_1") != "en"))
    return trailers[0].get("key")


def extract_cast(detail):
    cast = ((detail.get("credits") or {}).get("cast")) or []
    return [
        {"id": c.get("id"), "name": c.get("name"), "character": c.get("character"), "profile_path": c.get("profile_path")}
        for c in cast[:CAST_LIMIT] if c.get("name")
    ]


def extract_recommendations(detail, media_type):
    # overview/vote_average already present in the same TMDB response this
    # reads from (no extra API call) - added 2026-08-22, same change and
    # same reasoning as enrich_tmdb.py's own copy of this function (kept
    # separate rather than shared, matching how this whole module already
    # duplicates a few small extract_* helpers rather than importing them).
    recs = ((detail.get("recommendations") or {}).get("results")) or []
    out = []
    for r in recs[:RECOMMENDATIONS_LIMIT]:
        title = r.get("title") or r.get("name")
        if not title:
            continue
        out.append({
            "tmdb_id": r.get("id"), "title": title, "poster_path": r.get("poster_path"), "type": media_type,
            "overview": (r.get("overview") or "")[:200], "vote_average": r.get("vote_average"),
        })
    return out


def fetch_season_episodes(cache, tmdb_id, season_number):
    """Real per-episode list (number, name, air_date) for one owned
    physical season, cached forever the same way fetch_detail() is - a
    season's episode list doesn't change once it's aired, so this is a
    genuine one-time cost per owned season (~86 across the whole physical
    collection today), not something worth re-fetching on every refresh.
    Stores only the fields the app actually displays, not the full raw
    TMDB response (overview/crew/guest_stars etc.) - keeps the cache file
    small since this can be dozens of episodes per season, times however
    many seasons are owned."""
    key = f"season:{tmdb_id}:{season_number}"
    if key in cache:
        return cache[key]
    detail = api_get(f"/tv/{tmdb_id}/season/{season_number}", {})
    time.sleep(SLEEP_BETWEEN_CALLS)
    if not detail or detail.get("success") is False:
        return None
    episodes = [
        {"episode_number": e.get("episode_number"), "name": e.get("name"), "air_date": e.get("air_date")}
        for e in (detail.get("episodes") or []) if e.get("episode_number") is not None
    ]
    cache[key] = episodes
    return episodes


def synthetic_entry(path_key, name, season=None, episode=None, root="Physical", air_date=None):
    # root is also reused by enrich_amazon.py (root="Amazon") - the
    # root-dot/legend (render_engine.py's ROOT_INDEX/ROOT_LABELS_FRIENDLY)
    # is what tells "on the shelf" and "opens Amazon" entries apart at a
    # glance, so this can't default silently wrong for a non-Physical caller.
    # air_date defaults to None (matches every existing caller) - only the
    # real per-episode physical entries pass a real one, added 2026-08-21
    # so a physical episode sorts/displays the same way a digital one does.
    return {
        "path": path_key, "video_path": None, "root": root, "category": root,
        "name": name, "season": season, "episode": episode, "mtime": None, "size_bytes": 0,
        "no_real_file": True, "air_date": air_date,
    }


def build_movie_show(seed_item, genre_map, cache):
    tmdb_id = seed_item["tmdb_id"]
    detail = fetch_detail(cache, "movie", tmdb_id)
    if not detail:
        print(f"  WARNING: movie id {tmdb_id} ({seed_item['spine_title']}) failed to fetch, skipped", file=sys.stderr)
        return None

    title = detail.get("title") or seed_item["spine_title"]
    genre_ids = [g["id"] for g in (detail.get("genres") or [])]
    genres = [genre_map.get(gid) for gid in genre_ids if genre_map.get(gid)]
    release_date = detail.get("release_date") or ""
    year = int(release_date[:4]) if release_date[:4].isdigit() else None
    cast = extract_cast(detail)
    crew = (detail.get("credits") or {}).get("crew") or []

    # Optional hand-written note on the seed entry - e.g. a real bonus
    # episode packaged onto this disc that has no TMDB entry of its own
    # (Futurama's DVD-only extras "Everybody Loves Hypnotoad"/"The Lost
    # Adventure" - real TMDB season-0 episodes, but never worth tracking
    # as their own completeness item, per the user's own call: "they're
    # on the disc, I'll see it when I plug it in"). Prepended to the real
    # TMDB overview rather than given its own UI section - the disc's
    # actual contents speak for themselves once played, this is just a
    # heads-up in the detail view, not a properly tracked thing.
    note = seed_item.get("note")
    overview = detail.get("overview", "")
    if note:
        overview = f"{note}\n\n{overview}" if overview else note

    entry_path = f"physical://movie/{tmdb_id}"
    return {
        "title": title, "type": "Movie", "genre": genres[0] if genres else "Uncategorized",
        "years": [year] if year else [],
        "episode_count": None, "version_count": 1,
        "roots": ["Physical"], "archive_categories": ["Physical"], "pipeline_stage": "Archived",
        "entries": [synthetic_entry(entry_path, title)],
        "latest_added": None, "total_size_bytes": 0, "wasted_bytes": 0,
        "duplicates": [], "missing_episodes": [], "missing_seasons": [],
        "episodes_expected_so_far": None, "assumed_physical_media": False,
        "completeness_skip_reason": None, "runtime_mismatch": False, "file_runtime_minutes": None,
        "tmdb_matched": True, "tmdb_genres": genres or None, "tmdb_id": tmdb_id, "tmdb_name": title,
        "tmdb_overview": overview, "tmdb_poster": detail.get("poster_path"),
        "tmdb_backdrop": detail.get("backdrop_path"), "tmdb_vote": detail.get("vote_average"),
        "tmdb_release_date": release_date or None, "tmdb_runtime": detail.get("runtime") or None,
        "tmdb_imdb_id": detail.get("imdb_id") or None, "tmdb_trailer_key": extract_trailer_key(detail),
        "tmdb_cast": cast, "tmdb_director": next((c.get("name") for c in crew if c.get("job") == "Director"), None),
        "tmdb_recommendations": extract_recommendations(detail, "Movie"),
        "physical_collection": True, "physical_spine_title": seed_item["spine_title"],
    }


def build_tv_show(seed_item, genre_map, digital_tv_seasons, cache):
    tmdb_id = seed_item["tmdb_id"]
    owned_seasons = seed_item["owned_seasons"]
    already_digital = digital_tv_seasons.get(tmdb_id, set())
    physical_only_seasons = [s for s in range(1, owned_seasons + 1) if s not in already_digital]

    if not physical_only_seasons:
        print(f"  SKIPPED: TV id {tmdb_id} ({seed_item['spine_title']}) - every owned season "
              f"(1-{owned_seasons}) is already digital, nothing physical-only to add", file=sys.stderr)
        return None
    dropped = sorted(set(range(1, owned_seasons + 1)) - set(physical_only_seasons))
    if dropped:
        print(f"  note: TV id {tmdb_id} ({seed_item['spine_title']}) - season(s) {dropped} already "
              f"digital, dropped from the physical entry; {physical_only_seasons} kept", file=sys.stderr)

    detail = fetch_detail(cache, "tv", tmdb_id)
    if not detail:
        print(f"  WARNING: TV id {tmdb_id} ({seed_item['spine_title']}) failed to fetch, skipped", file=sys.stderr)
        return None

    name = detail.get("name") or seed_item["spine_title"]
    genre_ids = [g["id"] for g in (detail.get("genres") or [])]
    genres = [genre_map.get(gid) for gid in genre_ids if genre_map.get(gid)]
    first_air = detail.get("first_air_date") or ""
    year = int(first_air[:4]) if first_air[:4].isdigit() else None
    cast = extract_cast(detail)
    runtime = (detail.get("last_episode_to_air") or {}).get("runtime") \
        or (detail.get("next_episode_to_air") or {}).get("runtime")

    # One real entry per episode (added 2026-08-21, per explicit user
    # request: "list every episode per season... a fully functioning
    # Watched for those as well") - each gets its own synthetic path, so
    # the existing per-episode watched-checkbox machinery (already the
    # single source of truth for digital content) just works for physical
    # seasons too, no separate tracking system needed. Falls back to the
    # old one-entry-per-season placeholder ONLY if TMDB's season endpoint
    # genuinely fails (network hiccup, etc.) - never silently drops an
    # owned season just because its episode list couldn't be fetched.
    entries = []
    for s in physical_only_seasons:
        episodes = fetch_season_episodes(cache, tmdb_id, s)
        if episodes:
            for ep in episodes:
                en = ep["episode_number"]
                ep_name = ep.get("name") or f"Episode {en}"
                # Bare episode title, no "S01E01 -" prefix baked in - the
                # entry-row renderer already prepends that itself whenever
                # season+episode are both set (renderEntriesList()), same
                # convention every digital TV entry already follows. Baking
                # it in here too would double it up: "S01E01 - S01E01 - Pilot".
                entries.append(synthetic_entry(
                    f"physical://tv/{tmdb_id}/season/{s}/episode/{en}", ep_name,
                    season=s, episode=en, air_date=ep.get("air_date"),
                ))
        else:
            print(f"  WARNING: TV id {tmdb_id} ({seed_item['spine_title']}) season {s} episode list "
                  f"failed to fetch - falling back to one placeholder entry for that season", file=sys.stderr)
            entries.append(synthetic_entry(
                f"physical://tv/{tmdb_id}/season/{s}", f"{name} - Season {s} (Physical)", season=s, episode=1,
            ))

    return {
        "title": name, "type": "TV", "genre": genres[0] if genres else "Uncategorized",
        "years": [year] if year else [],
        "episode_count": len(entries), "version_count": len(physical_only_seasons),
        "roots": ["Physical"], "archive_categories": ["Physical"], "pipeline_stage": "Archived",
        "entries": entries,
        "latest_added": None, "total_size_bytes": 0, "wasted_bytes": 0,
        "duplicates": [], "missing_episodes": [], "missing_seasons": [],
        "episodes_expected_so_far": None, "assumed_physical_media": False,
        "completeness_skip_reason": None, "runtime_mismatch": False, "file_runtime_minutes": None,
        "tmdb_matched": True, "tmdb_genres": genres or None, "tmdb_id": tmdb_id, "tmdb_name": name,
        "tmdb_overview": detail.get("overview", ""), "tmdb_poster": detail.get("poster_path"),
        "tmdb_backdrop": detail.get("backdrop_path"), "tmdb_vote": detail.get("vote_average"),
        "tmdb_release_date": first_air or None, "tmdb_status": detail.get("status"),
        "tmdb_tv_type": detail.get("type"), "tmdb_number_of_seasons": detail.get("number_of_seasons"),
        "tmdb_imdb_id": (detail.get("external_ids") or {}).get("imdb_id") or None,
        "tmdb_episode_run_time": runtime or None, "tmdb_trailer_key": extract_trailer_key(detail),
        "tmdb_cast": cast, "tmdb_creators": [c.get("name") for c in (detail.get("created_by") or []) if c.get("name")] or None,
        "tmdb_recommendations": extract_recommendations(detail, "TV"),
        "physical_collection": True, "physical_spine_title": seed_item["spine_title"],
        "physical_owned_seasons": len(physical_only_seasons),
        "physical_owned_seasons_total": owned_seasons,
    }


def build_manual_show(item):
    """A real disc with no TMDB entry at all - kept visible with a
    non-TMDB cover image (or none) rather than silently dropped."""
    title = item["spine_title"]
    entry_path = f"physical://manual/{title}"
    return {
        "title": title, "type": "Movie", "genre": "Uncategorized", "years": [],
        "episode_count": None, "version_count": 1,
        "roots": ["Physical"], "archive_categories": ["Physical"], "pipeline_stage": "Archived",
        "entries": [synthetic_entry(entry_path, title)],
        "latest_added": None, "total_size_bytes": 0, "wasted_bytes": 0,
        "duplicates": [], "missing_episodes": [], "missing_seasons": [],
        "episodes_expected_so_far": None, "assumed_physical_media": False,
        "completeness_skip_reason": None, "runtime_mismatch": False, "file_runtime_minutes": None,
        "tmdb_matched": False, "tmdb_genres": None, "tmdb_id": None, "tmdb_name": None,
        "tmdb_overview": item.get("note", ""), "tmdb_poster": None, "tmdb_backdrop": None,
        "tmdb_vote": None, "tmdb_recommendations": None,
        "physical_collection": True, "physical_spine_title": title,
        "physical_manual_cover_url": item.get("cover_image_url"),
    }


def main():
    with open(SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)

    movie_genre_map = {g["id"]: g["name"] for g in (api_get("/genre/movie/list", {}) or {}).get("genres", [])}
    tv_genre_map = {g["id"]: g["name"] for g in (api_get("/genre/tv/list", {}) or {}).get("genres", [])}
    digital_tv_seasons = load_digital_tv_seasons()
    cache = load_cache()

    shows = []
    fetched, failed, skipped_full_overlap = 0, 0, 0

    for item in seed.get("movies", []):
        print(f"movie: {item['spine_title']} (tmdb {item['tmdb_id']})")
        show = build_movie_show(item, movie_genre_map, cache)
        if show:
            shows.append(show)
            fetched += 1
            cache_show_images(show)
        else:
            failed += 1

    for item in seed.get("tv", []):
        print(f"tv: {item['spine_title']} (tmdb {item['tmdb_id']}, {item['owned_seasons']} owned seasons)")
        show = build_tv_show(item, tv_genre_map, digital_tv_seasons, cache)
        if show:
            shows.append(show)
            fetched += 1
            cache_show_images(show)
        elif item["tmdb_id"] in digital_tv_seasons:
            skipped_full_overlap += 1
        else:
            failed += 1

    for item in seed.get("manual_no_tmdb_match", []):
        print(f"manual (no TMDB): {item['spine_title']}")
        shows.append(build_manual_show(item))

    shows.sort(key=lambda s: s["title"].lower())

    # Atomic write - see pipeline/scan_media.py's own copy of this comment
    # (2026-08-21): web_server.py reads this file on every page load, so a
    # direct write here can hand a concurrent reader a torn/truncated file.
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2, ensure_ascii=False)
    atomic_replace(tmp, OUT_PATH)
    save_cache(cache)

    print(f"\nWrote {len(shows)} physical entries to {OUT_PATH}")
    print(f"TMDB-fetched: {fetched}, failed: {failed}, "
          f"skipped (every owned season already digital): {skipped_full_overlap}, "
          f"manual/no-match: {len(seed.get('manual_no_tmdb_match', []))}")


if __name__ == "__main__":
    main()

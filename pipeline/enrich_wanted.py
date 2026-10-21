"""Injects "wanted" shows/movies into media_db.json - titles the user
owns nothing of but wants, so they flow through the existing
missing-episode contract the companion tool already reads (a hand-off
from a discovery tool, plus a plain manual "shows I want" list).

Reads data\\wanted_seed.json:

    {"shows": [
      {"tmdb_id": 1437, "type": "tv",
       "want": "all" | [1, 2],          # whole series, or these seasons
       "requested_by": "you" | "partner" | null,
       "added_at": "2026-09-09T..."}
    ]}

For each entry NOT already present in media_db.json (owned something),
fetch TMDB detail once (cached forever in data\\tmdb_wanted_cache.json)
and append a synthetic show record: entries[] empty, wanted: true, and
- for TV - every aired episode of the wanted seasons written into
missing_seasons[] in the exact shape enrich_tv_coverage.py produces.
An already-owned entry is silently skipped (satisfied request), never
mutating the seed file.

Runs AFTER enrich_tv_coverage.py / detect_runtime_mismatch.py (both reset
and recompute missing_* for real shows and skip zero-owned ones), BEFORE
the plan builders. Also in automation\\daily_refresh.ps1's $Scripts.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from atomic_write import atomic_replace
from cached_cast_lookup import cached_cast_crew
from missing_episode_grace import missing_episode_cutoff
from tmdb_image_cache import cache_show_images

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from app_config import DATA_DIR  # noqa: E402
SEED_PATH = os.path.join(DATA_DIR, "wanted_seed.json")
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
CACHE_PATH = os.path.join(DATA_DIR, "tmdb_wanted_cache.json")

from app_config import tmdb_api_key  # noqa: E402
API_KEY = tmdb_api_key()

from app_config import TMDB_API_BASE as BASE  # noqa: E402
# Same missing-episode grace period enrich_tv_coverage.py/track_purged_shows.py
# read (2026-09-14) - keeps a wanted show's missing_seasons contract on the
# same "aired more than N days ago" freshness window as every other source
# of missing-episode data the companion tool reads, instead of flagging an episode
# the moment it airs.
MISSING_EPISODE_CUTOFF = missing_episode_cutoff()


def api_get(path, params=None):
    p = dict(params or {})
    p["api_key"] = API_KEY
    url = f"{BASE}{path}?{urllib.parse.urlencode(p)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f"  TMDB {e.code} on {path}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError, ValueError):
            time.sleep(1.0)
    return None


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _cache_get(cache, key, fetch):
    if key in cache:
        return cache[key]
    val = fetch()
    if val is not None:
        cache[key] = val
    return val


def build_tv(entry, detail, cache):
    tmdb_id = entry["tmdb_id"]
    want = entry.get("want", "all")
    seasons = detail.get("seasons") or []
    season_nums = sorted(
        s["season_number"] for s in seasons
        if s.get("season_number") and s["season_number"] > 0
    )
    if isinstance(want, list):
        season_nums = [n for n in season_nums if n in want]

    missing_seasons = []
    for n in season_nums:
        sd = _cache_get(cache, f"season:{tmdb_id}:{n}",
                        lambda n=n: api_get(f"/tv/{tmdb_id}/season/{n}"))
        if not sd:
            continue
        aired = sorted(
            ({"episode": e["episode_number"], "name": e.get("name"),
              "air_date": e.get("air_date")}
             for e in sd.get("episodes", [])
             if e.get("air_date") and e["air_date"] <= MISSING_EPISODE_CUTOFF),
            key=lambda e: e["episode"],
        )
        if aired:
            missing_seasons.append({
                "season": n, "aired_episode_count": len(aired),
                "total_episode_count": len(sd.get("episodes", [])),
                "first_air_date": aired[0]["air_date"],
                "episodes": aired,
                "covered_physically": False, "covered_by_amazon": False,
            })
    genres = [g["name"] for g in detail.get("genres", [])]
    return {
        "title": detail.get("name") or entry.get("title") or f"tmdb {tmdb_id}",
        "type": "TV", "entries": [],
        "tmdb_matched": True, "tmdb_id": tmdb_id,
        "tmdb_name": detail.get("name"),
        "tmdb_status": detail.get("status"),
        "tmdb_genres": genres,
        "tmdb_poster": detail.get("poster_path"),
        "tmdb_backdrop": detail.get("backdrop_path"),
        "tmdb_overview": detail.get("overview"),
        "tmdb_number_of_seasons": detail.get("number_of_seasons"),
        # int, not the raw string slice - same real crash + same fix as
        # pipeline/track_purged_shows.py's own build_tv() (2026-09-13),
        # confirmed there against a real purged_tracked record; fixed here
        # too since this build_tv() has the exact identical bug and would
        # hit it the same way the moment wanted_seed.json isn't empty.
        "years": [int(detail["first_air_date"][:4])] if detail.get("first_air_date") else [],
        "wanted": True,
        "wanted_want": want,
        "wanted_requested_by": entry.get("requested_by"),
        "wanted_added_at": entry.get("added_at"),
        "missing_episodes": [],
        "missing_seasons": missing_seasons,
        "episode_count": 0,
        "version_count": 0,
        # Never goes through scan_media.py (the only script that normally
        # sets this) - without it, render_engine.py's Dashboard stage-count
        # loop would crash the entire page the first time a wanted record
        # actually existed (same latent bug confirmed real 2026-09-13 for
        # track_purged_shows.py's synthetic records - this one's never been
        # exercised live since wanted_seed.json has stayed empty so far).
        "pipeline_stage": "Not Owned",
    }


def build_movie(entry, detail):
    tmdb_id = entry["tmdb_id"]
    genres = [g["name"] for g in detail.get("genres", [])]
    return {
        "title": detail.get("title") or entry.get("title") or f"tmdb {tmdb_id}",
        "type": "Movie", "entries": [],
        "tmdb_matched": True, "tmdb_id": tmdb_id,
        "tmdb_name": detail.get("title"),
        "tmdb_status": detail.get("status"),
        "tmdb_genres": genres,
        "tmdb_poster": detail.get("poster_path"),
        "tmdb_backdrop": detail.get("backdrop_path"),
        "tmdb_overview": detail.get("overview"),
        "years": [int(detail["release_date"][:4])] if detail.get("release_date") else [],  # int - see build_tv()'s own comment above
        "wanted": True,
        "wanted_requested_by": entry.get("requested_by"),
        "wanted_added_at": entry.get("added_at"),
        # movies don't use the missing_episodes contract - a wanted movie
        # is visible in the app and to other tools, but companion-tool acquisition
        # of wanted movies is a separate, not-yet-designed hand-off.
        "missing_episodes": [], "missing_seasons": [],
        "episode_count": 0, "version_count": 0,
        "pipeline_stage": "Not Owned",  # see build_tv()'s own comment above
    }


def main():
    seed = _load(SEED_PATH, {"shows": []})
    wanted = seed.get("shows") or []
    if not wanted:
        print("wanted_seed.json empty - nothing to inject.")
        return
    if not API_KEY:
        print("No TMDB API key - wanted shows not injected.", file=sys.stderr)
        return

    shows = _load(DB_PATH, None)
    if not isinstance(shows, list):
        print("media_db.json missing/unreadable - wanted shows not injected.", file=sys.stderr)
        return
    # Drop any wanted records from a previous run first, so this is
    # idempotent whether it runs standalone (after an /api/wanted/add) or
    # as part of a full pipeline - the seed file is the single source of
    # truth for what's wanted, re-injected fresh every time.
    dropped = sum(1 for s in shows if s.get("wanted"))
    shows = [s for s in shows if not s.get("wanted")]
    owned = {(s.get("tmdb_id"), s.get("type")) for s in shows if s.get("tmdb_id")}
    cache = _load(CACHE_PATH, {})

    injected, skipped_owned, failed = 0, 0, 0
    for entry in wanted:
        tmdb_id = entry.get("tmdb_id")
        kind = (entry.get("type") or "tv").lower()
        typ = "Movie" if kind in ("movie", "film") else "TV"
        if not tmdb_id:
            continue
        if (tmdb_id, typ) in owned:
            skipped_owned += 1
            continue
        ep = "movie" if typ == "Movie" else "tv"
        detail = _cache_get(cache, f"detail:{ep}:{tmdb_id}",
                            lambda: api_get(f"/{ep}/{tmdb_id}"))
        if not detail:
            failed += 1
            continue
        rec = build_movie(entry, detail) if typ == "Movie" else build_tv(entry, detail, cache)
        rec["show_key"] = f"tmdb:{typ}:{tmdb_id}"
        # Reuses enrich_tmdb.py's already-cached cast/crew for this exact
        # title/type if it was ever matched before (previously owned, then
        # removed, then re-added to Wanted) - no second TMDB call. A title
        # never owned before is a genuine miss and just stays cast-less,
        # same as before this existed (2026-09-20).
        cast, director, creators = cached_cast_crew(typ, tmdb_id)
        if cast:
            rec["tmdb_cast"] = cast
            rec["tmdb_director"] = director
            rec["tmdb_creators"] = creators
        # Same proactive caching as track_purged_shows.py's identical fix
        # (2026-09-13, "every poster should be cached") - a wanted show's
        # poster shouldn't depend on someone happening to load a page that
        # displays it before it's guaranteed local. Now also covers cast
        # photos, once the reuse above actually populates tmdb_cast.
        cache_show_images(rec)
        shows.append(rec)
        owned.add((tmdb_id, typ))
        injected += 1

    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=True)
    atomic_replace(tmp, CACHE_PATH)

    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, ensure_ascii=True)
    atomic_replace(tmp, DB_PATH)

    print(f"Wanted: {injected} injected ({dropped} stale wanted record(s) cleared first), "
          f"{skipped_owned} now owned (skipped), {failed} TMDB lookup failure(s).")


if __name__ == "__main__":
    main()

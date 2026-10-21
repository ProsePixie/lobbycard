"""Keeps a "delete after watching" show/movie trackable in media_db.json
even after every local file of it has been purged - so it doesn't silently
vanish from Recently Watched, Continue Watching, or the companion tool's missing-
episode sweep just because nothing of it is currently on disk.

The problem this fixes: a show set to retention "delete_after_watch" gets
its files moved to Pending Deletion once watched (app/render_engine.py's
ready_to_delete), and once every file is gone, scan_media.py has nothing to
find - the show has NO record in media_db.json at all. That means:
  - prune_state() (app/state_store.py) drops its `recently_watched`,
    `retention` and `top10`/`no_interest` entries the very next refresh,
    since its show_key is no longer in known_show_keys - watch history for
    something you deliberately don't keep files for was getting erased.
  - The Recently Watched / Continue Watching panels have no show object left
    to render, even though the key might still be in recently_watched for
    one more cycle.
  - enrich_tv_coverage.py never runs on it again (no entry to enrich), so
    missing_episodes/missing_seasons - the exact contract the companion tool reads
    to know what to fetch next - stops being computed. A new season airing
    after the old one was watched-and-purged would never be noticed.

Fix: for every user_state.json retention key shaped tmdb:TV:<id> or
tmdb:Movie:<id> with value "delete_after_watch" that currently owns NOTHING
digital, inject a synthetic zero-owned record - same shape
enrich_wanted.py already uses for an explicitly wanted show, but marked
`purged_tracked: True` instead of `wanted: True` so it reads as "an
existing library show the app is still following," not "the user asked to
acquire something new" (companion tools shouldn't treat it as a fresh
request). TV gets every aired episode across every season written into
missing_seasons (same shape enrich_tv_coverage.py produces) plus
tmdb_next_episode, so the "Airs <date>" badge and the companion tool's normal
missing-episode sweep both keep working with zero code changes on their
side. Movies get the plain synthetic record (no missing-episode contract -
same as a wanted movie).

Fully idempotent: every run drops its own prior purged_tracked records and
rebuilds from the current retention dict, so a show that becomes owned
again (re-downloaded) or has its retention changed away from
delete_after_watch simply stops being reinjected.

Runs AFTER enrich_wanted.py (so "wanted" and "purged_tracked" don't fight
over the same tmdb_id) and BEFORE the plan builders / prune_state's final
call in run_refresh_pipeline() - see app/web_server.py's PIPELINE_SCRIPTS.
Also in automation\\daily_refresh.ps1's $Scripts.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from atomic_write import atomic_replace
from cached_cast_lookup import cached_cast_crew
from missing_episode_grace import missing_episode_cutoff
from tmdb_image_cache import cache_show_images

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
STATE_PATH = os.path.join(DATA_DIR, "user_state.json")
CACHE_PATH = os.path.join(DATA_DIR, "tmdb_purge_tracking_cache.json")
# show_key -> the date this show FIRST went zero-owned (purged_tracked).
# Same bounded-tracking cache shape as enrich_tv_coverage.py's own
# genre_tracking_start.json - see build_tv()'s own comment on why this
# exists and app/state_store.py's purged_backlog_tracking for the opt-out.
TRACKING_START_CACHE_PATH = os.path.join(DATA_DIR, "purged_tracking_start.json")

from app_config import tmdb_api_key  # noqa: E402
API_KEY = tmdb_api_key()

from app_config import TMDB_API_BASE as BASE  # noqa: E402
# Same missing-episode grace period enrich_tv_coverage.py reads (2026-09-14)
# - without this, a purged_tracked (zero-owned) show flagged an episode as
# "aired" the instant it aired, while the exact same show with real owned
# files still waited out the grace period there - a still-airing show
# purged after its last file gets an inconsistent completeness signal
# depending purely on whether anything's currently on disk.
MISSING_EPISODE_CUTOFF = missing_episode_cutoff()
# A companion tool's own "never search this again" decisions, read-only, for
# delete_after_watch shows only (its own scoping - see the file's own
# comment). A
# purged_tracked placeholder computes missing_seasons independently of
# enrich_tv_coverage.py (real confirmed gap: Kitchen Nightmares S03E09/E10
# kept showing missing here even after the user downloaded, watched,
# deleted AND explicitly clicked "Skip" on both in the companion tool's own
# console - nothing on this side ever learned about that). Same posture as
# every other cross-app signal file this pipeline reads: read-only, never
# imported code, a missing/malformed file just means nothing gets
# suppressed (fails open to "still show it," never silently hides a real
# gap because the companion tool's own file happened to be absent).
from app_config import companion_file  # noqa: E402
COMPANION_SKIPPED_EPISODES_PATH = companion_file("companion_skipped_episodes.json")


def _load_companion_skipped_episodes():
    data = _load(COMPANION_SKIPPED_EPISODES_PATH, {})
    out = set()
    for e in data.get("skipped") or []:
        try:
            out.add((int(e["tmdb_id"]), int(e["season"]), int(e["episode"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


# tmdb:TV:1234 / tmdb:Movie:1234 - a real digital identity's plain show_key
# (state_store.show_key()). Deliberately excludes the :physical/:amazon/
# :netflix/:prime suffixed keys - those sources were never a local file
# Purge could have removed in the first place.
PLAIN_KEY_RE = re.compile(r"^tmdb:(TV|Movie):(\d+)$")


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


COMPANION_SKIPPED_EPISODES = _load_companion_skipped_episodes()


def load_tracking_starts():
    return _load(TRACKING_START_CACHE_PATH, {})


def save_tracking_starts(tracking_starts):
    tmp = TRACKING_START_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tracking_starts, f, indent=2, sort_keys=True)
    atomic_replace(tmp, TRACKING_START_CACHE_PATH)


def _cache_get(cache, key, fetch):
    if key in cache:
        return cache[key]
    val = fetch()
    if val is not None:
        cache[key] = val
    return val


def _next_episode(detail):
    ne = detail.get("next_episode_to_air")
    if not ne or not ne.get("air_date"):
        return None
    return {
        "season": ne.get("season_number"), "episode": ne.get("episode_number"),
        "name": ne.get("name"), "air_date": ne.get("air_date"),
    }


def build_tv(tmdb_id, detail, cache, min_air_date=None):
    """min_air_date (2026-09-17, correcting a wrong default): delete_after_
    watch means "watch once, then it's gone on purpose" - an episode that
    aired before this show first went zero-owned was already watched and
    deliberately discarded, not a backlog to re-chase. None (the show has
    opted into full-backlog tracking - see purged_backlog_tracking in
    app/state_store.py) means the old unbounded behavior, same as before
    this date. Same bounded-tracking shape enrich_tv_coverage.py already
    uses for genre-excluded shows (tracking_start_date/min_air_date there)."""
    seasons = detail.get("seasons") or []
    season_nums = sorted(s["season_number"] for s in seasons if s.get("season_number") and s["season_number"] > 0)

    missing_seasons = []
    for n in season_nums:
        if n == season_nums[-1]:
            # Latest season is still gaining episodes - never trust a
            # cached copy of it (same freeze as the detail fetch).
            sd = api_get(f"/tv/{tmdb_id}/season/{n}") or cache.get(f"season:{tmdb_id}:{n}")
            if sd:
                cache[f"season:{tmdb_id}:{n}"] = sd
        else:
            sd = _cache_get(cache, f"season:{tmdb_id}:{n}", lambda n=n: api_get(f"/tv/{tmdb_id}/season/{n}"))
        if not sd:
            continue
        aired = sorted(
            ({"episode": e["episode_number"], "name": e.get("name"), "air_date": e.get("air_date")}
             for e in sd.get("episodes", [])
             if e.get("air_date") and e["air_date"] <= MISSING_EPISODE_CUTOFF
             and (not min_air_date or e["air_date"] >= min_air_date)
             and (tmdb_id, n, e["episode_number"]) not in COMPANION_SKIPPED_EPISODES),
            key=lambda e: e["episode"],
        )
        if aired:
            missing_seasons.append({
                "season": n, "aired_episode_count": len(aired),
                "total_episode_count": len(sd.get("episodes", [])),
                "first_air_date": aired[0]["air_date"], "episodes": aired,
                "covered_physically": False, "covered_by_amazon": False,
            })
    genres = [g["name"] for g in detail.get("genres", [])]
    return {
        "title": detail.get("name") or f"tmdb {tmdb_id}",
        "type": "TV", "entries": [],
        "tmdb_matched": True, "tmdb_id": tmdb_id,
        "tmdb_name": detail.get("name"),
        "tmdb_status": detail.get("status"),
        "tmdb_genres": genres,
        "tmdb_poster": detail.get("poster_path"),
        "tmdb_backdrop": detail.get("backdrop_path"),
        "tmdb_overview": detail.get("overview"),
        "tmdb_number_of_seasons": detail.get("number_of_seasons"),
        "tmdb_next_episode": _next_episode(detail),
        # int, not the raw string slice - scan_media.py's own real parsing
        # (extract_year()) always stores years as int, and enrich_tmdb.py's
        # best_match() does real arithmetic on this value (year - r_year) -
        # a string here crashes the ENTIRE pipeline the next time this
        # show needs a fresh TMDB re-search (confirmed real 2026-09-13,
        # the first time a purged_tracked record ever actually existed AND
        # was "ongoing" enough to force one).
        "years": [int(detail["first_air_date"][:4])] if detail.get("first_air_date") else [],
        "purged_tracked": True,
        "tracking_start_date": min_air_date,
        "missing_episodes": [],
        "missing_seasons": missing_seasons,
        "episode_count": 0,
        "version_count": 0,
        # Never goes through scan_media.py (the only script that normally
        # sets this) - without it, render_engine.py's Dashboard stage-count
        # loop crashed the entire page the first time a purged_tracked
        # record actually existed (2026-09-13, confirmed real). Deliberately
        # its own distinct value, not "Archived" (implies real files) or
        # "In Queue" (implies something's downloading) - neither is true
        # for a zero-owned placeholder.
        "pipeline_stage": "Not Owned",
    }


def build_movie(tmdb_id, detail):
    genres = [g["name"] for g in detail.get("genres", [])]
    return {
        "title": detail.get("title") or f"tmdb {tmdb_id}",
        "type": "Movie", "entries": [],
        "tmdb_matched": True, "tmdb_id": tmdb_id,
        "tmdb_name": detail.get("title"),
        "tmdb_status": detail.get("status"),
        "tmdb_genres": genres,
        "tmdb_poster": detail.get("poster_path"),
        "tmdb_backdrop": detail.get("backdrop_path"),
        "tmdb_overview": detail.get("overview"),
        "years": [int(detail["release_date"][:4])] if detail.get("release_date") else [],  # int - see build_tv()'s own comment above
        "purged_tracked": True,
        "missing_episodes": [], "missing_seasons": [],
        "episode_count": 0, "version_count": 0,
        "pipeline_stage": "Not Owned",  # see build_tv()'s own comment above
    }


def main():
    state = _load(STATE_PATH, {})
    retention = state.get("retention") or {}
    candidates = [
        (m.group(1), int(m.group(2)))
        for key, policy in retention.items()
        if policy == "delete_after_watch" and (m := PLAIN_KEY_RE.match(key))
    ]
    if not candidates:
        print("track_purged_shows: no delete_after_watch shows in retention - nothing to do.")
        return
    if not API_KEY:
        print("No TMDB API key - purged-show tracking skipped.", file=sys.stderr)
        return

    shows = _load(DB_PATH, None)
    if not isinstance(shows, list):
        print("media_db.json missing/unreadable - purged-show tracking skipped.", file=sys.stderr)
        return
    # Idempotent, same convention as enrich_wanted.py: drop this script's own
    # prior output and rebuild fresh from the current retention dict every
    # run, so a re-downloaded or un-delete_after_watch'd show just stops
    # being reinjected next time, no separate cleanup step needed.
    dropped = sum(1 for s in shows if s.get("purged_tracked"))
    shows = [s for s in shows if not s.get("purged_tracked")]
    owned = {(s.get("tmdb_id"), s.get("type")) for s in shows if s.get("tmdb_id")}
    cache = _load(CACHE_PATH, {})
    # Per-show opt-IN to unbounded (full-backlog) tracking - see build_tv()'s
    # own comment and app/state_store.py's purged_backlog_tracking.
    backlog_opt_in = state.get("purged_backlog_tracking") or {}
    tracking_starts = load_tracking_starts()
    today = date.today().isoformat()

    injected, skipped_owned, failed, backlog_tracked = 0, 0, 0, 0
    for typ, tmdb_id in candidates:
        if (tmdb_id, typ) in owned:
            skipped_owned += 1  # re-downloaded / still has something local - normal coverage handles it
            continue
        key = f"tmdb:{typ}:{tmdb_id}"
        ep = "movie" if typ == "Movie" else "tv"
        # Always fresh (2026-09-25): a permanently-cached detail froze
        # next_episode_to_air forever - Deadliest Catch stuck on S22E18
        # after it aired, so its "Airs" badge and the companion tool's tracking
        # never moved on. Falls back to the cached copy only if TMDB fails.
        detail = api_get(f"/{ep}/{tmdb_id}")
        if detail:
            cache[f"detail:{ep}:{tmdb_id}"] = detail
        else:
            detail = cache.get(f"detail:{ep}:{tmdb_id}")
        if not detail:
            failed += 1
            continue
        if typ == "Movie":
            rec = build_movie(tmdb_id, detail)
        elif backlog_opt_in.get(key):
            rec = build_tv(tmdb_id, detail, cache)  # min_air_date=None - this show opted into the full backlog
            backlog_tracked += 1
        else:
            # First time this show is seen zero-owned, remember today as its
            # tracking start forever after - never recomputed once set, so
            # the bound doesn't creep forward on every later run.
            start = tracking_starts.get(key)
            if start is None:
                start = today
                tracking_starts[key] = start
            rec = build_tv(tmdb_id, detail, cache, min_air_date=start)
        rec["show_key"] = key
        # Cast/crew is never fetched fresh for a purged_tracked record (no
        # credits/aggregate_credits requested here - deliberately kept
        # cheap), but enrich_tmdb.py almost certainly already has it cached
        # from when this show was still owned - owning it once is how it
        # became purged_tracked in the first place. Reuse that instead of a
        # second TMDB call for data that's already on disk.
        cast, director, creators = cached_cast_crew(typ, tmdb_id)
        if cast:
            rec["tmdb_cast"] = cast
            rec["tmdb_director"] = director
            rec["tmdb_creators"] = creators
        # Same proactive caching enrich_tmdb.py does for real, still-owned
        # matches - without this, a purged_tracked show's poster only ever
        # got cached lazily, the next time someone actually loaded a page
        # showing it (web_server.py's /tmdb-image/ self-heal). That's the
        # normal case for something visibly sitting in Recently Watched,
        # but leaves it to chance rather than guaranteed (2026-09-13, "every
        # poster should be cached"). Now also covers cast photos, once the
        # reuse above actually populates tmdb_cast (2026-09-20).
        cache_show_images(rec)
        shows.append(rec)
        owned.add((tmdb_id, typ))
        injected += 1

    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=True)
    atomic_replace(tmp, CACHE_PATH)

    save_tracking_starts(tracking_starts)

    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, ensure_ascii=True)
    atomic_replace(tmp, DB_PATH)

    print(f"Purged-show tracking: {injected} tracked ({dropped} stale record(s) cleared first), "
          f"{skipped_owned} now owned again (skipped), {failed} TMDB lookup failure(s), "
          f"{backlog_tracked} opted into full-backlog tracking (see purged_backlog_tracking).")


if __name__ == "__main__":
    main()

"""Builds data\\streaming_db.json from data\\streaming_follows_seed.json -
streaming titles the user "follows" so the app shows them in the
library as if owned (a live subscription is a live licence). Same model as
enrich_amazon.py: synthetic no-real-file entries, merged into the library
by web_server.py's load_shows(), watched/watch-list state via the normal
per-episode machinery.

Normally the seed is appended to by an optional companion tool (draining
its streaming_follow_requests.json hand-off file, see companion_data_dir in
CONFIG.md); without one the seed is hand-maintained, exactly the way
amazon_purchases_seed.json works.

Per follow:
  - full TMDB episode list per season (each episode individually watchable),
    like enrich_physical.py's per-episode entries;
  - is_streaming: true, streaming_provider, streaming_link, root
    "Netflix"/"Prime"; tmdb_matched true, real tmdb_id/type, NOT wanted
    (so Lighthouse counts it as owned and stops re-suggesting);
  - seasons/movie already owned digitally are suppressed (overlap, same as
    enrich_amazon.py via load_digital_tv_seasons());
  - current availability re-checked against
    the companion tool\\data\\netflix_prime_availability.json: flipped to "no"
    -> streaming_lapsed: true (card shows a "no longer on X" badge + a
    release prompt, NOT auto-removed); "soon" -> streaming_arriving: <date>.

In PIPELINE_SCRIPTS and QUICK_SCAN_SCRIPTS (after consolidate.py) -
TMDB fetches are cached forever (data\\tmdb_streaming_cache.json), so a
re-run with an unchanged seed is a handful of file reads.
"""
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from enrich_physical import (  # noqa: E402
    api_get, extract_cast, extract_recommendations, extract_trailer_key,
    fetch_detail, fetch_season_episodes, load_digital_tv_seasons, synthetic_entry,
)
from atomic_write import atomic_replace  # noqa: E402
from added_at import local_naive_iso  # noqa: E402
import step_progress  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from app_config import DATA_DIR  # noqa: E402
SEED_PATH = os.path.join(DATA_DIR, "streaming_follows_seed.json")
OUT_PATH = os.path.join(DATA_DIR, "streaming_db.json")
DIGITAL_DB_PATH = os.path.join(DATA_DIR, "media_db.json")
CACHE_PATH = os.path.join(DATA_DIR, "tmdb_streaming_cache.json")
# The companion tool's files - best-effort sibling paths, never required.
from app_config import AMAZON_DOMAIN, companion_file  # noqa: E402
AVAIL_PATH = companion_file("netflix_prime_availability.json")
# Append-only follow requests written by a companion tool's "add to library"
# button (file hand-off, no cross-app token).
HANDOFF_PATH = companion_file("streaming_follow_requests.json")
WATERMARK_PATH = os.path.join(DATA_DIR, "streaming_ingest_watermark.json")

PROVIDERS = ("netflix", "prime")
PROVIDER_LABEL = {"netflix": "Netflix", "prime": "Prime Video"}
PROVIDER_ROOT = {"netflix": "Netflix", "prime": "Prime"}


def _cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    atomic_replace(tmp, CACHE_PATH)


def _search_link(provider, title, season=None):
    """Provider search URL - used when the seed carries no real deep_link.
    Netflix has no reliable title-specific link without a logged-in session
    (rejected 2026-09-11); Prime's real /gp/video/detail/<asin> link comes
    from the companion tool when it ships, and lands in the seed's deep_link."""
    q = urllib.parse.quote(f"{title} Season {season}" if season else title)
    if provider == "netflix":
        return f"https://www.netflix.com/search?q={q}"
    return f"https://www.{AMAZON_DOMAIN}/s?k={q}&i=instant-video"


def _load_availability():
    if not os.path.exists(AVAIL_PATH):
        return {}
    try:
        with open(AVAIL_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _drain_handoff():
    """Merge new records from the companion tool's append-only
    streaming_follow_requests.json into streaming_follows_seed.json.
    Watermarked by requested_at so re-processing an unchanged handoff file
    is a no-op, and a title the user has since released only comes back if
    the companion tool writes a *fresh* request (newer requested_at) for it -
    which is the intended "re-follow" path. Best-effort: a missing or
    malformed handoff file just means nothing new to ingest."""
    if not os.path.exists(HANDOFF_PATH):
        return 0
    try:
        with open(HANDOFF_PATH, encoding="utf-8") as f:
            handoff = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0
    requests = handoff.get("requests") or []
    if not requests:
        return 0

    watermark = ""
    if os.path.exists(WATERMARK_PATH):
        try:
            with open(WATERMARK_PATH, encoding="utf-8") as f:
                watermark = (json.load(f) or {}).get("last_ingested_at", "")
        except (json.JSONDecodeError, OSError):
            watermark = ""

    seed = {"shows": []}
    if os.path.exists(SEED_PATH):
        try:
            with open(SEED_PATH, encoding="utf-8") as f:
                seed = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    seed.setdefault("shows", [])
    # Index by identity so a request for something already in the seed
    # UPDATES it in place (a hand-seeded deep_link:null entry getting the
    # real one the companion tool resolved later, e.g.) instead of being silently
    # dropped forever just because the identity already existed.
    by_key = {(e.get("tmdb_id"), (e.get("type") or "tv").lower()): e for e in seed["shows"]}

    added, updated, newest = 0, 0, watermark
    for r in requests:
        req_at = r.get("requested_at") or ""
        if req_at and req_at <= watermark:
            continue
        tmdb_id = r.get("tmdb_id")
        typ = (r.get("type") or "tv").lower()
        typ = "movie" if typ == "movie" else "tv"
        provider = (r.get("provider") or "").lower()
        if not isinstance(tmdb_id, int) or provider not in PROVIDERS:
            continue
        # Only 'high'-confidence availability is followable (design decision
        # 10). A record with no confidence field passes for now - the companion tool
        # is adding that field; once it does, 'inferred' will be rejected here.
        if r.get("confidence") == "inferred":
            continue
        if req_at > newest:
            newest = req_at
        key = (tmdb_id, typ)
        existing = by_key.get(key)
        if existing:
            new_deep_link = r.get("deep_link") or None
            if new_deep_link and new_deep_link != existing.get("deep_link"):
                existing["deep_link"] = new_deep_link
                existing["provider"] = provider
                existing["added_at"] = req_at or existing.get("added_at")
                updated += 1
            continue
        entry = {
            "tmdb_id": tmdb_id, "type": typ, "provider": provider,
            "title": r.get("title") or None,
            "deep_link": r.get("deep_link") or None,
            "added_at": req_at or None,
        }
        seed["shows"].append(entry)
        by_key[key] = entry
        added += 1

    if newest != watermark:
        tmp = SEED_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(seed, f, indent=2, ensure_ascii=False)
        atomic_replace(tmp, SEED_PATH)
        wtmp = WATERMARK_PATH + ".tmp"
        with open(wtmp, "w", encoding="utf-8") as f:
            json.dump({"last_ingested_at": newest}, f)
        atomic_replace(wtmp, WATERMARK_PATH)
    if added or updated:
        print(f"enrich_streaming: ingested {added} new + {updated} updated follow request(s) from the companion tool.")
    return added + updated


def _load_owned_movies():
    if not os.path.exists(DIGITAL_DB_PATH):
        return set()
    with open(DIGITAL_DB_PATH, encoding="utf-8") as f:
        digital = json.load(f)
    return {s["tmdb_id"] for s in digital if s.get("type") == "Movie" and s.get("tmdb_matched") and s.get("tmdb_id")}


def build_show(item, genre_map, cache, digital_tv_seasons, availability):
    tmdb_id = item["tmdb_id"]
    kind = "movie" if item.get("type", "tv").lower() == "movie" else "tv"
    provider = item.get("provider", "prime")
    root = PROVIDER_ROOT.get(provider, "Prime")
    plabel = PROVIDER_LABEL.get(provider, provider)

    detail = fetch_detail(cache, kind, tmdb_id)
    if not detail:
        print(f"  WARNING: {kind} id {tmdb_id} ({item.get('title', '?')}) failed to fetch, skipped", file=sys.stderr)
        return None

    show_type = "Movie" if kind == "movie" else "TV"
    name = detail.get("title") or detail.get("name") or item.get("title", "?")
    genre_ids = [g["id"] for g in (detail.get("genres") or [])]
    genres = [genre_map.get(gid) for gid in genre_ids if genre_map.get(gid)]
    date_str = detail.get("release_date") or detail.get("first_air_date") or ""
    year = int(date_str[:4]) if date_str[:4].isdigit() else None

    # availability re-check
    avail = availability.get(f"tmdb:{show_type}:{tmdb_id}") or {}
    state = avail.get(provider) if provider in PROVIDERS else None
    lapsed = state == "no"
    arriving = avail.get("arriving") if state == "soon" else None

    link = item.get("deep_link") or _search_link(provider, name)

    if kind == "movie":
        if tmdb_id in _OWNED_MOVIES:
            print(f"  SKIPPED: movie id {tmdb_id} ({name}) - already owned digitally", file=sys.stderr)
            return None
        entries = [synthetic_entry(f"stream://{provider}/movie/{tmdb_id}", name, root=root)]
        for e in entries:
            e["streaming_link"] = link
        episode_count, version_count = None, 1
        seasons_used = []
    else:
        n_seasons = detail.get("number_of_seasons") or 1
        already_digital = digital_tv_seasons.get(tmdb_id, set())
        seasons_used = [s for s in range(1, n_seasons + 1) if s not in already_digital]
        if not seasons_used:
            print(f"  SKIPPED: TV id {tmdb_id} ({name}) - every season already owned digitally", file=sys.stderr)
            return None
        entries = []
        for s in seasons_used:
            eps = fetch_season_episodes(cache, tmdb_id, s)
            if eps:
                for ep in eps:
                    en = ep["episode_number"]
                    e = synthetic_entry(
                        f"stream://{provider}/tv/{tmdb_id}/season/{s}/episode/{en}",
                        ep.get("name") or f"Episode {en}",
                        season=s, episode=en, air_date=ep.get("air_date"), root=root,
                    )
                    e["streaming_link"] = _search_link(provider, name, season=s) if not item.get("deep_link") else link
                    entries.append(e)
            else:
                e = synthetic_entry(
                    f"stream://{provider}/tv/{tmdb_id}/season/{s}", f"{name} - Season {s}",
                    season=s, episode=1, root=root,
                )
                e["streaming_link"] = link
                entries.append(e)
        episode_count, version_count = len(entries), len(seasons_used)

    show = {
        "title": name, "type": show_type, "genre": genres[0] if genres else "Uncategorized",
        "years": [year] if year else [],
        "episode_count": episode_count, "version_count": version_count,
        "roots": [root], "archive_categories": [root], "pipeline_stage": "Archived",
        "entries": entries,
        # The date the user followed it = "added to the library", so it shows
        # in What's New like a downloaded title would (it used to be None,
        # which What's New's `latest_added within 7 days` test can never pass).
        "latest_added": local_naive_iso(item.get("added_at")), "total_size_bytes": 0, "wasted_bytes": 0,
        "duplicates": [], "missing_episodes": [], "missing_seasons": [],
        "episodes_expected_so_far": None, "assumed_physical_media": False,
        "completeness_skip_reason": None, "runtime_mismatch": False, "file_runtime_minutes": None,
        "tmdb_matched": True, "tmdb_genres": genres or None, "tmdb_id": tmdb_id, "tmdb_name": name,
        "tmdb_overview": detail.get("overview", ""), "tmdb_poster": detail.get("poster_path"),
        "tmdb_backdrop": detail.get("backdrop_path"), "tmdb_vote": detail.get("vote_average"),
        "tmdb_release_date": date_str or None,
        "tmdb_cast": extract_cast(detail), "tmdb_trailer_key": extract_trailer_key(detail),
        "tmdb_recommendations": extract_recommendations(detail, show_type),
        # --- the streaming-follow markers ---
        "is_streaming": True,
        "streaming_provider": provider,
        "streaming_provider_label": plabel,
        "streaming_link": link,
        "streaming_added_at": item.get("added_at"),
        "streaming_lapsed": bool(lapsed),
        "streaming_arriving": arriving,
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
        show["tmdb_episode_run_time"] = (
            (detail.get("last_episode_to_air") or {}).get("runtime")
            or (detail.get("next_episode_to_air") or {}).get("runtime") or None
        )
    return show


_OWNED_MOVIES = set()


def main():
    _drain_handoff()  # pull in anything new the companion tool wrote
    if not os.path.exists(SEED_PATH):
        print("enrich_streaming: no streaming_follows_seed.json - nothing to do.")
        return
    with open(SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    follows = seed.get("shows", [])
    if not follows:
        # still write an empty db so a removed last follow clears the UI
        tmp = OUT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump([], fh)
        atomic_replace(tmp, OUT_PATH)
        print("enrich_streaming: seed empty - wrote empty streaming_db.json.")
        return

    global _OWNED_MOVIES
    _OWNED_MOVIES = _load_owned_movies()
    tv_genre_map = {g["id"]: g["name"] for g in (api_get("/genre/tv/list", {}) or {}).get("genres", [])}
    movie_genre_map = {g["id"]: g["name"] for g in (api_get("/genre/movie/list", {}) or {}).get("genres", [])}
    digital_tv_seasons = load_digital_tv_seasons()
    availability = _load_availability()
    cache = _cache()

    shows, built, skipped = [], 0, 0
    for n, item in enumerate(follows):
        step_progress.report(n + 1, len(follows), "followed titles", item.get("title"))
        gmap = movie_genre_map if item.get("type", "tv").lower() == "movie" else tv_genre_map
        show = build_show(item, gmap, cache, digital_tv_seasons, availability)
        if show:
            shows.append(show)
            built += 1
        else:
            # SKIPPED (owned) vs genuine failure - build_show already logged which
            skipped += 1
        time.sleep(0.05)

    shows.sort(key=lambda s: s["title"].lower())
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2, ensure_ascii=False)
    atomic_replace(tmp, OUT_PATH)
    _save_cache(cache)

    lapsed_n = sum(1 for s in shows if s.get("streaming_lapsed"))
    print(f"enrich_streaming: {built} followed streaming title(s) -> {OUT_PATH} "
          f"({skipped} skipped as owned/failed, {lapsed_n} flagged no-longer-streaming).")


if __name__ == "__main__":
    main()

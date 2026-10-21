"""Matches each show/movie in media_db.json against TMDB (search-then-detail),
caching results in data\\tmdb_cache.json (never re-queried once matched;
unmatched titles retry every 7 days - EXCEPT a still-airing TV show, which is
always re-fetched regardless of what's already cached, since tmdb_status/
tmdb_next_episode/tmdb_episode_run_time are snapshots that go stale the
moment a new episode airs; cached for good only once the show stops airing,
see ONGOING_STATUSES). Stores genres, poster/backdrop, release date, TV
status/type/season-count, real IMDB id (movies: native field, TV:
append_to_response=external_ids - same call, not an extra one), runtime
(movies: total; TV: last-aired-episode length), and - all via the same
append_to_response=videos,credits,recommendations piggybacked onto that one
detail call, never a separate request - a real trailer key, top cast,
director/creators, TMDB's own recommendations, and (TV only) the next
episode to air. Runs second in the refresh pipeline, right after
scan_media.py.

Also locally caches every matched show's poster/backdrop/cast/
recommendation images via tmdb_image_cache.py (data\\tmdb_images\\, same
"download once, store locally" precedent enrich_wikipedia.py already
established for wiki_images\\) - a cheap no-op stat() check for anything
already cached, so this stays fast on ordinary runs and only downloads
for genuinely new/re-matched titles."""
import json
import math
import os
import re
import sys
import time
import difflib
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

from tmdb_image_cache import cache_show_images
import fallback_identify as fi

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "app"))
from state_store import load_state, update_state  # noqa: E402
from atomic_write import atomic_replace
import step_progress
from split_by_year_titles import is_split_by_year_title
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
CACHE_PATH = os.path.join(DATA_DIR, "tmdb_cache.json")
PERSON_CACHE_PATH = os.path.join(DATA_DIR, "tmdb_person_cache.json")
RETRY_UNMATCHED_AFTER_DAYS = 7

from app_config import tmdb_api_key, TMDB_KEY_HELP  # noqa: E402
API_KEY = tmdb_api_key()
if not API_KEY and __name__ == "__main__":
    print(TMDB_KEY_HELP, file=sys.stderr)
    sys.exit(1)

from app_config import TMDB_API_BASE as BASE  # noqa: E402
SLEEP_BETWEEN_CALLS = 0.12
HASH_LIKE = re.compile(r"^[0-9a-f]{24,64}$", re.IGNORECASE)
# Same set enrich_tv_coverage.py uses to decide whether ITS season cache is
# worth re-fetching - a show's cached tmdb_status/tmdb_next_episode/
# tmdb_episode_run_time all go stale exactly the same way that script's
# per-season episode cache does, and enrich_tv_coverage.py's own "is this
# the currently-airing season" check depends on tmdb_status/tmdb_number_of_
# seasons staying fresh in the first place - one show-level check here
# keeps both caches honest instead of needing two separate staleness fixes.
ONGOING_STATUSES = {"Returning Series", "In Production"}


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
            if e.code == 401:
                print("TMDB API key rejected (401 Unauthorized). Check the key.", file=sys.stderr)
                sys.exit(1)
            return None
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1.0)
            continue
    return None


def normalize(title):
    t = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def similarity(a, b):
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def looks_like_hash(title):
    return bool(HASH_LIKE.match(title.replace(" ", "")))


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


def save_media_db(shows):
    """Same atomic pattern as save_cache(), called both at the periodic
    checkpoint below and at the very end of main(). Without a mid-run
    checkpoint, a crash/timeout anywhere in a long enrich pass discarded
    the WHOLE run's matches from media_db.json - even titles matched and
    safely cached minutes earlier - since shows[] used to only ever get
    written once, at the end. Confirmed real 2026-09-30: Coyote vs Acme
    and The Great British Bake Off S17E02 both matched and cached
    correctly, but media_db.json still showed them unmatched because
    whatever interrupted that run never reached the final save."""
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2)
    atomic_replace(tmp, DB_PATH)


def load_person_cache():
    if not os.path.exists(PERSON_CACHE_PATH):
        return {}
    try:
        with open(PERSON_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_person_cache(cache):
    tmp = PERSON_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    atomic_replace(tmp, PERSON_CACHE_PATH)


def backfill_imdb_ids(cast, person_cache):
    """Cast entries already carry a TMDB person id (extract_cast) - this
    adds each person's imdb_id, cached forever per person_id since it
    never changes, so a shared actor across many shows only costs one real
    API call total, not one per show they appear in."""
    for c in cast:
        person_id = c.get("id")
        if person_id is None:
            continue
        key = str(person_id)
        cached_person = person_cache.get(key)
        if cached_person is None:
            data = api_get(f"/person/{person_id}/external_ids", {})
            time.sleep(SLEEP_BETWEEN_CALLS)
            cached_person = {"imdb_id": (data or {}).get("imdb_id")}
            person_cache[key] = cached_person
        c["imdb_id"] = cached_person.get("imdb_id")


def override_lookup_key(show_type, title, cached):
    """Same identity show_key() (app/state_store.py) would compute for
    this show, from the one place that identity is actually knowable at
    this point in the pipeline: this show's OWN tmdb_id doesn't exist yet
    this run (that's what this whole script is about to determine) - the
    previous run's result, if any, lives in the match cache instead. A
    title never matched before falls back to the same title-based key
    show_key() itself falls back to for an unmatched show, so an override
    on a still-unmatched title still finds it once it exists."""
    if cached and cached.get("tmdb_matched") and cached.get("tmdb_id"):
        return f"tmdb:{show_type}:{cached['tmdb_id']}"
    return f"title:{show_type}:{title}"


def load_tmdb_id_overrides():
    """Corrections made through the app's Admin panel (see
    app/web_server.py's /api/override) - real-world example that started
    this: Doctor Who (2023), TMDB's own first_air_date for that show (id
    239770) is 2024-05-11, not 2023, despite the 60th-anniversary specials
    airing that year, so a year=2023-filtered search used to find "Doctor
    Who: Video Commentaries" (a bonus-content spinoff, id 240310) instead.
    That fix used to live as a hardcoded dict here - now it's just
    whatever's in user_state.json, so a future wrong match gets corrected
    through the UI, never a code edit. {override_lookup_key: tmdb_id}."""
    state = load_state()
    out = {}
    for key, override in state.get("overrides", {}).items():
        tmdb_id = override.get("tmdb_id_override")
        if tmdb_id:
            # media_type is optional - absent means "infer from the show's
            # current type", same as before this field existed. Needed when
            # the correct id lives on the OTHER endpoint than the show's
            # current type would imply (e.g. a single loose file with no
            # season markers always re-derives as "Movie" at scan time, but
            # the real TMDB entity is a TV movie/miniseries) - without it,
            # the override endpoint-selection below blindly calls
            # /movie/{id} and can silently attach a completely unrelated
            # movie that happens to share that numeric id (confirmed real:
            # tmdb tv id 19614 is "It" 1990, but movie id 19614 is an
            # unrelated Jay-Z concert film - forcing the movie endpoint
            # would have overwritten Stephen King's IT with that instead).
            #
            # media_type "tv_episode" is a third option: for a one-off
            # special that TMDB only catalogs as an episode of its long-
            # running flagship show, never as its own standalone entity
            # (confirmed real: "SNL50: The Anniversary Special" has no
            # movie or tv listing of its own - it's tmdb show 1667
            # "Saturday Night Live", season 50, episode 13). tmdb_id here
            # is the SHOW's id, with "season"/"episode" giving the specific
            # episode to pull instead of the whole series.
            out[key] = (tmdb_id, override.get("media_type"), override.get("season"), override.get("episode"))
    return out


def load_force_unmatched_titles():
    """Titles confirmed to have no real TMDB entry at all - personal/home-
    made content that happens to share a generic name with an unrelated
    real film (confirmed real: a home-recorded student film "collusion.mov"
    kept matching an unrelated 2003 British thriller called "Collusion").
    The regular override dict above only ever REPLACES a match with a
    different real id - there's no id to point at here, so this is a
    separate flag: {title:{type}:{title}: True}."""
    state = load_state()
    return {
        key for key, override in state.get("overrides", {}).items()
        if override.get("force_unmatched")
    }


def cache_key(title, year, original_type):
    # Year is part of the key, not just a search-time hint - without it,
    # two different real titles sharing an exact name (a remake/reboot,
    # e.g. "The Stand" 1994 vs 2020) collide on the same cache entry and
    # one silently steals the other's cached match. Confirmed real:
    # "the stand|TV" was serving the 1994 miniseries' id to the 2020
    # series once a folder-scan fix reclassified it from Movie to TV.
    return f"{normalize(title)}|{year}|{original_type}"


def apply_cached_fields(show, cached):
    show["tmdb_matched"] = cached["tmdb_matched"]
    # Self-heals cache entries written before this normalization existed:
    # ["Uncategorized"] used to be a literal placeholder value, but a
    # non-empty list is truthy in both Python and JS, so it permanently won
    # every "tmdb_genres or [fallback genre guess]" check and hid the
    # (often correct) keyword-based genre guess underneath it forever. None
    # is falsy in both languages, so the fallback actually falls through.
    cached_genres = cached["tmdb_genres"]
    show["tmdb_genres"] = None if cached_genres == ["Uncategorized"] else cached_genres
    show["tmdb_id"] = cached["tmdb_id"]
    show["tmdb_name"] = cached["tmdb_name"]
    show["tmdb_overview"] = cached["tmdb_overview"]
    show["tmdb_poster"] = cached["tmdb_poster"]
    show["tmdb_backdrop"] = cached.get("tmdb_backdrop")
    show["tmdb_vote"] = cached["tmdb_vote"]
    if cached.get("tmdb_release_date") is not None:
        show["tmdb_release_date"] = cached["tmdb_release_date"]
    if cached.get("tmdb_status") is not None:
        show["tmdb_status"] = cached["tmdb_status"]
    if cached.get("tmdb_tv_type") is not None:
        show["tmdb_tv_type"] = cached["tmdb_tv_type"]
    if cached.get("tmdb_number_of_seasons") is not None:
        show["tmdb_number_of_seasons"] = cached["tmdb_number_of_seasons"]
    if cached.get("tmdb_runtime") is not None:
        show["tmdb_runtime"] = cached["tmdb_runtime"]
    if cached.get("tmdb_imdb_id") is not None:
        show["tmdb_imdb_id"] = cached["tmdb_imdb_id"]
    if cached.get("tmdb_episode_run_time") is not None:
        show["tmdb_episode_run_time"] = cached["tmdb_episode_run_time"]
    if cached.get("tmdb_trailer_key") is not None:
        show["tmdb_trailer_key"] = cached["tmdb_trailer_key"]
    if cached.get("tmdb_cast") is not None:
        show["tmdb_cast"] = cached["tmdb_cast"]
    if cached.get("tmdb_director") is not None:
        show["tmdb_director"] = cached["tmdb_director"]
    if cached.get("tmdb_creators") is not None:
        show["tmdb_creators"] = cached["tmdb_creators"]
    if cached.get("tmdb_recommendations") is not None:
        show["tmdb_recommendations"] = cached["tmdb_recommendations"]
    if cached.get("tmdb_next_episode") is not None:
        show["tmdb_next_episode"] = cached["tmdb_next_episode"]
    # scan_media.py re-derives "type" from scratch every run (raw filename
    # parsing, no TMDB knowledge) - a cache-hit re-applies whatever that
    # fresh guess was UNLESS it's overridden here, so this needs to cover
    # every promotion case the live-search path above can produce, not
    # just "Other". Confirmed real gap: without the Movie branch, The
    # Langoliers correctly matched onto TV id 18197 on a live search, but
    # silently reverted to "Movie" on the very next cache-hit run, since
    # only "Other" was covered here.
    if cached.get("resolved_type") and cached["resolved_type"] != "Other" and (
        show["type"] == "Other" or (show["type"] == "Movie" and cached["resolved_type"] == "TV")
    ):
        show["type"] = cached["resolved_type"]
        show["type_promoted"] = True


CAST_LIMIT = 15
RECOMMENDATIONS_LIMIT = 6


def extract_trailer_key(detail):
    """Real official trailer, not a search - prefers an official YouTube
    trailer in English, falls back to the first YouTube trailer found.
    None if there genuinely isn't one (never guessed)."""
    videos = ((detail.get("videos") or {}).get("results")) or []
    trailers = [v for v in videos if v.get("type") == "Trailer" and v.get("site") == "YouTube"]
    if not trailers:
        return None
    trailers.sort(key=lambda v: (not v.get("official", False), v.get("iso_639_1") != "en"))
    return trailers[0].get("key")


# A long-running animated show's prolific voice actors can carry a `roles`
# list in the HUNDREDS (confirmed real 2026-09-14: The Simpsons' Tress
# MacNeille has 516 separate role entries, Pamela Hayden 283 - decades of
# one-line "Crowd (voice)"/"Woman on Street (voice)" bit parts each got
# their own aggregate_credits entry). Joining all of them turned one cast
# card into pages of scrolling before the actual show info. Capped to this
# many character NAMES per person (not role entries - TMDB itself sometimes
# already pre-joins several names into one role's character string for a
# regular cast member, e.g. Hank Azaria's single 803-episode role is
# "Moe Szyslak / Chief Wiggum / Apu / Comic Book Guy / Carl Carlson /
# Superintendent Chalmers (voice)" - capping by role COUNT would have left
# that alone while still letting a person with many short single-name roles
# blow past the same limit). Ordered by episode_count first so a one-off
# bit part never crowds out a real recurring character. 6 matches what
# real, substantial main-cast entries look like on this show (confirmed
# 2026-09-14: Dan Castellaneta's own 6-name main role) - the ceiling
# explicitly confirmed as still readable, not a number picked in a vacuum.
CAST_NAMES_PER_PERSON = 6


def extract_cast(detail):
    """TV's plain `credits.cast` field is TMDB's own top-level cast list -
    unmaintained and often near-empty for older/classic shows (confirmed
    real 2026-09-13: Twin Peaks' `credits.cast` has exactly ONE person,
    Kyle MacLachlan, while the real cast is dozens). `aggregate_credits`
    (only present on TV responses, built by TMDB from every episode's own
    per-episode credits) is the complete, correctly-ordered list for
    exactly this case, so it's preferred whenever present - movies have no
    such field, so this falls through to the plain list for them
    unchanged. An aggregate entry's character comes from a `roles` array
    (a recast character, or a title change like "Dale Cooper" ->
    "Dale Cooper / Dougie Jones", becomes more than one role) - every
    role's character string is split on TMDB's own " / " separator first
    (some of theirs already join multiple names in one role) and the
    result flattened into one ordered, deduped, CAST_NAMES_PER_PERSON-
    capped list of names - see that constant's own comment for why."""
    agg_cast = ((detail.get("aggregate_credits") or {}).get("cast")) or []
    if agg_cast:
        out = []
        for c in agg_cast[:CAST_LIMIT]:
            if not c.get("name"):
                continue
            roles_sorted = sorted((c.get("roles") or []), key=lambda r: r.get("episode_count") or 0, reverse=True)
            names = [n for r in roles_sorted if r.get("character") for n in r["character"].split(" / ")]
            kept = []
            for n in names:
                if len(kept) >= CAST_NAMES_PER_PERSON:
                    break
                # Exact-dup guard only (not substring-containment) - once
                # already split into individual names, "Ed" and "Fred"
                # sharing letters is no longer a real risk the way a whole
                # pre-joined TMDB string was (see the module's own note on
                # why this changed from containment to exact-match here).
                if n in kept:
                    continue
                kept.append(n)
            character = " / ".join(kept) or None
            out.append({"id": c.get("id"), "name": c.get("name"), "character": character, "profile_path": c.get("profile_path")})
        return out
    cast = ((detail.get("credits") or {}).get("cast")) or []
    return [
        {"id": c.get("id"), "name": c.get("name"), "character": c.get("character"), "profile_path": c.get("profile_path")}
        for c in cast[:CAST_LIMIT] if c.get("name")
    ]


def extract_recommendations(detail, media_type):
    """TMDB's own 'people who liked this also liked' list - cross-referenced
    against the user's own library client-side (JS), not here, since
    whether a title is already owned can change without a re-fetch.
    overview/vote_average are already present in the same TMDB response
    this reads from (no extra API call) - added 2026-08-22 for the
    recommendation hover-preview, kept short the same way admin-search
    overviews already are so this doesn't bloat media_db.json across
    potentially hundreds of recommendations per show."""
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


def best_match(results, title, year, date_field, return_ambiguous=False):
    if not results:
        return (None, None) if return_ambiguous else None
    # Defensive - `year` is supposed to always be an int or None (real
    # source: scan_media.py's extract_year(), always int) but has, in
    # practice, arrived here as a string more than once (confirmed real
    # 2026-09-13: a synthetic record elsewhere stored `years` as a raw
    # date-string slice instead of int, crashing the arithmetic below and
    # taking down the entire pipeline run with it - fixed at that source
    # too, but this one bad value anywhere should never be able to do
    # that again).
    if isinstance(year, str):
        year = int(year) if year.isdigit() else None
    scored = []
    for r in results:
        name = r.get("title") or r.get("name") or ""
        sim = similarity(title, name)
        date_str = r.get(date_field) or ""
        r_year = int(date_str[:4]) if date_str[:4].isdigit() else None
        year_bonus = 0.0
        if year and r_year:
            diff = abs(year - r_year)
            if diff == 0:
                year_bonus = 0.25
            elif diff == 1:
                year_bonus = 0.1
            elif diff > 3:
                year_bonus = -0.3
        # Small log-scaled nudge, capped well below what a real title-
        # similarity gap represents - a landslide-popular show can win a
        # genuinely close spelling call (real case: "MasterChef US" query
        # scores 0.963 similarity against the obscure 2000 PBS pilot
        # "MasterChef USA" - popularity 2.6 - vs 0.870 against the actual
        # Fox show everyone means by that title - popularity 32 - so the
        # decoy always won on pure string similarity alone), but a
        # popular-yet-unrelated result still can't beat a real title match:
        # the 0.20 cap is far smaller than the similarity gap between any
        # two genuinely different titles. Confirmed real, not a guess -
        # this exact MasterChef US collision was found live 2026-08-20.
        popularity_bonus = min(0.20, 0.12 * math.log10((r.get("popularity") or 0) + 1))
        score = sim + year_bonus + popularity_bonus
        scored.append((score, sim, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top_sim, top = scored[0]
    if top_sim < 0.45:
        return (None, None) if return_ambiguous else None

    # Ambiguous-match safety net (plan item 20 / Entry 13) - the automatic
    # matcher still just picks the top score below, same as always; this
    # only adds VISIBILITY when the pick was genuinely a close call
    # between two different real titles, the classic case being a
    # cancelled original vs. its modern reboot sharing an exact name with
    # no year in the filename to disambiguate them (confirmed real:
    # Kitchen Nightmares 2007 tmdb:11294 "Ended" vs. 2023 tmdb:235884
    # "Returning Series" - both score within a hair of each other on
    # title similarity alone). Never changes which result gets returned -
    # a wrong guess here is still fixed the same way any other one is,
    # through the Admin panel's tmdb_id override - this just means it
    # doesn't have to be *noticed* by accident first. Requires BOTH
    # candidates to be near-exact title matches (not just "both kind of
    # close"), or two merely-similar-but-different titles scoring near
    # each other by coincidence would print a warning for no real reason.
    #
    # return_ambiguous=True (pipeline/rename_queue_inplace.py's fresh-match
    # path)
    # surfaces the SAME check as a real value instead of just a log line, so
    # a caller that can offer the user a choice (unlike this module's own
    # main matching pass, which has no UI to ask through) can flag it as
    # needs_review with both candidates offered, rather than silently
    # picking one the way this function always has.
    ambiguous_runner = None
    if len(scored) > 1:
        runner_score, runner_sim, runner = scored[1]
        if runner_sim >= 0.85 and top_sim >= 0.85 and (top_score - runner_score) < 0.05 and top.get("id") != runner.get("id"):
            ambiguous_runner = runner
            top_name = top.get("title") or top.get("name") or "?"
            runner_name = runner.get("title") or runner.get("name") or "?"
            top_date = (top.get(date_field) or "")[:4] or "?"
            runner_date = (runner.get(date_field) or "")[:4] or "?"
            print(
                f"  AMBIGUOUS MATCH: \"{title}\" -> picked \"{top_name}\" ({top_date}, tmdb:{top.get('id')}) "
                f"over \"{runner_name}\" ({runner_date}, tmdb:{runner.get('id')}) - scores {top_score:.3f} vs "
                f"{runner_score:.3f}, nearly tied. If this is the wrong one (e.g. a cancelled original picked "
                f"over its reboot, or vice versa), fix it via the Admin panel's \"Fix a title\"."
            )

    return (top, ambiguous_runner) if return_ambiguous else top


def search_movie(title, year):
    params = {"query": title, "include_adult": "true"}
    if year:
        params["year"] = year
    data = api_get("/search/movie", params)
    results = data.get("results") if data else None
    match = best_match(results, title, year, "release_date")
    if not match and year:
        data = api_get("/search/movie", {"query": title, "include_adult": "true"})
        results = data.get("results") if data else None
        match = best_match(results, title, year, "release_date")
    return match


def search_tv(title, year):
    params = {"query": title, "include_adult": "true"}
    if year:
        params["first_air_date_year"] = year
    data = api_get("/search/tv", params)
    results = data.get("results") if data else None
    match = best_match(results, title, year, "first_air_date")
    if not match and year:
        data = api_get("/search/tv", {"query": title, "include_adult": "true"})
        results = data.get("results") if data else None
        match = best_match(results, title, year, "first_air_date")
    return match


def apply_episode_reshape(result):
    """Reshapes a /tv/{id}/season/{n}/episode/{m} response into the same
    title/release_date/poster_path/genre_ids shape a movie/tv search
    result already has - same reshape the tv_episode override branch
    below does, factored out so fallback_identify's tv_episode results
    (see main()) go through identical logic, not a second copy of it."""
    result["title"] = result.get("name")
    result["release_date"] = result.get("air_date")
    result["poster_path"] = result.get("still_path")
    result["backdrop_path"] = result.get("still_path")
    result["genre_ids"] = []
    return result


def persist_fallback_override(title, show_type, tmdb_id, media_type, season, episode, source):
    """Writes a chain-resolved identity (pipeline/fallback_identify.py)
    into user_state.json's overrides - same mechanism the Admin panel and
    manual corrections already use (see load_tmdb_id_overrides() above),
    keyed by title so it's durable across future re-searches the way a
    same-key tmdb_id_override already is (an ongoing show forces a fresh
    needs_backfill search every run - without persisting here, the next
    run would just re-derive the same result from scratch every time,
    wasting the epguides/Wikidata round-trip for no reason. Also
    auditable: auto_resolved_via records which chain found it, so this
    never reads as a human-confirmed correction when a script actually
    made the call."""
    key = f"title:{show_type}:{title}"
    entry = {"tmdb_id_override": tmdb_id, "auto_resolved_via": source}
    if media_type in ("movie", "tv", "tv_episode"):
        entry["media_type"] = media_type
    if media_type == "tv_episode":
        entry["season"] = season
        entry["episode"] = episode

    def mutator(state):
        state.setdefault("overrides", {})[key] = entry

    update_state(mutator)


def main():
    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    cache = load_cache()
    person_cache = load_person_cache()
    tmdb_id_overrides = load_tmdb_id_overrides()
    force_unmatched_titles = load_force_unmatched_titles()
    now = datetime.now(timezone.utc)

    movie_genre_map = {g["id"]: g["name"] for g in (api_get("/genre/movie/list", {}) or {}).get("genres", [])}
    tv_genre_map = {g["id"]: g["name"] for g in (api_get("/genre/tv/list", {}) or {}).get("genres", [])}

    matched = 0
    promoted = 0
    skipped_hash = 0
    from_cache = 0
    unmatched = []
    fallback_matched = []

    for i, s in enumerate(shows):
        title = s["title"]
        step_progress.report(i + 1, len(shows), "titles", title)
        year = s["years"][0] if s.get("years") else None
        key = cache_key(title, year, s["type"])
        cached = cache.get(key)
        # Also try the title-based key, not just whatever id-based key
        # cached currently resolves to - without this, an override that
        # successfully fixes a show once becomes permanently self-defeating
        # for any ongoing show (tmdb_status in ONGOING_STATUSES forces a
        # fresh needs_backfill re-search every run - see below - so once
        # the cache reflects the corrected id, override_lookup_key's
        # id-based key shifts to match it instead of the wrong id the
        # override was actually saved under, the lookup misses, and the
        # unguided re-search lands right back on the wrong id forever).
        # Confirmed real, not hypothetical: MasterChef US (id 40290, an
        # ongoing Fox show) flipped back to the wrong decoy match (id
        # 203934) on the very next run after being fixed, 2026-08-20.
        # A split-by-year title (Doctor Who, The Stand) ONLY ever gets a
        # year-qualified title key written (web_server.py's /api/override) -
        # falling back to the bare title key for one of these would risk
        # exactly the 2026-09-26 incident this key scheme exists to
        # prevent: fixing one era's wrong match via the bare key silently
        # reapplied that id to every OTHER era sharing the title, and
        # consolidate.py's merge_by_tmdb_id then merged all of them into
        # one garbled show. Everything else still falls back to the bare
        # key, unchanged.
        title_override_key = (
            f"title:{s['type']}:{title}:{year}" if is_split_by_year_title(title) and year
            else f"title:{s['type']}:{title}"
        )
        override_entry = (
            tmdb_id_overrides.get(override_lookup_key(s["type"], title, cached))
            or tmdb_id_overrides.get(title_override_key)
        )
        override_id, override_media_type, override_season, override_episode = (
            override_entry if override_entry else (None, None, None, None)
        )

        is_force_unmatched = f"title:{s['type']}:{title}" in force_unmatched_titles
        if looks_like_hash(title) or is_force_unmatched:
            s["tmdb_matched"] = False
            s["tmdb_genres"] = None
            s["tmdb_id"] = None
            s["tmdb_name"] = None
            s["tmdb_overview"] = ""
            s["tmdb_poster"] = None
            s["tmdb_backdrop"] = None
            s["tmdb_vote"] = None
            skipped_hash += 1
            continue

        if cached:
            stale_unmatched = False
            if not cached["tmdb_matched"]:
                checked = cached.get("last_checked")
                if checked:
                    age_days = (now - datetime.fromisoformat(checked)).days
                    stale_unmatched = age_days >= RETRY_UNMATCHED_AFTER_DAYS
                else:
                    stale_unmatched = True
            # one-time backfill: cache entries written before tmdb_release_date /
            # tmdb_backdrop / tmdb_runtime / tmdb_imdb_id / tmdb_episode_run_time
            # existed are missing the key entirely (distinct from present-but-
            # None). tmdb_runtime and tmdb_episode_run_time are type-specific -
            # checking either for the wrong type would trigger a pointless
            # backfill forever, since that type never gains that key.
            #
            # Ongoing shows are a SEPARATE, permanent (not one-time) reason to
            # re-fetch: tmdb_next_episode/tmdb_status/tmdb_episode_run_time
            # are all snapshots that go stale the moment a new episode airs -
            # confirmed for real (Attack on Titan's cached "next episode"
            # badge would otherwise silently freeze the first time it aired
            # and never update again). Cached forever only once the show
            # stops airing, same "current stays fresh, finished stays cached"
            # split enrich_tv_coverage.py's own season cache already uses.
            is_ongoing_tv = s["type"] == "TV" and cached.get("tmdb_status") in ONGOING_STATUSES
            needs_backfill = cached["tmdb_matched"] and (
                "tmdb_release_date" not in cached or "tmdb_backdrop" not in cached
                or "tmdb_imdb_id" not in cached or "tmdb_cast" not in cached
                or (s["type"] == "Movie" and "tmdb_runtime" not in cached)
                or (s["type"] == "TV" and "tmdb_episode_run_time" not in cached)
                # One-time backfill for cast entries cached before per-actor
                # TMDB person id (and its own imdb_id) were kept - added for
                # the "actor photo links" feature. Every entry either has
                # both or neither, so checking one flags the whole show.
                or (cached.get("tmdb_cast") and any("id" not in c for c in cached["tmdb_cast"]))
                # One-time backfill for TV shows cached before extract_cast()
                # preferred aggregate_credits over TMDB's own near-empty
                # top-level `credits.cast` (2026-09-13 - Twin Peaks' cached
                # cast was exactly ONE person, Kyle MacLachlan, while the
                # real cast is dozens). A structural marker (like the "id"
                # check just above), not a cast-size guess - an earlier
                # version of this trigger used "2 or fewer cast members",
                # which would have re-fetched a legitimately small-cast show
                # (a two-hander, an interview-format series) on every single
                # run forever, never converging. tmdb_cast_source is stamped
                # below every time a TV show is (re-)fetched, so this fires
                # exactly once per show, same as the "id" check. Versioned
                # ("_v2", not just "aggregate_credits") so a later change to
                # extract_cast()'s own join/cap logic - not just which TMDB
                # field it reads - can force one more re-fetch the same way:
                # 2026-09-14 added a per-person name cap after The Simpsons'
                # prolific voice actors (Tress MacNeille: 516 raw roles)
                # printed pages of joined character names for one cast card.
                or (s["type"] == "TV" and cached.get("tmdb_cast") and cached.get("tmdb_cast_source") not in ("aggregate_credits_v2", "credits_v2"))
                or is_ongoing_tv
                # An active MANUAL_TMDB_ID_OVERRIDES entry always wins over
                # whatever's cached - without this, a wrong match already
                # cached from before the override existed would keep
                # winning the cache-hit shortcut below forever, since
                # nothing else here would ever mark it stale.
                or (override_id is not None and cached.get("tmdb_id") != override_id)
            )
            # Same override-forces-refresh escape hatch as needs_backfill
            # above, but for the OTHER cache-hit branch: a title that was
            # previously unmatched and isn't stale yet would otherwise keep
            # winning the shortcut below forever once an override exists for
            # it, since stale_unmatched only tracks time elapsed, not
            # whether a fix showed up in the meantime. Confirmed real: "Color
            # Out Of Space" stayed unmatched through a full pipeline run
            # despite a fresh override, because its unmatched cache entry
            # wasn't old enough to be "stale" on its own terms.
            override_forces_unmatched_refresh = not cached["tmdb_matched"] and override_id is not None
            if (cached["tmdb_matched"] and not needs_backfill) or (
                not cached["tmdb_matched"] and not stale_unmatched and not override_forces_unmatched_refresh
            ):
                apply_cached_fields(s, cached)
                from_cache += 1
                if s["tmdb_matched"]:
                    matched += 1
                    cache_show_images(s)  # no-ops (a stat() per image) unless something's genuinely still missing locally
                else:
                    unmatched.append(f"{title} ({s['type']}{', ' + str(year) if year else ''})")
                continue

        result = None
        matched_via = None

        if override_id is not None and override_media_type == "tv_episode":
            matched_via = "tv_episode"
            result = api_get(f"/tv/{override_id}/season/{override_season}/episode/{override_episode}", {})
            if result:
                apply_episode_reshape(result)
        elif override_id is not None:
            if override_media_type in ("movie", "tv"):
                matched_via = override_media_type
            else:
                matched_via = "movie" if s["type"] == "Movie" else "tv"
            endpoint = matched_via
            result = api_get(f"/{endpoint}/{override_id}", {})
            if result and "genre_ids" not in result:
                # detail endpoint returns genres:[{id,name},...], not the
                # search endpoint's genre_ids:[id,...] the code below reads
                result["genre_ids"] = [g["id"] for g in (result.get("genres") or [])]
        elif s["type"] == "Movie":
            result = search_movie(title, year)
            matched_via = "movie"
            if not result:
                # A single-file miniseries filed as "Movie" (one archive
                # folder, no season pattern to trigger "TV" classification)
                # has a real TMDB entity, just not a movie one - confirmed
                # real case, not hypothetical: The Langoliers (1995) is TV
                # miniseries id 18197, so search_movie() legitimately finds
                # nothing and this used to be a dead end. "TV" already gets
                # the same fallback in reverse below.
                result = search_tv(title, year)
                matched_via = "tv"
        elif s["type"] == "TV":
            result = search_tv(title, year)
            matched_via = "tv"
            if not result:
                result = search_movie(title, year)
                matched_via = "movie"
        else:  # Other / unclear from filename alone
            result = search_movie(title, year)
            matched_via = "movie"
            if not result:
                result = search_tv(title, year)
                matched_via = "tv"

        # Last resort before giving up: TMDB's own title search (both
        # directions, above) found nothing usable, and there's no explicit
        # human override already handling this title. Try the same
        # non-guessing epguides/Wikidata identity chain the reorg side of
        # this project uses (see pipeline/fallback_identify.py) - default
        # behavior now, not a one-off manual investigation, per the user's
        # explicit ask 2026-08-21 ("this should be default fallback
        # behavior instead of making guesses"). Every step past the
        # initial epguides/Wikipedia title match is an exact-id
        # cross-reference, never a second round of fuzzy guessing.
        fallback_source = None
        if not result and override_id is None:
            fallback = fi.resolve(title, year, s["type"], api_get)
            if fallback:
                f_id, f_media_type, f_season, f_episode, fallback_source = fallback
                if f_media_type == "tv_episode":
                    matched_via = "tv_episode"
                    result = api_get(f"/tv/{f_id}/season/{f_season}/episode/{f_episode}", {})
                    if result:
                        apply_episode_reshape(result)
                else:
                    matched_via = f_media_type
                    result = api_get(f"/{f_media_type}/{f_id}", {})
                    if result and "genre_ids" not in result:
                        result["genre_ids"] = [g["id"] for g in (result.get("genres") or [])]
                if result:
                    persist_fallback_override(title, s["type"], f_id, f_media_type, f_season, f_episode, fallback_source)

        time.sleep(SLEEP_BETWEEN_CALLS)

        if result:
            genre_map = movie_genre_map if matched_via == "movie" else tv_genre_map
            genre_ids = result.get("genre_ids", [])
            genres = [genre_map.get(gid) for gid in genre_ids if genre_map.get(gid)]
            s["tmdb_matched"] = True
            s["tmdb_genres"] = genres or None
            s["tmdb_id"] = result.get("id")
            s["tmdb_name"] = result.get("title") or result.get("name")
            s["tmdb_overview"] = result.get("overview", "")
            s["tmdb_poster"] = result.get("poster_path")
            s["tmdb_backdrop"] = result.get("backdrop_path")
            s["tmdb_vote"] = result.get("vote_average")
            s["tmdb_release_date"] = result.get("release_date") or result.get("first_air_date")
            matched += 1
            if fallback_source:
                fallback_matched.append(f"{title} ({s['type']}) -> tmdb {s['tmdb_id']} via {fallback_source}")

            if s["type"] == "Other" or (s["type"] == "Movie" and matched_via == "tv"):
                s["type"] = "Movie" if matched_via == "movie" else "TV"
                s["type_promoted"] = True
                promoted += 1

            if matched_via == "tv":
                # append_to_response=external_ids piggybacks the imdb_id
                # lookup onto this same call - still one request, not two.
                # episode_run_time is TMDB's old series-level field and comes
                # back empty for most shows now (confirmed against Attack on
                # Titan - a real, long-running, unambiguously ~23min/episode
                # show still returned []) - last_episode_to_air.runtime is
                # the field TMDB actually keeps populated today, also free on
                # this same response. It's one real episode's runtime, not a
                # true series average, but it's honest data, not a guess -
                # falls back to next_episode_to_air for a show that hasn't
                # aired anything yet.
                # videos/credits/recommendations piggyback onto this same
                # append_to_response call too - still one request total.
                # aggregate_credits added 2026-09-13 - see extract_cast()'s
                # own comment for why plain `credits` alone isn't enough.
                detail = api_get(f"/tv/{result['id']}", {"append_to_response": "external_ids,videos,credits,aggregate_credits,recommendations"})
                time.sleep(SLEEP_BETWEEN_CALLS)
                if detail:
                    s["tmdb_status"] = detail.get("status")
                    s["tmdb_tv_type"] = detail.get("type")
                    s["tmdb_number_of_seasons"] = detail.get("number_of_seasons")
                    s["tmdb_imdb_id"] = (detail.get("external_ids") or {}).get("imdb_id") or None
                    # 2026-09-13: added a third fallback after a real gap
                    # was found - "Diners, Drive-Ins and Dives" has no
                    # runtime on either last_episode_to_air or
                    # next_episode_to_air (both come back null), but TMDB's
                    # own deprecated series-level episode_run_time array
                    # (the "comes back empty for most shows now" field
                    # mentioned above) actually HAD a real value ([22]) for
                    # this exact show - not empty for everyone, just most.
                    # Worth checking as a last resort before giving up.
                    runtime = (detail.get("last_episode_to_air") or {}).get("runtime") \
                        or (detail.get("next_episode_to_air") or {}).get("runtime") \
                        or (detail.get("episode_run_time") or [None])[0]
                    s["tmdb_episode_run_time"] = runtime or None
                    s["tmdb_trailer_key"] = extract_trailer_key(detail)
                    s["tmdb_cast"] = extract_cast(detail)
                    # Structural marker for needs_backfill's one-time cast
                    # re-fetch trigger above - records which source this
                    # cast list actually came from, not just that a fetch
                    # happened, so a TV show fetched before aggregate_credits
                    # was requested is reliably distinguishable from one
                    # fetched after.
                    s["tmdb_cast_source"] = "aggregate_credits_v2" if ((detail.get("aggregate_credits") or {}).get("cast")) else "credits_v2"
                    backfill_imdb_ids(s["tmdb_cast"], person_cache)
                    s["tmdb_creators"] = [c.get("name") for c in (detail.get("created_by") or []) if c.get("name")] or None
                    s["tmdb_recommendations"] = extract_recommendations(detail, "TV")
                    next_ep = detail.get("next_episode_to_air")
                    s["tmdb_next_episode"] = {
                        "season": next_ep.get("season_number"), "episode": next_ep.get("episode_number"),
                        "name": next_ep.get("name"), "air_date": next_ep.get("air_date"),
                    } if next_ep and next_ep.get("air_date") else None
            elif matched_via == "movie":
                # Search results don't include runtime/imdb_id - one extra
                # call, cached same as everything else, never repeated once
                # cached. tmdb_runtime is used by detect_runtime_mismatch.py
                # to flag a probably-wrong match (real file duration way off
                # from what TMDB says this movie runs); imdb_id is native on
                # this response, no append_to_response needed. videos/
                # credits/recommendations piggyback the same way as TV above.
                detail = api_get(f"/movie/{result['id']}", {"append_to_response": "videos,credits,recommendations"})
                time.sleep(SLEEP_BETWEEN_CALLS)
                if detail:
                    s["tmdb_runtime"] = detail.get("runtime") or None
                    s["tmdb_imdb_id"] = detail.get("imdb_id") or None
                    s["tmdb_trailer_key"] = extract_trailer_key(detail)
                    s["tmdb_cast"] = extract_cast(detail)
                    backfill_imdb_ids(s["tmdb_cast"], person_cache)
                    crew = (detail.get("credits") or {}).get("crew") or []
                    s["tmdb_director"] = next((c.get("name") for c in crew if c.get("job") == "Director"), None)
                    s["tmdb_recommendations"] = extract_recommendations(detail, "Movie")
            elif matched_via == "tv_episode":
                # No extra call needed - the episode response already
                # carries its own runtime. Cast/director/imdb_id/trailer
                # would need a call against the PARENT show, which mixes in
                # data that isn't really about this one episode - left
                # unset rather than guessed.
                s["tmdb_runtime"] = result.get("runtime") or None
        else:
            s["tmdb_matched"] = False
            s["tmdb_genres"] = None
            s["tmdb_id"] = None
            s["tmdb_name"] = None
            s["tmdb_overview"] = ""
            s["tmdb_poster"] = None
            s["tmdb_backdrop"] = None
            s["tmdb_vote"] = None
            unmatched.append(f"{title} ({s['type']}{', ' + str(year) if year else ''})")

        if s["tmdb_matched"]:
            cache_show_images(s)

        cache[key] = {
            "tmdb_matched": s["tmdb_matched"], "tmdb_genres": s["tmdb_genres"], "tmdb_id": s["tmdb_id"],
            "tmdb_name": s["tmdb_name"], "tmdb_overview": s["tmdb_overview"], "tmdb_poster": s["tmdb_poster"],
            "tmdb_backdrop": s.get("tmdb_backdrop"),
            "tmdb_vote": s["tmdb_vote"], "tmdb_release_date": s.get("tmdb_release_date"),
            "tmdb_status": s.get("tmdb_status"), "tmdb_tv_type": s.get("tmdb_tv_type"),
            "tmdb_number_of_seasons": s.get("tmdb_number_of_seasons"), "resolved_type": s["type"],
            "tmdb_runtime": s.get("tmdb_runtime"),
            "tmdb_imdb_id": s.get("tmdb_imdb_id"), "tmdb_episode_run_time": s.get("tmdb_episode_run_time"),
            "tmdb_trailer_key": s.get("tmdb_trailer_key"), "tmdb_cast": s.get("tmdb_cast"),
            # Without this the one-time cast re-fetch trigger (see needs_backfill) never sees
            # the marker and fires on every run, re-fetching every TV show forever.
            "tmdb_cast_source": s.get("tmdb_cast_source"),
            "tmdb_director": s.get("tmdb_director"), "tmdb_creators": s.get("tmdb_creators"),
            "tmdb_recommendations": s.get("tmdb_recommendations"), "tmdb_next_episode": s.get("tmdb_next_episode"),
            "last_checked": now.isoformat(),
        }

        if (i + 1) % 50 == 0:
            print(f"...{i + 1}/{len(shows)}", file=sys.stderr)
            save_cache(cache)
            save_person_cache(person_cache)
            # Checkpoint media_db.json too - shows[:i+1] already carry this
            # run's updated tmdb_* fields, shows[i+1:] are untouched (same
            # state scan_media.py left them in), so this is always a valid
            # snapshot to persist, not a partial/torn one.
            save_media_db(shows)

    # Atomic write - see scan_media.py's own copy of this comment
    # (2026-08-21): web_server.py reads this file on every page load, so a
    # direct write here can hand a concurrent reader a torn/truncated file.
    save_media_db(shows)
    save_cache(cache)
    save_person_cache(person_cache)

    print(f"\nMatched {matched}/{len(shows)} titles against TMDB.")
    print(f"Served {from_cache} from cache (no API call needed).")
    print(f"Skipped {skipped_hash} hash-named/unidentifiable folders (no lookup attempted).")
    print(f"Promoted {promoted} 'Other' items to Movie/TV once TMDB confirmed a real match.")
    if fallback_matched:
        print(f"{len(fallback_matched)} resolved via the epguides/Wikidata fallback chain (TMDB's own search found nothing):")
        for line in fallback_matched:
            print(f"  {line}")
    print(f"{len(unmatched)} unmatched (kept as 'Uncategorized').")


if __name__ == "__main__":
    main()

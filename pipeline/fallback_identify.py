"""Automated, non-guessing identity fallback for anything TMDB's own
`/search/movie`/`/search/tv` comes up empty on. Two independent chains,
tried in order from `enrich_tmdb.py`'s main loop - both only ever resolve
via an exact-id cross-reference at the final step, never a second round
of fuzzy title matching. That's the "never guess" bar this whole project
already holds itself to (see best_match()'s own 0.45 similarity floor and
force_unmatched/runtime_confirmed in user_state.json) - this module just
gives that bar more places to succeed before giving up and marking
something Uncategorized.

Chain 1 - epguides -> TVmaze -> IMDb -> TMDB /find:
  enrich_epguides.py's own title/year match (never guesses - single
  unambiguous hit, or year-corroborated) gives a TVmaze id straight out of
  the locally cached allshows index, no extra request. TVmaze's API turns
  that into a real IMDb id (`externals.imdb`). TMDB's own
  `/find/{imdb_id}?external_source=imdb_id` turns THAT into a real
  tmdb_id - checking tv_results/movie_results/tv_episode_results in that
  order (tv_episode_results is real, not a theoretical case: "SNL50: The
  Anniversary Special" has no standalone TMDB listing of its own, only
  shows up as season 50 episode 13 of Saturday Night Live's own show id -
  confirmed by the reorg side of this project 2026-08-21, same lesson
  applied here).

Chain 2 - Wikipedia -> Wikidata:
  enrich_wikipedia.py's own search-then-relatedness-check (already tuned
  through several confirmed false-positive fixes - word-boundary keyword
  matching, minimum overlap counts) finds the right page. Its REST summary
  response already includes `wikibase_item` (the Wikidata QID) with no
  extra request. Wikidata's own entity API exposes P4983 (TMDB TV series
  ID) / P4947 (TMDB movie ID) directly when present - looks like the most
  confident source available, a real structured cross-reference rather
  than an inference, EXCEPT Wikidata's copy can and does go stale: TMDB
  merges/renumbers entries and nothing re-syncs Wikidata automatically.
  Confirmed real, not theoretical - "SNL50: The Anniversary Special"'s own
  P4983/P4947 values all point at TMDB ids that return nothing today. So
  every direct-property candidate gets a live TMDB detail fetch before
  being trusted; only once those are exhausted does this fall back to
  P345 (IMDb ID) bridged through the same TMDB /find endpoint chain 1
  uses - which doesn't need that same validation, since TMDB's own /find
  can only ever hand back an id that's live in its database right now.

Verified against all three real, independent public APIs before being
wired into enrich_tmdb.py (2026-08-21): TMDB id 61268 ("A to Z") reached
through BOTH chains independently, real IDs at every step, not simulated.

Deliberately never touches user_state.json itself - enrich_tmdb.py is
responsible for deciding whether/how to persist a resolution this module
finds; this module only ever answers "what does epguides/Wikipedia's own
structured data say the identity is," nothing more."""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from enrich_epguides import ensure_allshows, load_allshows_index, find_match  # noqa: E402
from enrich_wikipedia import wiki_search, wiki_summary, looks_related, looks_like_hash  # noqa: E402

USER_AGENT = "MediaLibraryScripts/1.0 (personal home media library enrichment; local single-user tool)"
BRIDGE_SLEEP = 0.3

_allshows_index_cache = None


def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def _tmdb_find_by_imdb(imdb_id, tmdb_api_get):
    """(tmdb_id, media_type, season, episode) or None. season/episode are
    only meaningful (non-None) when media_type == "tv_episode"."""
    data = tmdb_api_get(f"/find/{imdb_id}", {"external_source": "imdb_id"})
    if not data:
        return None
    if data.get("tv_results"):
        return data["tv_results"][0]["id"], "tv", None, None
    if data.get("movie_results"):
        return data["movie_results"][0]["id"], "movie", None, None
    ep_results = data.get("tv_episode_results")
    if ep_results:
        e = ep_results[0]
        show_id = e.get("show_id")
        if show_id is not None:
            return show_id, "tv_episode", e.get("season_number"), e.get("episode_number")
    return None


def _get_allshows_index():
    global _allshows_index_cache
    if _allshows_index_cache is None:
        if not ensure_allshows():
            return None
        _allshows_index_cache = load_allshows_index()
    return _allshows_index_cache


def resolve_via_epguides(title, year, show_type, tmdb_api_get):
    """epguides is TV-only - never called for a confirmed Movie. Returns
    (tmdb_id, media_type, season, episode) or None."""
    if show_type not in ("TV", "Other") or looks_like_hash(title):
        return None
    index = _get_allshows_index()
    if index is None:
        return None
    directory, matched_title, tvmaze_id = find_match(title, year, index)
    if not tvmaze_id:
        return None

    time.sleep(BRIDGE_SLEEP)
    tvmaze_data = _get_json(f"https://api.tvmaze.com/shows/{tvmaze_id}")
    imdb_id = ((tvmaze_data or {}).get("externals") or {}).get("imdb")
    if not imdb_id:
        return None

    time.sleep(BRIDGE_SLEEP)
    return _tmdb_find_by_imdb(imdb_id, tmdb_api_get)


def resolve_via_wikidata(title, show_type, tmdb_api_get):
    """Works for both Movie and TV. Returns (tmdb_id, media_type, season,
    episode) or None."""
    if looks_like_hash(title):
        return None
    page_title = wiki_search(title)
    if not page_title:
        return None
    summary = wiki_summary(page_title)
    if not summary or not looks_related(title, page_title, summary.get("extract")):
        return None
    qid = summary.get("wikibase_item")
    if not qid:
        return None

    time.sleep(BRIDGE_SLEEP)
    entity_data = _get_json(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    claims = (((entity_data or {}).get("entities") or {}).get(qid) or {}).get("claims") or {}

    def all_values(prop):
        out = []
        for c in claims.get(prop, []):
            dv = (c.get("mainsnak") or {}).get("datavalue")
            if dv and dv.get("value"):
                out.append(dv["value"])
        return out

    # Wikidata's own TMDB-id properties are NOT reliable on their own -
    # confirmed real, not a theoretical worry: SNL50's P4983/P4947 values
    # all point at TMDB ids that no longer resolve to anything (TMDB
    # merges/renumbers entries; Wikidata's copy goes stale and nothing
    # re-syncs it automatically). A direct property is still the fastest
    # path WHEN it's actually current, but every candidate gets a real
    # TMDB detail fetch before being trusted - never propagate a dead id
    # into media_db.json just because Wikidata said so.
    for tv_id in all_values("P4983"):
        if str(tv_id).isdigit() and tmdb_api_get(f"/tv/{tv_id}", {}):
            return int(tv_id), "tv", None, None
    for movie_id in all_values("P4947"):
        if str(movie_id).isdigit() and tmdb_api_get(f"/movie/{movie_id}", {}):
            return int(movie_id), "movie", None, None

    # The IMDb bridge doesn't need this same validation - TMDB's own
    # /find endpoint can only ever return ids that are live in ITS
    # database right now, there's no way for it to hand back something
    # stale the way a copied-once Wikidata property can.
    for imdb_id in all_values("P345"):
        time.sleep(BRIDGE_SLEEP)
        result = _tmdb_find_by_imdb(imdb_id, tmdb_api_get)
        if result:
            return result
    return None


def resolve(title, year, show_type, tmdb_api_get):
    """The single entry point enrich_tmdb.py calls - tries epguides first
    (TV only, cheaper: no network round-trip if there's simply no TVmaze
    id for this title), then Wikidata (works for both types). Returns
    (tmdb_id, media_type, season, episode, source) or None, where source
    is "epguides" or "wikidata" for auditability - callers should record
    which one resolved it, not just that something did."""
    result = resolve_via_epguides(title, year, show_type, tmdb_api_get)
    if result:
        return result + ("epguides",)
    result = resolve_via_wikidata(title, show_type, tmdb_api_get)
    if result:
        return result + ("wikidata",)
    return None

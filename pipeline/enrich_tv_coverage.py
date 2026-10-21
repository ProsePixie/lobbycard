"""Nightly-only (not the fast startup quick-scan): for every TMDB-matched TV
show, flags every aired episode/season not owned - full completeness back to
season 1, no recency cutoff - tags real per-episode air_date and
episode_title via data\\tmdb_season_cache.json, and separately (own pass,
unconditional on the completeness gates) dates real season-0 specials for
display/sort purposes.

Cooking/reality/documentary/talk/news shows (EXCLUDED_GENRES/
EXCLUDED_TV_TYPES below) are NOT fully excluded from completeness
tracking anymore (2026-08-21) - the user's own call: excluding the whole
genre outright was "limiting myself by default" for shows they genuinely
keep and want gap-tracked (their example: the Expedition Unknown family).
Each such show gets its own tracking_start_date (data\\genre_tracking_
start.json), recorded once the first time this runs for it and never
moved forward again - everything aired before that date stays
permanently exempt (no retroactive backlog), everything on/after it is
tracked exactly like any other show. queue-only minor content and
shows set to "Delete after watching" (read from data\\user_state.json -
never collected to completion, so never worth flagging gaps in) are
still fully excluded, unaffected by this change.

Also reads data\\physical_db.json and data\\amazon_db.json (both
read-only, optional - a missing file just means no cross-reference for
that source) to tell a genuine digital gap apart from one that's owned
some other way: a season with zero digital episodes but a matching
physical or Amazon entry (same tmdb_id) gets covered_physically/
covered_by_amazon: true instead of being reported as a plain gap.
Confirmed real and worth doing for both, not speculative - Futurama's
digital copy only has seasons 10-11 (7 more on physical disc), and
Legion's digital copy only has season 3 (seasons 1-2 bought on Amazon) -
"missing" would have been actively wrong for either without this."""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "app"))
from state_store import load_state, show_key
from atomic_write import atomic_replace
import step_progress
from split_by_year_titles import is_split_by_year_title

from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
PHYSICAL_DB_PATH = os.path.join(DATA_DIR, "physical_db.json")
# Same reasoning as PHYSICAL_DB_PATH above, real case confirmed
# 2026-08-20: Legion's digital copy only has season 3, but seasons 1-2
# are bought on Amazon - without this, those would wrongly read as a
# genuine gap instead of "not downloaded, but not missing either."
AMAZON_DB_PATH = os.path.join(DATA_DIR, "amazon_db.json")
CACHE_PATH = os.path.join(DATA_DIR, "tmdb_season_cache.json")
# {show_key: "YYYY-MM-DD"} - the date a genre-excluded show (Reality/
# Documentary/etc, see EXCLUDED_GENRES/EXCLUDED_TV_TYPES below) started
# being tracked for missing episodes. Recorded ONCE, the first time this
# ran for that show, and never moved forward again - everything the show
# aired before that date stays permanently exempt (no retroactive
# backlog), everything on/after it is tracked exactly like any other
# show. Per the user's explicit call 2026-08-21: excluding an entire
# genre outright was "limiting myself by default" for shows they
# genuinely keep and want gap-tracked (their own example: the Expedition
# Unknown family) - this is the blanket version they chose (every
# currently-excluded show gets this, not a per-show opt-in list).
TRACKING_START_CACHE_PATH = os.path.join(DATA_DIR, "genre_tracking_start.json")

from app_config import tmdb_api_key, TMDB_KEY_HELP  # noqa: E402
API_KEY = tmdb_api_key()
if not API_KEY and __name__ == "__main__":
    print(TMDB_KEY_HELP, file=sys.stderr)
    sys.exit(1)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _looks_like_same_episode(local_name, tmdb_episode_name):
    """Sanity check before trusting a (season, episode)-number lookup against
    whatever tmdb_id the show is CURRENTLY matched to - confirmed real,
    2026-09-26: a show wrongly matched to a near-duplicate TMDB id (Doctor
    Who (2023) briefly resolved to "Doctor Who: Video Commentaries", a real,
    separate, official companion show with its own season 1) silently
    overwrote 5 real episodes' air_date/episode_title with that WRONG
    show's own episode data, purely because the (season, episode) numbers
    happened to coincide - nothing here previously checked that the two
    episodes were actually the same episode. A real release filename always
    carries the episode's own title somewhere in it (scene-release
    convention), so requiring the TMDB name to appear in the local filename,
    both normalized to bare alphanumerics, is a cheap, reliable guard
    against exactly this - it can never block a genuinely correct match
    (the title's real name is always still in the filename), only a wrong
    show's episode being blindly substituted in by position."""
    if not tmdb_episode_name:
        return True  # nothing to compare against - not this check's job to flag that
    local_norm = _NON_ALNUM.sub("", local_name.lower())
    tmdb_norm = _NON_ALNUM.sub("", tmdb_episode_name.lower())
    return bool(tmdb_norm) and tmdb_norm in local_norm


_VIDEO_EXT_RE = re.compile(r"\.(mkv|avi|mp4|m4v|ts|wmv|mov)$", re.I)


def _has_title_signal(local_name, show_title):
    """False for a release that never carries an episode title at all - a
    bare sequential name like "Gordons Great Escape 1.avi" (confirmed real,
    a genre-excluded reality show whose files have always looked like this -
    see this file's own 2026-08-21 comment above about it). There's nothing
    in a name like that FOR _looks_like_same_episode to judge, so it must
    not count as evidence toward a SEASON MISMATCH verdict either way -
    only a release that has real title-shaped text to compare should be
    "checkable" at all. Strips the show's own title and the video
    extension first, since neither is the signal being looked for."""
    local_norm = _NON_ALNUM.sub("", _VIDEO_EXT_RE.sub("", local_name).lower())
    title_norm = _NON_ALNUM.sub("", show_title.lower())
    residual = local_norm.replace(title_norm, "", 1) if title_norm else local_norm
    residual = re.sub(r"\d+", "", residual)  # season/episode/sequence numbers aren't title text either
    return len(residual) >= 3


from app_config import TMDB_API_BASE as BASE  # noqa: E402
SLEEP_BETWEEN_CALLS = 0.12
TODAY_D = date.today()
TODAY = TODAY_D.isoformat()
ONGOING_STATUSES = {"Returning Series", "In Production"}

# Grace period (2026-09-13, at the user's request; centralized 2026-09-14
# into missing_episode_grace.py once track_purged_shows.py/enrich_wanted.py
# needed the same cutoff for their own missing_seasons builds - an episode
# that aired very recently is often still realistically propagating through
# a download queue, so flagging it "missing" the instant it airs is a false
# positive more often than a real gap). Read fresh every run (not cached in
# tmdb_season_cache.json) so an Admin change takes effect on the very next
# refresh.
from missing_episode_grace import load_missing_episode_grace_days, missing_episode_cutoff  # noqa: E402
MISSING_EPISODE_GRACE_DAYS = load_missing_episode_grace_days()
MISSING_EPISODE_CUTOFF = missing_episode_cutoff(TODAY_D)

# Cooking/reality/documentary/talk/news content is watch-and-done, not
# collected to completion - never worth flagging gaps in.
EXCLUDED_TV_TYPES = {"Reality", "Documentary", "Talk Show", "News"}
EXCLUDED_GENRES = {"Reality", "Documentary"}

# Explicit, hand-curated per-show exceptions - not a general "opt out of
# completeness tracking" feature, just the real cases where a show is
# deliberately never collected to completion (what's owned is whatever run
# sounded interesting or happened to be available, not a gap to fill).
# Missing-episode/missing-season tracking simply doesn't apply to it. Lives
# in <data dir>/library_rules.json ("completeness_excluded_tmdb_ids", see
# pipeline/library_rules.py).
from library_rules import completeness_excluded_tmdb_ids  # noqa: E402
COMPLETENESS_EXCLUDED_TMDB_IDS = completeness_excluded_tmdb_ids()

# A show whose only presence is the downloader's transient "this week" queue hasn't
# demonstrated real collecting intent yet - unless it's substantial enough
# (a full season's worth, spans multiple seasons, or is clearly keeping pace
# with a currently-airing season) to suggest it's actually being kept.
MIN_EPISODES_FOR_FULL_SEASON_HEURISTIC = 6
MIN_EPISODES_FOLLOWING_CURRENT_SEASON = 2


def is_genre_excluded(show):
    if show.get("tmdb_tv_type") in EXCLUDED_TV_TYPES:
        return True
    genres = show.get("tmdb_genres") or []
    return any(g in EXCLUDED_GENRES for g in genres)


def is_queue_only_minor(show, owned_by_season):
    from app_config import QUEUE_LABEL
    if any(e["root"] != QUEUE_LABEL for e in show["entries"]):
        return False  # has presence beyond the transient queue, treat normally

    distinct_seasons = len(owned_by_season)
    max_in_one_season = max((len(eps) for eps in owned_by_season.values()), default=0)
    latest_season = show.get("tmdb_number_of_seasons")
    following_current_season = (
        latest_season is not None
        and latest_season in owned_by_season
        and len(owned_by_season[latest_season]) >= MIN_EPISODES_FOLLOWING_CURRENT_SEASON
    )
    substantial = (
        distinct_seasons >= 2
        or max_in_one_season >= MIN_EPISODES_FOR_FULL_SEASON_HEURISTIC
        or following_current_season
    )
    return not substantial


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
            if e.code == 404:
                return None
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


def load_tracking_starts():
    if not os.path.exists(TRACKING_START_CACHE_PATH):
        return {}
    try:
        with open(TRACKING_START_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_tracking_starts(tracking_starts):
    tmp = TRACKING_START_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tracking_starts, f, indent=2, sort_keys=True)
    atomic_replace(tmp, TRACKING_START_CACHE_PATH)


def fetch_season_episodes(tmdb_id, season_num):
    data = api_get(f"/tv/{tmdb_id}/season/{season_num}", {})
    time.sleep(SLEEP_BETWEEN_CALLS)
    episodes = {}
    if data and "episodes" in data:
        for ep in data["episodes"]:
            if ep.get("air_date"):
                episodes[str(ep["episode_number"])] = {
                    "air_date": ep["air_date"],
                    "name": ep.get("name") or f"Episode {ep['episode_number']}",
                }
    return episodes


def get_season_episodes(cache, tmdb_id, season_num, is_currently_airing_season, stats):
    """Cached per-season episode lookup: {episode_number(str): {air_date, name}}.
    A season already known to be finished is cached forever; the current
    season of a still-airing show is always re-fetched, since new episodes
    keep landing there."""
    key = f"{tmdb_id}:{season_num}"
    entry = cache.get(key)
    if entry is not None and entry.get("complete", True):
        stats["from_cache"] += 1
        return entry["episodes"]
    if entry is not None and not is_currently_airing_season:
        # was provisional last time (still airing then); not anymore - trust it as final now
        entry["complete"] = True
        stats["from_cache"] += 1
        return entry["episodes"]

    episodes = fetch_season_episodes(tmdb_id, season_num)
    cache[key] = {
        "episodes": episodes,
        "complete": not is_currently_airing_season,
        "fetched_at": datetime.now().isoformat(),
    }
    stats["fetched"] += 1
    return episodes


def load_physical_seasons_by_tmdb_id():
    """{tmdb_id: {season numbers owned physically}} - built directly from
    physical_db.json's own entries[].season (already correctly excludes
    any season enrich_physical.py determined is also digital, see that
    script's per-season overlap handling), so no re-deriving of overlap
    logic needed here."""
    if not os.path.exists(PHYSICAL_DB_PATH):
        return {}
    with open(PHYSICAL_DB_PATH, encoding="utf-8") as f:
        physical = json.load(f)
    out = {}
    for s in physical:
        if s.get("type") != "TV" or not s.get("tmdb_id"):
            continue
        seasons = {e["season"] for e in s["entries"] if e.get("season") is not None}
        out.setdefault(s["tmdb_id"], set()).update(seasons)
    return out


def load_amazon_seasons_by_tmdb_id():
    """Same as load_physical_seasons_by_tmdb_id() above, for
    amazon_db.json instead - enrich_amazon.py's own per-season overlap
    handling already excludes anything also digital, same as physical's."""
    if not os.path.exists(AMAZON_DB_PATH):
        return {}
    with open(AMAZON_DB_PATH, encoding="utf-8") as f:
        amazon = json.load(f)
    out = {}
    for s in amazon:
        if s.get("type") != "TV" or not s.get("tmdb_id"):
            continue
        seasons = {e["season"] for e in s["entries"] if e.get("season") is not None}
        out.setdefault(s["tmdb_id"], set()).update(seasons)
    return out


def main():
    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    physical_seasons_by_tmdb_id = load_physical_seasons_by_tmdb_id()
    amazon_seasons_by_tmdb_id = load_amazon_seasons_by_tmdb_id()
    cache = load_cache()
    tracking_starts = load_tracking_starts()
    stats = {"fetched": 0, "from_cache": 0}
    state = load_state()
    retention = state.get("retention", {})
    # Manual per-show "mark complete" override (Admin -> Fix a title,
    # 2026-09-13) - the escape hatch for whenever the automatic rules above
    # get a show wrong anyway. Deliberately the FIRST thing checked below,
    # ahead of even COMPLETENESS_EXCLUDED_TMDB_IDS - a real, current
    # decision the user just made in the UI should always outrank a
    # hardcoded list or a content-type guess, no exceptions.
    missing_episode_overrides = state.get("missing_episode_overrides", {})

    tv_shows = [s for s in shows if s["type"] == "TV" and s.get("tmdb_matched") and s.get("tmdb_id")]

    checked = 0
    incomplete = 0
    genre_excluded = 0
    queue_only_minor = 0
    delete_after_watch_excluded = 0
    completeness_excluded_by_id = 0
    manually_marked_complete = 0
    no_dateable_owned = 0

    for s in shows:
        s["missing_episodes"] = []
        s["missing_seasons"] = []
        s["episodes_expected_so_far"] = None
        s["assumed_physical_media"] = False
        s["completeness_skip_reason"] = None
        s["tracking_start_date"] = None
        # Real physical holdings for this exact tmdb_id, regardless of
        # whether this show even reaches completeness tracking below (a
        # genre-excluded or delete-after-watch show can still genuinely
        # have physical seasons worth knowing about).
        s["physical_seasons_owned"] = sorted(physical_seasons_by_tmdb_id.get(s.get("tmdb_id"), set()))
        s["amazon_seasons_owned"] = sorted(amazon_seasons_by_tmdb_id.get(s.get("tmdb_id"), set()))

    for i, s in enumerate(tv_shows):
        step_progress.report(i + 1, len(tv_shows), "shows", s["title"])
        owned_by_season = {}
        for e in s["entries"]:
            if e["season"] is not None:
                owned_by_season.setdefault(e["season"], set()).add(e["episode"])

        if not owned_by_season:
            continue

        # queue_only_minor skips Pass 1 entirely (below) - deliberately,
        # since it's specifically about not wasting a TMDB season fetch on
        # something barely started that's likely to change shape soon.
        # genre/delete-after-watch exclusion is different: that content is
        # typically fully downloaded and stable, just never tracked for
        # completeness by design - there's no reason those owned episodes
        # shouldn't still show their real titles, so those two skip only
        # the completeness computation (Pass 2/3), not Pass 1's tagging.
        if is_queue_only_minor(s, owned_by_season):
            s["completeness_skip_reason"] = "queue_only_minor"
            s["assumed_physical_media"] = True
            queue_only_minor += 1
            continue

        # Same id-keyed-alone staleness risk tmdb_id_override used to have
        # (MasterChef US, 2026-08-20) - an ongoing show's forced re-search
        # can land on a different id than whatever this override was saved
        # under, silently dropping a real "mark complete" decision on the
        # next refresh with no visible sign it happened. Falls back to a
        # title-keyed key, year-qualified for a split-by-year title (see
        # split_by_year_titles.py) for the same cross-era-bleed reason
        # tmdb_id_override's own title key needed qualifying (2026-09-26).
        title_lookup_key = (
            f"title:TV:{s['title']}:{s['years'][0]}" if is_split_by_year_title(s["title"]) and s.get("years")
            else f"title:TV:{s['title']}"
        )
        missing_complete_flag = (
            missing_episode_overrides.get(show_key(s))
            or missing_episode_overrides.get(title_lookup_key)
        )

        skip_completeness = None
        if missing_complete_flag:
            # Manual override wins over everything else, unconditionally -
            # see its own comment above where missing_episode_overrides is
            # loaded.
            s["completeness_skip_reason"] = "manually_marked_complete"
            s["assumed_physical_media"] = True
            manually_marked_complete += 1
            skip_completeness = "manually_marked_complete"
        elif s.get("tmdb_id") in COMPLETENESS_EXCLUDED_TMDB_IDS:
            # Explicit per-show exception, takes priority over genre/
            # retention-based skipping below - see the constant's own
            # comment for why.
            s["completeness_skip_reason"] = "not_collected_to_completion"
            s["assumed_physical_media"] = True
            skip_completeness = "not_collected_to_completion"
            completeness_excluded_by_id += 1
        elif retention.get(show_key(s)) == "delete_after_watch":
            # Checked BEFORE genre exclusion, as its own independent
            # branch, not sharing an elif with it - the user's own
            # explicit delete_after_watch retention is a stronger signal
            # than a content-type guess and must always win outright, even
            # for a show that also happens to be genre-excluded (Reality,
            # Documentary, etc). Confirmed real bug 2026-09-13: Deadliest
            # Catch (tmdb_tv_type "Reality") kept showing "missing
            # episodes" despite being set to delete_after_watch, because
            # is_genre_excluded() used to be checked first in this same
            # elif chain and silently swallowed this branch - genre
            # exclusion falls through to bounded tracking (see below),
            # retention exclusion does not fall through to anything.
            s["completeness_skip_reason"] = "delete_after_watch"
            s["assumed_physical_media"] = True
            delete_after_watch_excluded += 1
            skip_completeness = "delete_after_watch"
        elif is_genre_excluded(s):
            # No longer a hard skip (see TRACKING_START_CACHE_PATH above) -
            # falls through to Pass 2/3 like any other show, just gated by
            # this show's own tracking_start_date once assigned below, so
            # it never retroactively floods with a backlog of everything
            # aired before tracking began. Only reached for a genre-
            # excluded show that ISN'T also delete_after_watch - that
            # stronger, explicit signal above always wins first.
            key = show_key(s)
            start_date = tracking_starts.get(key)
            if start_date is None:
                start_date = TODAY
                tracking_starts[key] = start_date
            s["assumed_physical_media"] = True
            s["tracking_start_date"] = start_date
            genre_excluded += 1

        tmdb_id = s["tmdb_id"]
        status = s.get("tmdb_status")
        latest_season_num = s.get("tmdb_number_of_seasons") or 0
        is_ongoing = status in ONGOING_STATUSES

        # Pass 1: fetch owned seasons, tag air_date/episode_title onto
        # owned entries (real display info for EVERY matched show, tracked
        # for completeness or not - confirmed real gap: "Gordon's Great
        # Escape", genre-excluded as Reality/Documentary, had all 3 owned
        # episodes sitting with no real title at all until this ran for
        # excluded shows too, 2026-08-21), find the latest date among
        # episodes actually owned (the window anchor for completeness
        # shows only continue past this point).
        last_owned_air_date = None
        owned_season_data = {}
        for season_num in owned_by_season:
            if season_num == 0:
                continue  # TMDB's "specials" bucket is noisy, not tracked
            is_current = is_ongoing and season_num == latest_season_num
            episodes = get_season_episodes(cache, tmdb_id, season_num, is_current, stats)
            owned_season_data[season_num] = episodes
            owned_eps = owned_by_season[season_num]
            season_entries = [e for e in s["entries"] if e["season"] == season_num and e["episode"] in owned_eps]

            # Aggregate sanity check, not per-episode (confirmed real,
            # 2026-09-26 - see _looks_like_same_episode's own docstring for
            # the incident this guards against): a single oddly-named local
            # release - a NORDiC/regional scene group that drops the
            # episode title entirely, a multi-episode combo file
            # ("S01E17E18"), a deliberately-different cut - is normal
            # release-naming noise, not evidence of a wrong show, and
            # flagging those individually was a real false-positive
            # (confirmed live: House of the Dragon's NORDiC releases,
            # Modern Family's combined finale, Mythic Quest's
            # ALTERNATIVE.CUT episode all wrongly flagged on a per-episode
            # basis even though the show match was correct). Only distrust
            # this season's data as a whole when NONE of its checkable
            # episodes' TMDB names appear anywhere locally - that's the
            # actual signature of a wrong show entirely, which a real
            # correct match essentially never produces (some normally-named
            # release is always in the mix).
            checkable = [(e, episodes.get(str(e["episode"]))) for e in season_entries]
            checkable = [
                (e, ep) for e, ep in checkable
                if ep and ep.get("name") and _has_title_signal(e.get("name") or "", s["title"])
            ]
            if checkable and not any(_looks_like_same_episode(e.get("name") or "", ep["name"]) for e, ep in checkable):
                print(
                    f"  SEASON MISMATCH: {s['title']!r} season {season_num} - none of its "
                    f"{len(checkable)} checkable episodes' TMDB names appear anywhere in the "
                    f"matching local filenames (tmdb:{tmdb_id}) - air_date/title NOT overwritten "
                    f"for this season, likely a wrong show match (fix via Admin panel's \"Fix a title\").",
                    file=sys.stderr,
                )
                continue

            for e in season_entries:
                ep_data = episodes.get(str(e["episode"]))
                if ep_data:
                    e["air_date"] = ep_data["air_date"]
                    e["episode_title"] = ep_data["name"]
                    # An owned episode can have a real TMDB entry with no
                    # air_date at all (confirmed real, not hypothetical -
                    # this crashed here 2026-08-21: None > "2010-01-18"
                    # raises TypeError once a dateable episode had
                    # already set last_owned_air_date first). An unknown
                    # date can't be "later" than a known one, so it just
                    # doesn't move the anchor - never crash comparing
                    # against it.
                    if ep_data["air_date"] and (last_owned_air_date is None or ep_data["air_date"] > last_owned_air_date):
                        last_owned_air_date = ep_data["air_date"]

        if skip_completeness:
            continue  # titles/dates tagged above; completeness tracking not wanted for this show

        if last_owned_air_date is None:
            no_dateable_owned += 1
            checked += 1
            continue  # nothing dateable owned yet (e.g. brand new match) - nothing to compare against

        # tracking_start_date (genre-excluded shows only, set above) is a
        # lower bound on top of the existing upper bound (TODAY) - an
        # episode that aired before tracking began is exactly as exempt as
        # one that hasn't aired yet, so the SAME "not eligible to be
        # flagged" treatment applies to both, just from the other
        # direction. None for every normal show, so this changes nothing
        # about the existing no-cutoff behavior for anything that was
        # always fully tracked.
        min_air_date = s.get("tracking_start_date")

        # Pass 2: missing episodes within owned seasons - every aired
        # episode not owned, full stop, no recency cutoff (see enrich_tv_coverage
        # module docstring - completeness is tracked back to season 1 for
        # every show that reaches this point, EXCEPT bounded below by
        # min_air_date for a genre-excluded show that's opted into
        # tracking - see above).
        missing = []
        expected_total = 0
        for season_num, episodes in owned_season_data.items():
            owned_eps = owned_by_season[season_num]
            for ep_str, ep_data in episodes.items():
                air_date = ep_data["air_date"]
                # A real TMDB episode entry with no air_date at all (not
                # just falsy - genuinely None) is exactly as unconfirmed as
                # one dated in the future - same "don't flag it as missing
                # until there's a real date to expect it by" treatment,
                # not a crash comparing None > MISSING_EPISODE_CUTOFF. The
                # cutoff (not bare TODAY) is the grace-period fix - a
                # just-aired episode is still realistically propagating
                # through downloads, not a real gap yet.
                if air_date is None or air_date > MISSING_EPISODE_CUTOFF:
                    continue  # not aired yet, or aired too recently to count
                if min_air_date and air_date < min_air_date:
                    continue  # aired before tracking began - never a backlog
                expected_total += 1
                ep_num = int(ep_str)
                if ep_num in owned_eps:
                    continue
                missing.append({
                    "season": season_num, "episode": ep_num,
                    "name": ep_data["name"], "air_date": air_date,
                })

        # Pass 3: entirely unowned seasons, same no-cutoff rule (bounded
        # below by min_air_date the same way as Pass 2)
        missing_seasons = []
        for season_num in range(1, latest_season_num + 1):
            if season_num in owned_by_season:
                continue
            is_current = is_ongoing and season_num == latest_season_num
            episodes = get_season_episodes(cache, tmdb_id, season_num, is_current, stats)
            # Same None-air_date guard as Pass 2 above - a real TMDB episode
            # entry can have no air_date at all, not just a future one.
            # Carries the per-episode detail (not just a count) since
            # A companion tool asked for it - it was building a second,
            # independent TMDB season lookup just to expand this exact same
            # data we're already sitting on here. Same shape as the `missing` list above
            # (Pass 2) on purpose - one episode-detail shape across this
            # whole file, not two.
            aired = [
                {"episode": int(ep_str), "name": ep["name"], "air_date": ep["air_date"]}
                for ep_str, ep in episodes.items()
                # <= MISSING_EPISODE_CUTOFF (not bare TODAY) - same
                # grace-period reasoning as Pass 2 above.
                if ep["air_date"] is not None and ep["air_date"] <= MISSING_EPISODE_CUTOFF
                and (not min_air_date or ep["air_date"] >= min_air_date)
            ]
            aired.sort(key=lambda e: e["episode"])
            if aired:
                missing_seasons.append({
                    "season": season_num,
                    "aired_episode_count": len(aired),
                    "total_episode_count": len(episodes),
                    "first_air_date": min(ep["air_date"] for ep in aired),
                    "episodes": aired,
                    # A digital gap, but not an ACTUAL gap - this exact
                    # season is sitting on the shelf physically (see
                    # physical_seasons_owned above / pipeline/
                    # enrich_physical.py). Still reported, just no longer
                    # misrepresented as something owned nowhere.
                    "covered_physically": season_num in physical_seasons_by_tmdb_id.get(tmdb_id, set()),
                    "covered_by_amazon": season_num in amazon_seasons_by_tmdb_id.get(tmdb_id, set()),
                })

        missing.sort(key=lambda m: (m["season"], m["episode"]))
        missing_seasons.sort(key=lambda m: m["season"])
        s["missing_episodes"] = missing
        s["missing_seasons"] = missing_seasons
        s["episodes_expected_so_far"] = expected_total
        checked += 1
        if missing or missing_seasons:
            incomplete += 1

        if (i + 1) % 20 == 0:
            print(f"...{i + 1}/{len(tv_shows)} shows checked", file=sys.stderr)
            save_cache(cache)

    # Season-0 (specials) date tagging - deliberately separate from and
    # unconditional on the completeness-tracking gates above (genre
    # exclusion, queue-only-minor, "no dateable owned content yet"): this
    # is a display/sort concern (README roadmap - specials sorted by
    # release date), not completeness tracking, so it applies to every TV
    # show with an owned season-0 entry, not just shows tracked to
    # completion. Season 0 itself stays excluded from missing-episode/
    # missing-season detection above - "TMDB's specials bucket is noisy"
    # is still true for THAT purpose, just not for simply wanting a date.
    season0_dated = 0
    season0_fell_back = 0
    for s in tv_shows:
        season0_entries = [e for e in s["entries"] if e["season"] == 0]
        if not season0_entries:
            continue
        episodes = get_season_episodes(cache, s["tmdb_id"], 0, False, stats)
        # Same aggregate-not-per-episode reasoning as the main season loop
        # above - a single oddly-named special (no title in the filename,
        # a combo file) is normal noise, not a wrong-show signal.
        checkable = [(e, episodes.get(str(e["episode"])) if e.get("episode") is not None else None) for e in season0_entries]
        checkable = [
            (e, ep) for e, ep in checkable
            if ep and ep.get("name") and _has_title_signal(e.get("name") or "", s["title"])
        ]
        season_mismatch = checkable and not any(_looks_like_same_episode(e.get("name") or "", ep["name"]) for e, ep in checkable)
        if season_mismatch:
            print(
                f"  SEASON MISMATCH: {s['title']!r} specials - none of its {len(checkable)} "
                f"checkable episodes' TMDB names appear anywhere in the matching local filenames "
                f"(tmdb:{s['tmdb_id']}) - air_date/title NOT overwritten, likely a wrong show match "
                f"(fix via Admin panel's \"Fix a title\").",
                file=sys.stderr,
            )
        for e in season0_entries:
            ep_data = episodes.get(str(e["episode"])) if e.get("episode") is not None else None
            if season_mismatch:
                continue
            if ep_data and ep_data.get("air_date"):
                e["air_date"] = ep_data["air_date"]
                if ep_data.get("name"):
                    e["episode_title"] = ep_data["name"]
                season0_dated += 1
            elif not e.get("air_date"):
                e["air_date"] = e.get("mtime")  # no real TMDB date - fall back to file date so sorting still has something
                season0_fell_back += 1

    # Atomic write - see scan_media.py's own copy of this comment
    # (2026-08-21): web_server.py reads this file on every page load, so a
    # direct write here can hand a concurrent reader a torn/truncated file.
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2)
    atomic_replace(tmp, DB_PATH)
    save_cache(cache)
    save_tracking_starts(tracking_starts)

    print(f"TV coverage: checked {checked}/{len(tv_shows)} matched shows for full completeness back to season 1 (no recency cutoff).")
    print(f"{genre_excluded} cooking/reality/documentary/talk/news shows tracked from their own start date forward (no backlog before it).")
    print(f"{delete_after_watch_excluded} shows skipped (retention set to Delete after watching - never tracked to completion).")
    print(f"{manually_marked_complete} show(s) skipped (manually marked complete via Admin).")
    print(f"Missing-episode grace period: {MISSING_EPISODE_GRACE_DAYS} day(s) (Admin-adjustable).")
    print(f"{queue_only_minor} shows skipped (not enough collected yet, only sitting in the download queue).")
    if completeness_excluded_by_id:
        print(f"{completeness_excluded_by_id} show(s) skipped (explicit per-show exception - see COMPLETENESS_EXCLUDED_TMDB_IDS).")
    if no_dateable_owned:
        print(f"{no_dateable_owned} shows had no dateable owned episodes yet (nothing to compare against).")
    print(f"Season fetches: {stats['fetched']} new, {stats['from_cache']} served from cache.")
    print(f"Season-0 specials dated: {season0_dated} from TMDB, {season0_fell_back} fell back to file date.")
    print(f"{incomplete} shows have missing aired episodes and/or entirely missing seasons:")
    for s in tv_shows:
        if s["missing_episodes"] or s["missing_seasons"]:
            parts = []
            if s["missing_seasons"]:
                seasons = ", ".join(f"S{m['season']:02d} ({m['aired_episode_count']} eps)" for m in s["missing_seasons"])
                parts.append(f"whole season(s) missing: {seasons}")
            if s["missing_episodes"]:
                eps = ", ".join(f"S{m['season']:02d}E{m['episode']:02d}" for m in s["missing_episodes"][:8])
                more = f" (+{len(s['missing_episodes']) - 8} more)" if len(s["missing_episodes"]) > 8 else ""
                parts.append(f"episodes missing: {eps}{more}")
            print(f"  - {s['title']}: " + "; ".join(parts))


if __name__ == "__main__":
    main()

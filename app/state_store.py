"""Reads/writes data\\user_state.json (watched, retention, retention
overrides, Watch List, travel selection) - the only sanctioned way to touch
that file, via load_state()/update_state()/prune_state() (thread-safe
read-modify-write). Also defines show_key(), the stable cross-refresh show
identity (TMDB id when matched, title+type fallback otherwise) used
throughout the app."""
import json
import os
import sys
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "pipeline"))
from app_config import DATA_DIR  # noqa: E402
STATE_PATH = os.path.join(DATA_DIR, "user_state.json")

# atomic_replace() lives in pipeline\ (not duplicated here) - see that
# module's own docstring for why a shared function beats yet another
# copy-pasted retry loop. This is the highest-traffic writer of all -
# every single state-changing API call in web_server.py goes through
# save_state()/update_state() below.
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "pipeline"))
from atomic_write import atomic_replace  # noqa: E402

TOP10_PEOPLE = ("you", "partner")
TOP10_CAP = 10
RECENTLY_WATCHED_CAP = 12

_lock = threading.Lock()


def default_state():
    return {
        "watched": {}, "retention": {}, "retention_overrides": {},
        "top10": {p: [] for p in TOP10_PEOPLE},
        "no_interest": {p: [] for p in TOP10_PEOPLE},
        "recently_watched": [],
        "travel_selection": [],
        "travel_last_destination": "",
        "overrides": {},
        # Manual "mark complete" override (2026-09-13) - a show_key -> True
        # map, checked as the highest-priority skip reason in
        # pipeline/enrich_tv_coverage.py, ahead of every automatic rule.
        # Absence means "no override" (not tracked here at all), same
        # shape as retention_overrides above.
        "missing_episode_overrides": {},
        # Per-show opt-IN (2026-09-17, correcting a wrong default) - a
        # show_key -> True map. delete_after_watch means "watch once, then
        # it's gone on purpose" - the episodes purged before now were
        # already watched and deliberately discarded, not a backlog to
        # re-chase. pipeline/track_purged_shows.py's synthetic placeholder
        # therefore only tracks episodes airing from the point it first went
        # zero-owned forward, by default - same bounded-tracking shape
        # enrich_tv_coverage.py already uses for genre-excluded shows
        # (tracking_start_date). Setting a show's key to True here opts
        # that ONE show back into the old unbounded behavior (track the
        # full historical backlog too), for the rarer case where the user
        # actually does want the companion tool to fill old gaps despite deleting
        # after each watch. Absence means "no override" - bounded (new
        # default), same shape as missing_episode_overrides above.
        "purged_backlog_tracking": {},
    }


def load_state():
    if not os.path.exists(STATE_PATH):
        return default_state()
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return default_state()
    data.setdefault("watched", {})
    data.setdefault("retention", {})
    data.setdefault("retention_overrides", {})
    data.setdefault("top10", {})
    for p in TOP10_PEOPLE:
        data["top10"].setdefault(p, [])
    data.setdefault("no_interest", {})
    for p in TOP10_PEOPLE:
        data["no_interest"].setdefault(p, [])
    data.setdefault("recently_watched", [])
    data.setdefault("travel_selection", [])
    data.setdefault("travel_last_destination", "")
    data.setdefault("missing_episode_overrides", {})
    data.setdefault("purged_backlog_tracking", {})
    # Admin-panel corrections (wrong TMDB match, custom poster) - open-ended
    # per show_key(), see web_server.py's /api/override. Applied at render
    # time in render_engine.py's render_page() and, for a re-matched
    # tmdb_id specifically, read authoritatively by enrich_tmdb.py on its
    # next run (replaces the old hardcoded MANUAL_TMDB_ID_OVERRIDES dict -
    # a correction made through the Admin panel never needs a code edit).
    data.setdefault("overrides", {})
    return data


def promote_recently_watched(state, key):
    """MRU-ordered, capped, append-don't-replace: a show just watched moves
    to #1; re-watching an already-listed show promotes it back to #1
    rather than duplicating it. Called only on watched=true (not on
    unwatch/restart) - the point is a history of what was watched, not a
    live reflection of current watched-state (that's resolve_watched()'s
    job). A show later deleted from the library is pruned the same way
    top10/no_interest already are - see prune_state()."""
    current = state.setdefault("recently_watched", [])
    state["recently_watched"] = ([key] + [k for k in current if k != key])[:RECENTLY_WATCHED_CAP]


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    atomic_replace(tmp, STATE_PATH)


def update_state(mutator):
    """mutator(state) -> None, mutates the dict in place. Thread-safe read-modify-write."""
    with _lock:
        state = load_state()
        mutator(state)
        save_state(state)
        return state


def show_key(show):
    """Stable identity for a show across refreshes: prefer TMDB id (survives
    title/consolidation changes), fall back to title+type when unmatched.
    A physical-collection or Amazon-purchase entry (pipeline/
    enrich_physical.py / enrich_amazon.py) gets a ":physical"/":amazon"
    suffix when it shares a tmdb_id+type with a real digital show - this
    happens for real (a TV show partially owned across sources, e.g.
    Futurama: digital has seasons 10-11, physical has 1-5; Legion: digital
    has season 3, Amazon has 1-2 - each source's card is rendered
    separately) and without the suffix the cards silently share retention/
    top10/no-interest state despite being separate list items. Confirmed
    real, not hypothetical: digital Legion and the Amazon Legion entry
    collided on the bare key before this fix (2026-08-20)."""
    if show.get("tmdb_id"):
        if show.get("physical_collection"):
            suffix = ":physical"
        elif show.get("is_amazon_purchase"):
            suffix = ":amazon"
        elif show.get("is_streaming"):
            # :netflix / :prime - a followed streaming title carries its own
            # watched / watch-list / retention state, separate from a real
            # digital copy of the same show (pipeline/enrich_streaming.py).
            suffix = ":" + (show.get("streaming_provider") or "streaming").split("_")[0]
        else:
            suffix = ""
        return f"tmdb:{show['type']}:{show['tmdb_id']}{suffix}"
    return f"title:{show['type']}:{show['title']}"


def prune_state(known_paths, known_show_keys):
    """Drop watched/retention/top10/travel-selection entries for paths or
    shows that no longer exist (e.g. after files were actually deleted), so
    the state file doesn't grow stale forever. Called during each refresh."""
    def mutator(state):
        state["watched"] = {p: v for p, v in state["watched"].items() if p in known_paths}
        state["retention"] = {k: v for k, v in state["retention"].items() if k in known_show_keys}
        state["retention_overrides"] = {p: v for p, v in state.get("retention_overrides", {}).items() if p in known_paths}
        for person in TOP10_PEOPLE:
            state["top10"][person] = [k for k in state["top10"].get(person, []) if k in known_show_keys]
            state["no_interest"][person] = [k for k in state.get("no_interest", {}).get(person, []) if k in known_show_keys]
        state["travel_selection"] = [p for p in state.get("travel_selection", []) if p in known_paths]
        state["recently_watched"] = [k for k in state.get("recently_watched", []) if k in known_show_keys]
        # Same "only prune when the show is TRULY gone" rule as retention
        # above - a purged_tracked placeholder (pipeline/track_purged_
        # shows.py) still counts as known, so marking a show complete and
        # then purging its last file doesn't lose the override. Title-keyed
        # entries (title:TV:.../title:Movie:..., added 2026-09-26 for the
        # same reason tmdb_id_override's own title-keyed twin exists -
        # confirmed real: this dict was id-keyed only, so an ongoing show's
        # forced re-search landing on a different id silently dropped a
        # real "mark complete" decision) are exempted the same permanent
        # way overrides' own title_keyed entries are just below - a
        # title-based key can never equal a matched show's show_key(), so
        # it would never survive this filter on its own merit otherwise.
        state["missing_episode_overrides"] = {
            k: v for k, v in state.get("missing_episode_overrides", {}).items()
            if k in known_show_keys or k.startswith(("title:TV:", "title:Movie:"))
        }
        # Same "only prune when truly gone" rule as the override just above.
        state["purged_backlog_tracking"] = {
            k: v for k, v in state.get("purged_backlog_tracking", {}).items() if k in known_show_keys
        }
        # A tmdb_id_override that the pipeline has since confirmed changes
        # the show's own show_key() (computed from its now-corrected
        # tmdb_id) - the override under the OLD key is naturally stale at
        # that point and self-cleans here, same as a deleted show would.
        #
        # force_unmatched and runtime_confirmed are NOT eligible for this
        # self-cleaning: neither one is ever meant to produce a matched
        # tmdb_id that would give the show a new, different show_key() -
        # force_unmatched's whole point is staying unmatched forever, and a
        # runtime_confirmed movie keeps its ORIGINAL matched id (nothing
        # about it changes). Both stay title-keyed permanently, so pruning
        # them the instant this pipeline's known_show_keys snapshot doesn't
        # happen to include that exact title (e.g. it's mid-refresh, or ran
        # from a media_db.json snapshot slightly out of sync with a
        # concurrent hand-edit) silently destroys a fix with no self-cache
        # to fall back on - confirmed real: "Collusion"'s force_unmatched
        # override vanished this way and the wrong match came right back,
        # 2026-08-21.
        #
        # auto_resolved_via (pipeline/fallback_identify.py, via
        # persist_fallback_override() in enrich_tmdb.py) has the SAME
        # self-cleaning problem as a normal tmdb_id_override, but never
        # actually gets to use the self-clean path: it's always saved
        # title-keyed (there's no "current wrong id" to key it by - the
        # whole point is TMDB's own search found nothing at all), and the
        # resulting show's show_key() becomes tmdb-id-based the moment it
        # resolves, which will never again equal that title-based key. Left
        # unexempted, this would get pruned on the very next refresh, every
        # time, for every fallback-resolved title, permanently - not a
        # one-off race like the two above, a guaranteed loss every run.
        # title_keyed (2026-08-26, /api/override) is the same shape of
        # permanent exemption as the three above and for the same reason:
        # a title-based override key can never equal a matched show's own
        # show_key() (that's always tmdb-id-based once matched), so it
        # would never naturally survive this filter on its own merit -
        # it's meant to persist forever as the durable fallback an ongoing
        # show's perpetual re-search needs, not to self-clean once "no
        # longer needed" the way a normal id-keyed override correctly does.
        state["overrides"] = {
            k: v for k, v in state.get("overrides", {}).items()
            if k in known_show_keys or v.get("force_unmatched") or v.get("runtime_confirmed")
            or v.get("auto_resolved_via") or v.get("title_keyed")
        }
    return update_state(mutator)

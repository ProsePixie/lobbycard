"""Shared "this title is actually multiple unrelated productions" registry -
previously a local copy in scan_media.py alone (SPLIT_BY_YEAR_TITLES +
_load_split_by_year_titles()), factored out 2026-09-26 after that drift
caused a real, serious bug: enrich_tmdb.py's title-keyed override fallback
(for "Fix a title" on an ongoing show, see its own override_lookup_key
comment) keys purely on {type}:{title}, with no year - fine for a single
show, but for a split-by-year title, Doctor Who's three real, distinct eras
(1963/2005/2023) share that exact same title. Fixing one era's wrong TMDB
match via the title-keyed override silently reapplied that SAME id to the
other two eras on the next refresh, and consolidate.py's merge_by_tmdb_id
then merged all three into one garbled show (confirmed real, 2026-09-26).

The fix: any title-keyed override for a split-by-year title must itself be
year-qualified (title:{type}:{title}:{year}), so it can only ever apply to
the one era it was actually written for. This module is the single source
of truth both scan_media.py (deciding whether to bucket by year at all)
and web_server.py/enrich_tmdb.py (deciding whether to year-qualify an
override key) now import from - never duplicate this set again."""
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from app_config import DATA_DIR  # noqa: E402
STATE_PATH = os.path.join(DATA_DIR, "user_state.json")

_MULTISPACE = re.compile(r"\s+")


def _normalize_key(title):
    """Deliberately its own copy, not imported from scan_media.py/
    consolidate.py's normalize_key() - this module has to be importable
    FROM scan_media.py, so importing the other way would be a cycle. Same
    tiny-pure-function duplication convention this project already uses
    elsewhere (e.g. consolidate.py's quality_label() mirroring
    scan_media.py's own copy) - keep them in sync deliberately if either
    ever changes."""
    key = re.sub(r"[^a-z0-9]+", " ", title.lower())
    key = _MULTISPACE.sub(" ", key).strip()
    key = re.sub(r"\b(\w+) s\b", r"\1s", key)
    return key


SPLIT_BY_YEAR_TITLES = {"the stand", "doctor who"}

_cache = None


def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _admin_set_titles():
    """User-set additions via the Admin panel's "These are different,
    unrelated productions" button (app/web_server.py's /api/override, field
    split_by_year) - same title-keyed-override storage as everything else
    in state["overrides"], just this one specific boolean flag."""
    global _cache
    if _cache is None:
        state = _load_state()
        _cache = set()
        for key, override in state.get("overrides", {}).items():
            if not override.get("split_by_year"):
                continue
            for prefix in ("title:TV:", "title:Movie:"):
                if key.startswith(prefix):
                    _cache.add(_normalize_key(key[len(prefix):]))
    return _cache


def is_split_by_year_title(title):
    """Accepts a raw (not pre-normalized) title - normalizes internally, so
    every caller compares the same way regardless of whatever casing/
    punctuation their own copy of the title happens to be in."""
    norm = _normalize_key(title)
    return norm in SPLIT_BY_YEAR_TITLES or norm in _admin_set_titles()


def reset_cache():
    """Test/long-running-process hook - state's admin-set split_by_year
    titles are cached for the life of whatever process imported this
    module (matches scan_media.py's own prior per-run caching), but
    enrich_tmdb.py and web_server.py are longer-lived than a single
    pipeline run, so anything that needs a just-written override to be
    visible immediately (the /api/override route itself) should call this
    right after saving."""
    global _cache
    _cache = None

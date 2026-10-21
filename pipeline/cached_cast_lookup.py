"""Reuses cast/crew data enrich_tmdb.py already fetched and cached for any
title it has matched, so a show that's since left the library
(purged_tracked) or been added to Wanted can show cast/crew on its own
placeholder card without a second TMDB request for the same data
enrich_tmdb.py's own tmdb_cache.json already holds (extending the "every
poster should be cached" rule to cast).

Indexed by tmdb_id/type, since track_purged_shows.py and enrich_wanted.py
work from a bare tmdb_id at this point, not the title/year enrich_tmdb.py's
own cache is keyed by. A title never matched before (rare for a wanted
title added by hand, impossible for purged_tracked - owning it once is how
it became purged) simply isn't in here; callers get an explicit cache miss
back, not an error, and their placeholder card just stays cast-less, same
as before this module existed."""
import json
import os

from app_config import DATA_DIR  # noqa: E402
TMDB_CACHE_PATH = os.path.join(DATA_DIR, "tmdb_cache.json")

# Built once per process, not once per show looked up - tmdb_cache.json is
# a flat title:type:year-keyed dict with one entry per library title ever
# matched, so re-scanning it per-show would be O(n*m) for no reason.
_by_tmdb_id = None


def _load_index():
    global _by_tmdb_id
    if _by_tmdb_id is not None:
        return _by_tmdb_id
    _by_tmdb_id = {}
    try:
        with open(TMDB_CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _by_tmdb_id  # no cache file yet, or unreadable - every lookup is just a miss
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        tmdb_id = entry.get("tmdb_id")
        typ = entry.get("resolved_type")
        if tmdb_id and typ and entry.get("tmdb_cast"):
            _by_tmdb_id[(typ, tmdb_id)] = entry
    return _by_tmdb_id


def cached_cast_crew(typ, tmdb_id):
    """Returns (tmdb_cast, tmdb_director, tmdb_creators) from enrich_tmdb.py's
    own cache if this exact title/type was matched before, else
    (None, None, None) - a genuine cache miss."""
    entry = _load_index().get((typ, tmdb_id))
    if not entry:
        return None, None, None
    return entry.get("tmdb_cast"), entry.get("tmdb_director"), entry.get("tmdb_creators")

"""
Hand-curated, per-library exceptions, read from <data dir>/library_rules.json.

The matcher and the scanner are general; a few decisions are not, because they
are about one particular collection ("this movie belongs inside that show's
season list", "never track gaps for that show"). Those live in a data file the
owner edits, so the code stays free of anyone's library. Every key is
optional and a missing or malformed file means "no exceptions".

    {
      "movie_to_tv_merge": {"<movie tmdb id>": [<tv tmdb id>, <season slot>]},
      "completeness_excluded_tmdb_ids": {"<tv tmdb id>": "why"},
      "manual_covers": [["<title substring>", "<image url>"]]
    }

movie_to_tv_merge puts an owned movie inside a TV show's season list. The
season slot must be fractional (for example 2.5 = between seasons 2 and 3):
real TMDB season numbers are whole, so it can never collide with real data.
"""
import json
import os

from app_config import DATA_DIR

RULES_PATH = os.path.join(DATA_DIR, "library_rules.json")


def _load():
    try:
        with open(RULES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_RULES = _load()


def movie_to_tv_merge():
    out = {}
    for movie_id, target in (_RULES.get("movie_to_tv_merge") or {}).items():
        try:
            out[int(movie_id)] = (int(target[0]), float(target[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def completeness_excluded_tmdb_ids():
    out = {}
    for tmdb_id, why in (_RULES.get("completeness_excluded_tmdb_ids") or {}).items():
        try:
            out[int(tmdb_id)] = str(why)
        except (TypeError, ValueError):
            continue
    return out


def manual_covers():
    return [(str(t), str(u)) for t, u in (_RULES.get("manual_covers") or []) if t and u]

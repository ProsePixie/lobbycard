"""Matches every TV show against epguides.com's own published show index
(https://epguides.com/common/allshows.txt - title, directory slug, tvrage/
TVmaze ids, start/end year, episode count, run time, network, country) so
the UI can link straight to the real https://epguides.com/{directory}/ page
instead of a Google site: search. Runs after enrich_tmdb.py deliberately -
a show's own TMDB-derived release year is used to disambiguate epguides
title collisions (remakes, same title in different countries).

The index itself is a single ~1MB flat file, re-downloaded at most every
ALLSHOWS_REFRESH_DAYS (epguides' own show list doesn't change day to day)
and cached locally at data\\epguides_allshows.txt - a good-citizen choice
for a small site's bandwidth, not just a speed optimization. Per-show match
results are cached separately in data\\epguides_cache.json, same "retry
unmatched after N days" shape as data\\tmdb_cache.json/wikipedia_cache.json,
just a longer interval since rechecking faster than the index itself
refreshes would be pointless.

Matching never guesses: an exact-normalized-title hit against a single,
unambiguous epguides row is trusted outright; anything else (a title
collision, or no exact hit at all) falls back to a strict fuzzy match that
additionally requires the show's own release year to agree with epguides'
start year within a year. No confident match -> epguides_url stays None and
the UI falls back to its own Google site: search, same as it always has for
titles this can't resolve."""
import csv
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from atomic_write import atomic_replace

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
ALLSHOWS_PATH = os.path.join(DATA_DIR, "epguides_allshows.txt")
CACHE_PATH = os.path.join(DATA_DIR, "epguides_cache.json")

ALLSHOWS_URL = "https://epguides.com/common/allshows.txt"
ALLSHOWS_REFRESH_DAYS = 7
RETRY_UNMATCHED_AFTER_DAYS = 30
USER_AGENT = "MediaLibraryScripts/1.0 (personal home media library enrichment; local single-user tool)"

FUZZY_THRESHOLD = 0.9
YEAR_START_RE = re.compile(r"((?:19|20)\d{2})")


def normalize(title):
    t = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def download_allshows():
    req = urllib.request.Request(ALLSHOWS_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as ex:
        print(f"Couldn't download {ALLSHOWS_URL}: {ex}", file=sys.stderr)
        return False
    with open(ALLSHOWS_PATH, "wb") as f:
        f.write(data)
    return True


def ensure_allshows():
    """Downloads data\\epguides_allshows.txt if missing or older than
    ALLSHOWS_REFRESH_DAYS. Returns True if a usable file is present
    afterward (a stale cached copy is still usable if a refresh fails -
    epguides.com being briefly unreachable shouldn't blank out every link
    that already worked)."""
    if os.path.exists(ALLSHOWS_PATH):
        age_days = (time.time() - os.path.getmtime(ALLSHOWS_PATH)) / 86400
        if age_days < ALLSHOWS_REFRESH_DAYS:
            return True
    if download_allshows():
        return True
    return os.path.exists(ALLSHOWS_PATH)


def load_allshows_index():
    """{normalized_title: [(real_title, directory, start_year, tvmaze_id), ...]}
    - a list per key since epguides has genuine title collisions (remakes,
    same title in different countries). tvmaze_id (int or None - not every
    row has one) is carried through even though this script's own
    find_match() callers only ever use directory/real_title - it's the
    bridge pipeline/fallback_identify.py uses to reach TVmaze's
    externals.imdb and from there TMDB's own /find/{imdb_id}, so a title
    TMDB's search alone can't find still resolves to a real tmdb_id via an
    exact-id chain, never a second round of fuzzy guessing."""
    index = {}
    with open(ALLSHOWS_PATH, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("title") or "").strip()
            directory = (row.get("directory") or "").strip()
            if not title or not directory:
                continue
            m = YEAR_START_RE.search(row.get("start date") or "")
            start_year = int(m.group(1)) if m else None
            tvmaze_raw = (row.get("TVmaze") or "").strip()
            tvmaze_id = int(tvmaze_raw) if tvmaze_raw.isdigit() else None
            index.setdefault(normalize(title), []).append((title, directory, start_year, tvmaze_id))
    return index


def find_match(show_title, show_year, index):
    """Returns (directory, matched_title, tvmaze_id) or (None, None, None).
    Exact-normalized hits against a single unambiguous row are trusted
    outright; a collision or a miss falls back to a strict fuzzy scan
    across the whole index, additionally requiring show_year to agree with
    epguides' start year within a year - never guessing when there's
    nothing to disambiguate with."""
    key = normalize(show_title)
    rows = index.get(key)
    if rows and len(rows) == 1:
        return rows[0][1], rows[0][0], rows[0][3]
    if rows and show_year:
        for real_title, directory, start_year, tvmaze_id in rows:
            if start_year is not None and abs(start_year - show_year) <= 1:
                return directory, real_title, tvmaze_id

    # index keys are already normalized - compare directly instead of
    # re-normalizing both sides on every one of these ~13,500 comparisons.
    matcher = difflib.SequenceMatcher(None, key)
    best = None
    best_score = 0.0
    for norm_title, entries in index.items():
        matcher.set_seq2(norm_title)
        score = matcher.ratio()
        if score > best_score:
            best_score = score
            best = entries
    if best is None or best_score < FUZZY_THRESHOLD:
        return None, None, None
    if len(best) == 1:
        return best[0][1], best[0][0], best[0][3]
    if show_year:
        for real_title, directory, start_year, tvmaze_id in best:
            if start_year is not None and abs(start_year - show_year) <= 1:
                return directory, real_title, tvmaze_id
    return None, None, None  # still ambiguous, no year to break the tie - never guess


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


def main():
    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    if not ensure_allshows():
        print("No epguides index available (download failed and no cached copy exists) - skipping this run.", file=sys.stderr)
        return

    index = load_allshows_index()
    cache = load_cache()
    now = datetime.now(timezone.utc)

    tv_shows = [s for s in shows if s["type"] == "TV"]

    matched = 0
    from_cache = 0
    unmatched = 0

    for s in shows:
        s["epguides_url"] = None

    for i, s in enumerate(tv_shows):
        key = normalize(s["title"])
        cached = cache.get(key)
        if cached:
            stale = False
            if not cached["matched"]:
                checked = cached.get("last_checked")
                age_days = (now - datetime.fromisoformat(checked)).days if checked else RETRY_UNMATCHED_AFTER_DAYS
                stale = age_days >= RETRY_UNMATCHED_AFTER_DAYS
            if cached["matched"] or not stale:
                if cached["matched"]:
                    s["epguides_url"] = f"https://epguides.com/{cached['directory']}/"
                    matched += 1
                else:
                    unmatched += 1
                from_cache += 1
                continue

        show_year = s["years"][0] if s.get("years") else None
        directory, matched_title, tvmaze_id = find_match(s["title"], show_year, index)

        if directory:
            s["epguides_url"] = f"https://epguides.com/{directory}/"
            cache[key] = {
                "matched": True, "directory": directory, "matched_title": matched_title,
                "tvmaze_id": tvmaze_id, "last_checked": now.isoformat(),
            }
            matched += 1
        else:
            cache[key] = {"matched": False, "last_checked": now.isoformat()}
            unmatched += 1

        if (i + 1) % 50 == 0:
            print(f"...{i + 1}/{len(tv_shows)}", file=sys.stderr)
            save_cache(cache)

    # Atomic write - see scan_media.py's own copy of this comment
    # (2026-08-21): web_server.py reads this file on every page load, so a
    # direct write here can hand a concurrent reader a torn/truncated file.
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2)
    atomic_replace(tmp, DB_PATH)
    save_cache(cache)

    print(f"epguides: {len(tv_shows)} TV shows checked ({from_cache} from cache).")
    print(f"{matched} matched to a real epguides.com page, {unmatched} fell back to a site search (no confident match).")


if __name__ == "__main__":
    main()

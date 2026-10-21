"""Fallback enrichment for titles TMDB couldn't match at all (e.g. "La Blue
Girl") - looks them up on Wikipedia instead, so the app has *something*
(a real summary + image) rather than nothing. Runs after consolidate.py
deliberately - prefix_merge() there folds a lot of loosely-titled leftovers
into their real parent show as "extras" first, so this only ever spends a
lookup on shows that are genuinely standalone and still unidentified.

Two-step lookup mirroring enrich_tmdb.py's own search-then-detail shape:
Wikipedia's search API to find the best candidate page, then the REST
summary API for the actual extract/thumbnail. Cached forever once found,
same "retry unmatched after N days" pattern as data/tmdb_cache.json - never
re-queried needlessly. The actual thumbnail image is downloaded and saved
locally to data/wiki_images/ (not just linked), per the user's explicit
request to have images stored locally rather than re-fetched.

This is explicitly a lower-confidence fallback, not a TMDB-grade match -
the UI labels it "via Wikipedia" rather than presenting it as verified,
same spirit as every other "never guess, flag instead" mechanism in this
app. A title Wikipedia doesn't have anything plausible for is left alone,
same as always - not every leftover is going to be identifiable.
"""
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from atomic_write import atomic_replace

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
CACHE_PATH = os.path.join(DATA_DIR, "wikipedia_cache.json")
IMAGES_DIR = os.path.join(DATA_DIR, "wiki_images")
RETRY_UNMATCHED_AFTER_DAYS = 7

USER_AGENT = "MediaLibraryScripts/1.0 (personal home media library enrichment; local single-user tool)"
SLEEP_BETWEEN_CALLS = 0.2
HASH_LIKE = re.compile(r"^[0-9a-f]{24,64}$", re.IGNORECASE)
STOPWORDS = {
    "the", "and", "for", "with", "live", "from", "aus", "at", "of", "a", "an",
    "this", "that", "des", "dub", "sub", "eng", "jpn",
}


def api_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1.0)
    return None


def normalize(title):
    t = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def looks_like_hash(title):
    return bool(HASH_LIKE.match(title.replace(" ", "")))


def looks_related(query, candidate_title, extract):
    """Lenient by design - this is a fallback for content TMDB already gave
    up on entirely, and Wikipedia's own search ranking is already doing the
    real relevance work. Just requires at least one substantial (4+ char,
    non-generic) word from the original title to actually appear somewhere
    in the candidate - catches genuine non-matches without rejecting good
    hits whose title text just doesn't look structurally similar (e.g.
    "Peter Fox and Cold Steel Live aus Berlin" -> "Peter Fox (musician)")."""
    if not candidate_title:
        return False
    q_words = [w for w in normalize(query).split() if len(w) >= 4 and w not in STOPWORDS]
    if not q_words:
        return False
    # Whole-word membership, not a raw substring check - "engl" (meant as
    # an abbreviation for "English" in a release filename) is a literal
    # substring of "English" itself, which produced a real false-positive
    # match to the "Netherlands" Wikipedia article (its official-languages
    # section mentions English) before this was word-boundary aware.
    haystack_words = set(normalize(candidate_title + " " + (extract or "")).split())
    overlap = sum(1 for w in q_words if w in haystack_words)
    # A single coincidental word overlap is too weak a bar on its own - real
    # false positives found this way during verification: "CC Stand-Up
    # Chris Hardwick" -> a different comedian's page (only "stand"
    # overlapped, not "chris"/"hardwick"), "Screams From A Mall" -> an
    # unrelated real shopping mall's page (only "mall" overlapped), "Spay
    # Anything" -> a band's discography (only "anything" overlapped, not
    # "spay"). Require 2 overlaps when 2+ candidate words exist; a
    # genuinely short query (only 1 real word to check at all) can't do
    # better than 1, so that's still accepted.
    required = 2 if len(q_words) >= 2 else 1
    return overlap >= required


def _wiki_search_raw(title):
    params = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": title,
        "format": "json", "srlimit": 3,
    })
    data = api_get(f"https://en.wikipedia.org/w/api.php?{params}")
    time.sleep(SLEEP_BETWEEN_CALLS)
    if not data:
        return None
    results = data.get("query", {}).get("search", [])
    return results[0]["title"] if results else None


def wiki_search(title):
    candidate = _wiki_search_raw(title)
    if candidate:
        return candidate
    # Messy release-style titles ("La Blue Girl - 01 jpn-dub engl") often
    # return zero hits verbatim, since "jpn-dub"/"engl" aren't real words -
    # confirmed with a real zero-hit case. Retry with just the portion
    # before the first " - ", a separator this library's own titles
    # routinely use ahead of episode-number/language-tag suffixes.
    if " - " in title:
        shortened = title.split(" - ")[0].strip()
        if shortened and shortened != title:
            return _wiki_search_raw(shortened)
    return None


def wiki_summary(page_title):
    encoded = urllib.parse.quote(page_title.replace(" ", "_"))
    data = api_get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}")
    time.sleep(SLEEP_BETWEEN_CALLS)
    if not data or data.get("type") == "disambiguation":
        return None
    return data


def safe_image_filename(title, url):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".jpg"
    safe = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:80]
    return f"{safe}{ext}"


def download_image(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, urllib.error.HTTPError):
        return False
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(data)
    return True


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


def apply_cached_fields(show, cached):
    show["wiki_matched"] = cached["wiki_matched"]
    show["wiki_title"] = cached.get("wiki_title")
    show["wiki_extract"] = cached.get("wiki_extract")
    show["wiki_page_url"] = cached.get("wiki_page_url")
    show["wiki_image_path"] = cached.get("wiki_image_path")
    # Defaults to "Wikipedia" for every real match this script itself ever
    # wrote (they never set the field). A cache entry can also be written
    # by hand for a title Wikipedia genuinely doesn't have but a specific
    # source article does (see Reorg_Session_Insights.md, 2026-08-21 -
    # "Queens of the Stone Age Glastonbury", sourced from NME) - that entry
    # sets wiki_source itself, so the display label stays honest instead of
    # claiming Wikipedia for something that isn't.
    show["wiki_source"] = cached.get("wiki_source") or "Wikipedia"


def main():
    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    cache = load_cache()
    now = datetime.now(timezone.utc)

    candidates = [
        s for s in shows
        if not s.get("tmdb_matched") and not looks_like_hash(s["title"])
    ]
    # Genuinely hash-like filenames (e.g. a NZB/release hash with no real
    # title at all) are skipped - a Wikipedia lookup on those would be
    # guessing, same never-guess bar as everywhere else. Everything else
    # unmatched is worth a lookup regardless of type=="Other" - that just
    # means scan_media.py couldn't tell if it's TV or Movie from the
    # filename alone (e.g. "La Blue Girl - 01 jpn-dub engl" is `type:
    # "Other"` in the real library despite being a perfectly real,
    # readable title - confirmed by testing against the real data before
    # settling on this filter, not assumed).

    matched = 0
    from_cache = 0
    unmatched = 0
    downloaded = 0

    for i, s in enumerate(candidates):
        key = normalize(s["title"])
        cached = cache.get(key)
        if cached:
            stale = False
            if not cached["wiki_matched"]:
                checked = cached.get("last_checked")
                age_days = (now - datetime.fromisoformat(checked)).days if checked else RETRY_UNMATCHED_AFTER_DAYS
                stale = age_days >= RETRY_UNMATCHED_AFTER_DAYS
            if cached["wiki_matched"] or not stale:
                apply_cached_fields(s, cached)
                from_cache += 1
                if s["wiki_matched"]:
                    matched += 1
                else:
                    unmatched += 1
                continue

        page_title = wiki_search(s["title"])
        summary = wiki_summary(page_title) if page_title else None
        extract = summary.get("extract") if summary else None

        if summary and looks_related(s["title"], page_title, extract):
            image_path = None
            thumb = (summary.get("thumbnail") or {}).get("source")
            if thumb:
                filename = safe_image_filename(s["title"], thumb)
                dest = os.path.join(IMAGES_DIR, filename)
                if download_image(thumb, dest):
                    image_path = f"wiki_images/{filename}"
                    downloaded += 1
            s["wiki_matched"] = True
            s["wiki_title"] = page_title
            s["wiki_extract"] = extract
            s["wiki_page_url"] = (summary.get("content_urls") or {}).get("desktop", {}).get("page")
            s["wiki_image_path"] = image_path
            s["wiki_source"] = "Wikipedia"
            matched += 1
        else:
            s["wiki_matched"] = False
            s["wiki_title"] = None
            s["wiki_extract"] = None
            s["wiki_page_url"] = None
            s["wiki_image_path"] = None
            s["wiki_source"] = None
            unmatched += 1

        cache[key] = {
            "wiki_matched": s["wiki_matched"], "wiki_title": s["wiki_title"],
            "wiki_extract": s["wiki_extract"], "wiki_page_url": s["wiki_page_url"],
            "wiki_image_path": s["wiki_image_path"], "wiki_source": s["wiki_source"],
            "last_checked": now.isoformat(),
        }

        if (i + 1) % 20 == 0:
            print(f"...{i + 1}/{len(candidates)}", file=sys.stderr)
            save_cache(cache)

    # Atomic write - see scan_media.py's own copy of this comment
    # (2026-08-21): web_server.py reads this file on every page load, so a
    # direct write here can hand a concurrent reader a torn/truncated file.
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2)
    atomic_replace(tmp, DB_PATH)
    save_cache(cache)

    print(f"Wikipedia fallback: {len(candidates)} unmatched titles checked ({from_cache} from cache).")
    print(f"{matched} matched on Wikipedia ({downloaded} new images downloaded), {unmatched} still unidentified.")


if __name__ == "__main__":
    main()

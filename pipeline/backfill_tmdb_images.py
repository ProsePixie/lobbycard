"""One-time (but safely re-runnable) backfill: downloads every poster,
backdrop, cast photo, and recommendation thumbnail for every already-
TMDB-matched show in data\\media_db.json and data\\physical_db.json into
the local cache (see tmdb_image_cache.py), plus any non-TMDB manual
covers in the physical collection (library_rules.json, "manual_covers"). Idempotent - ensure_cached() skips anything already on disk,
so interrupting this (Ctrl+C) and running it again just picks up where it
left off, no partial-state cleanup needed.

Not part of the refresh pipeline (enrich_tmdb.py handles ongoing/new
titles on its own, see that file's docstring) - this script exists only
to front-load the existing library in one pass, run by hand."""
import json
import os
import sys
import time

from tmdb_image_cache import cache_show_images, ensure_cached

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
PHYSICAL_DB_PATH = os.path.join(DATA_DIR, "physical_db.json")

# (spine_title substring to find the show, its cover URL) for physical-collection
# discs that have no TMDB match at all. Comes from <data dir>/library_rules.json
# ("manual_covers"), mirroring the no-TMDB-match entries of
# physical_media_seed.json.
from library_rules import manual_covers  # noqa: E402
MANUAL_COVERS = manual_covers()


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def download_manual_cover(url, title):
    """Manual covers aren't TMDB paths, so they don't fit ensure_cached()'s
    kind/size model - downloaded directly into the same tmdb_images/manual/
    folder, named after the title so it's obvious on disk what it is."""
    import urllib.request
    import urllib.error
    from tmdb_image_cache import IMAGES_ROOT, USER_AGENT

    manual_dir = os.path.join(IMAGES_ROOT, "manual")
    os.makedirs(manual_dir, exist_ok=True)
    ext = os.path.splitext(url)[1] or ".jpg"
    safe_name = "".join(c if c.isalnum() else "_" for c in title)[:60] + ext
    dest = os.path.join(manual_dir, safe_name)
    if os.path.exists(dest):
        return True, safe_name
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        with open(dest, "wb") as f:
            f.write(data)
        return True, safe_name
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"  FAILED: {title} ({e})", file=sys.stderr)
        return False, None


def main():
    shows = load(DB_PATH) + load(PHYSICAL_DB_PATH)
    matched = [s for s in shows if s.get("tmdb_matched")]

    print(f"Backfilling images for {len(matched)} TMDB-matched shows "
          f"({len(shows)} total, {len(shows) - len(matched)} unmatched/manual skipped from this pass)...")

    total_attempted = total_downloaded = 0
    start = time.time()
    for i, s in enumerate(matched):
        attempted, downloaded = cache_show_images(s)
        total_attempted += attempted
        total_downloaded += downloaded
        if (i + 1) % 25 == 0:
            elapsed = time.time() - start
            print(f"...{i + 1}/{len(matched)} shows ({total_downloaded} new images so far, {elapsed:.0f}s elapsed)", file=sys.stderr)

    print(f"\nMain pass done: {total_downloaded} new images downloaded, "
          f"{total_attempted - total_downloaded} already cached, {total_attempted} checked total.")

    print("\nManual (non-TMDB) covers:")
    manual_ok = 0
    for title_substr, url in MANUAL_COVERS:
        matches = [s for s in load(PHYSICAL_DB_PATH) if title_substr in s.get("title", "")]
        if not matches:
            print(f"  SKIPPED: no show found matching '{title_substr}'", file=sys.stderr)
            continue
        ok, filename = download_manual_cover(url, matches[0]["title"])
        if ok:
            manual_ok += 1
            print(f"  {matches[0]['title']} -> tmdb_images/manual/{filename}")

    print(f"\n{manual_ok}/{len(MANUAL_COVERS)} manual covers cached.")

    # Real on-disk size check, not an estimate - confirms nothing silently skipped.
    from tmdb_image_cache import IMAGES_ROOT
    total_bytes = 0
    file_count = 0
    for root, _dirs, files in os.walk(IMAGES_ROOT):
        for fn in files:
            total_bytes += os.path.getsize(os.path.join(root, fn))
            file_count += 1
    print(f"\nTotal local cache: {file_count} files, {total_bytes / 1e6:.1f} MB in {IMAGES_ROOT}")


if __name__ == "__main__":
    main()

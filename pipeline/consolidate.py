"""Merges duplicate-identity shows in media_db.json after scan_media.py:
first by shared TMDB id (merge_by_tmdb_id), then by a whole-word "contains"
match against canonical TMDB-matched TV show titles for unmatched leftovers
and bonus/extras content (prefix_merge - extras get flagged is_extra on
their entries, never renamed or moved). Runs third in the refresh pipeline,
after enrich_tmdb.py, before enrich_wikipedia.py."""
import json
import os
import re
from atomic_write import atomic_replace

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")

from app_config import QUEUE_LABEL  # noqa: E402
ROOT_KIND = {
    QUEUE_LABEL: "queue", "Archive": "archived",
}
RES_RANK = {"2160p": 4, "4k": 4, "uhd": 4, "1080p": 3, "720p": 2, "480p": 1}
SOURCE_RANK = {
    "bluray": 5, "blu-ray": 5, "bdrip": 4, "web-dl": 4, "webdl": 4, "webrip": 3,
    "web": 3, "hdtv": 2, "dvdrip": 1, "dvd": 1,
}
MULTISPACE = re.compile(r"\s+")


def normalize_key(title):
    key = re.sub(r"[^a-z0-9]+", " ", title.lower())
    key = MULTISPACE.sub(" ", key).strip()
    key = re.sub(r"\b(\w+) s\b", r"\1s", key)
    return key


def quality_score(name):
    low = name.lower()
    res = max((v for k, v in RES_RANK.items() if k in low), default=0)
    src = max((v for k, v in SOURCE_RANK.items() if k in low), default=0)
    return (res, src)


def quality_label(name):
    """Mirrors pipeline/scan_media.py's own copy - human-readable quality
    string for the Dashboard's duplicate picker, same RES_RANK/SOURCE_RANK
    keyword matches quality_score() uses, just rendered as text."""
    low = name.lower()
    res_key = max((k for k in RES_RANK if k in low), key=lambda k: RES_RANK[k], default=None)
    src_key = max((k for k in SOURCE_RANK if k in low), key=lambda k: SOURCE_RANK[k], default=None)
    parts = [p for p in (res_key, src_key) if p]
    return ", ".join(parts) if parts else "Unknown quality"


# Mirrors pipeline/scan_media.py's own copy (that one runs first and covers
# shows that never reach this file's aggregate() at all - a show with no
# tmdb_id collision and nothing to prefix-merge passes through unchanged).
# Kept in sync deliberately, unlike DISPOSABLE_SIDECAR_EXTS's drift with
# the paused reorg project - both copies here are live, load-bearing code.
# Matched against normalize_key() output, not raw text - apostrophes are
# stripped there (director's -> directors), so only the no-apostrophe form
# of each keyword can ever match; no separate "director's cut" entry.
ALT_VERSION_KEYWORDS = (
    "alternative cut", "extended cut", "extended edition", "extended version",
    "directors cut", "theatrical cut", "unrated cut", "uncut",
)


def aggregate(entries, ftype):
    episode_keys = set()
    for e in entries:
        if ftype == "TV" and e["season"] is not None:
            episode_keys.add((e["season"], e["episode"]))
    episode_count = len(episode_keys) if ftype == "TV" else None
    if ftype == "TV" and episode_count == 0:
        episode_count = len(entries)

    # Entries with no video file (video_path is None) are excluded from
    # duplicate ranking - see the matching note in pipeline/scan_media.py.
    # Without this, Auto-File's deliberately-stripped clean filenames
    # routinely lose the quality_score() comparison to a leftover, video-
    # less release folder that still has the original scene-release name.
    #
    # ALT_VERSION_KEYWORDS gets its own tag in the key so a deliberately
    # different cut (confirmed real: "Mythic Quest" S04E10's finale vs its
    # ALTERNATIVE.CUT release) competes for the "keep" slot only against
    # another copy of the *same* variant, never against the main episode -
    # see scan_media.py's mirrored comment for the full incident.
    # normalize_key(), not a plain .lower() - release names separate words
    # with dots/underscores ("ALTERNATIVE.CUT"), which a raw substring
    # check against "alternative cut" would never match.
    alt_tag = lambda e: next((kw for kw in ALT_VERSION_KEYWORDS if kw in normalize_key(e["name"])), None)
    dup_groups = {}
    if ftype == "TV":
        for e in entries:
            if e["season"] is not None and e.get("video_path"):
                dup_groups.setdefault((e["season"], e["episode"], alt_tag(e)), []).append(e)
    else:
        for e in entries:
            if e.get("video_path"):
                dup_groups.setdefault((None, None, alt_tag(e)), []).append(e)

    # A folder-wrapped leaf's OWN name (e["name"]) is often a cleaned-up
    # release/category name with no resolution tag at all, even though the
    # real video file one level down still carries it in its filename -
    # mirrors pipeline/scan_media.py's own fix (2026-08-27) for the exact
    # same bug, confirmed still live here too: this file's aggregate() runs
    # AFTER scan_media.py, for any show that goes through a merge (shared
    # TMDB id or prefix-merge), and OVERWRITES scan_media.py's already-
    # correct duplicates/wasted_bytes with this file's own separate (until
    # now, still-broken) copy of the same logic - confirmed real via
    # Doctor Who: 49 duplicate groups, all missing keep_detail/extra_details
    # (added below, mirroring scan_media.py) because this second code path
    # never got the fix scan_media.py got earlier today.
    dup_quality_name = lambda e: os.path.basename(e["video_path"]) if e.get("video_path") else e["name"]
    dup_detail = lambda e: {
        "path": e["path"],
        "name": dup_quality_name(e),
        "size_bytes": e["size_bytes"],
        "quality_label": quality_label(dup_quality_name(e)),
    }

    wasted_bytes = 0
    duplicates = []
    for key, group in dup_groups.items():
        if len(group) < 2:
            continue
        group_sorted = sorted(group, key=lambda e: (quality_score(dup_quality_name(e)), e["size_bytes"]), reverse=True)
        keep, extras = group_sorted[0], group_sorted[1:]
        wasted = sum(e["size_bytes"] for e in extras)
        wasted_bytes += wasted
        duplicates.append({
            "season": key[0], "episode": key[1],
            "keep_path": keep["path"], "keep_quality": quality_score(dup_quality_name(keep)),
            "extra_paths": [e["path"] for e in extras], "wasted_bytes": wasted,
            "keep_detail": dup_detail(keep),
            "extra_details": [dup_detail(e) for e in extras],
        })

    roots = sorted({e["root"] for e in entries})
    categories = sorted({e["category"] for e in entries if e.get("category")})
    # Stage classification only considers entries with a real video file -
    # a video-less leftover (e.g. a thumbnail.jpg Auto-File left behind
    # after extracting the video, see roadmap item 25) isn't real unfiled
    # content, so it shouldn't make a fully-archived show look like it
    # still has something coming. `roots`/`categories` above stay
    # unfiltered - the leftover still shows up as its own entry and is
    # still findable via the location filter, this only affects the
    # show-level stage label.
    stage_roots = {e["root"] for e in entries if e.get("video_path")}
    kinds = {ROOT_KIND.get(r, "queue") for r in stage_roots}
    if "archived" in kinds and len(kinds) == 1:
        stage = "Archived"
    elif "archived" in kinds:
        stage = "Archived + still in queue"
    else:
        stage = "In Queue"

    total_size = sum(e["size_bytes"] for e in entries)
    # ctime preferred over mtime - see scan_media.py's leaf_from_folder()
    # comment: mtime is routinely preserved from the original release by
    # copy tools and can be years stale, while ctime (Windows' real
    # file-creation time) reflects when this copy actually landed on disk.
    latest = max((e.get("ctime") or e["mtime"] for e in entries if e.get("ctime") or e["mtime"]), default=None)
    entries_sorted = sorted(entries, key=lambda e: (e["season"] or 0, e["episode"] or 0, e["name"]))
    return {
        "episode_count": episode_count, "version_count": len(entries), "roots": roots,
        "archive_categories": categories, "pipeline_stage": stage, "entries": entries_sorted,
        "latest_added": latest, "total_size_bytes": total_size, "wasted_bytes": wasted_bytes,
        "duplicates": duplicates,
    }


def dedupe_entries(entries):
    seen, out = set(), []
    for e in entries:
        if e["path"] in seen:
            continue
        seen.add(e["path"])
        out.append(e)
    return out


def merge_group(group):
    canonical = None
    for s in group:
        if s.get("tmdb_name"):
            canonical = s["tmdb_name"]
            break
    if not canonical:
        canonical = max((s["title"] for s in group), key=len)

    entries = dedupe_entries([e for s in group for e in s["entries"]])
    agg = aggregate(entries, group[0]["type"])
    years = sorted({y for s in group for y in s.get("years", [])})
    best = max(group, key=lambda s: s["version_count"])

    return {
        "title": canonical, "type": group[0]["type"], "genre": best.get("genre", "Uncategorized"),
        "years": years,
        **agg,
        "tmdb_matched": best.get("tmdb_matched", False), "tmdb_genres": best.get("tmdb_genres"),
        "tmdb_id": best.get("tmdb_id"), "tmdb_name": best.get("tmdb_name"),
        "tmdb_overview": best.get("tmdb_overview"), "tmdb_poster": best.get("tmdb_poster"),
        "tmdb_vote": best.get("tmdb_vote"), "tmdb_status": best.get("tmdb_status"),
        "tmdb_release_date": best.get("tmdb_release_date"),
        "tmdb_tv_type": best.get("tmdb_tv_type"), "tmdb_number_of_seasons": best.get("tmdb_number_of_seasons"),
        "merged_from": sorted({s["title"] for s in group}),
        # carried forward from scan_media.py's own carry-forward (see its
        # main()) so a merge during the fast startup quick-scan doesn't drop
        # completeness data that isn't recomputed until the next full refresh
        "missing_episodes": best.get("missing_episodes", []),
        "missing_seasons": best.get("missing_seasons", []),
        "episodes_expected_so_far": best.get("episodes_expected_so_far"),
        "assumed_physical_media": best.get("assumed_physical_media", False),
        "completeness_skip_reason": best.get("completeness_skip_reason"),
        # Same carry-forward reasoning, added 2026-08-20 - these were
        # missing from this list too (a real, separate gap from
        # scan_media.py's own whitelist: a merge here would have silently
        # dropped them even on runs where scan_media.py's own carry-forward
        # worked fine).
        "wiki_matched": best.get("wiki_matched", False), "wiki_title": best.get("wiki_title"),
        "wiki_extract": best.get("wiki_extract"), "wiki_page_url": best.get("wiki_page_url"),
        "wiki_image_path": best.get("wiki_image_path"),
        "epguides_url": best.get("epguides_url"),
        "physical_seasons_owned": best.get("physical_seasons_owned"),
    }


def merge_by_tmdb_id(shows):
    groups = {}
    singles = []
    for s in shows:
        if s.get("tmdb_matched") and s.get("tmdb_id"):
            key = (s["tmdb_id"], s["type"])
            groups.setdefault(key, []).append(s)
        else:
            singles.append(s)

    merged = []
    report = []
    for key, group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        new_show = merge_group(group)
        merged.append(new_show)
        report.append((new_show["title"], [s["title"] for s in group]))

    merged.extend(singles)
    return merged, report


MIN_PREFIX_WORDS = 2
MIN_PREFIX_CHARS = 6


def prefix_merge(shows):
    """Sweep leftover unmatched items whose title contains an already-
    canonical (TMDB-matched) TV show's title as a whole word sequence -
    e.g. bonus content, behind-the-scenes, or old story-numbered episodes
    that reference the show's name (not necessarily at the very start -
    promotional/bonus titles routinely bury the show name mid-title, e.g.
    "A Deleted Scene - The Day of the Doctor - Doctor Who 50th Anniversary").
    Leftovers merged this way are flagged is_extra on their entries (not
    real episodes - the UI hides them by default with a toggle) rather
    than moved or renamed on disk; a genuinely-unmatched leftover whose
    title doesn't reference any canonical show is left exactly as its own
    entry, same never-guess bar as everywhere else in this app."""
    canonical_tv = [
        s for s in shows
        if s["type"] == "TV" and s.get("tmdb_matched") and len(s["title"].split()) >= MIN_PREFIX_WORDS
        and len(s["title"]) >= MIN_PREFIX_CHARS
    ]
    # longest titles first, so a more specific show claims a candidate before a shorter one might
    canonical_tv.sort(key=lambda s: len(s["title"]), reverse=True)

    # Movie included too - filename parsing sometimes misclassifies bonus
    # content as a "Movie" (e.g. "Expedition X Shark Week S" - a known,
    # documented parsing edge case, README Section 4) purely because its
    # name doesn't happen to contain a season/episode pattern.
    leftover_idx = [
        i for i, s in enumerate(shows)
        if s["type"] in ("TV", "Other", "Movie") and not s.get("tmdb_matched")
    ]

    claims = {}  # (canon title, canon type) -> (canon show, [leftover shows])
    claimed_idx = set()

    for i in leftover_idx:
        leftover = shows[i]
        norm_leftover = f" {normalize_key(leftover['title'])} "
        for canon in canonical_tv:
            norm_canon = normalize_key(canon["title"])
            if norm_leftover.strip() == norm_canon:
                continue
            if f" {norm_canon} " in norm_leftover:
                key = (canon["title"], canon["type"])
                claims.setdefault(key, (canon, []))[1].append(leftover)
                claimed_idx.add(i)
                break

    if not claims:
        return shows, []

    report = []
    updated_canon = {}
    for key, (canon, members) in claims.items():
        extra_entries = []
        for m in members:
            for e in m["entries"]:
                e = dict(e)
                e["is_extra"] = True
                extra_entries.append(e)
        entries = dedupe_entries(canon["entries"] + extra_entries)
        agg = aggregate(entries, canon["type"])
        merged = dict(canon)
        merged.update(agg)
        updated_canon[key] = merged
        report.append((canon["title"], [m["title"] for m in members]))

    result = []
    for i, s in enumerate(shows):
        if i in claimed_idx:
            continue
        key = (s["title"], s["type"])
        result.append(updated_canon.get(key, s))

    return result, report


def main():
    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    before = len(shows)
    shows, id_report = merge_by_tmdb_id(shows)
    shows, prefix_report = prefix_merge(shows)
    shows.sort(key=lambda s: s["title"].lower())

    # Atomic write - see scan_media.py's own copy of this comment
    # (2026-08-21): web_server.py reads this file on every page load, so a
    # direct write here can hand a concurrent reader a torn/truncated file.
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2)
    atomic_replace(tmp, DB_PATH)

    print(f"Shows: {before} -> {len(shows)}")
    print(f"\nMerged by shared TMDB identity ({len(id_report)} groups):")
    for canon, members in id_report:
        print(f"  {canon}  <=  {', '.join(m for m in members if m != canon)}")
    print(f"\nMerged by title-prefix match ({len(prefix_report)} groups):")
    for canon, members in prefix_report:
        print(f"  {canon}  <=  {', '.join(members)}")


if __name__ == "__main__":
    main()

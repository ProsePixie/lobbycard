"""Walks every queue/staging/archive root, parses each release folder or
loose video file into a TV/Movie/Other leaf item (season/episode/year via
filename regex), groups into shows, and rebuilds data\\media_db.json from
scratch every run - including the fast startup quick-scan, so it carries
forward fields only the nightly-only steps compute (missing-episode data,
air dates, runtime-mismatch flags). Runs first in the refresh pipeline."""
import os
import re
import json
import sys
from datetime import datetime

from atomic_write import atomic_replace
import step_progress
from split_by_year_titles import is_split_by_year_title

# For load_state() only (plan item 20's season_offset override) - same
# sys.path pattern enrich_tmdb.py already uses to reach app/state_store.py
# from a pipeline/ script.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
from state_store import load_state  # noqa: E402

# "queue" = temporary/incoming, "staging" = watched but not yet filed,
# "archived" = permanently filed in the real library
from app_config import ARCHIVE_ROOT, QUEUE_ROOTS as _QUEUE_ROOTS, DATA_DIR as _CFG_DATA_DIR  # noqa: E402
ROOTS = {r["label"]: (r["path"], "queue") for r in _QUEUE_ROOTS}
ARCHIVE_ROOT_LABEL = "Archive"
# The old staging-based filing workflow (Recent Downloads / Things to Watch /
# Watched roots) is gone: a single incoming (queue) root files into the
# archive.
ARCHIVE_SKIP_SUBFOLDERS = {"_Review Before Delete"}  # created by build_quarantine_plan.py; never rescan it

QUARANTINE_FOLDER_NAME = "_Review Before Delete"  # created by build_quarantine_plan.py; never rescan it

# Titles where two genuinely different, unrelated productions share an
# exact name (a remake/reboot) - normally items are grouped into one show
# by (title, type) alone with no year, which is correct for the vast
# majority of real shows (episode filenames often omit the year entirely,
# and a show can legitimately span many years across seasons - adding year
# to that grouping key generally would fragment normal shows, not fix
# anything). These titles opt in to year-aware grouping instead, since for
# them merging is actively wrong, not just imprecise. Confirmed real:
# "The Stand" 1994 miniseries vs 2020 series - same title, unrelated
# productions, both consistently carry their year in every episode
# filename. Add a title here (normalized form, via normalize_key) only for
# a confirmed real collision like this one, not preemptively.
# "Doctor Who" 1963/2005/2023 - three separate productions (matching real,
# distinct TMDB/epguides entries), reorganized 2026-08-19 into per-era
# folders "Doctor Who (1963)"/"(2005)"/"(2023)". Unlike The Stand, none of
# the individual episode filenames carry a year at all - only the top
# folder name does, which is exactly what process_folder()'s fallback_year
# propagation (see its docstring) exists to carry down to each episode.
#
# The actual set (SPLIT_BY_YEAR_TITLES) plus the Admin-panel-set additions
# now live in split_by_year_titles.py, imported below - factored out
# 2026-09-26 after a real bug: enrich_tmdb.py's title-keyed override
# fallback used to key on bare title alone, with no year, so fixing one
# split-by-year era's wrong TMDB match via that override silently reapplied
# the same id to every other era sharing the title (Doctor Who's three eras
# collapsed into one merged, garbled show). is_split_by_year_title() is now
# the single source of truth both here and there import from.

ROOT_KIND = {label: kind for label, (_, kind) in ROOTS.items()}
ROOT_KIND[ARCHIVE_ROOT_LABEL] = "archived"

MAX_DEPTH = 8

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".wmv", ".mov", ".m4v", ".flv", ".webm", ".iso",
    ".mpg", ".mpeg",
    # DVD-Video's own raw MPEG-PS stream file (a ripped VIDEO_TS\ folder,
    # never re-encoded) - real content in the library, confirmed real via
    # <library>/Music\Music Videos\ (13 .VOB files, completely invisible to
    # every scan until now). .IFO/.BUP deliberately NOT added alongside it
    # - those are DVD navigation data and backups thereof, not video
    # streams themselves; adding them would create a confusing second
    # "video" entry sitting right next to the real one for every VOB.
    # VLC/ffprobe (see handlers\OpenVlcHandler.cs) already handle .vob
    # natively - nothing extension-specific to change there, this is the
    # only gap.
    ".vob",
}

# --- private / opt-out folders --------------------------------------------
# Two ways to keep a folder (and everything under it) out of media_db.json
# entirely - so it's never TMDB-matched, never in system_status.json /
# The companion tool digest, never a filing or reorg candidate. Applies in
# BOTH roots (<library>/ AND <downloads folder>), at any depth:
#
#   1. A ".mediaboxignore" file (contents ignored) anywhere in the folder.
#   2. The folder's own name listed in data\scan_ignore.txt (one name per
#      line, case-insensitive, '#' comments and blank lines skipped). That
#      file is gitignored - the names in it stay on this machine only.
#
# Pair (2) with an the downloader category of the same name: the downloader drops that
# category's completed downloads straight into
# <downloads folder>\<CategoryName>\ the moment they finish,
# so there's no window where a scan could catalog it first.
IGNORE_MARKER = ".mediaboxignore"
from app_config import DATA_DIR as _DATA_DIR  # noqa: E402


def _load_scan_ignore_names():
    path = os.path.join(_DATA_DIR, "scan_ignore.txt")
    names = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    names.add(line.lower())
    except OSError:
        pass
    return names


SCAN_IGNORE_NAMES = _load_scan_ignore_names()


def is_ignored_dir(path):
    """True if this directory is opted out of scanning - by name
    (data\\scan_ignore.txt) or by a .mediaboxignore marker file inside it."""
    if os.path.basename(os.path.normpath(path)).lower() in SCAN_IGNORE_NAMES:
        return True
    try:
        return os.path.isfile(os.path.join(path, IGNORE_MARKER))
    except OSError:
        return False

QUALITY_TOKENS = r"(?:1080p|720p|2160p|480p|4K|UHD|BluRay|Blu-Ray|BDRip|WEB[-.]?DL|WEBRip|WEB|HDTV|DVDRip|DVD|REMASTERED|EXTENDED|REPACK|PROPER|INTERNAL|NORDiC|MULTi|HYBRID|LIMITED|UNRATED|DC|CUSTOM)"
YEAR_PAREN = re.compile(r"\(((?:19|20)\d{2})\)")
YEAR_BEFORE_QUALITY = re.compile(r"[.\s]((?:19|20)\d{2})[.\s](?=" + QUALITY_TOKENS + r")", re.IGNORECASE)
ANY_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
SEASON_EP = re.compile(r"[Ss](\d{1,3})[EeXx](\d{1,3})(?:[Ee]\d{1,3})?")
ANIME_RG_PREFIX = re.compile(r"^\[?AnimeRG\]?\s+", re.IGNORECASE)
ANIME_ABS_EP = re.compile(r"^(.+?)\s*-\s*(\d{2,4})(?:\s|$)")
# DVD-Video spec filename for a titleset's own video content -
# VTS_{titleset:2d}_{part:1d}.VOB. Part 0 is that titleset's own menu VOB
# (can be substantial - confirmed real, 136MB for one real disc - so never
# assumed skippable), parts 1+ are the actual title, split across ~1GB
# files by the DVD spec's own size limit, meant to play back-to-back as
# one continuous title. A DIFFERENT titleset number is genuinely separate
# content (e.g. a bonus feature alongside the main one), never combined
# with another titleset's parts. See process_video_ts_folder().
VTS_PART_RE = re.compile(r"^VTS_(\d{2})_(\d)\.VOB$", re.IGNORECASE)

RES_RANK = {"2160p": 4, "4k": 4, "uhd": 4, "1080p": 3, "720p": 2, "480p": 1}
SOURCE_RANK = {
    "bluray": 5, "blu-ray": 5, "bdrip": 4, "web-dl": 4, "webdl": 4, "webrip": 3,
    "web": 3, "hdtv": 2, "dvdrip": 1, "dvd": 1,
}

DOC_KEYWORDS = [
    "documentary", "roots", "expedition", "files", "secrets", "history",
    "nazi", "hitler", "world war", "handmaidens", "genghis", "corridors",
    "strangeness", "occult", "unearthed", "skinwalker", "predator hunters",
    "yellowstone",
]
TRUE_CRIME_KEYWORDS = [
    "detective", "true crime", "gangsters", "money murder", "tyrants",
    "squatters",
]
REALITY_KEYWORDS = [
    "kitchen", "grocery games", "bake off", "flavortown", "food fight",
    "chopped", "diners", "drive-ins", "drive ins",
]
ANIME_KEYWORDS = ["elfen lied", "anime"]
MUSIC_KEYWORDS = [
    "live aus", "live at the", "in concert", "unplugged", "sonic highways",
    "radiohead", "queens of the stone age", "puscifer", "from the basement",
]

SEP_CLEAN = re.compile(r"[._]+")
BRACKETS = re.compile(r"[\[\]{}()]")
MULTISPACE = re.compile(r"\s+")
EDGE_PUNCT = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")


def strip_release_group(name: str) -> str:
    return re.sub(r"-[A-Za-z0-9]+$", "", name)


def clean_title(raw: str) -> str:
    t = SEP_CLEAN.sub(" ", raw)
    t = BRACKETS.sub(" ", t)
    t = MULTISPACE.sub(" ", t).strip()
    t = EDGE_PUNCT.sub("", t)
    return t.strip()


def normalize_key(title: str) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", title.lower())
    key = MULTISPACE.sub(" ", key).strip()
    key = re.sub(r"\b(\w+) s\b", r"\1s", key)
    return key


def year_bucket_for(norm_title, years_or_year):
    """None for every title except split_by_year_titles.is_split_by_year_title()
    (hardcoded set + Admin-panel-set titles - see that module, factored out
    2026-09-26 after a real cross-file drift bug), so grouping (and the
    completeness/air-date carryover cache, which must key the same way or
    it'll wrongly hand one of the split titles' prior data to the other) is
    unaffected for everything else. Accepts either a single year (a leaf
    item's) or a show's already-collected years list."""
    if not is_split_by_year_title(norm_title):
        return None
    if isinstance(years_or_year, list):
        return years_or_year[0] if years_or_year else None
    return years_or_year


# Per-show, per-episode season/episode corrections for titles whose local
# filenames don't carry a real distinguishing episode number at all - a
# "precious little creature" exception list, not a general mechanism,
# since collapsing multiple real episodes onto one (season, episode) key
# is not something normal shows do. The real case that motivated it: a
# show whose specials were all filed as S01E00 (a generic "extra"
# placeholder, not a real number) - identical (season, episode) keys
# silently fed the duplicate-detection logic (main(), dup_groups below),
# which flagged most of the real, distinct specials as "duplicate copies"
# of one of them and marked gigabytes of them reclaimable. Matched by
# whether the raw filename contains the given (normalized) episode title.
#
# The data is per-library, so it lives in <data dir>/scan_overrides.json
# ("episode_number_overrides": {"normalized show title": [[needle, season,
# episode], ...]}), not in code. See personal.example/scan_overrides.json.
EPISODE_NUMBER_OVERRIDES = {}  # filled from scan_overrides.json below


def apply_episode_number_override(norm_show_title, raw_name, season, episode):
    overrides = EPISODE_NUMBER_OVERRIDES.get(norm_show_title)
    if not overrides:
        return season, episode
    norm_name = normalize_key(raw_name)
    for needle, real_season, real_episode in overrides:
        if needle in norm_name:
            return real_season, real_episode
    return season, episode


_season_offset_cache = None


def _load_season_offsets():
    """User-set per-show season-number corrections (plan item 20 - the
    'scene season offset' problem: a rebooted show's TMDB entry restarts
    numbering at season 1 while scene release groups keep counting
    forward from the original's seasons - e.g. Kitchen Nightmares' 2023
    reboot, tmdb:235884, is TMDB season 1 even though scene files call it
    S10 continuing the 2007 original's own 9 seasons). Set via the Admin
    panel's season_offset field (app/web_server.py's /api/override), same
    title-keyed override scheme the matcher's own tmdb_id_override
    fallback already uses (pipeline/enrich_tmdb.py's
    override_lookup_key()) - a show not yet TMDB-matched at scan time can
    only ever be looked up by title, so that's the only key this reads.
    Keyed by (normalize_key(title), year), not the raw title string alone,
    since the folder-name title parsed here and the TMDB-canonical title
    the override was saved under (set later, once matched) commonly differ
    in punctuation/capitalization alone - year is None for a normal title's
    key (title:TV:Foo), or the int from a split-by-year title's
    year-qualified key (title:TV:Foo:2023, see split_by_year_titles.py's
    own docstring for why that qualification exists at all: reusing an
    unqualified key across a split title's separate eras is exactly the
    2026-09-26 Doctor Who incident, just for season_offset instead of
    tmdb_id_override). Cached for the run - this is called once per file,
    sometimes thousands of times.
    """
    global _season_offset_cache
    if _season_offset_cache is None:
        try:
            state = load_state()
        except Exception:
            state = {}
        _season_offset_cache = {}
        prefix = "title:TV:"
        for key, override in state.get("overrides", {}).items():
            offset = override.get("season_offset")
            if not offset or not key.startswith(prefix):
                continue
            suffix = key[len(prefix):]
            m = re.match(r"^(.*):(\d{4})$", suffix)
            title_part, year_part = (m.group(1), int(m.group(2))) if m else (suffix, None)
            _season_offset_cache[(normalize_key(title_part), year_part)] = offset
    return _season_offset_cache


def apply_season_offset(norm_show_title, season, episode, year=None):
    if season is None:
        return season, episode
    cache = _load_season_offsets()
    # A split-by-year title's offset (if any) is ONLY ever stored
    # year-qualified (see web_server.py's /api/override write side) -
    # falling back to an unqualified lookup for one of these would risk
    # exactly the cross-era bleed this key scheme exists to prevent, so
    # there deliberately is no fallback for them.
    if is_split_by_year_title(norm_show_title):
        offset = cache.get((norm_show_title, year)) if year is not None else None
    else:
        offset = cache.get((norm_show_title, None))
    if not offset:
        return season, episode
    real_season = season - offset
    # Never produce a nonsensical negative season - a stale/wrong offset
    # (the show's numbering scheme changed, or it was set for the wrong
    # title collision) should surface as a normal parse rather than
    # silently going more wrong.
    if real_season < 0:
        return season, episode
    return real_season, episode


# Whole-leaf identity overrides for individual folders whose loose files
# are so irregular that normal parsing (parse_folder_name) can't even
# agree on a consistent TITLE across them - every file's "title" ends up
# being its own full cleaned filename, so they never group into one show
# at all (EPISODE_NUMBER_OVERRIDES above assumes the title already
# grouped correctly and only fixes season/episode; this fixes title too).
# Keyed by the containing folder's own name (matched via os.path.basename,
# not full path, so it works regardless of which root/drive the folder
# lives under) so this can never accidentally match an unrelated file
# elsewhere - scoped to exactly the one directory it was written for.
# Each entry: (filename substring, title, type, season, episode, year).
#
# Typical uses: a fan series with one file per whole season, a folder that
# mixes episodes of several spin-off shows, season labels that disagree with
# TMDB's numbering. Like the table above, the data is per-library and lives
# in <data dir>/scan_overrides.json ("directory_leaf_overrides": {"folder
# name": [[needle, title, type, season, episode, year], ...]}).
DIRECTORY_LEAF_OVERRIDES = {}  # filled from scan_overrides.json below


def _load_scan_overrides():
    """Merge <data dir>/scan_overrides.json into the two tables above.
    Missing or malformed file means no overrides - never an error."""
    path = os.path.join(_DATA_DIR, "scan_overrides.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    for show, rows in (data.get("episode_number_overrides") or {}).items():
        EPISODE_NUMBER_OVERRIDES[show] = [(str(r[0]), int(r[1]), int(r[2])) for r in rows]
    for folder, rows in (data.get("directory_leaf_overrides") or {}).items():
        DIRECTORY_LEAF_OVERRIDES[folder] = [
            (str(r[0]), str(r[1]), str(r[2]), int(r[3]), int(r[4]), (int(r[5]) if r[5] is not None else None))
            for r in rows
        ]


_load_scan_overrides()


def apply_directory_leaf_override(path, name):
    """Returns (title, ftype, season, episode, year) if this exact file
    matches a DIRECTORY_LEAF_OVERRIDES entry for its containing folder,
    else None."""
    parent = os.path.basename(os.path.dirname(path))
    entries = DIRECTORY_LEAF_OVERRIDES.get(parent)
    if not entries:
        return None
    lname = name.lower()
    for needle, title, ftype, season, episode, year in entries:
        if needle.lower() in lname:
            return title, ftype, season, episode, year
    return None


# A general (not per-show) signal that a file is a deliberately different
# cut of the same real episode/movie, not a redundant re-encode - see the
# dup_groups comment in main() for why this matters. General because
# release groups routinely tag genuinely-different variants this way
# regardless of show, unlike the placeholder-episode-number problem above,
# which is specific to how individual shows number their specials.
# Matched against normalize_key() output, not raw text - apostrophes are
# stripped there (director's -> directors), so only the no-apostrophe form
# of each keyword can ever match; no separate "director's cut" entry.
ALT_VERSION_KEYWORDS = (
    "alternative cut", "extended cut", "extended edition", "extended version",
    "directors cut", "theatrical cut", "unrated cut", "uncut",
)

# Same phrase list, different job: strip these out of a parsed TITLE itself
# (not just tag their presence for duplicate-detection, which is all
# ALT_VERSION_KEYWORDS did before this) before it's used for identification
# (TMDB/Wikipedia search, display). They're release metadata, not part of a
# movie's real name, but clean_title() only normalizes punctuation/spacing -
# it never removes words, so a filename like "...Extended.Version.2022..."
# passed the literal string "Extended Version" straight through into the
# title used for searching. Confirmed real and confirmed why it matters:
# "Spider-Man.No.Way.Home.Extended.Version.2022...-PrimeFix" - live TMDB
# /search/movie returned zero results for "Spider-Man No Way Home Extended
# Version" (with or without a year filter), but matched instantly once
# stripped to "Spider-Man No Way Home" (TMDB id 634649). Nothing is lost by
# stripping it: the raw filename is still shown per-file in the entries
# list, and if the library ever also has the plain cut of the same movie,
# they'll correctly group as one show with two versions instead of two
# unrelated shows - ALT_VERSION_KEYWORDS still tags them apart in
# duplicate-detection so neither gets flagged as wasted space.
EDITION_DESCRIPTOR_RE = re.compile(
    r"\b(" + "|".join(kw.replace(" ", r"\s+") for kw in ALT_VERSION_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def strip_edition_descriptor(title: str) -> str:
    stripped = EDITION_DESCRIPTOR_RE.sub(" ", title)
    stripped = MULTISPACE.sub(" ", stripped).strip()
    return stripped or title


def extract_year(segment: str):
    m = YEAR_PAREN.search(segment)
    if m:
        return int(m.group(1)), m.start(1)
    m = YEAR_BEFORE_QUALITY.search(segment)
    if m:
        return int(m.group(1)), m.start(1)
    matches = list(ANY_YEAR.finditer(segment))
    if matches:
        m = matches[-1]
        return int(m.group(1)), m.start(1)
    return None, None


def parse_folder_name(name: str):
    """Classify a name (folder or filename-without-extension). Returns type in {TV, Movie, None}."""
    se = SEASON_EP.search(name)
    if se:
        season, episode = int(se.group(1)), int(se.group(2))
        title_region = name[: se.start()]
        year, ypos = extract_year(title_region)
        if year is not None:
            title_region = title_region[:ypos]
        title = clean_title(strip_release_group(title_region)) or clean_title(title_region)
        title = strip_edition_descriptor(title)
        return {"type": "TV", "title": title, "year": year, "season": season, "episode": episode}

    year, ypos = extract_year(name)
    if year is not None:
        title_region = name[:ypos]
        title = clean_title(strip_release_group(title_region))
        if not title:
            title = clean_title(strip_release_group(name))
        title = strip_edition_descriptor(title)
        return {"type": "Movie", "title": title, "year": year, "season": None, "episode": None}

    # old-school fansub naming: "AnimeRG <Title> - <absolute episode number> ..."
    rg_match = ANIME_RG_PREFIX.match(name)
    if rg_match:
        remainder = name[rg_match.end():]
        abs_ep = ANIME_ABS_EP.match(remainder)
        if abs_ep:
            title = clean_title(abs_ep.group(1))
            return {"type": "TV", "title": title, "year": None, "season": 1, "episode": int(abs_ep.group(2))}

    return {"type": None, "title": strip_edition_descriptor(clean_title(strip_release_group(name))), "year": None, "season": None, "episode": None}


def quality_score(name: str):
    low = name.lower()
    res = max((v for k, v in RES_RANK.items() if k in low), default=0)
    src = max((v for k, v in SOURCE_RANK.items() if k in low), default=0)
    return (res, src)


def quality_label(name: str):
    """Human-readable quality string for the Dashboard's duplicate picker -
    same RES_RANK/SOURCE_RANK keyword matches quality_score() uses, just
    rendered as text instead of a sort tuple."""
    low = name.lower()
    res_key = max((k for k in RES_RANK if k in low), key=lambda k: RES_RANK[k], default=None)
    src_key = max((k for k in SOURCE_RANK if k in low), key=lambda k: SOURCE_RANK[k], default=None)
    parts = [p for p in (res_key, src_key) if p]
    return ", ".join(parts) if parts else "Unknown quality"


def guess_genre(title: str) -> str:
    low = title.lower()
    for kw in ANIME_KEYWORDS:
        if kw in low:
            return "Anime"
    for kw in MUSIC_KEYWORDS:
        if kw in low:
            return "Music"
    for kw in REALITY_KEYWORDS:
        if kw in low:
            return "Reality / Competition"
    for kw in TRUE_CRIME_KEYWORDS:
        if kw in low:
            return "True Crime"
    for kw in DOC_KEYWORDS:
        if kw in low:
            return "Documentary / History"
    return "Uncategorized"


def is_real_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False) and not entry.is_symlink()
    except OSError:
        return False


def is_video_file(entry: os.DirEntry) -> bool:
    try:
        if not entry.is_file(follow_symlinks=False):
            return False
    except OSError:
        return False
    return os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS


def analyze_folder(path: str):
    """Single walk: total size of everything in the folder, plus the path of
    the largest actual video file inside (for a direct 'play this' link)."""
    total = 0
    best_video, best_video_size = None, -1
    for dirpath, dirnames, filenames in os.walk(path):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(full)
            except OSError:
                continue
            total += sz
            if os.path.splitext(fn)[1].lower() in VIDEO_EXTENSIONS and sz > best_video_size:
                best_video, best_video_size = full, sz
    return total, best_video


def safe_mtime(raw_timestamp):
    """A corrupted filesystem timestamp in the future (confirmed real,
    twice now - see README's Creepshow incident, and the two Doctor Who
    (2005) episode files fixed by hand 2026-08-20, both ~2097) would sort
    to the top of What's New forever and never age out, since nothing
    ever exceeds it. Clamped to "now" rather than dropped to None - a
    real, sane date beats a missing one for every other place mtime gets
    displayed/sorted (entry-date fallback, latest_added)."""
    mtime = datetime.fromtimestamp(raw_timestamp)
    now = datetime.now()
    return (now if mtime > now else mtime).isoformat()


def leaf_from_folder(path, name, root_label, category, ftype_override=None, fallback_year=None):
    try:
        st = os.stat(path)
        mtime = safe_mtime(st.st_mtime)
        # Windows NTFS tracks real file-creation time separately from
        # last-modified - unlike mtime, a copy/move tool (robocopy, the downloader,
        # Auto-File's own os.rename) does NOT normally preserve it, so it
        # reflects when this copy actually landed here, not whenever the
        # original release was first put together. mtime routinely gets
        # preserved from the source and can be years old (confirmed real,
        # 2026-09-13: "Star Trek First Contact" added today has an mtime of
        # 2025-08-28; a "The Walking Dead: Dead City" episode added today
        # has an mtime of 2000-07-10) - see consolidate.py's/this file's
        # own latest_added computation, which now prefers this field.
        ctime = safe_mtime(st.st_ctime)
    except OSError:
        mtime, ctime = None, None
    total_size, video_path = analyze_folder(path)

    # A release-wrapped single episode ("Show.S01E02.Title.GROUP\Show.S01E02....mkv")
    # is a leaf FOLDER, not a leaf FILE - leaf_from_file() below already
    # checked DIRECTORY_LEAF_OVERRIDES, but this folder-leaf path never did,
    # so any override keyed to this folder's own PARENT was silently
    # ignored for wrapper-folder leaves (confirmed real gap: Expedition X's
    # "Atomic Sharks" special sits in exactly this shape and kept coming up
    # unmatched even after its override was added, 2026-08-20).
    override = apply_directory_leaf_override(path, name)
    if override:
        title, ftype, season, episode, year = override
        return {
            "path": path,
            "video_path": video_path,
            "root": root_label,
            "category": category,
            "raw_name": name,
            "title": title,
            "year": year,
            "type": ftype,
            "season": season,
            "episode": episode,
            "mtime": mtime,
            "ctime": ctime,
            "size_bytes": total_size,
        }

    parsed = parse_folder_name(name)
    norm_title = normalize_key(parsed["title"] or name)
    effective_year = parsed["year"] if parsed["year"] is not None else fallback_year
    season, episode = apply_episode_number_override(norm_title, name, parsed["season"], parsed["episode"])
    season, episode = apply_season_offset(norm_title, season, episode, effective_year)
    return {
        "path": path,
        "video_path": video_path,
        "root": root_label,
        "category": category,
        "raw_name": name,
        "title": parsed["title"] or name,
        "year": effective_year,
        "type": ftype_override or parsed["type"] or "Other",
        "season": season,
        "episode": episode,
        "mtime": mtime,
        "ctime": ctime,
        "size_bytes": total_size,
    }


def leaf_from_file(entry: os.DirEntry, root_label, category, fallback_year=None):
    stem = os.path.splitext(entry.name)[0]
    try:
        st = entry.stat()
        mtime = safe_mtime(st.st_mtime)
        ctime = safe_mtime(st.st_ctime)  # see leaf_from_folder()'s own comment on why
        size = st.st_size
    except OSError:
        mtime, ctime, size = None, None, 0

    override = apply_directory_leaf_override(entry.path, entry.name)
    if override:
        title, ftype, season, episode, year = override
        return {
            "path": entry.path,
            "video_path": entry.path,
            "root": root_label,
            "category": category,
            "raw_name": entry.name,
            "title": title,
            "year": year,
            "type": ftype,
            "season": season,
            "episode": episode,
            "mtime": mtime,
            "ctime": ctime,
            "size_bytes": size,
        }

    # Note: a loose .VOB sitting inside a VIDEO_TS\ folder never reaches this
    # function at all - process_folder() intercepts the whole VIDEO_TS
    # folder before recursing into individual files (see
    # process_video_ts_folder() below) so multi-part titles can be grouped
    # and combined into one continuous-playback entry, which requires
    # seeing every file in the folder together, not one at a time.

    parsed = parse_folder_name(stem)
    norm_title = normalize_key(parsed["title"] or entry.name)
    effective_year = parsed["year"] if parsed["year"] is not None else fallback_year
    season, episode = apply_episode_number_override(norm_title, entry.name, parsed["season"], parsed["episode"])
    season, episode = apply_season_offset(norm_title, season, episode, effective_year)
    return {
        "path": entry.path,
        "video_path": entry.path,
        "root": root_label,
        "category": category,
        "raw_name": entry.name,
        "title": parsed["title"] or entry.name,
        "year": effective_year,
        "type": parsed["type"] or "Other",
        "season": season,
        "episode": episode,
        "mtime": mtime,
        "ctime": ctime,
        "size_bytes": size,
    }


def folder_wraps_episode_files(path, depth=0):
    """A folder whose own name parses as a bare Movie (Title (Year), no
    season/episode marker) can still actually be a TV show's top-level
    folder, with the real season/episode identity living in files nested
    inside instead - either loose episode files directly inside, or
    per-episode/per-season subfolders. Checked recursively (bounded by
    MAX_DEPTH like the main walk) since episodes can be nested more than
    one level deep. Confirmed real cases this was written for: "The Stand
    (2020)" (loose S01E05-07 files + per-episode S01E08/E09 subfolders) and
    "The.Maxx.(1995)...NEOeclipse" (S00/S01 subfolders)."""
    if depth >= MAX_DEPTH:
        return False
    try:
        with os.scandir(path) as it:
            entries = list(it)
    except OSError:
        return False
    for e in entries:
        if is_video_file(e) and SEASON_EP.search(e.name):
            return True
        if is_real_dir(e) and folder_wraps_episode_files(e.path, depth + 1):
            return True
    return False


def process_video_ts_folder(path, root_label, category, fallback_year=None):
    """A ripped DVD's VIDEO_TS\\ folder: VTS_NN_P.VOB parts sharing the same
    titleset (NN) are combined into ONE entry (play continuously via
    playlist_extra_paths - see the openvlc:// URI builders in
    app\\render_engine.py/app\\web_server.py and handlers\\OpenVlcHandler.cs
    for the other half of this), sorted by part number so the whole title
    plays start to finish instead of stopping after ~15-20 minutes (the
    first part alone) - confirmed real, not a guess: the user reported this
    exact symptom for two real DVDs, 2026-08-21.

    A DIFFERENT titleset is a genuinely separate piece of content (never
    combined with another titleset's parts) - confirmed real: "Nine Inch
    Nails - The Downward Spiral - Live" has titleset 01 (the main concert,
    4 parts) and titleset 02 (a smaller bonus feature, 1 part) sitting in
    the SAME VIDEO_TS folder. The bare VIDEO_TS.VOB (no VTS_ prefix) is the
    disc-level menu present on every DVD authored this way - not a titleset
    at all (VTS_PART_RE never matches it), so it's excluded here rather
    than becoming a library entry of its own."""
    try:
        st = os.stat(path)
        mtime = safe_mtime(st.st_mtime)
        ctime = safe_mtime(st.st_ctime)  # see leaf_from_folder()'s own comment on why
    except OSError:
        mtime, ctime = None, None

    by_titleset = {}
    for e in os.scandir(path):
        m = VTS_PART_RE.match(e.name)
        if m:
            by_titleset.setdefault(m.group(1), []).append((int(m.group(2)), e))

    disc_title = os.path.basename(os.path.dirname(path))
    parsed = parse_folder_name(disc_title)

    for titleset in sorted(by_titleset):
        parts = sorted(by_titleset[titleset], key=lambda pe: pe[0])
        ordered = [e for _, e in parts]
        total_size = 0
        for e in ordered:
            try:
                total_size += e.stat().st_size
            except OSError:
                pass
        primary = ordered[0]
        extra_paths = [e.path for e in ordered[1:]]
        yield {
            "path": primary.path,
            "video_path": primary.path,
            "root": root_label,
            "category": category,
            "raw_name": primary.name,
            "title": parsed["title"] or disc_title,
            "year": parsed["year"] if parsed["year"] is not None else fallback_year,
            # Season/episode never inferred from a VOB's own filename
            # (VTS_01_1 is a disc-structure index, not an episode number).
            "type": "Movie",
            "season": None,
            "episode": None,
            "mtime": mtime,
            "ctime": ctime,
            "size_bytes": total_size,
            "playlist_extra_paths": extra_paths or None,
        }


def process_folder(path, root_label, depth=0, category=None, fallback_year=None):
    """Recursively flatten into leaf items: one per actual episode/movie/version,
    whether it's a folder-wrapped release or a loose video file. fallback_year
    carries a year down from an ancestor folder's own name (see below) for
    leaf items that don't have their own - never overrides a leaf's own
    year, only fills the gap when nothing more specific exists."""
    name = os.path.basename(path.rstrip("\\/"))
    if is_ignored_dir(path):
        return  # opted out - see IGNORE_MARKER / scan_ignore.txt
    parsed = parse_folder_name(name)

    if parsed["type"] == "Movie" and folder_wraps_episode_files(path):
        # The folder's own name looks like a bare movie release (no
        # season/episode marker) but really wraps a TV show - don't trust
        # the folder-name-only Movie parse, fall through to the normal
        # recursive scan below so the real per-file/per-subfolder episode
        # identity is used instead. The year IS still worth keeping though -
        # confirmed real case: "Doctor Who (1963)"/"(2005)"/"(2023)", three
        # separate productions reorganized into per-era folders, where the
        # year lives ONLY in the top folder name - individual episode
        # filenames never carry one (unlike "The Stand (2020)", where every
        # episode filename did). Without carrying it down as a fallback,
        # every episode across all three eras would show year=None and be
        # indistinguishable by year at all, silently defeating
        # SPLIT_BY_YEAR_TITLES for exactly the case it exists for.
        if parsed["year"] is not None:
            fallback_year = parsed["year"]
        parsed = {"type": None, "title": parsed["title"], "year": None, "season": None, "episode": None}

    if parsed["type"] is not None:
        yield leaf_from_folder(path, name, root_label, category, fallback_year=fallback_year)
        return

    if depth >= MAX_DEPTH:
        yield leaf_from_folder(path, name, root_label, category, ftype_override="Other", fallback_year=fallback_year)
        return

    try:
        with os.scandir(path) as it:
            entries = list(it)
    except OSError:
        entries = []
    dirs = [e for e in entries if is_real_dir(e)]
    files = [e for e in entries if is_video_file(e)]

    if name.upper() == "VIDEO_TS" and files:
        # Must see every file in the folder together to group multi-part
        # titlesets correctly - never routed through the generic per-file
        # leaf_from_file() path below, even for a lone single-part disc.
        yield from process_video_ts_folder(path, root_label, category, fallback_year=fallback_year)
        return

    if not dirs:
        if not files:
            return  # empty organizational folder
        if len(files) == 1:
            # This folder's own name already failed to parse (checked at the
            # top of this function) - two real cases produce that: a release
            # folder wrapping junk around a genuinely unparseable video name
            # (identity lives in the folder name, which has none - keep
            # today's behavior, "Other"), or a plain organizational folder
            # like Auto-File's own "Season NN" layout (roadmap item 24 -
            # never has season/episode in ITS OWN name by design, that lives
            # in each contained file's name instead). Prefer the file's own
            # name whenever it parses successfully, rather than discarding
            # real season/episode info the folder name never had to begin
            # with - this is what a freshly-filed episode landing alone in a
            # brand-new season folder needs to be identified correctly.
            file_parsed = parse_folder_name(os.path.splitext(files[0].name)[0])
            if file_parsed["type"] is not None:
                yield leaf_from_file(files[0], root_label, category, fallback_year=fallback_year)
            else:
                # this folder just wraps one release (video + nfo/jpg
                # siblings) - treat the folder itself as the item so extras
                # stay bundled
                yield leaf_from_folder(path, name, root_label, category, fallback_year=fallback_year)
        else:
            # multiple loose videos directly here with no wrapping folder -
            # catalog each independently
            for f in files:
                yield leaf_from_file(f, root_label, category, fallback_year=fallback_year)
        return

    for d in dirs:
        yield from process_folder(d.path, root_label, depth + 1, category, fallback_year=fallback_year)
    for f in files:
        yield leaf_from_file(f, root_label, category, fallback_year=fallback_year)


def scan_root(root_label, root_path):
    items = []
    if not os.path.isdir(root_path):
        print(f"WARNING: root not found: {root_path}", file=sys.stderr)
        return items
    with os.scandir(root_path) as it:
        entries = list(it)
    dirs = [e for e in entries
            if is_real_dir(e) and e.name != QUARANTINE_FOLDER_NAME and not is_ignored_dir(e.path)]
    files = [e for e in entries if is_video_file(e)]
    for entry in dirs:
        items.extend(process_folder(entry.path, root_label, depth=0))
    for f in files:
        items.append(leaf_from_file(f, root_label, None))
    return items


def scan_archive():
    items = []
    if not os.path.isdir(ARCHIVE_ROOT):
        print(f"WARNING: archive root not found: {ARCHIVE_ROOT or '(not set)'}", file=sys.stderr)
        return items
    with os.scandir(ARCHIVE_ROOT) as it:
        entries = list(it)
    dirs = [e for e in entries
            if is_real_dir(e) and e.name not in ARCHIVE_SKIP_SUBFOLDERS and not is_ignored_dir(e.path)]
    files = [e for e in entries if is_video_file(e)]
    for cat_entry in dirs:
        category = cat_entry.name
        with os.scandir(cat_entry.path) as it2:
            sub_entries = list(it2)
        sub_dirs = [e for e in sub_entries if is_real_dir(e)]
        sub_files = [e for e in sub_entries if is_video_file(e)]
        for d in sub_dirs:
            items.extend(process_folder(d.path, ARCHIVE_ROOT_LABEL, depth=0, category=category))
        for f in sub_files:
            items.append(leaf_from_file(f, ARCHIVE_ROOT_LABEL, category))
    for f in files:
        items.append(leaf_from_file(f, ARCHIVE_ROOT_LABEL, None))  # loose files at <library>/ root, uncategorized
    return items


def main():
    # Preserve fields that only get computed by the nightly-only pipeline
    # steps (enrich_tv_coverage.py's missing-episode detection and
    # per-episode air dates, detect_runtime_mismatch.py's movie runtime
    # check) - this script rebuilds media_db.json from scratch every run,
    # including the fast startup quick-scan, which doesn't run those slower
    # steps. Without carrying these forward, every server restart would
    # silently wipe them until the next full refresh (nightly or manual)
    # recomputes them.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = _CFG_DATA_DIR
    out_path = os.path.join(data_dir, "media_db.json")
    prev_completeness_by_key = {}
    prev_air_dates = {}
    prev_episode_titles = {}
    prev_shows = []  # stays empty on a first run or when the old file is unreadable
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                prev_shows = json.load(f)
            for ps in prev_shows:
                ps_norm_title = normalize_key(ps["title"])
                ps_key = (ps_norm_title, ps["type"], year_bucket_for(ps_norm_title, ps.get("years")))
                prev_completeness_by_key[ps_key] = {
                    field: ps[field] for field in (
                        "missing_episodes", "missing_seasons", "episodes_expected_so_far",
                        "assumed_physical_media", "completeness_skip_reason",
                        "runtime_mismatch", "file_runtime_minutes",
                        "wiki_matched", "wiki_title", "wiki_extract", "wiki_page_url", "wiki_image_path",
                        # Added 2026-08-21, same bug, one more time: wiki_source
                        # (enrich_wikipedia.py) says whether wiki_extract/
                        # wiki_page_url really came from Wikipedia or from a
                        # hand-written non-Wikipedia source entry (see
                        # Reorg_Session_Insights.md, 2026-08-21 - "Queens of
                        # the Stone Age Glastonbury", sourced from NME). Left
                        # out of this tuple, a quick-scan restart would wipe
                        # it and the display label would silently go back to
                        # claiming "via Wikipedia" for a non-Wikipedia source.
                        "wiki_source",
                        # Added 2026-08-20: both were nightly/manual-only
                        # computed fields missing from this whitelist,
                        # same silent-wipe-on-restart bug as the others
                        # here - physical_seasons_owned (enrich_tv_coverage.py,
                        # cross-referenced against the physical collection)
                        # was caught live; epguides_url (enrich_epguides.py)
                        # was found the same way but had been silently
                        # resetting on every quick-scan restart already,
                        # just never as visibly as "9 seasons missing" was.
                        "physical_seasons_owned", "epguides_url",
                        # Added 2026-08-21, same bug, caught before it ever
                        # shipped wrong instead of after: tracking_start_date
                        # (enrich_tv_coverage.py) is a frozen-once date for a
                        # genre-excluded show's own missing-episode tracking -
                        # if a quick-scan restart silently dropped it, the
                        # very next nightly/manual refresh would re-assign it
                        # to THAT day instead of when tracking actually began,
                        # quietly re-opening the backlog window this feature
                        # exists to prevent.
                        "tracking_start_date",
                    ) if field in ps
                }
                for e in ps.get("entries", []):
                    if e.get("air_date"):
                        prev_air_dates[e["path"]] = e["air_date"]
                    if e.get("episode_title"):
                        prev_episode_titles[e["path"]] = e["episode_title"]
        except (json.JSONDecodeError, OSError):
            pass

    all_items = []
    _roots = list(ROOTS.items())
    for n, (label, (path, _kind)) in enumerate(_roots):
        step_progress.report(n, len(_roots) + 2, "locations", label)
        all_items.extend(scan_root(label, path))
    step_progress.report(len(_roots), len(_roots) + 2, "locations", "Archive")
    all_items.extend(scan_archive())
    step_progress.report(len(_roots) + 1, len(_roots) + 2, "locations", "Building the library list")

    groups = {}
    for item in all_items:
        norm_title = normalize_key(item["title"])
        key = (norm_title, item["type"], year_bucket_for(norm_title, item["year"]))
        groups.setdefault(key, []).append(item)

    shows = []
    for (norm_title, ftype, _year_bucket), members in groups.items():
        display_title = max((m["title"] for m in members), key=len)
        years = sorted({m["year"] for m in members if m["year"]})
        roots_present = sorted({m["root"] for m in members})
        categories_present = sorted({m["category"] for m in members if m["category"]})

        entries_out = []
        episode_keys = set()
        for m in members:
            entries_out.append({
                "path": m["path"], "video_path": m.get("video_path"), "root": m["root"], "category": m["category"],
                "name": m["raw_name"], "season": m["season"], "episode": m["episode"],
                "mtime": m["mtime"], "ctime": m.get("ctime"), "size_bytes": m.get("size_bytes", 0),
                # Sibling VOB parts (process_video_ts_folder()) to queue
                # after video_path in the same VLC launch, so a multi-part
                # disc title plays start to finish instead of stopping
                # after just the first part. None for every normal entry.
                "playlist_extra_paths": m.get("playlist_extra_paths"),
            })
            if ftype == "TV" and m["season"] is not None:
                episode_keys.add((m["season"], m["episode"]))

        episode_count = len(episode_keys) if ftype == "TV" else None
        if ftype == "TV" and episode_count == 0:
            episode_count = len(members)

        # duplicate-copy detection, ranked by resolution+source quality first,
        # falling back to file size as a tiebreaker within the same tier.
        # Entries with no video file (video_path is None - e.g. a release
        # folder Auto-File already extracted the video out of, leaving only
        # a leftover .nfo/.jpg behind per its "never touch anything else"
        # rule) are excluded here: they're not a real copy of anything, and
        # quality_score() reading a still-original scene-release name would
        # otherwise routinely outrank Auto-File's own deliberately-stripped
        # clean filename, wrongly flagging the real file as the "duplicate."
        #
        # ALT_VERSION_KEYWORDS in a filename (alongside a real season/episode
        # number, unlike the placeholder-number Doctor Who problem above)
        # means a genuinely different cut of the same episode, not a
        # redundant re-encode - confirmed real: "Mythic Quest" S04E10 has
        # both the finale and a file explicitly labeled ALTERNATIVE.CUT,
        # and quality_score() alone (which only reads resolution/source
        # tags) had no way to know the second one wasn't just a worse copy
        # of the first, so it flagged 2.1 GB of real, distinct content as
        # wasted space. Given its own key tag, so it competes for the
        # "keep" slot only against another copy of the *same* variant, not
        # against the main episode - still dedupes correctly if the same
        # alternate cut genuinely gets downloaded twice.
        # normalize_key(), not a plain .lower() - release names separate
        # words with dots/underscores ("ALTERNATIVE.CUT"), which a raw
        # substring check against "alternative cut" would never match.
        alt_tag = lambda e: next((kw for kw in ALT_VERSION_KEYWORDS if kw in normalize_key(e["name"])), None)
        # Distinguishes different VIDEO_TS titlesets under the same show
        # (process_video_ts_folder() above) so they never compete against
        # each other for the "keep" slot - each is genuinely separate
        # content (a main feature vs. a bonus feature living on the same
        # disc), not quality-alternates of one thing. Every part WITHIN one
        # titleset is already combined into a single entry before this
        # runs (playlist_extra_paths), so there's nothing left to
        # distinguish there - this only ever separates entry from entry.
        vts_tag = lambda e: (VTS_PART_RE.match(os.path.basename(e["video_path"])).group(1)
                              if e.get("video_path") and VTS_PART_RE.match(os.path.basename(e["video_path"])) else None)
        dup_groups = {}
        if ftype == "TV":
            for e in entries_out:
                if e["season"] is not None and e.get("video_path"):
                    dup_groups.setdefault((e["season"], e["episode"], alt_tag(e)), []).append(e)
        else:
            for e in entries_out:
                if e.get("video_path"):
                    dup_groups.setdefault((None, None, alt_tag(e), vts_tag(e)), []).append(e)

        # A folder-wrapped leaf's OWN name (e["name"]) is often a cleaned-up
        # release/category name with no resolution tag at all (e.g. the downloader's
        # "Attack on Titan S03E03", no "1080p") even though the real video
        # file one level down still carries it in its filename. Scoring
        # quality against e["name"] alone made a not-yet-filed 1080p
        # download lose to an already-archived 720p file every time - it
        # scored quality_score(0, 0), below literally any tagged filename.
        # Confirmed real: exactly this happened to Attack on Titan S03E03/
        # 05/06 in this session (2026-08-26) - the archived 720p copies
        # outranked the freshly downloaded 1080p ones and got marked "keep"
        # while the better copies were queued for quarantine as "extras".
        # Falling back to the actual video file's basename fixes it without
        # touching leaf_from_file entries (already correctly named).
        dup_quality_name = lambda e: os.path.basename(e["video_path"]) if e.get("video_path") else e["name"]
        # Per-file detail objects for the Dashboard's duplicate picker (real
        # name/size/quality-label for every copy in a group, not just the
        # bare path strings extra_paths/keep_path already carry for the
        # .ps1-script consumers) - additive fields only, existing consumers
        # (build_quarantine_plan.py, the old summary panel) are untouched.
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
            group_sorted = sorted(
                group, key=lambda e: (quality_score(dup_quality_name(e)), e["size_bytes"]), reverse=True
            )
            keep, extras = group_sorted[0], group_sorted[1:]
            wasted = sum(e["size_bytes"] for e in extras)
            wasted_bytes += wasted
            duplicates.append({
                "season": key[0],
                "episode": key[1],
                "keep_path": keep["path"],
                "keep_quality": quality_score(dup_quality_name(keep)),
                "extra_paths": [e["path"] for e in extras],
                "wasted_bytes": wasted,
                "keep_detail": dup_detail(keep),
                "extra_details": [dup_detail(e) for e in extras],
            })

        # Stage classification only considers entries with a real video file
        # - a video-less leftover (e.g. a thumbnail.jpg Auto-File left
        # behind after extracting the video, see roadmap item 25) isn't
        # real unfiled content, so it shouldn't make a fully-archived show
        # look like it still has something coming. roots_present above
        # stays unfiltered - the leftover still shows up as its own entry
        # and is still findable via the location filter, this only affects
        # the show-level stage label.
        stage_roots = {e["root"] for e in entries_out if e.get("video_path")}
        kinds_present = {ROOT_KIND.get(r, "queue") for r in stage_roots}
        if "archived" in kinds_present and len(kinds_present) == 1:
            pipeline_stage = "Archived"
        elif "archived" in kinds_present:
            pipeline_stage = "Archived + still in queue"
        else:
            pipeline_stage = "In Queue"

        total_size_bytes = sum(e["size_bytes"] for e in entries_out)
        genre = guess_genre(display_title)
        # ctime (when this copy actually landed on disk) preferred over
        # mtime (often preserved from the original release, sometimes
        # years stale - see leaf_from_folder()'s own comment) for "What's
        # New"/sort-by-recent's sake; falls back to mtime only for the rare
        # entry missing ctime (an OSError at scan time).
        latest_mtime = max((m.get("ctime") or m["mtime"] for m in members if m.get("ctime") or m["mtime"]), default=None)
        entries_out.sort(key=lambda e: (e["season"] or 0, e["episode"] or 0, e["name"]))

        shows.append({
            "title": display_title,
            "type": ftype,
            "genre": genre,
            "years": years,
            "episode_count": episode_count,
            "version_count": len(members),
            "roots": roots_present,
            "archive_categories": categories_present,
            "pipeline_stage": pipeline_stage,
            "entries": entries_out,
            "latest_added": latest_mtime,
            "total_size_bytes": total_size_bytes,
            "wasted_bytes": wasted_bytes,
            "duplicates": duplicates,
        })

    for s in shows:
        if s["title"] and s["title"] == s["title"].lower():
            s["title"] = s["title"].title()

    carried_completeness = 0
    carried_air_dates = 0
    for s in shows:
        s_norm_title = normalize_key(s["title"])
        key = (s_norm_title, s["type"], year_bucket_for(s_norm_title, s.get("years")))
        prev_fields = prev_completeness_by_key.get(key)
        if prev_fields:
            s.update(prev_fields)
            carried_completeness += 1
        for e in s["entries"]:
            air_date = prev_air_dates.get(e["path"])
            if air_date:
                e["air_date"] = air_date
                carried_air_dates += 1
            episode_title = prev_episode_titles.get(e["path"])
            if episode_title:
                e["episode_title"] = episode_title

    # Mover-agnostic rename/move detection, so watched/retention_override/
    # travel_selection state survives ANY file move - not just ones a
    # specific tool remembered to report. heal_reorg_state.py already
    # carries state forward for renames the reorg tooling explicitly logs
    # to logs\reorg\*.jsonl, and Auto-File carries forward its own renames
    # directly - both are high-confidence since those movers KNOW exactly
    # what they did. This is the safety net underneath both: a manual
    # rename, a future script that never learns about the audit-log
    # convention, drag-and-drop in Explorer, anything - by the time this
    # runs, all we can see is "this path vanished, this other path is new."
    # A file's exact byte size plus its modified-time survives a plain
    # move/rename on NTFS (MoveFile/robocopy's default copy flags/Explorer
    # drag-and-drop all preserve mtime) even though the path changed, so
    # that pair is used as a fingerprint: an orphaned old path and a
    # brand-new path sharing an EXACT, UNIQUE fingerprint is almost
    # certainly the same file. "Unique" is the safety rail - if two or
    # more candidates share a fingerprint on either side, the match is
    # ambiguous and is deliberately skipped rather than guessed at (a
    # missed migration just means that one file's state resets to
    # Unwatched on the next prune, per resolve_watched()'s own no-record-
    # means-unwatched rule - safe by construction; a WRONG migration would
    # silently hand one file's watched history to a different file, which
    # is worse and not an acceptable trade for catching a few more edge
    # cases). Runs every single scan (startup and full refresh alike),
    # since a rename can happen at any time, not just during a nightly
    # refresh.
    prev_paths_meta = {
        e["path"]: (e.get("size_bytes"), e.get("mtime"))
        for ps in prev_shows for e in ps.get("entries", [])
    }
    new_paths_meta = {
        e["path"]: (e.get("size_bytes"), e.get("mtime"))
        for s in shows for e in s["entries"]
    }
    orphaned_paths = set(prev_paths_meta) - set(new_paths_meta)
    renamed_count = ambiguous_count = 0
    if orphaned_paths:
        genuinely_new_paths = set(new_paths_meta) - set(prev_paths_meta)
        fp_to_old = {}
        for p in orphaned_paths:
            fp_to_old.setdefault(prev_paths_meta[p], []).append(p)
        fp_to_new = {}
        for p in genuinely_new_paths:
            fp_to_new.setdefault(new_paths_meta[p], []).append(p)

        moves = []
        for fp, old_candidates in fp_to_old.items():
            new_candidates = fp_to_new.get(fp)
            if not new_candidates or fp[0] is None or fp[1] is None:
                continue  # no candidate, or a fingerprint too incomplete to trust
            if len(old_candidates) == 1 and len(new_candidates) == 1:
                moves.append((old_candidates[0], new_candidates[0]))
            else:
                ambiguous_count += len(old_candidates)  # multiple plausible matches - don't guess

        if moves:
            sys.path.insert(0, os.path.join(project_root, "plans"))
            from migrate_state_paths import migrate  # noqa: E402
            migrate(moves)
            renamed_count = len(moves)

    shows.sort(key=lambda s: s["title"].lower())

    # Atomic write - web_server.py's load_shows() reads this file on every
    # single page request while the server is live, including while this
    # script is running (startup quick-scan, nightly refresh). A direct
    # write here leaves a window where a concurrent read sees a truncated/
    # torn JSON file and 500s - confirmed real, not theoretical (caught
    # live 2026-08-21: "JSONDecodeError: Expecting ':' delimiter" mid-write
    # on a real page load). tmp-then-replace makes the swap atomic on
    # Windows, so a reader always sees either the old complete file or the
    # new complete file, never a partial one.
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=2)
    # atomic_replace(), not a bare os.replace() - see that module's own
    # docstring for the real 2026-08-28 incident (the companion tool's console.py,
    # not this project's own process, briefly holding a read handle on
    # this exact file) this retries through instead of crashing over.
    atomic_replace(tmp, out_path)

    by_type = {}
    for s in shows:
        by_type[s["type"]] = by_type.get(s["type"], 0) + 1
    by_stage = {}
    for s in shows:
        by_stage[s["pipeline_stage"]] = by_stage.get(s["pipeline_stage"], 0) + 1

    print("Total grouped entries:", len(shows))
    for t, c in sorted(by_type.items()):
        print(f"  {t}: {c}")
    print("Pipeline stages:")
    for st, c in sorted(by_stage.items()):
        print(f"  {st}: {c}")
    print("Total leaf items scanned:", len(all_items))
    total_size = sum(s["total_size_bytes"] for s in shows)
    total_wasted = sum(s["wasted_bytes"] for s in shows)
    print(f"Total library size: {total_size / 1e9:.1f} GB")
    print(f"Reclaimable from duplicate copies: {total_wasted / 1e9:.1f} GB")
    print(f"Carried forward from previous scan: {carried_completeness} shows' completeness data, {carried_air_dates} episode air dates")
    if renamed_count or ambiguous_count:
        print(f"Detected {renamed_count} moved/renamed file(s) by size+modified-time match - watched/retention/travel state carried forward automatically.")
        if ambiguous_count:
            print(f"  {ambiguous_count} orphaned path(s) had an ambiguous match (multiple candidates) and were left alone - any tracked state there resets to Unwatched rather than being guessed at.")
    print("Wrote", out_path)


if __name__ == "__main__":
    main()

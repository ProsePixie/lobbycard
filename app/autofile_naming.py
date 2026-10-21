"""Naming/validation logic for the Auto-File Queue feature - the full spec
the user provided 2026-08-17 (see README roadmap item 24). Kept separate
from web_server.py's job-orchestration/threading/status-tracking so the
naming rules can be unit-tested in isolation against real temp files.

TMDB is the sole source of truth for season/episode numbers and titles.
The original scene-release filename is used ONLY to identify which episode
a file represents and to read its resolution tag - never trusted for
anything else without validating against TMDB first. See validate_tv_episode()
for the "never guess" enforcement.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "pipeline"))
from scan_media import quality_score, VIDEO_EXTENSIONS  # noqa: E402  (resolution+source ranking, §8)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "plans"))
from build_movie_reorg_manifest import bucket_for, BUCKET_CATEGORIES  # noqa: E402
# ^ single source of truth for the Movies alphabetical-bucket rule (see that
# module's own docstring + Reorg_Session_Insights.md, 2026-08-21) - Auto-File
# must file new movies into the same bucket structure the reorg pass created,
# or a brand-new movie (existing_movie_dir=None) lands in a stale flat path.

from app_config import ARCHIVE_ROOT, TMDB_API_BASE  # noqa: E402

ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*]')
WHITESPACE_UNDERSCORE_RE = re.compile(r"[\s_]+")
MULTI_DOT_RE = re.compile(r"\.{2,}")
SEASON_DIR_RE = re.compile(r"Season \d+$", re.IGNORECASE)

RESOLUTION_TIERS = [
    (4, re.compile(r"2160p|4k|uhd", re.IGNORECASE)),
    (3, re.compile(r"1080p", re.IGNORECASE)),
    (2, re.compile(r"720p", re.IGNORECASE)),
    (1, re.compile(r"480p", re.IGNORECASE)),
]
RESOLUTION_LABELS = {4: "2160p", 3: "1080p", 2: "720p", 1: "480p"}

# e.g. "S06E16-17" or "S06E16-E17" or "S06E16E17" - a second episode number
# glued onto the first. Deliberately conservative: only fires on an actual
# multi-episode SHAPE, never on a bare "S06E16".
MULTI_EPISODE_RE = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,3}(?:-[Ee]?\d{1,3}|[Ee]\d{1,3})", re.IGNORECASE)

SIDECAR_SUBTITLE_EXTS = {".srt", ".ass", ".sub", ".idx", ".vtt"}


# ---------- §5 dot-token text conversion ----------

def strip_illegal_chars(text):
    return ILLEGAL_CHARS_RE.sub("", text or "")


def dot_tokenize(text):
    t = strip_illegal_chars(text)
    t = WHITESPACE_UNDERSCORE_RE.sub(".", t)
    t = MULTI_DOT_RE.sub(".", t)
    return t.strip(".")


# ---------- §1/§4 resolution tag ----------

def extract_resolution_tag(*filenames):
    """Highest-ranked resolution token found across any of the given
    original filenames/paths (source/codec/release-group tags are simply
    never looked for - they're dropped by construction, not stripped)."""
    best_rank = 0
    for name in filenames:
        if not name:
            continue
        for rank, pattern in RESOLUTION_TIERS:
            if rank > best_rank and pattern.search(name):
                best_rank = rank
    return RESOLUTION_LABELS.get(best_rank)


def looks_multi_episode(*filenames):
    return any(name and MULTI_EPISODE_RE.search(name) for name in filenames)


# ---------- §2/§3/§6 destination folders ----------

def show_folder_name(tmdb_name):
    """§6: TMDB canonical name, natural spacing kept - only illegal
    Windows-filename characters stripped, no dot-tokenizing."""
    return strip_illegal_chars(tmdb_name).strip()


def tv_season_folder(season_num):
    return "Specials" if season_num == 0 else f"Season {season_num:02d}"


def tv_dest_dir(category, show_name, season_num, existing_show_dir=None):
    """existing_show_dir: pass the show's already-archived folder (if any)
    so category/show-folder-name are preserved rather than re-guessed,
    per §6. Season/Specials subfolder is always appended fresh."""
    show_dir = existing_show_dir or os.path.join(ARCHIVE_ROOT, category, show_folder_name(show_name))
    return os.path.join(show_dir, tv_season_folder(season_num))


def movie_name_year(title, year):
    base = strip_illegal_chars(title).strip()
    return f"{base} ({year})" if year else base


def movie_dest_dir(category, title, year, existing_movie_dir=None):
    if existing_movie_dir:
        return existing_movie_dir
    folder_name = movie_name_year(title, year)
    if category in BUCKET_CATEGORIES:
        return os.path.join(ARCHIVE_ROOT, category, bucket_for(folder_name), folder_name)
    return os.path.join(ARCHIVE_ROOT, category, folder_name)


# ---------- §1/§4 filenames ----------

def tv_filename(show_tmdb_name, season_num, episode_num, episode_name, resolution, ext):
    se = f"S{season_num:02d}E{episode_num:02d}"
    title_part = f".{dot_tokenize(episode_name)}" if episode_name else ""
    res_part = f".{resolution}" if resolution else ""
    return f"{dot_tokenize(show_tmdb_name)}.{se}{title_part}{res_part}{ext}"


def movie_filename(title, year, resolution, ext):
    res_part = f".{resolution}" if resolution else ""
    return f"{movie_name_year(title, year)}{res_part}{ext}"


# ---------- §10 "confirmed" validation: real TMDB episode, never guessed ----------

def _tmdb_api_key(data_dir):
    key = (os.environ.get("MEDIABOX_TMDB_API_KEY") or os.environ.get("TMDB_API_KEY") or "").strip()
    if not key:
        key_file = os.path.join(data_dir, "tmdb_api_key.txt")
        if os.path.exists(key_file):
            key = open(key_file, encoding="utf-8").read().strip()
    return key


def _fetch_season_episodes_live(tmdb_id, season_num, api_key):
    url = f"{TMDB_API_BASE}/tv/{tmdb_id}/season/{season_num}?{urllib.parse.urlencode({'api_key': api_key})}"
    data = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {}
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1.0)
            continue
    episodes = {}
    for ep in (data or {}).get("episodes", []):
        if ep.get("air_date"):
            episodes[str(ep["episode_number"])] = {
                "air_date": ep["air_date"], "name": ep.get("name") or f"Episode {ep['episode_number']}",
            }
    return episodes


def _season_cache_is_stale(entry, episode_num):
    """A cached season marked complete=true is trusted forever by the check
    below - but a `complete` entry fetched weeks before a new episode aired
    is simply wrong (real case, Diners Drive-Ins and Dives S56E06: cache
    fetched 2026-08-20, episode aired 2026-09-04, season still marked
    complete). Treat it as stale - re-fetch live - when it's more than
    STALE_DAYS old AND the episode being asked about is past the last one
    it knows, i.e. plausibly a newly-added episode rather than a genuine
    gap."""
    STALE_DAYS = 10
    fetched_at = entry.get("fetched_at")
    if not fetched_at:
        return True
    try:
        when = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)  # cache writes UTC, naive
    age = datetime.now(timezone.utc) - when
    if age.days < STALE_DAYS:
        return False
    known = [int(k) for k in entry.get("episodes", {}) if str(k).isdigit()]
    return bool(known) and episode_num > max(known)


_SEASON_NAME_RE = re.compile(r"^\s*(?:series|season)\s+(\d+)\s*$", re.IGNORECASE)


def named_season_to_tmdb(tmdb_id, season_num, data_dir):
    """File/release season number -> TMDB season number, via TMDB's own
    season NAMES. Some shows' TMDB entries renumber from 1 while naming
    the seasons after the real series (The Great British Bake Off, tmdb
    87012: TMDB season 10 is named "Series 17"; releases and the archive
    say S17). Returns season_num unchanged unless a season is named
    "Series/Season <season_num>" under a different number. Cached per
    show for a week in data/tmdb_season_names.json."""
    path = os.path.join(data_dir, "tmdb_season_names.json")
    cache = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}
    entry = cache.get(str(tmdb_id))
    fresh = False
    if entry:
        try:
            fresh = (datetime.now(timezone.utc) - datetime.fromisoformat(entry["fetched_at"])).days < 7
        except (KeyError, ValueError):
            fresh = False
    if not fresh:
        api_key = _tmdb_api_key(data_dir)
        if not api_key:
            return season_num
        url = f"{TMDB_API_BASE}/tv/{tmdb_id}?{urllib.parse.urlencode({'api_key': api_key})}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            return season_num
        mapping = {}
        for s in data.get("seasons") or []:
            m = _SEASON_NAME_RE.match(s.get("name") or "")
            if m and int(m.group(1)) != s.get("season_number"):
                mapping[m.group(1)] = s["season_number"]
        entry = {"map": mapping, "fetched_at": datetime.now(timezone.utc).isoformat()}
        cache[str(tmdb_id)] = entry
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        os.replace(tmp, path)
    return entry["map"].get(str(season_num), season_num)


def validate_tv_episode(tmdb_id, season_num, episode_num, season_cache_path, data_dir,
                        is_current_airing_season=False):
    """Returns the real TMDB episode name if (tmdb_id, season, episode) is a
    confirmed real TMDB episode, else None - the file gets flagged, never
    renamed on a guess. Checks the durable local season cache
    (tmdb_season_cache.json) first; falls back to a one-off live TMDB fetch
    for shows enrich_tv_coverage.py's nightly pass never looks at (cooking/
    reality/documentary/talk/news - see its EXCLUDED_TV_TYPES/
    EXCLUDED_GENRES) rather than refusing to file an otherwise-valid
    episode just because nothing pre-fetched it. Persists what it fetches
    back into the same cache, respecting the same complete/provisional
    contract enrich_tv_coverage.py uses (a still-airing show's current
    season is never marked permanently complete, so the nightly pass keeps
    checking it for newly-aired episodes)."""
    cache = {}
    if os.path.exists(season_cache_path):
        try:
            with open(season_cache_path, encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}

    season_num = named_season_to_tmdb(tmdb_id, season_num, data_dir)
    key = f"{tmdb_id}:{season_num}"
    entry = cache.get(key)
    if entry:
        ep = entry.get("episodes", {}).get(str(episode_num))
        # A real bug, confirmed live (2026-09): TMDB often creates the
        # episode NUMBER right around air date before a contributor has
        # written the title text - ep exists with name:"" for days/weeks.
        # This used to be treated as "confirmed, file it" and silently
        # produced a title-less filename (Silo S03E09 and 97 other real
        # episodes found this way) - blank is now treated the same as
        # "not confirmed yet" so autofile flags it for review instead of
        # filing on a name that isn't there yet. Never files on a guess,
        # same standard as every other case here - an empty string is
        # just as much a guess as a missing episode.
        if ep and ep.get("name"):
            return ep["name"]
        if ep:
            return None  # TMDB knows this episode exists but hasn't published a title yet
        if entry.get("complete", True) and not is_current_airing_season \
                and not _season_cache_is_stale(entry, episode_num):
            return None  # confirmed-complete season, this episode genuinely doesn't exist
        # else: still-airing season, or a weeks-old "complete" cache that
        # predates a newly-aired episode - fall through to a live re-check
        # rather than trusting the stale snapshot (Diners S56E06, 2026-09).

    api_key = _tmdb_api_key(data_dir)
    if not api_key:
        return None
    fetched = _fetch_season_episodes_live(tmdb_id, season_num, api_key)
    if not fetched:
        return None

    cache[key] = {"episodes": fetched, "complete": not is_current_airing_season, "fetched_at": datetime.now(timezone.utc).isoformat()}
    tmp = season_cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    os.replace(tmp, season_cache_path)

    ep = fetched.get(str(episode_num))
    return ep["name"] if (ep and ep.get("name")) else None


_REL_SE_RE = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,3}")
_REL_TAG_RE = re.compile(
    # 2160/1080/720/480 with OR without a trailing 'p' - a bare "1080"
    # (malformed "1080p", real case Doctor Who S06E00 ...Wardrobe.1080.mkv)
    # must be treated as a quality tag, not part of the episode title.
    r"[._\s-](?:(?:2160|1080|720|480)p?|4k|uhd|WEBRip|WEB-DL|WEB|BluRay|BDRip|BRRip|HDTV|"
    r"DVDRip|DVD|DISC|REPACK|PROPER|iNTERNAL|INTERNAL|x264|x265|H\.?26[45]|HEVC|"
    r"AAC\d?|AC3|DTS|DDP?\d?|EAC3|RARBG|RAWR)\b",
    re.IGNORECASE,
)


def release_episode_title(*names):
    """Best-effort episode title pulled from a scene release name - the text
    between the (rightmost) SxxExx token and the first quality/source/codec
    tag. Only used as a match key for resolve_between_seasons_special()
    (which does its own exact, punctuation-insensitive compare against
    TMDB's Season 0 names and tolerates trailing junk), never trusted as a
    title to file on. None if nothing parses."""
    for name in names:
        if not name:
            continue
        base, ext = os.path.splitext(name)
        stem = base if ext.lower() in VIDEO_EXTENSIONS else name  # folder names have no real ext
        hits = list(_REL_SE_RE.finditer(stem))
        if not hits:
            continue
        # rightmost SxxExx - the scene name proper, past any "S06E00 · "
        # prefix rename_queue_inplace.py may have prepended.
        tail = stem[hits[-1].end():].lstrip("._ ·-")
        cut = _REL_TAG_RE.search(tail)
        if cut:
            tail = tail[: cut.start()]
        tail = tail.replace(".", " ").replace("_", " ").strip()
        if tail:
            return tail
    return None


def resolve_between_seasons_special(tmdb_id, release_title, data_dir):
    """A between-seasons special tagged SxxE00 by scene convention has no
    SxxE00 slot on TMDB - modern-era shows file every special under Season
    0 (real case: Doctor Who S06E00 "The Doctor, the Widow and the
    Wardrobe" is TMDB 57243 season 0 episode 47). Given the release's own
    title, find that Season 0 episode by an exact (case/punctuation-
    insensitive) name match - never a fuzzy guess. Returns
    (0, s0_episode_num, canonical_name) so the caller can file it into
    Specials\\ with correct S00Enn numbering, or None -> flagged for
    review, same never-guess bar as validate_tv_episode().

    The release title is expected to be imperfect - an upstream rename can
    leave a resolution/codec/source token stuck on the end (real case: a
    scene name ending `.1080` - a malformed `1080p`). So the match is:
    exact after normalization, OR the TMDB name is a prefix of the release
    title and the only leftover words are known junk tokens. Still never a
    fuzzy guess - every leftover word must be recognised junk."""
    if not release_title:
        return None
    api_key = _tmdb_api_key(data_dir)
    if not api_key:
        return None
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()
    want = norm(release_title)
    if not want:
        return None
    want_words = want.split()
    for num, ep in _fetch_season_episodes_live(tmdb_id, 0, api_key).items():
        name = ep.get("name") or ""
        cand = norm(name)
        if not cand:
            continue
        cand_words = cand.split()
        if cand_words != want_words:
            if want_words[:len(cand_words)] != cand_words:
                continue
            leftover = want_words[len(cand_words):]
            if not leftover or any(w not in _JUNK_TITLE_TOKENS for w in leftover):
                continue
        try:
            return 0, int(num), name
        except (TypeError, ValueError):
            return None
    return None


# Resolution/codec/source tokens (lowercased, punctuation already stripped)
# that can cling to the end of an imperfectly-renamed release name. Used
# only to let resolve_between_seasons_special() tolerate trailing junk -
# never to parse a title *into* anything.
_JUNK_TITLE_TOKENS = {
    "2160p", "2160", "1080p", "1080", "720p", "720", "480p", "480", "4k", "uhd",
    "x264", "x265", "h264", "h265", "hevc", "avc", "xvid", "divx",
    "web", "webdl", "webrip", "bluray", "bdrip", "brrip", "hdtv", "dvdrip", "dvd",
    "dl", "rip", "hdr", "sdr", "10bit", "8bit", "proper", "repack", "internal",
    "aac", "ac3", "dts", "ddp", "dd", "5", "1", "eac3", "flac", "remux",
}


# ---------- §9 sidecar subtitle files ----------

def find_matching_sidecars(video_path):
    """Subtitle files in the same folder as video_path sharing its exact
    filename stem. Everything else (.nfo, .jpg, samples, etc.) is
    deliberately left out - never touched, per §9."""
    video_dir = os.path.dirname(video_path)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    sidecars = []
    try:
        for fn in os.listdir(video_dir):
            s, ext = os.path.splitext(fn)
            if s == stem and ext.lower() in SIDECAR_SUBTITLE_EXTS:
                sidecars.append(os.path.join(video_dir, fn))
    except OSError:
        pass
    return sidecars


# ---------- §8 same-pass duplicate/collision resolution ----------

def rank_by_quality(original_filename, size_bytes):
    res_rank, src_rank = quality_score(original_filename)
    return (res_rank, src_rank, size_bytes)


def pick_best_duplicate(candidates):
    """candidates: list of dicts with at least 'original_name' and
    'size_bytes'. Returns (winner, [losers]), highest quality_score()
    (resolution, then source, then size as tiebreaker) wins - losers are
    flagged as superseded, never deleted, never touched."""
    ranked = sorted(candidates, key=lambda c: rank_by_quality(c["original_name"], c.get("size_bytes", 0)), reverse=True)
    return ranked[0], ranked[1:]

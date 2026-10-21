"""Config: non-secret tunables merged from settings.json over DEFAULT_SETTINGS,
plus the data-file paths. No secrets — the whole app runs on public RSS + page
scraping.

Paths self-locate from this file, so the package works wherever
extras/youtube/ lives (the host app imports it via sys.path.insert; it
never assumes a working directory).
"""
import json
import os
import shutil
import threading
from pathlib import Path

from .atomic_write import atomic_replace

# config.py lives in extras/youtube/youtube/ — project root is its parent.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# The real channel list + watched history live under DATA_DIR and have NO other
# backup, so never point a throwaway harness at the default. Set
# YOUTUBE_DATA_DIR to redirect (devserve does this) rather than deleting files.
DATA_DIR = Path(os.environ.get("YOUTUBE_DATA_DIR", str(PROJECT_ROOT / "data")))
SETTINGS_PATH = PROJECT_ROOT / "settings.json"
LOG_DIR = PROJECT_ROOT / "logs"

DEFAULT_SETTINGS = {
    # How often the poll thread checks every channel for new uploads.
    "poll_interval_sec": 3600,
    # A video older than this (by publish date) never appears in the New view,
    # even if still unwatched — "you missed it", no guilt pile.
    "new_window_days": 14,
    # video_cache.json keeps each channel's videos this far back — a little
    # wider than new_window_days so a late watch still resolves cleanly.
    "catalog_retention_days": 21,
    # Duration at/under which a video is a Shorts candidate (confirmed with a
    # /shorts/ URL probe before it's actually dropped).
    "shorts_max_sec": 60,
    # Consecutive failed polls before a channel is surfaced as failing.
    "failure_alert_threshold": 3,
    # RSS feed page size on each poll (also the add-time baseline size).
    "playlist_page_size": 15,
    # Optional explicit browser executable for opening watch pages from the
    # machine the host app runs on. "" = the OS default-browser association (normally
    # the user's real Chrome). Set to e.g.
    # "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" to force one.
    "open_browser_command": "",
    # After opening a watch page, pull the browser window to the foreground so
    # the user doesn't have to alt-tab (Windows only; best-effort).
    "raise_browser_on_open": True,
}

# Data files (this package is the ONLY writer of everything under DATA_DIR).
CHANNELS_PATH = DATA_DIR / "channels.json"
VIDEO_CACHE_PATH = DATA_DIR / "video_cache.json"
SEEN_VIDEOS_PATH = DATA_DIR / "seen_videos.json"
STATUS_PATH = DATA_DIR / "youtube_status.json"  # only written if a VORTEX tile is ever added

_settings_lock = threading.Lock()


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def snapshot_backup(path):
    """Keep a rolling `<name>.bak` / `.bak2` of a precious file right before it
    is overwritten. Cheap insurance for channels.json + seen_videos.json, which
    have no other backup (data/ is git-ignored). Best-effort — never raises."""
    try:
        path = Path(path)
        if not path.exists():
            return
        b1 = path.with_suffix(path.suffix + ".bak")
        b2 = path.with_suffix(path.suffix + ".bak2")
        if b1.exists():
            shutil.copy2(b1, b2)
        shutil.copy2(path, b1)
    except OSError:
        pass


def load_settings():
    """DEFAULT_SETTINGS with settings.json shallow-merged on top. Missing or
    unreadable file just means defaults."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        on_disk = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(on_disk, dict):
            settings.update(on_disk)
    except (OSError, json.JSONDecodeError):
        pass
    return settings


def save_settings(settings):
    with _settings_lock:
        ensure_dirs()
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        atomic_replace(str(tmp), str(SETTINGS_PATH))

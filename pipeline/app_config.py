"""
The one place paths, folders and tool locations are decided.

Every script that used to carry its own hard-coded folder (the library, the
incoming folder, the data folder, VLC, ffprobe...) reads it from here instead.

Order of precedence, highest first:
  1. environment variables (MEDIABOX_*), handy for one-off runs and CI
  2. config.json in the project root (the setup wizard writes this file)
  3. built-in defaults: nothing personal, nothing machine-specific

Nothing in this module needs a third-party package, and importing it has no
side effects beyond reading two small files, so every script can import it.
"""
import json
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.environ.get("MEDIABOX_CONFIG") or os.path.join(PROJECT_ROOT, "config.json")

IS_WINDOWS = os.name == "nt"

_DEFAULTS = {
    # The organised library: <archive_root>/<Category>/<Show>/Season NN/...
    "archive_root": "",
    # Folders new files land in before they are filed (a downloads folder, a
    # "to sort" folder). Each is {"label": "...", "path": "..."}.
    "queue_roots": [],
    # Optional tools. Empty means "look for it on this computer".
    "vlc_path": "",
    "ffprobe_path": "",
    "ffmpeg_path": "",
    "browser_path": "",
    # Optional add-ons. Empty means the feature is simply off.
    "youtube_dir": "",
    "companion_data_dir": "",
    "html_snapshot_path": "",
    # Folder holding your own name, art and flavour text (see branding.py).
    # Empty means ./personal if it exists.
    "pack_dir": "",
    # Your Amazon storefront for generated Prime Video search links, e.g.
    # "amazon.de". Empty means "work it out from streaming_region".
    "amazon_domain": "",
    # Two-letter region for the streaming features (Prime Video links).
    "streaming_region": "US",
    # Show the built-in sample library instead of real folders.
    "demo": False,
}

QUEUE_LABEL_DEFAULT = "Incoming"


def _load_file():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_file = _load_file()


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _get(key):
    env = os.environ.get("MEDIABOX_" + key.upper())
    if env is not None and env != "":
        return env
    return _file.get(key, _DEFAULTS[key])


DEMO = _truthy(_get("demo"))

# The data folder holds every generated and personal file (library database,
# watch state, caches, images). Demo mode gets its own so it can never mix
# with real data.
DATA_DIR = (
    os.environ.get("MEDIABOX_DATA_DIR")
    or os.path.join(PROJECT_ROOT, "demo_data" if DEMO else "data")
)

try:  # every script writes here; a fresh clone has no data folder yet
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    pass

ARCHIVE_ROOT = _get("archive_root")


def _queue_roots():
    env = os.environ.get("MEDIABOX_QUEUE_ROOT")
    if env:
        return [{"label": QUEUE_LABEL_DEFAULT, "path": env}]
    roots = _file.get("queue_roots") or []
    out = []
    for r in roots:
        if isinstance(r, str):
            r = {"label": QUEUE_LABEL_DEFAULT, "path": r}
        if isinstance(r, dict) and r.get("path"):
            out.append({"label": r.get("label") or QUEUE_LABEL_DEFAULT, "path": r["path"]})
    return out


QUEUE_ROOTS = _queue_roots()
# The code was written around a single incoming folder; the first one is it.
QUEUE_LABEL = QUEUE_ROOTS[0]["label"] if QUEUE_ROOTS else QUEUE_LABEL_DEFAULT
QUEUE_PATH = QUEUE_ROOTS[0]["path"] if QUEUE_ROOTS else ""


def pack_dir_setting():
    """The configured personal-pack folder, '' when none was set."""
    return _get("pack_dir")


def find_tool(config_key, names, extra_locations=()):
    """Configured path, else PATH, else the usual install locations, else ''."""
    configured = _get(config_key)
    if configured and os.path.isfile(configured):
        return configured
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for loc in extra_locations:
        if os.path.isfile(loc):
            return loc
    return ""


def vlc_path():
    return find_tool("vlc_path", ["vlc", "vlc.exe"], [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        "/Applications/VLC.app/Contents/MacOS/VLC",
    ])


def ffprobe_path():
    return find_tool("ffprobe_path", ["ffprobe", "ffprobe.exe"])


def ffmpeg_path():
    return find_tool("ffmpeg_path", ["ffmpeg", "ffmpeg.exe"])


def browser_path():
    return find_tool("browser_path", ["google-chrome", "chromium", "chrome", "chrome.exe"], [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ])


_AMAZON_STOREFRONTS = {
    "US": "amazon.com", "CA": "amazon.ca", "MX": "amazon.com.mx", "BR": "amazon.com.br",
    "GB": "amazon.co.uk", "UK": "amazon.co.uk", "DE": "amazon.de", "FR": "amazon.fr",
    "ES": "amazon.es", "IT": "amazon.it", "NL": "amazon.nl", "SE": "amazon.se",
    "PL": "amazon.pl", "AU": "amazon.com.au", "JP": "amazon.co.jp", "IN": "amazon.in",
}
STREAMING_REGION = str(_get("streaming_region") or "US").strip().upper()
AMAZON_DOMAIN = str(_get("amazon_domain") or _AMAZON_STOREFRONTS.get(STREAMING_REGION, "amazon.com")).strip()

def _project_path(value):
    """A configured folder, with a relative path read from the project root."""
    value = str(value or "").strip()
    if value and not os.path.isabs(value):
        value = os.path.join(PROJECT_ROOT, value)
    return value


# Folder holding the optional YouTube block (extras/youtube). Empty means off.
YOUTUBE_DIR = _project_path(_get("youtube_dir"))
# Data folder of an optional companion tool that shares files with this app.
# Empty means no companion: every read of its files just finds nothing.
COMPANION_DATA_DIR = _get("companion_data_dir")


def companion_file(name):
    return os.path.join(COMPANION_DATA_DIR, name) if COMPANION_DATA_DIR else ""


HTML_SNAPSHOT_PATH = _get("html_snapshot_path") or os.path.join(DATA_DIR, "library_snapshot.html")


# TMDB's API and image hosts. The environment can point them elsewhere, which is how the
# tests run the whole pipeline against a small fake TMDB server instead of the real one.
TMDB_API_BASE = (os.environ.get("MEDIABOX_TMDB_API_BASE") or "https://api.themoviedb.org/3").rstrip("/")
TMDB_IMAGE_BASE = (os.environ.get("MEDIABOX_TMDB_IMAGE_BASE") or "https://image.tmdb.org/t/p").rstrip("/")

TMDB_KEY_HELP = (
    "No TMDB API key found. Run the setup page (python run.py), or set "
    "MEDIABOX_TMDB_API_KEY, or put the key in data/tmdb_api_key.txt."
)
TMDB_KEY_PATH = os.path.join(DATA_DIR, "tmdb_api_key.txt")


def tmdb_api_key():
    """Environment (MEDIABOX_TMDB_API_KEY, or the common TMDB_API_KEY), then
    data/tmdb_api_key.txt. Empty string when there is none."""
    key = (os.environ.get("MEDIABOX_TMDB_API_KEY") or os.environ.get("TMDB_API_KEY") or "").strip()
    if key:
        return key
    try:
        with open(TMDB_KEY_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_tmdb_key(key):
    """Write the key to data/tmdb_api_key.txt (never into config.json, which
    people share). Owner-only permissions where the OS supports it."""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = TMDB_KEY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(key.strip() + "\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, TMDB_KEY_PATH)


# True when there is a library to show: the demo, or a configured archive.
# Anything else means "first run": the app serves the setup page.
CONFIGURED = DEMO or bool(ARCHIVE_ROOT)


def save(updates):
    """Merge `updates` into config.json (used by the setup wizard)."""
    data = _load_file()
    data.update(updates)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


if __name__ == "__main__":
    print("project root :", PROJECT_ROOT)
    print("config file  :", CONFIG_PATH, "(found)" if _file else "(not found, using defaults)")
    print("demo mode    :", DEMO)
    print("data dir     :", DATA_DIR)
    print("archive root :", ARCHIVE_ROOT or "(not set)")
    print("queue roots  :", QUEUE_ROOTS or "(none)")
    print("vlc          :", vlc_path() or "(not found)")
    print("ffprobe      :", ffprobe_path() or "(not found)")
    print("tmdb key     :", "set" if tmdb_api_key() else "not set")
    print("python       :", sys.executable)

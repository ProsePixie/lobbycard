"""Shared local-image-cache helper for every TMDB-sourced image (posters,
backdrops, cast photos, recommendation thumbnails) - the same "download
once, store locally, never re-fetched" pattern enrich_wikipedia.py already
established for its own wiki_images/ fallback (see that file's module
docstring), extended here to TMDB's images so the whole app can render
fully offline once cached, not just its text data (titles/overviews/cast
names, already local in media_db.json/physical_db.json - only images ever
required a live connection). Used by enrich_tmdb.py (ongoing, only for
genuinely new/re-matched titles - stays cheap), backfill_tmdb_images.py
(one-time, the whole existing library), and web_server.py's /tmdb-image/
route (self-heals anything that slips through either of those)."""
import io
import os
import secrets
import threading
import time
import urllib.request
import urllib.error

# PIL is deliberately NOT imported at module level - this module is
# imported by web_server.py at startup (needed for ensure_cached()/
# cache_show_images(), used on every page load's /tmdb-image/ route), but
# Pillow itself (8 separate compiled .pyd binaries) is only ever needed by
# save_custom_image()/_cover_resize() below, for the one Admin-panel
# "upload your own poster" action. Importing it eagerly meant every single
# server startup paid Pillow's import cost - including its binaries being
# freshly on-access-scanned by antivirus on a cold boot - for a feature
# used maybe once in a while. Real, recurring symptom this is part of
# fixing: launcher timeouts with zero diagnostic trace, 2026-08-21. Now
# imported lazily, right where it's actually used, so a server that's
# never had anyone touch that one feature never pays for it at all.

from app_config import DATA_DIR  # noqa: E402
IMAGES_ROOT = os.path.join(DATA_DIR, "tmdb_images")

# Same size TMDB is already fetched at for each use in render_engine.py
# today (grep image.tmdb.org there) - caching locally shouldn't change
# quality, only whether a live request is needed to see it.
SIZE_BY_KIND = {
    "poster": "w500",
    "backdrop": "w1280",
    "cast": "w185",
    "rec_poster": "w185",
}
from app_config import TMDB_IMAGE_BASE as CDN_BASE  # noqa: E402
USER_AGENT = "Media-Library-Scripts/1.0 (local offline image cache)"

# Simple in-process circuit breaker - a genuinely dead network (as opposed
# to a normal HTTP response like 404/429) shouldn't mean every missing
# image on a page load retries and times out one at a time (a real page
# has dozens of images; at 15s timeout each that's minutes of hanging).
# After a few consecutive connection failures, stop attempting new fetches
# for a short cooldown and fail immediately instead - the whole point of
# the "Feature Offline" fallback (see render_engine.py's onTmdbImageError)
# is failing fast enough for it to actually feel fast. Resets the moment
# a real download succeeds, so recovery is automatic, no restart needed.
# Cooldown shortened from 20s to 10s (2026-08-22), kept in sync with
# render_engine.py's own IMG_RETRY_DELAYS_MS - that function now retries a
# failed image a few times with increasing delay rather than giving up
# permanently on the first miss, but only if this cooldown is short enough
# for a real retry schedule to actually outlast it. 20s left this bar too
# high for what's normally just a brief blip, not a genuinely dead network.
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_COOLDOWN_SECONDS = 10
_consecutive_network_failures = 0
_circuit_open_until = 0.0


def local_dir(kind):
    d = os.path.join(IMAGES_ROOT, kind)
    os.makedirs(d, exist_ok=True)
    return d


def local_path(kind, tmdb_relative_path):
    """tmdb_relative_path looks like '/l67ir3kU53FYd6Vjwhqrkih1H1P.jpg' -
    TMDB's own assigned filename is already a unique, filesystem-safe id,
    no name-mangling needed (unlike enrich_wikipedia.py's
    safe_image_filename(), which has to invent one since Wikipedia
    doesn't hand you a clean unique id)."""
    filename = tmdb_relative_path.lstrip("/")
    return os.path.join(local_dir(kind), filename)


def is_cached(kind, tmdb_relative_path):
    if not tmdb_relative_path:
        return False
    return os.path.exists(local_path(kind, tmdb_relative_path))


def ensure_cached(kind, tmdb_relative_path, sleep_after=0.05):
    """Downloads and saves the image if not already local. Returns True if
    the file is now present locally (whether just downloaded or already
    there), False if the download genuinely failed (network error, 404,
    etc - never raises, matches this project's existing api_get() retry-
    and-give-up style rather than crashing a whole pipeline run over one
    missing image). Retries only apply to HTTP 429 (TMDB rate-limiting -
    retrying is genuinely correct there); a real connection failure
    (URLError/TimeoutError - the network itself, not a TMDB response)
    fails after a single attempt and trips the circuit breaker above,
    since retrying a dead network 3x per image just makes "offline" take
    3x longer to actually fail."""
    global _consecutive_network_failures, _circuit_open_until
    if not tmdb_relative_path or kind not in SIZE_BY_KIND:
        return False
    dest = local_path(kind, tmdb_relative_path)
    if os.path.exists(dest):
        return True
    if time.time() < _circuit_open_until:
        return False
    url = f"{CDN_BASE}/{SIZE_BY_KIND[kind]}{tmdb_relative_path}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # Unique per call, not a fixed dest + ".tmp" - now that web_server.py's
    # /tmdb-image/ route runs threaded (2026-08-22), two concurrent
    # requests for the same not-yet-cached image could both reach this
    # point at once; a shared tmp path would let one thread's write
    # corrupt the other's. os.replace() at the end is already atomic
    # regardless of which thread's download wins the race - both fetch
    # the identical URL, so there's nothing to reconcile, just no shared
    # file to collide on while getting there.
    tmp = dest + f".tmp.{threading.get_ident()}.{secrets.token_hex(4)}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read()
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            _consecutive_network_failures = 0
            _circuit_open_until = 0.0
            if sleep_after:
                time.sleep(sleep_after)
            return True
        except urllib.error.HTTPError as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            if e.code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            return False  # 404/etc - a real response, genuinely not there, no point retrying
        except (urllib.error.URLError, TimeoutError, OSError):
            if os.path.exists(tmp):
                os.remove(tmp)
            _consecutive_network_failures += 1
            if _consecutive_network_failures >= _CIRCUIT_FAILURE_THRESHOLD:
                _circuit_open_until = time.time() + _CIRCUIT_COOLDOWN_SECONDS
            return False  # fail fast - see the circuit breaker comment above
    return False


# Same w500/w1280 target sizes SIZE_BY_KIND already fetches TMDB at for
# poster/backdrop - a user-uploaded image should look identical in the grid
# next to real TMDB ones, not stand out as a different resolution/aspect.
POSTER_TARGET = (500, 750)
BACKDROP_TARGET = (1280, 720)
# Only worth cropping a backdrop out of the same upload when it's genuinely
# wide/large enough that a 16:9 center-crop is a reasonable guess at what
# the user meant as a banner - a portrait poster scan forced into 16:9 would
# just be a bad crop of someone's face, so that case skips the backdrop
# entirely rather than producing something ugly by default.
BACKDROP_MIN_WIDTH = 1280
BACKDROP_MIN_ASPECT = 1.3
CUSTOM_TOKEN_PREFIX = "custom"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _cover_resize(img, target_w, target_h):
    """Resize + center-crop to exactly (target_w, target_h) - the same
    visual contract as CSS `object-fit: cover`: fills the box completely,
    crops whatever doesn't fit, never stretches/distorts."""
    from PIL import Image  # see the module-level comment on why this is lazy
    src_w, src_h = img.size
    target_ratio = target_w / target_h
    if (src_w / src_h) > target_ratio:
        new_w = round(src_h * target_ratio)
        x0 = (src_w - new_w) // 2
        box = (x0, 0, x0 + new_w, src_h)
    else:
        new_h = round(src_w / target_ratio)
        y0 = (src_h - new_h) // 2
        box = (0, y0, src_w, y0 + new_h)
    return img.crop(box).resize((target_w, target_h), Image.LANCZOS)


def save_custom_image(image_bytes):
    """Saves a user-uploaded image (Admin panel "Fix a title" -> upload a
    local image) as this show's poster - always - and backdrop, only when
    the source is wide/large enough (see BACKDROP_MIN_WIDTH/_ASPECT above)
    that a 16:9 crop is a reasonable banner, not a bad guess. Saved under
    the SAME local_dir()/local_path() layout real TMDB images use, with a
    'custom' + random-hex filename (matches TMDB_IMAGE_FILENAME_RE in
    web_server.py, which only allows alphanumeric + jpg/jpeg/png - no
    separators) so /tmdb-image/<kind>/<filename> serves it identically to
    a real cached TMDB image, no route changes needed. Returns
    (poster_relative_path, backdrop_relative_path_or_None) in the same
    '/<filename>' shape a TMDB path uses, so the caller can drop it
    straight into poster_override/backdrop_override - same mechanism as
    picking one of TMDB's own alternate posters, just a different source.
    Raises ValueError (with a message safe to show the user) if the bytes
    aren't a real, decodable image."""
    from PIL import Image, UnidentifiedImageError  # see the module-level comment on why this is lazy
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()  # force full decode now - a truncated/corrupt upload
        # must fail here, inside the try, not later on first real use
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("That doesn't look like a readable image file.") from e
    img = img.convert("RGB")
    src_w, src_h = img.size

    filename = f"{CUSTOM_TOKEN_PREFIX}{secrets.token_hex(8)}.jpg"
    rel_path = f"/{filename}"

    poster_img = _cover_resize(img, *POSTER_TARGET)
    poster_img.save(local_path("poster", rel_path), "JPEG", quality=90)

    backdrop_rel = None
    if src_w >= BACKDROP_MIN_WIDTH and (src_w / src_h) >= BACKDROP_MIN_ASPECT:
        backdrop_img = _cover_resize(img, *BACKDROP_TARGET)
        backdrop_img.save(local_path("backdrop", rel_path), "JPEG", quality=90)
        backdrop_rel = rel_path

    return rel_path, backdrop_rel


def cache_show_images(show):
    """Caches images for content actually in the library - poster, backdrop,
    each cast member's profile photo of a show/movie the user owns.
    Deliberately does NOT cache tmdb_recommendations posters - those are
    TMDB's "people who liked this also liked" suggestions for titles the
    user does NOT own (caught live 2026-08-20: caching those for every
    matched show meant downloading thousands of images for things not in
    the library at all, defeating the entire point of an "offline copy of
    my collection" cache - recommendation posters stay live-fetched from
    image.tmdb.org on demand, same as before this cache existed, at the
    low volume a person actually browsing "More like this" produces).
    Safe to call on any show dict (missing fields just mean nothing to
    cache for that slot). Returns (attempted, downloaded) counts for
    progress reporting."""
    attempted = downloaded = 0
    for kind, path in (("poster", show.get("tmdb_poster")), ("backdrop", show.get("tmdb_backdrop"))):
        if not path:
            continue
        attempted += 1
        was_cached = is_cached(kind, path)
        if ensure_cached(kind, path) and not was_cached:
            downloaded += 1
    for c in (show.get("tmdb_cast") or []):
        path = c.get("profile_path")
        if not path:
            continue
        attempted += 1
        was_cached = is_cached("cast", path)
        if ensure_cached("cast", path) and not was_cached:
            downloaded += 1
    return attempted, downloaded

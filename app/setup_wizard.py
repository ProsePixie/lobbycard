"""
Settings, in the browser: first-run setup and everything you may want to change later.

Served at /setup (shown automatically while no library is configured, and linked
from the Admin menu afterwards). It covers:

  - appearance: the title, subtitle, header image, accent colour and the two
    watch-list names (saved at once, no restart; see pipeline/branding.py)

and, for the library itself, four steps:

  1. pick the library folder (where your organised shows and movies live)
  2. optionally pick one or more incoming folders (downloads waiting to be filed)
  3. paste a TMDB API key and test it live
  4. look inside the chosen folders without touching them (a dry run), then save

Saving writes config.json through app_config.save() and the key to
data/tmdb_api_key.txt (never into config.json), then restarts the server so
every module re-reads its settings. The key is never echoed back or logged.

Everything here is local-only: each route refuses requests that do not come
from the same machine, and the write routes also need the per-run auth token
the page embeds, like every other mutating route in the app.
"""
import ipaddress
import json
import mimetypes
import os
import re
import string
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import abort, jsonify, request, Response, send_file

import app_config
import branding

DRY_RUN_FILE_LIMIT = 40000
DRY_RUN_SECONDS = 15
SAMPLE_SIZE = 12
RESTART_EXIT_CODE = 75  # run.py relaunches the server when it sees this

_TMDB_V3_KEY = re.compile(r"^[0-9a-fA-F]{32}$")


def _is_local_request():
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------- helpers ----

def list_directory(path):
    """Subfolders of `path` for the folder picker. An empty path lists the
    drive letters on Windows and the home folder + root elsewhere."""
    if not path:
        if app_config.IS_WINDOWS:
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]
            return {"path": "", "parent": None, "entries": [{"name": d, "path": d} for d in drives]}
        home = os.path.expanduser("~")
        entries = [{"name": "Home (" + home + ")", "path": home}, {"name": "/", "path": "/"}]
        if os.path.isdir("/Volumes"):
            entries.append({"name": "/Volumes", "path": "/Volumes"})
        if os.path.isdir("/mnt"):
            entries.append({"name": "/mnt", "path": "/mnt"})
        return {"path": "", "parent": None, "entries": entries}
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        return {"path": path, "parent": os.path.dirname(path), "entries": [], "error": "Not a folder"}
    parent = os.path.dirname(path)
    entries = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                        entries.append({"name": e.name, "path": e.path})
                except OSError:
                    continue
    except OSError as ex:
        return {"path": path, "parent": parent, "entries": [], "error": str(ex)}
    entries.sort(key=lambda x: x["name"].lower())
    return {"path": path, "parent": None if parent == path else parent, "entries": entries[:500]}


def test_tmdb_key(key):
    """(ok, message). Calls TMDB's /configuration, the cheapest authenticated
    endpoint. The key is only ever sent to TMDB itself."""
    key = (key or "").strip()
    if not key:
        return False, "Paste your TMDB API key first."
    if key.startswith("eyJ") or len(key) > 40:
        return False, "That looks like the long v4 access token. This app needs the shorter v3 \"API Key\" (32 characters) from the same TMDB settings page."
    if not _TMDB_V3_KEY.match(key):
        return False, "A TMDB v3 API key is 32 letters and digits. Check for stray spaces or a missing character."
    url = app_config.TMDB_API_BASE + "/configuration?" + urllib.parse.urlencode({"api_key": key})
    req = urllib.request.Request(url, headers={"User-Agent": "mediabox-setup"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            json.load(r)
        return True, "Key accepted by TMDB."
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "TMDB rejected that key (401). Check it was copied completely."
        return False, f"TMDB answered with an error ({e.code}). Try again in a moment."
    except (urllib.error.URLError, OSError, ValueError):
        return False, "Could not reach TMDB. Check your internet connection, then test again."


def dry_run(paths):
    """Read-only look at folders: how many videos, what they would be
    recognised as. Writes nothing and calls no network service."""
    sys.path.insert(0, os.path.join(app_config.PROJECT_ROOT, "pipeline"))
    import scan_media  # lazy: it is a heavy module, and only needed here
    exts = scan_media.VIDEO_EXTENSIONS
    report = {"folders": [], "warnings": []}
    for path in paths:
        info = {"path": path, "exists": os.path.isdir(path), "videos": 0, "bytes": 0, "shows": 0,
                "movies": 0, "unrecognised": 0, "top_level": [], "samples": [], "truncated": False}
        report["folders"].append(info)
        if not info["exists"]:
            report["warnings"].append(f"{path} is not a folder this computer can see.")
            continue
        started, found, seen_titles = time.time(), [], set()
        top = {}
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            rel = os.path.relpath(dirpath, path)
            first = rel.split(os.sep)[0] if rel != "." else ""
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in exts:
                    continue
                full = os.path.join(dirpath, fn)
                info["videos"] += 1
                try:
                    info["bytes"] += os.path.getsize(full)
                except OSError:
                    pass
                if first:
                    top[first] = top.get(first, 0) + 1
                parsed = scan_media.parse_folder_name(os.path.splitext(fn)[0])
                if parsed["type"] == "TV":
                    seen_titles.add(("TV", parsed["title"].lower()))
                elif parsed["type"] == "Movie":
                    info["movies"] += 1
                else:
                    info["unrecognised"] += 1
                found.append((fn, parsed))
            if info["videos"] >= DRY_RUN_FILE_LIMIT or time.time() - started > DRY_RUN_SECONDS:
                info["truncated"] = True
                break
        info["shows"] = len(seen_titles)
        info["top_level"] = [{"name": k, "videos": v} for k, v in sorted(top.items(), key=lambda kv: -kv[1])[:10]]
        step = max(1, len(found) // SAMPLE_SIZE)
        for fn, p in found[::step][:SAMPLE_SIZE]:
            if p["type"] == "TV":
                what = f"TV: {p['title']} S{p['season']:02d}E{p['episode']:02d}"
            elif p["type"] == "Movie":
                what = f"Movie: {p['title']} ({p['year']})"
            else:
                what = "not recognised yet"
            info["samples"].append({"file": fn, "as": what})
        if not info["videos"]:
            report["warnings"].append(f"No video files found under {path}.")
        elif info["unrecognised"] > info["videos"] * 0.5:
            report["warnings"].append(
                f"Most files in {path} were not recognised as a TV episode or a movie with a year. "
                "The scanner can still file them, but names like 'Show - S01E02' or 'Movie (2019)' work best."
            )
    report["tools"] = {
        "vlc": bool(app_config.vlc_path()), "ffprobe": bool(app_config.ffprobe_path()),
    }
    return report


def _clean_path(p):
    p = (p or "").strip().strip('"')
    return os.path.abspath(os.path.expanduser(p)) if p else ""


# ----------------------------------------------------------------- routes ----

def register(app, check_auth, auth_token):
    """Attach the setup routes to the Flask app. `auth_token` is a callable
    (the token is created after this module is imported)."""

    def local_only():
        if not _is_local_request():
            abort(403)

    def guarded():
        local_only()
        check_auth()

    @app.route("/setup")
    def setup_page():
        local_only()
        values = {
            "token_json": _js(auth_token()),
            "title": _html(branding.app_name()),
            "demo_json": _js(app_config.DEMO),
            "configured_json": _js(app_config.CONFIGURED),
            "defaults_json": _js({"title": _pack_name(), "tagline": _pack_tagline(), "accent": _pack_accent()}),
            "accent_css": branding.accent_css(),
        }
        page = re.sub(r"@@([a-z_]+)@@", lambda m: str(values[m.group(1)]), _page_template())
        return Response(page, mimetype="text/html")

    @app.route("/setup/header.jpg")
    def setup_header_image():
        local_only()
        if not branding.has_header():
            abort(404)
        # read into memory: on Windows a file that is still open for sending cannot be replaced or removed
        with open(branding.HEADER_PATH, "rb") as f:
            resp = Response(f.read(), mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/setup/banner-default")
    def setup_default_banner():
        local_only()
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            path = branding.pack_file("banner" + ext)
            if path:
                return send_file(path, mimetype=mimetypes.guess_type(path)[0] or "image/jpeg")
        abort(404)

    @app.route("/api/setup/appearance", methods=["GET", "POST"])
    def setup_appearance():
        guarded()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            allowed = {k: data[k] for k in ("title", "tagline", "accent", "people", "header_focus") if k in data}
            try:
                branding.save_appearance(allowed)
            except (ValueError, TypeError) as ex:
                return jsonify({"ok": False, "message": str(ex)}), 400
        return jsonify({
            "ok": True, "saved": branding.appearance(), "has_header": branding.has_header(),
            "has_pack_banner": bool(branding.pack_file("banner.jpg") or branding.pack_file("banner.png")),
        })

    @app.route("/api/setup/header", methods=["POST"])
    def setup_header_upload():
        guarded()
        f = request.files.get("image")
        if not f:
            return jsonify({"ok": False, "message": "Choose an image first."}), 400
        data = f.read(branding.HEADER_MAX_BYTES + 1)
        try:
            branding.save_header(data)
        except ValueError as ex:
            return jsonify({"ok": False, "message": str(ex)}), 400
        return jsonify({"ok": True})

    @app.route("/api/setup/header/clear", methods=["POST"])
    def setup_header_clear():
        guarded()
        branding.clear_header()
        return jsonify({"ok": True})

    @app.route("/api/setup/status")
    def setup_status():
        guarded()
        return jsonify({
            "configured": app_config.CONFIGURED, "demo": app_config.DEMO,
            "archive_root": app_config.ARCHIVE_ROOT,
            "queue_roots": app_config.QUEUE_ROOTS,
            "has_tmdb_key": bool(app_config.tmdb_api_key()),
            "is_windows": app_config.IS_WINDOWS,
        })

    @app.route("/api/setup/browse")
    def setup_browse():
        guarded()
        return jsonify(list_directory(request.args.get("path", "")))

    @app.route("/api/setup/test-tmdb", methods=["POST"])
    def setup_test_tmdb():
        guarded()
        ok, message = test_tmdb_key((request.get_json(silent=True) or {}).get("key", ""))
        return jsonify({"ok": ok, "message": message})

    @app.route("/api/setup/dry-run", methods=["POST"])
    def setup_dry_run():
        guarded()
        data = request.get_json(silent=True) or {}
        paths = [_clean_path(data.get("archive_root"))] + [_clean_path(p) for p in data.get("queue_roots", [])]
        paths = [p for p in dict.fromkeys(paths) if p]
        if not paths:
            return jsonify({"ok": False, "message": "Choose a library folder first."}), 400
        return jsonify({"ok": True, **dry_run(paths)})

    @app.route("/api/setup/save", methods=["POST"])
    def setup_save():
        guarded()
        if app_config.DEMO:
            return jsonify({"ok": False, "message": "Demo mode does not save settings."}), 400
        data = request.get_json(silent=True) or {}
        archive = _clean_path(data.get("archive_root"))
        if not archive or not os.path.isdir(archive):
            return jsonify({"ok": False, "message": "The library folder does not exist."}), 400
        queues = []
        for q in data.get("queue_roots", []):
            p = _clean_path(q.get("path") if isinstance(q, dict) else q)
            if p and os.path.isdir(p):
                label = (q.get("label") if isinstance(q, dict) else "") or app_config.QUEUE_LABEL_DEFAULT
                queues.append({"label": str(label)[:40], "path": p})
        updates = {"archive_root": archive, "queue_roots": queues}
        key = (data.get("tmdb_key") or "").strip()
        if key:
            if not _TMDB_V3_KEY.match(key):
                return jsonify({"ok": False, "message": "That TMDB key does not look right. Test it first."}), 400
            app_config.save_tmdb_key(key)
        app_config.save(updates)
        return jsonify({"ok": True, "message": "Saved. Restarting..."})

    @app.route("/api/setup/restart", methods=["POST"])
    def setup_restart():
        guarded()
        threading.Timer(0.8, lambda: os._exit(RESTART_EXIT_CODE)).start()
        return jsonify({"ok": True})

    @app.route("/api/ping")
    def ping():
        return jsonify({"ok": True})


def _js(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _pack_name():
    return str(branding._S.get("app_name") or branding.DEFAULT_NAME)


def _pack_tagline():
    return str(branding._S.get("tagline") if "tagline" in branding._S else branding.DEFAULT_TAGLINE)


def _pack_accent():
    a = branding._S.get("accent")
    if isinstance(a, str):
        return a
    if isinstance(a, dict):
        return a.get("light") or a.get("dark") or branding.DEFAULT_ACCENT[0]
    return branding.DEFAULT_ACCENT[0]


_PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "settings.html")


def _page_template():
    with open(_PAGE_PATH, encoding="utf-8") as f:
        return f.read()

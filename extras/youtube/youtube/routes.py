"""make_youtube_bp(deps) -> flask.Blueprint — contract symbol #1.

The host app: `app.register_blueprint(make_youtube_bp(deps), url_prefix="/youtube")`
where deps = {"check_auth": callable, "auth_token": str, "log": callable}.

The factory takes its dependencies by injection so this package imports NOTHING
from the host app's web_server (no import cycle). `check_auth()` is the host app's own
gate — it aborts the request on a bad/missing X-Auth-Token. Read endpoints are
ungated (they only expose public YouTube metadata + local watched flags), which
matches the host app's own "gate mutations only" convention.

No handler is allowed to surface a 500 with a traceback: every one returns a
JSON {ok: false, error: "..."} on failure instead.
"""
import functools
import os
import re
import subprocess
import threading
import time
import webbrowser

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import HTTPException

from . import api, catalog, config, fetch, state, status

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "iexplore.exe", "arc.exe",
}


def _request_is_local():
    ra = (request.remote_addr or "").lower()
    return ra in ("127.0.0.1", "::1", "localhost") or ra.startswith("127.")


def _bring_browser_to_front(timeout=3.0):
    """Best-effort: after launching a watch URL, raise the browser's window so
    the user doesn't have to alt-tab. `os.startfile` opens the tab but Windows'
    foreground-lock keeps the window behind the host app. Windows-only; silently
    no-ops elsewhere or on any failure. Meant to run in a daemon thread."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        SW_RESTORE, VK_MENU, KEYEVENTF_KEYUP = 9, 0x12, 0x0002

        def _exe_of(pid):
            h = kernel32.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION | VM_READ
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(260)
                if psapi.GetModuleFileNameExW(h, None, buf, 260):
                    return buf.value.rsplit("\\", 1)[-1].lower()
            finally:
                kernel32.CloseHandle(h)
            return ""

        def _title(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value

        found = []  # (hwnd, title), in Z-order (topmost first)

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _exe_of(pid.value) in _BROWSER_EXES:
                found.append((hwnd, _title(hwnd)))
            return True

        # Poll until a browser window whose title mentions YouTube shows up (the
        # tab we just opened, once it loads: "<video> - YouTube - <browser>"),
        # or the timeout — then fall back to the top-most browser window, which
        # is where a new tab lands.
        deadline = time.time() + timeout
        hwnd = None
        while time.time() < deadline:
            found.clear()
            user32.EnumWindows(_enum, 0)  # top of Z-order first
            yt = next((h for h, t in found if "youtube" in t.lower()), None)
            if yt:
                hwnd = yt
                break
            time.sleep(0.25)
        if hwnd is None:
            if not found:
                return
            hwnd = found[0][0]
        user32.ShowWindow(hwnd, SW_RESTORE)
        # ALT press/release unlocks SetForegroundWindow for a cross-process swap
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    except Exception:
        pass


def _open_url_fallback(url):
    if hasattr(os, "startfile"):
        os.startfile(url)  # noqa: S606 - Windows: hands a validated URL to the shell/default browser
    else:
        webbrowser.open(url)


# Same hardcoded-path + fallback pattern the host app's own app/web_server.py uses
# for its Netflix/Amazon links (automation/MediaLibraryLauncher.cs's own
# install-location check, extended there first).
_CHROME_PATH_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
)


def _find_chrome_exe():
    for path in _CHROME_PATH_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return None


def _visible_chrome_windows():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    hwnds = []

    def _cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if cls_buf.value == "Chrome_WidgetWin_1":
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return set(hwnds)


def _focus_window(hwnd, maximize=False):
    import ctypes
    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(target_thread, fg_thread, True)
    try:
        user32.ShowWindow(hwnd, 3 if maximize else 9)  # SW_MAXIMIZE : SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        user32.AttachThreadInput(target_thread, fg_thread, False)


def _launch_chrome_app_window(url, raise_on_open=True):
    """Opens url in a chromeless Chrome "app mode" window using the user's
    REAL default Chrome profile (no --user-data-dir override) - already
    logged into YouTube, with real extensions/language prefs, but with no
    tabs/bookmarks/address bar, so it reads as part of the app rather than
    a separate browser window (2026-09-20: "a popup that is my active
    browser... but masquerades as the app" - same fix as the host app's own
    Netflix/Amazon links in app/web_server.py, duplicated here rather than
    cross-repo-imported since this package is deliberately its own
    independent repo, per this module's own docstring). Falls back to the
    plain OS-default-browser open this route always used before if Chrome
    isn't found at its expected install path, or on a non-Windows host."""
    if os.name != "nt":
        _open_url_fallback(url)
        return
    chrome = _find_chrome_exe()
    if not chrome:
        _open_url_fallback(url)
        if raise_on_open:
            threading.Thread(target=_bring_browser_to_front, name="yt-raise-browser", daemon=True).start()
        return
    before = _visible_chrome_windows()
    # Maximized (not the old 75%-centered size) - same as the host app's own
    # Netflix/Amazon windows (2026-09-21 there; this copy was left behind),
    # so everything you watch opens the same way: filling the screen but
    # keeping a real title bar with a close button.
    subprocess.Popen([chrome, f"--app={url}", "--start-maximized"], close_fds=True)
    if not raise_on_open:
        return
    hwnd = None
    for _ in range(25):
        time.sleep(0.2)
        new = _visible_chrome_windows() - before
        if new:
            hwnd = new.pop()
            break
    if hwnd is None:
        return
    # --start-maximized alone is ignored when Chrome folds the launch into an
    # already-running process (it then opens a small remembered-size window),
    # and a single maximize right after the window appears loses a race with
    # Chrome's own post-launch layout. Reasserting for ~2s outlasts both.
    for _ in range(8):
        _focus_window(hwnd, maximize=True)
        time.sleep(0.25)


def make_youtube_bp(deps):
    check_auth = deps["check_auth"]
    log = deps.get("log") or (lambda _m: None)

    bp = Blueprint("youtube", __name__)

    def safe(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except HTTPException:
                # check_auth()'s abort(403) and friends must pass straight
                # through — never masked as a 200 {ok:false}.
                raise
            except (api.APIError, ValueError) as ex:
                return jsonify({"ok": False, "error": str(ex)}), 200
            except Exception as ex:  # noqa: BLE001 - never leak a stack to the page
                log(f"YOUTUBE ROUTE ERROR {request.path}: {ex}")
                return jsonify({"ok": False, "error": "internal error (see server log)"}), 200
        return wrapper

    # --- reads (ungated) --------------------------------------------------

    @bp.get("/api/data")
    @safe
    def data():
        return jsonify({
            "ok": True,
            "videos": catalog.new_videos(),
            "channels": [
                {"channel_id": c["channel_id"], "title": c.get("title"),
                 "handle": c.get("handle"), "avatar_url": c.get("avatar_url"),
                 "consecutive_failures": c.get("consecutive_failures", 0),
                 "last_ok_fetch": c.get("last_ok_fetch")}
                for c in catalog.load_channels()
            ],
            "status": status.youtube_status(),
            "new_window_days": config.load_settings()["new_window_days"],
        })

    @bp.get("/api/status")
    @safe
    def status_route():
        return jsonify({"ok": True, "status": status.youtube_status()})

    # --- open a video in the machine's REAL default browser -----------

    @bp.post("/api/open")
    @safe
    def open_video():
        """When the request comes from the machine the host app runs on, open the
        watch page via the OS default-browser association (the user's real
        Chrome — extensions, password manager, YouTube login — not the
        isolated profile the host app itself runs in). Remote devices (phone /
        travel laptop over the LAN) get told to open it client-side instead.

        settings.json `open_browser_command` (optional): an explicit executable
        to use instead of the OS default, e.g. a specific Chrome install."""
        check_auth()
        vid = (request.get_json(force=True, silent=True) or {}).get("id", "")
        if not isinstance(vid, str) or not _VIDEO_ID_RE.match(vid):
            return jsonify({"ok": False, "error": "bad video id"}), 200
        if not _request_is_local():
            return jsonify({"ok": True, "opened": "client"})  # remote — the device opens its own browser
        url = "https://www.youtube.com/watch?v=" + vid
        try:
            settings = config.load_settings()
            cmd = (settings.get("open_browser_command") or "").strip()
            raise_on_open = settings.get("raise_browser_on_open", True)
            if cmd:
                # An explicit override - respected as-is, not forced into
                # app-mode flags, since it could be any arbitrary command,
                # not necessarily Chrome.
                subprocess.Popen([cmd, url])  # list form, no shell — url is a validated youtube.com URL
                if raise_on_open:
                    threading.Thread(target=_bring_browser_to_front, name="yt-raise-browser",
                                     daemon=True).start()
            else:
                threading.Thread(
                    target=_launch_chrome_app_window, args=(url,), kwargs={"raise_on_open": raise_on_open},
                    name="yt-chrome-app-window", daemon=True,
                ).start()
            log(f"YOUTUBE OPEN {vid} -> default browser")
            return jsonify({"ok": True, "opened": "server"})
        except Exception as ex:  # noqa: BLE001
            log(f"YOUTUBE OPEN failed for {vid}: {ex}")
            return jsonify({"ok": True, "opened": "failed"})  # client falls back to window.open

    # --- watched (gated) ------------------------------------------------

    @bp.post("/api/watched")
    @safe
    def watched():
        check_auth()
        vid = (request.get_json(force=True, silent=True) or {}).get("id")
        if not vid or not isinstance(vid, str):
            return jsonify({"ok": False, "error": "missing video id"}), 200
        state.mark_watched(vid)
        return jsonify({"ok": True})

    @bp.post("/api/watched/undo")
    @safe
    def watched_undo():
        check_auth()
        vid = (request.get_json(force=True, silent=True) or {}).get("id")
        if not vid or not isinstance(vid, str):
            return jsonify({"ok": False, "error": "missing video id"}), 200
        state.unmark_watched(vid)
        return jsonify({"ok": True})

    # --- channels (gated for writes) ----------------------------------

    @bp.get("/api/channels")
    @safe
    def channels_list():
        return jsonify({"ok": True, "channels": catalog.load_channels()})

    @bp.post("/api/channels")
    @safe
    def channels_add():
        check_auth()
        url = (request.get_json(force=True, silent=True) or {}).get("url", "")
        if not url or not isinstance(url, str):
            return jsonify({"ok": False, "error": "missing channel url or @handle"}), 200
        channel = fetch.add_channel(url, log)
        return jsonify({"ok": True, "channel": channel})

    @bp.delete("/api/channels/<channel_id>")
    @safe
    def channels_remove(channel_id):
        check_auth()
        fetch.remove_channel(channel_id, log)
        return jsonify({"ok": True})

    return bp

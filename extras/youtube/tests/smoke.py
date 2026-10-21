"""Dependency-free smoke test — runs on the ecosystem's Python 3.14 (which has
Flask but not pytest). Exercises the contract surface and the core logic
against a throwaway data dir.

    python tests/smoke.py

The richer pytest suite (test_*.py) is for a machine that has pytest for 3.14.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_checks = 0


def check(label, cond):
    global _checks
    _checks += 1
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ok  {label}")


def point_config_at(tmp):
    from youtube import config
    data = Path(tmp) / "data"
    data.mkdir()
    config.DATA_DIR = data
    config.LOG_DIR = Path(tmp) / "logs"
    config.SETTINGS_PATH = Path(tmp) / "settings.json"
    config.CHANNELS_PATH = data / "channels.json"
    config.VIDEO_CACHE_PATH = data / "video_cache.json"
    config.SEEN_VIDEOS_PATH = data / "seen_videos.json"


def main():
    tmp = tempfile.mkdtemp(prefix="yt-smoke-")
    point_config_at(tmp)

    # contract surface imports
    import youtube
    for sym in ("make_youtube_bp", "render_youtube_block", "start_poller", "youtube_status"):
        check(f"exports {sym}", hasattr(youtube, sym))

    from youtube import api, fetch, state, catalog, block, status, config
    _real_video_probe = api.video_probe  # api.video_probe gets monkeypatched below;
                                          # keep the real one for testing its own body

    NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def entry(**o):
        base = {"id": "v1", "title": "T", "published_at": "2026-09-08T00:00:00+00:00",
                "thumb_url": "https://i.ytimg.com/vi/v1/hqdefault.jpg"}
        base.update(o)
        return base

    # RSS-entry filtering (watch-page probe stubbed)
    def probe(**o):
        base = {"is_short": False, "duration_sec": 600, "live_state": "ok",
                "restricted": False, "restricted_reason": None}
        base.update(o)
        return base

    _verdict = {"v": True}   # what the stubbed /shorts/<id> redirect check answers
    api.shorts_url_check = lambda vid: _verdict["v"]
    api.video_probe = lambda vid: probe(is_short=True)
    rec, permanent = fetch._video_record(entry(), NOW)
    check("Short dropped, and marked permanent only because the /shorts/ check AGREES",
          rec is None and permanent is True)
    _verdict["v"] = None
    rec, permanent = fetch._video_record(entry(), NOW)
    check("probe says Short but the /shorts/ check can't tell: hidden, NOT buried (retried next poll)",
          rec is None and permanent is False)
    _verdict["v"] = False
    rec, permanent = fetch._video_record(entry(), NOW)
    check("probe says Short, YouTube says it isn't (long duration): kept as a normal video, never buried",
          rec and rec["id"] == "v1" and permanent is False)
    _verdict["v"] = True
    api.video_probe = lambda vid: probe()
    rec, permanent = fetch._video_record(entry(), NOW)
    check("normal video kept + duration + not permanent",
          rec and rec["id"] == "v1" and rec["duration_sec"] == 600 and permanent is False)
    api.video_probe = lambda vid: probe(is_short=False, duration_sec=45)
    rec, permanent = fetch._video_record(entry(), NOW, {"shorts_max_sec": 60})
    check("no Shorts, ever: duration<=shorts_max_sec drops it (permanent) once the /shorts/ check agrees",
          rec is None and permanent is True)
    _verdict["v"] = False
    rec, permanent = fetch._video_record(entry(), NOW, {"shorts_max_sec": 60})
    check("a <=60s clip YouTube says is NOT a Short: still hidden, but not buried",
          rec is None and permanent is False)
    _verdict["v"] = True
    # Premieres that turn into normal videos (BattleBots: ~30 min live at 13:00 PT, then a plain
    # video) were being buried forever as "confirmed Shorts" - 2026-10-10.
    api.video_probe = lambda vid: probe(was_live=True, live_ended=True, duration_sec=1887, is_short=True)
    rec, permanent = fetch._video_record(entry(), NOW)
    check("finished premiere with a real duration is a normal video, even if the Short flag misfired",
          rec and rec["duration_sec"] == 1887 and rec["live_origin"] is True and permanent is False)
    api.video_probe = lambda vid: probe(was_live=True, live_ended=True, duration_sec=12)
    rec, permanent = fetch._video_record(entry(), NOW)
    check("premiere that just ended (VOD still processing, tiny duration): deferred, never buried",
          rec is None and permanent is False)
    api.video_probe = lambda vid: probe(was_live=True, live_ended=False, duration_sec=30)
    rec, permanent = fetch._video_record(entry(), NOW)
    check("premiere still airing: deferred, never buried",
          rec is None and permanent is False)
    api.video_probe = lambda vid: probe(live_state="live", duration_sec=20, is_short=True)
    rec, permanent = fetch._video_record(entry(), NOW)
    check("live now is checked before the Short rules: deferred, never buried",
          rec is None and permanent is False)
    api.video_probe = lambda vid: probe(live_state="upcoming")
    rec, permanent = fetch._video_record(entry(), NOW)
    check("upcoming premiere dropped, not permanent (will air later)",
          rec is None and permanent is False)
    api.video_probe = lambda vid: {}
    rec, permanent = fetch._video_record(entry(), NOW)
    check("no Shorts, ever: probe failure defers the video (not permanent, retried next poll)",
          rec is None and permanent is False)
    api.video_probe = lambda vid: probe()
    from datetime import timedelta as _td
    rec, permanent = fetch._video_record(entry(published_at=(NOW + _td(days=2)).isoformat()), NOW)
    check("future-dated premiere skipped, not permanent", rec is None and permanent is False)

    # video_probe: retry-then-fail-open, and the marker check that stops a
    # bot-check/consent interstitial (a normal 200, just not the real watch
    # page) from being read as if it were real probe data.
    marker = '"videoId":"x"'
    _calls = {"n": 0}
    def flaky_then_ok(url, **kw):
        _calls["n"] += 1
        if _calls["n"] < 2:
            raise api.APIError("transient")
        return url, (f'<link rel="canonical" href="https://www.youtube.com/watch?v=x">'
                     f'...{marker}...{{"lengthSeconds":"888"}}...').encode(), 200
    api._get = flaky_then_ok
    p = _real_video_probe("x", retries=2, backoff=0.01)
    check("video_probe retries a transient failure then succeeds",
          p.get("duration_sec") == 888 and _calls["n"] == 2)

    def always_fails(url, **kw):
        raise api.APIError("down")
    api._get = always_fails
    check("video_probe fails open after exhausting retries",
          _real_video_probe("x", retries=1, backoff=0.01) == {})

    def wrong_page(url, **kw):
        return url, b'<link rel="canonical" href="https://www.youtube.com/watch?v=x">no marker', 200
    api._get = wrong_page
    check("video_probe rejects a 200 that isn't actually this video's page",
          _real_video_probe("x", retries=1, backoff=0.01) == {})

    def bot_wall(url, **kw):
        # 2026-09-14 real incident: YouTube's "Sign in to confirm you're not a
        # bot" wall carries the requested videoId, no lengthSeconds, and
        # "status":"LOGIN_REQUIRED" — it used to sail through the marker check
        # and get cached as a normal video with a bogus "age-restricted" flag.
        return url, (f'<link rel="canonical" href="https://www.youtube.com/watch?v=x">'
                     f'...{marker}..."status":"LOGIN_REQUIRED"...'
                     '"reason":"Sign in to confirm you’re not a bot"...').encode("utf-8"), 200
    api._get = bot_wall
    check("video_probe rejects YouTube's anti-bot wall instead of reading it as real",
          _real_video_probe("x", retries=1, backoff=0.01) == {})

    # RSS parse
    xml = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
           b'xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
           b'xmlns:media="http://search.yahoo.com/mrss/"><title>Cool</title>'
           b'<entry><yt:videoId>AAA</yt:videoId><title>One</title>'
           b'<published>2026-09-08T12:00:00+00:00</published>'
           b'<media:group><media:thumbnail url="https://i2.ytimg.com/vi/AAA/x.jpg"/></media:group></entry>'
           b'<entry><yt:videoId>BBB</yt:videoId><title>Two</title>'
           b'<published>2026-09-07T12:00:00+00:00</published></entry></feed>')
    _orig_get = api._get
    api._get = lambda url, **kw: (url, xml, 200)
    try:
        entries, ftitle = api.fetch_feed("UCxxxxxxxxxxxxxxxxxxxxx1", want_feed_title=True)
    finally:
        api._get = _orig_get
    check("fetch_feed parses ids", [e["id"] for e in entries] == ["AAA", "BBB"])
    check("fetch_feed reads feed title", ftitle == "Cool")
    check("fetch_feed thumb fallback", entries[1]["thumb_url"] == "https://i.ytimg.com/vi/BBB/hqdefault.jpg")

    # state
    state.mark_watched("abc"); check("mark/is watched", state.is_watched("abc"))
    state.unmark_watched("abc"); check("unmark watched", not state.is_watched("abc"))
    state.mark_watched("k"); state.mark_watched("d1"); state.mark_watched("d2")
    state.purge_videos({"d1", "d2"})
    check("purge_videos", set(state.load()["watched"]) == {"k"})

    # catalog / new_videos
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(days=2)).isoformat()
    stale = (now - timedelta(days=40)).isoformat()
    catalog.save_channels([{"channel_id": "UC1", "title": "Chan One", "avatar_url": None,
                            "added_at": fresh, "baseline_ids": [],
                            "last_ok_fetch": fresh, "consecutive_failures": 0}])
    mk = lambda i, p: {"id": i, "title": i, "published_at": p, "duration_sec": 0,
                       "thumb_url": None, "live_origin": False, "restricted": False, "restricted_reason": None}
    catalog.save_cache({"UC1": [mk("new1", fresh), mk("old1", stale), mk("seen1", fresh)]})
    state.mark_watched("seen1")
    rows = catalog.new_videos(now=now)
    check("new_videos filters watched+stale", [r["id"] for r in rows] == ["new1"])
    check("new_videos carries channel_title", rows[0]["channel_title"] == "Chan One")

    catalog.save_cache({"UC1": [mk("older", (now - timedelta(days=3)).isoformat()),
                                mk("newer", (now - timedelta(days=1)).isoformat())]})
    check("new_videos newest-first", [r["id"] for r in catalog.new_videos(now=now)] == ["newer", "older"])

    # reprobe_zero_duration: the one-off remediation for cache entries a past
    # burst of unpaced probes mis-cached with duration_sec=0 (some of them
    # real Shorts that never get re-checked once cached, since poll_channel
    # only probes newly-seen ids).
    catalog.save_channels([{"channel_id": "UCr", "title": "Reprobe Chan", "avatar_url": None,
                            "added_at": fresh, "baseline_ids": [],
                            "last_ok_fetch": fresh, "consecutive_failures": 0}])
    zd = lambda i: {"id": i, "title": i, "published_at": fresh, "duration_sec": 0,
                    "thumb_url": None, "live_origin": False, "restricted": True,
                    "restricted_reason": "age-restricted"}
    catalog.save_cache({"UCr": [zd("was-short"), zd("was-fine"), zd("still-broken")]})
    def reprobe_stub(vid):
        if vid == "was-short":
            return {"is_short": True, "duration_sec": 40, "live_state": "ok",
                     "restricted": False, "restricted_reason": None}
        if vid == "was-fine":
            return {"is_short": False, "duration_sec": 900, "live_state": "ok",
                     "restricted": False, "restricted_reason": None}
        return {}  # still failing
    api.video_probe = reprobe_stub
    fixed, dropped = fetch.reprobe_zero_duration(lambda m: None)
    check("reprobe_zero_duration fixed/dropped counts", fixed == 1 and dropped == 2)
    reprobed_cache = catalog.load_cache()["UCr"]
    reprobed_ids = {v["id"] for v in reprobed_cache}
    check("reprobe drops the real Short", "was-short" not in reprobed_ids)
    check("reprobe fixes the real video's duration + clears bogus restricted flag",
          next(v for v in reprobed_cache if v["id"] == "was-fine")["duration_sec"] == 900 and
          next(v for v in reprobed_cache if v["id"] == "was-fine")["restricted"] is False)
    check("no Shorts, ever: a still-unconfirmed probe is removed, not left showing",
          "still-broken" not in reprobed_ids)

    # status
    st = status.youtube_status()
    check("status shape", set(["channels", "last_poll", "failing_count", "new_videos_waiting"]).issubset(st))
    check("status carries no credential/oauth keys",
          not any(k in st for k in ("account_connected", "oauth_configured", "secrets")))

    # block render — never raises, always has the inner hook
    for ctx in ({}, None, {"auth_token": "tok"}):
        out = block.render_youtube_block(ctx)
        check(f"render_youtube_block({ctx!r}) -> inner", 'id="youtube-block-inner"' in out)
    config.VIDEO_CACHE_PATH.write_text("{ broken json", encoding="utf-8")
    check("render survives corrupt cache",
          'id="youtube-block-inner"' in block.render_youtube_block({}))
    out = block.render_youtube_block({})
    check("render has no external <script src=>", "<script src" not in out and "<script  src" not in out)

    # DOM-inertness invariants (the pytest suite has these too, but pytest isn't
    # installed for 3.14 — so assert them here where they actually run)
    import re as _re
    _css = (ROOT / "youtube" / "assets" / "block.css").read_text(encoding="utf-8")
    _css_nc = _re.sub(r"/\*.*?\*/", "", _css, flags=_re.S)
    _bad = [s.strip() for chunk in _re.findall(r"([^{}]+)\{", _css_nc) for s in chunk.split(",")
            if s.strip() and not s.strip().startswith(("@", "--", "#youtube-block"))]
    check("every CSS selector scoped under #youtube-block", not _bad)
    _js = (ROOT / "youtube" / "assets" / "block.js").read_text(encoding="utf-8")
    check("block JS defines only window.YTBlock",
          set(_re.findall(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=", _js)) == {"YTBlock"})
    check("block JS is one IIFE (after banner comment)",
          _re.sub(r"^\s*(/\*.*?\*/\s*)*", "", _js, count=1, flags=_re.S).startswith("(function"))
    check("block JS embeds no iframe / message listener (external playback only)",
          "<iframe" not in _js and 'addEventListener("message"' not in _js and "youtube.com/embed" not in _js)
    check("card open action targets the real watch URL",
          'window.open(watchUrl(id)' in _js and "youtube.com/watch?v=" in _js)

    # blueprint factory
    from flask import Flask
    app = Flask("smoke")
    calls = {"auth": 0}
    deps = {"check_auth": lambda: calls.__setitem__("auth", calls["auth"] + 1),
            "auth_token": "tok", "log": lambda m: None}
    app.register_blueprint(youtube.make_youtube_bp(deps), url_prefix="/youtube")
    client = app.test_client()
    r = client.get("/youtube/api/data")
    check("GET /youtube/api/data 200", r.status_code == 200 and r.get_json().get("ok") is True)
    r = client.get("/youtube/api/status")
    check("GET /youtube/api/status ok", r.get_json().get("ok") is True)
    r = client.post("/youtube/api/watched", json={"id": "zzz"})
    check("POST /youtube/api/watched calls check_auth", calls["auth"] == 1)
    check("watched persisted via route", state.is_watched("zzz"))

    # /api/open — validates the id, and a remote request never opens anything.
    # Default path (no open_browser_command override) launches a Chrome
    # app-mode window via _launch_chrome_app_window (2026-09-20) - stub that
    # itself, never os.startfile/subprocess.Popen, so a real chrome.exe on
    # the test machine is never actually invoked.
    import time as _t
    import youtube.routes as _routes
    _opened = []
    _routes._launch_chrome_app_window = lambda u, raise_on_open=True: _opened.append(u)
    r = client.post("/youtube/api/open", json={"id": "not a valid id"})
    check("open rejects bad video id", r.get_json().get("ok") is False)
    r = client.post("/youtube/api/open", json={"id": "dQw4w9WgXcQ"},
                    environ_overrides={"REMOTE_ADDR": "10.0.0.9"})
    check("open from a remote device -> opened:client (no server-side launch)",
          r.get_json().get("opened") == "client" and not _opened)
    r = client.post("/youtube/api/open", json={"id": "dQw4w9WgXcQ"},
                    environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    _t.sleep(0.1)
    check("open from the local machine -> opened:server via chrome app-mode window",
          r.get_json().get("opened") == "server" and _opened == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"])

    # open_browser_command override - an explicit custom launcher, respected
    # as-is (never forced into app-mode flags) and still raised via the old
    # PID/title-based _bring_browser_to_front, not the Chrome-specific one.
    _popen_calls = []
    _routes.subprocess.Popen = lambda args, **k: _popen_calls.append(args)
    _raise_calls = []
    _routes._bring_browser_to_front = lambda *a, **k: _raise_calls.append(True)
    settings_path = config.SETTINGS_PATH
    settings_path.write_text(json.dumps({"open_browser_command": "C:\\fake\\browser.exe"}), encoding="utf-8")
    r = client.post("/youtube/api/open", json={"id": "dQw4w9WgXcQ"},
                    environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    _t.sleep(0.1)
    check("open_browser_command override launches that command, not Chrome app-mode",
          r.get_json().get("opened") == "server"
          and _popen_calls == [["C:\\fake\\browser.exe", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"]]
          and _raise_calls == [True])
    settings_path.write_text("{}", encoding="utf-8")  # reset for any tests below
    # add-channel end to end, with resolve + feed + probe stubbed (no network)
    _n = datetime.now(timezone.utc)
    old_id_date = (_n - timedelta(days=90)).isoformat()   # older than the 14d window -> baselined
    new_id_date = (_n - timedelta(days=3)).isoformat()    # within the window -> seeded immediately
    api.resolve_channel = lambda u: {"channel_id": "UCsmoke", "title": "Smoke Chan", "avatar_url": None}
    api.fetch_feed = lambda cid, want_feed_title=False: [
        {"id": "recent1", "title": "recent", "published_at": new_id_date, "thumb_url": None},
        {"id": "old1", "title": "old", "published_at": old_id_date, "thumb_url": None},
    ]
    api.video_probe = lambda vid: {"is_short": False, "duration_sec": 500, "live_state": "ok",
                                   "restricted": False, "restricted_reason": None}
    r = client.post("/youtube/api/channels", json={"url": "@smoke"})
    body = r.get_json()
    check("add-channel ok", r.status_code == 200 and body.get("ok") is True)
    check("add-channel persisted", any(c["channel_id"] == "UCsmoke" for c in catalog.load_channels()))
    check("add-channel baselines only pre-window ids", body["channel"]["baseline_ids"] == ["old1"])
    check("add-channel seeds recent video into New view",
          [v["id"] for v in catalog.new_videos()] == ["recent1"])
    r = client.delete("/youtube/api/channels/UCsmoke")
    check("remove-channel ok",
          r.get_json().get("ok") is True and
          not any(c["channel_id"] == "UCsmoke" for c in catalog.load_channels()))

    # poll_channel folds a confirmed Short into baseline_ids so it's never
    # re-probed on a later poll (the fix for 2026-09-14's request-volume issue)
    probe_calls = {"n": 0}
    def counting_probe(vid):
        probe_calls["n"] += 1
        if vid == "short1":
            return {"is_short": True, "duration_sec": 40, "live_state": "ok",
                     "restricted": False, "restricted_reason": None}
        return {"is_short": False, "duration_sec": 500, "live_state": "ok",
                 "restricted": False, "restricted_reason": None}
    api.video_probe = counting_probe
    poll_chan = {"channel_id": "UCpoll", "title": "Poll Chan", "avatar_url": None,
                 "added_at": new_id_date, "baseline_ids": [], "last_ok_fetch": new_id_date,
                 "consecutive_failures": 0}
    catalog.with_lock(lambda channels, cache: (channels + [poll_chan], {**cache, "UCpoll": []}))
    api.fetch_feed = lambda cid, want_feed_title=False: [
        {"id": "short1", "title": "a short", "published_at": new_id_date, "thumb_url": None},
        {"id": "long1", "title": "a long video", "published_at": new_id_date, "thumb_url": None},
    ]
    fetch.poll_channel(poll_chan, lambda m: None)
    updated = next(c for c in catalog.load_channels() if c["channel_id"] == "UCpoll")
    check("poll_channel folds a confirmed Short into baseline_ids",
          "short1" in updated["baseline_ids"] and "long1" not in updated["baseline_ids"])
    calls_before_second_poll = probe_calls["n"]
    fetch.poll_channel(updated, lambda m: None)
    check("the now-baselined Short isn't re-probed on the next poll",
          probe_calls["n"] == calls_before_second_poll)  # only long1 would need a probe, and it's already known
    catalog.with_lock(lambda channels, cache: (
        [c for c in channels if c["channel_id"] != "UCpoll"],
        {k: v for k, v in cache.items() if k != "UCpoll"}))

    # a resolve failure must be a clean {ok:false}, not a 500
    api.resolve_channel = lambda u: (_ for _ in ()).throw(api.APIError("no channel found"))
    r = client.post("/youtube/api/channels", json={"url": "@nope"})
    check("add-channel resolve-fail -> ok:false (not 500)",
          r.status_code == 200 and r.get_json().get("ok") is False)

    # a rejecting check_auth must produce a real 403, not a masked 200
    from flask import Flask as _F, abort as _abort
    app2 = _F("smoke2")
    app2.register_blueprint(
        youtube.make_youtube_bp({"check_auth": lambda: _abort(403), "auth_token": "t", "log": lambda m: None}),
        url_prefix="/youtube")
    r = app2.test_client().post("/youtube/api/watched", json={"id": "q"})
    check("rejecting check_auth -> 403 (not masked)", r.status_code == 403)

    print(f"\nALL {_checks} CHECKS PASSED")


if __name__ == "__main__":
    main()

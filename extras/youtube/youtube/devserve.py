"""Standalone dev harness:  python -m youtube.devserve

Mounts the blueprint on a bare Flask app exactly the way the host app will, serves a
minimal page that embeds render_youtube_block() inside <section id="youtube-block">,
and starts the poller. Lets the package be built and exercised solo, before
The host app wires it in.

Not part of the contract — the host app never imports this module.

Data safety: this harness runs against a SEPARATE `data_dev/` directory, never
the real `data/` (which holds the user's curated channel list + watched history
and has no other backup). Pass --real to override.
"""
import secrets
import sys

from flask import Flask, Response, abort, request

from . import config, make_youtube_bp, render_youtube_block, start_poller

PORT = 5185
_TOKEN = secrets.token_urlsafe(24)

if "--real" not in sys.argv:
    _dev = config.PROJECT_ROOT / "data_dev"
    _dev.mkdir(exist_ok=True)
    config.DATA_DIR = _dev
    config.CHANNELS_PATH = _dev / "channels.json"
    config.VIDEO_CACHE_PATH = _dev / "video_cache.json"
    config.SEEN_VIDEOS_PATH = _dev / "seen_videos.json"


def _check_auth():
    if request.headers.get("X-Auth-Token") != _TOKEN:
        abort(403)


def _log(msg):
    print(f"[youtube] {msg}", flush=True)


def create_app():
    app = Flask(__name__)
    deps = {"check_auth": _check_auth, "auth_token": _TOKEN, "log": _log}
    app.register_blueprint(make_youtube_bp(deps), url_prefix="/youtube")

    @app.after_request
    def _csp(resp):
        # The subset of the host app's CSP widening that this block needs.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' https://*.ytimg.com https://yt3.ggpht.com https://yt3.googleusercontent.com data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'"
        )
        return resp

    @app.route("/")
    def index():
        block = render_youtube_block({"auth_token": _TOKEN, "log": _log})
        page = (
            "<!doctype html><meta charset=utf-8>"
            "<title>YouTube block — devserve</title>"
            "<body style='margin:0;background:#14161a'>"
            "<section id='youtube-block' style='max-width:1200px;margin:0 auto;padding:24px'>"
            + block +
            "</section></body>"
        )
        return Response(page, mimetype="text/html")

    return app


def main():
    start_poller(_log)
    app = create_app()
    print(f"[youtube] devserve on http://127.0.0.1:{PORT}/  (auth token: {_TOKEN})")
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()

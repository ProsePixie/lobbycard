"""render_youtube_block(ctx) -> str — contract symbol #2.

Returns one self-contained HTML string: scoped <style> (every selector under
#youtube-block), a JSON data island, and one IIFE <script> (defines only
window.YTBlock). The host app drops it inside <section id="youtube-block" hidden>.

Hard guarantee: this function NEVER raises. Any internal failure returns a
minimal valid "unavailable" node so the host app's page render can't be broken.

ctx = {"auth_token": str, "log": callable}   (both optional)
"""
import json
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "assets"

_FALLBACK = (
    '<div id="youtube-block-inner">'
    '<p style="color:#9aa0a6;padding:24px 0">YouTube block unavailable right now.</p>'
    "</div>"
)


def _asset(name):
    return (_ASSETS / name).read_text(encoding="utf-8")


def render_youtube_block(ctx=None):
    ctx = ctx or {}
    log = ctx.get("log") or (lambda _m: None)
    try:
        from . import catalog, config, status  # imported here so import errors degrade too

        island = {
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
            "authToken": ctx.get("auth_token", ""),
        }
        # json.dumps then escape </ so a "</script>" inside any title can't
        # break out of the data island.
        island_json = json.dumps(island).replace("</", "<\\/")

        return (
            '<div id="youtube-block-inner">'
            "<style>" + _asset("block.css") + "</style>"
            '<div class="yt-block-mount"></div>'
            '<script id="yt-data" type="application/json">' + island_json + "</script>"
            "<script>" + _asset("block.js") + "</script>"
            "</div>"
        )
    except Exception as ex:  # noqa: BLE001 - never break the host page
        try:
            log(f"YOUTUBE BLOCK render failed (showing fallback): {ex}")
        except Exception:
            pass
        return _FALLBACK

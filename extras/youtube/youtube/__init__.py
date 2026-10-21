"""YouTube — an independently-owned block that renders inside the host app.

Contract surface (the ONLY things the host app imports). See
README.md.

    from youtube import (
        make_youtube_bp,        # (deps) -> flask.Blueprint, mount at /youtube
        render_youtube_block,   # (ctx) -> str, never raises
        start_poller,           # () -> None, idempotent daemon
        youtube_status,         # () -> dict
    )

`deps` / `ctx` = {"check_auth": callable, "auth_token": str, "log": callable}
(render_youtube_block only needs auth_token + log). This package imports nothing
from the host app.
"""
from .block import render_youtube_block
from .poller import start_poller
from .routes import make_youtube_bp
from .status import youtube_status

__all__ = ["make_youtube_bp", "render_youtube_block", "start_poller", "youtube_status"]

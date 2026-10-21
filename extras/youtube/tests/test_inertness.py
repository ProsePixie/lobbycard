"""The DOM-inertness hard rule from the the host app contract, enforced.

- render_youtube_block() never raises and always yields #youtube-block-inner
- no external <script src> (the player iframe is added by JS at click time)
- every CSS selector in the block's <style> is scoped under #youtube-block
- the block JS defines exactly one global: window.YTBlock
"""
import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "youtube" / "assets"


def test_render_never_raises_and_has_inner(tmp_data):
    from youtube import render_youtube_block

    for ctx in ({}, None, {"auth_token": "x"}, {"log": lambda m: None}):
        out = render_youtube_block(ctx)
        assert isinstance(out, str)
        assert 'id="youtube-block-inner"' in out


def test_render_survives_broken_data(tmp_data):
    from youtube import config, render_youtube_block

    config.VIDEO_CACHE_PATH.write_text("{ this is not json", encoding="utf-8")
    out = render_youtube_block({})
    assert 'id="youtube-block-inner"' in out  # fallback or real, but valid


def test_no_external_script_src_in_rendered_block(tmp_data):
    from youtube import render_youtube_block

    out = render_youtube_block({})
    assert not re.search(r"<script[^>]+\bsrc=", out), "block must not load external scripts"


def test_css_selectors_all_scoped():
    css = (ASSETS / "block.css").read_text(encoding="utf-8")
    # strip comments
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # crude rule splitter: take the selector part before each '{'
    for chunk in re.findall(r"([^{}]+)\{", css):
        for sel in chunk.split(","):
            sel = sel.strip()
            if not sel or sel.startswith("@") or sel.startswith("--"):
                continue
            assert sel.startswith("#youtube-block"), f"unscoped selector: {sel!r}"


def test_js_defines_only_one_global():
    js = (ASSETS / "block.js").read_text(encoding="utf-8")
    globals_assigned = set(re.findall(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=", js))
    assert globals_assigned == {"YTBlock"}, f"unexpected globals: {globals_assigned}"
    # one IIFE wrapper (allow a leading /* */ banner comment)
    body = re.sub(r"^\s*(/\*.*?\*/\s*)*", "", js, count=1, flags=re.S)
    assert body.startswith("(function"), "block JS must be a single IIFE"


def test_js_has_no_embedded_player():
    """Playback opens youtube.com in the real browser — the block embeds no
    iframe and adds no 'message' listener."""
    js = (ASSETS / "block.js").read_text(encoding="utf-8")
    assert "<iframe" not in js
    assert 'addEventListener("message"' not in js
    assert "youtube.com/embed" not in js
    assert "window.open(watchUrl(id)" in js

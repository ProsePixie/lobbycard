# Personal pack (example)

A *pack* is a plain folder that changes how your copy looks and sounds without touching any code:
its name, tagline, banner, loading-screen lines, household names, and an optional franchise mode.

To use one:

1. Copy this folder to `personal/` (it is git-ignored, so it stays on your machine), or put the
   folder anywhere and set `"pack_dir"` in `config.json` (or the `MEDIABOX_PACK` environment variable).
2. Keep only the files you want to change; anything missing falls back to the built-in pack.
3. Restart the app.

| File | What it does |
| --- | --- |
| `pack.json` | `app_name`, `tagline`, `people` (two display names), `accent` colours, `field_guide_url` |
| `hints.json` | loading-screen lines by category, small captions in `_labels`, and `idle` lines for the phone remote |
| `banner.jpg` | header art, about 1600x368 (`.png` works too) |
| `boot.jpg` | loading-screen art, about 1920x1080, focal point right of centre |
| `favicon.png`, `app-icon-180/192/512.png` | browser tab and home-screen icons |
| `franchise.json` | optional Franchise mode: an ordered list of TMDB titles with its own palette |
| `franchise-banner.jpg`, `franchise-icon.png` | art for that mode |
| `youtube-banner.jpg` | header art while the optional YouTube block is on |
| `field_guide.html` | a longer guide, served at `/guide` (the Help menu shows a button when it exists) |

Two more files are *data*, not look-and-feel, and go in your data folder (`data/` by default),
not in a pack: `scan_overrides.json` and `library_rules.json`. Examples are included here.
They hold per-library fixes (mislabelled seasons, a movie that belongs inside a show's season list)
so none of that has to live in code. See `docs/CONFIG.md`.

Use only art, names and text you have the right to use. The built-in pack is original and generated
by `tools/make_art.py`.

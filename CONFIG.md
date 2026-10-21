# Configuration

Almost everything works with no configuration at all (`python run.py --demo`) and the first run of
`python run.py` writes the essentials for you through the setup page. This file lists everything that can
be set by hand.

## Where settings come from

Highest first:

1. **Environment variables**, handy for one-off runs and CI.
2. **`config.json`** in the project root (git-ignored). The setup page writes it. `config.example.json` is a template.
3. **Built-in defaults**: nothing personal, nothing machine-specific.

`python pipeline/app_config.py` prints what the app currently sees.

## `config.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `archive_root` | `""` | The organised library: `<archive_root>/<Category>/<Show>/Season NN/...`. Empty means "first run": the setup page is shown. |
| `queue_roots` | `[]` | Incoming folders: `[{"label": "Incoming", "path": "..."}]`. The first one is the folder Auto-File works from. A bare string is accepted too. |
| `vlc_path` | `""` | VLC executable. Empty: looked up on `PATH`, then the usual install locations. |
| `ffprobe_path`, `ffmpeg_path` | `""` | Same lookup. `ffprobe` is optional (file runtimes, chapters, audio/subtitle choice). |
| `browser_path` | `""` | Browser used for popup windows. Empty: Chrome from `PATH` or the usual locations. |
| `pack_dir` | `""` | Folder of your own name, art and text (see below). Empty means `./personal` if it exists. |
| `streaming_region` | `"US"` | Two-letter region for generated Prime Video links. |
| `amazon_domain` | `""` | Overrides the storefront derived from `streaming_region`, for example `"amazon.de"`. |
| `youtube_dir` | `""` | Folder of the optional YouTube block, for example `"extras/youtube"` (relative paths are read from the project root). Empty means off. |
| `companion_data_dir` | `""` | Data folder of an optional companion tool that shares files with this app (see below). Empty means none. |
| `html_snapshot_path` | `<data>/library_snapshot.html` | Where `app/generate_html.py` writes a static, read-only snapshot. |
| `demo` | `false` | Use the built-in sample library. |

## Environment variables

| Variable | Effect |
| --- | --- |
| `MEDIABOX_<KEY>` | Any `config.json` key above, upper-cased: `MEDIABOX_ARCHIVE_ROOT`, `MEDIABOX_VLC_PATH`, ... |
| `MEDIABOX_QUEUE_ROOT` | One incoming folder (label "Incoming"). |
| `MEDIABOX_CONFIG` | Use this file instead of `./config.json`. |
| `MEDIABOX_DATA_DIR` | Where every generated and personal file lives (default `./data`, or `./demo_data` in demo mode). |
| `MEDIABOX_DEMO=1` | Same as `--demo`. |
| `MEDIABOX_PORT` | Web port, default `5183`. |
| `MEDIABOX_REMOTE_PORT` | The phone-remote-only port, default `5184`. |
| `MEDIABOX_HOST` | Listen address, default `127.0.0.1` (this machine only). `0.0.0.0` (or `python run.py --lan`) lets a phone on your network reach it; changes still need the per-run token. |
| `MEDIABOX_PACK` | Pack folder, same as `pack_dir`. |
| `MEDIABOX_TMDB_API_KEY` | TMDB v3 API key. `TMDB_API_KEY` works too. |

`python run.py --port N` sets the web port and the next one up for the remote.

## The TMDB key

Resolved from `MEDIABOX_TMDB_API_KEY` / `TMDB_API_KEY`, then `<data dir>/tmdb_api_key.txt`. It is a free
**v3 "API Key"** (32 characters), not the long v4 access token. The setup page tests it and writes the file
with owner-only permissions where the OS allows. It is never placed in `config.json`. Without a key the app
still shows whatever is already in `media_db.json`; matching, posters and episode data need it.

## Settings page

`/setup` (Admin menu → Settings) edits the things most people need, and works for the first run and afterwards. It only answers requests from the machine running the server.

| Section | What it changes | Where it is saved | Takes effect |
| --- | --- | --- | --- |
| Library folder, incoming folders | `archive_root`, `queue_roots` | `config.json` | after a restart (the page does it) |
| TMDB key | the v3 API key | `<data dir>/tmdb_api_key.txt` | after a restart |
| Title, subtitle | the header text, browser tab, home-screen name | `<data dir>/appearance.json` | next page load |
| Header image | JPEG, PNG, WebP or GIF up to 8 MB, stored as a JPEG at most 1920 px wide | `<data dir>/appearance/header.jpg` | next page load |
| Image position | which vertical part of a tall image stays in the strip | `appearance.json` | next page load |
| Accent colour, watch-list names | `--accent`, the two names | `appearance.json` | next page load |

Settings made here win over a pack's `pack.json`. "Reset all to the defaults" removes them.

## Packs: name, look and flavour

A pack is a folder. For each file the app looks in your personal pack, then (in demo mode) `demo/pack`, then
the built-in `packs/default`, and uses the first it finds, so a personal pack only needs what it changes.

| File | What it does |
| --- | --- |
| `pack.json` | `app_name`, `tagline`, `people` (two display names), `accent` (`{"light": "#hex", "dark": "#hex"}` or one hex), `field_guide_url` |
| `hints.json` | Loading-screen lines `{"category": ["line", ...]}`, captions under `_labels`, `idle` lines for the phone remote |
| `banner.jpg` / `.png` | Header art, about 1600x368 |
| `boot.jpg` / `.png` | Loading-screen art, about 1920x1080 with its focal point right of centre |
| `favicon.png`, `app-icon-180/192/512.png` | Browser tab and home-screen icons |
| `franchise.json` | Optional franchise mode (below) |
| `franchise-banner.jpg`, `franchise-icon.png` | Art for that mode |
| `youtube-banner.jpg` | Header art while the YouTube block is on |
| `field_guide.html` | A longer guide served at `/guide`; the Help menu shows a button when it exists |

The two household members are stored as `you` and `partner` in `user_state.json`; only their display names
and initials come from the pack.

### Franchise mode

```json
{
  "name": "Example Saga",
  "theme": { "bg": "#1a1024", "panel": "#25173a", "text": "#efe6fa", "text_dim": "#b9a3d6",
             "border": "#3d2760", "accent": "#b57cff", "accent_soft": "#2f1d4d", "chip_bg": "#2c1b47" },
  "titles": [ { "id": 603, "type": "Movie", "release": 1, "chrono": 1 } ]
}
```

`id` is the TMDB id, `type` is `Movie` or `TV`, `release` is release order and `chrono` is story order. Entries for
titles you don't own do nothing, so upcoming ones can be listed in advance. The mode appears only when at least
one listed title is in your library. It gives a header button and a filter button, a timeline badge in each
title's popup with a release/story order switch, a single *what's next* panel, and the pack's palette and banner.

## Data files (`<data dir>`)

Generated and personal state, all plain JSON and all safe to delete: they are rebuilt (watched history excepted).

| File | Holds |
| --- | --- |
| `media_db.json` | The scanned, matched library. Rebuilt by every refresh. |
| `user_state.json` | **Yours:** watched marks, retention choices, watch lists, overrides. Back this one up. |
| `tmdb_*cache*.json`, `tmdb_images/`, `wiki_images/` | Caches. |
| `refresh_status.json`, `refresh_step_*.json`, `system_status.json` | Refresh progress and a status summary other tools can read. |
| `session_auth.json` | The current run's token, so a local helper process can report a watched mark. |
| `physical_media_seed.json`, `amazon_purchases_seed.json`, `streaming_follows_seed.json`, `wanted_seed.json` | Hand-maintained lists of discs, purchases, followed streaming titles and wanted titles that appear in the library without a file on disk. |
| `appearance.json`, `appearance/header.jpg` | What the Settings page saved (title, subtitle, accent, names, header image). |
| `scan_ignore.txt` | Folder names the scanner skips. A `.mediaboxignore` file inside a folder does the same. |
| `scan_overrides.json` | Per-library scan fixes (below). |
| `library_rules.json` | Hand-curated exceptions (below). |

### `scan_overrides.json`

For files the scanner can't place by name alone. Both tables are optional; an example is in
[`personal.example/scan_overrides.json`](personal.example/scan_overrides.json).

```json
{
  "episode_number_overrides": { "some show": [["the christmas special", 0, 1]] },
  "directory_leaf_overrides": { "Season1": [["Pilot", "Some Show", "TV", 1, 1, null]] }
}
```

* `episode_number_overrides`: for a show (normalised title), if a file name contains the text, treat it as
  that season and episode. Fixes specials that were all filed as `S01E00`.
* `directory_leaf_overrides`: for files in a folder with this name, if a file name contains the text, treat it as
  `[title, type, season, episode, year]`. Fixes folders whose names parse as nonsense.

### `library_rules.json`

```json
{
  "movie_to_tv_merge": { "1923": [1920, 2.5] },
  "completeness_excluded_tmdb_ids": { "121": "collected on purpose, never track gaps" },
  "manual_covers": [["Some Disc", "https://example.invalid/cover.png"]]
}
```

* `movie_to_tv_merge`: show an owned movie inside a TV show's season list, in watch order. The slot is fractional
  (2.5 sits between seasons 2 and 3) so it can never collide with a real season.
* `completeness_excluded_tmdb_ids`: never flag missing episodes for these shows.
* `manual_covers`: cover images for physical-collection discs that have no TMDB match.

## Companion tools

`companion_data_dir` points at the data folder of another local tool that cooperates through plain files:
a status file saying which incoming folders it is about to file (so one-click filing doesn't race it), a list
of episodes you told it to stop chasing, streaming availability, and a hand-off file of titles to follow
(`streaming_follow_requests.json`). Every read is best-effort: a missing file just means "nothing to report".
The app also writes `system_status.json` for such tools.

## Optional extras

| | |
| --- | --- |
| [`extras/youtube/`](extras/youtube/README.md) | A curated-channel block; set `youtube_dir` to turn it on. |
| [`extras/vortex/`](extras/vortex/README.md) | A small Windows launcher window. |
| [`extras/windows/handlers/`](extras/windows/handlers/README.md) | `openvlc://` / `openfolder://` protocol handlers, only needed to click those links from another Windows PC. |

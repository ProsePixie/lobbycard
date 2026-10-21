# YouTube block (optional example)

A small, self-contained block that renders *inside* the library page: a hand-curated list of YouTube
channels, with a "New" view of recent unwatched uploads. It is here as an example of how a bolt-on
block plugs into the host app, and it works as-is.

* **No API key, no Google account.** It reads each channel's public RSS feed and, per new video, one
  watch-page request (to tell Shorts, live streams and premieres from ordinary uploads).
* Real uploads only: Shorts, live streams and unfinished premieres are left out.
* Clicking a video opens `youtube.com/watch` in your normal browser. "Mark watched" is a manual button.
* A background thread polls hourly (`settings.json`: `poll_interval_sec`). It does nothing until you add a channel.

## Turn it on

Set the block's folder in `config.json` (a relative path is read from the project root):

```json
{ "youtube_dir": "extras/youtube" }
```

or `MEDIABOX_YOUTUBE_DIR=extras/youtube`, then restart. A YouTube button appears in the header.
Channel list and watch history are stored in `<data dir>/youtube/`.

## How it plugs in

`youtube/__init__.py` exports the only four things the host imports:

| Symbol | Purpose |
| --- | --- |
| `make_youtube_bp(deps)` | Flask blueprint mounted at `/youtube`. `deps` = `{check_auth, auth_token, log}`, injected so the block imports nothing from the host |
| `render_youtube_block(ctx)` | self-contained HTML/CSS/JS for the block; never raises |
| `start_poller(log)` | the hourly fetch thread; errors are contained |
| `youtube_status()` | a small dict for a status panel |

The whole mount is guarded: a missing folder or any import error leaves the toggle out and the app
boots exactly as before. `tests/` holds the block's own tests (`python extras/youtube/tests/smoke.py`
needs nothing installed; `pytest extras/youtube/tests` runs the rest). Standalone harness:
`python -m youtube.devserve` from this folder.

## The premiere bug (why `_video_record` looks the way it does)

A premiere is a live stream for about half an hour, then an ordinary video. While it airs, its watch
page reports no trustworthy duration, so it looked like a Short, and a Short is excluded permanently.
Entire series went missing from the feed until someone noticed. The fix: burying anything needs two
independent signals (the page's own flag *and* YouTube's `/shorts/<id>` redirect behaviour agreeing),
and anything that was ever live is deferred and re-checked, never buried. See `tests/test_fetch_filtering.py`.

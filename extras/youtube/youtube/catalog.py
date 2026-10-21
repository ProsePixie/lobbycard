"""Persistence for the two catalog files and the one derived query the UI needs.

  channels.json     [ {channel_id, title, avatar_url, added_at, baseline_ids,
                       last_ok_fetch, consecutive_failures} ]
  video_cache.json  { channel_id: [ {id, title, published_at, duration_sec,
                       thumb_url, live_origin, restricted, restricted_reason} ] }

This package is the sole writer of both. Every write is tmp+atomic_replace.
(Added to docs/PLAN.md's file layout: state.py owns seen_videos, catalog.py
owns these two — same "one file per concern" split the host app uses.)
"""
import json
import threading
from datetime import datetime, timezone

from . import config
from .atomic_write import atomic_replace

_lock = threading.Lock()


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path, obj, backup=False):
    config.ensure_dirs()
    if backup:
        config.snapshot_backup(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    atomic_replace(str(tmp), str(path))


def load_channels():
    data = _read_json(config.CHANNELS_PATH, [])
    return data if isinstance(data, list) else []


def save_channels(channels):
    _write_json(config.CHANNELS_PATH, channels, backup=True)  # the curated list has no other backup


def load_cache():
    data = _read_json(config.VIDEO_CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def save_cache(cache):
    _write_json(config.VIDEO_CACHE_PATH, cache)


def with_lock(mutator):
    """mutator(channels, cache) -> (channels, cache); persisted atomically.
    The poll thread and request handlers both mutate through here."""
    with _lock:
        channels = load_channels()
        cache = load_cache()
        channels, cache = mutator(channels, cache)
        save_channels(channels)
        save_cache(cache)
        return channels, cache


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def new_videos(now=None):
    """The New view: every unwatched, non-filtered video published within
    new_window_days, flat across channels, newest first. Pure read — safe to
    call from a request handler or from block.py at render."""
    from . import state  # local import avoids a cycle at module load

    settings = config.load_settings()
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - settings["new_window_days"] * 86400
    watched = state.load().get("watched", {})

    channels_by_id = {c["channel_id"]: c for c in load_channels()}
    cache = load_cache()
    rows = []
    for channel_id, videos in cache.items():
        chan = channels_by_id.get(channel_id)
        if not chan:
            continue
        for v in videos:
            if v["id"] in watched:
                continue
            published = _parse_iso(v.get("published_at"))
            if not published or published.timestamp() < cutoff:
                continue
            rows.append({
                **v,
                "channel_id": channel_id,
                "channel_title": chan.get("title"),
                "channel_avatar": chan.get("avatar_url"),
            })
    rows.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    return rows

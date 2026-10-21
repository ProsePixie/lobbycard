"""seen_videos.json — the household-level watched map. Keyed by YouTube video
id (there are no file paths here). Single shared map, matching how the rest of
the ecosystem treats watches.

  { "watched": { "<videoId>": {"at": "<iso8601>"} } }

Thread-safe read-modify-write, atomic replace — same shape as the host app's
state_store.py.
"""
import json
import threading
from datetime import datetime, timezone

from . import config
from .atomic_write import atomic_replace

_lock = threading.Lock()


def _default():
    return {"watched": {}}


def load():
    try:
        data = json.loads(config.SEEN_VIDEOS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default()
    if not isinstance(data, dict):
        return _default()
    data.setdefault("watched", {})
    return data


def _save(state):
    config.ensure_dirs()
    config.snapshot_backup(config.SEEN_VIDEOS_PATH)  # watched history has no other backup
    tmp = config.SEEN_VIDEOS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    atomic_replace(str(tmp), str(config.SEEN_VIDEOS_PATH))


def update(mutator):
    """mutator(state) -> None, mutates in place. Thread-safe."""
    with _lock:
        state = load()
        mutator(state)
        _save(state)
        return state


def is_watched(video_id):
    return video_id in load().get("watched", {})


def mark_watched(video_id):
    def m(state):
        state["watched"][video_id] = {"at": datetime.now(timezone.utc).isoformat()}
    update(m)


def unmark_watched(video_id):
    def m(state):
        state["watched"].pop(video_id, None)
    update(m)


def purge_videos(video_ids):
    """Drop watched entries for a set of ids — used when a channel is removed
    (its history goes with it, per the 'purge' decision)."""
    ids = set(video_ids)
    def m(state):
        state["watched"] = {k: v for k, v in state["watched"].items() if k not in ids}
    update(m)

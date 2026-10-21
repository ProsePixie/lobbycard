"""Persistent add/removed history for the library (user's "Idea #2").

Every pipeline run, diff the fresh media_db.json against the last snapshot
and append events to data\\library_ledger.jsonl (one JSON object per
line). A show that leaves the library gets a permanent tombstone in
data\\library_tombstones.json so it stays trackable - "did this get a new
season since I dropped it" is answerable later.

Identity is keyed on show_key() - `tmdb:{type}:{id}` for matched shows,
`title:{type}:{title}` when unmatched - NOT on path, so a folder reorg
(Movies\\X -> Series\\X) never logs a spurious remove+add. Episodes are
keyed `(show_key, season, episode)` from entries[].

Deliberately the LAST script in both PIPELINE_SCRIPTS and
daily_refresh.ps1's $Scripts (same "only runs after a genuinely clean
scan" invariant as build_system_status.py) - so a half-finished refresh
never writes a garbage diff.

Safety: if the fresh scan lost more than half the shows vs. last time
(drive unplugged, scan crash), NOTHING is logged as removed - a single
`scan_shrank` warning event is written instead and the snapshot is left
untouched so the next healthy run diffs against real history.

data\\library_ledger.jsonl and data\\library_tombstones.json are tracked
in git (irreplaceable history, like user_state.json).
data\\library_ledger_state.json is the regenerable snapshot - gitignored.
"""
import json
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
from app_config import DATA_DIR  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
LEDGER_PATH = os.path.join(DATA_DIR, "library_ledger.jsonl")
TOMBSTONES_PATH = os.path.join(DATA_DIR, "library_tombstones.json")
SNAPSHOT_PATH = os.path.join(DATA_DIR, "library_ledger_state.json")
USER_STATE_PATH = os.path.join(DATA_DIR, "user_state.json")

sys.path.insert(0, os.path.join(PROJECT_ROOT, "pipeline"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))
from atomic_write import atomic_replace  # noqa: E402
from state_store import show_key  # noqa: E402

# A run that comes back with fewer than this fraction of last run's shows
# is treated as a failed/partial scan, not a mass deletion.
SHRINK_GUARD_FRACTION = 0.5


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _snapshot_from_shows(shows):
    """{show_key: {"title","type","tmdb_id","episodes":[[s,e],...],
    "episode_count":N,"seasons":[...]}} - everything the diff needs."""
    snap = {}
    for s in shows:
        k = show_key(s)
        eps = sorted(
            [e["season"], e["episode"]]
            for e in s.get("entries", [])
            if e.get("season") is not None and e.get("episode") is not None
        )
        seasons = sorted({e["season"] for e in s.get("entries", []) if e.get("season") is not None})
        snap[k] = {
            "title": s.get("title"),
            "type": s.get("type"),
            "tmdb_id": s.get("tmdb_id"),
            "episodes": eps,
            "episode_count": len(s.get("entries", [])),
            "seasons": seasons,
        }
    return snap


def _removal_reason(key, old_rec, removed_eps, retention, watched_paths, shows_by_key_new):
    """purged_after_watch (intentional) vs disappeared (unexplained)."""
    if retention.get(key) == "delete_after_watch":
        return "purged_after_watch"
    # If every removed episode's path was marked watched, it reads as a
    # deliberate post-watch purge even without the retention flag. We don't
    # have paths in the snapshot, so approximate: a delete_after_watch
    # retention is the reliable signal; otherwise call it disappeared.
    return "disappeared"


def main():
    shows = _load_json(DB_PATH, None)
    if not isinstance(shows, list) or not shows:
        print("media_db.json missing or empty - ledger not updated.", file=sys.stderr)
        return

    new_snap = _snapshot_from_shows(shows)
    old_snap = _load_json(SNAPSHOT_PATH, None)
    tombstones = _load_json(TOMBSTONES_PATH, {})
    state = _load_json(USER_STATE_PATH, {})
    retention = state.get("retention", {}) or {}
    watched_paths = set((state.get("watched", {}) or {}).keys())

    now = datetime.now(timezone.utc).isoformat()
    events = []

    def emit(event, **kw):
        events.append({"at": now, "event": event, **kw})

    # ---- first run -------------------------------------------------------
    if old_snap is None:
        emit("ledger_initialized", show_count=len(new_snap))
        _write(new_snap, tombstones, events)
        print(f"Library ledger initialized: {len(new_snap)} shows recorded, no diff.")
        return

    # ---- shrink guard -------------------------------------------------
    if old_snap and len(new_snap) < SHRINK_GUARD_FRACTION * len(old_snap):
        emit("scan_shrank", old_count=len(old_snap), new_count=len(new_snap),
             note="fresh scan lost >50% of shows - treated as a failed scan, "
                  "nothing logged as removed, snapshot NOT updated")
        # append the warning but do NOT overwrite the snapshot
        _append_events(events)
        print(f"Library ledger: scan shrank {len(old_snap)} -> {len(new_snap)}, "
              "skipped (snapshot preserved).", file=sys.stderr)
        return

    old_keys = set(old_snap)
    new_keys = set(new_snap)

    # ---- added shows --------------------------------------------------
    for k in sorted(new_keys - old_keys):
        rec = new_snap[k]
        base = {"identity": k, "title": rec["title"], "type": rec["type"],
                "tmdb_id": rec["tmdb_id"], "episode_count": rec["episode_count"]}
        if k in tombstones:
            emit("show_returned", was_removed_at=tombstones[k].get("removed_at"), **base)
            tombstones.pop(k, None)
        else:
            emit("show_added", **base)

    # ---- removed shows ----------------------------------------------
    for k in sorted(old_keys - new_keys):
        rec = old_snap[k]
        reason = _removal_reason(k, rec, rec["episodes"], retention, watched_paths, new_snap)
        emit("show_removed", identity=k, title=rec["title"], type=rec["type"],
             tmdb_id=rec["tmdb_id"], episode_count=rec["episode_count"],
             seasons=rec["seasons"], reason=reason)
        tombstones[k] = {
            "identity": k, "title": rec["title"], "type": rec["type"],
            "tmdb_id": rec["tmdb_id"],
            "first_seen": (tombstones.get(k) or {}).get("first_seen") or old_snap[k].get("first_seen") or now,
            "removed_at": now, "reason": reason,
            "last_known_episode_count": rec["episode_count"],
            "last_known_seasons": rec["seasons"],
        }

    # ---- episode-level churn for shows present in both --------------
    for k in sorted(new_keys & old_keys):
        old_eps = {tuple(x) for x in old_snap[k]["episodes"]}
        new_eps = {tuple(x) for x in new_snap[k]["episodes"]}
        gained = sorted(new_eps - old_eps)
        lost = sorted(old_eps - new_eps)
        if gained:
            emit("episodes_added", identity=k, title=new_snap[k]["title"],
                 type=new_snap[k]["type"], tmdb_id=new_snap[k]["tmdb_id"],
                 episodes=[list(x) for x in gained])
        if lost:
            reason = _removal_reason(k, old_snap[k], lost, retention, watched_paths, new_snap)
            emit("episodes_removed", identity=k, title=new_snap[k]["title"],
                 type=new_snap[k]["type"], tmdb_id=new_snap[k]["tmdb_id"],
                 episodes=[list(x) for x in lost], reason=reason)

    _write(new_snap, tombstones, events)
    added = sum(1 for e in events if e["event"] in ("show_added", "show_returned"))
    removed = sum(1 for e in events if e["event"] == "show_removed")
    ep_ch = sum(1 for e in events if e["event"] in ("episodes_added", "episodes_removed"))
    print(f"Library ledger: {added} added, {removed} removed, {ep_ch} episode-change "
          f"event(s); {len(tombstones)} tombstone(s) on file.")


def _append_events(events):
    if not events:
        return
    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=True) + "\n")


def _write(new_snap, tombstones, events):
    _append_events(events)
    tmp = TOMBSTONES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tombstones, f, indent=2, ensure_ascii=True, sort_keys=True)
    atomic_replace(tmp, TOMBSTONES_PATH)
    tmp = SNAPSHOT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new_snap, f, ensure_ascii=True)
    atomic_replace(tmp, SNAPSHOT_PATH)


if __name__ == "__main__":
    main()

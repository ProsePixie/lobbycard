"""Lets a long refresh step report live progress ("412 of 567 titles") so the
loading screen can show it.

The server runs every refresh step as a subprocess and only sees its output
once the step ends, so a step that wants to be seen mid-run writes a tiny
JSON file instead (data\\refresh_step_progress.json). The server's status
endpoint reads it, ignores it unless it belongs to the step that is running
right now, and the page shows it. Running a script by hand (or from VORTEX /
daily_refresh.ps1) just writes the file harmlessly.

Best-effort on purpose: a failed write must never slow or break a refresh.
"""
import json
import os
import sys
import time

from atomic_write import atomic_replace

from app_config import DATA_DIR  # noqa: E402
_PATH = os.path.join(DATA_DIR, "refresh_step_progress.json")
_last_write = 0.0


def report(done, total, noun, note=None):
    """done of total <noun>, with an optional short note (e.g. the title being
    worked on). Throttled to ~2 writes a second; the final call (done >= total)
    always goes through."""
    global _last_write
    now = time.time()
    if done < total and now - _last_write < 0.4:
        return
    _last_write = now
    try:
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "script": os.path.basename(sys.argv[0]),
                "done": int(done), "total": int(total), "noun": noun,
                "note": (str(note)[:80] if note else None), "at": now,
            }, f)
        atomic_replace(tmp, _PATH)
    except OSError:
        pass

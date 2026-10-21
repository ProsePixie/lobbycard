"""Shared missing-episode grace-period setting - one place for the
data/settings.json read + cutoff math, instead of four independent copies
(enrich_tv_coverage.py, track_purged_shows.py, enrich_wanted.py, and
app/web_server.py's /api/settings route each had/needed their own). An
episode only counts as "missing" once it aired more than this many days
ago - avoids flagging something still realistically working its way
through downloads the instant it airs. Read fresh every call (not cached
across a pipeline run) so an Admin change takes effect on the very next
script that reads it, not just the next full refresh.
"""
import json
import os
from datetime import date, timedelta

from app_config import DATA_DIR  # noqa: E402
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
MISSING_EPISODE_GRACE_CHOICES = (7, 14, 21, 28)


def load_missing_episode_grace_days():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            val = json.load(f).get("missing_episode_grace_days", 7)
    except (OSError, ValueError):
        val = 7
    return val if val in MISSING_EPISODE_GRACE_CHOICES else 7


def missing_episode_cutoff(today=None):
    """An aired episode only counts as missing once it's <= this date -
    not merely "aired by today." today= lets a caller that already has
    date.today() pass it in rather than compute it twice; defaults to
    computing it here."""
    today = today or date.today()
    return (today - timedelta(days=load_missing_episode_grace_days())).isoformat()

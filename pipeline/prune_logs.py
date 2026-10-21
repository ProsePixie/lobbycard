"""Shared helper for pruning old per-run audit logs in logs\\ - keeps only
the most recent N runs of a given category (autofile_*.log, purge_*.log,
dup_resolve_*.log, travel_export_*.log, rename_queue_*.log) so the folder
doesn't grow without bound (2026-08-28, at the user's request - "the logs
are still a little too verbose," found to mean the sheer number of files
piling up in logs\\, not any one file's content).

Count-based, not time-based, on purpose: daily_refresh.ps1 already prunes
its own refresh_*.log by age (30 days) because that one runs on a fixed
nightly cadence, so age and count track each other. Every category here
runs on-demand, sometimes several times in one sitting (autofile alone
had 5 runs in one hour during testing today) - a day-based cutoff would
still let a single busy day leave dozens of files behind. Keeping the
last N runs bounds the folder regardless of how often a feature gets used."""
import glob
import os


def prune_logs(log_dir, prefix, keep=15):
    """Deletes all but the most recent `keep` files matching
    "<prefix>_*.log" in log_dir. Sorts by filename, not mtime - every
    caller's filenames already embed a sortable "YYYY-MM-DD_HHMMSS"
    timestamp, so a plain sort is both correct and avoids a stat() call
    per file. Failures deleting an individual file are swallowed (same
    "never let logging machinery break the real operation" reasoning as
    every log write itself already follows) - worst case a stale file
    sticks around one run longer, never a crash."""
    files = sorted(glob.glob(os.path.join(log_dir, f"{prefix}_*.log")))
    for f in files[:-keep] if keep > 0 else files:
        try:
            os.remove(f)
        except OSError:
            pass

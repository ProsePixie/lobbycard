"""The fast-subset refresh pipeline - `scan_media` through `consolidate`,
plus a cheap local `reconcile_missing_episodes` pass, skipping the slow
per-season TMDB completeness checks, the Wikipedia/epguides passes,
`detect_runtime_mismatch`, and the plan-builders. This is what the server
already runs automatically on every startup (a fresh boot, the household
shortcut) just to pick up new downloads quickly.

Pulled out of app/web_server.py into its own dependency-free module
so a CLI caller - `pipeline/file_queue.py --refresh`, which VORTEX
subprocesses - can run the exact same subset without importing Flask and
the whole server. One list, two callers.
"""
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Order matters - heal_reorg_state.py MUST run before scan_media.py (it
# carries watched/retention/travel state forward for anything renamed
# since the last scan, before scan_media's fresh results get pruned
# against). Kept in sync with automation/daily_refresh.ps1's own $Scripts,
# same deliberate duplication that file already has for the full list.
QUICK_SCAN_SCRIPTS = [
    "pipeline/heal_reorg_state.py",
    "pipeline/scan_media.py",
    "pipeline/enrich_tmdb.py",
    "pipeline/consolidate.py",
    # No TMDB calls - just drops missing_episodes/missing_seasons entries
    # whose file is now on disk, so a freshly-downloaded episode stops
    # showing "1 ep missing" without waiting for the next full
    # enrich_tv_coverage.py run. Must come after consolidate.py (needs the
    # merged entries[]). Not in the full pipeline / daily_refresh.ps1 -
    # enrich_tv_coverage.py recomputes both lists authoritatively there.
    "pipeline/reconcile_missing_episodes.py",
    # Followed Netflix DE / Prime DE titles -> synthetic library entries.
    # TMDB fetches cached forever, so an unchanged seed is a few file reads;
    # cheap enough for the startup path so a just-followed title shows up
    # without waiting for the full nightly refresh. Also in PIPELINE_SCRIPTS.
    "pipeline/enrich_streaming.py",
    # MUST run before this function's caller falls through to prune_state()
    # (app/web_server.py's run_refresh_pipeline() calls it unconditionally,
    # regardless of which script list ran) - without this, purging the
    # LAST file of a delete_after_watch show goes: scan_media.py drops it
    # from media_db.json (zero files left) -> prune_state sees its key
    # missing from this run's results -> wipes its retention entry on the
    # spot, permanently, since nothing ever rechecks a show once retention
    # for it is gone. Confirmed real, not hypothetical (2026-09-13,
    # Deadliest Catch - watched, deleted, vanished from Recently Watched
    # and never got its "still following, next season TBD" placeholder).
    # Only costs a TMDB call for whatever's ACTUALLY currently zero-owned +
    # delete_after_watch (a small, cached-forever subset), not the whole
    # library - cheap enough for the quick-scan path this same reasoning
    # already applies enrich_streaming.py above.
    "pipeline/track_purged_shows.py",
]


def run_quick_scan(log=None, python_exe=None):
    """Runs QUICK_SCAN_SCRIPTS in order, aborting on the first failure
    (same "stop on first bad script" contract as web_server.py's own
    run_refresh_pipeline). `log`, if given, is a list each script's
    one-line result gets appended to. Returns (ok, failing_script_or_None).
    """
    python_exe = python_exe or sys.executable
    for rel in QUICK_SCAN_SCRIPTS:
        script = os.path.join(PROJECT_ROOT, rel)
        proc = subprocess.run(
            [python_exe, script],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=600,
        )
        line = f"{rel}: exit {proc.returncode}"
        if proc.returncode != 0:
            line += f"  STDERR: {(proc.stderr or '').strip()[-500:]}"
        if log is not None:
            log.append(line)
        if proc.returncode != 0:
            return False, rel
    return True, None

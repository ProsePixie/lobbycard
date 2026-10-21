"""Runs automatically as the FIRST step of every pipeline refresh (both
the fast startup scan and the full nightly chain - see app\\web_server.py's
STARTUP_SCRIPTS/PIPELINE_SCRIPTS) - sweeps every reorg audit log under
logs\\reorg\\*.jsonl written since the last sweep and carries forward any
watched/retention_overrides/travel_selection entry still sitting under
its old path (see plans\\migrate_state_paths.py for the actual mechanism
and why this exists at all).

Why this needs to be a permanent, universal pipeline step rather than a
call bolted onto one reorg script: by the time this problem was first
found and fixed (2026-08-20, wired directly into build_reorg_manifest.py's
generated .ps1), several OTHER independent rename mechanisms had already
sprung up in this same reorg project - build_movie_reorg_manifest.py,
leftover-folder sweeps, and hand-written one-off fix scripts
(simpsons_2405_manual_fix, revival_movies_folder_cleanup, etc.) - not all
of which reliably called the migration helper themselves. Confirmed real:
the user hit the exact same symptom again on 2026-08-21 despite the
original fix working correctly for the one script it was wired into.

Every rename mechanism observed so far independently writes the same
{old_path, new_path, ...} JSON-lines shape to logs\\reorg\\ - it's already
the de facto standard, not something invented here - so sweeping every
*.jsonl file there, regardless of which script wrote it or whether that
script remembered to call migrate_state_paths.py itself, closes this
class of bug for good instead of chasing one script at a time.

MUST run before scan_media.py (and therefore before prune_state(), which
run_refresh_pipeline() in app\\web_server.py calls at the end of every
refresh): prune_state() deletes any watched/retention_override/travel
entry whose path isn't in that scan's fresh known_paths, so an orphaned
old-path entry that hasn't been migrated yet is gone for good the moment
that runs. This script's only job is making sure that never happens
un-migrated - hence first in both script lists, not last.

Checkpointed (data\\reorg_state_heal_checkpoint.json) so a growing
logs\\reorg\\ directory doesn't get re-swept from scratch on every single
refresh - only genuinely new audit logs since the last successful sweep
are processed. Safe to delete that checkpoint file any time (just costs
one full re-sweep, which is itself idempotent/harmless - see
migrate_state_paths.py's own docstring)."""
import glob
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
from app_config import DATA_DIR  # noqa: E402
LOG_DIR = os.path.join(PROJECT_ROOT, "logs", "reorg")
CHECKPOINT_PATH = os.path.join(DATA_DIR, "reorg_state_heal_checkpoint.json")

sys.path.insert(0, os.path.join(PROJECT_ROOT, "plans"))
from migrate_state_paths import load_moves, migrate  # noqa: E402


def load_checkpoint():
    if not os.path.exists(CHECKPOINT_PATH):
        return set()
    try:
        with open(CHECKPOINT_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_checkpoint(processed):
    tmp = CHECKPOINT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(processed), f, indent=2)
    os.replace(tmp, CHECKPOINT_PATH)


def main():
    if not os.path.isdir(LOG_DIR):
        print("No logs/reorg directory yet - nothing to heal.")
        return

    processed = load_checkpoint()
    all_logs = {os.path.basename(p) for p in glob.glob(os.path.join(LOG_DIR, "*.jsonl"))}
    new_logs = sorted(all_logs - processed)
    if not new_logs:
        print("No new reorg audit logs since the last heal pass.")
        return

    paths = [os.path.join(LOG_DIR, name) for name in new_logs]
    moves = load_moves(paths)
    counts = migrate(moves)
    processed |= set(new_logs)
    save_checkpoint(processed)

    print(f"Healed state across {len(new_logs)} new audit log(s), {len(moves)} move(s):")
    print(f"  watched entries carried forward:             {counts['watched']}")
    print(f"  retention_overrides entries carried forward:  {counts['retention_overrides']}")
    print(f"  travel_selection entries carried forward:     {counts['travel_selection']}")


if __name__ == "__main__":
    main()

"""Carries per-path entries in data\\user_state.json (watched,
retention_overrides, travel_selection) forward from old_path to new_path
after a reorg rename, using the old_path/new_path pairs every
reorg_plan_*.ps1 already records in logs\\reorg\\*.jsonl for each file it
moves.

Root cause this closes: app\\render_engine.py's resolve_watched() falls
back to `root == "Archive"` (watched=True) whenever a path has no explicit
state record - correct for genuinely-new archive content, but wrong for a
file that WAS being tracked (e.g. legitimately unwatched) and just got
renamed by the reorg tooling, which only ever calls Move-Item and never
touches user_state.json. The old path's record goes orphaned, nothing
exists yet under the new path, and the Archive default silently flips it
to watched. Confirmed real 2026-08-20: previously-unwatched episodes
showed Watched immediately after a reorg pass, no user action involved.

Safe to re-run: only moves a key if it's still sitting under its old_path
(so a file already fixed by hand, or with no state to begin with, is a
no-op) and old_path==new_path pairs are skipped outright.

Usage:
  # called automatically at the end of every generated reorg_plan_*.ps1:
  python migrate_state_paths.py --audit-log logs\\reorg\\reorg_audit_<ts>.jsonl

  # one-time recovery sweep over every audit log ever written:
  python migrate_state_paths.py --all-audit-logs
"""
import argparse
import glob
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))
from state_store import update_state

LOG_DIR = os.path.join(PROJECT_ROOT, "logs", "reorg")


def load_moves(audit_log_paths):
    moves = []
    for path in audit_log_paths:
        # utf-8-sig: some audit logs carry a BOM (written by PowerShell's
        # Add-Content -Encoding utf8), some don't - utf-8-sig reads both.
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                old_path, new_path = entry.get("old_path"), entry.get("new_path")
                if old_path and new_path:
                    moves.append((old_path, new_path))
    return moves


def migrate(moves):
    counts = {"watched": 0, "retention_overrides": 0, "travel_selection": 0}

    def mutator(state):
        watched = state.setdefault("watched", {})
        overrides = state.setdefault("retention_overrides", {})
        travel = state.setdefault("travel_selection", [])
        for old_path, new_path in moves:
            if old_path == new_path:
                continue
            if old_path in watched:
                watched[new_path] = watched.pop(old_path)
                counts["watched"] += 1
            if old_path in overrides:
                overrides[new_path] = overrides.pop(old_path)
                counts["retention_overrides"] += 1
            if old_path in travel:
                travel[travel.index(old_path)] = new_path
                counts["travel_selection"] += 1

    update_state(mutator)
    return counts


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--audit-log", help="Path to one *.jsonl audit log (the one just written by this reorg run)")
    group.add_argument("--all-audit-logs", action="store_true",
                        help="Sweep every audit log under logs\\reorg\\ - safe to re-run")
    args = parser.parse_args()

    if args.all_audit_logs:
        paths = sorted(glob.glob(os.path.join(LOG_DIR, "*.jsonl")))
    else:
        paths = [args.audit_log]

    moves = load_moves(paths)
    counts = migrate(moves)
    print(f"Checked {len(moves)} move(s) from {len(paths)} audit log(s).")
    print(f"  watched entries carried forward:             {counts['watched']}")
    print(f"  retention_overrides entries carried forward:  {counts['retention_overrides']}")
    print(f"  travel_selection entries carried forward:     {counts['travel_selection']}")


if __name__ == "__main__":
    main()

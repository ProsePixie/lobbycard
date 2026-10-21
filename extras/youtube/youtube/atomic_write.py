"""Atomic-replace helper - the same small function the host app uses, copied so
this package imports nothing from it. Every JSON write in this package goes
tmp-file-then-atomic_replace.

Original docstring:

    One shared atomic-replace helper for every module here that rewrites a
    data file readers poll concurrently.

    2026-08-28: the host app hit a real PermissionError crashing their own refresh —
    traced to a sibling app briefly holding a data file open for a plain read
    at the exact instant of a rename. The same Windows behavior applies here:
    this package runs inside the host app's process and writes files a poll thread
    and request handlers both touch.
"""
import os
import time


def atomic_replace(tmp_path, dest_path, attempts=5, base_delay=0.5):
    """Renames tmp_path over dest_path, retrying a transient Windows
    PermissionError (another thread/process briefly holding dest_path open
    for reading at that exact instant) with a short backoff instead of
    crashing. Raises on the final attempt if the lock genuinely never
    clears — never silently swallowed."""
    for attempt in range(attempts):
        try:
            os.replace(tmp_path, dest_path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(base_delay * (attempt + 1))

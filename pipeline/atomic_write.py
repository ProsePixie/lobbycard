"""One shared atomic-replace helper for every pipeline/plans script that
rewrites a shared data file (media_db.json, user_state.json, etc.) - the
tmp-file + os.replace() pattern itself was already correctly duplicated
across ~10 scripts (scan_media.py, consolidate.py, every enrich_*.py,
build_system_status.py, state_store.py...), but the retry-on-collision fix
below was not, until this module existed - and that gap bit for real:
scan_media.py got the fix first (2026-08-28, after a live PermissionError
traced to the companion tool's console.py holding a momentary read handle on
media_db.json at the exact instant of the rename), then consolidate.py -
a completely separate script, writing the same file the same way -
hit the identical collision minutes later, because the fix only lived in
one of the ~10 places that needed it. Exactly the class of drift this
project already got burned by once today (scan_media.py/consolidate.py's
duplicate-detection logic) - a shared function instead of a tenth
copy-paste closes it everywhere at once, not just wherever it was noticed
last."""
import os
import time


def atomic_replace(tmp_path, dest_path, attempts=5, base_delay=0.5):
    """Renames tmp_path over dest_path, retrying a transient Windows
    PermissionError (another process - confirmed real: the companion tool's
    console.py, not a stale/duplicate copy of this project's own server -
    briefly holding dest_path open for reading at that exact instant) with
    a short backoff instead of crashing the whole pipeline run over
    another well-behaved app's read finishing a beat late. Raises on the
    final attempt if the lock genuinely never clears - never silently
    swallowed."""
    for attempt in range(attempts):
        try:
            os.replace(tmp_path, dest_path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(base_delay * (attempt + 1))

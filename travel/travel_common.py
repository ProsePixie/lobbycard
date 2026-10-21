"""Shared travel-drive path convention (plan item 12, 2026-09-04). Single
source of truth for "where does this entry live on the travel drive" -
called identically by plans\\build_travel_plan.py (the copy planner) and
app\\render_engine.py's travel-mode runtime link construction, so a copied
file and a link pointing at it always agree regardless of the travel
drive's assigned letter.

The drive's Media\\ is a straight MIRROR of the home archive (<library>/):
an entry already filed there keeps its exact Season NN\\<canonical name>
shape, just rooted under Media\\ instead of <library>/ - no renaming, no
re-derivation, so the vast majority of entries (anything root == "Archive")
resolve with zero TMDB calls. An entry that ISN'T archived yet (sitting in
the incoming folder, still queued) gets the placement autofile_naming would
give it once it IS archived, reusing pipeline\\file_queue.py's own
TMDB-verified planning (never guessed) - so a queue item taken on a trip
already lands in its correct future home-library slot instead of a
throwaway shape the later import would have to re-derive.
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
for _sub in ("pipeline", "app", "plans"):
    _p = os.path.join(PROJECT_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app_config import ARCHIVE_ROOT  # noqa: E402


def safe_folder_name(name):
    for ch in '<>:"/\\|?*':
        name = (name or "Other").replace(ch, "_")
    return name.strip()


def travel_dest_relpath(category, title, basename):
    """Legacy flat convention (Media/{Category}/{Show}/{basename}) - the
    fallback when an entry is neither under ARCHIVE_ROOT nor resolvable via
    the queue-synthesis path below (no file, or TMDB couldn't confirm it).
    Kept so every entry gets SOME deterministic slot rather than failing
    the whole export over one unresolvable item."""
    cat = safe_folder_name(category or "Other")
    show = safe_folder_name(title)
    return f"Media/{cat}/{show}/{basename}"


def is_under_archive(path):
    try:
        a = os.path.normcase(os.path.abspath(path))
        root = os.path.normcase(os.path.abspath(ARCHIVE_ROOT))
        return a == root or a.startswith(root + os.sep)
    except (OSError, ValueError):
        return False


def _mirror_relpath(path):
    rel = os.path.relpath(path, ARCHIVE_ROOT)
    return "Media/" + rel.replace("\\", "/")


def _synthesize_queue_relpath(show, entry):
    """Where autofile_naming would place this NOT-yet-archived entry once
    it IS filed - reuses file_queue._plan_entry() directly (TMDB-verified
    naming, existing-show-folder preference, quality-tagged resolution)
    rather than a second re-implementation of the same rules. Returns None
    if it can't be confirmed (never guessed) - caller falls back to
    travel_dest_relpath()."""
    try:
        import file_queue  # pipeline/file_queue.py
    except ImportError:
        return None
    try:
        item, _reason = file_queue._plan_entry(show, entry, show.get("retention") or "archive")
    except Exception:
        # Any planning failure (network hiccup, malformed entry, etc.) just
        # falls back to the flat convention below - never blocks the export.
        return None
    if not item:
        return None
    return _mirror_relpath(item["dest_full"])


def travel_rel_path(show, entry):
    """The canonical Media\\... path (forward slashes) this entry occupies
    on the travel drive. Single call site for both build_travel_plan.py
    (what to copy where) and render_engine.py (what URL a travel-mode
    entry's Open/Play buttons resolve to)."""
    path = entry.get("path") or ""
    if path and os.path.exists(path):
        if is_under_archive(path):
            return _mirror_relpath(path)
        synthesized = _synthesize_queue_relpath(show, entry)
        if synthesized:
            return synthesized
    base = os.path.basename(path.rstrip("\\/")) if path else (entry.get("name") or "unknown")
    category = (show.get("archive_categories") or [None])[0] or show.get("type")
    return travel_dest_relpath(category, show.get("title"), base)

"""
Movie filename/folder reorganization - manifest generator. Same
manifest-then-review-then-execute workflow as build_reorg_manifest.py
(TV), but for Movie-typed content: no season/episode, one destination per
movie identity. Naming convention per generated\\naming_convention_spec.md
Section 4 (Plex standard):

  <library>/{category}\\Movie Name (Year)\\Movie Name (Year).RESOLUTION.ext

- "Movie Name" and "Year" come from TMDB (tmdb_name + the year parsed from
  tmdb_release_date), never from the original filename.
- RESOLUTION is extracted from the original filename (2160p/1080p/720p/480p,
  highest rank wins if multiple present), omitted entirely if none found -
  never a placeholder.
- Unlike the TV filename convention, the movie filename is NOT dot-tokenized
  - it reuses the exact same "Movie Name (Year)" text as the folder name,
  per the spec's own example.

Category defaults to "Movies" but any category can be passed via
--category - the SAME in-place-only rule from the original Movies-only
pass still applies (naming_convention_spec.md Section 6): a movie already
sitting in Comedy or Documentaries gets renamed to the Plex convention
right where it is, never relocated into Movies proper just because TMDB
types it "Movie". Added 2026-08-21 once the user confirmed they wanted
Comedy/Documentaries specials renamed in place too, not left in scene-
release form forever.

Processes every TMDB-matched Movie-typed entry belonging to the target
category in one pass and writes three review artifacts per category (so
running against Comedy doesn't clobber a still-open Documentaries run):

  movie_reorg_manifest_<category>.json  - full structured manifest (moves + flagged + collisions)
  movie_reorg_manifest_<category>.txt   - human-readable summary for review
  movie_reorg_plan_<category>.ps1       - the actual Move-Item commands, review-then-run

Nothing on the library is touched by this script. A movie whose TMDB match,
release year, or destination can't be confidently resolved is left out of
the moves list and reported under "flagged" instead - never guessed.

Usage:
  python build_movie_reorg_manifest.py                  # Movies (default)
  python build_movie_reorg_manifest.py --category Comedy
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
from app_config import DATA_DIR  # noqa: E402
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs", "reorg")
DB_PATH = os.path.join(DATA_DIR, "media_db.json")

sys.path.insert(0, os.path.join(PROJECT_ROOT, "pipeline"))
from scan_media import VIDEO_EXTENSIONS, quality_score  # noqa: E402

sys.path.insert(0, SCRIPT_DIR)
from build_reorg_manifest import (  # noqa: E402
    ARCHIVE_ROOT, SUBTITLE_EXTENSIONS,
    sanitize_folder_name, extract_resolution_tag, find_video_file, find_sidecars,
)


def movie_folder_name(tmdb_name, year):
    return sanitize_folder_name(f"{tmdb_name} ({year})")


# Categories large enough to warrant single-letter subfolders instead of
# one flat directory - App project's recommendation, 2026-08-21 (see
# Reorg_Session_Insights.md), based on real counts: Movies had 318 items
# sitting flat, everything else (Series 137, Documentaries 24, Music 12,
# Mini-Series 7, Comedy 6) was small enough to stay flat. Add a category
# here only once it's grown enough to be worth the same treatment.
BUCKET_CATEGORIES = {"Movies"}

ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
BUCKET_LETTERS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ#")


def bucket_for(name):
    """Single-letter bucket (or '#' for anything not A-Z), leading articles
    stripped for alphabetization only - "The Hunger Games" buckets under H,
    the folder name itself is untouched. Must stay in exact sync with the
    one-time restructure this was extracted from (see
    Reorg_Session_Insights.md, 2026-08-21) - this is now the single source
    of truth, nothing should reimplement the bucket rule separately."""
    stripped = ARTICLE_RE.sub("", name).strip() or name
    first = stripped[0].upper()
    return first if first.isalpha() and first in BUCKET_LETTERS else "#"


def same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def build_manifest(shows, category):
    category_root = os.path.join(ARCHIVE_ROOT, category)
    movies = [
        s for s in shows
        if s["type"] == "Movie" and s.get("tmdb_matched") and s.get("tmdb_id")
    ]

    moves = []
    flagged = []
    already_correct = []
    collisions = []
    skipped_unmatched = sum(1 for s in shows if s["type"] == "Movie" and not s.get("tmdb_matched"))

    for movie in movies:
        tmdb_id = movie["tmdb_id"]
        tmdb_name = movie.get("tmdb_name") or movie["title"]
        release_date = movie.get("tmdb_release_date")
        year = release_date[:4] if release_date and len(release_date) >= 4 and release_date[:4].isdigit() else None

        # Only touch files ALREADY sitting under <library>/{category} -
        # category placement is preserved, never re-guessed, exactly like
        # the TV script's rule (naming_convention_spec.md Section 6). TMDB
        # typing something "Movie" is not license to relocate it: this
        # archive deliberately keeps stand-up specials under Comedy,
        # documentaries under Documentaries, concert films under Music, and
        # TV-adjacent specials inside their show's own Series folder as
        # siblings of Movies, not subfolders of it. Confirmed real and
        # dangerous to skip this filter: without it, an early version of
        # this script would have force-moved "PartsUnknown.mkv" using its
        # ALREADY-KNOWN-WRONG movie match (tmdb_id 537445) out of
        # Series\Anthony Bourdain, even though that file was still an open,
        # explicitly-flagged question about which TV episode it actually is.
        archived_entries = [
            e for e in movie["entries"]
            if e["root"] == "Archive" and e["path"].lower().startswith(category_root.lower())
        ]
        entries_outside_category = [
            e for e in movie["entries"]
            if e["root"] == "Archive" and not e["path"].lower().startswith(category_root.lower())
        ]
        for e in entries_outside_category:
            flagged.append({
                "old_path": e["path"], "title": movie["title"], "tmdb_id": tmdb_id,
                "reason": f"TMDB matches this as a Movie ('{tmdb_name}'), but it's currently filed outside <library>/{category} - category placement is preserved, not re-guessed, so this was left exactly where it is.",
            })
        if not archived_entries:
            continue

        # Multi-part disc titles (pipeline/scan_media.py's
        # process_video_ts_folder(), playlist_extra_paths) don't fit this
        # script's "one video file = one movie" model at all - it only
        # ever finds/moves a SINGLE video_path per entry, so a DVD split
        # across several .VOB parts would get "renamed" as just its first
        # part while every other real part is silently abandoned in the
        # old folder. Worse, a genuinely separate second title on the same
        # disc (its own VTS index, no playlist_extra_paths of its own) can
        # look like a lower-quality "duplicate" of the main title to the
        # collision logic below and get mislabeled as superseded, when it's
        # actually different content entirely. Confirmed real and almost
        # executed, 2026-08-21: both Nine Inch Nails DVDs (Another Version
        # of the Truth - 6 real .VOB parts, only 1 would have moved; The
        # Downward Spiral Live - a real second DVD title wrongly flagged as
        # a duplicate of the first). Flag the WHOLE movie identity and skip
        # it entirely rather than attempt a partial, misleading move -
        # proper multi-part handling isn't built into this script yet.
        if any(e.get("playlist_extra_paths") for e in archived_entries):
            for e in archived_entries:
                flagged.append({
                    "old_path": e["path"], "title": movie["title"], "tmdb_id": tmdb_id,
                    "reason": f"'{tmdb_name}' is a multi-part disc title (DVD VIDEO_TS structure) - this script only handles single-file movies and would silently abandon or mislabel the other real parts, so the whole title was left untouched. Needs dedicated multi-part handling, not built yet.",
                })
            continue

        if year is None:
            for e in archived_entries:
                flagged.append({
                    "old_path": e["path"], "title": movie["title"], "tmdb_id": tmdb_id,
                    "reason": f"'{tmdb_name}' (tmdb_id {tmdb_id}) has no usable TMDB release year (tmdb_release_date={release_date!r}) - can't build the '(Year)' folder name without guessing.",
                })
            continue

        folder_name = movie_folder_name(tmdb_name, year)
        if category in BUCKET_CATEGORIES:
            movie_root = os.path.join(category_root, bucket_for(folder_name), folder_name)
        else:
            movie_root = os.path.join(category_root, folder_name)

        candidates = []
        for e in archived_entries:
            video_path = find_video_file(e)
            if video_path is None or not os.path.isfile(video_path):
                flagged.append({
                    "old_path": e["path"], "title": movie["title"], "tmdb_id": tmdb_id,
                    "reason": "no video file found at this entry's location (moved/deleted since last scan?)",
                })
                continue
            candidates.append({"entry": e, "video_path": video_path, "original_name": e["name"]})

        if not candidates:
            continue

        def compute_new_path(candidate):
            video_path = candidate["video_path"]
            ext = os.path.splitext(video_path)[1]
            # Check both the release-folder name AND the actual video
            # filename - a movie already sitting in a clean "Title (Year)"
            # folder (from an earlier informal rename) can have the
            # resolution tag survive only in the file's own basename, not
            # the folder/release name extract_resolution_tag() normally
            # reads (confirmed real: "Edge of Tomorrow (2014)\Edge of
            # Tomorrow (2014).1080p.mkv" - folder name alone has no "1080p").
            res_tag = extract_resolution_tag(
                candidate["original_name"] + " " + os.path.basename(video_path)
            )
            base = movie_folder_name(tmdb_name, year)
            new_filename = f"{base}.{res_tag}{ext}" if res_tag else f"{base}{ext}"
            return os.path.join(movie_root, new_filename)

        # Never overwrite a file already sitting at the destination from an
        # earlier run - same rule as the TV script (see build_reorg_manifest.py).
        already_placed = None
        for c in candidates:
            if same_path(c["video_path"], compute_new_path(c)):
                already_placed = c
                break

        if already_placed is not None:
            already_correct.append({
                "path": already_placed["video_path"], "title": movie["title"], "tmdb_id": tmdb_id,
            })
            for c in candidates:
                if c is already_placed:
                    continue
                flagged.append({
                    "old_path": c["video_path"], "title": movie["title"], "tmdb_id": tmdb_id,
                    "reason": f"'{tmdb_name}' is already correctly filed at the destination (from an earlier run) - this is a leftover duplicate, left in place untouched.",
                })
            continue

        def quality_key(c):
            return quality_score(c["original_name"] + " " + os.path.basename(c["video_path"]))

        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            ranked = sorted(
                candidates,
                key=lambda c: (quality_key(c), c["entry"]["size_bytes"]),
                reverse=True,
            )
            chosen = ranked[0]
            losers = ranked[1:]
            collisions.append({
                "title": movie["title"], "tmdb_id": tmdb_id,
                "kept": chosen["video_path"], "kept_quality": quality_key(chosen),
                "superseded": [
                    {"path": c["video_path"], "quality": quality_key(c)} for c in losers
                ],
            })
            for c in losers:
                flagged.append({
                    "old_path": c["video_path"], "title": movie["title"], "tmdb_id": tmdb_id,
                    "reason": f"duplicate of '{tmdb_name}' - lower quality ({quality_key(c)}) than the kept copy, not moved (left in place, not deleted).",
                })

        new_path = compute_new_path(chosen)

        if os.path.exists(new_path) and not same_path(chosen["video_path"], new_path):
            flagged.append({
                "old_path": chosen["video_path"], "title": movie["title"], "tmdb_id": tmdb_id,
                "reason": f"computed destination ({new_path}) already exists and is a DIFFERENT file than any candidate found for this movie - needs manual resolution, not touched.",
            })
            continue

        video_path = chosen["video_path"]
        new_dir = os.path.dirname(new_path)
        new_stem = os.path.splitext(os.path.basename(new_path))[0]

        sidecars = []
        for sc_path in find_sidecars(video_path):
            sc_ext = os.path.splitext(sc_path)[1]
            sidecars.append({"old_path": sc_path, "new_path": os.path.join(new_dir, new_stem + sc_ext)})

        moves.append({
            "title": movie["title"],
            "tmdb_id": tmdb_id,
            "tmdb_name": tmdb_name,
            "year": year,
            "old_path": video_path,
            "new_path": new_path,
            "size_bytes": chosen["entry"]["size_bytes"],
            "sidecars": sidecars,
            "source_release_folder": chosen["entry"]["path"] if chosen["entry"]["path"] != video_path else None,
        })

    moves.sort(key=lambda m: m["tmdb_name"].lower())

    return {
        "category": category,
        "generated_at": datetime.now().isoformat(),
        "movies_considered": len(movies),
        "movies_skipped_unmatched": skipped_unmatched,
        "moves": moves,
        "flagged": flagged,
        "already_correct": already_correct,
        "collisions": collisions,
    }


def write_outputs(manifest):
    category = manifest["category"]
    json_path = os.path.join(GENERATED_DIR, f"movie_reorg_manifest_{category}.json")
    txt_path = os.path.join(GENERATED_DIR, f"movie_reorg_manifest_{category}.txt")
    ps1_path = os.path.join(GENERATED_DIR, f"movie_reorg_plan_{category}.ps1")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    lines = [
        f"Movie reorg manifest - {category} pass",
        f"Generated: {manifest['generated_at']}",
        f"Movies considered (TMDB-matched): {manifest['movies_considered']}    Skipped (unmatched, no TMDB id): {manifest['movies_skipped_unmatched']}",
        f"Confident moves: {len(manifest['moves'])}    Already correctly filed: {len(manifest['already_correct'])}    Flagged (not touched): {len(manifest['flagged'])}    Collisions resolved: {len(manifest['collisions'])}",
        "",
        "=== MOVES (will be renamed/relocated if you approve) ===",
    ]
    for m in manifest["moves"]:
        lines.append(f"  \"{m['tmdb_name']}\" ({m['year']})")
        lines.append(f"    FROM: {m['old_path']}")
        lines.append(f"    TO:   {m['new_path']}")
        for sc in m["sidecars"]:
            lines.append(f"    + sidecar: {os.path.basename(sc['old_path'])} -> {os.path.basename(sc['new_path'])}")
        lines.append("")

    if manifest["already_correct"]:
        lines.append(f"=== ALREADY CORRECTLY FILED - {len(manifest['already_correct'])} movie(s), no action needed ===")
        lines.append("")

    if manifest["collisions"]:
        lines.append("=== COLLISIONS (same movie, multiple files - lower quality left in place, NOT deleted) ===")
        for c in manifest["collisions"]:
            lines.append(f"  \"{c['title']}\": kept {c['kept']} (quality {c['kept_quality']})")
            for s in c["superseded"]:
                lines.append(f"    superseded, left alone: {s['path']} (quality {s['quality']})")
        lines.append("")

    if manifest["flagged"]:
        lines.append("=== FLAGGED - left untouched, needs your eyes ===")
        for item in manifest["flagged"]:
            lines.append(f"  {item['old_path']}")
            lines.append(f"    -> {item['reason']}")
        lines.append("")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_log = os.path.join(LOG_DIR, f"movie_reorg_audit_{run_ts}.jsonl")

    ps1_lines = [
        "# Movie reorg execution plan - generated by build_movie_reorg_manifest.py",
        f"# Generated: {manifest['generated_at']}",
        "# REVIEW EVERY LINE BEFORE RUNNING - this moves real files on the library.",
        f"# Every successful move is appended to: {audit_log}",
        "",
        "$ErrorActionPreference = 'Stop'",
        f'$AuditLog = "{audit_log}"',
        "New-Item -ItemType Directory -Force -Path (Split-Path $AuditLog) | Out-Null",
        "",
        "function Write-AuditEntry {",
        "    param($OldPath, $NewPath, $Bytes)",
        "    $entry = [ordered]@{",
        "        timestamp = (Get-Date).ToString('o')",
        "        old_path = $OldPath",
        "        new_path = $NewPath",
        "        size_bytes = $Bytes",
        "    }",
        "    ($entry | ConvertTo-Json -Compress) | Add-Content -LiteralPath $AuditLog -Encoding utf8",
        "}",
        "",
    ]

    for m in manifest["moves"]:
        new_dir = os.path.dirname(m["new_path"])
        ps1_lines.append(f"# --- {m['tmdb_name']} ({m['year']}) ---")
        ps1_lines.append(f'New-Item -ItemType Directory -Force -Path "{new_dir}" | Out-Null')
        ps1_lines.append(f'Move-Item -LiteralPath "{m["old_path"]}" -Destination "{m["new_path"]}" -Force')
        ps1_lines.append(f'Write-AuditEntry -OldPath "{m["old_path"]}" -NewPath "{m["new_path"]}" -Bytes {m["size_bytes"]}')
        for sc in m["sidecars"]:
            ps1_lines.append(f'Move-Item -LiteralPath "{sc["old_path"]}" -Destination "{sc["new_path"]}" -Force')
            ps1_lines.append(f'Write-AuditEntry -OldPath "{sc["old_path"]}" -NewPath "{sc["new_path"]}" -Bytes 0')
        ps1_lines.append("")

    expected = len(manifest["moves"])
    category_root = os.path.join(ARCHIVE_ROOT, category)
    ps1_lines += [
        "# --- Post-move verification ---",
        f"$Expected = {expected}",
        f'$Actual = (Get-ChildItem -LiteralPath "{category_root}" -Recurse -File | Where-Object {{ $_.Extension -notin @(".nfo") }} | Measure-Object).Count',
        f'Write-Host "Post-move file count under {category}: $Actual (this run moved $Expected file(s))."',
        "",
        "# --- Carry watched/retention/travel state forward to the new paths ---",
        f'python "{os.path.join(SCRIPT_DIR, "migrate_state_paths.py")}" --audit-log $AuditLog',
    ]

    with open(ps1_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(ps1_lines))

    return json_path, txt_path, ps1_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default="Movies", help="Category to process in place (default: Movies)")
    args = parser.parse_args()

    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)

    manifest = build_manifest(shows, args.category)
    json_path, txt_path, ps1_path = write_outputs(manifest)

    print(f"Movie reorg manifest ({args.category}) - {manifest['movies_considered']} TMDB-matched movies considered ({manifest['movies_skipped_unmatched']} unmatched, skipped):")
    print(f"  Confident moves:         {len(manifest['moves'])}")
    print(f"  Already correctly filed: {len(manifest['already_correct'])}")
    print(f"  Collisions resolved:     {len(manifest['collisions'])}")
    print(f"  Flagged / untouched:     {len(manifest['flagged'])}")
    print()
    print(f"Wrote:\n  {json_path}\n  {txt_path}\n  {ps1_path}")
    print("\nNothing on the library has been touched. Review the .txt summary, then the .ps1, before running anything.")


if __name__ == "__main__":
    main()

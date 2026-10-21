"""Shared HTML/CSS/JS template used by all three render_page() call sites -
app\\web_server.py (server_mode=True, the live app), app\\generate_html.py
(the static snapshot), and plans\\build_travel_plan.py (travel_mode=True,
the portable drive app). One template, one place to edit for any UI change.
Owns the client-side filter/sort/search pipeline, the modal/card renderers,
and ~45 other JS functions embedded in HTML_TEMPLATE - the single largest,
most load-bearing file in this codebase."""
import base64
import glob
import json
import re
from datetime import datetime
from urllib.parse import quote

import os
import sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "travel"))
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))

import app_config
import branding
from state_store import RECENTLY_WATCHED_CAP, show_key
from travel_common import travel_rel_path

# Loaded once at import time (small, static, local files) and embedded as
# base64 data: URIs directly in the page - keeps render_page()'s three call
# sites (server, static snapshot, travel export) all fully self-contained
# with zero extra file-copying or Flask static-route logic, same approach
# already proven by the TMDB-backdrop hero (roadmap item 23).
def _js_json(value):
    """json.dumps that is safe to drop inside a <script> block: no raw <, > or & can appear,
    so data such as a file name containing </script> cannot close the tag."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _load_b64(filename):
    path = os.path.join(ASSETS_DIR, filename)
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


ORBITRON_B64 = _load_b64("orbitron-900.woff2")
# Name, art and flavour text come from the active pack (see pipeline/branding.py).
FAVICON_URI = branding.asset_uri("favicon")
YOUTUBE_BANNER_URI = branding.asset_uri("youtube-banner")  # header art while the YouTube block is on
# The loading overlay's background: a dark wide shot with its focal point right of
# centre, so the CSS keeps it right-anchored (a centred `cover` crop would push the
# focal point off-frame on a narrow phone viewport).
BOOT_OVERLAY_URI = branding.asset_uri("boot")

# Franchise mode: an optional, user-defined ordered list of titles (a film/series
# universe, a saga) with its own look. None when the pack defines none.
FRANCHISE = branding.franchise()
FRANCHISE_ICON_URI = branding.asset_uri("franchise-icon")
FRANCHISE_BANNER_URI = branding.asset_uri("franchise-banner")

# Loading-screen filler text, shown bottom-left on the blocking overlay and rotated
# while it is up. Flattened into one pool client-side; the category keys only pick
# the small caption above each line.
LOADING_HINTS = branding.hints()
LOADING_HINTS_JSON = _js_json(LOADING_HINTS)

# The loading overlay's own CSS / JS live in assets (plain files, so they can be
# syntax-checked on their own) and are dropped into the page below.
with open(os.path.join(ASSETS_DIR, "boot_overlay.css"), "r", encoding="utf-8") as _f:
    BOOT_OVERLAY_CSS = _f.read()
with open(os.path.join(ASSETS_DIR, "boot_overlay.js"), "r", encoding="utf-8") as _f:
    BOOT_OVERLAY_JS = _f.read()

NAME_PATTERN = re.compile(r"^[A-Z][a-zA-Z.'-]+ [A-Z][a-zA-Z.'-]+")

# Roots still in the download queue. Mirrors scan_media.py's ROOTS
# kind="queue" entries; kept as a local literal rather than a cross-import,
# matching this file's existing pattern of small duplicated constants.
from app_config import QUEUE_LABEL  # noqa: E402
AUTOFILE_QUEUE_ROOTS = {QUEUE_LABEL}
# A companion tool that owns search+acquire+file for TV end-to-end (with a
# human-review step Auto-File doesn't have) can collide with Auto-File on the
# same files, so a TV entry in a root that tool owns is excluded from
# Auto-File (TV only - movies stay in scope). Empty by default: nothing else
# files TV out of the incoming folder, so a companion tool that does would
# list its root here.
AUTOFILE_TV_EXCLUDED_ROOTS = set()


def suggest_category(show):
    """Still genuinely used - web_server.py's Auto-File Queue
    (_autofile_plan_tv/_autofile_plan_movie) calls this directly to guess a
    brand-new show's category when nothing's archived for it yet."""
    if show["type"] == "TV":
        tv_type = show.get("tmdb_tv_type")
        if tv_type == "Miniseries":
            return "Mini-Series", "High", "TMDB lists this as a miniseries"
        if tv_type == "Documentary":
            return "Documentaries", "High", "TMDB lists this as a documentary series"
        return "Series", "High", "TMDB lists this as a regular/scripted series"
    if show["type"] == "Movie":
        genres = show.get("tmdb_genres") or []
        if "Documentary" in genres:
            return "Documentaries", "High", "TMDB genre includes Documentary"
        if "Music" in genres:
            return "Music", "High", "TMDB genre includes Music"
        if genres == ["Comedy"] and NAME_PATTERN.match(show["title"]):
            return "Comedy", "Medium", "Looks like a stand-up special - please confirm"
        return "Movies", "High", "TMDB type is Movie"
    return None, "None", "No confident TMDB match - needs manual review"


def autofile_confidently_matched(show, entry):
    """A lightweight, optimistic PREVIEW check only - drives the header
    button's live count/highlight and the dashboard panel, nothing more.
    Deliberately does NOT require episode_title (that field only exists for
    shows enrich_tv_coverage.py tracks to completion, which excludes whole
    categories - cooking/reality/documentary/talk/news - a real chunk of
    this library; requiring it here would make the preview undercount for
    exactly those shows) and does NOT validate the episode against TMDB's
    real season data (too expensive to do live for every entry on every
    page load).

    The actual run (web_server.py's run_autofile(), see autofile_naming.py)
    does the full, rigorous validation from the naming spec - real per-
    episode TMDB confirmation via autofile_naming.validate_tv_episode(),
    multi-episode-shape detection, same-pass duplicate resolution,
    destination-collision checks - and may end up flagging entries this
    preview counted as eligible. That's expected, not a bug: this function
    only answers "does it look worth queuing," not "will it actually file
    clean." The real run's audit log in logs\\ is the source of truth for
    what happened and why."""
    if not show.get("tmdb_matched"):
        return False
    if show["type"] == "TV":
        return entry.get("season") is not None and entry.get("episode") is not None
    return show["type"] == "Movie"


def autofile_in_scope(show, entry):
    """False for a TV entry sitting in a root a companion tool owns end-to-
    end (AUTOFILE_TV_EXCLUDED_ROOTS) - deliberately separate from
    autofile_confidently_matched() above: an out-of-scope entry should
    disappear from Auto-File entirely (not eligible, not needs-review
    either - it's simply not this app's job), where a genuinely
    unconfident match should still surface in "needs review" so it isn't
    silently ignored. Folding the two together would have made every
    companion-owned TV episode show up as "needs review" forever, which
    is exactly the noise this split avoids."""
    return not (show["type"] == "TV" and entry.get("root") in AUTOFILE_TV_EXCLUDED_ROOTS)

# Re-enabled 2026-08-17 at the user's request, after being parked
# 2026-08-16 pending the filename/season reorg project - see the matching
# TRAVEL_UI_ENABLED flag in the JS below.
# The portable "Travel Mode" export is not part of this release (it needs a Windows
# robocopy/PowerShell build step that is not included), so its controls stay off.
# The client-side travel_mode branches of the page below are inert without it.
TRAVEL_UI_ENABLED = False


def resolve_watched(path, watched_state):
    """Tri-state watched lookup: an explicit record (dict or legacy bare
    timestamp) always wins; a path with no record at all is Unwatched,
    full stop - no implicit "Archive content defaults to watched" fallback
    anymore (removed 2026-08-21, per explicit user request: watched status
    must never change except by the user's own toggle in the app). That
    old fallback meant a lost/renamed record - possible any time
    prune_state() can't match a path to the current scan - silently
    flipped a show back to "watched" with zero user action, which is
    exactly the "magic" the user wants eliminated. Every entry that relied
    on the old default was frozen into an explicit record via a one-time
    backfill before this changed (see the user_state.json watched dict -
    4145/4145 entries now have an explicit record), so removing the
    fallback here doesn't retroactively flip anything that was already
    showing as watched; it only changes what happens to something new
    that genuinely has no record yet."""
    raw = watched_state.get(path)
    if isinstance(raw, dict):
        return raw.get("watched", False), raw.get("at")
    if isinstance(raw, str):
        return True, raw
    return False, None


def _encode_path_for_uri(path: str) -> str:
    p = path.replace("\\", "/")
    segments = p.split("/")
    return "/".join(quote(seg, safe=":") for seg in segments)


def path_to_uri(path: str, scheme: str) -> str:
    return f"{scheme}:///" + _encode_path_for_uri(path)


def playlist_uri(paths, scheme: str) -> str:
    """Same shape as path_to_uri(), for one or more files that should play
    as one continuous sequence - "|" joins each file's own encoded path
    ("|" is illegal in a Windows filename, so it can never collide with
    real path content). handlers\\OpenVlcHandler.cs splits on it to hand
    VLC a playlist instead of a single file. A one-element list produces
    byte-identical output to path_to_uri() - see pipeline\\scan_media.py's
    process_video_ts_folder() for why an entry can have more than one (a
    DVD title split across multiple ~1GB VOB parts, meant to play back-to-
    back - confirmed real, not hypothetical: the user reported playback
    stopping after just the first part, 2026-08-21)."""
    return f"{scheme}:///" + "|".join(_encode_path_for_uri(p) for p in paths)


def fmt_size(num_bytes: float) -> str:
    gb = num_bytes / 1e9
    if gb >= 1000:
        return f"{gb / 1000:.2f} TB"
    return f"{gb:.1f} GB"


# Kept out of the f-strings below: before Python 3.12 an f-string expression may not
# contain a backslash or reuse the outer quote character.
RECOMMENDED_BADGE = " <span class='dup-recommended'>recommended keep</span>"
PATH_SEPS = "\\/"


def html_escape(s):
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


def build_quarantine_panel_html(shows):
    candidates = sorted(
        [s for s in shows if s.get("wasted_bytes", 0) > 0],
        key=lambda s: s["wasted_bytes"], reverse=True,
    )
    if not candidates:
        return "<p style='color:var(--text-dim); font-size:13px;'>No duplicate copies found.</p>"

    total_bytes = sum(s["wasted_bytes"] for s in candidates)
    total_items = sum(len(d["extra_paths"]) for s in candidates for d in s.get("duplicates", []))
    shown = candidates[:20]
    groups_html = []
    group_idx = 0
    for s in shown:
        for d in s.get("duplicates", []):
            keep_detail = d.get("keep_detail")
            extra_details = d.get("extra_details")
            if not keep_detail or extra_details is None:
                continue  # data from before this field existed - re-run a refresh to populate it
            all_details = [keep_detail] + extra_details
            all_paths = [c["path"] for c in all_details]
            ep_label = f"S{d['season']:02d}E{d['episode']:02d} &mdash; " if d.get("season") is not None else ""
            # Radio per candidate (not a button) - picks which file this
            # group keeps, defaulting to the recommended one, without
            # acting immediately. A separate per-group checkbox (below)
            # is what actually opts a group into the batch when "Process
            # selected" runs - keeps "which file wins" and "should this
            # group be touched at all" as two independent choices, so
            # changing your mind about the keeper never accidentally
            # queues a group you hadn't meant to act on yet.
            candidate_rows = "".join(
                f'<div class="dup-candidate" data-path="{html_escape(c["path"])}">'
                f'<input type="radio" name="dup-keep-{group_idx}" value="{html_escape(c["path"])}" class="dup-keep-radio" '
                f'{"checked" if c["path"] == keep_detail["path"] else ""}>'
                f'<span class="dup-name" title="{html_escape(c["path"])}">{html_escape(c["name"])}'
                f'{RECOMMENDED_BADGE if c["path"] == keep_detail["path"] else ""}</span>'
                f'<span class="dup-quality">{html_escape(c["quality_label"])}</span>'
                f'<span class="dup-size">{fmt_size(c["size_bytes"])}</span>'
                f'</div>'
                for c in all_details
            )
            groups_html.append(
                f'<div class="dup-group" data-show="{html_escape(s["title"])}" data-paths=\'{html_escape(json.dumps(all_paths))}\'>'
                f'<div class="dup-group-header">'
                f'<label class="dup-group-select-label"><input type="checkbox" class="dup-group-select" onchange="updateDupProcessButton()"> '
                f'{html_escape(s["title"])} &mdash; {ep_label}{len(all_details)} copies found</label>'
                f'</div>'
                f'{candidate_rows}</div>'
            )
            group_idx += 1
    more = len(candidates) - len(shown)
    header = (
        f"<p style='font-size:13px; color:var(--text-dim); margin:0 0 8px;'>"
        f"{total_items} lower-quality duplicate files across {len(candidates)} titles &mdash; "
        f"{fmt_size(total_bytes)} reclaimable. Check the groups you want to resolve (pick a different "
        f"file to keep first if you don't want the recommended one), then <strong>Process selected</strong> "
        f"&mdash; the losers move to <code>Pending Deletion</code> (nothing is ever deleted here, only "
        f"moved), all in one pass at the end instead of a refresh per click.</p>"
    )
    batch_bar = (
        '<div class="dup-batch-bar">'
        '<button type="button" class="header-btn" id="dup-process-btn" disabled onclick="onProcessSelectedDuplicates()">Process selected (0)</button>'
        '</div>'
    ) if groups_html else ""
    footer = f"<p style='font-size:12px; color:var(--text-dim); margin-top:6px;'>+{more} more titles not shown &mdash; see <code>quarantine_plan.ps1</code> for the full list.</p>" if more > 0 else ""
    return header + batch_bar + "".join(groups_html) + footer


def build_retention_panel_html(shows):
    candidates = [s for s in shows if s.get("ready_to_delete_count", 0) > 0]
    if not candidates:
        return "<p style='color:var(--text-dim); font-size:13px;'>Nothing eligible right now.</p>"

    candidates = sorted(candidates, key=lambda s: s["ready_to_delete_count"], reverse=True)
    total_items = sum(s["ready_to_delete_count"] for s in candidates)
    total_bytes = 0
    rows = []
    for s in candidates:
        eligible = [e for e in s["entries"] if e["ready_to_delete"]]
        size = sum(e["size_bytes"] for e in eligible)
        total_bytes += size
        rows.append(
            f'<div class="plan-row"><span class="plan-title">{html_escape(s["title"])}</span>'
            f'<span class="plan-dest">{len(eligible)} of {len(s["entries"])} ready</span>'
            f'<span class="plan-size">{fmt_size(size)}</span></div>'
        )
    header = (
        f"<p style='font-size:13px; color:var(--text-dim); margin:0 0 8px;'>"
        f"{total_items} watched episode(s)/file(s) across {len(candidates)} shows set to \"delete after "
        f"watching,\" {fmt_size(total_bytes)} total. Click <strong>🗑️ Purge</strong> in the header "
        f"(now highlighted) to <strong>move, never delete</strong> them into a <code>Pending Deletion</code> "
        f"folder (next to the app's own folder), outside your library entirely &mdash; look "
        f"through it and delete whatever you're ready to, whenever you choose.</p>"
    )
    return header + "".join(rows)


def build_autofile_panel_html(shows):
    candidates = [s for s in shows if s.get("autofile_ready_count", 0) > 0]
    needs_review = [s for s in shows if s.get("autofile_needs_review_count", 0) > 0]
    if not candidates and not needs_review:
        return "<p style='color:var(--text-dim); font-size:13px;'>Nothing eligible right now.</p>"

    candidates = sorted(candidates, key=lambda s: s["autofile_ready_count"], reverse=True)
    total_items = sum(s["autofile_ready_count"] for s in candidates)
    rows = []
    for s in candidates:
        eligible = [e for e in s["entries"] if e["autofile_eligible"]]
        size = sum(e["size_bytes"] for e in eligible)
        # Per-file rows (name + size) so "Ready to Auto-File" shows exactly
        # which files are about to be renamed and moved into the library, not
        # just a per-show count - the destination filename itself isn't
        # computed here (that needs a live TMDB episode-name lookup, done
        # for real by _autofile_plan_tv() only at actual run time, not on
        # every Dashboard page load) but the source file is exactly what's
        # about to move.
        file_rows = "".join(
            f'<div class="plan-file-row">'
            f'<span class="plan-file-name">{html_escape(os.path.basename((e.get("video_path") or e["path"]).rstrip(PATH_SEPS)))}</span>'
            f'<span class="plan-file-size">{fmt_size(e["size_bytes"])}</span></div>'
            for e in eligible
        )
        rows.append(
            f'<div class="plan-group">'
            f'<div class="plan-row"><span class="plan-title">{html_escape(s["title"])}</span>'
            f'<span class="plan-dest">{len(eligible)} of {len(s["entries"])} queued</span>'
            f'<span class="plan-size">{fmt_size(size)}</span></div>'
            f'{file_rows}</div>'
        )
    header = (
        f"<p style='font-size:13px; color:var(--text-dim); margin:0 0 8px;'>"
        f"{total_items} watched episode(s)/file(s) across {len(candidates)} shows still sitting in the "
        f"download queue, set to \"Keep forever,\" and confidently identified. Click "
        f"<strong>📦 File to Library</strong> in the header (now highlighted) to rename and move them "
        f"into <code>Video\\</code>, creating a new show/season folder if needed.</p>"
    )
    footer = ""
    if needs_review:
        review_rows = "".join(
            f'<div class="plan-row"><span class="plan-title">{html_escape(s["title"])}</span>'
            f'<span class="plan-dest">{s["autofile_needs_review_count"]} watched, not confidently matched</span></div>'
            for s in sorted(needs_review, key=lambda s: s["autofile_needs_review_count"], reverse=True)
        )
        footer = (
            f"<p style='font-size:12px; color:var(--text-dim); margin-top:10px;'>Watched but "
            f"<strong>not</strong> auto-filed (no confident TMDB episode match yet - never guessed, "
            f"file these yourself for now):</p>{review_rows}"
        )
    return header + "".join(rows) + footer


def _franchise_theme_css():
    """The `html.franchise-mode { ... }` variable block from the pack's theme."""
    if not FRANCHISE:
        return ""
    t = FRANCHISE["theme"]
    return (
        "html.franchise-mode {\n"
        f"    --bg: {t['bg']}; --panel: {t['panel']}; --text: {t['text']}; --text-dim: {t['text_dim']};\n"
        f"    --border: {t['border']}; --accent: {t['accent']}; --accent-soft: {t['accent_soft']}; --chip-bg: {t['chip_bg']};\n"
        "    --shadow: 0 1px 3px rgba(0,0,0,0.5), 0 1px 2px rgba(0,0,0,0.4);\n"
        "  }"
    )


def render_page(shows_raw, state, server_mode=False, auth_token="", travel_mode=False, travel_tmdb_api_key="", travel_mode_kind="personal", youtube_available=False, youtube_block_html="", refresh_status=None, remote_only=False, image_base="", notice_html=""):
    # type == "Other" (not confidently TV/Movie from the filename alone) is
    # normally excluded entirely, count-only - but a Wikipedia-matched one
    # (roadmap item 32) has real, useful info to show now, just from a
    # lower-confidence source than TMDB, so it's let through and visually
    # labeled "via Wikipedia" instead of silently staying invisible forever.
    shows = [dict(s) for s in shows_raw if s["type"] != "Other" or s.get("wiki_matched")]

    watched = state.get("watched", {})
    retention = state.get("retention", {})
    retention_overrides = state.get("retention_overrides", {})
    # Shareable travel exports strip these at the source rather than just
    # hiding the UI - a shareable copy can end up on someone else's machine,
    # and the per-person watch lists / not-interested lists would otherwise
    # still be sitting in the page's embedded JSON, readable via View Source
    # regardless of what buttons are shown.
    travel_share_mode = bool(travel_mode and travel_mode_kind == "shareable")
    if travel_share_mode:
        top10 = {"you": [], "partner": []}
        no_interest = {"you": [], "partner": []}
    else:
        top10 = state.get("top10", {"you": [], "partner": []})
        no_interest = state.get("no_interest", {"you": [], "partner": []})
    travel_selection = set(state.get("travel_selection", []))
    overrides = state.get("overrides", {})

    # Disambiguate shows that share a title but are genuinely DIFFERENT
    # works. Real case: three separate series of the same name on disk
    # (".../Series/Some Show (1963)" / "(2005)" / "(2023)"), each its own
    # TMDB id - the folder year is stripped during scan so all three carry
    # the bare title and the grid shows three identical cards.
    # Append the distinguishing year, display-only: the title stays clean
    # in media_db.json (TMDB matching, overrides and cross-app keys all
    # still key off it).
    #
    # NOT the same-show-across-sources case: a digital + physical (or
    # Amazon) copy of one movie shares its tmdb_id and is meant to look
    # alike - show_key()'s :physical/:amazon suffix already keeps their
    # state separate, and render adds the year for movies anyway, so
    # touching the title here would only double it ("Stand by Me (1986)
    # (1986)"). So: only disambiguate a title group that spans two or more
    # distinct tmdb_ids, or (all unmatched) two or more distinct years -
    # and never re-append a year the title already ends with.
    _by_title = {}
    for s in shows:
        _by_title.setdefault((s.get("title"), s.get("type")), []).append(s)
    for (title, _type), group in _by_title.items():
        if len(group) < 2 or not title:
            continue
        ids = {s.get("tmdb_id") for s in group if s.get("tmdb_id")}
        years = {s["years"][0] for s in group if s.get("years")}
        if len(ids) < 2 and not (not ids and len(years) >= 2):
            continue
        for s in group:
            if s.get("years"):
                yr = s["years"][0]
                if not title.rstrip().endswith(f"({yr})"):
                    s["title"] = f"{title} ({yr})"

    for s in shows:
        s["entries"] = [dict(e) for e in s["entries"]]
        key = show_key(s)
        s["show_key"] = key
        override = overrides.get(key)
        if override:
            # Admin-panel correction (see web_server.py's /api/override) -
            # applied here for an immediate visual effect the moment it's
            # saved, not just after the next pipeline run. A poster-only
            # fix just swaps tmdb_poster; a wrong-match fix carries the
            # already-fetched replacement fields the endpoint saved at
            # override time (see build_override_tmdb_fields() there) - no
            # live network call belongs in render_page(), which runs on
            # every single page load.
            if override.get("poster_override"):
                s["tmdb_poster"] = override["poster_override"]
            if override.get("backdrop_override"):
                s["tmdb_backdrop"] = override["backdrop_override"]
                s["backdrop_pinned"] = True  # user picked this one in the carousel
            if override.get("tmdb_fields_override"):
                s.update(override["tmdb_fields_override"])
            if override.get("season_offset"):
                s["season_offset"] = override["season_offset"]
            if override.get("split_by_year"):
                s["split_by_year"] = True
            if override.get("genre_override"):
                # Forces every genre-badge/filter/count consumer to this one
                # value - same "override wins outright" shape as poster/
                # backdrop above, not a merge with whatever TMDB did or
                # didn't provide. genre_overridden is its own explicit flag
                # (not just "tmdb_genres has 1 entry", which is also true
                # for plenty of real single-genre shows) so the Admin panel
                # can show a Clear button only when there's actually
                # something to clear.
                s["tmdb_genres"] = [override["genre_override"]]
                s["genre_overridden"] = True
            s["has_override"] = True
        # Deliberately a separate, title-keyed lookup, not part of the
        # show_key()-keyed override block above - matches
        # detect_runtime_mismatch.py's own load_confirmed_runtime_titles()
        # and state_store.py's prune-exemption, both already title-keyed
        # for this field specifically (see /api/confirm-runtime). Applied
        # here so confirming a runtime clears the "Unknown" badge/detail
        # immediately, not just after the next full refresh re-runs that
        # script.
        title_key = f"title:{s['type']}:{s['title']}"
        if overrides.get(title_key, {}).get("runtime_confirmed"):
            s["runtime_mismatch"] = False
        s["retention"] = retention.get(key)
        for e in s["entries"]:
            if e.get("no_real_file"):
                # Physical-collection entry (pipeline/enrich_physical.py) -
                # there's no file on disk to open a folder for or play, in
                # travel mode or otherwise. e["path"] stays a synthetic
                # "physical://..." string (still used as the watched-state
                # key below, just never turned into a real URI).
                e["url"] = None
                e["file_url"] = None
            elif travel_mode:
                # Mirrors <library>/ for an already-archived entry, or the
                # synthesized future-archive placement for a queue item -
                # see travel_common.travel_rel_path()'s own docstring. Same
                # call build_travel_plan.py's copy planner makes, so a
                # copied file and this link always agree.
                e["rel_path"] = travel_rel_path(s, e)
                e["url"] = None
                e["file_url"] = None
            else:
                e["url"] = path_to_uri(e["path"], "openfolder")
                if e.get("video_path"):
                    all_parts = [e["video_path"]] + (e.get("playlist_extra_paths") or [])
                    e["file_url"] = playlist_uri(all_parts, "openvlc")
                else:
                    e["file_url"] = None
            e["watched"], e["watched_at"] = resolve_watched(e["path"], watched)
            e["travel_selected"] = e["path"] in travel_selection
            e["retention_override"] = bool(retention_overrides.get(e["path"]))
            e["ready_to_delete"] = s["retention"] == "delete_after_watch" and e["watched"] and not e["retention_override"]
            e["autofile_eligible"] = (
                e["root"] in AUTOFILE_QUEUE_ROOTS and e["watched"]
                and s["retention"] != "delete_after_watch"
                and autofile_in_scope(s, e)
                and autofile_confidently_matched(s, e)
            )
            e["autofile_needs_review"] = (
                e["root"] in AUTOFILE_QUEUE_ROOTS and e["watched"]
                and s["retention"] != "delete_after_watch"
                and autofile_in_scope(s, e)
                and not autofile_confidently_matched(s, e)
            )
            # TV: real per-episode air date (from enrich_tv_coverage.py).
            # Movies: show-level TMDB release date, same for every copy.
            e["release_date"] = e.get("air_date") or (s.get("tmdb_release_date") if s["type"] == "Movie" else None)

        watched_count = sum(1 for e in s["entries"] if e["watched"])
        s["all_watched"] = len(s["entries"]) > 0 and watched_count == len(s["entries"])
        s["ready_to_delete_count"] = sum(1 for e in s["entries"] if e["ready_to_delete"])
        s["autofile_ready_count"] = sum(1 for e in s["entries"] if e["autofile_eligible"])
        s["autofile_needs_review_count"] = sum(1 for e in s["entries"] if e["autofile_needs_review"])
        s["top10_you"] = key in top10.get("you", [])
        s["top10_partner"] = key in top10.get("partner", [])
        s["no_interest_you"] = key in no_interest.get("you", [])
        s["no_interest_partner"] = key in no_interest.get("partner", [])
        release_dates = [e["release_date"] for e in s["entries"] if e.get("release_date")]
        s["latest_release_date"] = max(release_dates, default=None)

    # Physical-collection entries are excluded from the headline library
    # stats deliberately - they have no real file on disk (zero bytes, no
    # filing workflow) and the whole point of the source-type filter
    # defaulting to "Digital only" is that physical stays invisible until
    # asked for; a jump in "Movies (N)"/"TV Shows (N)" the moment this
    # file exists would contradict that. tmdb_matched_count/has_tmdb below
    # get the same treatment for the same reason (the "Matched Online"
    # stat). Genre counts, size/wasted bytes, and filing-queue stats are
    # left on the full `shows` list - physical entries contribute zero to
    # all of those anyway, so there's nothing to skew.
    digital_shows = [s for s in shows if not s.get("physical_collection")]
    total_movies = sum(1 for s in digital_shows if s["type"] == "Movie")
    total_tv = sum(1 for s in digital_shows if s["type"] == "TV")
    total_episodes = sum(s["episode_count"] or 0 for s in digital_shows if s["type"] == "TV")
    total_folders = sum(s["version_count"] for s in digital_shows)

    stage_counts = {}
    for s in shows:
        # A synthetic zero-owned record (pipeline/track_purged_shows.py's
        # purged_tracked, pipeline/enrich_wanted.py's wanted) never goes
        # through scan_media.py, the only script that actually sets this -
        # defensive .get() so a real one of those existing doesn't crash
        # the entire page. Confirmed real, not hypothetical: 2026-09-13,
        # the first time a purged_tracked record ever actually existed.
        stage = s.get("pipeline_stage", "Not Owned")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    ready_to_delete_count = sum(s["ready_to_delete_count"] for s in shows)
    autofile_ready_count = sum(s["autofile_ready_count"] for s in shows)

    genre_counts = {}
    for s in shows:
        for g in s.get("tmdb_genres") or [s.get("genre", "Uncategorized")]:
            genre_counts[g] = genre_counts.get(g, 0) + 1

    tmdb_matched_count = sum(1 for s in digital_shows if s.get("tmdb_matched"))
    has_tmdb = any("tmdb_genres" in s for s in digital_shows)

    total_size_bytes = sum(s.get("total_size_bytes", 0) for s in shows)
    total_wasted_bytes = sum(s.get("wasted_bytes", 0) for s in shows)
    incomplete_count = sum(1 for s in shows if s.get("missing_episodes") or s.get("missing_seasons"))
    # Only worth showing the export button when there's actually something
    # to export - an empty click would just be confusing.
    has_duplicates = any(s.get("duplicates") for s in shows)
    duplicates_export_btn = (
        '<button type="button" class="stat-export-btn" onclick="exportDuplicatesList()">'
        'Export list to Desktop</button>'
        if has_duplicates else ""
    )

    data_json = _js_json(shows)
    genre_counts_json = _js_json(genre_counts)

    genre_note = (
        f"Genres come from TheMovieDB ({tmdb_matched_count}/{len(shows)} titles matched; unmatched titles fall back to \"Uncategorized\")."
        if has_tmdb else
        "Genre labels are a best-effort guess from folder names, not verified metadata."
    )

    quarantine_panel_html = build_quarantine_panel_html(shows)
    retention_panel_html = build_retention_panel_html(shows)
    autofile_panel_html = build_autofile_panel_html(shows)

    # Home-screen / PWA metadata - only when served by the live server
    # (the static snapshot and travel export have no /manifest.webmanifest
    # or /app-icon-*.png routes to point at). remote_only gets its own
    # manifest (orientation: landscape, display: fullscreen - Android/
    # Chrome-only in effect, iOS ignores both, but free to add) and its own
    # home-screen title, so someone who bookmarks the couch-remote page
    # doesn't get an icon indistinguishable from the real app.
    mobile_head = (
        f'<link rel="manifest" href="{"/remote-manifest.webmanifest" if remote_only else "/manifest.webmanifest"}">\n'
        '<link rel="apple-touch-icon" href="/app-icon-180.png">\n'
        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
        f'<meta name="apple-mobile-web-app-title" content="{html_escape(branding.app_name() + (" Remote" if remote_only else ""))}">\n'
        '<meta name="theme-color" content="#14161a">'
    ) if server_mode else ""

    # YouTube block (see web_server.py's "YouTube block" section). Only when
    # the live server is serving AND the block mounted AND it rendered
    # something - otherwise the chip and the <section> are both omitted
    # entirely, so the static snapshot / travel export never carry a dead
    # toggle. The block string is self-contained (its own scoped <style> +
    # one IIFE); it's passed as a .format() *argument*, so the braces inside
    # it are literal and need no escaping.
    _yt_on = bool(server_mode and youtube_available and youtube_block_html)
    youtube_chip_html = (
        '<button class="header-btn" id="youtube-mode-toggle" type="button" '
        'title="YouTube — the curated channel feed in place of the library grid">'
        '<span class="icon-chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="2" y="5" width="20" height="14" rx="4"></rect>'
        '<path d="M10 9l5 3-5 3z" fill="currentColor" stroke="currentColor"></path>'
        '</svg></span></button>'
    ) if _yt_on else ""
    youtube_section_html = (
        '<section id="youtube-block" hidden>' + youtube_block_html + "</section>"
    ) if _yt_on else ""

    # The franchise controls only exist when the pack defines a franchise AND at
    # least one of its titles is actually in the library.
    owned = {(s.get("type"), s.get("tmdb_id")) for s in shows if s.get("tmdb_id")}
    franchise_enabled = bool(FRANCHISE and any((t["type"], t["id"]) in owned for t in FRANCHISE["titles"]))

    people = branding.people()

    return _fill(HTML_TEMPLATE, 
        orbitron_b64=ORBITRON_B64,
        favicon_uri=FAVICON_URI,
        mobile_head=mobile_head,
        franchise_icon_uri=FRANCHISE_ICON_URI,
        banner_uri=branding.header_uri(),  # the uploaded header, else the pack's banner
        franchise_banner_uri=FRANCHISE_BANNER_URI,
        app_name=html_escape(branding.app_name()),
        image_base_json=_js_json(image_base),
        notice_html=notice_html,
        # long app names shrink the wordmark so it never runs under the header buttons
        title_fit=round(min(1.0, 21 / max(1, len(branding.app_name()))), 2),
        app_tagline=html_escape(branding.tagline()),
        accent_css=branding.appearance_css(),
        franchise_theme_css=_franchise_theme_css(),
        franchise_hidden_attr="" if franchise_enabled else 'style="display:none"',
        guide_hidden_attr="" if branding.FIELD_GUIDE_AVAILABLE else 'style="display:none"',
        app_name_json=_js_json(branding.app_name()),
        app_tagline_json=_js_json(branding.tagline()),
        franchise_name=html_escape(FRANCHISE["name"]) if FRANCHISE else "Franchise",
        franchise_json=_js_json(FRANCHISE or {"name": "", "titles": []}),
        people_json=_js_json({p["id"]: p for p in people}),
        you_name=html_escape(people[0]["name"]),
        you_initial=html_escape(people[0]["initial"]),
        partner_name=html_escape(people[1]["name"]),
        partner_initial=html_escape(people[1]["initial"]),
        field_guide_url_json=_js_json(branding.FIELD_GUIDE_URL),
        travel_ui_enabled_json=_js_json(TRAVEL_UI_ENABLED),
        amazon_domain_json=_js_json(app_config.AMAZON_DOMAIN),
        hint_labels_json=_js_json(branding.hint_labels()),
        idle_quips_json=_js_json(branding.idle_quips()),
        youtube_banner_uri=YOUTUBE_BANNER_URI,
        boot_overlay_uri=BOOT_OVERLAY_URI,
        total_movies=total_movies,
        total_tv=total_tv,
        total_episodes=f"{total_episodes:,}",
        total_folders=f"{total_folders:,}",
        total_shows_movies=f"{(total_movies + total_tv):,}",
        data_json=data_json,
        genre_counts_json=genre_counts_json,
        genre_note=html_escape(genre_note),
        tmdb_matched_count=tmdb_matched_count,
        total_size_label=fmt_size(total_size_bytes),
        wasted_size_label=fmt_size(total_wasted_bytes),
        duplicates_export_btn=duplicates_export_btn,
        incomplete_count=incomplete_count,
        archived_count=stage_counts.get("Archived", 0),
        queue_count=stage_counts.get("In Queue", 0),
        quarantine_panel_html=quarantine_panel_html,
        retention_panel_html=retention_panel_html,
        autofile_panel_html=autofile_panel_html,
        ready_to_delete_count=ready_to_delete_count,
        autofile_ready_count=autofile_ready_count,
        queue_label_json=_js_json(QUEUE_LABEL),
        server_mode_json=_js_json(server_mode),
        auth_token_json=_js_json(auth_token),
        travel_mode_json=_js_json(travel_mode),
        travel_share_mode_json=_js_json(travel_share_mode),
        travel_tmdb_api_key_json=_js_json(travel_tmdb_api_key if travel_mode else ""),
        recently_watched_json=_js_json(state.get("recently_watched", [])),
        recently_watched_cap=RECENTLY_WATCHED_CAP,
        travel_panel_display=("block" if (TRAVEL_UI_ENABLED and server_mode and not travel_mode) else "none"),
        # Pre-fills the destination field with wherever the last successful
        # Travel Export actually went (2026-08-27, at the user's request) -
        # readonly, so this is a default, not a lock: Browse still lets you
        # pick a different drive (e.g. exporting to a second drive as a
        # backup) without needing to clear anything first.
        travel_last_destination=html_escape(state.get("travel_last_destination", "")),
        youtube_chip_html=youtube_chip_html,
        youtube_section_html=youtube_section_html,
        refresh_status_json=_js_json(refresh_status or {"running": False}),
        loading_hints_json=LOADING_HINTS_JSON,
        boot_overlay_css=BOOT_OVERLAY_CSS,
        boot_overlay_js=BOOT_OVERLAY_JS,
        remote_only_json=_js_json(remote_only),
        # Rendered straight into <html class="..."> (not just set later by
        # JS) so html.remote-only's CSS rules (see their own comment) apply
        # from the very first paint - zero flash of the full grid before a
        # script gets a chance to run.
        remote_only_html_class=("remote-only" if remote_only else ""),
        # #remote-idle defaults to VISIBLE (not hidden) when remote_only, so
        # the common case (nothing playing) needs zero wait on the first
        # async /api/vlc/state poll to show something instead of a blank
        # page - pollVlcState()'s own hideRemoteIdle() takes over the
        # instant it learns something IS actually playing.
        remote_idle_initial_hidden=("" if remote_only else "hidden"),
        # viewport-fit=cover only for the remote-only page - it's what lets
        # the landscape transport card draw under the iPhone's sensor
        # housing/home indicator via env(safe-area-inset-*) at all. Left off
        # the main page since nothing there needs it and it's scoped
        # deliberately, not just harmless-by-default (2026-09-18).
        viewport_content=(
            "width=device-width, initial-scale=1.0, viewport-fit=cover" if remote_only
            else "width=device-width, initial-scale=1.0"
        ),
    )


# The page is three real files, not a string in this module:
#   app/templates/page.html   the skeleton (head, markup, includes)
#   app/static/app.css        the stylesheet
#   app/static/js/*.js        the application script, in feature files that run in order
# Per-render values appear in them as @@name@@ and are filled in by _fill(). It is a
# single pass over the text, so a value that happens to contain "@@x@@" is left alone.
_TEMPLATE_DIR = os.path.join(_ROOT, "app", "templates")
_STATIC_DIR = os.path.join(_ROOT, "app", "static")
_FIELD_RE = re.compile(r"@@([a-z_0-9]+)@@")


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_template():
    page = _read_text(os.path.join(_TEMPLATE_DIR, "page.html"))
    page = re.sub(r"@@file:([\w.]+)@@", lambda m: _read_text(os.path.join(_STATIC_DIR, m.group(1))), page)
    # @@files:js/*.js@@ includes every match, in name order, joined by newlines (one script, one scope)
    return re.sub(r"@@files:([\w./*-]+)@@",
                  lambda m: "\n".join(_read_text(p) for p in sorted(glob.glob(os.path.join(_STATIC_DIR, m.group(1))))), page)


def _fill(template, **values):
    """Replace every @@name@@ with str(values[name]); a missing name is a KeyError."""
    return _FIELD_RE.sub(lambda m: str(values[m.group(1)]), template)


HTML_TEMPLATE = _load_template()

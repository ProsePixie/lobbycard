"""
Builds the sample library used by demo mode.

Everything here is invented: the titles, descriptions, people, dates and the
poster artwork (drawn with Pillow). Nothing is downloaded and no TMDB data is
used, so demo mode runs offline with no API key and has no licensing strings.

Writes into the demo data folder (demo_data/ by default):
  media_db.json         the library, same shape the real scanner produces
  user_state.json       a few watched episodes so the lists have something in them
  tmdb_images/...       generated posters, thumbnails and backdrops, served by the normal image route
  library/...           empty stub video files, so folder and file actions have a target

Safe to re-run: it rewrites its own files and touches nothing else.
"""
import json
import math
import os
import random
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pipeline"))
os.environ["MEDIABOX_DEMO"] = "1"
import app_config  # noqa: E402

DATA_DIR = app_config.DATA_DIR
LIB = os.path.join(DATA_DIR, "library")
TODAY = date.today()

# (title, type, genres, seasons: [episode counts], hue, overview, status, year)
CATALOG = [
    ("The Salt Lighthouse", "TV", ["Drama"], [8, 8, 8], 205, "A keeper's family inherits a remote lighthouse and the logbooks of everyone who lived there before.", "Returning Series", 2022),
    ("Harbor Nine", "TV", ["Crime", "Drama"], [10, 10], 12, "Nine detectives share one cold-case room above a fish market, and one very patient informant.", "Returning Series", 2023),
    ("Orbital Tea Room", "TV", ["Comedy"], [8, 8, 8, 8], 38, "The only cafe on a failing space station keeps its regulars, its rules and its secrets.", "Ended", 2018),
    ("Kestrel & Vane", "TV", ["Mystery"], [6], 275, "Two rival auction appraisers keep finding the same stolen painting.", "Returning Series", 2026),
    ("The Long Tuesday", "TV", ["Mystery", "Sci-Fi"], [8, 8], 165, "One town relives a single Tuesday until someone remembers how it started.", "Returning Series", 2025),
    ("Paper Engines", "TV", ["Sci-Fi"], [8, 8], 48, "In a city powered by clockwork, a printer discovers a machine that writes tomorrow's news.", "Ended", 2020),
    ("Low Tide Radio", "TV", ["Documentary"], [5], 190, "A year with the volunteers who run a coastal radio station for fishing boats.", "Ended", 2024),
    ("Fieldnotes from Nowhere", "TV", ["Documentary"], [6, 6], 95, "A naturalist walks the least visited places in the country and writes down what is there.", "Ended", 2021),
    ("Brightwater Academy", "TV", ["Drama"], [10, 10, 10], 330, "Scholarship students and old money collide at a school that never closes for winter.", "Returning Series", 2021),
    ("Gravel & Honey", "TV", ["Comedy"], [8, 8], 22, "Two sisters inherit a roadside beekeeping stand and an enemy at the next stall.", "Ended", 2019),
    ("Night Ferry", "TV", ["Thriller"], [8], 250, "The last ferry of the night carries eleven passengers and one lie.", "Ended", 2024),
    ("Moth Season", "Movie", ["Drama"], [], 125, "A widow spends one summer cataloguing the moths her late husband collected.", "Released", 2021),
    ("Second Summit", "Movie", ["Adventure"], [], 150, "Two former climbing partners return to the mountain that ended their friendship.", "Released", 2019),
    ("The Clockmaker's Daughter", "Movie", ["Fantasy"], [], 60, "A girl finishes her father's last clock and finds out what it was counting down to.", "Released", 2023),
    ("Quiet Frequencies", "Movie", ["Sci-Fi"], [], 215, "An engineer at a deep-space listening post hears a signal that answers her.", "Released", 2022),
    ("A Table for Seven", "Movie", ["Comedy"], [], 15, "A family dinner goes wrong in exactly the order everyone predicted.", "Released", 2018),
]

CAST_POOL = [
    ("Mara Lindqvist", "Lead"), ("Tobias Okonkwo", "Supporting"), ("Ines Barros", "Supporting"),
    ("Callum Reyes", "Supporting"), ("Hana Petrov", "Guest"), ("Rowan Achterberg", "Guest"),
]
EPISODE_WORDS = [
    "The Long Way Round", "Small Hours", "Low Water", "What the Ledger Said", "A Quiet Arrangement",
    "Borrowed Light", "The Second Key", "Weathering", "Paper Lanterns", "Last Post", "Tin Roof",
    "The Understudy", "Open Water", "Salt and Iron", "Winter Hours", "The Good Chair", "Undertow",
    "Half Measures", "A Fair Wind", "Evening Edition",
]


def hsl_to_rgb(h, s, l):
    h = (h % 360) / 360.0
    def f(n):
        k = (n + h * 12) % 12
        a = s * min(l, 1 - l)
        return l - a * max(-1, min(k - 3, 9 - k, 1))
    return tuple(int(round(f(n) * 255)) for n in (0, 8, 4))


def make_art(path, width, height, hue, title, subtitle, seed):
    """Dusk-gradient poster with a few soft shapes and the title set on it."""
    from PIL import Image, ImageDraw, ImageFont
    rnd = random.Random(seed)
    top, bottom = hsl_to_rgb(hue, 0.55, 0.38), hsl_to_rgb(hue + 25, 0.60, 0.12)
    img = Image.new("RGB", (width, height))
    px = ImageDraw.Draw(img)
    for y in range(height):
        t = y / (height - 1)
        px.line([(0, y), (width, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    shapes = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shapes)
    for _ in range(5):
        r = rnd.randint(width // 6, width // 2)
        cx, cy = rnd.randint(0, width), rnd.randint(0, int(height * 0.7))
        col = hsl_to_rgb(hue + rnd.randint(-40, 40), 0.65, rnd.uniform(0.45, 0.65)) + (rnd.randint(30, 70),)
        sd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    horizon = int(height * 0.72)
    sd.rectangle([0, horizon, width, height], fill=(0, 0, 0, 90))
    img = Image.alpha_composite(img.convert("RGBA"), shapes).convert("RGB")
    d = ImageDraw.Draw(img)
    try:
        big = ImageFont.load_default(size=max(18, width // 11))
        small = ImageFont.load_default(size=max(12, width // 26))
    except TypeError:  # Pillow older than 10.1: bitmap font, still readable
        big = small = ImageFont.load_default()
    # wrap the title to the poster width
    words, lines, cur = title.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=big) <= width * 0.84 or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    line_h = int(getattr(big, "size", 20) * 1.15)
    y = int(height * 0.60) - (len(lines) - 1) * line_h // 2 if width < height else int(height * 0.40)
    for ln in lines:
        tw = d.textlength(ln, font=big)
        d.text(((width - tw) / 2 + 2, y + 2), ln, font=big, fill=(0, 0, 0))
        d.text(((width - tw) / 2, y), ln, font=big, fill=(245, 245, 240))
        y += line_h
    sw = d.textlength(subtitle, font=small)
    d.text(((width - sw) / 2, y + 8), subtitle, font=small, fill=(225, 225, 220))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG", optimize=True)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def build():
    rnd = random.Random(2026)
    shows, watched, recent = [], {}, []
    tmdb_id = 900001
    now = datetime.now().replace(microsecond=0)

    for idx, (title, kind, genres, seasons, hue, overview, status, year) in enumerate(CATALOG):
        tid = tmdb_id + idx
        poster, backdrop = f"/demoposter{tid}.png", f"/demobackdrop{tid}.png"
        sub = f"{year} · {genres[0]}"
        make_art(os.path.join(DATA_DIR, "tmdb_images", "poster", poster.lstrip("/")), 500, 750, hue, title, sub, tid)
        make_art(os.path.join(DATA_DIR, "tmdb_images", "rec_poster", poster.lstrip("/")), 185, 278, hue, title, sub, tid)
        make_art(os.path.join(DATA_DIR, "tmdb_images", "backdrop", backdrop.lstrip("/")), 1280, 720, hue, title, sub, tid + 7)

        category = "Series" if kind == "TV" else "Movies"
        entries, missing_eps, size_total = [], [], 0
        safe = title.replace("&", "and").replace("'", "")
        if kind == "TV":
            first_air = date(year, 3, 1)
            for s_no, ep_count in enumerate(seasons, start=1):
                season_start = first_air + timedelta(days=365 * (s_no - 1))
                # Brightwater Academy is deliberately incomplete: season 3 owns 7 of 10
                owned = 7 if (title == "Brightwater Academy" and s_no == 3) else ep_count
                for e_no in range(1, ep_count + 1):
                    ep_title = EPISODE_WORDS[(idx * 3 + s_no * 5 + e_no) % len(EPISODE_WORDS)]
                    air = season_start + timedelta(days=7 * (e_no - 1))
                    if e_no > owned:
                        missing_eps.append({"season": s_no, "episode": e_no, "name": ep_title, "air_date": air.isoformat()})
                        continue
                    fname = f"{safe} - S{s_no:02d}E{e_no:02d} - {ep_title}.mkv"
                    folder = os.path.join(LIB, category, safe, f"Season {s_no:02d}")
                    os.makedirs(folder, exist_ok=True)
                    full = os.path.join(folder, fname)
                    open(full, "ab").close()
                    size = rnd.randint(600, 1400) * 1024 * 1024
                    size_total += size
                    added = now - timedelta(days=rnd.randint(2, 400)) if s_no < len(seasons) else now - timedelta(days=rnd.randint(0, 20))
                    entries.append({
                        "path": full, "video_path": full, "root": "Archive", "category": category,
                        "name": fname, "season": s_no, "episode": e_no,
                        "mtime": iso(added), "ctime": iso(added), "size_bytes": size,
                        "playlist_extra_paths": None, "air_date": air.isoformat(), "episode_title": ep_title,
                    })
            ep_total = sum(seasons)
            owned_count = len(entries)
        else:
            fname = f"{safe} ({year}).mkv"
            folder = os.path.join(LIB, category, safe + f" ({year})")
            os.makedirs(folder, exist_ok=True)
            full = os.path.join(folder, fname)
            open(full, "ab").close()
            size = rnd.randint(1800, 5200) * 1024 * 1024
            size_total = size
            added = now - timedelta(days=rnd.randint(1, 300))
            entries.append({
                "path": full, "video_path": full, "root": "Archive", "category": category,
                "name": fname, "season": None, "episode": None,
                "mtime": iso(added), "ctime": iso(added), "size_bytes": size,
                "playlist_extra_paths": None, "air_date": None, "episode_title": None,
            })
            ep_total = owned_count = 1

        next_ep = None
        if status == "Returning Series" and kind == "TV":
            next_ep = {"season": len(seasons), "episode": seasons[-1] + 1, "name": "Episode %d" % (seasons[-1] + 1),
                       "air_date": (TODAY + timedelta(days=9 + idx)).isoformat()}

        recs = []
        for off in (1, 2, 3):
            r = CATALOG[(idx + off) % len(CATALOG)]
            rid = tmdb_id + (idx + off) % len(CATALOG)
            recs.append({"tmdb_id": rid, "title": r[0], "poster_path": f"/demoposter{rid}.png", "type": r[1],
                         "overview": r[5], "vote_average": round(6.4 + ((rid * 7) % 20) / 10, 1)})
        cast = [{"id": 800000 + idx * 10 + i, "name": n, "character": c, "profile_path": None, "imdb_id": None}
                for i, (n, c) in enumerate(CAST_POOL[:5])]

        show = {
            "title": title, "type": kind, "genre": genres[0], "years": [year],
            "episode_count": len(entries), "version_count": len(entries),
            "roots": ["Archive"], "archive_categories": [category], "pipeline_stage": "Archived",
            "entries": entries, "latest_added": max(e["mtime"] for e in entries),
            "total_size_bytes": size_total, "wasted_bytes": 0, "duplicates": [],
            "missing_episodes": missing_eps, "missing_seasons": [],
            "episodes_expected_so_far": ep_total, "assumed_physical_media": False,
            "completeness_skip_reason": None, "runtime_mismatch": False, "file_runtime_minutes": None,
            "physical_seasons_owned": [], "epguides_url": None, "tracking_start_date": None,
            "tmdb_matched": True, "tmdb_genres": genres, "tmdb_id": tid, "tmdb_name": title,
            "tmdb_overview": overview, "tmdb_poster": poster, "tmdb_backdrop": backdrop,
            "tmdb_vote": round(6.6 + ((tid * 13) % 25) / 10, 1), "tmdb_release_date": f"{year}-03-01",
            "tmdb_trailer_key": None, "tmdb_cast": cast, "tmdb_recommendations": recs,
        }
        if kind == "TV":
            show.update({
                "tmdb_status": status, "tmdb_tv_type": "Scripted", "tmdb_number_of_seasons": len(seasons),
                "tmdb_imdb_id": None, "tmdb_episode_run_time": 42, "tmdb_creators": ["Elin Marsh"],
                "tmdb_next_episode": next_ep,
            })
        else:
            show.update({"tmdb_runtime": rnd.randint(88, 134), "tmdb_imdb_id": None, "tmdb_director": "Jonas Whitlock"})
        shows.append(show)

        # watched: finish most of the early seasons of the first few shows
        if kind == "TV" and idx % 3 != 2:
            for e in entries:
                if e["season"] < len(seasons) or idx % 2 == 0:
                    watched[e["path"]] = {"watched": True, "at": iso(now - timedelta(days=rnd.randint(1, 60)))}
            recent.append(f"tmdb:TV:{tid}")
        if kind == "Movie" and idx % 2 == 1:
            watched[entries[0]["path"]] = {"watched": True, "at": iso(now - timedelta(days=rnd.randint(1, 90)))}
            recent.append(f"tmdb:Movie:{tid}")

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "media_db.json"), "w", encoding="utf-8") as f:
        json.dump(shows, f, indent=1, ensure_ascii=False)
    state_path = os.path.join(DATA_DIR, "user_state.json")
    state = {
        "watched": watched, "recently_watched": recent[:12],
        # a few watch-list stars and "not interested" marks so those views have content
        "top10": {"you": ["tmdb:TV:900009", "tmdb:Movie:900015"], "partner": ["tmdb:TV:900003", "tmdb:TV:900009"]},
        "no_interest": {"you": ["tmdb:TV:900007"], "partner": ["tmdb:Movie:900014"]},
    }
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    return len(shows), len(watched)


if __name__ == "__main__":
    n, w = build()
    print(f"Demo library written to {DATA_DIR}: {n} titles, {w} watched marks.")

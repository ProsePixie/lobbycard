"""
The refresh pipeline end to end, on a small invented library, against a fake TMDB:

    scan_media -> enrich_tmdb -> consolidate -> enrich_tv_coverage

No network, no real key. This is the test that proves the pieces fit: what the scanner
writes is what the matcher reads, what the matcher writes is what the page renders.
"""
import json
import os
import subprocess
import sys

import pytest

from fake_tmdb import FakeTMDB
from helpers import ROOT

PIPELINE = os.path.join(ROOT, "pipeline")


@pytest.fixture(scope="module")
def tmdb():
    srv = FakeTMDB()
    yield srv
    srv.close()


def touch(path, size=4096):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


@pytest.fixture(scope="module")
def world(tmp_path_factory, tmdb):
    root = tmp_path_factory.mktemp("e2e")
    lib, inc, data = root / "Library", root / "Incoming", root / "data"
    for e in (1, 2, 3):
        touch(f"{lib}/Series/Brightwater Academy/Season 01/Brightwater Academy - S01E0{e} - Episode {e}.mkv")
    # a second copy of episode 3 at higher quality, in the incoming folder, plus the new episode 4
    touch(f"{inc}/Brightwater.Academy.S01E03.1080p.WEB.x264-GRP/Brightwater.Academy.S01E03.1080p.WEB.x264-GRP.mkv", 8192)
    touch(f"{inc}/Brightwater.Academy.S01E04.720p.HDTV.x264-GRP/Brightwater.Academy.S01E04.720p.HDTV.x264-GRP.mkv")
    # a show whose episode titles match nothing TMDB has for it: the safety net must not trust it
    for e in (1, 2):
        touch(f"{lib}/Series/Night Ferry/Season 01/Night Ferry - S01E0{e} - Something Else Entirely.mkv")
    touch(f"{lib}/Movies/The Film (2019)/The Film (2019).mkv")
    touch(f"{lib}/Movies/Second Summit (2019)/Second Summit (2019).mkv")
    touch(f"{lib}/Movies/zzzz qqqq unknowable (2001)/zzzz qqqq unknowable (2001).mkv")
    env = dict(os.environ, MEDIABOX_ARCHIVE_ROOT=str(lib), MEDIABOX_QUEUE_ROOT=str(inc), MEDIABOX_DATA_DIR=str(data),
               MEDIABOX_CONFIG=str(root / "config.json"), MEDIABOX_TMDB_API_BASE=tmdb.api_base, MEDIABOX_TMDB_IMAGE_BASE=tmdb.image_base,
               MEDIABOX_TMDB_API_KEY="deadbeef" * 4)
    for k in ("TMDB_API_KEY", "MEDIABOX_DEMO"):
        env.pop(k, None)

    def run(script):
        r = subprocess.run([sys.executable, os.path.join(PIPELINE, script)], env=env, capture_output=True, text=True, cwd=ROOT, timeout=120)
        assert r.returncode == 0, f"{script} failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}"
        return r.stdout

    run("scan_media.py")
    scanned = json.load(open(data / "media_db.json", encoding="utf-8"))
    run("enrich_tmdb.py")
    run("consolidate.py")
    run("enrich_tv_coverage.py")
    return {"db": json.load(open(data / "media_db.json", encoding="utf-8")), "scanned": scanned, "data": data, "env": env, "run": run}


def by_title(db, title):
    return next(s for s in db if s["title"] == title)


def test_scanner_groups_files_into_shows_and_notes_the_incoming_copies(world):
    scanned = world["scanned"]
    bw = by_title(scanned, "Brightwater Academy")
    assert bw["type"] == "TV" and len(bw["entries"]) == 5
    assert {e["root"] for e in bw["entries"]} == {"Archive", "Incoming"}
    assert by_title(scanned, "The Film")["years"] == [2019]


def test_every_show_is_matched_except_the_unknowable_one(world):
    db = world["db"]
    assert by_title(db, "Brightwater Academy")["tmdb_id"] == 1001
    assert by_title(db, "The Film")["tmdb_id"] == 2001
    assert not by_title(db, "Zzzz Qqqq Unknowable")["tmdb_matched"], "no candidate close enough: left unmatched, not guessed"


def test_the_year_in_the_folder_name_picks_the_right_remake(world):
    assert by_title(world["db"], "Second Summit")["tmdb_id"] == 2003


def test_matched_shows_carry_what_the_page_needs(world):
    bw = by_title(world["db"], "Brightwater Academy")
    assert bw["tmdb_genres"] == ["Drama"] and bw["tmdb_poster"] == "/p1001.png"
    assert bw["tmdb_cast"][0]["name"] == "Mara Lindqvist"
    assert bw["tmdb_recommendations"]
    assert by_title(world["db"], "The Film")["tmdb_runtime"] == 105


def test_duplicates_are_reported_not_deleted(world):
    bw = by_title(world["db"], "Brightwater Academy")
    assert bw["duplicates"], "episode 3 exists twice"
    assert bw["wasted_bytes"] > 0
    for e in bw["entries"]:
        assert os.path.exists(e["path"]), "consolidation never touches files"


def test_missing_episodes_are_computed_from_the_tmdb_season_lists(world):
    bw = by_title(world["db"], "Brightwater Academy")
    missing = {(m["season"], m["episode"]) for m in bw.get("missing_episodes", [])}
    assert (1, 5) in missing, "season 1 has five episodes on TMDB and the library has four"
    assert (1, 1) not in missing and (1, 4) not in missing
    assert {m["season"] for m in bw.get("missing_seasons", [])} <= {2}


def test_the_pipeline_is_idempotent(world):
    before = json.load(open(world["data"] / "media_db.json", encoding="utf-8"))
    world["run"]("scan_media.py")
    world["run"]("enrich_tmdb.py")
    world["run"]("consolidate.py")
    after = json.load(open(world["data"] / "media_db.json", encoding="utf-8"))
    assert [(s["title"], s["tmdb_id"] if "tmdb_id" in s else None, len(s["entries"])) for s in after] == \
           [(s["title"], s["tmdb_id"] if "tmdb_id" in s else None, len(s["entries"])) for s in before]


def test_a_season_whose_titles_match_nothing_is_not_trusted(world):
    nf = by_title(world["db"], "Night Ferry")
    assert nf["tmdb_id"] == 1002
    assert nf["missing_episodes"] == [], "a likely wrong match must not generate a to-do list"


def test_finished_titles_are_served_from_the_cache_on_the_second_run(world, tmdb):
    before = len(tmdb.requests)
    world["run"]("enrich_tmdb.py")
    refetched = [r for r in tmdb.requests[before:] if r in ("/3/tv/1002", "/3/movie/2001", "/3/movie/2003")]
    assert refetched == [], f"cached, finished titles must not be fetched again: {refetched}"


def test_the_rendered_page_shows_the_result(world):
    sys.path.insert(0, os.path.join(ROOT, "app"))
    import render_engine
    html = render_engine.render_page(world["db"], {"watched": {}})
    assert "Brightwater Academy" in html and "Second Summit" in html
    assert "@@" not in html

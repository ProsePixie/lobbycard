"""
In-place rename safety and the naming rules.

rename_queue_inplace._rename_one() renames a video (and only its matching
subtitle files) inside its own release folder. It must never overwrite, never
touch other files, and only rename the folder when that folder holds a single
release.
"""
import os

import autofile_naming as naming
import rename_queue_inplace as rq


# ------------------------------------------------------------ _rename_one --

def make(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"x")
    return folder


def test_renames_file_and_subtitle_and_folds_the_folder(tmp_path):
    rel = make(tmp_path / "Some.Show.S01E01.1080p-GRP", "Some.Show.S01E01.1080p-GRP.mkv",
               "Some.Show.S01E01.1080p-GRP.srt", "readme.nfo")
    video = str(rel / "Some.Show.S01E01.1080p-GRP.mkv")
    log = []
    renamed, folder_move, skipped = rq._rename_one(video, "Some Show - S01E01 - Pilot.mkv", str(rel), log, set(), set())
    new_dir = tmp_path / "Some Show - S01E01 - Pilot"
    assert renamed and not skipped
    assert folder_move == (str(rel), str(new_dir))
    assert (new_dir / "Some Show - S01E01 - Pilot.mkv").exists()
    assert (new_dir / "Some Show - S01E01 - Pilot.srt").exists(), "matching subtitle renamed with it"
    assert (new_dir / "readme.nfo").exists(), "unrelated files are never touched"


def test_never_overwrites_an_existing_target(tmp_path):
    rel = make(tmp_path / "rel", "a.mkv", "Target.mkv")
    renamed, _, skipped = rq._rename_one(str(rel / "a.mkv"), "Target.mkv", str(rel), [], set(), set())
    assert not renamed and skipped
    assert (rel / "a.mkv").exists() and (rel / "Target.mkv").exists()


def test_two_files_cannot_claim_the_same_target(tmp_path):
    folder = make(tmp_path / "pack", "a.mkv", "b.mkv")
    seen = set()
    r1 = rq._rename_one(str(folder / "a.mkv"), "Same.mkv", str(folder), [], seen, set())
    r2 = rq._rename_one(str(folder / "b.mkv"), "Same.mkv", str(folder), [], seen, set())
    assert r1[0] and not r2[0] and r2[2], "the first wins, the second is skipped as a collision"
    assert (folder / "b.mkv").exists()


def test_a_folder_with_two_videos_is_not_renamed(tmp_path):
    rel = make(tmp_path / "Season Pack", "e1.mkv", "e2.mkv")
    renamed, folder_move, _ = rq._rename_one(str(rel / "e1.mkv"), "Show - S01E01.mkv", str(rel), [], set(), set())
    assert renamed and folder_move is None
    assert rel.is_dir() and (rel / "Show - S01E01.mkv").exists()


def test_folder_rename_is_skipped_on_a_name_collision(tmp_path):
    rel = make(tmp_path / "rel", "a.mkv")
    (tmp_path / "Show - S01E01").mkdir()
    renamed, folder_move, _ = rq._rename_one(str(rel / "a.mkv"), "Show - S01E01.mkv", str(rel), [], set(), set())
    assert renamed and folder_move is None and rel.is_dir()


def test_already_correct_name_is_a_no_op(tmp_path):
    rel = make(tmp_path / "Show - S01E01", "Show - S01E01.mkv")
    renamed, folder_move, skipped = rq._rename_one(str(rel / "Show - S01E01.mkv"), "Show - S01E01.mkv", str(rel), [], set(), set())
    assert (renamed, folder_move, skipped) == (False, None, False)


def test_folder_safe_to_rename(tmp_path):
    assert rq.folder_safe_to_rename(str(make(tmp_path / "one", "a.mkv", "a.nfo")))
    assert not rq.folder_safe_to_rename(str(make(tmp_path / "two", "a.mkv", "b.mp4")))
    assert not rq.folder_safe_to_rename(str(tmp_path / "missing"))


# ----------------------------------------------------------------- naming --

def test_dot_tokenize_and_illegal_characters():
    assert naming.strip_illegal_chars('A: B/C?*') == "A BC"
    assert naming.dot_tokenize("The  Long_Way  Round") == "The.Long.Way.Round"
    assert naming.dot_tokenize("..a..b..") == "a.b"


def test_tv_and_movie_filenames():
    assert naming.tv_filename("Some Show", 1, 2, "The Pilot", "1080p", ".mkv") == "Some.Show.S01E02.The.Pilot.1080p.mkv"
    assert naming.tv_filename("Some Show", 10, 12, None, None, ".mkv") == "Some.Show.S10E12.mkv"
    assert naming.movie_filename("A Film: Part 2", 2019, "720p", ".mkv") == "A Film Part 2 (2019).720p.mkv"
    assert naming.movie_filename("Untitled", None, None, ".mp4") == "Untitled.mp4"


def test_season_folders():
    assert naming.tv_season_folder(0) == "Specials"
    assert naming.tv_season_folder(3) == "Season 03"
    assert naming.tv_dest_dir("Series", "Show: One", 2, existing_show_dir=os.path.join("x", "Show")) == os.path.join("x", "Show", "Season 02")


def test_resolution_tag_takes_the_highest_across_names():
    assert naming.extract_resolution_tag("a.720p.mkv", "b.2160p.mkv") == "2160p"
    assert naming.extract_resolution_tag("a.mkv") is None
    assert naming.extract_resolution_tag(None, "x.UHD.mkv") == "2160p"


def test_multi_episode_shapes_only():
    assert naming.looks_multi_episode("Show.S06E16-17.mkv")
    assert naming.looks_multi_episode("Show.S06E16E17.mkv")
    assert not naming.looks_multi_episode("Show.S06E16.mkv")


def test_sidecars_must_share_the_exact_stem(tmp_path):
    folder = make(tmp_path / "f", "Ep.mkv", "Ep.srt", "Ep.nfo", "Ep.en.srt", "Other.srt")
    found = [os.path.basename(p) for p in naming.find_matching_sidecars(str(folder / "Ep.mkv"))]
    assert found == ["Ep.srt"], "no .nfo, no lookalike names, no other episodes' subtitles"


def test_best_duplicate_by_resolution_then_size():
    winner, losers = naming.pick_best_duplicate([
        {"original_name": "a.720p.mkv", "size_bytes": 9},
        {"original_name": "b.1080p.mkv", "size_bytes": 1},
        {"original_name": "c.1080p.mkv", "size_bytes": 5},
    ])
    assert winner["original_name"] == "c.1080p.mkv"
    assert {l["original_name"] for l in losers} == {"a.720p.mkv", "b.1080p.mkv"}, "losers are reported, never deleted"

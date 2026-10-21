"""
Filing safety: file_queue.execute() moves files from the incoming folder into
the library. The contract these tests pin down:

  * a file is only ever *moved*, never overwritten, and nothing is deleted;
  * anything that would be replaced goes to "Pending Deletion" first;
  * a failure leaves the source exactly where it was;
  * watched/retention state follows the file to its new path.
"""
import json
import os

import pytest

import file_queue
import state_store


@pytest.fixture()
def world(tmp_path, monkeypatch):
    incoming, library, pending = tmp_path / "Incoming", tmp_path / "Library", tmp_path / "Pending Deletion"
    incoming.mkdir()
    library.mkdir()
    monkeypatch.setattr(file_queue, "PENDING_DELETION_DIR", str(pending))
    monkeypatch.setattr(state_store, "STATE_PATH", str(tmp_path / "user_state.json"))
    monkeypatch.setattr(file_queue, "update_state", state_store.update_state, raising=False)
    return {"incoming": incoming, "library": library, "pending": pending, "tmp": tmp_path}


def release(world, name="Some.Show.S01E01.1080p", with_sub=True, with_nfo=True):
    folder = world["incoming"] / name
    folder.mkdir()
    video = folder / f"{name}.mkv"
    video.write_bytes(b"video")
    if with_sub:
        (folder / f"{name}.srt").write_text("subs")
    if with_nfo:
        (folder / "release.nfo").write_text("info")
    return folder, video


def item(world, folder, video, dest_name="Some Show - S01E01 - Pilot.mkv", collision=None):
    dest_dir = world["library"] / "Series" / "Some Show" / "Season 01"
    return {
        "src_video": str(video), "src_entry_path": str(folder),
        "dest_dir": str(dest_dir), "dest_full": str(dest_dir / dest_name), "dest_name": dest_name,
        "show_title": "Some Show", "collision": collision,
    }


def test_files_a_video_with_its_subtitle_and_sweeps_the_empty_release_folder(world):
    folder, video = release(world)
    log = []
    filed, replaced, skipped, failed = file_queue.execute([item(world, folder, video)], log)
    dest = world["library"] / "Series" / "Some Show" / "Season 01"
    assert (filed, replaced, skipped, failed) == (1, 0, 0, 0)
    assert (dest / "Some Show - S01E01 - Pilot.mkv").read_bytes() == b"video"
    assert (dest / "Some Show - S01E01 - Pilot.srt").read_text() == "subs"
    assert not video.exists()
    # the leftover release folder held only disposable files: moved, not deleted
    assert not folder.exists()
    assert (world["pending"] / "Some Show" / folder.name / "release.nfo").read_text() == "info"


def test_never_overwrites_a_file_already_at_the_destination(world):
    folder, video = release(world)
    it = item(world, folder, video)
    os.makedirs(it["dest_dir"])
    with open(it["dest_full"], "wb") as f:
        f.write(b"precious")
    filed, _, skipped, _ = file_queue.execute([it], [])
    assert (filed, skipped) == (0, 1)
    assert open(it["dest_full"], "rb").read() == b"precious"
    assert video.read_bytes() == b"video", "the source stays in the queue"


def test_keeps_the_existing_copy_unless_told_to_replace(world):
    folder, video = release(world)
    existing = world["library"] / "old.mkv"
    existing.write_bytes(b"better")
    it = item(world, folder, video, collision={"existing": str(existing), "action": "keep_existing"})
    filed, replaced, skipped, _ = file_queue.execute([it], [])
    assert (filed, replaced, skipped) == (0, 0, 1)
    assert existing.read_bytes() == b"better" and video.exists()


def test_replace_moves_the_old_copy_to_pending_deletion_first(world):
    folder, video = release(world)
    existing = world["library"] / "old.mkv"
    existing.write_bytes(b"worse")
    it = item(world, folder, video, collision={"existing": str(existing), "action": "replace"})
    filed, replaced, _, _ = file_queue.execute([it], [])
    assert (filed, replaced) == (1, 1)
    assert not existing.exists()
    assert (world["pending"] / "Some Show" / "old.mkv").read_bytes() == b"worse", "recoverable, not deleted"
    assert open(it["dest_full"], "rb").read() == b"video"


def test_a_vanished_source_is_skipped_not_an_error(world):
    folder, video = release(world)
    it = item(world, folder, video)
    video.unlink()
    assert file_queue.execute([it], [])[:3] == (0, 0, 1)


def test_a_failure_leaves_the_source_in_place(world):
    folder, video = release(world)
    it = item(world, folder, video)
    os.makedirs(os.path.dirname(it["dest_dir"]), exist_ok=True)
    with open(it["dest_dir"], "wb") as f:  # a *file* where the season folder should be
        f.write(b"in the way")
    log = []
    filed, _, _, failed = file_queue.execute([it], log)
    assert (filed, failed) == (0, 1)
    assert video.read_bytes() == b"video"
    assert any(line.startswith("FAILED (left in place)") for line in log)


def test_a_release_folder_with_real_content_is_not_swept(world):
    folder, video = release(world)
    (folder / "extras.mkv").write_bytes(b"x")
    file_queue.execute([item(world, folder, video)], [])
    assert folder.exists() and (folder / "extras.mkv").exists()


def test_watched_state_follows_the_file(world):
    folder, video = release(world)
    it = item(world, folder, video)
    state_store.update_state(lambda s: s["watched"].__setitem__(str(folder), {"watched": True, "at": "2026-01-01T00:00:00"}))
    file_queue.execute([it], [])
    state = state_store.load_state()
    assert str(folder) not in state["watched"]
    assert state["watched"][it["dest_full"]]["watched"] is True


def test_disposable_leftover_detection():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "a.nfo"), "w").close()
        open(os.path.join(d, "a.jpg"), "w").close()
        assert file_queue._is_orphaned_leftover(d)
        open(os.path.join(d, "movie.mkv"), "w").close()
        assert not file_queue._is_orphaned_leftover(d)


def test_safe_folder_name_strips_illegal_characters():
    assert file_queue.safe_folder_name('A: B/C?') == "A_ B_C_"
    assert file_queue.safe_folder_name(None) == "Other"


def test_pending_deletion_never_clobbers_an_existing_name(world):
    first = world["tmp"] / "x" / "same.mkv"
    first.parent.mkdir()
    first.write_bytes(b"1")
    second = world["tmp"] / "y" / "same.mkv"
    second.parent.mkdir()
    second.write_bytes(b"2")
    a = file_queue._move_to_pending(str(first), "Show", [])
    b = file_queue._move_to_pending(str(second), "Show", [])
    assert a != b and open(a, "rb").read() == b"1" and open(b, "rb").read() == b"2"

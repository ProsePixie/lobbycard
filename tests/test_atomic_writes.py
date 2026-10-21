"""
Atomic-write contracts: shared data files (media_db.json, user_state.json) are
only ever replaced whole, a reader never sees a half-written file, and a
transient lock is retried rather than crashing the pipeline.
"""
import json
import os
import threading

import pytest

import atomic_write
import state_store


def test_replaces_the_destination_whole(tmp_path):
    dest = tmp_path / "db.json"
    dest.write_text('{"old": true}')
    tmp = tmp_path / "db.json.tmp"
    tmp.write_text('{"new": true}')
    atomic_write.atomic_replace(str(tmp), str(dest))
    assert json.loads(dest.read_text()) == {"new": True}
    assert not tmp.exists()


def test_retries_a_transient_lock_then_succeeds(tmp_path, monkeypatch):
    calls = []
    real = os.replace

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("another process has the file open")
        return real(a, b)

    monkeypatch.setattr(atomic_write.os, "replace", flaky)
    monkeypatch.setattr(atomic_write.time, "sleep", lambda s: None)
    src, dest = tmp_path / "a.tmp", tmp_path / "a.json"
    src.write_text("x")
    atomic_write.atomic_replace(str(src), str(dest))
    assert len(calls) == 3 and dest.read_text() == "x"


def test_gives_up_loudly_and_leaves_the_original_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_write.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("locked")))
    monkeypatch.setattr(atomic_write.time, "sleep", lambda s: None)
    dest, tmp = tmp_path / "a.json", tmp_path / "a.tmp"
    dest.write_text("original")
    tmp.write_text("new")
    with pytest.raises(PermissionError):
        atomic_write.atomic_replace(str(tmp), str(dest), attempts=3)
    assert dest.read_text() == "original", "a failed replace never damages the existing file"


def test_other_errors_are_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_write.os, "replace", lambda a, b: (_ for _ in ()).throw(FileNotFoundError("gone")))
    with pytest.raises(FileNotFoundError):
        atomic_write.atomic_replace(str(tmp_path / "nope"), str(tmp_path / "dest"))


# ------------------------------------------------------------ state store --

@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "STATE_PATH", str(tmp_path / "user_state.json"))
    return tmp_path


def test_state_survives_a_round_trip(store):
    state_store.update_state(lambda s: s["watched"].__setitem__("/a.mkv", {"watched": True, "at": "t"}))
    assert state_store.load_state()["watched"]["/a.mkv"]["watched"] is True
    assert not os.path.exists(state_store.STATE_PATH + ".tmp"), "no temp file left behind"


def test_a_corrupt_state_file_falls_back_to_defaults(store):
    with open(state_store.STATE_PATH, "w") as f:
        f.write("{ this is not json")
    state = state_store.load_state()
    assert state["watched"] == {} and state["top10"] == {"you": [], "partner": []}


def test_concurrent_updates_lose_nothing_and_never_tear_the_file(store):
    n_threads, per_thread = 8, 25
    errors = []

    def worker(t):
        try:
            for i in range(per_thread):
                state_store.update_state(lambda s, k=f"{t}-{i}": s["watched"].__setitem__(k, {"watched": True, "at": "t"}))
        except Exception as ex:  # noqa: BLE001
            errors.append(ex)

    def reader():
        for _ in range(100):
            json.loads(open(state_store.STATE_PATH, encoding="utf-8").read()) if os.path.exists(state_store.STATE_PATH) else None

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)] + [threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(state_store.load_state()["watched"]) == n_threads * per_thread, "every read-modify-write landed"


def test_recently_watched_is_most_recent_first_and_capped():
    state = {"recently_watched": []}
    for k in range(20):
        state_store.promote_recently_watched(state, f"k{k}")
    state_store.promote_recently_watched(state, "k5")
    assert state["recently_watched"][0] == "k5"
    assert len(state["recently_watched"]) == state_store.RECENTLY_WATCHED_CAP
    assert len(set(state["recently_watched"])) == len(state["recently_watched"]), "no duplicates"

"""
The first-run setup page: folder picking, the TMDB key check, the dry run,
and saving (config.json written, key kept out of it, server restarts itself).

The network is never touched: the key test is exercised with a stubbed urlopen
and a malformed key, and the live server is only ever given folders on disk.
"""
import json
import os
import sys
import tempfile
import time
import urllib.error

import pytest

from helpers import ROOT, Server, clean_env, free_port, http, page_token  # noqa: F401

sys.path.insert(0, os.path.join(ROOT, "app"))


@pytest.fixture()
def library(tmp_path):
    lib = tmp_path / "Library"
    (lib / "Series" / "Some Show" / "Season 01").mkdir(parents=True)
    (lib / "Movies").mkdir()
    for n in (1, 2, 3):
        (lib / "Series" / "Some Show" / "Season 01" / f"Some Show - S01E0{n} - Title.mkv").write_bytes(b"")
    (lib / "Movies" / "A Film (2019).mkv").write_bytes(b"")
    (lib / "Movies" / "notes.txt").write_text("not a video")
    return lib


def test_list_directory_lists_only_folders(library):
    import setup_wizard
    d = setup_wizard.list_directory(str(library))
    assert [e["name"] for e in d["entries"]] == ["Movies", "Series"]
    assert d["parent"] == str(library.parent)
    assert setup_wizard.list_directory(str(library / "missing"))["error"]


def test_dry_run_counts_and_writes_nothing(library):
    import setup_wizard
    before = sorted(p.relative_to(library) for p in library.rglob("*"))
    report = setup_wizard.dry_run([str(library)])
    info = report["folders"][0]
    assert info["videos"] == 4 and info["shows"] == 1 and info["movies"] == 1 and info["unrecognised"] == 0
    assert any("S01E01" in s["as"] for s in info["samples"])
    assert sorted(p.relative_to(library) for p in library.rglob("*")) == before


def test_dry_run_warns_about_missing_and_empty_folders(tmp_path):
    import setup_wizard
    report = setup_wizard.dry_run([str(tmp_path / "nope"), str(tmp_path)])
    assert len(report["warnings"]) == 2


def test_tmdb_key_checks(monkeypatch):
    import setup_wizard
    assert not setup_wizard.test_tmdb_key("")[0]
    ok, msg = setup_wizard.test_tmdb_key("eyJhbGciOi" + "x" * 80)
    assert not ok and "v3" in msg
    assert not setup_wizard.test_tmdb_key("not a key")[0]

    good = "0123456789abcdef" * 2

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"images": {}}'

    monkeypatch.setattr(setup_wizard.urllib.request, "urlopen", lambda req, timeout=0: Resp())
    assert setup_wizard.test_tmdb_key(good)[0]

    def deny(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
    monkeypatch.setattr(setup_wizard.urllib.request, "urlopen", deny)
    ok, msg = setup_wizard.test_tmdb_key(good)
    assert not ok and "401" in msg and good not in msg

    def offline(req, timeout=0):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(setup_wizard.urllib.request, "urlopen", offline)
    assert "internet" in setup_wizard.test_tmdb_key(good)[1]


def test_first_run_flow_saves_config_and_restarts(library, tmp_path):
    config, data = tmp_path / "config.json", tmp_path / "data"
    port, remote = free_port(), free_port()
    env = clean_env(MEDIABOX_CONFIG=str(config), MEDIABOX_DATA_DIR=str(data),
                    MEDIABOX_PORT=str(port), MEDIABOX_REMOTE_PORT=str(remote))
    srv = Server(["run.py", "--no-browser", "--port", str(port)], env)
    try:
        base = f"http://127.0.0.1:{port}"
        status, page, final = srv.wait_up(base + "/")
        assert status == 200 and final.endswith("/setup"), "first run must land on the setup page"
        h = {"X-Auth-Token": page_token(page)}

        # write routes refuse a request without the token
        assert http(base + "/api/setup/save", {"archive_root": str(library)})[0] == 403

        code, body, _ = http(base + "/api/setup/status", headers=h)
        assert code == 200 and json.loads(body)["configured"] is False

        code, body, _ = http(base + "/api/setup/dry-run", {"archive_root": str(library)}, h)
        assert code == 200 and json.loads(body)["folders"][0]["videos"] == 4

        code, body, _ = http(base + "/api/setup/save", {"archive_root": str(library / "missing")}, h)
        assert code == 400

        incoming = tmp_path / "Incoming"
        incoming.mkdir()
        code, body, _ = http(base + "/api/setup/save",
                             {"archive_root": str(library), "queue_roots": [{"label": "Downloads", "path": str(incoming)}]}, h)
        assert code == 200 and json.loads(body)["ok"]
        saved = json.loads(config.read_text(encoding="utf-8"))
        assert saved["archive_root"] == str(library)
        assert saved["queue_roots"] == [{"label": "Downloads", "path": str(incoming)}]
        assert "tmdb" not in config.read_text(encoding="utf-8").lower()

        # restart: the server exits with the restart code and run.py brings it back
        assert http(base + "/api/setup/restart", {}, h)[0] == 200
        time.sleep(2)
        status, page, final = srv.wait_up(base + "/")
        assert status == 200 and not final.endswith("/setup"), "after setup the library page is served"
    finally:
        srv.stop()

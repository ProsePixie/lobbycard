"""
The promise of the README, as a test: a fresh clone, `python run.py`, point it at a real folder
through the setup page, and see the library appear.

Against a fake TMDB (no network): the key is tested live through the setup page, the settings are
saved, the server restarts itself, the startup refresh scans and matches the folder, and the page
shows the titles with their TMDB data.
"""
import json
import os
import re
import time

from fake_tmdb import FakeTMDB
from helpers import Server, clean_env, free_port, http, page_token, ROOT


def touch(path, size=2048):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


def test_setup_then_the_library_appears(tmp_path):
    tmdb = FakeTMDB()
    lib = tmp_path / "Library"
    for e in (1, 2, 3):
        touch(f"{lib}/Series/Brightwater Academy/Season 01/Brightwater Academy - S01E0{e} - Episode {e}.mkv")
    touch(f"{lib}/Movies/The Film (2019)/The Film (2019).mkv")
    config = tmp_path / "config.json"
    port, remote = free_port(), free_port()
    env = clean_env(MEDIABOX_CONFIG=str(config), MEDIABOX_DATA_DIR=str(tmp_path / "data"), MEDIABOX_PORT=str(port),
                    MEDIABOX_REMOTE_PORT=str(remote), MEDIABOX_TMDB_API_BASE=tmdb.api_base, MEDIABOX_TMDB_IMAGE_BASE=tmdb.image_base)
    srv = Server(["run.py", "--no-browser", "--port", str(port)], env)
    base = f"http://127.0.0.1:{port}"
    try:
        _, page, final = srv.wait_up(base + "/")
        assert final.endswith("/setup")
        h = {"X-Auth-Token": page_token(page)}

        code, body, _ = http(base + "/api/setup/test-tmdb", {"key": "a" * 32}, h)
        assert code == 200 and json.loads(body)["ok"], body
        code, body, _ = http(base + "/api/setup/test-tmdb", {"key": "a" * 31}, h)
        assert not json.loads(body)["ok"]

        key = "b" * 32
        code, body, _ = http(base + "/api/setup/save", {"archive_root": str(lib), "tmdb_key": key}, h)
        assert code == 200 and json.loads(body)["ok"]
        assert key not in config.read_text(encoding="utf-8"), "the key must never be written to config.json"
        key_file = tmp_path / "data" / "tmdb_api_key.txt"
        assert key_file.read_text(encoding="utf-8").strip() == key

        http(base + "/api/setup/restart", {}, h)
        time.sleep(2)
        _, page, final = srv.wait_up(base + "/")
        assert not final.endswith("/setup")
        token = {"X-Auth-Token": page_token(page)}

        # the startup refresh runs in the background: wait for it to finish
        status = {}
        for _ in range(120):
            _, body, _ = http(base + "/api/refresh/status", headers=token)
            status = json.loads(body)
            if status.get("finished_at") and not status.get("running"):
                break
            time.sleep(0.5)
        assert status.get("finished_at"), f"the first refresh never finished: {status}"

        _, page, _ = http(base + "/")
        text = page.decode("utf-8")
        data = json.loads(re.search(r'<script id="media-data" type="application/json">(.*?)</script>', text, re.S).group(1))
        titles = {s["title"]: s for s in data}
        assert {"Brightwater Academy", "The Film"} <= set(titles)
        assert titles["Brightwater Academy"]["tmdb_matched"] and titles["Brightwater Academy"]["tmdb_id"] == 1001
        assert titles["The Film"]["tmdb_poster"] == "/p2001.png"

        # and the poster is served from the local cache, fetched from the (fake) image host
        code, img, _ = http(base + "/tmdb-image/poster/p2001.png")
        assert code == 200 and img[:4] == b"\x89PNG"
    finally:
        srv.stop()
        tmdb.close()

"""
Smoke test: the sample library builds and the server serves it, neutral and
complete. Runs against a throwaway data folder, so it never touches real data.

    python -m pytest tests/        (or)        python tests/test_demo_smoke.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from helpers import ROOT, Server, clean_env, free_port, http, page_token  # noqa: E402


def _start(data, **extra):
    port, remote = free_port(), free_port()
    env = clean_env(MEDIABOX_DEMO="1", MEDIABOX_DATA_DIR=data, MEDIABOX_PORT=str(port),
                    MEDIABOX_REMOTE_PORT=str(remote), **extra)
    subprocess.run([sys.executable, os.path.join(ROOT, "demo", "make_demo.py")], check=True, env=env, cwd=ROOT,
                   stdout=subprocess.DEVNULL)
    srv = Server([os.path.join(ROOT, "app", "web_server.py")], env)
    return srv, f"http://127.0.0.1:{port}"


def test_demo_library_builds_and_serves():
    with tempfile.TemporaryDirectory() as data:
        srv, base = _start(data)
        try:
            db = json.load(open(os.path.join(data, "media_db.json"), encoding="utf-8"))
            assert len(db) >= 10 and all(s["tmdb_matched"] for s in db)

            status, page, _ = srv.wait_up(base + "/")
            html = page.decode("utf-8")
            assert status == 200 and "The Salt Lighthouse" in html

            # neutral: the built-in name (test_repo_hygiene.py guards against leftovers)
            assert "<title>Entertainment and Talk Over It</title>" in html

            # the demo pack names the household and defines a franchise, so both show up
            assert "Alex" in html and "Sam" in html
            assert 'id="franchise-mode-toggle" type="button"  title="Coastline mode' in html or "Coastline mode" in html
            assert 'style="display:none"' not in html.split('id="franchise-mode-toggle"')[1].split(">")[0]

            status, body, _ = http(base + "/tmdb-image/poster/demoposter900001.png")
            assert status == 200 and body[:4] == b"\x89PNG"

            # mutating and status routes want the per-run token the page embeds
            token = page_token(page)
            status, body, _ = http(base + "/api/refresh/status", headers={"X-Auth-Token": token})
            assert status == 200 and json.loads(body)["running"] is False
            assert http(base + "/api/refresh/status")[0] == 403

            # the setup page is reachable but refuses to save while in demo mode
            status, setup_html, _ = http(base + "/setup")
            assert status == 200 and b"Demo mode is on" in setup_html
            status, body, _ = http(base + "/api/setup/save", {"archive_root": data}, {"X-Auth-Token": page_token(setup_html)})
            assert status == 400
        finally:
            srv.stop()


def test_optional_youtube_block_mounts_only_when_configured():
    with tempfile.TemporaryDirectory() as data:
        srv, base = _start(data)
        try:
            _, page, _ = srv.wait_up(base + "/")
            assert b'id="youtube-mode-toggle"' not in page, "off by default"
        finally:
            srv.stop()
    with tempfile.TemporaryDirectory() as data:
        srv, base = _start(data, MEDIABOX_YOUTUBE_DIR="extras/youtube")
        try:
            _, page, _ = srv.wait_up(base + "/")
            assert b'id="youtube-mode-toggle"' in page and b'id="youtube-block"' in page
        finally:
            srv.stop()


if __name__ == "__main__":
    test_demo_library_builds_and_serves()
    test_optional_youtube_block_mounts_only_when_configured()
    print("demo smoke tests passed")

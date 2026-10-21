"""The static demo is published to the web, so it must not carry anything from the machine that built it."""
import os
import subprocess
import sys

from helpers import ROOT


def test_static_demo_has_no_local_paths_and_all_images(tmp_path):
    out = tmp_path / "site"
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_static_demo.py"), "--out", str(out)], check=True,
                   stdout=subprocess.DEVNULL)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Read-only demo" in html and "@@" not in html
    for needle in (os.path.expanduser("~"), os.path.expanduser("~").replace("\\", "\\\\"), os.path.expanduser("~").replace("\\", "/"),
                   tempfile_root()):
        assert needle not in html, f"the page contains {needle!r}"
    assert "D:/Media/" in html
    for kind in ("poster", "backdrop", "rec_poster"):
        assert any((out / "images" / kind).iterdir())
    assert (out / ".nojekyll").exists()


def tempfile_root():
    import tempfile
    return tempfile.gettempdir()

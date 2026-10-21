"""Child process of build_static_demo.py: renders the page from a demo data folder to stdout.

    python tools/_render_static.py <data dir> <notice html file>

Every path under the demo library is rewritten to a neutral one first, so the published
page never contains the (temporary) folder it was built in.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "app"), os.path.join(ROOT, "pipeline")]

import render_engine  # noqa: E402
from state_store import load_state  # noqa: E402

FAKE_ROOT = "D:/Media"


def main():
    data, notice_file = sys.argv[1], sys.argv[2]
    prefix = os.path.join(data, "library")

    def fix(v):
        if isinstance(v, str) and v.startswith(prefix):
            return FAKE_ROOT + v[len(prefix):].replace("\\", "/")
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {fix(k): fix(x) for k, x in v.items()}
        return v

    with open(os.path.join(data, "media_db.json"), encoding="utf-8") as f:
        shows = fix(json.load(f))
    with open(notice_file, encoding="utf-8") as f:
        notice = f.read()
    html = render_engine.render_page(shows, fix(load_state()), server_mode=False, image_base="images", notice_html=notice)
    sys.stdout.buffer.write(html.encode("utf-8"))


if __name__ == "__main__":
    main()

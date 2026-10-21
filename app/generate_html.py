"""Thin wrapper: loads media_db.json + user_state.json, calls
render_engine.render_page(server_mode=False), writes the static, read-only
"Media Library.html" snapshot kept outside the Scripts folder. Regenerated
by every nightly refresh (automation\\daily_refresh.ps1)."""
import json
import os
import sys

from render_engine import render_page
from state_store import load_state

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
from app_config import DATA_DIR, HTML_SNAPSHOT_PATH  # noqa: E402
DB_PATH = os.path.join(DATA_DIR, "media_db.json")
OUT_PATH = HTML_SNAPSHOT_PATH


def main():
    with open(DB_PATH, encoding="utf-8") as f:
        shows = json.load(f)
    state = load_state()

    html = render_page(shows, state, server_mode=False, auth_token="")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print("Wrote", OUT_PATH, f"({len(html)/1024:.0f} KB)")
    print("Note: this is a read-only static snapshot. Watched/retention checkboxes")
    print("are disabled here - start the local server (start_server.ps1) for the")
    print("interactive version that saves changes.")


if __name__ == "__main__":
    main()

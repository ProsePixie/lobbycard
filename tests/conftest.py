"""Test setup: an empty private config + data folder, so a developer's own
config.json / data/ is never read or written, and the project's source folders
are importable the way the app itself imports them."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SANDBOX = tempfile.mkdtemp(prefix="mediabox-tests-")

for key in [k for k in os.environ if k.startswith("MEDIABOX_")] + ["TMDB_API_KEY"]:
    os.environ.pop(key, None)
os.environ["MEDIABOX_CONFIG"] = os.path.join(_SANDBOX, "config.json")
os.environ["MEDIABOX_DATA_DIR"] = os.path.join(_SANDBOX, "data")

for sub in ("tests", "app", "pipeline", "plans", "travel"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)

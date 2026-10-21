import sys
from pathlib import Path

import pytest

# Make `import youtube` work the way the host app does it (sys.path.insert on the
# project root), without installing anything.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    """Point every data path at a throwaway dir."""
    from youtube import config

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(config, "CHANNELS_PATH", data / "channels.json")
    monkeypatch.setattr(config, "VIDEO_CACHE_PATH", data / "video_cache.json")
    monkeypatch.setattr(config, "SEEN_VIDEOS_PATH", data / "seen_videos.json")
    return tmp_path

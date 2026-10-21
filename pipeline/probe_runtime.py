"""Tiny wrapper around ffprobe for real video-file duration - used by
detect_runtime_mismatch.py to compare against TMDB's reported runtime.
Never guesses: a probe failure (missing file, corrupt/unreadable video,
ffprobe not found) returns None, never a fabricated duration.
"""
import subprocess

from app_config import ffprobe_path

# ffprobe is found through app_config: the configured path, then PATH.


def _ffprobe_path():
    return ffprobe_path() or "ffprobe"


def get_duration_seconds(video_path, timeout=20):
    ffprobe = _ffprobe_path()
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None

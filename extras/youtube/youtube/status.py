"""youtube_status() — contract symbol #4. A small dict for the host app's status
panel. A channel is only reported as failing once it has missed
`failure_alert_threshold` polls in a row (default 3).
"""
from . import catalog, config


def youtube_status():
    settings = config.load_settings()
    threshold = settings["failure_alert_threshold"]
    channels = catalog.load_channels()

    last_ok = max((c.get("last_ok_fetch") or "" for c in channels), default="") or None
    failing = [
        {"title": c.get("title"), "channel_id": c["channel_id"],
         "consecutive_failures": c.get("consecutive_failures", 0),
         "last_ok_fetch": c.get("last_ok_fetch")}
        for c in channels
        if c.get("consecutive_failures", 0) >= threshold
    ]
    try:
        new_count = len(catalog.new_videos())
    except Exception:
        new_count = None

    return {
        "channels": len(channels),
        "last_poll": last_ok,
        "new_videos_waiting": new_count,
        "failing_channels": failing,
        "failing_count": len(failing),
    }

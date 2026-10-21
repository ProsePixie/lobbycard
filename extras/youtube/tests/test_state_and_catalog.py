"""seen_videos round-trip, channel purge, and the New-view query."""
from datetime import datetime, timedelta, timezone


def test_watched_roundtrip(tmp_data):
    from youtube import state

    assert state.is_watched("abc") is False
    state.mark_watched("abc")
    assert state.is_watched("abc") is True
    state.unmark_watched("abc")
    assert state.is_watched("abc") is False


def test_purge_videos(tmp_data):
    from youtube import state

    state.mark_watched("keep1")
    state.mark_watched("drop1")
    state.mark_watched("drop2")
    state.purge_videos({"drop1", "drop2"})
    w = state.load()["watched"]
    assert set(w) == {"keep1"}


def test_new_videos_filters_watched_and_old(tmp_data):
    from youtube import catalog, state

    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(days=2)).isoformat()
    stale = (now - timedelta(days=40)).isoformat()

    catalog.save_channels([{
        "channel_id": "UC1", "title": "Chan One", "avatar_url": None,
        "added_at": fresh, "baseline_ids": [],
        "last_ok_fetch": fresh, "consecutive_failures": 0,
    }])
    catalog.save_cache({"UC1": [
        {"id": "new1", "title": "New", "published_at": fresh, "duration_sec": 600,
         "thumb_url": None, "live_origin": False, "restricted": False, "restricted_reason": None},
        {"id": "old1", "title": "Old", "published_at": stale, "duration_sec": 600,
         "thumb_url": None, "live_origin": False, "restricted": False, "restricted_reason": None},
        {"id": "seen1", "title": "Seen", "published_at": fresh, "duration_sec": 600,
         "thumb_url": None, "live_origin": False, "restricted": False, "restricted_reason": None},
    ]})
    state.mark_watched("seen1")

    rows = catalog.new_videos(now=now)
    ids = [r["id"] for r in rows]
    assert ids == ["new1"]
    assert rows[0]["channel_title"] == "Chan One"


def test_new_videos_newest_first(tmp_data):
    from youtube import catalog

    now = datetime.now(timezone.utc)
    a = (now - timedelta(days=1)).isoformat()
    b = (now - timedelta(days=3)).isoformat()
    catalog.save_channels([{"channel_id": "UC1", "title": "C", "avatar_url": None,
                            "added_at": b, "baseline_ids": [],
                            "last_ok_fetch": b, "consecutive_failures": 0}])
    catalog.save_cache({"UC1": [
        {"id": "older", "title": "o", "published_at": b, "duration_sec": 1,
         "thumb_url": None, "live_origin": False, "restricted": False, "restricted_reason": None},
        {"id": "newer", "title": "n", "published_at": a, "duration_sec": 1,
         "thumb_url": None, "live_origin": False, "restricted": False, "restricted_reason": None},
    ]})
    assert [r["id"] for r in catalog.new_videos(now=now)] == ["newer", "older"]

"""Fetch-time filtering on RSS entries: Shorts probe, future-dated premieres,
RSS parsing."""
from datetime import datetime, timedelta, timezone

from youtube import api, fetch

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _entry(**over):
    base = {"id": "vid123", "title": "A video",
            "published_at": "2026-09-08T00:00:00+00:00",
            "thumb_url": "https://i.ytimg.com/vi/vid123/hqdefault.jpg"}
    base.update(over)
    return base


def _probe(**over):
    base = {"is_short": False, "duration_sec": 600, "live_state": "ok",
            "restricted": False, "restricted_reason": None}
    base.update(over)
    return base


def test_normal_video_kept(monkeypatch):
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe())
    rec, permanent = fetch._video_record(_entry(), NOW)
    assert rec and rec["id"] == "vid123"
    assert rec["duration_sec"] == 600
    assert rec["restricted"] is False
    assert permanent is False


def test_short_dropped(monkeypatch):
    """A confirmed Short is marked for PERMANENT exclusion (folded into
    baseline_ids by the caller) — no reason to ever re-probe it again."""
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe(is_short=True))
    monkeypatch.setattr(api, "shorts_url_check", lambda vid: True)  # YouTube's own definition agrees
    rec, permanent = fetch._video_record(_entry(), NOW)
    assert rec is None and permanent is True


def test_short_dropped_by_duration_even_if_canonical_check_missed_it(monkeypatch):
    """No Shorts, ever: even if the canonical-URL signal says "not a Short"
    (e.g. a markup change broke that check), a duration at/under
    shorts_max_sec still drops it. Two independent signals, either one wins.
    Also permanent, same as the canonical-detected case."""
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe(is_short=False, duration_sec=45))
    monkeypatch.setattr(api, "shorts_url_check", lambda vid: True)
    rec, permanent = fetch._video_record(_entry(), NOW, {"shorts_max_sec": 60})
    assert rec is None and permanent is True


def test_upcoming_and_live_skipped(monkeypatch):
    """Not permanent — a premiere airs and a live stream finishes, so these
    ids should keep being reconsidered on later polls."""
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe(live_state="upcoming"))
    rec, permanent = fetch._video_record(_entry(), NOW)
    assert rec is None and permanent is False
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe(live_state="live"))
    rec, permanent = fetch._video_record(_entry(), NOW)
    assert rec is None and permanent is False


def test_probe_failure_defers_video(monkeypatch):
    """Policy: no Shorts, ever — including on uncertainty. A probe failure
    means Short-status is unconfirmed, so the video is NOT cached/shown (it
    stays a "new" id and gets retried on the next poll) rather than kept
    optimistically with duration 0. Not permanent — it might succeed later."""
    monkeypatch.setattr(api, "video_probe", lambda vid: {})
    rec, permanent = fetch._video_record(_entry(), NOW)
    assert rec is None and permanent is False


def test_restricted_flagged(monkeypatch):
    monkeypatch.setattr(api, "video_probe",
                        lambda vid: _probe(restricted=True, restricted_reason="age-restricted"))
    rec, permanent = fetch._video_record(_entry(), NOW)
    assert rec and rec["restricted"] and rec["restricted_reason"] == "age-restricted"
    assert permanent is False


def test_future_dated_premiere_skipped(monkeypatch):
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe())
    future = (NOW + timedelta(days=2)).isoformat()
    rec, permanent = fetch._video_record(_entry(published_at=future), NOW)
    assert rec is None and permanent is False


def test_rss_parse():
    xml = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns:media="http://search.yahoo.com/mrss/">
      <title>Cool Channel</title>
      <yt:channelId>UCabcabcabcabcabcabcab1</yt:channelId>
      <entry>
        <yt:videoId>AAA111</yt:videoId>
        <title>First</title>
        <published>2026-09-08T12:00:00+00:00</published>
        <media:group>
          <media:thumbnail url="https://i2.ytimg.com/vi/AAA111/hqdefault.jpg"/>
        </media:group>
      </entry>
      <entry>
        <yt:videoId>BBB222</yt:videoId>
        <title>Second</title>
        <published>2026-09-07T12:00:00+00:00</published>
      </entry>
    </feed>"""

    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return url, xml, 200

    import youtube.api as apimod
    orig = apimod._get
    apimod._get = fake_get
    try:
        entries, title = apimod.fetch_feed("UCabcabcabcabcabcabcab1", want_feed_title=True)
    finally:
        apimod._get = orig

    assert "channel_id=UCabcabcabcabcabcabcab1" in captured["url"]
    assert title == "Cool Channel"
    assert [e["id"] for e in entries] == ["AAA111", "BBB222"]
    assert entries[0]["thumb_url"].endswith("AAA111/hqdefault.jpg")
    # missing media:thumbnail falls back to the canonical i.ytimg URL
    assert entries[1]["thumb_url"] == "https://i.ytimg.com/vi/BBB222/hqdefault.jpg"


def test_video_probe_parses_watch_page(monkeypatch):
    # Every fixture carries the "videoId":"x" marker api.video_probe now
    # requires before it trusts anything else on the page (a bot-check/
    # consent interstitial can come back as a normal 200 with none of these
    # real fields — see test_video_probe_rejects_page_without_marker below).
    marker = '"videoId":"x"'
    short_html = ('<link rel="canonical" href="https://www.youtube.com/shorts/x">'
                  f'...{marker}...{{"lengthSeconds":"36"}}...').encode()
    long_html = ('<link rel="canonical" href="https://www.youtube.com/watch?v=x">'
                 f'...{marker}...{{"lengthSeconds":"888"}}...').encode()
    upcoming_html = ('<link rel="canonical" href="https://www.youtube.com/watch?v=x">'
                     f'...{marker}...{{"isUpcoming":true}}...').encode()
    agegate_html = ('<link rel="canonical" href="https://www.youtube.com/watch?v=x">'
                    f'...{marker}...' + '"status":"LOGIN_REQUIRED"...').encode()

    monkeypatch.setattr(api, "_get", lambda url, **kw: (url, short_html, 200))
    p = api.video_probe("x")
    assert p["is_short"] is True and p["duration_sec"] == 36

    monkeypatch.setattr(api, "_get", lambda url, **kw: (url, long_html, 200))
    p = api.video_probe("x")
    assert p["is_short"] is False and p["duration_sec"] == 888 and p["live_state"] == "ok"

    monkeypatch.setattr(api, "_get", lambda url, **kw: (url, upcoming_html, 200))
    assert api.video_probe("x")["live_state"] == "upcoming"

    monkeypatch.setattr(api, "_get", lambda url, **kw: (url, agegate_html, 200))
    p = api.video_probe("x")
    assert p["restricted"] is True and p["restricted_reason"] == "age-restricted"


def test_video_probe_network_failure_returns_empty(monkeypatch):
    def boom(url, **kw):
        raise api.APIError("down")
    monkeypatch.setattr(api, "_get", boom)
    assert api.video_probe("x", retries=1, backoff=0.01) == {}


def test_video_probe_rejects_page_without_marker(monkeypatch):
    """A bot-check/consent interstitial can come back as a normal HTTP 200
    that doesn't actually contain the requested video's data. Trusting
    is_short / restricted off that page is exactly how a burst of unpaced
    probes once mis-filed real Shorts as normal uploads (and mis-flagged
    normal uploads as age-restricted) — so a 200 with no videoId marker for
    this id must fail open (retry, then {}) rather than be read at all."""
    wrong_page = b'<link rel="canonical" href="https://www.youtube.com/watch?v=x">no marker here'
    monkeypatch.setattr(api, "_get", lambda url, **kw: (url, wrong_page, 200))
    assert api.video_probe("x", retries=1, backoff=0.01) == {}


def test_video_probe_rejects_anti_bot_wall(monkeypatch):
    """2026-09-14 real incident: YouTube's "Sign in to confirm you're not a
    bot" wall echoes the requested videoId (passes the marker check above),
    has no lengthSeconds, and reports "status":"LOGIN_REQUIRED" — which used
    to get read as a genuine age-gate on an otherwise normal video. Must be
    rejected the same as a marker-less page (retry, then fail open), not
    trusted as a real answer."""
    marker = '"videoId":"x"'
    wall = ('<link rel="canonical" href="https://www.youtube.com/watch?v=x">'
            f'...{marker}..."status":"LOGIN_REQUIRED"...'
            '"reason":"Sign in to confirm you’re not a bot"...').encode("utf-8")
    monkeypatch.setattr(api, "_get", lambda url, **kw: (url, wall, 200))
    assert api.video_probe("x", retries=1, backoff=0.01) == {}


# --- the premiere bug -------------------------------------------------------
# A premiere is a live stream for ~30 minutes, then an ordinary video. While it
# airs its watch page carries no trustworthy duration, so it used to look like a
# Short and got buried forever. A burial is unrecoverable, so it now takes two
# independent signals, and anything that was ever live is deferred, not buried.

def test_short_needs_both_signals_before_it_is_buried(monkeypatch):
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe(is_short=True))
    monkeypatch.setattr(api, "shorts_url_check", lambda vid: None)  # could not tell
    rec, permanent = fetch._video_record(_entry(), NOW)
    assert rec is None and permanent is False, "unconfirmed: hidden but retried next poll"


def test_short_clip_that_youtube_calls_a_video_is_deferred_not_buried(monkeypatch):
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe(is_short=False, duration_sec=45))
    monkeypatch.setattr(api, "shorts_url_check", lambda vid: False)
    rec, permanent = fetch._video_record(_entry(), NOW, {"shorts_max_sec": 60})
    assert rec is None and permanent is False


def test_live_and_upcoming_are_never_shorts(monkeypatch):
    for state in ("live", "upcoming"):
        monkeypatch.setattr(api, "video_probe", lambda vid, s=state: _probe(live_state=s, duration_sec=0))
        rec, permanent = fetch._video_record(_entry(), NOW)
        assert rec is None and permanent is False


def test_premiere_still_processing_is_deferred(monkeypatch):
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe(was_live=True, live_ended=False, duration_sec=30))
    rec, permanent = fetch._video_record(_entry(), NOW)
    assert rec is None and permanent is False


def test_finished_premiere_is_a_normal_video(monkeypatch):
    monkeypatch.setattr(api, "video_probe", lambda vid: _probe(was_live=True, live_ended=True, duration_sec=1800))
    rec, permanent = fetch._video_record(_entry(), NOW)
    assert rec and rec["live_origin"] is True and permanent is False


def test_future_dated_entry_is_not_probed_at_all(monkeypatch):
    def boom(vid):
        raise AssertionError("must not probe a scheduled premiere")
    monkeypatch.setattr(api, "video_probe", boom)
    rec, permanent = fetch._video_record(_entry(published_at="2026-09-10T00:00:00+00:00"), NOW)
    assert rec is None and permanent is False

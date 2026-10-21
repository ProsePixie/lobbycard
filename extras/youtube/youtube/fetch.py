"""Channel resolve + the hourly uploads poll, on YouTube's public per-channel
RSS feed (no API key, no Google account, no billing).

Discovery is the per-channel RSS feed (cheap, ~15 ids). Each *new* id is then
enriched by one GET of its watch page (api.video_probe) — that scrape yields the
Shorts flag (canonical URL is /shorts/ vs /watch), real duration, live/premiere
state, and age/embedding restriction. One extra request per new video only.

Filtering at fetch time:
  - Shorts             -> never shown. Two independent signals, either one
    makes a video LOOK like a Short: probe.is_short (canonical URL is
    /shorts/) OR duration_sec at/under settings.shorts_max_sec. Belt and
    suspenders — a markup change breaking the canonical check shouldn't be
    enough on its own to let a Short through. But *burying* one forever
    (baseline_ids, no way back) additionally needs api.shorts_url_check()
    to agree, and a Premiere/live stream that turned into a normal video is
    never treated as a Short at all (2026-10-10: BattleBots' premiered
    episodes had been buried as "confirmed Shorts").
  - future-dated /
    upcoming premieres -> skipped until they're real past-dated videos.
  - live right now      -> skipped (picked up later as the finished VOD).
  - no other filters, no backfill.

Policy: no Shorts, ever — including on uncertainty. If the probe fails
(network hiccup, rate-limit, YouTube markup change) the video's Short status
is unknown, so it is NOT cached or shown; it stays un-added and gets retried
on every later poll (it's still a "new" id — not yet in the cache or the
baseline) until a probe actually succeeds and confirms it one way or the
other. A delayed real video is an acceptable cost; a Short slipping through
is not.

A *confirmed* Short, though, is never worth re-probing again — it's folded
into the channel's baseline_ids (the same "don't treat this id as new" list
used for pre-add-time videos) so it drops out of consideration for good
instead of costing one probe request per poll, forever, until it ages out of
the RSS feed's ~15 latest entries. 2026-09-14: that unbounded per-poll
re-probing of already-confirmed Shorts was part of what drove the request
volume that tripped YouTube's anti-bot wall in the first place.
"""
import time
from datetime import datetime, timezone

from . import api, catalog, config, state

_PROBE_PACE_SEC = 0.3  # spacing between consecutive watch-page probes in one
                        # poll, so a batch of new uploads (or reseed_all across
                        # every channel) doesn't fire enough rapid requests to
                        # trip YouTube's rate-limiting and mass-fail probes


def _noop(_msg):
    pass


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _video_record(entry, now, settings=None):
    """RSS entry dict -> (record, permanent_exclude).

    record is None if the video should be dropped/deferred (Short /
    probe-failed-so-unconfirmed / premiere-not-yet-aired / live now).

    permanent_exclude is True only for a *confirmed* Short — the watch-page
    probe AND api.shorts_url_check() both say so (the id should never be
    reconsidered again; the caller folds it into the channel's baseline_ids,
    and there is no way back out of that, so one signal is not enough). It's
    False for every other None case (unconfirmed probe,
    future premiere, still-live) because those are temporary: a probe failure
    might succeed next time, a premiere will air, a live stream will finish —
    re-probing those forever on every poll (until they age out of the RSS
    feed) is exactly the kind of unbounded request volume that helped trip
    YouTube's anti-bot wall on 2026-09-14. A confirmed Short has no such
    reason to ever be re-probed."""
    vid = entry.get("id")
    if not vid:
        return None, False

    published = _parse_iso(entry.get("published_at"))
    if published and published > now:
        return None, False  # scheduled premiere, not a real upload yet

    probe = api.video_probe(vid)
    if not probe:
        return None, False  # unconfirmed Short status -> don't show it; retried next poll
    shorts_max_sec = (settings or {}).get("shorts_max_sec", 60)
    duration = probe.get("duration_sec", 0)

    # Live / upcoming comes FIRST: a stream or premiere is never a Short, and
    # while it is airing its watch page carries no trustworthy duration.
    if probe.get("live_state") in ("upcoming", "live"):
        return None, False

    if probe.get("was_live"):
        # A Premiere / live stream that has since turned into a normal video
        # (BattleBots premieres every episode at 13:00 PT, ~30 min live, then
        # it is just a video). Never a Short. Until the broadcast has ended and
        # the processed VOD reports a real length the page can't be trusted,
        # so defer - NOT bury - and look again next poll. This is the path
        # that used to bury EP13/EP15 forever as "confirmed Shorts" (found
        # 2026-10-10).
        if not probe.get("live_ended") or duration <= shorts_max_sec:
            return None, False
    elif probe.get("is_short") or (duration > 0 and duration <= shorts_max_sec):
        # The probe LOOKS like a Short. A permanent exclusion is
        # unrecoverable, so it needs YouTube's own definition of a Short
        # (/shorts/<id> doesn't redirect to /watch) to agree.
        verdict = api.shorts_url_check(vid)
        if verdict is True:
            return None, True  # both agree -> confirmed Short -> never probe this id again
        if verdict is None or duration <= shorts_max_sec:
            # Couldn't tell, or it's a not-a-Short clip of <=60s: policy still
            # hides it, but it is retried next poll rather than buried.
            return None, False
        # verdict False and a real duration: the probe's Short flag was the
        # outlier - an ordinary video, kept below.

    return {
        "id": vid,
        "title": entry.get("title") or "(untitled)",
        "published_at": entry.get("published_at"),
        "duration_sec": probe.get("duration_sec", 0),
        "thumb_url": entry.get("thumb_url"),
        "live_origin": bool(probe.get("was_live")),  # a premiere/stream that is now a normal video
        "restricted": bool(probe.get("restricted")),
        "restricted_reason": probe.get("restricted_reason"),
    }, False


def _prune(videos, retention_days, now):
    cutoff = now.timestamp() - retention_days * 86400
    keep = []
    for v in videos:
        ts = _parse_iso(v.get("published_at"))
        if ts is None or ts.timestamp() >= cutoff:
            keep.append(v)
    return keep


# --- channel management -----------------------------------------------

def add_channel(url_or_handle, log=_noop):
    """Resolve, persist, and seed. The baseline only suppresses feed entries
    OLDER than the New window (new_window_days) — so adding a channel
    immediately surfaces its unwatched uploads from the last ~14 days (Shorts /
    live still filtered), rather than showing an empty grid until the channel's
    next upload. An immediate poll_channel() enriches + caches those now."""
    info = api.resolve_channel(url_or_handle)
    if info["channel_id"] in {c["channel_id"] for c in catalog.load_channels()}:
        raise ValueError(f"channel already added: {info['title']}")

    settings = config.load_settings()
    now = datetime.now(timezone.utc)
    window_cutoff = now.timestamp() - settings["new_window_days"] * 86400

    entries = api.fetch_feed(info["channel_id"])
    baseline_ids = [
        e["id"] for e in entries
        if not _within(e.get("published_at"), window_cutoff)
    ]
    channel = {
        "channel_id": info["channel_id"],
        "title": info["title"],
        "avatar_url": info.get("avatar_url"),
        "added_at": now.isoformat(),
        "baseline_ids": baseline_ids,
        "last_ok_fetch": now.isoformat(),
        "consecutive_failures": 0,
    }

    def m(channels, cache):
        channels.append(channel)
        cache.setdefault(info["channel_id"], [])
        return channels, cache

    catalog.with_lock(m)
    seeded = 0
    try:
        seeded = poll_channel(channel, log)
    except Exception as ex:  # noqa: BLE001 - a seed failure must not fail the add
        log(f"YOUTUBE CHANNEL ADD seed poll failed for {info['title']}: {ex}")
    log(f"YOUTUBE CHANNEL ADD {info['title']} ({info['channel_id']}) "
        f"baseline={len(baseline_ids)} seeded={seeded}")
    return channel


def _within(published_at, cutoff_ts):
    ts = _parse_iso(published_at)
    return ts is not None and ts.timestamp() >= cutoff_ts


def reseed_all(log=_noop):
    """One-off: re-narrow every existing channel's baseline to only entries
    older than the New window, then poll — so channels added before the
    seed-on-add behaviour existed get their last ~14 days populated. Safe to
    run repeatedly (idempotent: already-cached / already-watched videos aren't
    re-added)."""
    settings = config.load_settings()
    cutoff = datetime.now(timezone.utc).timestamp() - settings["new_window_days"] * 86400
    total = 0
    for channel in catalog.load_channels():
        try:
            entries = api.fetch_feed(channel["channel_id"])
        except Exception as ex:  # noqa: BLE001
            log(f"YOUTUBE RESEED fetch failed for {channel.get('title')}: {ex}")
            continue
        new_baseline = [e["id"] for e in entries if not _within(e.get("published_at"), cutoff)]

        def m(channels, cache, cid=channel["channel_id"], nb=new_baseline):
            for c in channels:
                if c["channel_id"] == cid:
                    c["baseline_ids"] = nb
            return channels, cache

        catalog.with_lock(m)
        try:
            total += poll_channel({**channel, "baseline_ids": new_baseline}, log)
        except Exception as ex:  # noqa: BLE001
            log(f"YOUTUBE RESEED poll failed for {channel.get('title')}: {ex}")
    log(f"YOUTUBE RESEED done: +{total} video(s)")
    return total


def repair_buried_videos(log=_noop, channel_ids=None):
    """One-off repair for real videos an older single-signal Short check
    buried forever (2026-10-10, BattleBots: Premieres that turned into normal
    videos - EP13/EP15 - were baselined as "confirmed Shorts").

    For each baseline id, api.shorts_url_check must say a DEFINITE False
    (YouTube itself: not a Short). Those still inside the New window are taken
    out of baseline_ids and cached through the normal _video_record path;
    older real videos stay baselined - they wouldn't show anyway, and
    un-baselining them would re-probe them every poll until they age out of
    the feed (the request volume this design avoids). One paced request per
    baseline id (+1 per match), so pass `channel_ids` to limit it. Safe to
    re-run. Returns the number of videos restored."""
    settings = config.load_settings()
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - settings["new_window_days"] * 86400
    restored = 0
    for channel in catalog.load_channels():
        cid = channel["channel_id"]
        if channel_ids is not None and cid not in channel_ids:
            continue
        records, fixed_ids = [], []
        for i, vid in enumerate(channel.get("baseline_ids") or []):
            if i:
                time.sleep(_PROBE_PACE_SEC)
            if api.shorts_url_check(vid) is not False:
                continue
            probe = api.video_probe(vid)
            pub = _parse_iso(probe.get("upload_date")) if probe else None
            if not pub or pub.timestamp() < cutoff:
                continue
            entry = {
                "id": vid, "title": probe.get("title") or "(untitled)",
                "published_at": pub.astimezone(timezone.utc).isoformat(),
                "thumb_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            }
            rec, _permanent = _video_record(entry, now, settings)
            if rec:
                records.append(rec)
                fixed_ids.append(vid)

        if not records:
            continue

        def m(channels, cache, cid=cid, recs=records, ids=set(fixed_ids)):
            lst = cache.setdefault(cid, [])
            have = {v["id"] for v in lst}
            lst.extend(r for r in recs if r["id"] not in have)
            for c in channels:
                if c["channel_id"] == cid:
                    c["baseline_ids"] = [b for b in (c.get("baseline_ids") or []) if b not in ids]
            return channels, cache

        catalog.with_lock(m)
        restored += len(records)
        for r in records:
            log(f"YOUTUBE REPAIR restored {r['id']} to {channel.get('title')}: {r['title']}")
    log(f"YOUTUBE REPAIR done: restored {restored} video(s)")
    return restored


def reprobe_zero_duration(log=_noop):
    """Self-healing sweep: re-probe every cached duration_sec==0 video (paced)
    — these only exist because some past probe wasn't trustworthy (a rate-limit
    burst, or YouTube's "Sign in to confirm you're not a bot" wall, which used
    to look like a normal successful probe and get cached as if it were a
    confirmed long-form video). Policy is no Shorts ever, so an entry that
    still can't be confirmed is REMOVED, not left sitting there unconfirmed —
    same as a never-cached video, it'll be picked back up from the channel's
    RSS feed and retried on a later poll once a probe actually succeeds.
    Runs at the end of every run_poll(); safe to run repeatedly."""
    shorts_max_sec = config.load_settings().get("shorts_max_sec", 60)
    cache = catalog.load_cache()
    fixed = 0
    dropped = 0
    for channel_id, videos in list(cache.items()):
        targets = [v for v in videos if v.get("duration_sec", 0) == 0]
        for i, v in enumerate(targets):
            if i:
                time.sleep(_PROBE_PACE_SEC)
            probe = api.video_probe(v["id"])
            if not probe:
                def m(channels, cache, cid=channel_id, vid=v["id"]):
                    cache[cid] = [x for x in cache.get(cid, []) if x["id"] != vid]
                    return channels, cache
                catalog.with_lock(m)
                dropped += 1
                log(f"YOUTUBE REPROBE still unconfirmed, removed {v['id']} ({v.get('title')})")
                continue
            duration = probe.get("duration_sec", 0)
            is_short = probe.get("is_short") or (0 < duration <= shorts_max_sec)
            if is_short or probe.get("live_state") in ("upcoming", "live"):
                def m(channels, cache, cid=channel_id, vid=v["id"]):
                    cache[cid] = [x for x in cache.get(cid, []) if x["id"] != vid]
                    return channels, cache
                catalog.with_lock(m)
                dropped += 1
                log(f"YOUTUBE REPROBE dropped Short {v['id']} ({v.get('title')})")
            else:
                def m(channels, cache, cid=channel_id, vid=v["id"], p=probe):
                    for x in cache.get(cid, []):
                        if x["id"] == vid:
                            x["duration_sec"] = p.get("duration_sec", 0)
                            x["restricted"] = bool(p.get("restricted"))
                            x["restricted_reason"] = p.get("restricted_reason")
                    return channels, cache
                catalog.with_lock(m)
                fixed += 1
    log(f"YOUTUBE REPROBE done: fixed={fixed} dropped={dropped}")
    return fixed, dropped


def remove_channel(channel_id, log=_noop):
    """Purge: drop the channel, its cached videos, and its watched history."""
    removed = set()

    def m(channels, cache):
        nonlocal removed
        removed = {v["id"] for v in cache.get(channel_id, [])}
        channels = [c for c in channels if c["channel_id"] != channel_id]
        cache.pop(channel_id, None)
        return channels, cache

    catalog.with_lock(m)
    state.purge_videos(removed)
    log(f"YOUTUBE CHANNEL REMOVE {channel_id} purged {len(removed)} videos + history")


# --- the poll -----------------------------------------------------

def poll_channel(channel, log=_noop):
    """One channel. Returns the count of new videos added. Raises on fetch
    failure (the caller records the per-channel failure count)."""
    settings = config.load_settings()
    now = datetime.now(timezone.utc)
    cache = catalog.load_cache()
    known = {v["id"] for v in cache.get(channel["channel_id"], [])}
    baseline = set(channel.get("baseline_ids") or [])

    entries = api.fetch_feed(channel["channel_id"])
    added = []
    exclude_ids = []
    probed = False
    for e in entries:
        if e["id"] in known or e["id"] in baseline:
            continue
        if probed:
            time.sleep(_PROBE_PACE_SEC)
        probed = True
        rec, permanent = _video_record(e, now, settings)
        if rec:
            added.append(rec)
        elif permanent:
            exclude_ids.append(e["id"])

    def m(channels, cache):
        lst = cache.setdefault(channel["channel_id"], [])
        have = {v["id"] for v in lst}
        for rec in added:
            if rec["id"] not in have:
                lst.append(rec)
        cache[channel["channel_id"]] = _prune(lst, settings["catalog_retention_days"], now)
        for c in channels:
            if c["channel_id"] == channel["channel_id"]:
                c["last_ok_fetch"] = now.isoformat()
                c["consecutive_failures"] = 0
                if exclude_ids:
                    existing = set(c.get("baseline_ids") or [])
                    c["baseline_ids"] = sorted(existing | set(exclude_ids))
        return channels, cache

    catalog.with_lock(m)
    if added:
        log(f"YOUTUBE POLL {channel['title']}: +{len(added)} new")
    if exclude_ids:
        log(f"YOUTUBE POLL {channel['title']}: permanently excluded {len(exclude_ids)} "
            f"confirmed Short(s) — won't be re-probed again")
    return len(added)


def run_poll(log=_noop):
    """Every channel, once. A per-channel failure is isolated — its counter is
    bumped and the poll moves on. Nothing here raises out into a caller (the
    poller thread and the host app both rely on that)."""
    channels = catalog.load_channels()
    if not channels:
        return
    total_new = 0
    for channel in channels:
        try:
            total_new += poll_channel(channel, log)
        except Exception as ex:  # noqa: BLE001 - deliberate: contain every channel
            def m(chs, cache, cid=channel["channel_id"]):
                for c in chs:
                    if c["channel_id"] == cid:
                        c["consecutive_failures"] = c.get("consecutive_failures", 0) + 1
                return chs, cache
            try:
                catalog.with_lock(m)
            except Exception:
                pass
            log(f"YOUTUBE POLL FAIL {channel.get('title', channel.get('channel_id'))}: {ex}")
    if total_new:
        log(f"YOUTUBE POLL done: {total_new} new video(s) across {len(channels)} channel(s)")
    # Self-heal: normally a no-op (nothing should have duration_sec==0), but if
    # a future burst/rate-limit episode ever mis-caches an entry again (the
    # 2026-09-10 incident this guards against), every regular poll now quietly
    # corrects it instead of it silently piling up until someone notices.
    try:
        reprobe_zero_duration(log)
    except Exception as ex:  # noqa: BLE001 - never let remediation break the poll
        log(f"YOUTUBE POLL reprobe pass failed: {ex}")

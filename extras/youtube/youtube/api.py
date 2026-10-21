"""Data access: RSS + light HTML scraping. No API key, no Google account, no
billing — this is the entire uploads pipeline (channel resolve, per-channel
feed, per-video watch-page probe).

stdlib urllib only.
"""
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

_TIMEOUT = 15
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) mediabox-youtube/1.0"
_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"
_UC_RE = re.compile(r"(UC[0-9A-Za-z_-]{22})")


class APIError(RuntimeError):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _get(url, *, headers=None, method="GET", timeout=_TIMEOUT):
    hdrs = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
    if "youtube.com" in url:
        # Skip the EU cookie-consent interstitial (the user is in DE) so we get
        # the real page, not consent.youtube.com.
        hdrs["Cookie"] = "CONSENT=YES+1; SOCS=CAI"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.geturl(), resp.read(), resp.status
    except urllib.error.HTTPError as ex:
        raise APIError(f"HTTP {ex.code} for {url}", ex.code)
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise APIError(f"network error for {url}: {ex}")


# --- channel resolve (scrape) -------------------------------------------

def resolve_channel(url_or_handle):
    """Accept a channel URL, an @handle, a bare handle, a /channel/UC... URL,
    or a raw UC... id. Returns {channel_id, title, avatar_url}. Raises APIError
    if the id can't be found."""
    token = (url_or_handle or "").strip()

    # Already an id or a /channel/UC... URL -> no fetch needed for the id.
    m = _UC_RE.search(token)
    channel_id = m.group(1) if (token.startswith("UC") or "/channel/" in token) and m else None

    page_url = token if token.startswith("http") else None
    if not page_url:
        if token.startswith("@"):
            page_url = "https://www.youtube.com/" + token
        elif channel_id:
            page_url = "https://www.youtube.com/channel/" + channel_id
        else:
            page_url = "https://www.youtube.com/@" + token.lstrip("@")

    _final, body, _status = _get(page_url, headers={"Accept-Language": "en"})
    html = body.decode("utf-8", "replace")

    if not channel_id:
        m = (re.search(r'"externalId":"(UC[0-9A-Za-z_-]{22})"', html)
             or re.search(r'"channelId":"(UC[0-9A-Za-z_-]{22})"', html)
             or re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"', html))
        if not m:
            raise APIError(f"couldn't find a channel id for {url_or_handle!r}")
        channel_id = m.group(1)

    title = None
    mt = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if mt:
        title = _html_unescape(mt.group(1))
    # On a channel page, og:image is the channel avatar.
    avatar = None
    ma = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if ma:
        avatar = _html_unescape(ma.group(1))

    # Title fallback: the feed's own <title>.
    if not title:
        try:
            entries, feed_title = fetch_feed(channel_id, want_feed_title=True)
            title = feed_title or channel_id
        except APIError:
            title = channel_id

    return {"channel_id": channel_id, "title": title or channel_id, "avatar_url": avatar}


def _html_unescape(s):
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'"))


# --- per-channel RSS feed --------------------------------------------

def fetch_feed(channel_id, want_feed_title=False):
    """The channel's uploads RSS feed -> list of entry dicts (newest first, as
    the feed orders them), each {id, title, published_at, thumb_url}. ~15 most
    recent. If want_feed_title: returns (entries, feed_title)."""
    _final, body, _status = _get(_FEED_URL.format(urllib.parse.quote(channel_id)))
    try:
        root = ET.fromstring(body)
    except ET.ParseError as ex:
        raise APIError(f"bad RSS for {channel_id}: {ex}")

    entries = []
    for e in root.findall(f"{_ATOM}entry"):
        vid = e.findtext(f"{_YT}videoId")
        if not vid:
            continue
        group = e.find(f"{_MEDIA}group")
        thumb = None
        if group is not None:
            t = group.find(f"{_MEDIA}thumbnail")
            if t is not None:
                thumb = t.get("url")
        entries.append({
            "id": vid,
            "title": (e.findtext(f"{_ATOM}title") or "").strip() or "(untitled)",
            "published_at": e.findtext(f"{_ATOM}published"),
            "thumb_url": thumb or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        })
    if want_feed_title:
        return entries, (root.findtext(f"{_ATOM}title") or "").strip()
    return entries


# --- per-video probe (scrapes the watch page) ---------------------

_CANON_RE = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/(shorts|watch)')
_LEN_RE = re.compile(r'"lengthSeconds":"(\d+)"')
_UPCOMING_RE = re.compile(r'"isUpcoming":true')
_LIVENOW_RE = re.compile(r'"isLiveNow":true')
# A Premiere / live stream carries a liveBroadcastDetails block for life (its
# endTimestamp appears once the broadcast is over) - even after it has turned
# into a perfectly ordinary video. isLiveContent is NOT reliable for this
# (false on finished BattleBots premieres, confirmed 2026-10-10).
_WAS_LIVE_RE = re.compile(r'"liveBroadcastDetails"')
_LIVE_ENDED_RE = re.compile(r'"endTimestamp":"')
_UPLOAD_RE = re.compile(r'"uploadDate":"([^"]+)"')
_OGTITLE_RE = re.compile(r'<meta name="title" content="([^"]*)"')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def shorts_url_check(video_id):
    """Independent second opinion on "is this a Short?", from YouTube's own
    definition of one: /shorts/<id> stays put (HTTP 200, same video) for a
    real Short and 303-redirects to /watch?v=<id> for any normal video.

    True  -> a real Short.   False -> not a Short.   None -> couldn't tell
    (network error, rate limit, consent/bot wall, anything unexpected) -
    callers must treat None as "unknown", never as either answer.

    Why this exists: the watch-page probe alone used to be able to bury a
    real episode forever. A permanent "confirmed Short" exclusion now needs
    this check to agree. Verified 2026-10-10 on BattleBots: the 5 real
    episodes all 303'd to /watch, the other 29 baselined ids all stayed 200."""
    hdrs = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9",
            "Cookie": "CONSENT=YES+1; SOCS=CAI"}
    req = urllib.request.Request(f"https://www.youtube.com/shorts/{urllib.parse.quote(video_id)}", headers=hdrs)
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=12) as resp:
            if resp.status != 200:
                return None
            page = resp.read(600_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        if ex.code in (301, 302, 303, 307, 308) and "/watch" in (ex.headers.get("Location") or ""):
            return False
        return None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    # A 200 only counts if it's really this video's page, not a consent /
    # bot-check interstitial that happens to answer 200 for any URL.
    if f'"videoId":"{video_id}"' not in page or ("Sign in to confirm you" in page and "not a bot" in page):
        return None
    return True


def video_probe(video_id, retries=2, backoff=1.5):
    """One GET of the watch page -> the facts RSS can't give us. Returns a dict;
    an empty dict means the probe failed (even after retries) — the caller's
    policy is no Shorts ever, so an unconfirmed video is deferred (not shown)
    rather than trusted on a guess, and gets retried on the next poll.

    Retries a couple of times on failure: giving up too eagerly turns a
    transient block/rate-limit into a video that never gets a real answer
    (a video not yet cached is retried every poll, so it isn't lost — just
    delayed until a probe actually succeeds).

      {is_short: bool, duration_sec: int, live_state: 'ok'|'upcoming'|'live',
       restricted: bool, restricted_reason: str|None,
       was_live: bool (is/was a Premiere or live stream), live_ended: bool,
       upload_date: str|None, title: str|None}
    """
    marker = f'"videoId":"{video_id}"'
    h = None
    for attempt in range(retries + 1):
        try:
            _final, body, _status = _get(f"https://www.youtube.com/watch?v={video_id}", timeout=12)
        except APIError:
            body = None
        # A bot-check / consent interstitial can come back as a normal HTTP 200
        # with none of the fields we're about to trust — treat that the same as
        # a network failure (retry, then fail open) rather than reading real
        # signals off the wrong page (that's exactly how a burst of requests
        # once produced false "restricted"/non-Short results). The videoId
        # marker alone isn't enough proof: YouTube's own "Sign in to confirm
        # you're not a bot" wall (2026-09-14 — a sustained block, not just a
        # burst) *also* echoes the requested videoId, has no lengthSeconds,
        # and reports LOGIN_REQUIRED — which used to get misread as a genuine
        # age-gate on a perfectly normal video. Reject that page explicitly.
        if body is not None:
            candidate = body.decode("utf-8", "replace")
            is_bot_wall = "Sign in to confirm you" in candidate and "not a bot" in candidate
            if marker in candidate and not is_bot_wall:
                h = candidate
                break
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    if h is None:
        return {}

    canon = _CANON_RE.search(h)
    is_short = bool(canon and canon.group(1) == "shorts")

    length = _LEN_RE.search(h)
    duration = int(length.group(1)) if length else 0

    if _UPCOMING_RE.search(h):
        live_state = "upcoming"
    elif _LIVENOW_RE.search(h):
        live_state = "live"
    else:
        live_state = "ok"

    restricted, reason = False, None
    if '"status":"LOGIN_REQUIRED"' in h or '"reason":"Sign in to confirm your age"' in h:
        restricted, reason = True, "age-restricted"
    elif '"status":"UNPLAYABLE"' in h or '"playableInEmbed":false' in h:
        restricted, reason = True, "embedding-disabled"

    was_live = bool(_WAS_LIVE_RE.search(h))
    up = _UPLOAD_RE.search(h)
    ot = _OGTITLE_RE.search(h)
    return {"is_short": is_short, "duration_sec": duration,
            "live_state": live_state, "restricted": restricted, "restricted_reason": reason,
            "was_live": was_live, "live_ended": was_live and bool(_LIVE_ENDED_RE.search(h)),
            "upload_date": up.group(1) if up else None,
            "title": _html_unescape(ot.group(1)) if ot else None}

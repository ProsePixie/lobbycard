"""Shared by enrich_streaming.py / enrich_amazon.py: turn a seed entry's
``added_at`` (UTC ISO, written by the add routes) into the local-naive ISO
form scan_media.py writes into ``latest_added`` for real files.

Same format on purpose: the client sorts ``latest_added`` as a plain string
(What's New, the "recent" sort, Watch List), so a "+00:00" value would sort
2 hours off against every local-naive one."""
from datetime import datetime


def local_naive_iso(iso):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.isoformat()

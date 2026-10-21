"""start_poller() — the hourly fetch daemon. Contract symbol #3.

The host app calls this once from its startup thread, after the server binds, inside
the same try/except that wraps `import youtube` / `register_blueprint`. Every
poll cycle is itself wrapped: a throwing poll logs and sleeps to the next
cycle. Nothing here can ever reach a the host app refresh.
"""
import threading
import time

from . import config, fetch

_started = False
_start_lock = threading.Lock()


def _loop(log):
    # First cycle runs immediately on start, then every poll_interval_sec.
    while True:
        try:
            fetch.run_poll(log)
        except Exception as ex:  # noqa: BLE001 - the whole point is containment
            try:
                log(f"YOUTUBE POLLER cycle error (continuing): {ex}")
            except Exception:
                pass
        try:
            interval = int(config.load_settings()["poll_interval_sec"])
        except Exception:
            interval = 3600
        time.sleep(max(60, interval))


def start_poller(log=None):
    """Idempotent — a second call is a no-op. `log` is the callable the host app
    injects (its server.log writer); defaults to a stdout print for the
    standalone dev harness."""
    global _started
    if log is None:
        def log(msg):
            print(f"[youtube] {msg}", flush=True)
    with _start_lock:
        if _started:
            return
        _started = True
        config.ensure_dirs()
        threading.Thread(target=_loop, args=(log,), name="youtube-poller", daemon=True).start()
        log("YOUTUBE POLLER started")

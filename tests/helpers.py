"""Shared test helpers: free ports, starting/stopping the server, tiny HTTP."""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, os.path.join(ROOT, "app"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(url, data=None, headers=None, timeout=10):
    """(status, body bytes). `data` (a dict) is sent as JSON."""
    hdrs = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), url


def clean_env(**extra):
    """The environment with every MEDIABOX_* variable removed, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEDIABOX_") and k not in ("TMDB_API_KEY",)}
    env.update(extra)
    return env


class Server:
    """Runs `python run.py ...` (or app/web_server.py) in its own process group
    so the whole tree can be stopped. Never touches any port but its own."""

    def __init__(self, args, env, cwd=ROOT):
        kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": cwd, "env": env}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        self.proc = subprocess.Popen([sys.executable] + args, **kw)

    def wait_up(self, url, tries=80):
        for _ in range(tries):
            try:
                return http(url, timeout=2)
            except OSError:
                time.sleep(0.5)
        raise AssertionError("server did not come up: " + url)

    def stop(self):
        if self.proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
        else:
            import signal
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except OSError:
                pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def page_token(html):
    m = re.search(rb'(?:const AUTH_TOKEN = |const TOKEN = )"([^"]+)"', html)
    assert m, "no auth token in page"
    return m.group(1).decode()

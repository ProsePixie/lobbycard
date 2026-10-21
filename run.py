"""
Start the app.

    python run.py --demo     try it with a built-in sample library (no key, no files needed)
    python run.py            run it on your own library (first run opens a setup page)

Options:
    --port N      web port (default 5183)
    --lan         also listen on your local network, so a phone can use the remote page
    --rebuild     with --demo: regenerate the sample library first
    --no-browser  do not open the setup page automatically on first run
"""
import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
RESTART_EXIT_CODE = 75  # the setup page asks for a restart by exiting with this


def _open_browser_soon(url):
    def go():
        time.sleep(1.5)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def main():
    ap = argparse.ArgumentParser(description="Start the media library server.")
    ap.add_argument("--demo", action="store_true", help="use the built-in sample library")
    ap.add_argument("--port", type=int, help="web port (default 5183)")
    ap.add_argument("--lan", action="store_true", help="listen on your local network too")
    ap.add_argument("--rebuild", action="store_true", help="regenerate the demo library")
    ap.add_argument("--no-browser", action="store_true", help="do not open the setup page on first run")
    args = ap.parse_args()

    if sys.version_info < (3, 11):
        sys.exit("This needs Python 3.11 or newer.")
    try:
        import flask  # noqa: F401
        import PIL  # noqa: F401
    except ImportError as e:
        sys.exit(f"Missing a package ({e.name}). Run:  pip install -r requirements.txt")

    env = dict(os.environ)
    if args.demo:
        env["MEDIABOX_DEMO"] = "1"
    if args.port:
        env["MEDIABOX_PORT"] = str(args.port)
        env.setdefault("MEDIABOX_REMOTE_PORT", str(args.port + 1))
    if args.lan:
        env["MEDIABOX_HOST"] = "0.0.0.0"

    sys.path.insert(0, os.path.join(ROOT, "pipeline"))
    os.environ.update({k: v for k, v in env.items() if k.startswith("MEDIABOX_")})
    import app_config

    if app_config.DEMO:
        if args.rebuild or not os.path.isfile(os.path.join(app_config.DATA_DIR, "media_db.json")):
            print("Building the sample library...")
            subprocess.run([sys.executable, os.path.join(ROOT, "demo", "make_demo.py")], check=True, env=env)

    port = env.get("MEDIABOX_PORT", "5183")
    if app_config.CONFIGURED:
        url = f"http://127.0.0.1:{port}"
        print(f"Starting on {url}  (Ctrl+C to stop)")
    else:
        url = f"http://127.0.0.1:{port}/setup"
        print(f"No library is set up yet. Opening the setup page: {url}  (Ctrl+C to stop)")
        if not args.no_browser:
            _open_browser_soon(url)

    server = [sys.executable, os.path.join(ROOT, "app", "web_server.py")]
    try:
        while True:
            code = subprocess.call(server, env=env, cwd=ROOT)
            if code != RESTART_EXIT_CODE:
                sys.exit(code)
            print("Settings saved - restarting the server...")
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()

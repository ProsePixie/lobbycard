"""
Captures the README screenshots from demo mode.

    pip install playwright          (only needed for this script; it drives the Chrome you already have)
    python tools/capture_screenshots.py

Starts the demo on a spare port (and a second, unconfigured server for the
setup page), drives a headless Chrome and writes PNGs to docs/screenshots/.
Everything shown is the invented demo library. Nothing touches real data.
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from helpers import Server, clean_env, free_port  # noqa: E402

OUT = os.path.join(ROOT, "docs", "screenshots")


def start_demo(data):
    port, remote = free_port(), free_port()
    env = clean_env(MEDIABOX_DEMO="1", MEDIABOX_DATA_DIR=data, MEDIABOX_PORT=str(port), MEDIABOX_REMOTE_PORT=str(remote))
    subprocess.run([sys.executable, os.path.join(ROOT, "demo", "make_demo.py")], check=True, env=env, cwd=ROOT, stdout=subprocess.DEVNULL)
    srv = Server([os.path.join(ROOT, "app", "web_server.py")], env)
    srv.wait_up(f"http://127.0.0.1:{port}/")
    return srv, port, remote


def start_setup(tmp):
    port, remote = free_port(), free_port()
    env = clean_env(MEDIABOX_CONFIG=os.path.join(tmp, "config.json"), MEDIABOX_DATA_DIR=os.path.join(tmp, "data"),
                    MEDIABOX_PORT=str(port), MEDIABOX_REMOTE_PORT=str(remote))
    srv = Server([os.path.join(ROOT, "run.py"), "--no-browser", "--port", str(port)], env)
    srv.wait_up(f"http://127.0.0.1:{port}/setup")
    return srv, port


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs(OUT, exist_ok=True)
    data, tmp = tempfile.mkdtemp(), tempfile.mkdtemp()
    demo, port, remote = start_demo(data)
    setup, setup_port = start_setup(tmp)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)

            def page(width, height, scheme="dark", mobile=False):
                ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme,
                                          device_scale_factor=2 if mobile else 1, is_mobile=mobile, has_touch=mobile)
                return ctx.new_page()

            def settle(pg, ms=1500):
                pg.wait_for_load_state("networkidle")
                pg.wait_for_timeout(ms)

            # 1. the library
            pg = page(1360, 1000)
            pg.goto(f"http://127.0.0.1:{port}/")
            settle(pg)
            pg.screenshot(path=os.path.join(OUT, "library.png"))

            # 2. a title's detail view
            pg.evaluate("openModal(shows.findIndex(s => s.title === 'The Salt Lighthouse'))")
            settle(pg, 1200)
            pg.screenshot(path=os.path.join(OUT, "detail.png"))
            pg.evaluate("closeModal()")

            # 3. franchise mode
            pg.evaluate("document.getElementById('franchise-mode-toggle').click()")
            settle(pg, 1200)
            pg.screenshot(path=os.path.join(OUT, "franchise.png"))

            # 4. the setup page, after a dry run over the demo library
            pg = page(1000, 1250)
            pg.goto(f"http://127.0.0.1:{setup_port}/")
            pg.wait_for_selector("#archive")
            pg.fill("#archive", os.path.join(data, "library"))
            pg.fill("#key", "")
            pg.click("#b-dry")
            pg.wait_for_selector("#dry table")
            pg.wait_for_timeout(500)
            # show a neutral path, not the temporary folder the demo was built in
            pg.evaluate("""(real) => {
                const fake = 'D:/Media';
                document.getElementById('archive').value = fake;
                const w = document.createTreeWalker(document.getElementById('dry'), NodeFilter.SHOW_TEXT);
                for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeValue = n.nodeValue.split(real).join(fake);
            }""", os.path.join(data, "library"))
            pg.screenshot(path=os.path.join(OUT, "setup.png"), full_page=True)

            # 4b. the Settings page of a configured app: appearance with a live preview
            pg = page(1000, 1250)
            pg.goto(f"http://127.0.0.1:{port}/setup")
            pg.wait_for_selector("#a-title")
            pg.fill("#a-title", "Friday Night Pictures")
            pg.fill("#a-tag", "Snacks, then a film")
            pg.wait_for_timeout(400)
            pg.locator("#s-app").screenshot(path=os.path.join(OUT, "settings.png"))

            # 5. the phone remote (it only runs in landscape)
            pg = page(844, 390, mobile=True)
            pg.goto(f"http://127.0.0.1:{remote}/")
            settle(pg, 1800)
            pg.screenshot(path=os.path.join(OUT, "remote.png"))

            # 6. light theme
            pg = page(1360, 760, scheme="light")
            pg.goto(f"http://127.0.0.1:{port}/")
            settle(pg)
            pg.screenshot(path=os.path.join(OUT, "library-light.png"))
            browser.close()
    finally:
        demo.stop()
        setup.stop()
    print("Wrote", ", ".join(sorted(os.listdir(OUT))))


if __name__ == "__main__":
    main()

"""
Records the guided tour used in the README: a real browser driven through the
demo library, with captions and a visible cursor.

    pip install playwright           # plus Chrome or `playwright install chromium`
    python tools/record_demo.py      # writes docs/demo.gif and docs/demo.mp4

Frames come from Chrome's own screencast (no Playwright video codec download) and
ffmpeg on PATH turns them into the movie. The tour is deterministic: it uses the
invented demo library and a throwaway data folder, so it can be re-recorded any
time (after a UI change, say) with one command.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from helpers import Server, clean_env, free_port  # noqa: E402

DOCS = os.path.join(ROOT, "docs")
W, H = 1280, 720
FAKE_PATH = "D:/Media"

OVERLAY = """
(() => {
  const init = () => {
  if (window.__caption) return;
  const css = document.createElement('style');
  css.textContent = `
    #tour-caption { position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%); z-index: 2147483647;
      background: rgba(10,12,18,.92); color: #fff; padding: 12px 22px; border-radius: 12px; font: 600 22px/1.3 system-ui, sans-serif;
      border: 1px solid rgba(255,255,255,.18); box-shadow: 0 8px 30px rgba(0,0,0,.5); max-width: 82vw; text-align: center;
      opacity: 0; transition: opacity .25s; pointer-events: none; }
    #tour-caption.on { opacity: 1; }
    #tour-cursor { position: fixed; width: 22px; height: 22px; margin: -4px 0 0 -4px; z-index: 2147483647; pointer-events: none;
      border-radius: 50%; background: rgba(255,255,255,.9); border: 3px solid #E8A33D; box-shadow: 0 0 0 4px rgba(232,163,61,.25);
      transition: transform .08s; }
    #tour-cursor.down { transform: scale(.7); }`;
  document.documentElement.appendChild(css);
  const cap = document.createElement('div'); cap.id = 'tour-caption'; document.documentElement.appendChild(cap);
  const cur = document.createElement('div'); cur.id = 'tour-cursor'; cur.style.left = '-50px'; cur.style.top = '-50px';
  document.documentElement.appendChild(cur);
  window.__caption = (t) => { if (!t) { cap.classList.remove('on'); return; } cap.textContent = t; cap.classList.add('on'); };
  document.addEventListener('mousemove', e => { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; }, true);
  document.addEventListener('mousedown', () => cur.classList.add('down'), true);
  document.addEventListener('mouseup', () => cur.classList.remove('down'), true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
"""


def main():
    from playwright.sync_api import sync_playwright
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is needed on PATH to convert the recording")
    data, tmp = tempfile.mkdtemp(), tempfile.mkdtemp()
    port, remote = free_port(), free_port()
    env = clean_env(MEDIABOX_DEMO="1", MEDIABOX_DATA_DIR=data, MEDIABOX_PORT=str(port), MEDIABOX_REMOTE_PORT=str(remote))
    subprocess.run([sys.executable, os.path.join(ROOT, "demo", "make_demo.py")], check=True, env=env, cwd=ROOT, stdout=subprocess.DEVNULL)
    srv = Server([os.path.join(ROOT, "app", "web_server.py")], env)
    base = f"http://127.0.0.1:{port}"
    srv.wait_up(base + "/")
    setup_dir = tempfile.mkdtemp()
    setup_port = free_port()
    setup = Server([os.path.join(ROOT, "run.py"), "--no-browser", "--port", str(setup_port)],
                   clean_env(MEDIABOX_CONFIG=os.path.join(setup_dir, "config.json"), MEDIABOX_DATA_DIR=os.path.join(setup_dir, "data"),
                             MEDIABOX_PORT=str(setup_port), MEDIABOX_REMOTE_PORT=str(free_port())))
    setup.wait_up(f"http://127.0.0.1:{setup_port}/setup")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                browser = p.chromium.launch(channel="chrome", headless=True)
            ctx = browser.new_context(viewport={"width": W, "height": H}, color_scheme="dark")
            ctx.add_init_script(OVERLAY)
            pg = ctx.new_page()

            # The tour shows a neutral library path. The page is given FAKE_PATH; this route swaps in the
            # real temporary folder on the way to the server and swaps it back in the answer, so the
            # recording never shows where the demo library really lives.
            real = os.path.join(data, "library")

            def dry_run_route(route):
                body = json.loads(route.request.post_data or "{}")
                body["archive_root"] = real
                resp = route.fetch(post_data=json.dumps(body), headers={**route.request.headers, "content-type": "application/json"})
                text = resp.text().replace(json.dumps(real)[1:-1], FAKE_PATH)
                route.fulfill(status=resp.status, content_type="application/json", body=text)

            pg.route("**/api/setup/dry-run", dry_run_route)
            frames = []  # (timestamp, jpeg path)
            frame_dir = os.path.join(tmp, "frames")
            os.makedirs(frame_dir)
            cdp = ctx.new_cdp_session(pg)

            def on_frame(ev):
                path = os.path.join(frame_dir, f"{len(frames):06d}.jpg")
                with open(path, "wb") as f:
                    f.write(base64.b64decode(ev["data"]))
                frames.append((ev["metadata"]["timestamp"], path))
                cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

            cdp.on("Page.screencastFrame", on_frame)
            cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 82, "maxWidth": W, "maxHeight": H, "everyNthFrame": 1})

            def say(text, hold=1800):
                pg.evaluate("t => window.__caption && window.__caption(t)", text)
                pg.wait_for_timeout(hold)

            def move(selector, steps=28):
                pg.locator(selector).first.scroll_into_view_if_needed()
                pg.wait_for_timeout(150)
                box = pg.locator(selector).first.bounding_box()
                pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + min(box["height"] / 2, 30), steps=steps)
                pg.wait_for_timeout(250)

            def click(selector):
                move(selector)
                pg.mouse.down()
                pg.wait_for_timeout(90)
                pg.mouse.up()
                pg.wait_for_timeout(500)

            # 1. the library
            pg.goto(base + "/")
            pg.wait_for_load_state("networkidle")
            pg.mouse.move(640, 360)
            say("A catalog of the files you already own", 2600)
            say("What's new, what to continue, what you watched last", 2600)

            # 2. search
            click("#search")
            say("Search narrows the grid as you type", 600)
            pg.keyboard.type("tea", delay=220)
            pg.wait_for_timeout(1400)
            pg.fill("#search", "")
            pg.wait_for_timeout(500)
            pg.fill("#search", "lighthouse")
            pg.wait_for_timeout(1400)

            # 3. detail
            move("#grid > *")
            say("Open any title: cast, ratings, every episode", 500)
            pg.locator("#grid > *").first.click()
            pg.wait_for_selector("#modal-overlay.open")
            pg.wait_for_timeout(2400)
            pg.evaluate("document.querySelector('#modal-box .entries, #modal-box').scrollTo({top: 520, behavior: 'smooth'})")
            pg.wait_for_timeout(1800)

            # 4. watched marks
            pg.evaluate("document.querySelectorAll('#modal-box .season-header, #modal-box summary').forEach(h => h.click())")
            pg.wait_for_timeout(500)
            box = pg.locator("#modal-box .entry-row input[type=checkbox]:not(:checked)").first
            if box.count():
                say("Mark things watched. Saved on the server, not in the browser", 300)
                move("#modal-box .entry-row input[type=checkbox]:not(:checked)")
                box.check()
                pg.wait_for_timeout(1800)
            pg.evaluate("closeModal()")
            pg.fill("#search", "")
            pg.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            pg.wait_for_timeout(900)

            # 5. franchise mode
            say("Optional franchise mode: a saga in story order, with its own look", 400)
            click("#franchise-mode-toggle")
            pg.wait_for_timeout(2800)
            say("'What's next' knows the next title you haven't finished", 2600)
            click("#franchise-mode-toggle")
            pg.wait_for_timeout(900)

            # 6. first-run setup
            say("First run: pick your folders, test your TMDB key, look before you save", 300)
            pg.goto(f"http://127.0.0.1:{setup_port}/setup")
            pg.wait_for_selector("#archive")
            pg.wait_for_timeout(700)
            click("#archive")
            pg.keyboard.type(FAKE_PATH, delay=60)
            pg.wait_for_timeout(500)
            click("#b-dry")
            try:
                pg.wait_for_selector("#dry table", timeout=8000)
            except Exception:
                print("dry-run output:", pg.locator("#dry").inner_text(), "| archive:", pg.input_value("#archive"))
                raise
            pg.evaluate("document.getElementById('dry').scrollIntoView({behavior: 'smooth', block: 'center'})")
            say("A dry run shows what it found. Nothing is moved or written", 3200)
            pg.evaluate("() => window.__caption('')")
            pg.wait_for_timeout(600)

            cdp.send("Page.stopScreencast")
            ctx.close()
            browser.close()
        # constant-frame-rate movie from the timestamped frames
        listing = os.path.join(tmp, "frames.txt")
        with open(listing, "w") as f:
            for i, (ts, path) in enumerate(frames):
                nxt = frames[i + 1][0] if i + 1 < len(frames) else ts + 0.5
                name = path.replace(os.sep, "/")
                f.write("file '" + name + "'\n")
                f.write("duration %.4f\n" % max(0.01, nxt - ts))
            f.write("file '" + frames[-1][1].replace(os.sep, "/") + "'\n")
        video_path = os.path.join(tmp, "tour.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing, "-vf", "fps=25,format=yuv420p",
                        "-c:v", "libx264", "-crf", "20", video_path], check=True)
        os.makedirs(DOCS, exist_ok=True)
        mp4, gif = os.path.join(DOCS, "demo.mp4"), os.path.join(DOCS, "demo.gif")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video_path, "-c:v", "libx264", "-crf", "26", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-an", mp4], check=True)
        palette = os.path.join(tmp, "palette.png")
        vf = "fps=12,scale=960:-1:flags=lanczos"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video_path, "-vf", vf + ",palettegen=max_colors=96:stats_mode=diff", palette], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video_path, "-i", palette, "-lavfi",
                        vf + "[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4", gif], check=True)
        print(f"Wrote {mp4} ({os.path.getsize(mp4) // 1024} KB) and {gif} ({os.path.getsize(gif) // 1024} KB)")
    finally:
        srv.stop()
        setup.stop()


if __name__ == "__main__":
    main()

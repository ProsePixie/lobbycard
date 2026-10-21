"""
End-to-end tests in a real browser.

They need Playwright and a browser, so they are skipped when those are missing:

    pip install playwright && playwright install chromium
    python -m pytest tests/e2e

(Locally an installed Chrome is used if Playwright has no browser of its own.)
They drive the live demo server and the static build, and fail on any JavaScript
error the page raises.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from helpers import ROOT, Server, clean_env, free_port, http  # noqa: E402


@pytest.fixture(scope="session")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception:
            try:
                b = p.chromium.launch(channel="chrome", headless=True)
            except Exception as ex:  # noqa: BLE001
                pytest.skip(f"no browser available for Playwright: {ex}")
        yield b
        b.close()


@pytest.fixture(scope="module")
def live():
    """A running demo server on a throwaway data folder."""
    with tempfile.TemporaryDirectory() as data:
        port, remote = free_port(), free_port()
        env = clean_env(MEDIABOX_DEMO="1", MEDIABOX_DATA_DIR=data, MEDIABOX_PORT=str(port), MEDIABOX_REMOTE_PORT=str(remote))
        subprocess.run([sys.executable, os.path.join(ROOT, "demo", "make_demo.py")], check=True, env=env, cwd=ROOT,
                       stdout=subprocess.DEVNULL)
        srv = Server([os.path.join(ROOT, "app", "web_server.py")], env)
        try:
            srv.wait_up(f"http://127.0.0.1:{port}/")
            yield {"base": f"http://127.0.0.1:{port}", "remote": f"http://127.0.0.1:{remote}", "data": data}
        finally:
            srv.stop()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(viewport={"width": 1360, "height": 1000})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    yield pg
    ctx.close()


def settle(pg, ms=800):
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(ms)


def card_titles(pg):
    return pg.evaluate("[...document.querySelectorAll('#grid .card, #grid [data-idx]')].map(c => c.innerText.split('\\n')[0])")


def test_home_page_renders_without_javascript_errors(live, page):
    page.goto(live["base"] + "/")
    settle(page)
    assert page.title() == "Entertainment and Talk Over It"
    assert page.locator("#grid").inner_text() != ""
    assert page.errors == []


def test_search_narrows_the_grid_and_clearing_restores_it(live, page):
    page.goto(live["base"] + "/")
    settle(page)
    total = page.evaluate("document.getElementById('result-count').innerText")
    page.fill("#search", "lighthouse")
    page.wait_for_timeout(500)
    narrowed = page.evaluate("document.getElementById('result-count').innerText")
    assert narrowed != total and "1" in narrowed.split()[0]
    page.fill("#search", "")
    page.wait_for_timeout(500)
    assert page.evaluate("document.getElementById('result-count').innerText") == total
    assert page.errors == []


def test_detail_view_opens_and_closes(live, page):
    page.goto(live["base"] + "/")
    settle(page)
    page.evaluate("openModal(shows.findIndex(s => s.title === 'The Salt Lighthouse'))")
    page.wait_for_selector("#modal-overlay.open")
    assert "The Salt Lighthouse" in page.locator("#modal-box").inner_text()
    page.evaluate("closeModal()")
    page.wait_for_timeout(300)
    assert not page.evaluate("document.getElementById('modal-overlay').classList.contains('open')")
    assert page.errors == []


def test_marking_an_episode_watched_is_saved_by_the_server(live, page):
    page.goto(live["base"] + "/")
    settle(page)
    show = "Kestrel & Vane"  # a series with unwatched episodes in the demo data
    page.evaluate(f"openModal(shows.findIndex(s => s.title === {json.dumps(show)}))")
    page.wait_for_selector("#modal-overlay.open")
    # expand the first season, then tick the first unchecked episode
    page.evaluate("document.querySelectorAll('#modal-box .season-header, #modal-box summary').forEach(h => h.click())")
    page.wait_for_timeout(300)
    box = page.locator("#modal-box .entry-row input[type=checkbox]:not(:checked)").first
    path = box.get_attribute("data-path")
    box.check()
    page.wait_for_timeout(800)
    state = json.load(open(os.path.join(live["data"], "user_state.json"), encoding="utf-8"))
    assert state["watched"][path]["watched"] is True, "the checkbox must be written to user_state.json"
    page.reload()
    settle(page)
    assert page.evaluate(f"shows.find(s => s.title === {json.dumps(show)}).entries.find(e => e.path === {json.dumps(path)}).watched") is True
    assert page.errors == []


def test_franchise_mode_toggles_the_theme_and_the_whats_next_panel(live, page):
    page.goto(live["base"] + "/")
    settle(page)
    page.evaluate("localStorage.clear()")
    page.reload()
    settle(page)
    page.click("#franchise-mode-toggle")
    page.wait_for_timeout(500)
    assert page.evaluate("document.documentElement.classList.contains('franchise-mode')")
    assert "Second Summit" in page.locator("#franchise-whats-next-body").inner_text()
    page.click("#franchise-mode-toggle")
    page.wait_for_timeout(300)
    assert not page.evaluate("document.documentElement.classList.contains('franchise-mode')")
    assert page.errors == []


def test_phone_remote_page_loads_in_landscape(live, browser):
    ctx = browser.new_context(viewport={"width": 844, "height": 390}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live["remote"] + "/")
    settle(pg, 1500)
    assert "Nothing" in pg.locator("body").inner_text()
    assert errors == []
    ctx.close()


def test_the_static_build_browses_without_a_server(browser, tmp_path):
    out = tmp_path / "site"
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_static_demo.py"), "--out", str(out)], check=True,
                   stdout=subprocess.DEVNULL)
    port = free_port()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--directory", str(out), "--bind", "127.0.0.1"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=0x08000000 if os.name == "nt" else 0)
    try:
        time.sleep(1.5)
        ctx = browser.new_context(viewport={"width": 1360, "height": 1000})
        pg = ctx.new_page()
        errors, missing = [], []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("response", lambda r: missing.append(r.url) if r.status >= 400 else None)
        pg.goto(f"http://127.0.0.1:{port}/index.html")
        settle(pg, 1500)
        assert "Read-only demo" in pg.locator("body").inner_text()
        pg.evaluate("openModal(shows.findIndex(s => s.title === 'Harbor Nine'))")
        pg.wait_for_selector("#modal-overlay.open")
        settle(pg, 1200)
        assert errors == [] and missing == []
        ctx.close()
    finally:
        srv.kill()


def test_setup_page_dry_run_reports_what_it_found(browser, tmp_path):
    lib = tmp_path / "Lib" / "Series" / "A Show" / "Season 01"
    lib.mkdir(parents=True)
    for n in (1, 2):
        (lib / f"A Show - S01E0{n} - T.mkv").write_bytes(b"")
    port, remote = free_port(), free_port()
    env = clean_env(MEDIABOX_CONFIG=str(tmp_path / "config.json"), MEDIABOX_DATA_DIR=str(tmp_path / "data"),
                    MEDIABOX_PORT=str(port), MEDIABOX_REMOTE_PORT=str(remote))
    srv = Server([os.path.join(ROOT, "run.py"), "--no-browser", "--port", str(port)], env)
    try:
        srv.wait_up(f"http://127.0.0.1:{port}/setup")
        ctx = browser.new_context(viewport={"width": 1000, "height": 1200})
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{port}/")
        pg.wait_for_selector("#archive")
        assert pg.locator("#b-save").is_disabled()
        pg.fill("#archive", str(tmp_path / "Lib"))
        assert pg.locator("#b-save").is_enabled()
        pg.click("#b-dry")
        pg.wait_for_selector("#dry table")
        text = pg.locator("#dry").inner_text()
        assert "2 videos" in text.replace("\n", " ") and "A Show S01E01" in text
        pg.fill("#key", "nope")
        pg.click("#b-key")
        for _ in range(40):
            if pg.locator("#m-key").inner_text() not in ("", "Testing..."):
                break
            pg.wait_for_timeout(100)
        assert "32" in pg.locator("#m-key").inner_text()
        assert errors == []
        ctx.close()
    finally:
        srv.stop()


SAFE_VIEWS = [
    ("top10-btn", None),
    ("filemgmt-hub-btn", "file-queue-btn"),
    ("admin-hub-btn", "dashboard-toggle"),
    ("admin-hub-btn", "missing-episodes-btn"),
    ("admin-hub-btn", "library-history-btn"),
    ("admin-hub-btn", "admin-btn"),
    ("admin-hub-btn", "admin-logs-btn"),
    ("help-hub-btn", "help-btn"),
]


@pytest.mark.parametrize("hub, item", SAFE_VIEWS)
def test_every_view_opens_without_javascript_errors(live, page, hub, item):
    """Opens each screen the header offers (the ones that only look; nothing that purges or files)."""
    page.goto(live["base"] + "/")
    settle(page)
    page.click(f"#{hub}")
    if item:
        page.wait_for_selector(f"#{item}", state="visible")
        page.click(f"#{item}")
    overlay, box = ("#dashboard-overlay", "#dashboard-overlay") if item == "dashboard-toggle" else ("#modal-overlay", "#modal-box")
    page.wait_for_selector(f"{overlay}.open", timeout=8000)
    page.wait_for_timeout(600)
    assert page.locator(box).inner_text().strip() != ""
    assert page.errors == [], f"{hub}/{item}: {page.errors}"


def test_admin_tabs_all_render(live, page):
    page.goto(live["base"] + "/")
    settle(page)
    page.click("#admin-hub-btn")
    page.click("#admin-btn")
    page.wait_for_selector("#modal-overlay.open")
    tabs = page.locator("#modal-box [onclick*='switchAdminTab']")
    for i in range(tabs.count()):
        tabs.nth(i).click()
        page.wait_for_timeout(250)
    assert page.errors == []


def test_grid_and_list_views_and_theme_both_render(browser, live):
    for scheme in ("dark", "light"):
        ctx = browser.new_context(viewport={"width": 1200, "height": 900}, color_scheme=scheme)
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live["base"] + "/")
        settle(pg)
        pg.get_by_text("List", exact=True).first.click()
        pg.wait_for_timeout(400)
        pg.get_by_text("Grid", exact=True).first.click()
        pg.wait_for_timeout(400)
        assert errors == [], scheme
        ctx.close()


def test_settings_page_changes_title_subtitle_and_header_image(live, page, tmp_path):
    """The whole path a person takes: Admin menu, Settings, type, upload, save, see it on the library page."""
    from PIL import Image
    img = tmp_path / "mine.png"
    Image.new("RGB", (1600, 400), (200, 30, 90)).save(img)

    page.goto(live["base"] + "/")
    settle(page)
    before_bg = page.evaluate("getComputedStyle(document.querySelector('.app-hero')).backgroundImage.length")
    page.click("#admin-hub-btn")
    page.click("#settings-btn")
    page.wait_for_selector("#a-title")
    page.fill("#a-title", "Our Movie Night")
    page.fill("#a-tag", "Popcorn first")
    page.set_input_files("#a-file", str(img))
    page.wait_for_selector("#m-up.ok")
    assert page.locator("#pv-t").inner_text() == "Our Movie Night"
    page.click("#b-app-save")
    page.wait_for_selector("#m-app.ok")

    page.goto(live["base"] + "/")
    settle(page)
    assert page.title() == "Our Movie Night"
    assert page.locator(".app-title").inner_text() == "Our Movie Night"
    assert page.locator(".app-tagline").inner_text() == "Popcorn first"
    after_bg = page.evaluate("getComputedStyle(document.querySelector('.app-hero')).backgroundImage.length")
    assert after_bg != before_bg, "the uploaded header replaced the built-in one"

    # put everything back for the other tests
    page.goto(live["base"] + "/setup")
    page.wait_for_selector("#b-app-reset")
    page.click("#b-app-reset")
    page.wait_for_selector("#m-app.ok")
    page.goto(live["base"] + "/")
    settle(page)
    assert page.title() == "Entertainment and Talk Over It"
    assert page.errors == []

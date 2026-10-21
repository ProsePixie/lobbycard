"""
The Settings page's appearance endpoints, in-process against the real routes: change the
title and subtitle, upload a header image, see both on the library page, and be refused
when the request is not from this machine or has no token.
"""
import io
import os
import tempfile

import pytest

_DATA = tempfile.mkdtemp(prefix="mediabox-settings-")
os.environ["MEDIABOX_DATA_DIR"] = _DATA
os.environ["MEDIABOX_DEMO"] = "1"

import app_config  # noqa: E402
import branding  # noqa: E402
import web_server  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "APPEARANCE_PATH", str(tmp_path / "appearance.json"))
    monkeypatch.setattr(branding, "APPEARANCE_DIR", str(tmp_path / "appearance"))
    monkeypatch.setattr(branding, "HEADER_PATH", str(tmp_path / "appearance" / "header.jpg"))
    monkeypatch.setattr(app_config, "CONFIGURED", True)  # other test modules may have imported the config first
    web_server.app.config["TESTING"] = True
    return web_server.app.test_client()


H = lambda: {"X-Auth-Token": web_server.AUTH_TOKEN}  # noqa: E731


def png_bytes(w=1200, h=300):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 160)).save(buf, "PNG")
    return buf.getvalue()


def test_settings_page_loads_and_is_local_only(client):
    r = client.get("/setup")
    assert r.status_code == 200 and b"Appearance" in r.data and b"@@" not in r.data
    assert client.get("/setup", environ_overrides={"REMOTE_ADDR": "192.0.2.9"}).status_code == 403


def test_appearance_endpoints_need_the_token_and_a_local_client(client):
    assert client.get("/api/setup/appearance").status_code == 403
    assert client.post("/api/setup/appearance", json={"title": "x"}).status_code == 403
    r = client.post("/api/setup/appearance", json={"title": "x"}, headers=H(), environ_overrides={"REMOTE_ADDR": "192.0.2.9"})
    assert r.status_code == 403
    r = client.post("/api/setup/header", data={"image": (io.BytesIO(png_bytes()), "h.png")}, headers=H(),
                    environ_overrides={"REMOTE_ADDR": "192.0.2.9"})
    assert r.status_code == 403


def test_title_and_subtitle_show_up_on_the_library_page(client):
    r = client.post("/api/setup/appearance", json={"title": "Movie Night HQ", "tagline": "Pick something good"}, headers=H())
    assert r.status_code == 200 and r.get_json()["saved"]["title"] == "Movie Night HQ"
    page = client.get("/").get_data(as_text=True)
    assert "<title>Movie Night HQ</title>" in page
    assert 'class="app-tagline">Pick something good<' in page
    assert client.get("/manifest.webmanifest").get_json()["name"] == "Movie Night HQ"


def test_markup_in_the_title_is_escaped(client):
    client.post("/api/setup/appearance", json={"title": "<b onclick=x>Hi</b>"}, headers=H())
    page = client.get("/").get_data(as_text=True)
    assert "<b onclick=x>" not in page and "&lt;b onclick=x&gt;" in page


def test_bad_values_are_rejected_with_a_message(client):
    r = client.post("/api/setup/appearance", json={"accent": "blue"}, headers=H())
    assert r.status_code == 400 and "#RRGGBB" in r.get_json()["message"]


def test_uploaded_header_is_stored_served_and_used(client):
    assert client.get("/setup/header.jpg").status_code == 404
    default_page = client.get("/").get_data(as_text=True)
    r = client.post("/api/setup/header", data={"image": (io.BytesIO(png_bytes()), "wide.png")}, headers=H())
    assert r.status_code == 200 and r.get_json()["ok"]
    img = client.get("/setup/header.jpg")
    assert img.status_code == 200 and img.mimetype == "image/jpeg" and img.data[:2] == b"\xff\xd8"
    custom_page = client.get("/").get_data(as_text=True)
    assert custom_page != default_page and "data:image/jpeg;base64," in custom_page
    assert client.get("/api/setup/appearance", headers=H()).get_json()["has_header"] is True
    client.post("/api/setup/header/clear", json={}, headers=H())
    assert client.get("/setup/header.jpg").status_code == 404


def test_uploading_something_that_is_not_an_image_is_refused(client):
    r = client.post("/api/setup/header", data={"image": (io.BytesIO(b"<html>nope</html>"), "x.png")}, headers=H())
    assert r.status_code == 400 and "image" in r.get_json()["message"].lower()
    r = client.post("/api/setup/header", data={}, headers=H())
    assert r.status_code == 400


def test_focus_slider_value_reaches_the_stylesheet(client):
    client.post("/api/setup/appearance", json={"header_focus": 20}, headers=H())
    assert "--banner-y: 20%" in client.get("/").get_data(as_text=True)

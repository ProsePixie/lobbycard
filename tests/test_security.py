"""
Security properties of the server, checked in-process against the real routes.

    * every route that changes anything refuses a request without the per-run token
    * a wrong token or a foreign Origin is refused too
    * the unauthenticated image routes only ever serve what they should
    * the setup routes refuse anything that is not from this machine
"""
import os
import re
import tempfile

import pytest

# a throwaway data folder before the server module reads its configuration
_DATA = tempfile.mkdtemp(prefix="mediabox-sec-")
os.environ["MEDIABOX_DATA_DIR"] = _DATA
os.environ["MEDIABOX_DEMO"] = "1"

import web_server  # noqa: E402

DUMMY = {"int": 1, "string": "x", "path": "x/y"}


@pytest.fixture(scope="module")
def client():
    web_server.app.config["TESTING"] = True
    return web_server.app.test_client()


def _post_rules():
    for rule in web_server.app.url_map.iter_rules():
        if "POST" in rule.methods and rule.endpoint != "static":
            url = rule.rule
            for arg, conv in rule._converters.items():
                url = url.replace(f"<{conv.__class__.__name__.replace('Converter', '').lower()}:{arg}>", str(DUMMY.get(conv.__class__.__name__.replace("Converter", "").lower(), "x")))
                url = re.sub(rf"<(?:\w+:)?{arg}>", str(DUMMY.get(conv.__class__.__name__.replace("Converter", "").lower(), "x")), url)
            yield url


# setup routes have their own (stricter) guard and are covered in test_setup_wizard.py
POST_URLS = sorted(u for u in set(_post_rules()))


def test_there_are_mutating_routes_to_check():
    assert len(POST_URLS) > 25


@pytest.mark.parametrize("url", POST_URLS)
def test_mutating_routes_refuse_a_missing_token(client, url):
    r = client.post(url, json={})
    assert r.status_code == 403, f"{url} answered {r.status_code} without a token"


@pytest.mark.parametrize("url", POST_URLS)
def test_mutating_routes_refuse_a_wrong_token(client, url):
    r = client.post(url, json={}, headers={"X-Auth-Token": "not-the-token"})
    assert r.status_code == 403


def test_a_foreign_origin_is_refused_even_with_the_right_token(client):
    r = client.post("/api/refresh", json={}, headers={"X-Auth-Token": web_server.AUTH_TOKEN, "Origin": "http://evil.example"})
    assert r.status_code == 403


def test_status_endpoints_also_need_the_token(client):
    for url in ("/api/refresh/status", "/api/settings", "/api/server-log", "/api/shows-data"):
        assert client.get(url).status_code == 403, url


def test_the_token_is_not_predictable():
    assert len(web_server.AUTH_TOKEN) >= 32 and web_server.AUTH_TOKEN.isascii()


def test_tmdb_image_route_rejects_anything_but_plain_image_names(client):
    for path in ("/tmdb-image/poster/..%2F..%2Fconfig.json", "/tmdb-image/poster/a.php", "/tmdb-image/poster/a.png.exe",
                 "/tmdb-image/secrets/abc.png", "/tmdb-image/poster/%2e%2e%2fuser_state.json", "/tmdb-image/poster/a b.png"):
        assert client.get(path).status_code == 404, path


def test_wiki_image_route_only_serves_known_files(client):
    assert client.get("/wiki-image/../../etc/passwd").status_code == 404
    assert client.get("/wiki-image/nothing.png").status_code == 404


def test_security_headers_are_present(client):
    r = client.get("/api/ping")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-src" not in csp and "object-src" not in csp.replace("'none'", "")


def test_open_folder_and_play_only_act_on_library_entries(client):
    h = {"X-Auth-Token": web_server.AUTH_TOKEN}
    for url in ("/api/open-folder", "/api/play"):
        r = client.post(url, json={"path": os.path.abspath(os.sep)}, headers=h)
        assert r.status_code in (404, 200) and (r.status_code == 404 or not r.get_json().get("ok"))


def test_setup_routes_refuse_non_local_clients(client):
    r = client.get("/setup", environ_overrides={"REMOTE_ADDR": "192.0.2.10"})
    assert r.status_code == 403
    r = client.get("/api/setup/browse", environ_overrides={"REMOTE_ADDR": "192.0.2.10"}, headers={"X-Auth-Token": web_server.AUTH_TOKEN})
    assert r.status_code == 403


@pytest.mark.parametrize("host, ok", [
    ("localhost:5183", True), ("127.0.0.1:5183", True), ("[::1]:5183", True), ("192.168.1.20:5183", True),
    ("evil.example:5183", False), ("evil.example", False), ("127.0.0.1.evil.example", False), ("", False),
])
def test_dns_rebinding_guard(host, ok):
    assert web_server._host_allowed(host) is ok


def test_a_rebound_hostname_gets_nothing(client):
    r = client.get("/", headers={"Host": "attacker.example:5183"})
    assert r.status_code == 403
    assert client.get("/api/ping", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/ping", headers={"Host": "localhost:5183"}).status_code == 200


def test_lan_mode_allows_local_names_but_still_not_arbitrary_domains(monkeypatch):
    monkeypatch.setattr(web_server, "HOST", "0.0.0.0")
    assert web_server._host_allowed("mypc.local:5183")
    assert not web_server._host_allowed("attacker.example:5183")

"""
The page is real files now (app/templates, app/static). These tests render it and
check what a browser would be handed: every placeholder filled, the inline scripts
valid JavaScript (checked with Node when it is installed), the data block valid
JSON, and the stylesheet balanced.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

import render_engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def demo_shows():
    with tempfile.TemporaryDirectory() as data:
        env = dict(os.environ, MEDIABOX_DEMO="1", MEDIABOX_DATA_DIR=data)
        subprocess.run([sys.executable, os.path.join(ROOT, "demo", "make_demo.py")], check=True, env=env, cwd=ROOT,
                       stdout=subprocess.DEVNULL)
        with open(os.path.join(data, "media_db.json"), encoding="utf-8") as f:
            return json.load(f)


def render(shows, **kw):
    return render_engine.render_page(shows, {"watched": {}}, **kw)


def test_every_placeholder_is_filled(demo_shows):
    for kw in ({}, {"server_mode": True, "auth_token": "t"}, {"server_mode": True, "auth_token": "t", "remote_only": True}):
        assert not re.findall(r"@@[a-z_0-9:.]+@@", render(demo_shows, **kw))


def test_the_template_files_use_only_known_fields():
    page = render_engine.HTML_TEMPLATE
    fields = set(re.findall(r"@@([a-z_0-9]+)@@", page))
    assert fields, "expected placeholders in the template"
    html = render_engine.render_page([], {"watched": {}})  # raises KeyError if a field has no value
    assert "@@" not in html.replace("@@", "", 0) or not re.findall(r"@@[a-z_0-9]+@@", html)


def test_values_containing_placeholders_are_not_substituted_twice():
    assert render_engine._fill("a @@x@@ b", x="@@y@@", y="NO") == "a @@y@@ b"
    with pytest.raises(KeyError):
        render_engine._fill("@@missing@@")


def test_data_block_is_valid_json_and_complete(demo_shows):
    html = render(demo_shows)
    m = re.search(r'<script id="media-data" type="application/json">(.*?)</script>', html, re.S)
    assert m
    data = json.loads(m.group(1))
    assert len(data) == len(demo_shows)


def test_data_cannot_break_out_of_its_script_tag(demo_shows):
    nasty = [dict(demo_shows[0], title="</script><script>alert(1)</script>", tmdb_overview="<!-- x")]
    html = render(nasty)
    block = re.search(r'<script id="media-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert "</script" not in block.lower()
    assert json.loads(block)[0]["title"] == "</script><script>alert(1)</script>"


def test_stylesheet_is_balanced():
    css = open(os.path.join(ROOT, "app", "static", "app.css"), encoding="utf-8").read()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@@[a-z_0-9:.]+@@", "", css)
    assert css.count("{") == css.count("}")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_inline_scripts_are_valid_javascript(demo_shows, tmp_path):
    for name, kw in (("main", {"server_mode": True, "auth_token": "t"}), ("remote", {"server_mode": True, "auth_token": "t", "remote_only": True}),
                     ("static", {})):
        html = render(demo_shows, **kw)
        scripts = re.findall(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", html, re.S)
        assert scripts
        for i, src in enumerate(scripts):
            path = tmp_path / f"{name}-{i}.js"
            path.write_text(src, encoding="utf-8")
            r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            assert r.returncode == 0, f"{name} script {i}: {r.stderr[:600]}"


def test_branding_values_are_escaped_in_html_and_script_contexts(monkeypatch):
    monkeypatch.setattr(render_engine.branding, "_S", {"app_name": '<img src=x onerror=alert(1)> & "q"'})
    html = render_engine.render_page([], {"watched": {}})
    assert "<img src=x onerror" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "\\u003cimg" in html, "the JS copy of the name must not contain a raw < either"


def test_every_js_file_is_in_the_template_once_and_in_order():
    js_dir = os.path.join(ROOT, "app", "static", "js")
    files = sorted(f for f in os.listdir(js_dir) if f.endswith(".js"))
    assert files[0].startswith("01-") and len(files) >= 10
    joined = "\n".join(open(os.path.join(js_dir, f), encoding="utf-8").read() for f in files)
    assert render_engine.HTML_TEMPLATE.count(joined) == 1, "the page must contain the script files, whole, in name order"

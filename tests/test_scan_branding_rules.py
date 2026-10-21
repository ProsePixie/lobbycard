"""Scanner name parsing, the per-library override files, and the pack mechanism."""
import json
import os

import pytest

import branding
import library_rules
import scan_media


# ------------------------------------------------------------ name parsing --

@pytest.mark.parametrize("name, kind, title, season, episode, year", [
    ("Some.Show.S02E05.720p.HDTV.x264-GRP", "TV", "Some Show", 2, 5, None),
    ("Some Show - S01E10 - The Title", "TV", "Some Show", 1, 10, None),
    ("A.Film.2019.1080p.BluRay.x264-GRP", "Movie", "A Film", None, None, 2019),
    ("A Film (2019)", "Movie", "A Film", None, None, 2019),
])
def test_parse_folder_name(name, kind, title, season, episode, year):
    p = scan_media.parse_folder_name(name)
    assert (p["type"], p["title"], p["season"], p["episode"], p["year"]) == (kind, title, season, episode, year)


def test_unrecognisable_names_are_left_untyped_not_guessed():
    p = scan_media.parse_folder_name("holiday video final cut")
    assert p["type"] is None and p["season"] is None


def test_video_extensions_cover_the_usual_containers():
    for ext in (".mkv", ".mp4", ".avi", ".m4v", ".iso"):
        assert ext in scan_media.VIDEO_EXTENSIONS
    assert ".nfo" not in scan_media.VIDEO_EXTENSIONS and ".srt" not in scan_media.VIDEO_EXTENSIONS


def test_quality_ranks_resolution_first():
    assert scan_media.quality_score("x.2160p.mkv") > scan_media.quality_score("x.1080p.mkv") > scan_media.quality_score("x.720p.mkv")


# ----------------------------------------------------------- override files --

def test_scan_overrides_file_is_loaded_and_normalised(tmp_path, monkeypatch):
    data = {
        "episode_number_overrides": {"some show": [["the special", 0, 1]]},
        "directory_leaf_overrides": {"Season1": [["Pilot", "Some Show", "TV", 1, 1, None]]},
    }
    (tmp_path / "scan_overrides.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(scan_media, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scan_media, "EPISODE_NUMBER_OVERRIDES", {})
    monkeypatch.setattr(scan_media, "DIRECTORY_LEAF_OVERRIDES", {})
    scan_media._load_scan_overrides()
    assert scan_media.apply_episode_number_override("some show", "Some.Show.The.Special.mkv", 1, 0) == (0, 1)
    assert scan_media.apply_episode_number_override("some show", "Some.Show.Other.mkv", 1, 0) == (1, 0)
    assert scan_media.apply_episode_number_override("another show", "The.Special.mkv", 1, 0) == (1, 0)
    assert scan_media.DIRECTORY_LEAF_OVERRIDES["Season1"][0] == ("Pilot", "Some Show", "TV", 1, 1, None)


def test_missing_or_broken_override_files_mean_no_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_media, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scan_media, "EPISODE_NUMBER_OVERRIDES", {})
    scan_media._load_scan_overrides()
    (tmp_path / "scan_overrides.json").write_text("{ nope", encoding="utf-8")
    scan_media._load_scan_overrides()
    assert scan_media.EPISODE_NUMBER_OVERRIDES == {}


def test_library_rules_parse_and_ignore_junk(tmp_path, monkeypatch):
    rules = {
        "movie_to_tv_merge": {"1923": [1920, 2.5], "bad": "x"},
        "completeness_excluded_tmdb_ids": {"121": "on purpose", "x": "y"},
        "manual_covers": [["Some Disc", "https://example.invalid/c.png"], ["", "x"]],
    }
    monkeypatch.setattr(library_rules, "_RULES", rules)
    assert library_rules.movie_to_tv_merge() == {1923: (1920, 2.5)}
    assert library_rules.completeness_excluded_tmdb_ids() == {121: "on purpose"}
    assert library_rules.manual_covers() == [("Some Disc", "https://example.invalid/c.png")]
    monkeypatch.setattr(library_rules, "_RULES", {})
    assert library_rules.movie_to_tv_merge() == {}


# --------------------------------------------------------------------- packs --

@pytest.fixture()
def packs(tmp_path, monkeypatch):
    personal, default = tmp_path / "personal", tmp_path / "default"
    personal.mkdir()
    default.mkdir()
    monkeypatch.setattr(branding, "pack_dirs", lambda: [str(personal), str(default)])
    return personal, default


def test_the_first_pack_with_a_file_wins(packs):
    personal, default = packs
    (default / "banner.jpg").write_bytes(b"default")
    (default / "favicon.png").write_bytes(b"default-fav")
    (personal / "banner.jpg").write_bytes(b"mine")
    assert open(branding.pack_file("banner.jpg"), "rb").read() == b"mine"
    assert open(branding.pack_file("favicon.png"), "rb").read() == b"default-fav", "falls back per file"
    assert branding.pack_file("nothing.png") == ""


def test_asset_uri_picks_the_right_mime_by_extension(packs):
    personal, _ = packs
    (personal / "boot.png").write_bytes(b"\x89PNG")
    assert branding.asset_uri("boot").startswith("data:image/png;base64,")
    (personal / "banner.jpg").write_bytes(b"\xff\xd8")
    assert branding.asset_uri("banner").startswith("data:image/jpeg;base64,")
    assert branding.asset_uri("missing") == ""


def test_hints_labels_and_idle_lines(packs):
    personal, _ = packs
    (personal / "hints.json").write_text(json.dumps({
        "_comment": "x", "_labels": {"tips": "Tip"}, "tips": ["a", "b"], "facts": ["c"], "idle": ["quiet"], "broken": "not a list",
    }), encoding="utf-8")
    assert branding.hints() == {"tips": ["a", "b"], "facts": ["c"]}
    assert branding.hint_labels() == {"tips": "Tip", "facts": "Facts"}
    assert branding.idle_quips() == ["quiet"]


def test_idle_quips_always_have_a_fallback(packs):
    assert branding.idle_quips()


def test_franchise_parsing_drops_bad_entries_and_merges_the_theme(packs):
    personal, _ = packs
    (personal / "franchise.json").write_text(json.dumps({
        "name": "Saga", "theme": {"accent": "#fff", "unknown": "x", "bg": 5},
        "titles": [{"id": 10, "type": "movie", "release": 1}, {"id": "x", "type": "TV", "release": 2},
                   {"id": 11, "type": "TV", "release": 3, "chrono": 1}, {"type": "TV"}],
    }), encoding="utf-8")
    f = branding.franchise()
    assert f["name"] == "Saga"
    assert f["titles"] == [{"id": 10, "type": "Movie", "release": 1, "chrono": 1}, {"id": 11, "type": "TV", "release": 3, "chrono": 1}]
    assert f["theme"]["accent"] == "#fff" and f["theme"]["bg"] == "#0e1a1c", "bad values keep the default"


def test_no_franchise_when_nothing_usable(packs):
    personal, _ = packs
    assert branding.franchise() is None
    (personal / "franchise.json").write_text(json.dumps({"name": "x", "titles": []}), encoding="utf-8")
    assert branding.franchise() is None


def test_accent_css_only_when_changed(monkeypatch):
    monkeypatch.setattr(branding, "_S", {})
    assert branding.accent_css() == ""
    monkeypatch.setattr(branding, "_S", {"accent": {"light": "#2563EB", "dark": "#60A5FA"}})
    css = branding.accent_css()
    assert "#2563EB" in css and "#60A5FA" in css and "prefers-color-scheme: dark" in css
    monkeypatch.setattr(branding, "_S", {"accent": "#ff0000"})
    assert branding.accent() == ("#ff0000", "#ff0000")


def test_people_default_to_you_and_partner(monkeypatch):
    monkeypatch.setattr(branding, "_S", {})
    assert [p["id"] for p in branding.people()] == ["you", "partner"]
    assert [p["name"] for p in branding.people()] == ["You", "Partner"]
    monkeypatch.setattr(branding, "_S", {"people": ["alex", "  "]})
    people = branding.people()
    assert people[0] == {"id": "you", "name": "alex", "initial": "A"} and people[1]["name"] == "Partner"


def test_the_shipped_default_pack_is_complete():
    default = branding.PACK_DEFAULT
    for f in ("banner.jpg", "boot.jpg", "favicon.png", "app-icon-180.png", "app-icon-192.png", "app-icon-512.png", "hints.json", "pack.json"):
        assert os.path.isfile(os.path.join(default, f)), f
    hints = json.load(open(os.path.join(default, "hints.json"), encoding="utf-8"))
    assert sum(len(v) for k, v in hints.items() if isinstance(v, list)) >= 50
    assert hints["idle"]


# ------------------------------------------------------------ appearance (Settings page) --

@pytest.fixture()
def appearance_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "APPEARANCE_PATH", str(tmp_path / "appearance.json"))
    monkeypatch.setattr(branding, "APPEARANCE_DIR", str(tmp_path / "appearance"))
    monkeypatch.setattr(branding, "HEADER_PATH", str(tmp_path / "appearance" / "header.jpg"))
    monkeypatch.setattr(branding, "_S", {"app_name": "Pack Name", "tagline": "Pack tagline"})
    return tmp_path


def _png(width=800, height=600, color=(200, 40, 40)):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return buf.getvalue()


def test_appearance_choices_win_over_the_pack_and_can_be_reset(appearance_dir):
    assert branding.app_name() == "Pack Name" and branding.tagline() == "Pack tagline"
    branding.save_appearance({"title": "  My Library  ", "tagline": "Movie night", "accent": "#2563EB", "people": ["Ana", "Ben"]})
    assert branding.app_name() == "My Library" and branding.tagline() == "Movie night"
    assert branding.accent() == ("#2563eb", "#2563eb")
    assert [p["name"] for p in branding.people()] == ["Ana", "Ben"]
    assert "#2563eb" in branding.appearance_css()
    branding.save_appearance({"title": None, "tagline": None, "accent": None, "people": None})
    assert branding.app_name() == "Pack Name" and branding.tagline() == "Pack tagline"
    assert [p["name"] for p in branding.people()] == ["You", "Partner"]


def test_an_empty_subtitle_is_a_choice_not_a_reset(appearance_dir):
    branding.save_appearance({"tagline": ""})
    assert branding.tagline() == ""


def test_appearance_values_are_validated(appearance_dir):
    with pytest.raises(ValueError):
        branding.save_appearance({"accent": "red"})
    with pytest.raises(ValueError):
        branding.save_appearance({"nonsense": 1})
    branding.save_appearance({"title": "x" * 500, "header_focus": 900})
    assert len(branding.app_name()) == branding.MAX_TITLE and branding.header_focus() == 100


def test_a_broken_appearance_file_falls_back_to_the_pack(appearance_dir):
    open(branding.APPEARANCE_PATH, "w").write("{ nope")
    assert branding.app_name() == "Pack Name"


def test_header_upload_is_processed_and_used(appearance_dir):
    assert not branding.has_header()
    branding.save_header(_png(3000, 900))
    assert branding.has_header()
    from PIL import Image
    with Image.open(branding.HEADER_PATH) as im:
        assert im.format == "JPEG" and im.width == branding.HEADER_MAX_WIDTH, "stored as a JPEG, capped in width"
    assert branding.header_uri().startswith("data:image/jpeg;base64,")
    branding.clear_header()
    assert not branding.has_header()


@pytest.mark.parametrize("payload, why", [
    (b"", "empty"),
    (b"not an image at all", "garbage"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "svg is not accepted"),
    (b"GIF89a" + b"\x00" * 3, "truncated image"),
])
def test_header_upload_rejects_things_that_are_not_images(appearance_dir, payload, why):
    with pytest.raises(ValueError):
        branding.save_header(payload)
    assert not branding.has_header()


def test_header_upload_has_a_size_limit(appearance_dir):
    with pytest.raises(ValueError):
        branding.save_header(b"\x89PNG" + b"0" * (branding.HEADER_MAX_BYTES + 1))


def test_transparent_images_are_flattened(appearance_dir):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGBA", (100, 40), (0, 0, 0, 0)).save(buf, "PNG")
    branding.save_header(buf.getvalue())
    assert branding.has_header()

"""
Name, look and flavour text: everything that makes an install feel like *yours*.

All of it lives in a "pack", a plain folder. The app looks for each file in
this order and uses the first one it finds:

  1. your personal pack  (config "pack_dir" / env MEDIABOX_PACK / ./personal)
  2. the demo pack       (demo/pack, only in --demo mode)
  3. the built-in pack   (packs/default)

so a personal pack only needs the files it wants to change. A pack can hold:

  pack.json           app_name, tagline, people, field_guide_url, accent
  hints.json          loading-screen lines: {"any category": ["line", ...]}
  banner.jpg          header art (about 1600x368; .png works too)
  boot.jpg            loading-screen art
  favicon.png         browser tab icon
  app-icon-180.png    home-screen icons (also -192, -512)
  franchise.json      optional "Franchise mode": see personal.example/
  franchise-icon.png  franchise-banner.jpg   art for that mode
  youtube-banner.jpg  header art for the optional YouTube block

Whatever the packs say, the person using the app can change the title, subtitle,
accent colour, the two household names and the header image from the Settings page.
Those choices are saved in <data dir>/appearance.json and <data dir>/appearance/
header.jpg and win over the pack. They apply on the next page load, no restart.

Nothing here needs a third-party package except Pillow (header image uploads).
"""
import base64
import json
import os
import re

import app_config

PACK_DEFAULT = os.path.join(app_config.PROJECT_ROOT, "packs", "default")
PACK_DEMO = os.path.join(app_config.PROJECT_ROOT, "demo", "pack")


def _personal_pack():
    chosen = os.environ.get("MEDIABOX_PACK") or app_config.pack_dir_setting()
    if chosen:
        return chosen if os.path.isabs(chosen) else os.path.join(app_config.PROJECT_ROOT, chosen)
    return os.path.join(app_config.PROJECT_ROOT, "personal")


PACK_PERSONAL = _personal_pack()


def pack_dirs():
    """Existing pack folders, most specific first."""
    dirs = [PACK_PERSONAL]
    if app_config.DEMO:
        dirs.append(PACK_DEMO)
    dirs.append(PACK_DEFAULT)
    return [d for d in dirs if os.path.isdir(d)]


def pack_file(name):
    """Full path of `name` from the first pack that has it, else ''."""
    for d in pack_dirs():
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return ""


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (OSError, ValueError):
        return None


_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


def asset_uri(stem):
    """A pack image as a data: URI (for embedding in the page), looked up by
    name without extension (banner -> banner.jpg / .png / ...); '' if missing."""
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        path = pack_file(stem + ext)
        if path:
            with open(path, "rb") as f:
                return "data:%s;base64,%s" % (_MIME[ext], base64.b64encode(f.read()).decode("ascii"))
    return ""


def _settings():
    merged = {}
    for d in reversed(pack_dirs()):  # least specific first, so personal wins
        data = _read_json(os.path.join(d, "pack.json"))
        if isinstance(data, dict):
            merged.update(data)
    return merged


_S = _settings()

# Optional link to a how-to page; empty hides the button.
FIELD_GUIDE_URL = str(_S.get("field_guide_url") or "")
# The Field Guide button shows when there is a guide to open: a field_guide.html
# in a pack (served at /guide) or a URL.
FIELD_GUIDE_AVAILABLE = bool(FIELD_GUIDE_URL or pack_file("field_guide.html"))

DEFAULT_NAME = "Entertainment and Talk Over It"
DEFAULT_TAGLINE = "What do you want to watch tonight?"
DEFAULT_ACCENT = ("#B5750F", "#E8A33D")
MAX_TITLE = 60
MAX_TAGLINE = 120

# ------------------------------------------------------------------ appearance
# What the person using the app chose on the Settings page.
APPEARANCE_DIR = os.path.join(app_config.DATA_DIR, "appearance")
APPEARANCE_PATH = os.path.join(app_config.DATA_DIR, "appearance.json")
HEADER_PATH = os.path.join(APPEARANCE_DIR, "header.jpg")
HEADER_MAX_BYTES = 8 * 1024 * 1024
HEADER_MAX_WIDTH = 1920
HEADER_MAX_PIXELS = 40_000_000
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def appearance():
    """The saved appearance choices ({} when there are none)."""
    data = _read_json(APPEARANCE_PATH)
    return data if isinstance(data, dict) else {}


def save_appearance(updates):
    """Validate and merge `updates` into appearance.json. A key set to None (or an
    empty string) goes back to the pack's value. Returns the saved appearance."""
    cur = appearance()
    for key, value in (updates or {}).items():
        if value is None or value == [] or (value == "" and key != "tagline"):
            cur.pop(key, None)  # back to the pack's value
            continue
        if key == "title":
            cur[key] = str(value).strip()[:MAX_TITLE]
        elif key == "tagline":
            cur[key] = str(value).strip()[:MAX_TAGLINE]
        elif key == "accent":
            if not _HEX.match(str(value)):
                raise ValueError("The accent colour must look like #RRGGBB.")
            cur[key] = str(value).lower()
        elif key == "people":
            names = [str(x).strip()[:30] for x in list(value)[:2]]
            if len(names) < 2:
                names += [""] * (2 - len(names))
            cur[key] = names
        elif key == "header_focus":
            cur[key] = max(0, min(100, int(value)))
        else:
            raise ValueError("Unknown appearance setting: " + str(key))
    os.makedirs(app_config.DATA_DIR, exist_ok=True)
    tmp = APPEARANCE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)
    os.replace(tmp, APPEARANCE_PATH)
    return cur


def save_header(image_bytes):
    """Validate an uploaded image and store it as the header (a JPEG no wider than
    HEADER_MAX_WIDTH). Raises ValueError with a message fit for the user."""
    if not image_bytes:
        raise ValueError("No image was sent.")
    if len(image_bytes) > HEADER_MAX_BYTES:
        raise ValueError("That image is larger than %d MB." % (HEADER_MAX_BYTES // (1024 * 1024)))
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    try:
        probe = Image.open(BytesIO(image_bytes))
        if probe.format not in ("JPEG", "PNG", "WEBP", "GIF"):
            raise ValueError("Use a JPEG, PNG, WebP or GIF image.")
        if probe.width * probe.height > HEADER_MAX_PIXELS:
            raise ValueError("That image is too large to process. Try one under about 6000 by 6000 pixels.")
        probe.verify()
        img = Image.open(BytesIO(image_bytes))
        img.seek(0)
        img = img.convert("RGBA")
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValueError("That file is not an image this app can read.")
    flat = Image.new("RGB", img.size, (16, 18, 24))
    flat.paste(img, mask=img.split()[3])
    if flat.width > HEADER_MAX_WIDTH:
        flat = flat.resize((HEADER_MAX_WIDTH, round(flat.height * HEADER_MAX_WIDTH / flat.width)), Image.LANCZOS)
    os.makedirs(APPEARANCE_DIR, exist_ok=True)
    tmp = HEADER_PATH + ".tmp"
    flat.save(tmp, "JPEG", quality=86, optimize=True)
    os.replace(tmp, HEADER_PATH)


def clear_header():
    try:
        os.remove(HEADER_PATH)
    except OSError:
        pass


def has_header():
    return os.path.isfile(HEADER_PATH)


def header_uri():
    """The header image as a data: URI: the uploaded one, else the pack's banner."""
    if has_header():
        with open(HEADER_PATH, "rb") as f:
            return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
    return asset_uri("banner")


def header_focus():
    """Vertical position (0 top .. 100 bottom) of the part of a tall image that is kept."""
    try:
        return max(0, min(100, int(appearance().get("header_focus", 50))))
    except (TypeError, ValueError):
        return 50


def app_name():
    return str(appearance().get("title") or _S.get("app_name") or DEFAULT_NAME)


def tagline():
    a = appearance()
    if "tagline" in a:
        return str(a["tagline"])
    return str(_S.get("tagline") if "tagline" in _S else DEFAULT_TAGLINE)


def people():
    """Two household members, as [{id, name, initial}]. The ids are what is
    stored in user_state.json; only the display names are configurable."""
    names = appearance().get("people") or _S.get("people") or []
    out = []
    for i, pid in enumerate(("you", "partner")):
        name = str(names[i]).strip() if i < len(names) and str(names[i]).strip() else ("You" if i == 0 else "Partner")
        out.append({"id": pid, "name": name, "initial": name[0].upper()})
    return out


def accent():
    """(light, dark) accent colours, hex strings."""
    chosen = appearance().get("accent")
    if chosen:
        return chosen, chosen
    a = _S.get("accent")
    if isinstance(a, str):
        return a, a
    if isinstance(a, dict):
        light = a.get("light") or a.get("dark") or "#B5750F"
        return light, a.get("dark") or light
    return DEFAULT_ACCENT


def _mix(hex_a, hex_b, amount):
    """hex_a blended `amount` (0..1) of the way towards hex_b."""
    def rgb(h):
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    try:
        a, b = rgb(hex_a), rgb(hex_b)
    except ValueError:
        return hex_a
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * amount) for i in range(3))


def accent_css():
    """Extra CSS that re-points --accent, or '' when the built-in one is kept."""
    light, dark = accent()
    if (light, dark) == DEFAULT_ACCENT:
        return ""
    return (
        f":root {{ --accent: {light}; --accent-soft: {_mix(light, '#ffffff', 0.86)}; }}\n"
        f"@media (prefers-color-scheme: dark) {{ :root {{ --accent: {dark}; "
        f"--accent-soft: {_mix(dark, '#14161a', 0.82)}; }} }}"
    )


def appearance_css():
    """Everything the appearance adds to the stylesheet: the accent and the header image's
    vertical focus."""
    return accent_css() + "\n:root { --banner-y: %d%%; }" % header_focus()


def _hints_file():
    data = _read_json(pack_file("hints.json"))
    return data if isinstance(data, dict) else {}


def hints():
    """Loading-screen lines as {category: [lines]}; {} when there are none."""
    return {k: v for k, v in _hints_file().items()
            if not k.startswith("_") and k != "idle" and isinstance(v, list)}


def hint_labels():
    """Small caption shown above a loading line, per category."""
    labels = _hints_file().get("_labels")
    out = {k: k.replace("_", " ").capitalize() for k in hints()}
    if isinstance(labels, dict):
        out.update({k: str(v) for k, v in labels.items()})
    return out


def idle_quips():
    """Lines the phone remote rotates through when nothing is playing."""
    lines = _hints_file().get("idle")
    lines = [str(x) for x in lines] if isinstance(lines, list) else []
    return lines or ["Nothing playing right now."]


_FRANCHISE_THEME = {
    "bg": "#0e1a1c", "panel": "#142528", "text": "#e3f1ef", "text_dim": "#8fb3ae",
    "border": "#234146", "accent": "#3cc4b4", "accent_soft": "#103236", "chip_bg": "#183034",
}


def franchise():
    """The optional Franchise mode: a user-defined, ordered list of titles.

    Returns {"name", "titles", "theme"} or None. Titles are
    {"id": <TMDB id>, "type": "Movie"|"TV", "release": n, "chrono": n}; an
    entry for something you do not own is inert.
    """
    data = _read_json(pack_file("franchise.json"))
    if not isinstance(data, dict):
        return None
    titles = []
    for t in data.get("titles") or []:
        try:
            titles.append({
                "id": int(t["id"]), "type": "TV" if str(t.get("type")).lower() == "tv" else "Movie",
                "release": int(t["release"]), "chrono": int(t.get("chrono", t["release"])),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not titles:
        return None
    theme = dict(_FRANCHISE_THEME)
    theme.update({k: v for k, v in (data.get("theme") or {}).items() if k in theme and isinstance(v, str)})
    return {"name": str(data.get("name") or "Franchise"), "titles": titles, "theme": theme}


if __name__ == "__main__":
    print("packs      :", pack_dirs())
    print("app name   :", app_name())
    print("people     :", [p["name"] for p in people()])
    print("hints      :", sum(len(v) for v in hints().values()), "lines")
    print("franchise  :", (franchise() or {}).get("name", "(none)"))

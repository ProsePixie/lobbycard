"""
Draws the built-in artwork: header banner, loading-screen art, icons.

Everything is generated from a few colours and sine curves, so the images in
packs/default and demo/pack are original and can be regenerated or restyled:

    python tools/make_art.py

Needs Pillow (already a requirement). Output is deterministic.
"""
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=_lerp(top, bottom, y / max(1, h - 1)))
    return img


def _hills(img, base_y, amp, color, phase, freq=1.0):
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    pts = [(0, h)]
    for x in range(0, w + 8, 8):
        y = base_y + amp * (0.6 * math.sin(freq * x / w * 6.28 + phase) + 0.4 * math.sin(freq * 2.3 * x / w * 6.28 + phase * 1.7))
        pts.append((x, y))
    pts.append((w, h))
    d.polygon(pts, fill=color + (255,))
    img.paste(layer, (0, 0), layer)


def banner(path, w, h, top, bottom, sun, hills, seed=0):
    img = _gradient(w, h, top, bottom)
    glow = Image.new("RGB", img.size, (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy, r = int(w * 0.74), int(h * 0.50), int(h * 0.30)
    for k in range(6, 0, -1):
        gd.ellipse([cx - r * k // 2, cy - r * k // 2, cx + r * k // 2, cy + r * k // 2],
                   fill=tuple(int(c * 0.12 * (7 - k) / 6) for c in sun))
    glow = glow.filter(ImageFilter.GaussianBlur(h // 8))
    img = _screen(img, glow)
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], fill=sun)
    for i, col in enumerate(hills):
        _hills(img, h * (0.62 + 0.11 * i), h * 0.07, col, phase=seed + i * 1.9, freq=1.0 + 0.4 * i)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "JPEG", quality=88, optimize=True)


def _screen(a, b):
    return ImageChops.screen(a, b)


def boot(path, w=1920, h=1080):
    """Near-black loading art with a glowing ring on the right."""
    img = _gradient(w, h, (8, 10, 16), (4, 5, 9))
    glow = Image.new("RGB", img.size, (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = int(w * 0.76), int(h * 0.5)
    for r, c in ((340, (10, 40, 52)), (260, (14, 64, 80)), (190, (24, 100, 120)), (120, (50, 170, 190))):
        gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    img = _screen(img, glow)
    d = ImageDraw.Draw(img)
    for r, wd in ((150, 3), (190, 2), (235, 1)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(120, 220, 230), width=wd)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "JPEG", quality=85, optimize=True)


def icon(path, size, bg, fg):
    """Rounded square with a play triangle."""
    s = size * 4  # draw large, then downsample for clean edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=bg)
    cx, cy, t = s * 0.52, s * 0.5, s * 0.26
    d.polygon([(cx - t * 0.8, cy - t), (cx - t * 0.8, cy + t), (cx + t, cy)], fill=fg)
    img = img.resize((size, size), Image.LANCZOS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG", optimize=True)


def build_default():
    out = os.path.join(ROOT, "packs", "default")
    banner(os.path.join(out, "banner.jpg"), 1600, 368, (20, 26, 58), (86, 58, 92), (255, 196, 120),
           [(46, 38, 78), (30, 28, 58), (16, 18, 36)], seed=0.4)
    boot(os.path.join(out, "boot.jpg"))
    icon(os.path.join(out, "favicon.png"), 64, (40, 44, 80, 255), (255, 196, 120, 255))
    for n in (180, 192, 512):
        icon(os.path.join(out, f"app-icon-{n}.png"), n, (40, 44, 80, 255), (255, 196, 120, 255))
    banner(os.path.join(out, "youtube-banner.jpg"), 1600, 368, (56, 18, 24), (110, 40, 52), (255, 150, 140),
           [(70, 26, 36), (46, 20, 28), (24, 12, 18)], seed=2.1)


def build_demo():
    out = os.path.join(ROOT, "demo", "pack")
    banner(os.path.join(out, "franchise-banner.jpg"), 1900, 559, (10, 36, 40), (24, 92, 96), (140, 240, 220),
           [(18, 70, 74), (12, 50, 54), (8, 30, 34)], seed=3.3)
    icon(os.path.join(out, "franchise-icon.png"), 96, (16, 56, 60, 255), (140, 240, 220, 255))


def _spiral(size, turns, colors, width):
    """A glowing spiral on a transparent square."""
    big = size * 2
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = big / 2
    for arm, col in enumerate(colors):
        pts = []
        steps = 600
        for i in range(steps):
            t = i / steps
            r = t * big * 0.46
            a = t * turns * 6.2832 + arm * 6.2832 / len(colors)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        d.line(pts, fill=col + (230,), width=width * 2, joint="curve")
    glow = img.filter(ImageFilter.GaussianBlur(width * 3))
    out = Image.alpha_composite(glow, img)
    return out.resize((size, size), Image.LANCZOS)


def build_vortex():
    out = os.path.join(ROOT, "extras", "vortex", "Assets")
    os.makedirs(out, exist_ok=True)
    # background: deep space, soft rings, a few stars
    import random
    rnd = random.Random(7)
    w, h = 1120, 970
    bg = _gradient(w, h, (6, 9, 22), (10, 18, 38))
    rings = Image.new("RGB", (w, h), (0, 0, 0))
    rd = ImageDraw.Draw(rings)
    for k in range(1, 9):
        r = k * 70
        rd.ellipse([w * 0.5 - r * 1.3, h * 0.36 - r, w * 0.5 + r * 1.3, h * 0.36 + r], outline=(14 + k * 3, 36 + k * 4, 60 + k * 5), width=2)
    rings = rings.filter(ImageFilter.GaussianBlur(3))
    bg = _screen(bg, rings)
    d = ImageDraw.Draw(bg)
    for _ in range(220):
        x, y, b = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(80, 200)
        d.point((x, y), fill=(b, b, min(255, b + 30)))
    bg.save(os.path.join(out, "VORTEX2.png"), "PNG", optimize=True)
    # portal art
    art = _spiral(400, 3.2, [(56, 189, 248), (181, 124, 255), (93, 255, 186)], 7)
    art.save(os.path.join(out, "Vortex.png"), "PNG", optimize=True)
    # window icon
    ico = _spiral(256, 2.6, [(56, 189, 248), (181, 124, 255)], 10)
    ico.save(os.path.join(out, "vortex_hub.ico"), sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    build_default()
    build_demo()
    build_vortex()
    print("Artwork written to packs/default, demo/pack and extras/vortex/Assets")

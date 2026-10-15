"""Generate all app icons from one master drawing (requires Pillow).

    pip install pillow
    python tools/make_icons.py

Design: blue->teal gradient, white phone handset (incoming call) and an
orange badge with a check mark (task done). Assets are committed, so the
normal Windows/Android builds do NOT need Pillow.
"""
import os
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 1024
TOP = (37, 99, 235)       # #2563EB
BOTTOM = (13, 148, 136)   # #0D9488
BADGE = (245, 158, 11)    # #F59E0B

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "C:\\Windows\\Fonts\\seguisym.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]


def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise SystemExit("DejaVuSans-Bold.ttf not found; adjust FONT_CANDIDATES")


def rounded_mask(size, radius_ratio=0.22):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255)
    return m


def base_icon():
    """Full-bleed legacy icon on a 1024 canvas."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # vertical gradient clipped to rounded square
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(
            int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,))
    img.paste(grad, (0, 0), rounded_mask(S))
    draw_art(img, scale=1.0)
    return img


def foreground_icon():
    """Adaptive-icon foreground: transparent, art inside the safe zone."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_art(img, scale=0.74)
    return img


def draw_art(img, scale):
    d = ImageDraw.Draw(img)
    cx, cy = S / 2, S / 2
    # phone handset, centred a bit up-left to leave room for the badge
    phone_font = font(int(560 * scale))
    ch = "\u260e"  # ☎
    box = d.textbbox((0, 0), ch, font=phone_font)
    w, h = box[2] - box[0], box[3] - box[1]
    px, py = cx - w / 2 - box[0] - 30 * scale, cy - h / 2 - box[1] - 40 * scale
    d.text((px + 10 * scale, py + 14 * scale), ch, font=phone_font, fill=(0, 0, 0, 70))
    d.text((px, py), ch, font=phone_font, fill=(255, 255, 255, 255))
    # orange badge with check
    r = 165 * scale
    bx, by = cx + 245 * scale, cy + 255 * scale
    d.ellipse([bx - r - 14, by - r - 14, bx + r + 14, by + r + 14], fill=(255, 255, 255, 255))
    d.ellipse([bx - r, by - r, bx + r, by + r], fill=BADGE + (255,))
    check_font = font(int(300 * scale))
    ch2 = "\u2713"  # ✓
    box = d.textbbox((0, 0), ch2, font=check_font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text((bx - w / 2 - box[0], by - h / 2 - box[1]), ch2,
           font=check_font, fill=(255, 255, 255, 255))


def save_png(img, path, size):
    img.resize((size, size), Image.LANCZOS).save(path)
    print(" ", path, size)


def main():
    legacy = base_icon()
    foreground = foreground_icon()
    round_icon = legacy.copy()
    circle = Image.new("L", (S, S), 0)
    ImageDraw.Draw(circle).ellipse([0, 0, S - 1, S - 1], fill=255)
    round_icon.putalpha(circle)

    win_dir = os.path.join(ROOT, "windows")
    legacy.save(os.path.join(win_dir, "icon.ico"),
                sizes=[(s, s) for s in (16, 20, 24, 32, 48, 64, 128, 256)])
    print(" ", os.path.join(win_dir, "icon.ico"))
    save_png(legacy, os.path.join(win_dir, "icon.png"), 512)

    res = os.path.join(ROOT, "android", "app", "src", "main", "res")
    densities = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}
    for name, mult in densities.items():
        folder = os.path.join(res, f"mipmap-{name}")
        os.makedirs(folder, exist_ok=True)
        save_png(legacy, os.path.join(folder, "ic_launcher.png"), int(48 * mult))
        save_png(round_icon, os.path.join(folder, "ic_launcher_round.png"), int(48 * mult))
        # adaptive foreground is 108dp
        save_png(foreground, os.path.join(folder, "ic_launcher_foreground.png"), int(108 * mult))
    print("done")


if __name__ == "__main__":
    main()

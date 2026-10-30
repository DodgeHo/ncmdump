"""生成程序图标（仅需运行一次）。"""

import math
import os

from PIL import Image, ImageDraw

S = 1024          # 画布边长
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")

BG = (28, 28, 30, 255)
RED = (198, 47, 47, 255)
WHITE = (255, 255, 255, 255)


def rotated_ellipse(cx, cy, rx, ry, deg, n=180):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    return pts


def quad(p0, p1, p2, n=40):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


def _draw_note():
    """在一张透明画布上画八分音符，返回该图层。"""
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    cx, cy = S * 0.44, S * 0.70
    head = rotated_ellipse(cx, cy, S * 0.135, S * 0.098, -22)
    d.polygon(head, fill=WHITE)

    sw = S * 0.036
    top_y = S * 0.235
    x_right = cx + S * 0.108
    x_left = x_right - sw
    stem_bottom = cy - S * 0.045
    d.polygon([(x_left, stem_bottom), (x_right, stem_bottom),
               (x_right, top_y), (x_left, top_y)], fill=WHITE)

    flag = quad((x_right, top_y),
                (x_right + S * 0.20, top_y + S * 0.10),
                (x_right + S * 0.055, top_y + S * 0.235), n=50)
    flag += [(x_right, top_y + S * 0.215), (x_right, top_y)]
    d.polygon(flag, fill=WHITE)
    return layer


def build():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    r = int(S * 0.22)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=BG)

    pad = int(S * 0.055)
    d.ellipse([pad, pad, S - pad, S - pad], fill=RED)

    # 音符：按实际包围盒自动缩放并居中到红圈内
    note = _draw_note()
    bbox = note.getbbox()
    note = note.crop(bbox)
    target = int((S - 2 * pad) * 0.70)
    w, h = note.size
    scale = target / max(w, h)
    note = note.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                       Image.LANCZOS)
    ox = (S - note.width) // 2
    oy = (S - note.height) // 2
    img.alpha_composite(note, (ox, oy))

    return img


def main():
    img = build()
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
             (64, 64), (96, 96), (128, 128), (256, 256)]
    img.save(OUT, format="ICO", sizes=sizes)
    print("icon ->", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()

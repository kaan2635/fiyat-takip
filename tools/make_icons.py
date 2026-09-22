#!/usr/bin/env python3
"""PWA ikonlarini uretir. Harici bagimlilik yok - PNG'yi kendimiz yaziyoruz.

Ikonlar bir kez uretilip depoya konur; her taramada yeniden uretilmez.
Tasarimi degistirmek istersen asagidaki DESIGN sabitlerini duzenleyip calistir:

    python tools/make_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "icons"

# --- Tasarim -----------------------------------------------------------------
BG_TOP = (42, 120, 214)      # #2a78d6 - seri rengiyle ayni mavi
BG_BOTTOM = (28, 92, 171)    # #1c5cab
INK = (255, 255, 255)

# Dusen fiyat cizgisi (birim koordinat: 0..1)
LINE = [(0.17, 0.33), (0.33, 0.44), (0.46, 0.39), (0.61, 0.57), (0.79, 0.67)]
LINE_W = 0.078               # cizgi kalinligi
DOT_R = 0.082                # bitis noktasi yaricapi

SS = 4                       # kenar yumusatma icin asiri ornekleme kati


# --- PNG yazici --------------------------------------------------------------
def write_png(path: Path, pixels: list[list[tuple[int, int, int, int]]]) -> None:
    """RGBA piksel matrisini PNG olarak yazar."""
    height, width = len(pixels), len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)                       # filtre tipi: None
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


# --- Geometri ----------------------------------------------------------------
def dist_to_segment(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def inside_rounded_rect(x, y, radius) -> bool:
    """Birim karede, koseleri `radius` yaricapla yuvarlatilmis alan icinde mi?"""
    cx = min(max(x, radius), 1 - radius)
    cy = min(max(y, radius), 1 - radius)
    return math.hypot(x - cx, y - cy) <= radius or (radius <= x <= 1 - radius) or (radius <= y <= 1 - radius)


def render(size: int, corner: float = 0.0, scale: float = 1.0) -> list[list[tuple]]:
    """Ikonu cizer.

    corner: kose yuvarlatma yaricapi (0 = tam kare, iOS zaten kendisi maskeler)
    scale : maskable ikonlar icin icerigi kucultme orani (guvenli alan)
    """
    big = size * SS
    acc = [[(0, 0, 0, 0)] * size for _ in range(size)]

    # Asiri ornekleme: her cikti pikseli icin SSxSS alt ornek topla
    for oy in range(size):
        row = acc[oy]
        for ox in range(size):
            r = g = b = a = 0
            for sy in range(SS):
                for sx in range(SS):
                    ux = (ox * SS + sx + 0.5) / big
                    uy = (oy * SS + sy + 0.5) / big

                    if corner and not inside_rounded_rect(ux, uy, corner):
                        continue

                    # Arka plan: dikey gecis
                    t = uy
                    br = round(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
                    bg = round(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
                    bb = round(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
                    pr, pg, pb = br, bg, bb

                    # Icerigi guvenli alana sigdir (maskable icin)
                    cx = 0.5 + (ux - 0.5) / scale
                    cy = 0.5 + (uy - 0.5) / scale

                    on_ink = math.hypot(cx - LINE[-1][0], cy - LINE[-1][1]) <= DOT_R
                    if not on_ink:
                        for i in range(len(LINE) - 1):
                            ax, ay = LINE[i]
                            bx, by = LINE[i + 1]
                            if dist_to_segment(cx, cy, ax, ay, bx, by) <= LINE_W / 2:
                                on_ink = True
                                break
                    if on_ink:
                        pr, pg, pb = INK

                    r += pr; g += pg; b += pb; a += 255

            n = SS * SS
            row[ox] = (r // n, g // n, b // n, a // n) if a else (0, 0, 0, 0)
    return acc


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("apple-touch-icon.png", 180, 0.0, 1.0),    # iOS kendi maskeler, tam kare ver
        ("icon-192.png", 192, 0.22, 1.0),
        ("icon-512.png", 512, 0.22, 1.0),
        ("icon-maskable-512.png", 512, 0.0, 0.66),  # Android maskesi icin guvenli alan
    ]
    for name, size, corner, scale in jobs:
        write_png(OUT / name, render(size, corner, scale))
        print(f"  {name:26} {size}x{size}  ({(OUT / name).stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render favicon.ico (16 + 32) and apple-touch-icon.png (180) from the mark.

These are ADDITIVE fallbacks. The inline `data:` SVG favicon in index.html
stays the preferred icon for clients that honour it; these two files exist
only for clients that request the conventional root paths and never look at
the markup (Safari's touch icon, RSS readers, some crawlers, old browsers).

The mark is defined ONCE, as the path in assets/mark.svg (November 2026: a
solid ring crossed by an orbit). This script asserts that the same path is
what index.html draws in the hero, what both favicons carry, and what it is
about to rasterize. If any copy drifts, it fails loudly instead of quietly
shipping a stale icon.

Icon framing: the mark's full extent (orbit tail to orbit tip) is centred in
a 1600-unit square on the #040404 ground, the same box the data: favicons use.

Rasterizer: cairosvg if importable, else rsvg-convert. Each size is rendered
at 8x and reduced with LANCZOS so the orbit's thin tip survives at 16px.

Run from anywhere:  python3 tools/make-icons.py
"""

import io
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MARK = ROOT / "assets" / "mark.svg"
PAGES = [ROOT / "index.html", ROOT / "404.html"]
ICO = ROOT / "favicon.ico"
TOUCH = ROOT / "apple-touch-icon.png"

ICON_BOX = "221 224 1600 1600"
SUPERSAMPLE = 8


def die(msg):
    sys.exit(f"HALT: {msg}")


def mark_path():
    m = re.search(r' d="([^"]+)"', MARK.read_text(encoding="utf-8"))
    if not m:
        die("assets/mark.svg has no path")
    return m.group(1)


def assert_copies(d):
    for page in PAGES:
        src = page.read_text(encoding="utf-8")
        m = re.search(r'<link rel="icon" type="image/svg\+xml" href="data:image/svg\+xml,([^"]+)"', src)
        if not m:
            die(f"no inline data: SVG favicon in {page.name}")
        fav = urllib.parse.unquote(m.group(1))
        if f"viewBox='{ICON_BOX}'" not in fav or f"d='{d}'" not in fav:
            die(f"{page.name}'s favicon no longer matches assets/mark.svg")
    hero = re.search(r'<svg class="logo"[^>]*>.*?<path d="([^"]+)"', PAGES[0].read_text(encoding="utf-8"), re.S)
    if not hero or hero.group(1) != d:
        die("index.html's hero mark no longer matches assets/mark.svg")
    print("mark verified: assets/mark.svg == hero == both favicons")


def render(d, px):
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{ICON_BOX}'>"
           f"<rect x='221' y='224' width='1600' height='1600' fill='#040404'/>"
           f"<path fill='#F8F4F0' d='{d}'/></svg>").encode()
    big = px * SUPERSAMPLE
    try:
        import cairosvg
        png = cairosvg.svg2png(bytestring=svg, output_width=big, output_height=big)
    except ImportError:
        if not shutil.which("rsvg-convert"):
            die("need cairosvg (pip install cairosvg) or rsvg-convert (brew install librsvg)")
        png = subprocess.run(["rsvg-convert", "-w", str(big), "-h", str(big)],
                             input=svg, capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGB").resize((px, px), Image.LANCZOS)


def main():
    d = mark_path()
    assert_copies(d)
    render(d, 180).save(TOUCH, optimize=True)
    print(f"wrote {TOUCH.relative_to(ROOT)}: 180x180, {TOUCH.stat().st_size} bytes")
    i32, i16 = render(d, 32), render(d, 16)
    i32.save(ICO, format="ICO", sizes=[(32, 32), (16, 16)], append_images=[i16])
    print(f"wrote {ICO.relative_to(ROOT)}: 16+32, {ICO.stat().st_size} bytes")


if __name__ == "__main__":
    main()

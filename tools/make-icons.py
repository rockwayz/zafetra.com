#!/usr/bin/env python3
"""Render favicon.ico (16 + 32) and apple-touch-icon.png (180) from the mark.

These are ADDITIVE fallbacks. The inline `data:` SVG favicon in index.html
stays and is still the preferred icon for clients that honour it; these two
files exist only for clients that request the conventional root paths and
never look at the markup (Safari's touch icon, RSS readers, some crawlers,
old browsers).

The mark is defined ONCE, as the inline SVG in index.html. This script does
not re-draw it from memory: it parses that data: URI and asserts every
number it is about to draw against what the markup actually says. If the
mark in index.html ever changes, this fails loudly instead of quietly
shipping a stale icon.

Geometry, from that SVG (a 64-unit box):
  - ground        #040404 fill
  - outer ring    r=26 stroke 4.6, in two arcs: a long one running 12->9->6
                  o'clock, and a short one 3->5 o'clock. That leaves a wide
                  gap from 12 to 3 and a narrow gap at bottom centre.
  - inner circle  r=16 stroke 2.8
  - rule          x=32, y 16->48, stroke 2.8

SVG arc flags map to PIL angles as follows. PIL measures from 3 o'clock and
increases CLOCKWISE, which is also the direction of SVG's sweep-flag=1.
  'M32 6  A26 26 0 1 0 31.1 58'  -> from -90deg counter-clockwise to 92deg,
                                    i.e. PIL arc(92, 270)
  'M58 32 A26 26 0 0 1 34.7 57.9' -> from 0deg clockwise to 84deg,
                                    i.e. PIL arc(0, 84)

Every size is drawn at 8x and reduced with LANCZOS: at 16px the strokes are
well under a pixel (4.6/64*16 = 1.15px, 2.8/64*16 = 0.70px), so drawing
direct to the target size would drop them to hairlines or lose them.

Run from anywhere:  python3 tools/make-icons.py
"""

import re
import sys
import urllib.parse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
ICO = ROOT / "favicon.ico"
TOUCH = ROOT / "apple-touch-icon.png"

INK = (4, 4, 4)          # --ink
PAPER = (248, 244, 240)  # --paper

BOX = 64.0               # the SVG viewBox
RING_R, RING_W = 26.0, 4.6
INNER_R, INNER_W = 16.0, 2.8
RULE_X, RULE_Y0, RULE_Y1 = 32.0, 16.0, 48.0
ARC_LONG = (92, 270)     # PIL degrees
ARC_SHORT = (0, 84)

SUPERSAMPLE = 8


def die(msg):
    sys.exit(f"HALT: {msg}")


def assert_matches_markup():
    """Parse the inline data: SVG and check it still says what we draw."""
    src = INDEX.read_text(encoding="utf-8")
    m = re.search(r'<link rel="icon" type="image/svg\+xml" href="data:image/svg\+xml,([^"]+)"', src)
    if not m:
        die("no inline data: SVG favicon found in index.html")
    svg = urllib.parse.unquote(m.group(1))
    expected = [
        (r"viewBox='0 0 64 64'", "viewBox 0 0 64 64"),
        (r"fill='#040404'", "ground #040404"),
        (r"stroke='#F8F4F0' stroke-width='4.6'", "ring stroke 4.6 #F8F4F0"),
        (r"M32 6 A26 26 0 1 0 31\.1 58", "long ring arc"),
        (r"M58 32 A26 26 0 0 1 34\.7 57\.9", "short ring arc"),
        (r"cx='32' cy='32' r='16'[^/]*stroke-width='2\.8'", "inner circle r16 w2.8"),
        (r"x1='32' y1='16' x2='32' y2='48'[^/]*stroke-width='2\.8'", "rule w2.8"),
    ]
    for pat, label in expected:
        if not re.search(pat, svg):
            die(f"index.html's mark no longer matches this script — missing: {label}")
    print(f"mark verified against index.html ({len(expected)} geometry assertions)")


def draw_mark(size):
    s = size * SUPERSAMPLE
    k = s / BOX
    img = Image.new("RGB", (s, s), INK)
    d = ImageDraw.Draw(img)
    c = 32.0 * k

    def box(r):
        return [c - r * k, c - r * k, c + r * k, c + r * k]

    ring_w = max(1, round(RING_W * k))
    d.arc(box(RING_R), *ARC_LONG, fill=PAPER, width=ring_w)
    d.arc(box(RING_R), *ARC_SHORT, fill=PAPER, width=ring_w)

    inner_w = max(1, round(INNER_W * k))
    d.ellipse(box(INNER_R), outline=PAPER, width=inner_w)
    d.line([(RULE_X * k, RULE_Y0 * k), (RULE_X * k, RULE_Y1 * k)], fill=PAPER, width=inner_w)

    return img.resize((size, size), Image.LANCZOS)


def contrast_report(img, label):
    """Crude legibility probe: how much ink survives, and is the rule visible?"""
    w = img.size[0]
    px = img.convert("L").load()
    lit = sum(1 for y in range(w) for x in range(w) if px[x, y] > 40)
    # the rule is the vertical run through the middle; sample the column
    col = [px[w // 2, y] for y in range(w)]
    print(f"  {label:9} {w}x{w}  lit px {lit:5}/{w*w}  centre-column max {max(col):3}")


def main():
    assert_matches_markup()

    touch = draw_mark(180)
    touch.save(TOUCH, optimize=True)
    print(f"wrote {TOUCH.relative_to(ROOT)}: 180x180, {TOUCH.stat().st_size} bytes")

    i32, i16 = draw_mark(32), draw_mark(16)
    contrast_report(i32, "ico 32")
    contrast_report(i16, "ico 16")
    i32.save(ICO, format="ICO", sizes=[(32, 32), (16, 16)], append_images=[i16])
    print(f"wrote {ICO.relative_to(ROOT)}: 16+32, {ICO.stat().st_size} bytes")


if __name__ == "__main__":
    main()

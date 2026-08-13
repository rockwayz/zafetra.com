#!/usr/bin/env python3
"""Render assets/og.png — the 1200x630 link-preview card.

The card is the wordmark over the motto on the page's ground (#040404):

    ZAFETRA
    Magic is a formula you haven't read yet.   (U+2019 apostrophe)

Faces, by ruling:
  - Wordmark: system Didot, loaded BY FILE PATH from the macOS supplemental
    TTC. The page's --wordmark stack is deliberately webfont-free, so there is
    no Didot file in this repo to use; loading the OS file by path means there
    is no name-resolution step that could silently fall back to a generic
    serif. The face actually loaded is proven below from its own name table,
    plus a glyph-metric diff against Georgia as a belt-and-braces check.
    The PNG freezes this rendering as the canonical wordmark for every
    viewer, including those whose own device would fall back down the stack.
  - Motto: the repo's own JetBrains Mono latin subset (assets/asset-09.woff2),
    decompressed in memory with fontTools. Every glyph the motto needs —
    including U+2019 — is asserted present in the cmap before rendering.
    That subset was rebuilt by tools/make-fonts.py, which proves glyph-for-glyph
    that no existing outline moved — so the card this renders is unchanged.

Renders at 2x (2400x1260) and downscales LANCZOS to 1200x630.
Exits nonzero, producing nothing, if any face or glyph cannot be proven.

Run from anywhere:  python3 tools/make-og-card.py
"""

import io
import sys
from pathlib import Path

from fontTools.ttLib import TTFont, TTCollection
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "og.png"
JBM_WOFF2 = ROOT / "assets" / "asset-09.woff2"
DIDOT_TTC = Path("/System/Library/Fonts/Supplemental/Didot.ttc")
GEORGIA = Path("/System/Library/Fonts/Supplemental/Georgia.ttf")

INK = (4, 4, 4)          # --ink / page ground
PAPER = (248, 244, 240)  # --paper / wordmark
DIM = (168, 161, 153)    # --dim / motto

WORDMARK = "ZAFETRA"
TRACK_EM = 0.14          # the h1's letter-spacing
MOTTO = "Magic is a formula you haven’t read yet."

W, H = 2400, 1260        # 2x canvas


def die(msg):
    sys.exit(f"HALT: {msg}")


def name_of(font):
    """(family, subfamily) from a fontTools font's name table."""
    get = font["name"].getDebugName
    return (get(1) or "?", get(2) or "?")


def didot_regular_index():
    """Prove which face in the TTC is Didot Regular; return its index."""
    if not DIDOT_TTC.exists():
        die(f"{DIDOT_TTC} not found — Didot cannot be loaded by path")
    faces = [name_of(f) for f in TTCollection(DIDOT_TTC).fonts]
    print(f"faces in {DIDOT_TTC.name}: {faces}")
    for i, (fam, sub) in enumerate(faces):
        if fam == "Didot" and sub == "Regular":
            return i
    die(f"no (Didot, Regular) face in {DIDOT_TTC}")


def assert_glyphs(font, text, label):
    cmap = font.getBestCmap()
    missing = sorted({c for c in text if c != " " and ord(c) not in cmap})
    if missing:
        die(f"{label} lacks glyphs for: {[hex(ord(c)) for c in missing]}")
    print(f"{label}: all glyphs for {text!r} present in cmap")


def tracked_width(draw, text, font, track):
    """Advance width of text with tracking after every char but the last."""
    return sum(draw.textlength(c, font=font) for c in text) + track * (len(text) - 1)


def draw_tracked(draw, xy, text, font, track, fill):
    x, y = xy
    for c in text:
        draw.text((x, y), c, font=font, fill=fill, anchor="ls")
        x += draw.textlength(c, font=font) + track


def main():
    # ---- prove the faces -------------------------------------------------
    idx = didot_regular_index()
    didot_ft = TTCollection(DIDOT_TTC).fonts[idx]
    assert_glyphs(didot_ft, WORDMARK, "Didot")

    jbm_ft = TTFont(JBM_WOFF2)
    print(f"{JBM_WOFF2.name} name table: {name_of(jbm_ft)}")
    assert_glyphs(jbm_ft, MOTTO, "JetBrains Mono subset")
    jbm_ft.flavor = None                      # woff2 -> plain sfnt in memory
    jbm_buf = io.BytesIO()
    jbm_ft.save(jbm_buf)

    img = Image.new("RGB", (W, H), INK)
    draw = ImageDraw.Draw(img)

    # ---- size the wordmark to ~65% of the card ---------------------------
    probe = ImageFont.truetype(str(DIDOT_TTC), 100, index=idx)
    print(f"PIL loaded face at index {idx}: {probe.getname()}")
    if probe.getname()[0] != "Didot":
        die(f"PIL loaded {probe.getname()}, not Didot")
    w100 = tracked_width(draw, WORDMARK, probe, 0.14 * 100)
    wm_size = round(100 * (0.65 * W) / w100)
    wm_font = ImageFont.truetype(str(DIDOT_TTC), wm_size, index=idx)
    track = TRACK_EM * wm_size

    # belt-and-braces: same string, same size, Didot vs Georgia metrics
    if GEORGIA.exists():
        geo = ImageFont.truetype(str(GEORGIA), wm_size)
        dw = tracked_width(draw, WORDMARK, wm_font, track)
        gw = tracked_width(draw, WORDMARK, geo, track)
        print(f"metric diff at {wm_size}px: Didot {dw:.0f}px vs Georgia {gw:.0f}px "
              f"({'differs — not a fallback' if abs(dw - gw) > wm_size * 0.05 else 'TOO CLOSE'})")
        if abs(dw - gw) <= wm_size * 0.05:
            die("Didot metrics indistinguishable from Georgia — face unproven")

    mt_size = round(wm_size * 0.22)
    jbm_buf.seek(0)
    mt_font = ImageFont.truetype(jbm_buf, mt_size)

    # ---- lay out the two lines as one vertically centred group ----------
    wm_w = tracked_width(draw, WORDMARK, wm_font, track)
    cap_h = -draw.textbbox((0, 0), "Z", font=wm_font, anchor="ls")[1]
    mt_bbox = draw.textbbox((0, 0), MOTTO, font=mt_font, anchor="ls")
    mt_w, mt_asc, mt_desc = mt_bbox[2], -mt_bbox[1], mt_bbox[3]
    gap = round(wm_size * 0.34)               # wordmark baseline -> motto cap top

    group_h = cap_h + gap + mt_asc + mt_desc
    wm_base = round((H - group_h) / 2 + cap_h)
    mt_base = wm_base + gap + mt_asc

    draw_tracked(draw, ((W - wm_w) / 2, wm_base), WORDMARK, wm_font, track, PAPER)
    draw.text(((W - mt_w) / 2, mt_base), MOTTO, font=mt_font, fill=DIM, anchor="ls")

    img = img.resize((W // 2, H // 2), Image.LANCZOS)
    OUT.parent.mkdir(exist_ok=True)
    img.save(OUT, optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)}: {img.size[0]}x{img.size[1]}")


if __name__ == "__main__":
    main()

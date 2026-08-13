#!/usr/bin/env python3
"""Rebuild the JetBrains Mono subsets, and assert they cover what the page draws.

WHY THIS EXISTS
The two woff2 files were the stock Google-Fonts latin and greek subsets. Their
unicode-ranges stop before the blocks this page actually uses: no U+2070-209F
(super/subscripts), no U+2200-22FF (math operators), and arrows limited to
U+2191/U+2193 -- so no arrow. 71 distinct characters the page can draw were in
NEITHER file and rendered from the platform fallback stack on every OS: the
worst offenders by count were the subscript two (82), the arrow (65), the
superscript minus (34), the partial differential (31) and the nabla (27).

Note the failure was purely a cmap gap, not a declaration gap: every character
that WAS in a face's cmap was also inside that face's declared unicode-range.
Measured before the fix: 0 declaration-gap characters. So widening the ranges
alone would have fixed nothing -- the glyphs genuinely were not in the files.

PROVENANCE, AND WHY THE SOURCE IS GOOGLE'S BUILD AND NOT JETBRAINS'
The shipped files report "Version 2.211", which is not a JetBrains release --
JetBrains never tagged a 2.211, and its neighbouring 2.210 tag ships no variable
font at all. 2.211 is Google's build. That matters, because the regenerated file
has to match the CURRENT rendered look rather than improve it, and the upstream
JetBrains releases do not:

  source                     outlines identical to the shipped latin subset
  google/fonts   2.211       229 / 229          <- exact, zero drift
  JetBrains  v2.221          147 / 229          82 glyphs redrawn
  JetBrains  v2.304           55 / 229         174 glyphs redrawn

Pinning JetBrains upstream would silently redraw the digits, `i`, `j`, `l`, `F`,
`M`, `T` and every accented capital on a page whose whole surface is type. So
the pin is google/fonts, at an immutable commit, verified by sha256 below.

The pipeline below reproduces the shipped files EXACTLY -- same table set, same
394 glyphs, same fvar, same GSUB/GPOS feature tags, and 0 geometric differences
across all 229 codepoints, compared with a pen that decomposes composites. Three
flags are load-bearing in getting there and were each found by diffing:
  --layout-features must include `mark`, or GPOS is dropped entirely;
  --glyph-names, or `post` drops to format 3.0 and composite components lose
    their names (this shows up as a phantom 37-glyph "difference" that is only
    naming, not geometry);
  --drop-tables+=HVAR, which Google's own build drops and pyftsubset keeps.

THE CEILING: 33 CHARACTERS NO JETBRAINS MONO CAN SERVE
Widening from this source recovers 38 of the 71. The other 33 are absent from
2.211's cmap. They are not recoverable by remapping either: JetBrains Mono has
no unencoded `.sups`/`.subs` glyph variants to point a cmap entry at -- its
sups/subs/sinf features map onto codepoints that are already encoded.

  27 of the 33 occur in the STATIC FIELD. Per the standing rule that copy is an
     owner call, they are NOT substituted here. They are listed in ESCALATED
     below and reported by --report for owner review.
   6 occur ONLY in the runtime junk pools (TOKENS/FILL), which are junk by
     definition, so they were substituted in index.html for covered
     near-equivalents. See SUBSTITUTED below; each swap is commented at its
     site in the script.

A later JetBrains release would recover 12 of the 27 (the nabla, right tack,
double arrow, union, intersection, the negated existential, the double-struck R
and Q, minus-or-plus, square image, up tack, questioned equal). That is a
typeface-version decision, not a bug fix, so it is the owner's call -- taking it
means accepting the 174-glyph redraw above, or carrying a third face.

WHAT THE CHECK GUARDS
Default mode takes no network and rebuilds nothing. It re-derives the inventory
from index.html and 404.html, reads the shipped woff2 files, and fails if any
character the page can draw is served by no face and is not on one of the two
documented lists. So a future edit to the junk pools that reaches for a new
symbol cannot silently reopen the gap -- it fails here instead.

  python3 tools/make-fonts.py            check only, no network   (CI-safe)
  python3 tools/make-fonts.py --report   the same, plus the full coverage table
  python3 tools/make-fonts.py --write    refetch the pinned source and rebuild
"""

import os
import re
import sys
import html
import hashlib
import tempfile
import unicodedata
import subprocess
import urllib.request
from pathlib import Path
from collections import Counter

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent
from fontTools.pens.recordingPen import DecomposingRecordingPen

ARGS_ARE_XY_VALUES = 0x0002
ROUND_XY_TO_GRID = 0x0004

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
NOTFOUND = ROOT / "404.html"
GREEK = ROOT / "assets" / "asset-05.woff2"
LATIN = ROOT / "assets" / "asset-09.woff2"

# google/fonts, pinned to the commit that introduced this build. Immutable: a
# tag or a branch could move under us and silently change the letterforms.
SOURCE_URL = (
    "https://raw.githubusercontent.com/google/fonts/"
    "2e05c1cf00a6e4f40a4b931600a90881c26e15cd/"
    "ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"
)
SOURCE_SHA256 = "48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda"
SOURCE_VERSION = "Version 2.211"

# Matched to the shipped files, not to pyftsubset's defaults. `mark` is GPOS;
# dropping it would drop GPOS entirely and unstack every combining accent.
LAYOUT_FEATURES = "calt,ccmp,frac,locl,mark"
# Google restricts the 100-800 source axis to 400-800. Keep it: the page asks
# for 500 on the tagline and the 404 link, and `font-weight: 400 500` on the
# face is only honest if the axis actually spans it.
AXIS_LIMIT = "wght=400:800"

# ---- what gets fabricated ---------------------------------------------------
# 2.211 has no glyph for these, but it has the PARTS. Each is assembled as a
# TrueType composite from the face's own outlines — the same way the font builds
# its own subscripts (see FABRICATION NOTES far below). Components are named by
# CODEPOINT here and resolved through the cmap at build time: the shipped face
# carries production names, and `uni0307`-style names are an implementation
# detail that must never be assumed.
#
#   sup     scale a base to superscript size and raise it to the superscript
#           baseline, re-centring the ink in the 600-unit advance
#   sub     the same, then down by the font's own superscript->subscript offset
#   supsign / subsign
#           as above but on the SIGN transform, derived from the one native
#           base->superscript sign pair the font has (+ -> U+207A)
#   flip    vertical mirror of a single component about its own bbox
#   stack   a base with a mark centred over it, no scaling
FABRICATED = {
    0x207B: ("supsign", [0x2212], "superscript minus, from the minus"),
    0x207F: ("sup", [0x006E], "superscript n"),
    0x1D49: ("sup", [0x0065], "modifier small e"),
    0x02E2: ("sup", [0x0073], "modifier small s"),
    0x02E3: ("sup", [0x0078], "modifier small x"),
    0x1D40: ("sup", [0x0054], "modifier capital T"),
    0x207D: ("sup", [0x0028], "superscript left paren"),
    0x207E: ("sup", [0x0029], "superscript right paren"),
    0x1D62: ("sub", [0x0069], "subscript i"),
    # MODIFIER LETTER, not a subscript one: Unicode's modifier letters sit
    # RAISED. The field uses it as a contravariant tensor index — `½gᵘˢ(...)`,
    # where it pairs with the modifier s directly beside it — so lowering it
    # would have set one index of the pair against the other.
    0x1D58: ("sup", [0x0075], "modifier small u"),
    0x2C7C: ("sub", [0x006A], "subscript j"),
    0x2090: ("sub", [0x0061], "subscript a"),
    0x208B: ("subsign", [0x2212], "subscript minus"),
    0x2207: ("flip", [0x0394], "nabla: the greek Delta, mirrored"),
    0x1E8B: ("stack", [0x0078, 0x0307], "x with dot above"),
    0x1E8D: ("stack", [0x0078, 0x0308], "x with diaeresis"),
}

# Components that are not otherwise in the latin subset and must be pulled into
# it. U+0394 is greek: it stays SERVED by the greek face, so its cmap entry is
# stripped after subsetting and the latin unicode-range never mentions it. Only
# the outline is borrowed.
COMPONENT_ONLY = {0x0394}

# Characters the field draws that are still on the platform fallback, each with
# its field frequency and the reason no composite was shipped. This is the list
# the build asserts against: anything uncovered and NOT here fails the build, so
# a future field edit cannot quietly reopen the gap.
ALLOWLIST = {
    "⇌": (9, "equilibrium harpoons: no component pair reads as one, and half an arrow cannot be faked"),
    "⇒": (6, "double arrow: no double-stroke arrow parts in the face"),
    "∩": (5, "intersection: no arch component; would need original drawing"),
    "∪": (5, "union: as ∩, and flipping ∩ is not available while ∩ itself is absent"),
    "∄": (4, "negated existential: needs a slash overlay sized to ∃, an original drawing decision"),
    "⟹": (4, "long double arrow: as ⇒, plus a bar that would have to be drawn"),
    "ℏ": (1, "planck: h with a bar — the bar is an original stroke, not a component"),
    "ℝ": (1, "double-struck R: a distinct letterform, not a transform of R"),
    "∓": (1, "minus-or-plus: not yet attempted (Tier B candidate — ± mirrored)"),
    "∮": (1, "contour integral: not yet attempted (Tier B candidate — ∫ plus a ring)"),
    "⊢": (8, "right tack: not yet attempted (Tier B candidate — bar plus minus)"),
}

# Junk-pool-only characters that were swapped for covered near-equivalents.
# Kept here so the check can prove they are really gone from the pools.
SUBSTITUTED = {
    "ℚ": "ℕ",  # '√2 ∉ ℚ' -> '√2 ∉ ℕ'
    "≟": "≠",  # 'P ≟ NP' -> 'P ≠ NP'
    "⊨": "|=",  # '⊨ φ'    -> '|= φ'
    "⊑": "⊆",  # '⊑ ref'  -> '⊆ ref'
    "⊥": "F",  # '⊢ ⊥'    -> '⊢ F'
    "☉": "sun",  # 'M_☉'    -> 'M_sun'
}


def die(msg):
    sys.exit(f"HALT: {msg}")


# ---- what the page can draw -------------------------------------------------


def inventory():
    """Every character the page can put on screen, kept split by source.

    Split matters: a gap in the static field is copy and therefore an owner
    call, while a gap in the runtime junk pools is ours to substitute.
    """
    src = INDEX.read_text(encoding="utf-8")
    out = {}

    # (a) the three aria-hidden ghost SVGs. 567 fragments, all <textPath>.
    # ~87 of them legitimately hold < > & as entities, so decoding is required
    # rather than optional -- a raw read would count "&amp;" as five characters.
    field, per_svg = Counter(), {}
    for m in re.finditer(r'<svg[^>]*class="ghost (\w+)"[^>]*>', src):
        body = src[m.end():src.index("</svg>", m.end())]
        frags = re.findall(r"<textPath\b[^>]*>(.*?)</textPath>", body, re.S)
        per_svg[m.group(1)] = len(frags)
        for f in frags:
            if "<" in f:
                die(f"nested tag inside a textPath, parser assumption broken: {f[:60]!r}")
            field.update(html.unescape(f))
        # a fragment that ever carried text OUTSIDE its textPath would be
        # invisible to the loop above, so prove none does.
        for t in re.findall(r"<text\b(?!Path)[^>]*>(.*?)</text>", body, re.S):
            if re.sub(r"<textPath\b[^>]*>.*?</textPath>", "", t, flags=re.S).strip():
                die(f"text outside a textPath in the {m.group(1)} layer: {t[:60]!r}")
    if sum(per_svg.values()) != 567:
        die(f"expected 567 fragments, found {sum(per_svg.values())} {per_svg}")
    out["field"] = field

    # (b)+(c) the glitch wave's junk pools, written into the field at runtime.
    block = re.search(r"var TOKENS = \[(.*?)\];", src, re.S)
    fill = re.search(r"var FILL = '([^']*)';", src)
    if not block or not fill:
        die("could not find TOKENS/FILL in the script")
    tokens = Counter()
    for t in re.findall(r"'((?:[^'\\]|\\.)*)'", block.group(1)):
        tokens.update(t)
    out["tokens"] = tokens
    out["fill"] = Counter(fill.group(1))

    # (d) the copy: title, wordmark, motto, tagline (both the sizer and the
    # string the typewriter types), and the static accessible line.
    copy = Counter()
    for pat in (
        r"<title>(.*?)</title>",
        r"<h1>(.*?)</h1>",
        r'<p class="motto">(.*?)</p>',
        r'<span class="tw-sizer"[^>]*>(.*?)</span>',
        r'<span class="sr-only">(.*?)</span>',
        r"var FULL = '([^']*)';",
    ):
        for hit in re.findall(pat, src, re.S):
            copy.update(html.unescape(hit))
    out["copy"] = copy

    # (e) 404.html. It carries its own @font-face onto the same latin file, so
    # its copy is served by this subset too and belongs in the inventory.
    nf = NOTFOUND.read_text(encoding="utf-8")
    body = nf[nf.index("<body>"):]
    page404 = Counter()
    for pat in (r"<title>(.*?)</title>", r"<h1>(.*?)</h1>", r"<p>(.*?)</p>", r"<a [^>]*>(.*?)</a>"):
        for hit in re.findall(pat, nf if "title" in pat else body, re.S):
            page404.update(html.unescape(hit))
    out["404"] = page404

    for c in out.values():
        c.pop("\n", None)
    return out


def ranges_of(codepoints):
    """Contiguous runs, as a CSS unicode-range value."""
    cps = sorted(codepoints)
    runs, start, prev = [], cps[0], cps[0]
    for c in cps[1:]:
        if c == prev + 1:
            prev = c
            continue
        runs.append((start, prev))
        start = prev = c
    runs.append((start, prev))
    return ", ".join(f"U+{a:04X}" if a == b else f"U+{a:04X}-{b:04X}" for a, b in runs)


def codepoints_of(unicode_range):
    """The inverse of ranges_of: a CSS unicode-range value as a set."""
    out = set()
    for part in unicode_range.split(","):
        part = part.strip().removeprefix("U+")
        if not part:
            continue
        lo, _, hi = part.partition("-")
        out |= set(range(int(lo, 16), int(hi or lo, 16) + 1))
    return out


def faces_in(text):
    """{asset filename: unicode-range} for every @font-face in a document.

    Parsed per BLOCK rather than by one src-then-range regex, so a comment
    between the two declarations — which is where the reasoning for the range
    belongs — does not make a face invisible to the check.
    """
    out = {}
    for block in re.findall(r"@font-face\s*\{(.*?)\}", text, re.S):
        src = re.search(r'src: url\("assets/(asset-\d+\.woff2)"\)', block)
        rng = re.search(r"unicode-range: ([^;]+);", block)
        if not src or not rng:
            die(f"@font-face with no src or no unicode-range: {block.strip()[:80]!r}")
        out[src.group(1)] = " ".join(rng.group(1).split())
    return out


def face_blocks():
    """The @font-face declarations as index.html actually states them."""
    found = faces_in(INDEX.read_text(encoding="utf-8"))
    if len(found) != 2:
        die(f"expected 2 @font-face blocks in index.html, found {len(found)}")
    return found


# ---- the check --------------------------------------------------------------


def check(report=False):
    validate_recipes()
    inv = inventory()
    need = {ch for c in inv.values() for ch in c}

    faces = {}
    for path in (GREEK, LATIN):
        if not path.exists():
            die(f"{path.relative_to(ROOT)} is missing -- run with --write")
        f = TTFont(path)
        faces[path.name] = f
    declared = face_blocks()

    # A face serves a character only if the file HAS it and the CSS lets that
    # file be requested for it. Checking both together is the end-to-end
    # property; either alone misses a real failure mode. Measured before this
    # pass: the gap was entirely cmap, 0 characters were declared-but-absent.
    served = set()
    for name, f in faces.items():
        allowed = codepoints_of(declared.get(name, ""))
        served |= {chr(cp) for cp in f.getBestCmap() if cp in allowed}

    fails = []

    # 1. coverage. The whole point: nothing the page can draw may fall through
    #    to the platform stack unless it is on the explicit allowlist. An
    #    UNPLANNED gap is a build failure, so a future field edit that reaches
    #    for a new symbol cannot quietly reopen this.
    gap = sorted(need - served, key=lambda c: -sum(v[c] for v in inv.values() if c in v))
    unexpected = [c for c in gap if c not in ALLOWLIST]
    if unexpected:
        fails.append(
            "characters the page draws that NO face serves and that are not allowlisted: "
            + " ".join(f"U+{ord(c):04X} {c}" for c in unexpected)
        )
    now_served = [c for c in ALLOWLIST if c in served]
    if now_served:
        fails.append(
            "ALLOWLIST lists characters that are now served -- take them off the list: "
            + " ".join(now_served)
        )
    # every fabricated codepoint must actually have arrived in the face
    missing_fab = [cp for cp in FABRICATED if chr(cp) not in served]
    if missing_fab:
        fails.append("fabrication did not reach the shipped face for: "
                     + " ".join(f"U+{cp:04X}" for cp in missing_fab))
    # and a borrowed component must NOT be claimed by the latin face
    for cp in COMPONENT_ONLY:
        if chr(cp) in {chr(c) for c in codepoints_of(declared.get(LATIN.name, ""))}:
            fails.append(f"U+{cp:04X} is borrowed as a component but the latin face "
                         "declares it — the greek face must keep serving it")

    # 2. the substitutions really are gone from the junk pools.
    pools = inv["tokens"] + inv["fill"]
    back = [c for c in SUBSTITUTED if c in pools]
    if back:
        fails.append("substituted characters are back in TOKENS/FILL: " + " ".join(back))

    # 3. the face this script generates must declare exactly what it contains,
    #    derived from the cmap, so the declaration cannot drift off the binary
    #    in either direction.
    #
    #    The greek face is stock Google-Fonts and is NOT regenerated here, and
    #    its declaration does not match its cmap: it promises ~52 codepoints the
    #    file lacks (the edges of U+0370-03FF) and carries 8 it never declares
    #    (CR, space, NBSP, A, Aacute, Abreve, U+0374-0375, U+037E) as leftovers
    #    of Google's build. Both are inert. The latin face is declared second
    #    and wins every overlap, so the undeclared latin characters in the greek
    #    file are unreachable either way; and a character landing in the
    #    promised-but-absent set would fail check 1 above, which is the check
    #    that matters. Holding a file we do not build to an equality we would
    #    then have to satisfy by rewriting its declaration buys nothing.
    for asset in declared:
        if asset not in faces:
            fails.append(f"index.html points at assets/{asset}, which is not a shipped face")
    if LATIN.name in declared:
        want = ranges_of(faces[LATIN.name].getBestCmap())
        got = " ".join(declared[LATIN.name].split())
        if got != want:
            fails.append(f"assets/{LATIN.name}: unicode-range does not match its cmap\n"
                         f"    declared: {got}\n"
                         f"    actual:   {want}")

    # 4. every preload must point at a face that is actually declared, and vice
    #    versa. A preload for a file nothing requests is a wasted round trip and
    #    a console warning; a face with no preload re-opens the extra layout
    #    pass the preload comment in index.html exists to prevent.
    src = INDEX.read_text(encoding="utf-8")
    preloads = set(re.findall(r'<link rel="preload" href="assets/(asset-\d+\.woff2)"', src))
    if preloads != set(declared):
        fails.append(f"preloads {sorted(preloads)} != declared faces {sorted(declared)}")

    # 5. 404.html carries its own copy of the latin face. It has no preload and
    #    needs none (no field to lay out), but its src and range must not drift.
    nf404 = faces_in(NOTFOUND.read_text(encoding="utf-8"))
    if list(nf404) != [LATIN.name]:
        fails.append(f"404.html declares {sorted(nf404)}, expected exactly [{LATIN.name}]")
    elif nf404[LATIN.name] != declared.get(LATIN.name):
        fails.append("404.html's unicode-range has drifted from index.html's")

    # 6. the tagline and the 404 link both ask for weight 500, and both faces
    #    declare `font-weight: 400 500`. That is only true if the axis spans it.
    for name, f in faces.items():
        if "fvar" not in f:
            fails.append(f"assets/{name} is no longer a variable font -- weight 500 would be synthesised")
            continue
        wght = [a for a in f["fvar"].axes if a.axisTag == "wght"]
        if not wght or wght[0].minValue > 400 or wght[0].maxValue < 500:
            fails.append(f"assets/{name}: wght axis {wght} does not span 400-500")

    # ---- report
    total = sum(sum(c.values()) for c in inv.values())
    print(f"inventory: {len(need)} distinct characters, {total} occurrences "
          "(567 field fragments, TOKENS, FILL, copy, 404)")
    for name, f in faces.items():
        cm = f.getBestCmap()
        wght = [(a.minValue, a.defaultValue, a.maxValue) for a in f["fvar"].axes if a.axisTag == "wght"]
        print(f"  assets/{name}: {len(cm)} codepoints, {f['maxp'].numGlyphs} glyphs, "
              f"wght {wght[0] if wght else '-'}, {(ROOT / 'assets' / name).stat().st_size} bytes")
    fabricated = sorted(cp for cp in FABRICATED if chr(cp) in served)
    native = len(need & served) - len(fabricated)
    print(f"census: {native} native, {len(fabricated)} fabricated, "
          f"{len(gap) - len(unexpected)} allowlisted, {len(unexpected)} unplanned  "
          f"(of {len(need)} distinct; {len(SUBSTITUTED)} more substituted out of the junk pools)")

    if report:
        print("\nfabricated -- composites built from 2.211's own outlines:")
        latin = faces[LATIN.name]
        lglyf = latin["glyf"]
        for cp in fabricated:
            kind, parts, note = FABRICATED[cp]
            g = lglyf[latin.getBestCmap()[cp]]
            comps = ",".join(c.glyphName for c in g.components)
            print(f"  U+{cp:04X} {chr(cp)}  {inv['field'][chr(cp)]:3} in field  "
                  f"{kind:8} <- {comps:24} {note}")
        print("\nallowlisted -- still on the platform fallback, with the reason:")
        for c in sorted(ALLOWLIST, key=lambda c: -ALLOWLIST[c][0]):
            freq, why = ALLOWLIST[c]
            print(f"  U+{ord(c):04X} {c}  {freq:3} in field  {why}")
        print("\nsubstituted in the junk pools:")
        for a, b in SUBSTITUTED.items():
            print(f"  U+{ord(a):04X} {a} -> {b}")

    if fails:
        print()
        for f in fails:
            print(f"FAIL: {f}")
        die(f"{len(fails)} check(s) failed")
    print("OK: every character the page draws is served, or documented as unservable")


# ---- the build --------------------------------------------------------------


def fetch_source(dest):
    print(f"fetching {SOURCE_URL}")
    data = urllib.request.urlopen(SOURCE_URL, timeout=60).read()
    got = hashlib.sha256(data).hexdigest()
    if got != SOURCE_SHA256:
        die(f"source sha256 mismatch\n  expected {SOURCE_SHA256}\n  got      {got}")
    dest.write_bytes(data)
    version = TTFont(dest)["name"].getDebugName(5)
    if version != SOURCE_VERSION:
        die(f"source is {version!r}, expected {SOURCE_VERSION!r}")
    print(f"  {len(data)} bytes, {SOURCE_VERSION}, sha256 verified")


def decomposed(font):
    """Glyph outlines with composites flattened, keyed by codepoint.

    Flattening matters: comparing composites by component NAME reports
    differences that are only naming (post format 3.0 renames every component
    to glyphNNNNN) while the geometry is identical.
    """
    gs = font.getGlyphSet()
    out = {}
    for cp, gname in font.getBestCmap().items():
        pen = DecomposingRecordingPen(gs)
        gs[gname].draw(pen)
        out[cp] = tuple(pen.value)
    return out


# Font dates count from 1904-01-01, Unix time from 1970-01-01.
# ---- fabrication ------------------------------------------------------------
#
# FABRICATION NOTES — why these constants are measured and not chosen.
#
# The face already tells us how JetBrains Mono relates a base glyph to its
# superscript and its subscript. Two facts, both read off 2.211 itself:
#
#  1. Its SUBSCRIPTS ARE ALREADY COMPOSITES. U+2082 is literally U+00B2 with an
#     offset of (0, -515) and no scaling; U+2080 and U+2086 are the same over
#     their own superscripts. So the superscript->subscript drop is not a value
#     anyone here picked — it is read out of the font's own composite and is
#     exact by construction.
#  2. Its SUPERSCRIPTS ARE DRAWN, not scaled, so the base->superscript transform
#     has to be fitted. Across the ten native digit pairs (0-9 against U+2070,
#     U+00B9, U+00B2, U+00B3, U+2074-2079) the HEIGHT ratio is tight — spread
#     0.011 — while the WIDTH ratio is loose, spread 0.067. That gap is the
#     designer optically widening the superscripts so their stems survive at
#     small size, and it is why the x and y scales here are fitted SEPARATELY
#     rather than forced equal. A single uniform scale would reproduce the
#     height correctly and come out ~12% too narrow and correspondingly too
#     light. Fitting both axes reproduces the font's own proportions.
#
# Signs are fitted apart from letters because they are positioned apart: a digit
# sits on the baseline, a sign sits on the math axis. The face has exactly one
# native base->superscript SIGN pair, + against U+207A, so that transform is
# taken from it exactly rather than averaged. It lands the fabricated superscript
# minus on the same crossbar height as the native superscript plus — which is the
# thing that actually has to match, since the field sets them side by side.
#
# The derivation is checked against a glyph it did not come from: predicting
# U+2082's y-range from the digit fit reproduces the real one within ~2 units.
#
# WHY NO gvar ENTRIES ARE NEEDED. Every component here carries its own gvar
# deltas, and a composite with no gvar entry simply inherits whatever its
# components do — so these glyphs get heavier with weight for free. What a gvar
# entry would buy is VARYING OFFSETS, and the offsets here are constant: the face
# is monospace at 600 units per em at every weight, so the centring never has to
# move. The one honest caveat is that the offsets are computed from bounding
# boxes measured at the default instance, wght 400. At heavier weights the ink
# grows slightly and the centring drifts sub-unit. The field renders at 400 and
# nothing else on the page uses these characters at all, so that drift is never
# on screen.
FONT_EPOCH_OFFSET = 2082844800
ADVANCE = 600  # monospace, every weight


def _gname(font, cp):
    """Resolve a component by codepoint. Never assume a production name."""
    name = font.getBestCmap().get(cp)
    if name is None:
        die(f"component U+{cp:04X} is not in the face — cannot fabricate from it")
    return name


def _bbox(glyf, name):
    g = glyf[name]
    g.recalcBounds(glyf)
    return g.xMin, g.yMin, g.xMax, g.yMax


def _leaves(glyf, name, sx=1.0, sy=1.0, dx=0.0, dy=0.0, depth=0):
    """Flatten a composite to its simple glyphs, composing transforms.

    Nesting a composite inside a composite is legal but thinly exercised in
    rasterizers, and `i` and `j` in this face are themselves composites. So the
    tree is flattened instead: every fabricated glyph references only simple
    outlines, one level deep.
    """
    if depth > 4:
        die(f"component nesting deeper than 4 at {name}")
    g = glyf[name]
    if not g.isComposite():
        return [(name, sx, sy, dx, dy)]
    out = []
    for c in g.components:
        t = getattr(c, "transform", [[1, 0], [0, 1]])
        out += _leaves(glyf, c.glyphName, sx * t[0][0], sy * t[1][1],
                       dx + sx * getattr(c, "x", 0), dy + sy * getattr(c, "y", 0), depth + 1)
    return out


def validate_recipes():
    """Check each recipe's kind against what Unicode says the character IS.

    This exists because it caught a real error: U+1D58 was written as a subscript
    when it is a MODIFIER LETTER, which Unicode places RAISED. The field uses it
    as a contravariant tensor index next to another modifier letter, so the two
    halves of the same index pair would have sat at opposite heights. Cheap to
    assert, and the failure mode is one that looks plausible in isolation and
    only reads as wrong in context.
    """
    for cp, (kind, _parts, _note) in sorted(FABRICATED.items()):
        try:
            name = unicodedata.name(chr(cp))
        except ValueError:
            continue
        raised = "SUPERSCRIPT" in name or "MODIFIER LETTER" in name
        lowered = "SUBSCRIPT" in name
        if not (raised or lowered):
            continue                       # flip/stack kinds carry no height claim
        want = "sup" if raised else "sub"
        got = kind.replace("sign", "")
        if got != want:
            die(f"U+{cp:04X} {chr(cp)} is built as {kind!r} but Unicode calls it "
                f"{name!r} — that is a {want}")


def _crossbar(glyf, name):
    """(thickness, centre y) of a sign's horizontal bar.

    Taken as the two outline y-values nearest the glyph's vertical middle, which
    is the crossbar band for a plus and the whole glyph for a minus. Both of the
    glyphs this is used on are 4-point or 12-point rectilinear signs, so there is
    nothing to disambiguate.
    """
    g = glyf[name]
    while g.isComposite():
        g = glyf[g.components[0].glyphName]
    ys = sorted({y for _x, y in g.coordinates})
    if len(ys) < 2:
        die(f"{name} has no horizontal bar to measure")
    mid = (ys[0] + ys[-1]) / 2
    a, b = sorted(sorted(ys, key=lambda y: abs(y - mid))[:2])
    return b - a, (a + b) / 2


def derive_transforms(font):
    """Measure the base->superscript and superscript->subscript relations."""
    glyf = font["glyf"]
    pairs = [(0x30, 0x2070), (0x31, 0x00B9), (0x32, 0x00B2), (0x33, 0x00B3), (0x34, 0x2074),
             (0x35, 0x2075), (0x36, 0x2076), (0x37, 0x2077), (0x38, 0x2078), (0x39, 0x2079)]
    sx, sy, dy = [], [], []
    for base, sup in pairs:
        b = _bbox(glyf, _gname(font, base))
        s = _bbox(glyf, _gname(font, sup))
        sx.append((s[2] - s[0]) / (b[2] - b[0]))
        ys = (s[3] - s[1]) / (b[3] - b[1])
        sy.append(ys)
        dy.append(s[1] - ys * b[1])
    t = {
        "sx": sum(sx) / len(sx), "sy": sum(sy) / len(sy), "dy": sum(dy) / len(dy),
        "sx_spread": max(sx) - min(sx), "sy_spread": max(sy) - min(sy),
    }
    # Signs are fitted from the one native base->superscript SIGN pair, + against
    # U+207A, and they are fitted on TWO measurements rather than one, because a
    # single scale gets the stroke weight visibly wrong. The native superscript
    # plus is 0.5957 of the base's WIDTH but its crossbar is 0.875 of the base's
    # THICKNESS: the designer shrank the sign's extent and kept its stroke almost
    # intact. Scaling uniformly instead produced a superscript minus 48 units
    # thick sitting beside a native 70 — a 31% weight jump, measured, and the one
    # place in this pass where that was visible rather than theoretical.
    # A bar is the one shape where extent and weight can both be matched exactly,
    # because its bbox height IS its thickness. The result reproduces the native
    # superscript plus's crossbar band to the unit.
    pb = _bbox(glyf, _gname(font, 0x2B))
    ps = _bbox(glyf, _gname(font, 0x207A))
    t["sxsign"] = (ps[2] - ps[0]) / (pb[2] - pb[0])
    bt, bc = _crossbar(glyf, _gname(font, 0x2B))
    st, sc = _crossbar(glyf, _gname(font, 0x207A))
    t["sysign"] = st / bt
    t["dysign"] = sc - t["sysign"] * bc
    if not 0.7 < t["sysign"] <= 1.0:
        die(f"derived sign thickness ratio {t['sysign']:.4f} is outside 0.7-1.0")

    sub = glyf[_gname(font, 0x2082)]
    if not sub.isComposite() or len(sub.components) != 1:
        die("U+2082 is no longer a single-component composite — the subscript drop "
            "was read from it and can no longer be trusted")
    t["subdy"] = sub.components[0].y

    for k in ("sx", "sy", "sxsign"):
        if not 0.5 < t[k] < 0.8:
            die(f"derived {k}={t[k]:.4f} is outside the sane 0.5-0.8 band")
    # Predict a glyph the fit did not come from, and check it.
    b = _bbox(glyf, _gname(font, 0x32))
    native = _bbox(glyf, _gname(font, 0x2082))
    pred = (t["sy"] * b[1] + t["dy"] + t["subdy"], t["sy"] * b[3] + t["dy"] + t["subdy"])
    err = max(abs(pred[0] - native[1]), abs(pred[1] - native[3]))
    if err > 6:
        die(f"the derivation misses the font's own U+2082 by {err:.1f} units")
    t["check_err"] = err
    return t


def _component(name, sx, sy, dx, dy):
    c = GlyphComponent()
    c.glyphName = name
    c.x, c.y = int(round(dx)), int(round(dy))
    c.flags = ARGS_ARE_XY_VALUES | ROUND_XY_TO_GRID
    if (sx, sy) != (1.0, 1.0):
        c.transform = [[sx, 0], [0, sy]]
    return c


def fabricate(font, transforms):
    """Append the composite glyphs. Sorted by codepoint, so builds are stable."""
    validate_recipes()
    glyf = font["glyf"]
    t = transforms
    made = []
    for cp in sorted(FABRICATED):
        kind, parts, _note = FABRICATED[cp]
        names = [_gname(font, p) for p in parts]
        if kind in ("sup", "sub", "supsign", "subsign"):
            sign = kind.endswith("sign")
            sx = t["sxsign"] if sign else t["sx"]
            sy = t["sysign"] if sign else t["sy"]
            dy = t["dysign"] if sign else t["dy"]
            if kind.startswith("sub"):
                dy += t["subdy"]
            b = _bbox(glyf, names[0])
            # re-centre the shrunken ink in the advance; a scale about the origin
            # would otherwise leave every superscript hard against the left side
            dx = (ADVANCE - sx * (b[2] - b[0])) / 2 - sx * b[0]
            comps = _leaves(glyf, names[0], sx, sy, dx, dy)
        elif kind == "flip":
            b = _bbox(glyf, names[0])
            # mirror about the component's own bbox: y -> (yMin+yMax) - y, which
            # re-seats it on exactly the baseline and cap-height it occupied.
            comps = _leaves(glyf, names[0], 1.0, -1.0, 0.0, b[1] + b[3])
        elif kind == "stack":
            base, mark = names
            bb, mb = _bbox(glyf, base), _bbox(glyf, mark)
            # centre the mark over the base's ink, the way the face's own i does
            dx = (bb[0] + bb[2]) / 2 - (mb[0] + mb[2]) / 2
            comps = _leaves(glyf, base) + _leaves(glyf, mark, 1.0, 1.0, dx, 0.0)
        else:
            die(f"unknown fabrication kind {kind!r}")

        g = Glyph()
        g.numberOfContours = -1
        g.components = [_component(*c) for c in comps]
        name = f"uni{cp:04X}"
        if name in glyf.glyphs:
            die(f"{name} already exists — fabricating over a real glyph")
        glyf.glyphs[name] = g
        font.setGlyphOrder(font.getGlyphOrder() + [name])
        glyf.glyphOrder = font.getGlyphOrder()
        g.recalcBounds(glyf)
        font["hmtx"].metrics[name] = (ADVANCE, g.xMin)
        for sub in font["cmap"].tables:
            sub.cmap[cp] = name
        made.append((cp, name, (g.xMin, g.yMin, g.xMax, g.yMax), len(g.components)))
    font["maxp"].numGlyphs = len(font.getGlyphOrder())
    return made


def strip_component_only(font):
    """Drop the cmap entries for glyphs borrowed purely as components.

    U+0394 is served by the GREEK face. The latin face needs its outline for the
    nabla, but must not claim the codepoint — otherwise the derived unicode-range
    would grow to cover it and, being declared second, would win the overlap and
    pull greek text out of the greek file.
    """
    for cp in sorted(COMPONENT_ONLY):
        for sub in font["cmap"].tables:
            sub.cmap.pop(cp, None)





def run(*argv, epoch=None):
    """Run a fontTools module, optionally pinning the build timestamp.

    Without this the output is not reproducible: fontTools stamps head.modified
    with the wall clock, so two builds of identical inputs differ in exactly
    those 4 bytes and nothing else (measured). SOURCE_DATE_EPOCH is the standard
    lever for that, and pinning it to the SOURCE font's own date means the
    output inherits a timestamp rather than inventing one.
    """
    env = dict(os.environ)
    if epoch is not None:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    subprocess.run(argv, check=True, capture_output=True, text=True, env=env)


def build():
    inv = inventory()
    need = {ch for c in inv.values() for ch in c}

    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        source_ttf = td / "source.ttf"
        fetch_source(source_ttf)
        src_cmap = set(TTFont(source_ttf).getBestCmap())

        # Whatever the greek face already serves stays there. Both faces are
        # named 'JetBrains Mono', so a codepoint declared by both would be
        # resolved by declaration order rather than by intent -- and it would
        # move greek text into the latin file for no reason.
        declared_greek = codepoints_of(face_blocks()[GREEK.name])

        # The shipped latin face is the floor: never regress a codepoint it
        # already served, whether or not today's copy happens to use it. That
        # makes a rebuild monotonic — coverage can only ever grow.
        floor = set(TTFont(LATIN).getBestCmap()) if LATIN.exists() else set()
        wanted = {ord(c) for c in need if ord(c) in src_cmap and ord(c) not in declared_greek}
        # Fabricated codepoints are NOT requested from the subsetter — 2.211 has
        # no glyphs for them. What has to be requested is their COMPONENTS, plus
        # any borrowed outline the latin face would not otherwise carry.
        components = {c for _k, parts, _n in FABRICATED.values() for c in parts}
        subset_cps = (floor | wanted | components | COMPONENT_ONLY) - set(FABRICATED)
        target = sorted(subset_cps)
        print(f"latin target: {len(target)} codepoints from the subsetter "
              f"({len(floor & subset_cps)} kept from the shipped face, "
              f"{len(subset_cps - floor)} added), plus {len(FABRICATED)} fabricated")

        epoch = TTFont(source_ttf)["head"].modified - FONT_EPOCH_OFFSET
        cps = td / "cps.txt"
        cps.write_text("\n".join(f"{c:04X}" for c in target))
        inst, out = td / "inst.ttf", td / "out.woff2"
        run(sys.executable, "-m", "fontTools.varLib.instancer", str(source_ttf),
            AXIS_LIMIT, "-o", str(inst), epoch=epoch)
        run(sys.executable, "-m", "fontTools.subset", str(inst),
            f"--unicodes-file={cps}", f"--layout-features={LAYOUT_FEATURES}",
            "--flavor=woff2", f"--output-file={out}",
            "--no-hinting", "--glyph-names", "--drop-tables+=HVAR", epoch=epoch)

        # ---- fabrication, on the subsetted font, before it is saved ---------
        # recalcTimestamp=False, or save() re-stamps head.modified from the wall
        # clock and undoes the epoch the subsetter was given — assigning the
        # field by hand is not enough, the head table overwrites it at compile.
        # recalcBBoxes stays ON: the fabricated composites need real bounds.
        font = TTFont(out, recalcTimestamp=False)
        # gvar validates its glyphCount against the glyph order when it is
        # decompiled, so every table has to be read BEFORE the order grows —
        # otherwise the first access after fabrication asserts.
        font.ensureDecompiled()
        t = derive_transforms(font)
        print(f"derived from the face: letters x{t['sx']:.4f} y{t['sy']:.4f} dy{t['dy']:.1f} "
              f"(spreads {t['sx_spread']:.4f}/{t['sy_spread']:.4f}); "
              f"signs x{t['sxsign']:.4f} y{t['sysign']:.4f} dy{t['dysign']:.1f}; "
              f"subscript drop {t['subdy']}; self-check off by {t['check_err']:.1f} units")
        made = fabricate(font, t)
        strip_component_only(font)
        font.save(out)
        for cp, name, bb, ncomp in made:
            print(f"  fabricated U+{cp:04X} {chr(cp)} as {name}: {ncomp} component(s), bbox {bb}")

        # Prove no glyph that came from the pinned source was redrawn. This is
        # the whole justification for pinning google/fonts rather than JetBrains
        # upstream, so it is asserted on every build, not taken on trust.
        #
        # Fabricated codepoints are held to a DIFFERENT standard, deliberately.
        # They are regenerated from the derived constants every build, so an
        # improvement to a recipe legitimately moves one — and a guard that
        # cannot tell that apart from an upstream glyph drifting would make the
        # recipes unimprovable. So: native outlines may never move, fabricated
        # ones may, and when they do it is printed rather than swallowed.
        if LATIN.exists():
            was, now = decomposed(TTFont(LATIN)), decomposed(TTFont(out))
            fab = set(FABRICATED)
            drift = [cp for cp in was if cp in now and was[cp] != now[cp]]
            native_drift = [cp for cp in drift if cp not in fab]
            lost = [cp for cp in was if cp not in now]
            if native_drift:
                die(f"{len(native_drift)} glyphs from the pinned source were redrawn: "
                    + " ".join(f"U+{c:04X}" for c in native_drift[:20]))
            if lost:
                die(f"{len(lost)} codepoints regressed out of the face: "
                    + " ".join(f"U+{c:04X}" for c in lost[:20]))
            print(f"outline check: {len(was) - len(was.keys() & fab)} native codepoints, "
                  f"0 redrawn, 0 lost")
            for cp in sorted(set(drift) & fab):
                print(f"  RECIPE CHANGED: U+{cp:04X} {chr(cp)} differs from the shipped build")

        LATIN.write_bytes(out.read_bytes())
        built = TTFont(LATIN)
        print(f"wrote {LATIN.relative_to(ROOT)}: {LATIN.stat().st_size} bytes, "
              f"{len(built.getBestCmap())} codepoints, {built['maxp'].numGlyphs} glyphs")
        print("\npaste this unicode-range into index.html and 404.html:")
        print(f"  unicode-range: {ranges_of(built.getBestCmap())};")


def main():
    args = sys.argv[1:]
    if "--write" in args:
        build()
        print()
    check(report="--report" in args)


if __name__ == "__main__":
    main()

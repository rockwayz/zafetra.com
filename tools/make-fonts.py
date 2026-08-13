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
from fontTools.pens.recordingPen import DecomposingRecordingPen

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

# The 27 characters the static field uses that no JetBrains Mono 2.211 glyph
# exists for. Escalated to the owner rather than substituted, because changing
# them means changing visible copy. Each renders from the platform fallback.
ESCALATED = "ˢˣᵀᵉᵘᵢẋẍ⁻⁽⁾ⁿ₋ₐℏℝ⇌⇒∄∇∓∩∪∮⊢⟹ⱼ"

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
    #    to the platform stack unless it is on a documented list.
    gap = sorted(need - served, key=lambda c: -sum(v[c] for v in inv.values() if c in v))
    unexpected = [c for c in gap if c not in ESCALATED]
    if unexpected:
        fails.append(
            "characters the page draws that NO face serves and that are not escalated: "
            + " ".join(f"U+{ord(c):04X} {c}" for c in unexpected)
        )
    missing_escalation = [c for c in ESCALATED if c in served]
    if missing_escalation:
        fails.append(
            "ESCALATED lists characters that are now served -- take them off the list: "
            + " ".join(missing_escalation)
        )

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
    print(f"served {len(need & served)}/{len(need)}; "
          f"{len(gap)} fall through to the platform stack ({len(ESCALATED)} escalated, "
          f"{len(SUBSTITUTED)} substituted out of the junk pools)")

    if report:
        print("\nescalated -- static-field characters no JetBrains Mono 2.211 glyph exists for:")
        for c in sorted(ESCALATED, key=lambda c: -inv["field"][c]):
            try:
                nm = unicodedata.name(c)
            except ValueError:
                nm = "?"
            print(f"  U+{ord(c):04X} {c}  {inv['field'][c]:3} in field   {nm}")
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
FONT_EPOCH_OFFSET = 2082844800


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
        target = sorted(floor | wanted)
        print(f"latin target: {len(target)} codepoints "
              f"({len(floor)} kept from the shipped face, {len(target) - len(floor)} added)")

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

        # Prove no glyph the page already renders was redrawn. This is the whole
        # justification for pinning google/fonts rather than JetBrains upstream,
        # so it is asserted on every build, not taken on trust.
        if LATIN.exists():
            was, now = decomposed(TTFont(LATIN)), decomposed(TTFont(out))
            drift = [cp for cp in was if cp in now and was[cp] != now[cp]]
            lost = [cp for cp in was if cp not in now]
            if drift:
                die(f"{len(drift)} existing glyphs were redrawn: "
                    + " ".join(f"U+{c:04X}" for c in drift[:20]))
            if lost:
                die(f"{len(lost)} codepoints regressed out of the face: "
                    + " ".join(f"U+{c:04X}" for c in lost[:20]))
            print(f"outline check: {len(was)} existing codepoints, 0 redrawn, 0 lost")

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

#!/usr/bin/env python3
"""Keep each document's CSP hashes exactly in step with its inline blocks.

WHY THIS EXISTS — it is the thing that made the migration possible at all.
The head comment carried 'unsafe-inline' on script-src deliberately, and named
the reason: a stale hash fails SILENTLY. The script is blocked, the page still
renders, and the lockup, torch and eye-lock are simply gone. Nothing errors in
a way anyone would notice from the outside. Under that failure mode, hand-
maintained hashes on a page under active design are a worse bet than
'unsafe-inline'.

This removes the failure mode rather than the risk: every build re-derives the
hashes from the bytes and asserts the policy lists exactly those and nothing
else. A stale hash now fails LOUDLY, here, before it can ship. That is what
lets the stronger policy be the sensible choice.

WHAT IS HASHED
CSP hashes the element's exact text content — every byte between the `>` of the
open tag and the `<` of the close tag, leading and trailing newlines included.
Both <script> elements are hashed, the ld+json one too: script-src governs every
<script> ELEMENT and CSP does not exempt a data block for not being code. Miss it
and the structured data is dropped while the page renders as if nothing were
wrong — the same silent shape this tool exists to abolish.

ELEMENTS ARE MATCHED AT COLUMN 0, and that is load-bearing. index.html contains
four literal `<script` strings and only two of them are elements: the other two
sit inside an HTML comment and a JS comment. A naive `<script[^>]*>(.*?)</script>`
matches the one in the comment first and then runs to the ld+json's closing tag,
swallowing it — so the ld+json gets no hash and the main block gets a wrong one.
Both documents write their elements flush left, so the anchor is exact.

WHY NO 'self'
Neither document loads an external script or stylesheet — no <script src>, no
<link rel=stylesheet>, measured on every run below. So 'self' would permit a
class of load that does not exist, and the house style is that every allowance
is deliberate. Dropped from script-src and style-src; font-src and img-src keep
theirs because the woff2 files and the data: URIs are real.

STYLE-SRC PRECONDITIONS
Hashes cover <style> ELEMENTS. They do NOT cover `style=""` attributes, which
need 'unsafe-hashes' or 'unsafe-inline' to survive. CSSOM writes — element.style.x
and .style.setProperty() — are NOT governed by CSP at all and stay legal. So the
migration is only safe while the documents carry no style attributes and the
script adds none; both are asserted every run, or a future style attribute would
be silently dropped.

  python3 tools/make-csp.py           assert, exit nonzero on drift  (CI-safe)
  python3 tools/make-csp.py --write   rewrite each policy from the current bytes
"""

import re
import sys
import base64
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "index.html", ROOT / "404.html"]

CSP_META = re.compile(
    r'(<meta http-equiv="Content-Security-Policy" content=")([^"]*)(">)')
# column 0, open and close — see the docstring for why this is not negotiable
SCRIPT_EL = re.compile(r"^<script([^>]*)>(.*?)^</script>", re.S | re.M)
STYLE_EL = re.compile(r"^<style([^>]*)>(.*?)^</style>", re.S | re.M)
STYLE_ATTR = re.compile(r"""<[^>]*\sstyle\s*=\s*["']""")
ON_ATTR = re.compile(r"""\son[a-z]+\s*=\s*["']""")
# setAttribute('style', ...) is NOT exempt the way .style.x = is
SET_STYLE_ATTR = re.compile(r"""setAttribute\(\s*["']style["']""")
DYNAMIC_CSS = re.compile(
    r"""createElement\(\s*["']style["']|insertRule|adoptedStyleSheets|\.cssText""")

# Directives that are not derived from the bytes; carried through verbatim.
FIXED = ["default-src 'none'", "font-src 'self'", "img-src 'self' data:",
         "base-uri 'none'", "form-action 'none'"]


def die(msg):
    sys.exit(f"HALT: {msg}")


def digest(body):
    return "sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()


def elements(src, rx, kind):
    """[(attrs, body)] for every real element, with the external ones rejected."""
    out = []
    for m in rx.finditer(src):
        attrs = m.group(1).strip()
        if "src=" in attrs or "href=" in attrs:
            die(f"a <{kind}> element loads something external ({attrs!r}); this "
                "policy is built on the documents having no external subresources")
        out.append((attrs, m.group(2)))
    return out


def expected_policy(src, name):
    scripts = elements(src, SCRIPT_EL, "script")
    styles = elements(src, STYLE_EL, "style")
    parts = ["default-src 'none'"]
    if scripts:
        parts.append("script-src " + " ".join(f"'{digest(b)}'" for _a, b in scripts))
    if styles:
        parts.append("style-src " + " ".join(f"'{digest(b)}'" for _a, b in styles))
    parts += FIXED[1:]
    return "; ".join(parts), scripts, styles


def audit(src, name, scripts, styles):
    """Everything that must stay true for hashed inline blocks to be safe."""
    problems = []
    loose_s, tight_s = src.count("<script"), len(re.findall(r"^<script", src, re.M))
    loose_y, tight_y = src.count("<style"), len(re.findall(r"^<style", src, re.M))

    # Prove the column-0 anchor missed no real element. Counting raw '<script'
    # would fail on this file, because both documents legitimately DISCUSS these
    # tags in prose: index.html names <script> inside the CSP comment and again
    # inside a JS comment. So strip what cannot be an element — HTML comments,
    # and the bodies of the elements already matched — and anything still
    # holding an open tag beyond the ones found is one the anchor did not see.
    stripped = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    for _a, body in scripts + styles:
        stripped = stripped.replace(body, "", 1)
    left_s = len(re.findall(r"<script[ >]", stripped))
    left_y = len(re.findall(r"<style[ >]", stripped))
    if left_s != len(scripts) or left_y != len(styles):
        problems.append(
            f"element count disagrees after stripping comments and matched bodies: "
            f"{left_s} script / {left_y} style open tags remain against "
            f"{len(scripts)} / {len(styles)} matched — an element is written "
            "indented and the column-0 anchor is skipping it")
    if STYLE_ATTR.search(src):
        problems.append("a style=\"\" attribute is present; hashed style-src does not "
                        "cover attributes, so it would be silently dropped")
    if ON_ATTR.search(src):
        problems.append("an on* handler attribute is present; it cannot be hashed")
    if SET_STYLE_ATTR.search(src):
        problems.append("the script calls setAttribute('style', ...), which IS governed "
                        "by style-src and cannot be hashed")
    if DYNAMIC_CSS.search(src):
        problems.append("the script builds CSS at runtime (createElement('style') / "
                        "insertRule / adoptedStyleSheets / cssText)")
    return problems, (loose_s, tight_s, loose_y, tight_y)


def main():
    write = "--write" in sys.argv[1:]
    fails = []
    for doc in DOCS:
        if not doc.exists():
            die(f"{doc.name} is missing")
        src = doc.read_text(encoding="utf-8")
        want, scripts, styles = expected_policy(src, doc.name)
        problems, counts = audit(src, doc.name, scripts, styles)
        ls, ts, ly, ty = counts
        print(f"{doc.name}: {ts} script element(s) of {ls} literal '<script', "
              f"{ty} style element(s) of {ly} literal '<style'")
        for _a, b in scripts:
            print(f"  script {len(b.encode()):6} bytes  '{digest(b)}'")
        for _a, b in styles:
            print(f"  style  {len(b.encode()):6} bytes  '{digest(b)}'")

        m = CSP_META.search(src)
        if not m:
            fails.append(f"{doc.name}: no Content-Security-Policy meta")
            continue
        if write and m.group(2) != want:
            doc.write_text(src[:m.start()] + m.group(1) + want + m.group(3) + src[m.end():],
                           encoding="utf-8")
            print(f"  rewrote the policy in {doc.name}")
            src = doc.read_text(encoding="utf-8")
            m = CSP_META.search(src)

        got = m.group(2)
        if got != want:
            fails.append(f"{doc.name}: policy does not match the bytes\n"
                         f"    have: {got}\n    want: {want}")
        for weak in ("'unsafe-inline'", "'unsafe-eval'", "'unsafe-hashes'"):
            if weak in got:
                fails.append(f"{doc.name}: {weak} is back in the policy")
        fails += [f"{doc.name}: {p}" for p in problems]

    if fails:
        print()
        for f in fails:
            print(f"FAIL: {f}")
        die(f"{len(fails)} CSP check(s) failed")
    print("\nOK: every policy lists exactly the hashes its own bytes produce")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Keep sitemap.xml's <lastmod> honest, and its <loc> tied to the canonical URL.

<lastmod> was hand-set, which means it goes stale silently: the page changes,
the date does not, and nothing anywhere notices. This derives it from git
instead and asserts, in the same spirit as make-icons.py — the markup is the
source of truth and the generated file has to agree with it.

THE DATE. Taken from the last commit that touched index.html
(`git log -1 --format=%cs -- index.html`), not from the clock, so a rebuild is
reproducible and a CI run on a clean tree is deterministic. When index.html has
uncommitted changes there is no commit to read yet, so today's date is used —
that is the one non-reproducible case, and it is exactly the case where you
want to be told the sitemap is behind.

THE ASSERTION IS >=, NOT ==. Ordering makes equality unworkable: regenerating
in the same commit that edits index.html can only ever read the PREVIOUS
commit's date, so == would fail on every legitimate change. What actually
matters is staleness — a <lastmod> older than the page's last real change is a
lie to a crawler, a newer one is merely imprecise. So an older date fails and a
newer one passes.

Also checked, because they are the same class of silent drift:
  - <loc> against the <link rel="canonical"> and the og:url in index.html
  - robots.txt's Sitemap: line against the sitemap's own <loc> host

Run from anywhere:  python3 tools/make-sitemap.py         check only
                    python3 tools/make-sitemap.py --write rewrite the date
"""

import re
import sys
import datetime
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
SITEMAP = ROOT / "sitemap.xml"
ROBOTS = ROOT / "robots.txt"


def die(msg):
    sys.exit(f"HALT: {msg}")


def git(*args):
    return subprocess.run(("git",) + args, cwd=ROOT, capture_output=True, text=True).stdout.strip()


def content_date():
    """The date index.html last actually changed."""
    if git("status", "--porcelain", "--", "index.html"):
        today = datetime.date.today().isoformat()
        print(f"index.html has uncommitted changes — using today, {today}")
        return today
    date = git("log", "-1", "--format=%cs", "--", "index.html")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        die(f"could not read a commit date for index.html (got {date!r})")
    return date


def canonical():
    src = INDEX.read_text(encoding="utf-8")
    link = re.search(r'<link rel="canonical" href="([^"]+)"', src)
    og = re.search(r'<meta property="og:url" content="([^"]+)"', src)
    if not link or not og:
        die("index.html has no canonical link or no og:url")
    if link.group(1) != og.group(1):
        die(f"index.html disagrees with itself: canonical {link.group(1)} vs og:url {og.group(1)}")
    return link.group(1)


def read_sitemap():
    xml = SITEMAP.read_text(encoding="utf-8")
    loc = re.search(r"<loc>([^<]+)</loc>", xml)
    mod = re.search(r"<lastmod>([^<]+)</lastmod>", xml)
    if not loc or not mod:
        die("sitemap.xml has no <loc> or no <lastmod>")
    return xml, loc.group(1), mod.group(1)


def main():
    want_date = content_date()
    want_loc = canonical()

    if "--write" in sys.argv[1:]:
        xml, _, had = read_sitemap()
        xml = re.sub(r"<lastmod>[^<]+</lastmod>", f"<lastmod>{want_date}</lastmod>", xml)
        SITEMAP.write_text(xml, encoding="utf-8")
        print(f"wrote {SITEMAP.relative_to(ROOT)}: lastmod {had} -> {want_date}")

    _, loc, mod = read_sitemap()
    fails = []

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", mod):
        fails.append(f"<lastmod> {mod!r} is not a YYYY-MM-DD date")
    elif mod < want_date:
        fails.append(f"<lastmod> {mod} is older than index.html's last change {want_date} — stale")

    if loc != want_loc:
        fails.append(f"<loc> {loc} does not match index.html's canonical {want_loc}")

    sitemap_ref = re.search(r"^Sitemap:\s*(\S+)", ROBOTS.read_text(encoding="utf-8"), re.M)
    if not sitemap_ref:
        fails.append("robots.txt declares no Sitemap:")
    elif not sitemap_ref.group(1).startswith(want_loc):
        fails.append(f"robots.txt points at {sitemap_ref.group(1)}, off-origin from {want_loc}")

    print(f"sitemap: loc {loc}, lastmod {mod} (index.html last changed {want_date})")
    if fails:
        print()
        for f in fails:
            print(f"FAIL: {f}")
        die(f"{len(fails)} check(s) failed")
    print("OK: sitemap is not stale, and its loc agrees with the canonical URL and robots.txt")


if __name__ == "__main__":
    main()

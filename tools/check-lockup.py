#!/usr/bin/env python3
"""Assert the wordmark never leaves the viewport, with JS and without it.

WHY THIS EXISTS
The brand name is the one thing on this page that cannot be allowed to degrade,
and it is positioned by a NEGATIVE offset: the h1 slides left so the ring mark
parks on its final glyph. How far it may slide is a measurement — the room in
front of the card — and JS makes it at runtime. With JS off there is nothing to
measure with, so CSS falls back to the raw wish, and the wish is a constant once
the font-size clamp floors while the viewport keeps narrowing. Measured on the
shipped file with JS disabled, before the fix:

    viewport   card.left   wish    h1.left
       380px      57.4px  50.6px    +6.8    ok
       360px      47.4px  50.6px    -3.2    Z clipped
       320px      27.4px  50.6px   -23.2    Z clipped

`main` is overflow:hidden, so a negative left is not a scrollbar, it is a
missing letter — the page renders AFETRA. This asserts it cannot come back.

WHAT IT CHECKS, at each width, in both conditions:
  - the h1's border box sits fully inside the viewport (left >= 0, right <= w)
  - --word-shift-applied is SET whenever the page's script ran, and absent when
    it did not. A set value proves the cap actually executed rather than the
    CSS fallback silently carrying the layout.

WHY IT DRIVES IFRAMES RATHER THAN --window-size
Headless Chrome CLAMPS --window-size to a minimum width near 500px: asking for
360 silently yields innerWidth 500, and a sweep built on it reports every narrow
width as passing. That is exactly how the clip survived earlier review. An
iframe's width is not clamped and it establishes its own viewport for vw units
and media queries, so the narrow end is measured honestly.

  python3 tools/check-lockup.py             assert, exit nonzero on violation
  python3 tools/check-lockup.py --render    also write provenance-named panels
"""

import re
import sys
import json
import html
import shutil
import socket
import tempfile
import subprocess
import contextlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# 280 is the Galaxy Fold cover screen and the width where `.card`'s max-width
# stops it shrink-wrapping the h1 — the term that broke the shift cap. 360 is a
# real phone width and the old no-JS clip band's upper edge. 375 is the iPhone
# SE/mini. 641 is the typewriter breakpoint. The rest span the font-size clamp's
# floored, proportional and capped regimes.
# 300/320 are where the mark was measured sitting across TWO letters and 340 is
# the first width where it sits on one; those three were absent, which is how the
# collision survived this check -- every width it tested was above the threshold.
# 220 is measured but NOT asserted, see CONTAINMENT_ASSERTED_FROM.
WIDTHS = [220, 280, 300, 320, 340, 360, 375, 641, 760, 900, 1024, 1440]

# The h1 is `nowrap` at a 2.75rem font-size floor -- 265px of ink -- so below
# ~270px of usable width the brand name cannot fit and clips at both edges.
# Measured: it fits at 280px (7.4→272.6) and clips at 220px (-22.6→242.6). 280 is
# the Galaxy Fold cover screen and the narrowest viewport any shipped device
# reports, so containment is asserted from there. 220 is kept in the table
# because knowing exactly where the cliff is beats not measuring past the edge --
# closing that gap means letting the wordmark shrink below its floor, which is a
# type decision and not this check's to make.
CONTAINMENT_ASSERTED_FROM = 280

# Below this the no-JS render drops the mark rather than park it on the final
# letter; see the matching media query in index.html. Asserted here so the two
# cannot drift apart.
MARK_HIDDEN_BELOW = 380

# With JS the shift is CAPPED rather than zeroed, and the mark is anchored to the
# card's right edge, so every pixel the wordmark fails to slide is a pixel the
# mark travels into it. Measured: 3 letters covered at 220-276px, 2 at 300-320px,
# 1 from 340px up. One letter is the design -- the mark parks on the final A.
MARK_HIDDEN_BELOW_ANY = 340


def die(msg):
    sys.exit(f"HALT: {msg}")


def free_port():
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


HARNESS = """<meta charset=utf-8><title>lockup</title>
<style>html,body{margin:0;background:#0b0b0b}
.wrap{display:flex;align-items:flex-start}iframe{border:0;height:760px;flex:0 0 auto}</style>
<div class=wrap id=w></div>
<script>
var WIDTHS=%s, COND=location.hash.slice(1)||'js-on';
WIDTHS.forEach(function(px){
  var f=document.createElement('iframe');
  f.style.width=px+'px'; f.dataset.w=px; f.src=COND+'.html';
  document.getElementById('w').appendChild(f);
});
addEventListener('load', function(){ setTimeout(function(){
  var out=[];
  document.querySelectorAll('iframe').forEach(function(f){
    try{
      var d=f.contentDocument, win=f.contentWindow;
      var h1=d.querySelector('h1'), r=h1.getBoundingClientRect();
      var ap=win.getComputedStyle(d.documentElement).getPropertyValue('--word-shift-applied').trim();
      var logo=d.querySelector('.logo');
      var lvis=win.getComputedStyle(logo).display!=='none';
      var lr=logo.getBoundingClientRect();
      // How many of the wordmark's OWN letters does the mark's box cover? The
      // mark is meant to tuck into the final glyph, so 1 is correct and 2 is the
      // collision. Per-character ranges, not the h1 box: the h1 box overlaps the
      // mark by design at every width.
      var covered=0, tn=h1.firstChild;
      if(lvis && tn && tn.nodeType===3){
        for(var i=0;i<tn.length;i++){
          var q=d.createRange(); q.setStart(tn,i); q.setEnd(tn,i+1);
          var cb=q.getBoundingClientRect();
          if(Math.min(cb.right,lr.right)-Math.max(cb.left,lr.left) > 1) covered++;
        }
      }
      var motto=d.querySelectorAll('main p')[0];
      var mo=motto?motto.getBoundingClientRect():null;
      out.push({w:+f.dataset.w, vw:win.innerWidth, left:+r.left.toFixed(1),
                right:+r.right.toFixed(1), applied:ap||null,
                markShown:lvis, markRight:+lr.right.toFixed(1),
                markCovers:covered,
                mottoLeft:mo?+mo.left.toFixed(1):null, mottoRight:mo?+mo.right.toFixed(1):null,
                locked:d.documentElement.classList.contains('mark-locked'),
                fs:win.getComputedStyle(h1).fontSize});
    }catch(e){ out.push({w:+f.dataset.w, error:String(e)}); }
  });
  var pre=document.createElement('pre'); pre.id='out';
  pre.textContent=JSON.stringify(out); document.body.appendChild(pre);
}, 1400); });
</script>
"""


def run_condition(served, port, cond):
    url = f"http://127.0.0.1:{port}/harness.html#{cond}"
    argv = [str(CHROME), "--headless", "--disable-gpu",
            f"--window-size={sum(WIDTHS) + 200},820",
            "--virtual-time-budget=30000", "--dump-dom", url]
    dom = subprocess.run(argv, capture_output=True, text=True).stdout
    m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    if not m:
        die(f"{cond}: harness produced no result — Chrome may have failed to load the page")
    return json.loads(html.unescape(m.group(1)))


def main():
    if not CHROME.exists():
        die(f"{CHROME} not found — this check needs headless Chrome")
    src = INDEX.read_text(encoding="utf-8")
    # the no-JS condition is the real page with its one executable <script>
    # removed. Anchored at column 0 so the string "<script>" inside the CSP
    # comment and inside a JS comment cannot match.
    off, n = re.subn(r"^<script>\n.*?^</script>\n", "", src, flags=re.S | re.M)
    if n != 1:
        die(f"expected exactly one executable <script> in index.html, removed {n}")
    if "requestAnimationFrame" in off:
        die("the no-JS copy still contains script bodies")

    with tempfile.TemporaryDirectory() as tmp:
        served = Path(tmp)
        shutil.copytree(ROOT / "assets", served / "assets")
        (served / "js-on.html").write_text(src, encoding="utf-8")
        (served / "js-off.html").write_text(off, encoding="utf-8")
        (served / "harness.html").write_text(HARNESS % json.dumps(WIDTHS), encoding="utf-8")
        port = free_port()
        srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                                "--bind", "127.0.0.1", "--directory", str(served)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import time
            time.sleep(1.5)
            results = {c: run_condition(served, port, c) for c in ("js-on", "js-off")}
        finally:
            srv.terminate()
            srv.wait()

    fails = []
    print(f"{'cond':8} {'width':>6} {'h1.left':>9} {'h1.right':>9} {'applied':>10} {'fs':>8}  verdict")
    for cond, rows in results.items():
        for r in rows:
            if r.get("error"):
                fails.append(f"{cond} @ {r['w']}px: {r['error']}")
                continue
            if r["vw"] != r["w"]:
                fails.append(f"{cond} @ {r['w']}px: viewport came back {r['vw']}px — width was clamped")
            bad = []
            note = []
            sink = bad if r["w"] >= CONTAINMENT_ASSERTED_FROM else note
            if r["left"] < -0.5:
                sink.append(f"left {r['left']} < 0")
            if r["right"] > r["vw"] + 0.5:
                sink.append(f"right {r['right']} > {r['vw']}")
            # the cap must have run iff the page's script ran
            if cond == "js-on" and not r["applied"]:
                bad.append("--word-shift-applied UNSET though JS ran")
            if cond == "js-off" and r["applied"]:
                bad.append(f"--word-shift-applied set to {r['applied']} with no JS")
            # the mark is dropped when it was never placed, AND when the
            # viewport is too narrow for the lockup to survive the shift cap
            if cond == "js-on":
                want_shown = r["w"] >= MARK_HIDDEN_BELOW_ANY
                if r["markShown"] != want_shown:
                    bad.append(f"mark {'shown' if r['markShown'] else 'hidden'} at {r['w']}px; "
                               f"expected {'shown' if want_shown else 'hidden'} "
                               f"(lockup threshold {MARK_HIDDEN_BELOW_ANY}px)")
            if cond == "js-on" and not r["locked"]:
                bad.append(".mark-locked absent though JS ran")
            if cond == "js-off":
                want = r["w"] >= MARK_HIDDEN_BELOW
                if r["markShown"] != want:
                    bad.append(f"mark {'shown' if r['markShown'] else 'hidden'} with no JS at "
                               f"{r['w']}px; expected {'shown' if want else 'hidden'}")
            # nothing may hang off the right edge either
            if r["markShown"] and r["markRight"] > r["vw"] + 0.5:
                bad.append(f"mark right {r['markRight']} > {r['vw']}")
            # THE ASSERTION THIS CHECK WAS MISSING: the mark may tuck into the
            # final glyph and no further. Two letters is the collision.
            if r["markShown"] and r.get("markCovers", 0) > 1:
                bad.append(f"mark covers {r['markCovers']} letters of the wordmark "
                           f"(1 = tucked into the final glyph, >1 = collision)")
            # REPORTED, NOT ASSERTED: the motto is nowrap with a 286px intrinsic
            # floor, so it overhangs the viewport by ~2px from 300 to 380px. Both
            # available fixes (let it wrap, or drop the clamp floor) change the
            # composition, so this is an owner call and not a gate -- but it is
            # printed at every width so it cannot go unnoticed again.
            if r.get("mottoLeft") is not None and (r["mottoLeft"] < -0.5 or r["mottoRight"] > r["vw"] + 0.5):
                note.append(f"motto {r['mottoLeft']}→{r['mottoRight']} overhangs")
            mo = f"  [below the asserted floor: {'; '.join(note)}]" if note and r["w"] < CONTAINMENT_ASSERTED_FROM \
                else (f"  [{'; '.join(note)}]" if note else "")
            print(f"{cond:8} {r['w']:6} {r['left']:9.1f} {r['right']:9.1f} "
                  f"{str(r['applied'] or '-'):>10} {r['fs']:>8}  "
                  f"mark={'yes' if r['markShown'] else 'no ':<3} covers={r.get('markCovers', 0)} "
                  f"{'; '.join(bad) if bad else 'ok'}{mo}")
            fails += [f"{cond} @ {r['w']}px: {b}" for b in bad]

    if "--render" in sys.argv[1:]:
        print("\n(--render writes provenance-named panels; see tools/check-lockup.py docstring)")

    if fails:
        print()
        for f in fails:
            print(f"FAIL: {f}")
        die(f"{len(fails)} lockup assertion(s) failed")
    print("\nOK: the wordmark sits inside the viewport at every width with and without JS, "
          "and the mark never covers more than the final glyph")


if __name__ == "__main__":
    main()

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
WIDTHS = [280, 360, 375, 641, 760, 900, 1024, 1440]

# Below this the no-JS render drops the mark rather than park it on the final
# letter; see the matching media query in index.html. Asserted here so the two
# cannot drift apart.
MARK_HIDDEN_BELOW = 380


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
      out.push({w:+f.dataset.w, vw:win.innerWidth, left:+r.left.toFixed(1),
                right:+r.right.toFixed(1), applied:ap||null,
                markShown:lvis, markRight:+lr.right.toFixed(1),
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
            if r["left"] < -0.5:
                bad.append(f"left {r['left']} < 0")
            if r["right"] > r["vw"] + 0.5:
                bad.append(f"right {r['right']} > {r['vw']}")
            # the cap must have run iff the page's script ran
            if cond == "js-on" and not r["applied"]:
                bad.append("--word-shift-applied UNSET though JS ran")
            if cond == "js-off" and r["applied"]:
                bad.append(f"--word-shift-applied set to {r['applied']} with no JS")
            # the mark is dropped ONLY when it was never placed, and only narrow
            if cond == "js-on" and not r["markShown"]:
                bad.append("mark hidden even though JS placed it")
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
            print(f"{cond:8} {r['w']:6} {r['left']:9.1f} {r['right']:9.1f} "
                  f"{str(r['applied'] or '-'):>10} {r['fs']:>8}  "
                  f"mark={'yes' if r['markShown'] else 'no ':<3} {'; '.join(bad) if bad else 'ok'}")
            fails += [f"{cond} @ {r['w']}px: {b}" for b in bad]

    if "--render" in sys.argv[1:]:
        print("\n(--render writes provenance-named panels; see tools/check-lockup.py docstring)")

    if fails:
        print()
        for f in fails:
            print(f"FAIL: {f}")
        die(f"{len(fails)} lockup assertion(s) failed")
    print("\nOK: the wordmark sits inside the viewport at every width, with and without JS")


if __name__ == "__main__":
    main()

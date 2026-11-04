"""Run:  python tests/test_blackhole.py   (needs only `node`; no browser)
Checks the structural performance fixes in assist5/ui/blackhole5.js with a recording fake canvas."""
import json, os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS", name)
    else: fail += 1; print("  FAIL", name, extra)

print("black hole renderer (node, recording fake canvas)")
if not shutil.which("node"):
    print("  SKIP node not installed"); sys.exit(0)
HARNESS = r'''
const fs = require("fs");
const log = {};  let counting = false;
const count = (k) => { if (counting) log[k] = (log[k] || 0) + 1; };
function ctx2d() { const noop = () => {}; return new Proxy({}, { get(t, k) {
  if (k in t) return t[k];
  if (["createLinearGradient","createRadialGradient"].includes(k)) return () => { count(k); return { addColorStop: noop }; };
  return (...a) => { count(k); }; }, set(t, k, v) { t[k] = v; return true; } }); }
const made = [];
const mkCanvas = () => { const c = { width: 0, height: 0, style: {}, setAttribute() {}, getContext: () => ctx2d() }; made.push(c); return c; };
global.window = global; global.__bhTest = true;
global.document = { createElement: mkCanvas, body: { prepend() {} }, hidden: false, addEventListener() {} };
global.matchMedia = () => ({ matches: false });
global.localStorage = { getItem: () => null, setItem() {} };
global.innerWidth = 1280; global.innerHeight = 720; global.devicePixelRatio = 1;
let pending = null; global.requestAnimationFrame = (f) => { pending = f; return 1; }; global.cancelAnimationFrame = () => {};
global.addEventListener = () => {};
const frame = (t) => { const f = pending; pending = null; if (f) f(t); };
eval(fs.readFileSync(process.argv[2], "utf8"));
const main = made[0];
const out = { w: main.width, h: main.height, layers: made.length };
frame(100);                                   // first frame
counting = true; for (let i = 0; i < 30; i++) frame(200 + i * 60); counting = false;
out.perFrame = Object.fromEntries(Object.entries(log).map(([k, v]) => [k, v / 30]));
window.setFx("off"); out.offDisplay = main.style.display; pending = null; frame(9999); out.afterOffFrames = pending ? 1 : 0;
console.log(JSON.stringify(out));
'''
tmp = tempfile.mkdtemp(); h = os.path.join(tmp, "h.js"); open(h, "w").write(HARNESS)
o = subprocess.run(["node", h, os.path.join(ROOT, "assist5/ui/blackhole5.js")], capture_output=True, text=True)
try: j = json.loads(o.stdout.strip().splitlines()[-1])
except Exception: j = {}; print(o.stdout[-300:], o.stderr[-500:])
pf = j.get("perFrame", {})
check("script runs without errors", bool(j), o.stderr[-200:])
check("canvas is full resolution (not a down-scaled canvas stretched by CSS)", j.get("w") == 1280 and j.get("h") == 720, j)
check("expensive layers are cached (main canvas + back + front)", j.get("layers") == 3, j)
check("no gradients created per frame", pf.get("createLinearGradient", 0) == 0 and pf.get("createRadialGradient", 0) == 0, pf)
check("no wide strokes / ellipses per frame", pf.get("stroke", 0) == 0 and pf.get("ellipse", 0) == 0, pf)
check("each frame blits just the two cached layers", pf.get("drawImage") == 2, pf)
check("'off' hides the canvas and stops animating", j.get("offDisplay") == "none" and j.get("afterOffFrames") == 0, j)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)

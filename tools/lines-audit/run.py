#!/usr/bin/env python3
"""run.py <label> <cmd...> -- <setdir>... : run '<cmd> < page.pgm' (INKLINES1 output) on every page of the sets, score
one-to-one at IoU>=0.5 against MathPix (gemma4 testpool/heldout30/score1to1.py), print recall/precision per stratum.
Per-page results go to <WORK>/runs/<label>/<set>.json; boxes to .../<stem>.lines.json."""
from paths import LIB, WORK, GEMMA, DRILLGROUP  # noqa: F401
import json, os, subprocess, sys, time, statistics
sys.path.insert(0, f"{GEMMA}/testpool/heldout30")
from score1to1 import score
label = sys.argv[1]; k = sys.argv.index("--"); cmd = sys.argv[2:k]; sets = sys.argv[k + 1:]
tot = {}
times = []
for sd in sets:
    name = os.path.basename(sd.rstrip("/")); od = f"{WORK}/runs/{label}/{name}"; os.makedirs(od, exist_ok=True)
    res = []
    for m in json.load(open(os.path.join(sd, "meta.json"))):
        pgm = os.path.join(sd, m["stem"] + ".pgm")
        t0 = time.perf_counter()
        p = subprocess.run(cmd, stdin=open(pgm, "rb"), capture_output=True)
        dt = time.perf_counter() - t0; times.append(dt)
        lines = []
        if p.returncode != 0: print("FAIL", m["stem"], p.returncode, p.stderr[-200:], file=sys.stderr)
        for ln in p.stdout.decode().splitlines():
            f = ln.split()
            if len(f) == 5 and f[0] in ("text", "math", "equation_number", "page_info", "diagram"):
                x0, y0, x1, y1 = map(int, f[1:])
                lines.append({"type": f[0], "region": {"top_left_x": x0, "top_left_y": y0, "width": x1 - x0, "height": y1 - y0}})
        ours = {"pages": [{"page": m["page"], "page_width": m["pw"], "page_height": m["ph"], "lines": lines}]}
        json.dump(ours, open(os.path.join(od, m["stem"] + ".lines.json"), "w"))
        s = score(json.load(open(os.path.join(sd, m["stem"] + ".truth.json"))), ours)
        res.append({"stem": m["stem"], "stratum": m["stratum"], "t": s["truth"], "o": s["ours"], "m": s["matched"], "ms": round(dt * 1000)})
        for key in (m["stratum"], "all", name):
            a = tot.setdefault(key, [0, 0, 0, 0]); a[0] += s["truth"]; a[1] += s["ours"]; a[2] += s["matched"]; a[3] += 1
    json.dump(res, open(od + ".json", "w"))
print(f"{label}: " + "  ".join(f"{k}({v[3]}) {v[2]/max(1,v[0]):.3f}/{v[2]/max(1,v[1]):.3f}" for k, v in tot.items()))
print(f"   time/page median {statistics.median(times)*1000:.0f} ms, max {max(times)*1000:.0f} ms")

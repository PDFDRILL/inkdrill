#!/usr/bin/env python3
"""draw.py <seed> <exclude.tsv>... > set.tsv : drillgroup board_draw.py's strata over the library documents that
have a PDF in git; scan = pdffonts lists no embedded font (board_draw reads probe-pdffonts.txt, which is not in git)."""
from paths import LIB, WORK, GEMMA, DRILLGROUP  # noqa: F401
import importlib.util, json, os, random, subprocess, sys
spec = importlib.util.spec_from_file_location("bd", f"{DRILLGROUP}/scripts/board_draw.py"); bd = importlib.util.module_from_spec(spec); spec.loader.exec_module(bd)
QUOTA = {"scan": 20, "two_column": 30, "maths": 30, "table": 25, "german": 20}
seed = int(sys.argv[1]); exclude = set()
for f in sys.argv[2:]:
    exclude |= {l.split("\t")[1] for l in open(f) if l.strip() and not l.startswith(("#", "stratum"))}
cache = f"{WORK}/sets/cands.json"
if os.path.exists(cache): cands = json.load(open(cache))
else:
    cands = {k: [] for k in QUOTA}
    for d in sorted(os.listdir(LIB)):
        pdf = os.path.join(LIB, d, d + ".pdf"); lj = os.path.join(LIB, d, d + ".lines.json")
        if not (os.path.exists(pdf) and os.path.exists(lj)): continue
        try: data = json.load(open(lj))
        except Exception: continue
        if "source" in data: continue
        rows = [l for l in subprocess.run(["pdffonts", pdf], capture_output=True).stdout.decode("latin-1").splitlines()[2:] if l.strip()]
        scan = (not rows) or all((l.split()[-5] if len(l.split()) >= 5 else "") == "no" for l in rows)
        for p in data.get("pages") or []:
            s = bd.stratum(p, scan)
            if s: cands[s].append((d, p.get("page"), len(p.get("lines") or [])))
    json.dump(cands, open(cache, "w"))
rng = random.Random(seed); used = set()
print("# draw seed %d; candidates: %s" % (seed, ", ".join("%s %d" % (k, len(v)) for k, v in cands.items())))
print("stratum\tdoc\tpage\tlines")
for s in bd.ORDER:
    pool = sorted(map(tuple, cands[s])); rng.shuffle(pool); n = 0
    for d, pg, nl in pool:
        if n == QUOTA[s]: break
        if d in used or bd.doc_id(d) in exclude: continue
        used.add(d); n += 1
        print("%s\t%s\t%s\t%d" % (s, bd.doc_id(d), pg, nl))

"""gsscore.py <tag,tag,...> <set name>,... : recall/precision per stratum, raw and leaf, and inkcmp ms/page median,
from gsrun.py's results (<WORK>/gs/<set>.json)."""
import json, os, statistics, sys
from paths import WORK
tags, sets = sys.argv[1].split(","), sys.argv[2].split(",")
rows = {t: [] for t in tags}
for s in sets:
    d = json.load(open(os.path.join(WORK, "gs", s + ".json")))["rows"]
    for t in tags:
        rows[t] += d[t]
strata = sorted({r["stratum"] for r in rows[tags[0]]}) + ["ALL"]
for kind in ("raw", "leaf"):
    for t in tags:
        out = []
        for k in strata:
            a = [0, 0, 0]
            for r in rows[t]:
                if "fail" in r or (k != "ALL" and r["stratum"] != k):
                    continue
                for i in range(3):
                    a[i] += r[kind][i]
            out.append(f"{k}({sum(1 for r in rows[t] if k == 'ALL' or r['stratum'] == k)}) "
                       f"{a[2] / max(1, a[0]):.3f}/{a[2] / max(1, a[1]):.3f}")
        print(f"{t:>8} {kind:4} " + "  ".join(out))
for t in tags:
    ms = [r["ms"] for r in rows[t] if "ms" in r]
    print(f"{t:>8} inkcmp ms/page median {statistics.median(ms):.0f}, failures {sum('fail' in r for r in rows[t])}")

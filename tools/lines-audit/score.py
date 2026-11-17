#!/usr/bin/env python3
"""score.py <label|yolo> <setdir>... : recall/precision per stratum, one-to-one at IoU >= 0.5, page_info out of both.
  raw  = the committed scorer (every MathPix node is a 'line', containers included)
  leaf = truth without containers: nodes with children_ids, and column/table/table_row/table_column, are dropped.
YOLO's files come from gemma4/testpool/<heldoutN>/<stem>.sane.lines.json."""
from paths import LIB, WORK, GEMMA, DRILLGROUP  # noqa: F401
import json, os, sys
sys.path.insert(0, f"{GEMMA}/testpool/heldout30")
from score1to1 import score
YD = {"heldout": "heldout30", "heldout2": "heldout2", "heldout3": "heldout3", "heldout4": "heldout4"}
CONT = {"column", "table", "table_row", "table_column"}
def leaf(truth):
    pg = dict(truth["pages"][0]); pg["lines"] = [l for l in pg["lines"] if not l.get("children_ids") and l["type"] not in CONT]
    return {"pages": [pg]}
def cnt(t): return sum(l["type"] != "page_info" for l in t["pages"][0]["lines"])
label = sys.argv[1]; tot = {}; order = []
for sd in sys.argv[2:]:
    name = os.path.basename(sd.rstrip("/"))
    for m in json.load(open(os.path.join(sd, "meta.json"))):
        truth = json.load(open(os.path.join(sd, m["stem"] + ".truth.json")))
        f = (os.path.join(f"{GEMMA}/testpool", YD[name], m["stem"] + ".sane.lines.json") if label == "yolo"
             else f"{WORK}/runs/{label}/{name}/{m['stem']}.lines.json")
        ours = json.load(open(f)) if os.path.exists(f) else {"pages": []}
        for p in ours["pages"]: p["page"] = m["page"]
        for kind, T in (("raw", truth), ("leaf", leaf(truth))):
            s = score(T, ours) if ours["pages"] else {"truth": 0}
            if not s["truth"]: s = {"truth": cnt(T), "ours": s.get("ours", 0), "matched": 0}
            for key in (m["stratum"], "ALL"):
                if key not in order: order.append(key)
                a = tot.setdefault((kind, key), [0, 0, 0, 0]); a[0] += s["truth"]; a[1] += s["ours"]; a[2] += s["matched"]; a[3] += 1
for kind in ("raw", "leaf"):
    print(f"{label:>8} {kind:4} " + "  ".join(f"{k}({tot[kind,k][3]}) {tot[kind,k][2]/max(1,tot[kind,k][0]):.3f}/{tot[kind,k][2]/max(1,tot[kind,k][1]):.3f}" for k in sorted(order)))

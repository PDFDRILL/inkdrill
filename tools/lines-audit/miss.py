#!/usr/bin/env python3
"""miss.py <label> <setdir>... : classify every unmatched MathPix line and every unmatched box of ours.
truth side: MERGED (an our-box covers >=80% of it and also >=80% of another truth line), WIDE (covered >=80% by an our-box
that is larger, alone), SPLIT (>=2 of our boxes lie >=80% inside it), SHIFT (best IoU 0.2..0.5), NONE (best IoU < 0.2).
ours side: EXTRA_IN_TRUTH (>=80% inside a truth line), EXTRA_COVERS (covers truth lines), EXTRA_EMPTY (IoU<0.05 with all)."""
from paths import LIB, WORK, GEMMA, DRILLGROUP  # noqa: F401
import json, os, sys, collections
sys.path.insert(0, f"{GEMMA}/testpool/heldout30")
from score1to1 import iou
def box(l): r = l["region"]; return (r["top_left_x"], r["top_left_y"], r["top_left_x"] + r["width"], r["top_left_y"] + r["height"])
def area(a): return max(0, a[2] - a[0]) * max(0, a[3] - a[1])
def inter(a, b): return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
label = sys.argv[1]; C = collections.defaultdict(collections.Counter); TT = collections.defaultdict(collections.Counter); pages = []
for sd in sys.argv[2:]:
    name = os.path.basename(sd.rstrip("/"))
    for m in json.load(open(os.path.join(sd, "meta.json"))):
        tl = [l for l in json.load(open(os.path.join(sd, m["stem"] + ".truth.json")))["pages"][0]["lines"] if l["type"] != "page_info" and not l.get("children_ids") and l["type"] not in ("column","table","table_row","table_column")]
        ol = [l for l in json.load(open(f"{WORK}/runs/{label}/{name}/{m['stem']}.lines.json"))["pages"][0]["lines"] if l["type"] != "page_info"]
        t = [box(l) for l in tl]; o = [box(l) for l in ol]
        pairs = sorted(((iou(a, b), i, j) for i, a in enumerate(t) for j, b in enumerate(o)), reverse=True)
        ui, uj = set(), set()
        for v, i, j in pairs:
            if v < 0.5: break
            if i in ui or j in uj: continue
            ui.add(i); uj.add(j)
        cov = [[j for j, b in enumerate(o) if area(a) and inter(a, b) >= 0.8 * area(a)] for a in t]   # our boxes covering truth i
        ncov = collections.Counter(j for c in cov for j in c)
        pc = collections.Counter()
        for i, a in enumerate(t):
            TT[m["stratum"]][tl[i]["type"]] += 1
            if i in ui: continue
            best = max([iou(a, b) for b in o], default=0)
            inside = sum(1 for b in o if area(b) and inter(a, b) >= 0.8 * area(b))
            if any(ncov[j] >= 2 for j in cov[i]): k = "MERGED"
            elif inside >= 2: k = "SPLIT"
            elif cov[i]: k = "WIDE"
            elif best >= 0.2: k = "SHIFT"
            else: k = "NONE"
            C[m["stratum"]]["T_" + k] += 1; C[m["stratum"]]["Ttype_" + tl[i]["type"] + "_" + k] += 1; pc["T_" + k] += 1
        for j, b in enumerate(o):
            if j in uj: continue
            if any(area(b) and inter(a, b) >= 0.8 * area(b) for a in t): k = "IN_TRUTH"
            elif ncov[j] >= 1: k = "COVERS"
            elif max([iou(a, b) for a in t], default=0) < 0.05: k = "EMPTY"
            else: k = "OTHER"
            C[m["stratum"]]["O_" + k] += 1; pc["O_" + k] += 1; C[m["stratum"]]["Otype_" + ol[j]["type"] + "_" + k] += 1
        pages.append((len(t) - len(ui) + len(o) - len(uj), m["stratum"], m["stem"], len(t), len(o), len(ui), dict(pc)))
for s, c in C.items():
    print(s, " ".join(f"{k}={v}" for k, v in sorted(c.items()) if not k.startswith(("Ttype", "Otype"))))
    print("   truth types:", dict(TT[s]))
    print("   ", " ".join(f"{k[6:]}={v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1]) if k.startswith("Ttype"))[:400])
    print("   ", " ".join(f"{k[6:]}={v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1]) if k.startswith("Otype"))[:300])
if "-p" in os.environ.get("MISS", ""):
    for p in sorted(pages, reverse=True)[:25]: print(p)

#!/usr/bin/env python3
"""yolo.py <setdir>:<gemma4 testpool dir> ... : score the committed YOLO+Gemma sane.lines.json on the same pages."""
from paths import LIB, WORK, GEMMA, DRILLGROUP  # noqa: F401
import json, os, sys
sys.path.insert(0, f"{GEMMA}/testpool/heldout30")
from score1to1 import score
tot = {}; per = {}
for a in sys.argv[1:]:
    sd, yd = a.split(":"); name = os.path.basename(sd)
    for m in json.load(open(os.path.join(sd, "meta.json"))):
        y = os.path.join(f"{GEMMA}/testpool", yd, m["stem"] + ".sane.lines.json")
        truth = json.load(open(os.path.join(sd, m["stem"] + ".truth.json")))
        if os.path.exists(y):
            Y = json.load(open(y)); 
            for p in Y["pages"]: p["page"] = m["page"] if len(Y["pages"]) == 1 else p["page"]
            s = score(truth, Y)
            if s["truth"] == 0: s = {"truth": sum(l["type"] != "page_info" for l in truth["pages"][0]["lines"]), "ours": 0, "matched": 0}
        else: s = {"truth": sum(l["type"] != "page_info" for l in truth["pages"][0]["lines"]), "ours": 0, "matched": 0}; print("no YOLO file", m["stem"])
        per[m["stem"]] = s
        for key in (m["stratum"], "all", name):
            t = tot.setdefault(key, [0, 0, 0, 0]); t[0] += s["truth"]; t[1] += s["ours"]; t[2] += s["matched"]; t[3] += 1
json.dump(per, open(f"{WORK}/runs/yolo.json", "w"))
print("YOLO: " + "  ".join(f"{k}({v[3]}) {v[2]/max(1,v[0]):.3f}/{v[2]/max(1,v[1]):.3f}" for k, v in tot.items()))

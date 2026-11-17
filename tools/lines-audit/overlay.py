#!/usr/bin/env python3
"""overlay.py <label> <setdir> <stem> [out.png] [crop x0 y0 x1 y1 as fractions]: truth leaf boxes green (unmatched: blue fill), ours red (unmatched: thick orange)."""
import json, sys, os
from PIL import Image, ImageDraw
sys.path.insert(0, "/home/claude/gemma4/testpool/heldout30")
from score1to1 import iou
label, sd, stem = sys.argv[1:4]
out = sys.argv[4] if len(sys.argv) > 4 else f"/home/claude/work/ov/{label}-{stem}.png"
os.makedirs(os.path.dirname(out), exist_ok=True)
name = os.path.basename(sd.rstrip("/"))
CONT = {"column", "table", "table_row", "table_column"}
tl = [l for l in json.load(open(f"{sd}/{stem}.truth.json"))["pages"][0]["lines"] if l["type"] != "page_info" and not l.get("children_ids") and l["type"] not in CONT]
ol = [l for l in json.load(open(f"/home/claude/work/runs/{label}/{name}/{stem}.lines.json"))["pages"][0]["lines"] if l["type"] != "page_info"]
bx = lambda l: (l["region"]["top_left_x"], l["region"]["top_left_y"], l["region"]["top_left_x"] + l["region"]["width"], l["region"]["top_left_y"] + l["region"]["height"])
t = [bx(l) for l in tl]; o = [bx(l) for l in ol]
pairs = sorted(((iou(a, b), i, j) for i, a in enumerate(t) for j, b in enumerate(o)), reverse=True)
ui, uj = set(), set()
for v, i, j in pairs:
    if v < 0.5: break
    if i in ui or j in uj: continue
    ui.add(i); uj.add(j)
im = Image.open(f"{sd}/{stem}.pgm").convert("RGB"); ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
for i, a in enumerate(t):
    if i not in ui: d.rectangle(a, fill=(0, 0, 255, 50))
    d.rectangle(a, outline=(0, 160, 0, 255), width=2)
for j, b in enumerate(o):
    d.rectangle((b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2), outline=(255, 140, 0, 255) if j not in uj else (255, 0, 0, 255), width=4 if j not in uj else 1)
im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
if len(sys.argv) > 5:
    f = list(map(float, sys.argv[5:9])); W, H = im.size; im = im.crop((int(f[0] * W), int(f[1] * H), int(f[2] * W), int(f[3] * H)))
im.thumbnail((1500, 1500)); im.save(out); print(out, len(t), len(o), len(ui))

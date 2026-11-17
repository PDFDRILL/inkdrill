#!/usr/bin/env python3
"""detfig.py <model.onnx> <H> <W> <set.tsv>... -- the detector route with vector refinement (CONTRACT unit 7, the
coordinator's (ii), 2026-11-17): the detector (detector_set.py's preprocessing) gives every page its figure boxes; on a
born-digital page (no page-size image) the PDF's drawing (vecfig.page_figs: paths, images, inkcmp lines) refines them.
Each refinement is a named step; all variants are scored on the same pages from one detector run and one vector run:
  det        the detector alone
  +rows      detector boxes nested in another are dropped; boxes side by side in one row (vertical overlap >= 70% of the
             shorter, gap < 60 pt) are one figure unless each has its own sub-caption -- vecfig's panel rule
  +add       a vector figure that overlaps no detector box is added (born-digital only)
  +free      ... and only where the detector's other classes (text, table, title, formula, ...) cover < 50% of it
  +trim      a detector box is cut to the extent of the drawing and labels inside it (born-digital only, when the
             drawing covers >= 50% of the box)
Scores per variant: MathPix figures whole (IoU >= 0.5), body lines swallowed (detector_set.py's definition); the CPU
s/page of the detector and of the vector extraction (mutool trace + gs + inkcmp lines) separately."""
import csv, hashlib, io, json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image
import vecfig as V
VERBOSE = "-v" in sys.argv
sys.argv = [a for a in sys.argv if a != "-v"]
model, H, W = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
sess = ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])
names = {"lib:" + hashlib.sha256(d).hexdigest()[:12]: os.fsdecode(d) for d in os.listdir(os.fsencode(V.LIBROOT))}


def detect(img):
    w, h = img.size
    r = min(W / w, H / h)
    nw, nh = round(w * r), round(h * r)
    small = cv2.resize(np.asarray(img), (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((H, W, 3), 114, np.uint8)
    dx, dy = (W - nw) // 2, (H - nh) // 2
    canvas[dy:dy + nh, dx:dx + nw] = small
    out = sess.run(None, {"images": canvas.astype(np.float32).transpose(2, 0, 1)[None] / 255.0})[0][0]
    return [((b[0] - dx) / r, (b[1] - dy) / r, (b[2] - dx) / r, (b[3] - dy) / r, int(b[5])) for b in out if b[4] >= 0.2]


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def inter(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def rows(boxes, ink, k):
    boxes = [b for b in boxes if not any(c is not b and inter(b, c) >= 0.8 * area(b) and area(c) > area(b) for c in boxes)]

    def subcap(c):
        return any(t == "text" and c[1] < b[1] and 0 <= b[1] - c[3] <= 40 * k and b[0] >= c[0] - 5 * k
                   and b[2] <= c[2] + 5 * k for t, b in ink)
    boxes = list(boxes)
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i):
                a, b = boxes[i], boxes[j]
                ov = min(a[3], b[3]) - max(a[1], b[1])
                gap = max(a[0], b[0]) - min(a[2], b[2])
                if ov >= 0.7 * min(a[3] - a[1], b[3] - b[1]) and gap < 60 * k and not (subcap(a) and subcap(b)):
                    boxes[j] = union(a, b)
                    del boxes[i]
                    merged = True
                    break
            if merged:
                break
    return boxes


def trim(boxes, ctx):
    out = []
    for d in boxes:
        parts = [b for b in ctx["paths"] + ctx["images"] if inter(b, d) >= 0.8 * area(b) and area(b) > 0]
        if not parts:
            out.append(d); continue
        e = parts[0]
        for b in parts[1:]:
            e = union(e, b)
        for b in ctx["labels"]:                    # labels inside the detector box stay in the figure
            if inter(b, d) >= 0.8 * area(b):
                e = union(e, b)
        out.append(e if area(e) >= 0.5 * area(d) else d)
    return out


VARIANTS = ["det", "det+add", "det+add+free", "det+trim", "det+add+free+trim"]
score = {v: [0, 0, 0] for v in VARIANTS}            # hit, figures, swallowed
cpu_det = cpu_vec = 0.0
pages = 0
for tsv in sys.argv[4:]:
    for r in csv.reader(open(tsv), delimiter="\t"):
        if not r or r[0].startswith("#") or r[0] == "stratum":
            continue
        doc, page = r[1], int(r[2])
        folder = names.get(doc, doc)
        pdf, lj = (os.path.join(V.LIBROOT, folder, folder + e) for e in (".pdf", ".lines.json"))
        pg = next(p for p in json.load(open(lj))["pages"] if p.get("page") == page)
        mbox = lambda l: (l["region"]["top_left_x"], l["region"]["top_left_y"],
                          l["region"]["top_left_x"] + l["region"]["width"], l["region"]["top_left_y"] + l["region"]["height"])
        figs = [mbox(l) for l in pg["lines"] if l["type"] in ("diagram", "chart") and isinstance(l.get("region"), dict)]
        png = subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", "-f", str(page), "-l", str(page), pdf],
                             capture_output=True).stdout
        img = Image.open(io.BytesIO(png)).convert("RGB")
        sx, sy = pg["page_width"] / img.width, pg["page_height"] / img.height
        if pages == 0:
            detect(img)
        t = time.process_time()
        det = detect(img)
        cpu_det += time.process_time() - t
        other = [(b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy, b[4]) for b in det if b[4] != 3]   # what it calls not a figure
        det = [(b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy) for b in det if b[4] == 3]
        t0 = time.process_time(); c0 = os.times()
        vfig, ctx = V.page_figs(pdf, pg)
        c1 = os.times()
        cpu_vec += (c1.children_user + c1.children_system - c0.children_user - c0.children_system) + time.process_time() - t0
        pages += 1
        wpt, hpt = pg["page_width"] / ctx["k"], pg["page_height"] / ctx["k"]
        born = not any(area(b) > 0.9 * pg["page_width"] * pg["page_height"] for b in ctx["images"])
        k = ctx["k"]
        out = {}
        for v in VARIANTS:
            b = list(det)
            if "rows" in v:
                b = rows(b, ctx["ink"], k)
            if "trim" in v and born:
                b = trim(b, ctx)
            if "add" in v and born:
                b += [f for f in vfig if not any(inter(f, d) > 0 for d in b)
                      and ("free" not in v or sum(inter(f, o) for o in other) < 0.5 * area(f))]
            out[v] = b
        capt = {c for l in pg["lines"] if l["type"] in ("figure_label", "caption", "table_label") for c in l.get("children_ids") or []}
        body = []
        for l in pg["lines"]:
            if l["type"] != "text" or l.get("children_ids") or not isinstance(l.get("region"), dict) or l.get("id") in capt:
                continue
            b = mbox(l); cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if not any(f[0] <= cx <= f[2] and f[1] <= cy <= f[3] for f in figs):
                body.append((cx, cy))
        line = []
        for v in VARIANTS:
            h = sum(max((V.iou(f, d) for d in out[v]), default=0) >= 0.5 for f in figs)
            s_ = sum(any(d[0] <= cx <= d[2] and d[1] <= cy <= d[3] for d in out[v]) for cx, cy in body)
            score[v][0] += h; score[v][1] += len(figs); score[v][2] += s_
            line.append(f"{h}/{len(figs)}s{s_}")
        print(f"ROW {doc[:34]:34} p{page:<4} {'born' if born else 'scan'} " + " ".join(f"{v}={x}" for v, x in zip(VARIANTS, line)))
        if VERBOSE:
            print(f"     MathPix {[[int(x) for x in f] for f in figs]}\n     det {[[int(x) for x in f] for f in det]}\n"
                  f"     vec {[[int(x) for x in f] for f in vfig]}")
            for f in vfig:
                if not any(inter(f, d) > 0 for d in det):
                    cov = {}
                    for o in other:
                        cov[o[4]] = cov.get(o[4], 0) + inter(f, o) / area(f)
                    print(f"     added {[int(x) for x in f]} covered by the detector's classes {({c: round(x, 2) for c, x in cov.items() if x > 0.01})}")
for v in VARIANTS:
    h, n, s_ = score[v]
    print(f"TOTAL {v:20} {pages} pages: figures whole {h} of {n} = {h / max(1, n):.0%}; body lines swallowed {s_}")
print(f"CPU s/page (1 thread): detector {cpu_det / max(1, pages):.3f}; vector extraction {cpu_vec / max(1, pages):.3f}")

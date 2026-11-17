#!/usr/bin/env python3
"""detector_set.py <model.onnx> <H> <W> <set.tsv>... -- the detector route's figure regions on held-out pages
(CONTRACT unit 7): DocLayout-YOLO exported to ONNX at a fixed H x W, run by onnxruntime on ONE thread; the page
rendered at 150 dpi, shrunk as ultralytics does (cv2 INTER_LINEAR, no antialiasing: PIL's antialiased shrink lost
half the figures), letterboxed (grey 114, centred). Per page and in total: MathPix figures with a detected figure at
IoU >= 0.5; body lines swallowed (MathPix text lines outside every MathPix figure and caption whose centre falls in a
detected figure); CPU s per page for the detector (resize included)."""
import csv, io, json, os, subprocess, sys, time
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image
LIB = os.environ.get("LINES_AUDIT_LIB", os.path.expanduser("~/pdfdrill-library"))
VERBOSE = "-v" in sys.argv
sys.argv = [a for a in sys.argv if a != "-v"]
model, H, W = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
sess = ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0


def detect(img):
    w, h = img.size
    r = min(W / w, H / h)
    nw, nh = round(w * r), round(h * r)
    small = cv2.resize(np.asarray(img), (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((H, W, 3), 114, np.uint8)
    dx, dy = (W - nw) // 2, (H - nh) // 2
    canvas[dy:dy + nh, dx:dx + nw] = small
    out = sess.run(None, {"images": canvas.astype(np.float32).transpose(2, 0, 1)[None] / 255.0})[0][0]
    return [((b[0] - dx) / r, (b[1] - dy) / r, (b[2] - dx) / r, (b[3] - dy) / r) for b in out if b[4] >= 0.2 and int(b[5]) == 3]


hit = n = sw = pages = 0
cpu = 0.0
for tsv in sys.argv[4:]:
    for r in csv.reader(open(tsv), delimiter="\t"):
        if not r or r[0].startswith("#") or r[0] == "stratum":
            continue
        doc, page = r[1], int(r[2])
        pdf, lj = os.path.join(LIB, doc, doc + ".pdf"), os.path.join(LIB, doc, doc + ".lines.json")
        pg = next(p for p in json.load(open(lj))["pages"] if p.get("page") == page)
        box = lambda l: (l["region"]["top_left_x"], l["region"]["top_left_y"], l["region"]["top_left_x"] + l["region"]["width"],
                         l["region"]["top_left_y"] + l["region"]["height"])
        figs = [box(l) for l in pg["lines"] if l["type"] in ("diagram", "chart") and isinstance(l.get("region"), dict)]
        png = subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", "-f", str(page), "-l", str(page), pdf],
                             capture_output=True).stdout
        img = Image.open(io.BytesIO(png)).convert("RGB")
        sx, sy = pg["page_width"] / img.width, pg["page_height"] / img.height
        if pages == 0:
            detect(img)                                    # warm up
        t = time.process_time()
        det = detect(img)
        cpu += time.process_time() - t
        pages += 1
        det = [(b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy) for b in det]
        h_ = sum(max((iou(f, d) for d in det), default=0) >= 0.5 for f in figs)
        capt = {c for l in pg["lines"] if l["type"] in ("figure_label", "caption", "table_label") for c in l.get("children_ids") or []}
        s_ = 0
        for l in pg["lines"]:
            if l["type"] != "text" or l.get("children_ids") or not isinstance(l.get("region"), dict) or l.get("id") in capt:
                continue
            b = box(l); cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if any(f[0] <= cx <= f[2] and f[1] <= cy <= f[3] for f in figs):
                continue
            s_ += any(d[0] <= cx <= d[2] and d[1] <= cy <= d[3] for d in det)
        hit += h_; n += len(figs); sw += s_
        print(f"ROW {doc[:40]:40} p{page:<4} figures {h_}/{len(figs)} swallowed {s_}")
        if VERBOSE and (h_ < len(figs) or s_):
            for f in figs:
                bi = max(((iou(f, d), d) for d in det), default=(0, None))
                print(f"     MathPix {[int(v) for v in f]} best IoU {bi[0]:.2f} {[int(v) for v in bi[1]] if bi[1] else '-'}")
            print(f"     detector figures {[[int(v) for v in d] for d in det]}")
print(f"TOTAL {pages} pages: figures whole {hit} of {n} = {hit / max(1, n):.0%}; body lines swallowed {sw}; "
      f"CPU {cpu / max(1, pages):.3f} s/page (1 thread)")

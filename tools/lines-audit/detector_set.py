#!/usr/bin/env python3
"""detector_set.py <model.onnx> <H> <W> <set.tsv>... -- the detector route's figure regions on held-out pages
(CONTRACT unit 7): DocLayout-YOLO exported to ONNX at a fixed H x W, run by onnxruntime on ONE thread; the page
rendered at 150 dpi, shrunk as ultralytics does (cv2 INTER_LINEAR, no antialiasing: PIL's antialiased shrink lost
half the figures), letterboxed (grey 114, centred). Per page and in total: MathPix figures with a detected figure at
IoU >= 0.5; body lines swallowed (MathPix text lines outside every MathPix figure and caption whose centre falls in a
detected figure); CPU s per page for the detector (resize included).
Listings (coordinator, 2026-11-17): a MathPix diagram/chart region whose area is >= LISTING (0.15) covered by
readable text-layer words (pdftotext -bbox; >= 2 characters, >= 60% ASCII letters or digits -- a figure drawn with
line-font glyphs such as ajm p30's graphs has words too, "Ä", "??") is a code listing, prompt or algorithm: text in our
reading. It is reported in its own row and is not in the figure rate. The cut: on figure-set-9005 every listing looked
at reads >= 0.17, the highest figure 0.12.
--blocks (the swallow attempt): `inkcmp lines` on the page; a text line with another text line directly above or
below (vertical gap <= 1 lh, x-overlap >= 50% of the narrower) is a line of a multi-line text block, and a detected
figure never holds one: the box keeps its tallest y-interval free of such lines' centres (an author block above a
figure, 2304.03442 p1)."""
import csv, html, io, json, os, re, subprocess, sys, time
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image
LIB = os.environ.get("LINES_AUDIT_LIB", os.path.expanduser("~/pdfdrill-library"))
VERBOSE = "-v" in sys.argv
BLOCKS = "--blocks" in sys.argv
LISTING = 0.15
INKCMP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "build", "inkcmp")
sys.argv = [a for a in sys.argv if a not in ("-v", "--blocks")]
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


def readable_words(pdf, page, pg):
    bb = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-bbox", pdf, "-"], capture_output=True, text=True).stdout
    m = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', bb)
    if not m:
        return []
    kx, ky = pg["page_width"] / float(m[1]), pg["page_height"] / float(m[2])
    out = []
    for a, b, c, d, t in re.findall(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<', bb):
        t = html.unescape(t)
        if len(t) >= 2 and sum(ch.isascii() and ch.isalnum() for ch in t) >= 0.6 * len(t):
            out.append((float(a) * kx, float(b) * ky, float(c) * kx, float(d) * ky))
    return out


def cover(f, boxes):
    a = (f[2] - f[0]) * (f[3] - f[1])
    return sum(max(0, min(f[2], w[2]) - max(f[0], w[0])) * max(0, min(f[3], w[3]) - max(f[1], w[1])) for w in boxes) / a if a > 0 else 0


def block_lines(pdf, page, pg):
    """inkcmp lines' text lines that belong to a multi-line block, in MathPix pixels"""
    info = subprocess.run(["pdfinfo", "-box", "-f", str(page), "-l", str(page), pdf], capture_output=True, text=True).stdout
    m = re.search(r"Page\s+%d CropBox:\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)" % page, info)
    wpt = float(m[3]) - float(m[1]) if m else 612.0
    pgm = subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pgmraw", "-r%.3f" % (pg["page_width"] / wpt * 72),
                          "-dUseCropBox", f"-dFirstPage={page}", f"-dLastPage={page}", "-sOutputFile=%stdout", pdf],
                         capture_output=True).stdout
    out = subprocess.run([INKCMP, "lines"], input=pgm, capture_output=True).stdout.decode().splitlines()
    hd = out[0].split()
    sx, sy, lh = pg["page_width"] / int(hd[3]), pg["page_height"] / int(hd[4]), int(hd[hd.index("lh") + 1])
    t = [tuple(int(v) for v in f[1:5]) for f in (ln.split() for ln in out[1:-1]) if f[0] == "text"]

    def nb(a, b):
        ov = min(a[2], b[2]) - max(a[0], b[0])
        gap = max(a[1], b[1]) - min(a[3], b[3])
        return a is not b and ov >= 0.5 * min(a[2] - a[0], b[2] - b[0]) and 0 <= gap <= lh
    blk = [a for a in t if any(nb(a, b) for b in t)]
    return [(a[0] * sx, a[1] * sy, a[2] * sx, a[3] * sy) for a in blk]


def free_of(d, lines):
    """the tallest y-interval of box d holding no centre of the given lines"""
    ys = sorted((l[1] + l[3]) / 2 for l in lines if d[0] <= (l[0] + l[2]) / 2 <= d[2] and d[1] <= (l[1] + l[3]) / 2 <= d[3])
    if not ys:
        return d
    cuts = [d[1]] + ys + [d[3]]
    best = max(range(len(cuts) - 1), key=lambda i: cuts[i + 1] - cuts[i])
    lo, hi = cuts[best], cuts[best + 1]
    return (d[0], lo, d[2], hi)


hit = n = sw = pages = lhit = ln_ = 0
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
        if BLOCKS:
            bl = block_lines(pdf, page, pg)
            det = [free_of(d, bl) for d in det]
        words = readable_words(pdf, page, pg)
        lst = [f for f in figs if cover(f, words) >= LISTING]
        lh_ = sum(max((iou(f, d) for d in det), default=0) >= 0.5 for f in lst)
        lhit += lh_; ln_ += len(lst)
        allfigs = figs
        figs = [f for f in figs if f not in lst]
        h_ = sum(max((iou(f, d) for d in det), default=0) >= 0.5 for f in figs)
        capt = {c for l in pg["lines"] if l["type"] in ("figure_label", "caption", "table_label") for c in l.get("children_ids") or []}
        s_ = 0
        for l in pg["lines"]:
            if l["type"] != "text" or l.get("children_ids") or not isinstance(l.get("region"), dict) or l.get("id") in capt:
                continue
            b = box(l); cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if any(f[0] <= cx <= f[2] and f[1] <= cy <= f[3] for f in allfigs):
                continue
            s_ += any(d[0] <= cx <= d[2] and d[1] <= cy <= d[3] for d in det)
        hit += h_; n += len(figs); sw += s_
        print(f"ROW {doc[:40]:40} p{page:<4} figures {h_}/{len(figs)} swallowed {s_}" + (f" listings {lh_}/{len(lst)}" if lst else ""))
        if VERBOSE and (h_ < len(figs) or s_):
            for f in figs:
                bi = max(((iou(f, d), d) for d in det), default=(0, None))
                print(f"     MathPix {[int(v) for v in f]} best IoU {bi[0]:.2f} {[int(v) for v in bi[1]] if bi[1] else '-'}")
            print(f"     detector figures {[[int(v) for v in d] for d in det]}")
print(f"TOTAL {pages} pages: figures whole {hit} of {n} = {hit / max(1, n):.0%}; body lines swallowed {sw}; "
      f"CPU {cpu / max(1, pages):.3f} s/page (1 thread)")
print(f"LISTINGS (MathPix diagram regions >= {LISTING:.0%} readable words; text in our reading, not in the rate): "
      f"{ln_}, of which the detector boxed {lhit} as a figure")

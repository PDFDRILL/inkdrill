#!/usr/bin/env python3
"""vecfig.py -- rule-6 experiment for inkcmp lines' figure step, step 2 (CONTRACT unit 7, 2026-11-17).

    vecfig.py <library doc> [TOL pt] [--pages a-b]

Figures from the PDF's own drawn paths, labels bounded by inkcmp's line boxes:
1. every stroke/fill path (not clip; no page-size background) and every placed image or shading as its bbox in page
   pt (`mutool trace`);
2. paths closer than TOL pt join (union-find) into clusters; a cluster >= 30 pt on both sides is a figure candidate;
3. panels: figure candidates side by side in one row (vertical overlap >= 70% of the shorter, < 60 pt apart) are one
   figure, as MathPix boxes them -- unless each has a text line of its own just under it (a sub-caption: MathPix keeps
   such panels apart) (--no-rows: off);
4. labels: an inkcmp lines box (text, math, equation_number; the page rendered at MathPix's page size) within TOL of
   the figure joins it whole, repeatedly -- unless it is wider than a third of the figure: a box that wide is a body
   line, never a label (step 1's runaway: at 20 pt the glyph-wise attachment swallowed half a page).
Against MathPix's lines.json: per MathPix diagram/chart region the best IoU; "swallowed": MathPix text lines outside
every MathPix figure whose centre falls in a predicted figure; "no paths": pages with text and no drawn path (a scan,
a glyph-only figure) -- the kinds this route cannot reach.
"""
import json
import os
import re
import subprocess
import sys

ROWS = "--no-rows" not in sys.argv              # attempt 3: panels in one row are one figure
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INKCMP = os.path.join(ROOT, "build", "inkcmp")
LIBROOT = os.environ.get("LINES_AUDIT_LIB", os.path.expanduser("~/pdfdrill-library"))
PATH = re.compile(r'<(fill_path|stroke_path|clip_path|clip_stroke_path|fill_text|stroke_text|clip_text|ignore_text|'
                  r'fill_image|fill_image_mask|fill_shade)\b[^>]*?transform="([^"]+)"|<(moveto|lineto|curveto) ([^/]*)/>|</(fill_path|stroke_path|clip_path|'
                  r'clip_stroke_path)>')


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0


def near(a, b, t):
    return a[0] - t <= b[2] and b[0] - t <= a[2] and a[1] - t <= b[3] and b[1] - t <= a[3]


def cluster(boxes, tol):
    par = list(range(len(boxes)))

    def f(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    for i in range(len(boxes)):
        for j in range(i):
            if near(boxes[i], boxes[j], tol):
                par[f(i)] = f(j)
    g = {}
    for i, b in enumerate(boxes):
        r = f(i)
        c = g.get(r)
        g[r] = b if c is None else (min(c[0], b[0]), min(c[1], b[1]), max(c[2], b[2]), max(c[3], b[3]))
    return list(g.values())


def paths_of(pdf, n):
    tr = subprocess.run(["mutool", "trace", pdf, str(n)], capture_output=True, text=True).stdout
    out, cur, kind, M = [], None, None, None
    for m in PATH.finditer(tr):
        if m[1]:
            kind, M = m[1], [float(v) for v in m[2].split()]
            if kind.endswith("path"):
                cur = []
            elif kind.startswith("fill_image") or kind == "fill_shade":   # an image: the unit square, placed
                xs = [M[4], M[0] + M[4], M[2] + M[4], M[0] + M[2] + M[4]]
                ys = [M[5], M[1] + M[5], M[3] + M[5], M[1] + M[3] + M[5]]
                out.append((min(xs), min(ys), max(xs), max(ys)))
        elif m[3] and cur is not None:
            v = [float(x.split("=")[1].strip('"')) for x in m[4].split()]
            for i in range(0, len(v), 2):
                cur.append((v[i] * M[0] + v[i + 1] * M[2] + M[4], v[i] * M[1] + v[i + 1] * M[3] + M[5]))
        elif m[5]:
            if cur and kind in ("fill_path", "stroke_path"):
                xs, ys = [p[0] for p in cur], [p[1] for p in cur]
                out.append((min(xs), min(ys), max(xs), max(ys)))
            cur = None
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    doc, tol = args[0], float(args[1]) if len(args) > 1 else 12.0
    lib = os.path.join(LIBROOT, doc)
    pdf, mp = os.path.join(lib, doc + ".pdf"), json.load(open(os.path.join(lib, doc + ".lines.json")))
    rng = next((a.split("=")[1] for a in sys.argv if a.startswith("--pages=")), None)
    pages = mp["pages"]
    if rng:
        lo, hi = map(int, rng.split("-"))
        pages = [p for p in pages if lo <= p["page"] <= hi]
    figs_n = hit5 = swallowed = 0
    nopath = []
    for pg in pages:
        n, W, H = pg["page"], pg["page_width"], pg["page_height"]
        info = subprocess.run(["pdfinfo", "-box", "-f", str(n), "-l", str(n), pdf], capture_output=True, text=True).stdout
        m = re.search(r"Page\s+%d CropBox:\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)" % n, info)
        wpt = float(m[3]) - float(m[1]) if m else 612.0
        k = W / wpt
        pth = [tuple(v * k for v in b) for b in paths_of(pdf, n)]
        pth = [b for b in pth if not (b[2] - b[0] > 0.9 * W and b[3] - b[1] > 0.9 * H)]
        pgm = subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pgmraw", "-r%.3f" % (k * 72),
                              "-dUseCropBox", f"-dFirstPage={n}", f"-dLastPage={n}", "-sOutputFile=%stdout", pdf],
                             capture_output=True).stdout
        out = subprocess.run([INKCMP, "lines"], input=pgm, capture_output=True).stdout.decode().splitlines()
        hd = out[0].split()
        sx, sy = W / int(hd[3]), H / int(hd[4])
        ink = [(f[0], (int(f[1]) * sx, int(f[2]) * sy, int(f[3]) * sx, int(f[4]) * sy)) for f in
               (ln.split() for ln in out[1:-1])]
        labels = [b for t, b in ink if t in ("text", "math", "equation_number")]
        T = tol * k
        cand = [c for c in cluster(pth, T) if c[2] - c[0] >= 30 * k and c[3] - c[1] >= 30 * k]
        def subcap(c):
            """a text line of its own just under a panel, within its width: a sub-figure with its own caption, which
            MathPix keeps apart (1805.01788 p8: six images, six figures)"""
            return any(t == "text" and c[1] < b[1] and 0 <= b[1] - c[3] <= 40 * k and b[0] >= c[0] - 5 * k
                       and b[2] <= c[2] + 5 * k for t, b in ink)
        merged = True                                # panels of one figure side by side: one row (vertical overlap
        while merged and ROWS:                       # >= 70% of the shorter), < 60 pt apart
            merged = False
            for i in range(len(cand)):
                for j in range(i):
                    a, b = cand[i], cand[j]
                    ov = min(a[3], b[3]) - max(a[1], b[1])
                    gap = max(a[0], b[0]) - min(a[2], b[2])
                    if ov >= 0.7 * min(a[3] - a[1], b[3] - b[1]) and gap < 60 * k and not (subcap(a) and subcap(b)):
                        cand[j] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                        del cand[i]
                        merged = True
                        break
                if merged:
                    break
        figs = []
        for c in cand:
            taken = set()
            while True:
                w = c[2] - c[0]
                add = [i for i, b in enumerate(labels) if i not in taken and near(c, b, T) and b[2] - b[0] <= w / 3]
                if not add:
                    break
                taken |= set(add)
                for i in add:
                    b = labels[i]
                    c = (min(c[0], b[0]), min(c[1], b[1]), max(c[2], b[2]), max(c[3], b[3]))
            figs.append(c)
        mfig = []
        for ln in pg["lines"]:
            if ln["type"] in ("diagram", "chart") and isinstance(ln.get("region"), dict):
                r = ln["region"]
                mfig.append((r["top_left_x"], r["top_left_y"], r["top_left_x"] + r["width"], r["top_left_y"] + r["height"]))
        for b in mfig:
            best = max((iou(b, f) for f in figs), default=0)
            figs_n += 1
            hit5 += best >= 0.5
            print(f"p{n} MathPix figure {[int(v) for v in b]}: best IoU {best:.2f}")
        capt = {c for ln in pg["lines"] if ln["type"] in ("figure_label", "caption", "table_label")
                for c in ln.get("children_ids") or []}    # a caption's lines are not body text
        for ln in pg["lines"]:
            if ln["type"] != "text" or ln.get("children_ids") or not isinstance(ln.get("region"), dict) \
                    or ln.get("id") in capt:
                continue
            r = ln["region"]
            cx, cy = r["top_left_x"] + r["width"] / 2, r["top_left_y"] + r["height"] / 2
            if any(f[0] <= cx <= f[2] and f[1] <= cy <= f[3] for f in mfig):
                continue
            if any(f[0] <= cx <= f[2] and f[1] <= cy <= f[3] for f in figs):
                swallowed += 1
                print(f"p{n} SWALLOWED body line {ln.get('text', '')[:40]!r}")
        if not pth and labels:
            nopath.append(n)
    print(f"{doc} TOL {tol}: MathPix figures with IoU >= 0.5: {hit5} of {figs_n}; body lines swallowed: {swallowed}; "
          f"pages with text but no drawn path: {len(nopath)} {nopath[:12]}")


if __name__ == "__main__":
    main()

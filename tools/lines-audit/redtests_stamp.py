#!/usr/bin/env python3
"""Side-stamp red tests (coordinator, 2026-11-17; T-0154): rotated text down a page's margin -- arXiv's identifier --
is its own record with its rotation, never part of a line.

    redtests_stamp.py [--inkcmp BINARY]      exit 0 when every test passes, 1 when one is red, 2 NOT RUN

Pages at 250 dpi (Ghostscript, CropBox), `inkcmp lines` on them (default build/inkcmp).
S1  2312.11532 p1 against MathPix: every MathPix text line of the left column that has an inkcmp box (IoU >= 0.5)
    has one starting within 1 lh of MathPix's left edge; the stamp (MathPix page_info x58 w67 h1201) is exactly one
    inkcmp record, page_info with a rotation, at IoU >= 0.5, and no other record reaches into it.
S2  2209.02970 p1 (no MathPix): the reference is the PDF's text layer (pdftotext -bbox): the stamp is the union of
    words taller than 3x their width in the left 12% of the page; a line's left edge is the leftmost other word on its
    row in the left half (the abstract is indented, the body is not: one edge for the column was wrong by 57 px). Same two checks: inkcmp's left-half text boxes start within 1 lh
    of that edge, the stamp is one rotated record at IoU >= 0.5 and no other record reaches into it."""
import argparse, os, re, subprocess, sys
import json
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.environ.get("LINES_AUDIT_LIB", os.path.expanduser("~/pdfdrill-library"))


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0.0


def inter(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def lines_of(binary, pdf, n):
    pgm = subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pgmraw", "-r250", "-dUseCropBox",
                          f"-dFirstPage={n}", f"-dLastPage={n}", "-sOutputFile=%stdout", pdf],
                         capture_output=True, check=True).stdout
    out = subprocess.run([binary, "lines"], input=pgm, capture_output=True, check=True).stdout.decode().splitlines()
    hd = out[0].split()
    W, H, lh = int(hd[3]), int(hd[4]), int(hd[hd.index("lh") + 1])
    recs = []
    for ln in out[1:-1]:
        f = ln.split()
        rot = int(f[f.index("rot") + 1]) if "rot" in f else 0
        recs.append((f[0], tuple(int(v) for v in f[1:5]), rot))
    return W, H, lh, recs


def check(name, recs, lh, stamp, left_lines):
    """left_lines: [(reference box)] of the left column; -> number of red checks"""
    red = 0
    rot = [r for r in recs if r[2]]
    hit = [r for r in rot if iou(r[1], stamp) >= 0.5]
    others = [r for r in recs if not r[2] and inter(r[1], stamp) > 0]
    ok_s = len(rot) == 1 and len(hit) == 1 and hit[0][0] == "page_info" and not others
    print(f"{name} stamp {[int(v) for v in stamp]}: rotated records {[(r[0], list(r[1]), r[2]) for r in rot]}, "
          f"other records reaching into it {len(others)} -> {'green' if ok_s else 'RED'}")
    red += not ok_s
    bad = n = 0
    for b in left_lines:
        cand = [r[1] for r in recs if r[0] in ("text", "math") and iou(r[1], b) >= 0.5]
        if not cand:
            continue
        n += 1
        x = min(cand, key=lambda c: abs(c[0] - b[0]))[0]
        if abs(x - b[0]) > lh:
            bad += 1
            print(f"  {name} RED line at y {int(b[1])}: box starts at x {x}, reference {int(b[0])} (lh {lh})")
    print(f"{name} left column: {n - bad} of {n} matched lines start within 1 lh -> {'green' if not bad and n else 'RED'}")
    return red + bool(bad or not n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inkcmp", default=os.path.join(ROOT, "build", "inkcmp"))
    a = ap.parse_args()
    p1, p2 = os.path.join(LIB, "2312.11532", "2312.11532"), os.path.join(LIB, "arxiv.2209.02970", "arxiv.2209.02970")
    if not (os.path.exists(p1 + ".pdf") and os.path.exists(p1 + ".lines.json") and os.path.exists(p2 + ".pdf")):
        print("NOT RUN: 2312.11532 or 2209.02970 missing from the library (LINES_AUDIT_LIB)")
        return 2
    red = 0
    # S1
    pg = json.load(open(p1 + ".lines.json"))["pages"][0]
    W, H, lh, recs = lines_of(a.inkcmp, p1 + ".pdf", 1)
    sx, sy = W / pg["page_width"], H / pg["page_height"]
    box = lambda r: (r["top_left_x"] * sx, r["top_left_y"] * sy, (r["top_left_x"] + r["width"]) * sx, (r["top_left_y"] + r["height"]) * sy)
    stamp = next(box(l["region"]) for l in pg["lines"] if l["type"] == "page_info" and l["region"]["height"] > 10 * l["region"]["width"])
    left = [box(l["region"]) for l in pg["lines"] if l["type"] == "text" and not l.get("children_ids")
            and isinstance(l.get("region"), dict) and (box(l["region"])[0] + box(l["region"])[2]) / 2 < W / 2
            and box(l["region"])[2] - box(l["region"])[0] > 0.25 * W]
    red += check("S1 2312.11532 p1", recs, lh, stamp, left)
    # S2
    W, H, lh, recs = lines_of(a.inkcmp, p2 + ".pdf", 1)
    bb = subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-bbox", p2 + ".pdf", "-"], capture_output=True, text=True).stdout
    pw, ph = (float(v) for v in re.search(r'<page width="([\d.]+)" height="([\d.]+)"', bb).groups())
    k = W / pw
    words = [tuple(float(v) * k for v in m) for m in re.findall(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)"', bb)]
    sw = [w for w in words if w[3] - w[1] > 3 * (w[2] - w[0]) and w[2] < 0.12 * W]
    if not sw:
        print("S2: NOT RUN -- no rotated words in the text layer"); return 2
    stamp = (min(w[0] for w in sw), min(w[1] for w in sw), max(w[2] for w in sw), max(w[3] for w in sw))
    body = [w for w in words if w not in sw and w[2] < W / 2 and w[0] > stamp[2]]
    left = []
    for r in recs:                               # reference: the leftmost text-layer word on the box's row
        b = r[1]
        if r[0] != "text" or (b[0] + b[2]) / 2 >= W / 2 or b[2] - b[0] <= 0.25 * W:
            continue
        row = [w for w in body if min(w[3], b[3]) - max(w[1], b[1]) > 0.5 * (w[3] - w[1])]
        if row:
            left.append((min(w[0] for w in row), b[1], b[2], b[3]))
    red += check("S2 2209.02970 p1", recs, lh, stamp, left)
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())

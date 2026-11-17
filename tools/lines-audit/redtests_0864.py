#!/usr/bin/env python3
"""T-0154 red tests on 0864 (the owner's inspection, 2026-11-17), from boxes alone, against MathPix's lines.json.

    redtests_0864.py [--reading LINES.json]     exit 0 when every test passes, 1 when one is red

The page images: Ghostscript at 250 dpi, MathPix's page size (2125 x 2750 px). Boxes: `inkcmp lines` (build/inkcmp)
on them, or, with --reading, that lines.json's leaf regions (the Gemma inklines reading, for instance).

D  "misses part of diagram (separate by white space)": each MathPix diagram/chart region is one figure -- inkcmp's
   diagram boxes cover >= 90% of its area, and no text, math or equation_number box has its centre inside it.
   Runs on inkcmp's boxes (a reading's own types are its reader's). RED today: every MathPix figure of 0864 comes out
   as text/math/equation_number pieces, one p3 diagram is cut at the white space under its top (the diagram box starts
   110 px below MathPix's). Cause: inkcmp lines' figure step (contract unit 7, step 5).
L  "paragraph selection misses last line or last 2 lines": every MathPix text line shorter than 80% of its column (a
   paragraph's last line) has a box at IoU >= 0.5. GREEN on inkcmp's boxes (40 of 40), RED on the Gemma inklines
   reading (29 of 40): the loss is in gemma_lines' grouping and alignment of the boxes, not in inkcmp lines.
Inputs outside git (the library, the reading) are named on the command line or found at their usual paths; a missing
input is reported NOT RUN, not passed.
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INKCMP = os.path.join(ROOT, "build", "inkcmp")
LIB = os.path.join(os.environ.get("LINES_AUDIT_LIB", os.path.expanduser("~/pdfdrill-library")), "0864")


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0.0


def box(ln):
    r = ln["region"]
    return (r["top_left_x"], r["top_left_y"], r["top_left_x"] + r["width"], r["top_left_y"] + r["height"])


FIGURE_MODELS = None                                   # --figure-models M320 M640: the cascade's boxes (--figures hint)


def inkcmp_boxes(pdf, n, W, H):
    cmd = [INKCMP, "lines"]
    if FIGURE_MODELS:
        sys.path.insert(0, os.path.join(ROOT, "tools", "detector"))
        import figure_hint as FH
        import layout_hint as LH
        hf = os.path.join(os.environ.get("TMPDIR", "."), f"figure-hint-0864-p{n}.txt")
        with open(hf, "w") as f:
            for b in FH.figure_boxes(LH.session(FIGURE_MODELS[0]), LH.session(FIGURE_MODELS[1]), LH.page_image(pdf, n)):
                f.write("1 figure %.5f %.5f %.5f %.5f\n" % b)
        cmd += ["--hint", hf, "--figures", "hint"]
    pgm = subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pgmraw", "-r250", "-dUseCropBox",
                          f"-dFirstPage={n}", f"-dLastPage={n}", "-sOutputFile=%stdout", pdf],
                         capture_output=True, check=True).stdout
    out = subprocess.run(cmd, input=pgm, capture_output=True, check=True).stdout.decode().splitlines()
    hd = out[0].split()
    sx, sy = W / int(hd[3]), H / int(hd[4])
    return [(f[0], (int(f[1]) * sx, int(f[2]) * sy, int(f[3]) * sx, int(f[4]) * sy))
            for f in (ln.split() for ln in out[1:-1])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reading", help="a lines.json whose leaf regions are tested (L only); default: inkcmp lines")
    ap.add_argument("--figure-models", nargs=2, metavar=("M320", "M640"),
                    help="inkcmp lines with the detector cascade's figure boxes (tools/detector/figure_hint.py)")
    a = ap.parse_args()
    global FIGURE_MODELS
    FIGURE_MODELS = a.figure_models
    mp_path, pdf = os.path.join(LIB, "0864.lines.json"), os.path.join(LIB, "0864.pdf")
    if not (os.path.exists(mp_path) and os.path.exists(pdf)):
        print("NOT RUN: no 0864 in the library (LINES_AUDIT_LIB)")
        return 2
    mp = json.load(open(mp_path))
    reading = json.load(open(a.reading)) if a.reading else None
    red = 0
    dfig = dbad = lsh = lhit = 0
    for pg in mp["pages"]:
        n, W, H = pg["page"], pg["page_width"], pg["page_height"]
        ink = inkcmp_boxes(pdf, n, W, H)
        if reading is not None:
            rp = next((p for p in reading["pages"] if p.get("page") == n), None)
            boxes = [("text", box(ln)) for ln in (rp or {}).get("lines", [])
                     if isinstance(ln.get("region"), dict) and not ln.get("children_ids")]
        else:
            boxes = ink
        byid = {ln["id"]: ln for ln in pg["lines"] if ln.get("id")}
        if reading is None:
            for f in (ln for ln in pg["lines"] if ln["type"] in ("diagram", "chart")):
                b = box(f)
                area = (b[2] - b[0]) * (b[3] - b[1])
                cov = sum(max(0, min(b[2], x[2]) - max(b[0], x[0])) * max(0, min(b[3], x[3]) - max(b[1], x[1]))
                          for t, x in ink if t == "diagram")
                pieces = [t for t, x in ink if t in ("text", "math", "equation_number")
                          and b[0] <= (x[0] + x[2]) / 2 <= b[2] and b[1] <= (x[1] + x[3]) / 2 <= b[3]]
                dfig += 1
                if cov < 0.9 * area or pieces:
                    dbad += 1
                    print(f"D RED p{n} figure {[int(v) for v in b]}: diagram cover {cov / area:.0%}, "
                          f"{len(pieces)} text/math pieces inside")
        for col in (ln for ln in pg["lines"] if ln["type"] == "column"):
            cb = box(col)
            for c in col.get("children_ids", []):
                t = byid.get(c)
                if not t or t["type"] != "text" or t.get("children_ids") or not isinstance(t.get("region"), dict):
                    continue
                b = box(t)
                if b[2] - b[0] >= 0.8 * (cb[2] - cb[0]):
                    continue
                lsh += 1
                if max((iou(b, x) for _, x in boxes if _ != "diagram"), default=0) >= 0.5:
                    lhit += 1
                else:
                    print(f"L RED p{n} paragraph-final line has no box: {t.get('text', '')[:50]!r} {[int(v) for v in b]}")
    who = a.reading or "inkcmp lines"
    if reading is None:
        print(f"D: {dfig - dbad} of {dfig} MathPix figures are one inkcmp figure -> {'RED' if dbad else 'green'}")
        red += bool(dbad)
    print(f"L ({who}): {lhit} of {lsh} paragraph-final lines have a box -> {'RED' if lhit < lsh else 'green'}")
    red += lhit < lsh
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())

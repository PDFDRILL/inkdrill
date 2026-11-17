#!/usr/bin/env python3
"""Author-header red tests (owner: "author header in 3 columns not detected", 1805.01788 p1; coordinator 2026-11-17,
with 2304.03442 p1, whose centred author lines inkcmp lines typed as maths and merged four to a box).

    redtests_authors.py [--inkcmp BINARY] [--hint-model dly640x512.onnx]
                                               exit 0 when every test passes, 1 when one is red, 2 NOT RUN

Per page, against MathPix's `authors` records: every printed author line (an authors record's text children) has an
inkcmp `text` record at IoU >= 0.5, and no inkcmp `math` record has its centre inside an authors region. Pages at
MathPix's size (Ghostscript, CropBox), `inkcmp lines` (default build/inkcmp)."""
import argparse, json, os, re, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.environ.get("LINES_AUDIT_LIB", os.path.expanduser("~/pdfdrill-library"))
PAGES = [("2304.03442", 1), ("1805.01788", 1)]


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0.0


def box(r):
    return (r["top_left_x"], r["top_left_y"], r["top_left_x"] + r["width"], r["top_left_y"] + r["height"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inkcmp", default=os.path.join(ROOT, "build", "inkcmp"))
    ap.add_argument("--hint-model", help="the 640 x 512 layout graph (tools/detector/fetch_weights.sh): page 1's hint "
                                         "from tools/detector/layout_hint.py goes to inkcmp lines --hint")
    a = ap.parse_args()
    red = 0
    for doc, n in PAGES:
        base = os.path.join(LIB, doc, doc)
        if not (os.path.exists(base + ".pdf") and os.path.exists(base + ".lines.json")):
            print(f"NOT RUN: {doc} missing from the library"); return 2
        pg = next(p for p in json.load(open(base + ".lines.json"))["pages"] if p["page"] == n)
        info = subprocess.run(["pdfinfo", "-box", "-f", str(n), "-l", str(n), base + ".pdf"], capture_output=True, text=True).stdout
        m = re.search(r"Page\s+%d CropBox:\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)" % n, info)
        wpt = float(m[3]) - float(m[1])
        pgm = subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pgmraw", "-r%.4f" % (pg["page_width"] / wpt * 72),
                              "-dUseCropBox", f"-dFirstPage={n}", f"-dLastPage={n}", "-sOutputFile=%stdout", base + ".pdf"],
                             capture_output=True, check=True).stdout
        cmd = [a.inkcmp, "lines"]
        if a.hint_model:
            sys.path.insert(0, os.path.join(ROOT, "tools", "detector"))
            import layout_hint
            hf = os.path.join(os.environ.get("TMPDIR", "."), f"authors-hint-{doc}.txt")
            with open(hf, "w") as f:
                for c, x0, y0, x1, y1 in layout_hint.hints(a.hint_model, base + ".pdf", n):
                    f.write(f"1 {c} {x0:.5f} {y0:.5f} {x1:.5f} {y1:.5f}\n")      # page 1 of the one-page stream
            cmd += ["--hint", hf]
        out = subprocess.run(cmd, input=pgm, capture_output=True, check=True).stdout.decode().splitlines()
        hd = out[0].split(); sx, sy = pg["page_width"] / int(hd[3]), pg["page_height"] / int(hd[4])
        recs = [(f[0], (int(f[1]) * sx, int(f[2]) * sy, int(f[3]) * sx, int(f[4]) * sy)) for f in (l.split() for l in out[1:-1])]
        byid = {l.get("id"): l for l in pg["lines"]}
        blocks = [l for l in pg["lines"] if l["type"] == "authors" and isinstance(l.get("region"), dict)]
        lines = [byid[c] for b in blocks for c in b.get("children_ids") or [] if c in byid and isinstance(byid[c].get("region"), dict)]
        hit = 0
        for l in lines:
            if max((iou(box(l["region"]), r) for t, r in recs if t == "text"), default=0) >= 0.5:
                hit += 1
            else:
                print(f"  {doc} p{n} RED author line {l.get('text', '')[:30]!r}: no text record at IoU >= 0.5")
        maths = [r for t, r in recs if t == "math" and any(box(b["region"])[0] <= (r[0] + r[2]) / 2 <= box(b["region"])[2]
                                                         and box(b["region"])[1] <= (r[1] + r[3]) / 2 <= box(b["region"])[3] for b in blocks)]
        ok = lines and hit == len(lines) and not maths
        print(f"{doc} p{n}: {len(blocks)} author blocks, {hit} of {len(lines)} author lines have a text record, "
              f"{len(maths)} math records inside -> {'green' if ok else 'RED'}")
        red += not ok
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())

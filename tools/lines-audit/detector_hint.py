#!/usr/bin/env python3
"""detector_hint.py <model.onnx> <H> <W> <inkcmp-with-hint> -- experiment (a) for author blocks typed as maths
(CONTRACT unit 7, 2026-11-17): DocLayout-YOLO's class boxes as a typing hint for inkcmp lines. The hinted binary is
a build of lines.c whose step 6 reads INKCMP_HINT (lines "x0 y0 x1 y1 class", page pixels) and types a display
candidate text when its centre lies in a title / plain text / abandon box and in no isolate_formula box, before
displays merge (the patch: tools/lines-audit/detector_hint.patch, against GLYPHDRILL3 b0b24bf inkcmp/lines.c).
Prints the two author red tests (2304.03442 p1, 1805.01788 p1) and, on the board pages with a MathPix reference,
every record the hint changes with MathPix's type at that place; a display that becomes text is DISPLAY LOST."""
import io, json, os, re, subprocess, sys, collections
import cv2, numpy as np, onnxruntime as ort
from PIL import Image
MODEL, S, SW, NEW = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), os.path.abspath(sys.argv[4])
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
sess = ort.InferenceSession(MODEL, so, providers=["CPUExecutionProvider"])


def detect(img, conf=0.2):
    w, h = img.size; r = min(SW / w, S / h); nw, nh = round(w * r), round(h * r)
    small = cv2.resize(np.asarray(img), (nw, nh), interpolation=cv2.INTER_LINEAR)
    c = np.full((S, SW, 3), 114, np.uint8); dx, dy = (SW - nw) // 2, (S - nh) // 2; c[dy:dy + nh, dx:dx + nw] = small
    out = sess.run(None, {"images": c.astype(np.float32).transpose(2, 0, 1)[None] / 255.0})[0][0]
    return [((b[0] - dx) / r, (b[1] - dy) / r, (b[2] - dx) / r, (b[3] - dy) / r, int(b[5])) for b in out if b[4] >= conf]
def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - ix*iy
    return ix*iy/u if u > 0 else 0

OLD = os.path.join(ROOT, "build", "inkcmp")
HINT = os.path.join(os.environ.get("TMPDIR", "."), "detector_hint.txt")
B = os.path.expanduser("~/drillgroup/work/board"); L = os.path.expanduser("~/pdfdrill-library")
pages = [(f"{L}/{d}/{d}.pdf", 1, f"{L}/{d}/{d}.lines.json", d) for d in ("2304.03442", "1805.01788")]
pages += [(f"{B}/{k}/{k}.pdf", 1, f"{B}/{k}/mathpix.lines.json", k) for k in sorted(os.listdir(B)) if os.path.exists(f"{B}/{k}/mathpix.lines.json")]
verdict = collections.Counter(); changed = 0
box = lambda r: (r["top_left_x"], r["top_left_y"], r["top_left_x"] + r["width"], r["top_left_y"] + r["height"])
for pdf, n, mj, key in pages:
    pg = next(p for p in json.load(open(mj))["pages"] if p["page"] == n)
    W, H = pg["page_width"], pg["page_height"]
    info = subprocess.run(["pdfinfo", "-box", "-f", str(n), "-l", str(n), pdf], capture_output=True, text=True).stdout
    m = re.search(r"Page\s+%d CropBox:\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)" % n, info); wpt = float(m[3]) - float(m[1])
    pgm = subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pgmraw", "-r%.4f" % (W / wpt * 72), "-dUseCropBox", f"-dFirstPage={n}", f"-dLastPage={n}", "-sOutputFile=%stdout", pdf], capture_output=True).stdout
    old = subprocess.run([OLD, "lines"], input=pgm, capture_output=True).stdout.decode()
    hd = old.splitlines()[0].split(); pw, ph = int(hd[3]), int(hd[4])
    png = subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", "-f", str(n), "-l", str(n), pdf], capture_output=True).stdout
    img = Image.open(io.BytesIO(png)).convert("RGB"); kx, ky = pw / img.width, ph / img.height
    with open(HINT, "w") as f:
        for d in detect(img):
            f.write("%.1f %.1f %.1f %.1f %d\n" % (d[0] * kx, d[1] * ky, d[2] * kx, d[3] * ky, d[4]))
    new = subprocess.run([NEW, "lines"], input=pgm, capture_output=True, env=dict(os.environ, INKCMP_HINT=HINT)).stdout.decode()
    sx, sy = W / pw, H / ph
    recs = lambda o: [(f[0], (int(f[1]) * sx, int(f[2]) * sy, int(f[3]) * sx, int(f[4]) * sy)) for f in (l.split() for l in o.splitlines()[1:-1])]
    ro, rn = recs(old), recs(new)
    if key in ("2304.03442", "1805.01788"):
        byid = {l.get("id"): l for l in pg["lines"]}
        blocks = [l for l in pg["lines"] if l["type"] == "authors"]
        lines = [byid[c] for bl in blocks for c in bl.get("children_ids") or [] if c in byid and isinstance(byid[c].get("region"), dict)]
        hit = sum(max((iou(box(l["region"]), r) for t, r in rn if t == "text"), default=0) >= 0.5 for l in lines)
        mins = sum(1 for t, r in rn if t == "math" and any(box(bl["region"])[0] <= (r[0]+r[2])/2 <= box(bl["region"])[2] and box(bl["region"])[1] <= (r[1]+r[3])/2 <= box(bl["region"])[3] for bl in blocks))
        print(f"RED TEST {key} p1: {hit} of {len(lines)} author lines with a text record, {mins} math inside -> {'green' if hit == len(lines) and not mins else 'RED'}")
        continue
    if old == new: continue
    changed += 1
    mp = [(l["type"], box(l["region"]), (l.get("text") or "")[:40]) for l in pg["lines"] if isinstance(l.get("region"), dict) and not l.get("children_ids")]
    came = [r for r in rn if r not in ro]
    for t, b in came:
        best = max(mp, key=lambda x: iou(x[1], b), default=None)
        mt = best[0] if best and iou(best[1], b) >= 0.3 else "-"
        verdict[(t, mt)] += 1
        if mt == "math":
            print(f"  {'DISPLAY LOST' if t != 'math' else 'display reshaped'} {key}: new {t} {[int(v) for v in b]} MathPix math {best[2]!r}")
print("board pages changed:", changed, "new records (new type, MathPix type):", dict(verdict))

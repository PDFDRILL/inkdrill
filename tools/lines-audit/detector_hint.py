#!/usr/bin/env python3
"""detector_hint.py <model.onnx> <H> <W> <inkcmp> -- author blocks typed as maths (CONTRACT unit 7, 2026-11-17):
DocLayout-YOLO's class boxes as inkcmp lines' typing hint, through the run path (tools/detector/layout_hint.py ->
`inkcmp lines --hint`; the model must be the 640 x 512 graph: H and W are kept for the record). The first run used a
scratch patch of lines.c (detector_hint.patch); the hint is now lines.c's ic_lines_hinted.
Prints the two author red tests (2304.03442 p1, 1805.01788 p1) and, on the board pages with a MathPix reference,
every record the hint changes with MathPix's type at that place; a display that becomes text is DISPLAY LOST. The hint
runs on a document's page 1 only; --page1=N adds page 1 of N library documents with a MathPix display (seed 9006).
On other pages the detector does not run: their output is compared with build/inkcmp's, byte for byte."""
import io, json, os, re, subprocess, sys, collections
MODEL, S, SW, NEW = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), os.path.abspath(sys.argv[4])
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "detector"))
import layout_hint


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - ix*iy
    return ix*iy/u if u > 0 else 0

OLD = os.path.join(ROOT, "build", "inkcmp")
HINT = os.path.join(os.environ.get("TMPDIR", "."), "detector_hint.txt")
B = os.path.expanduser("~/drillgroup/work/board"); L = os.path.expanduser("~/pdfdrill-library")
pages = [(f"{L}/{d}/{d}.pdf", 1, f"{L}/{d}/{d}.lines.json", d) for d in ("2304.03442", "1805.01788")]
pages += [(f"{B}/{k}/{k}.pdf", 1, f"{B}/{k}/mathpix.lines.json", k) for k in sorted(os.listdir(B)) if os.path.exists(f"{B}/{k}/mathpix.lines.json")]
PAGE1 = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--page1=")), "0"))   # + page 1 of N library docs
if PAGE1:                                          # whose page 1 has a MathPix display (seed 9006, the red and board
    import random                                  # documents excluded): the pages the hint actually runs on
    excl = {"2304.03442", "1805.01788"} | {re.sub(r"-p\d+$", "", k) for k in os.listdir(B)}
    docs = sorted(d for d in os.listdir(L) if d not in excl and os.path.exists(f"{L}/{d}/{d}.pdf") and os.path.exists(f"{L}/{d}/{d}.lines.json"))
    random.Random(9006).shuffle(docs)
    got = 0
    for d in docs:
        try:
            p1 = json.load(open(f"{L}/{d}/{d}.lines.json"))["pages"][0]
        except (ValueError, OSError, IndexError, KeyError):
            continue
        if p1.get("page") == 1 and "page_width" in p1 and any(l.get("type") == "math" and isinstance(l.get("region"), dict) for l in p1.get("lines", [])):
            pages.append((f"{L}/{d}/{d}.pdf", 1, f"{L}/{d}/{d}.lines.json", d + "-p1"))
            got += 1
            if got == PAGE1:
                break


def doc_page(key, n):
    """the page's number in its document: a board key ends -pNNNN (its PDF holds that one page)"""
    m = re.search(r"-p(\d+)$", key)
    return int(m[1]) if m and not key.endswith("-p1") else n
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
    if doc_page(key, n) == 1:                       # the hint runs on a document's page 1 only (coordinator, option 1)
        with open(HINT, "w") as f:                  # the run path: layout_hint.py -> inkcmp lines --hint
            for c, x0, y0, x1, y1 in layout_hint.hints(MODEL, pdf, n):
                f.write(f"1 {c} {x0:.5f} {y0:.5f} {x1:.5f} {y1:.5f}\n")
        new = subprocess.run([NEW, "lines", "--hint", HINT], input=pgm, capture_output=True).stdout.decode()
    else:
        new = subprocess.run([NEW, "lines"], input=pgm, capture_output=True).stdout.decode()
        hinted_not = globals().setdefault("hinted_not", collections.Counter())
        hinted_not["same" if new == old else "DIFFERENT"] += 1
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
print("pages 2+ without the hint, output against the unhinted binary:", dict(globals().get("hinted_not", {})))
print("pages changed:", changed, "new records (new type, MathPix type):", dict(verdict))

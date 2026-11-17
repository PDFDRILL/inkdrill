"""gsrun.py: `inkcmp lines` binaries on one page set, the Ghostscript route (inkdrill's own;
prep.py is poppler): gs pgmraw, crop box, 300 dpi with the longer side <= 3500 px; a folder without its main PDF uses pdfdrill's inspect render resized to the
MathPix page. Each page is rendered once (cached PGM) and run through every binary; only inkcmp is timed.
Scores one-to-one at IoU >= 0.5 (score below), raw (every MathPix node) and leaf (no node with children_ids,
no column/table/table_row/table_column). Never prints a library folder name.
Writes <WORK>/gs/<set name>.json; gsscore.py prints the strata.
usage: gsrun.py <set.tsv> <set name> <tag=binary>..."""
import hashlib, io, json, os, re, subprocess, sys, time
from pathlib import Path
from paths import LIB, WORK
LIB = Path(LIB)
W = Path(WORK) / "gs"; W.mkdir(parents=True, exist_ok=True)

# one-to-one greedy matching at IoU >= 0.5 on page-normalised boxes, page_info out of both
def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0.0
def nb(l, pg):
    r = l["region"]
    return (r["top_left_x"] / pg["page_width"], r["top_left_y"] / pg["page_height"],
            (r["top_left_x"] + r["width"]) / pg["page_width"], (r["top_left_y"] + r["height"]) / pg["page_height"])
def score(truth, ours):
    T = {p["page"]: p for p in truth["pages"]}; O = {p["page"]: p for p in ours["pages"]}
    nt = no = m = 0
    for k, q in T.items():
        p = O.get(k)
        if p is None: continue
        t = [nb(l, q) for l in q["lines"] if l["type"] != "page_info"]
        o = [nb(l, p) for l in p["lines"] if l["type"] != "page_info"]
        nt += len(t); no += len(o)
        pairs = sorted(((iou(a, b), i, j) for i, a in enumerate(t) for j, b in enumerate(o)), reverse=True)
        ui, uj = set(), set()
        for v, i, j in pairs:
            if v < 0.5: break
            if i in ui or j in uj: continue
            ui.add(i); uj.add(j); m += 1
    return {"truth": nt, "ours": no, "matched": m}


SET, NAME = sys.argv[1], sys.argv[2]
BINS = [a.split("=", 1) for a in sys.argv[3:]]
CONT = {"column", "table", "table_row", "table_column"}
cache = W / "pgm" / NAME; cache.mkdir(parents=True, exist_ok=True)
names = {"lib:" + hashlib.sha256(d).hexdigest()[:12]: os.fsdecode(d) for d in os.listdir(os.fsencode(LIB))}
rows = [l.rstrip("\n").split("\t") for l in open(SET) if l.strip() and not l.startswith(("#", "stratum"))]
out = {t: [] for t, _ in BINS}
skipped = []
for k, r in enumerate(rows):
    stratum, ident, page = r[0], r[1], int(r[2])
    folder = LIB / names.get(ident, ident)
    key = f"{k:03d}"
    lj = folder / (folder.name + ".lines.json")
    pdf = folder / (folder.name + ".pdf")
    render = folder / "inspect" / "pages" / f"page-{page:04d}.png"
    if not lj.exists():
        skipped.append((stratum, key, "no lines.json")); continue
    tp = next((q for q in json.load(open(lj))["pages"] if q.get("page") == page), None)
    if tp is None:
        skipped.append((stratum, key, "page not in lines.json")); continue
    pgmf = cache / (key + ".pgm")
    if not pgmf.exists():
        if pdf.exists():
            info = subprocess.run(["pdfinfo", "-box", "-f", str(page), "-l", str(page), str(pdf)], capture_output=True,
                                  text=True).stdout
            m = re.search(r"CropBox:\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)", info)
            side = max(float(m[3]) - float(m[1]), float(m[4]) - float(m[2])) if m else 842.0
            dpi = min(300.0, 3500 * 72 / side)
            pgm = subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pgmraw", f"-r{dpi:.2f}",
                                  "-dUseCropBox", f"-dFirstPage={page}", f"-dLastPage={page}", "-sOutputFile=%stdout",
                                  str(pdf)], capture_output=True).stdout
        elif render.exists():
            from PIL import Image
            im = Image.open(render).convert("L").resize((tp["page_width"], tp["page_height"]), Image.LANCZOS)
            b = io.BytesIO(); im.save(b, format="PPM"); pgm = b.getvalue()
        else:
            skipped.append((stratum, key, "no main PDF, no inspect render")); continue
        if not pgm.startswith(b"P5"):
            skipped.append((stratum, key, "render failed")); continue
        pgmf.write_bytes(pgm)
    pgm = pgmf.read_bytes()
    truth = {"pages": [tp]}
    leaf = {"pages": [dict(tp, lines=[l for l in tp["lines"] if not l.get("children_ids") and l["type"] not in CONT])]}
    for tag, b in BINS:
        t0 = time.perf_counter()
        p = subprocess.run([b, "lines"], input=pgm, capture_output=True)
        dt = time.perf_counter() - t0
        if p.returncode:
            out[tag].append({"stratum": stratum, "key": key, "fail": p.returncode}); continue
        cur = None
        for ln in p.stdout.decode().splitlines():
            f = ln.split()
            if f[0] == "INKLINES1":
                cur = {"page": page, "page_width": int(f[3]), "page_height": int(f[4]), "lines": []}
            elif f[0] != "end":
                x0, y0, x1, y1 = map(int, f[1:])
                cur["lines"].append({"type": f[0], "region": {"top_left_x": x0, "top_left_y": y0,
                                                              "width": x1 - x0, "height": y1 - y0}})
        ours = {"pages": [cur]}
        sr, sl = score(truth, ours), score(leaf, ours)
        out[tag].append({"stratum": stratum, "key": key, "raw": [sr["truth"], sr["ours"], sr["matched"]],
                         "leaf": [sl["truth"], sl["ours"], sl["matched"]], "ms": dt * 1000})
(W / f"{NAME}.json").write_text(json.dumps({"rows": out, "skipped": skipped}))
print(NAME, "pages", len(rows), "skipped", len(skipped), [s[2] for s in skipped])

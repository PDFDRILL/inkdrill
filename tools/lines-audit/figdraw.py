#!/usr/bin/env python3
"""figdraw.py <out.tsv> [N] [seed] -- a held-out set of born-digital pages with MathPix figures (diagram/chart regions)
for the figure step (CONTRACT unit 7). The library's documents with a PDF and a MathPix lines.json, minus every
document of the board set, held-out 1-4, the audit dev and confirm sets, and the three development documents (0864,
1805.01788, 2312.11532); one page per document (its pages with a figure, one drawn); seed 9003; a page drawn as one
page-size image (a scan) is drawn past. Written before anything is measured on it."""
import csv, glob, hashlib, json, os, random, re, subprocess, sys
LIB = os.environ.get("LINES_AUDIT_LIB", os.path.expanduser("~/pdfdrill-library"))
HERE = os.path.dirname(os.path.abspath(__file__))
out, N = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 9003
names = {"lib:" + hashlib.sha256(d).hexdigest()[:12]: os.fsdecode(d) for d in os.listdir(os.fsencode(LIB))}
excl = {"0864", "1805.01788", "2312.11532", "2312.11532v2"}
sets = [os.path.join(HERE, "audit-dev-set.tsv"), os.path.join(HERE, "audit-confirm-set.tsv")] + \
       sorted(glob.glob(os.path.expanduser("~/drillgroup/results/heldout*-set.tsv"))) + \
       [os.path.expanduser("~/drillgroup/results/board-set.tsv")]
for f in sets:
    for r in csv.reader(open(f), delimiter="\t"):
        if len(r) > 1 and not r[0].startswith("#"):
            excl.add(names.get(r[1], r[1]))
docs = sorted(d for d in os.listdir(LIB) if d not in excl and os.path.exists(os.path.join(LIB, d, d + ".pdf"))
              and os.path.exists(os.path.join(LIB, d, d + ".lines.json")))
rng = random.Random(seed)
rng.shuffle(docs)
rows = []
for d in docs:
    try:
        mp = json.load(open(os.path.join(LIB, d, d + ".lines.json")))
    except (ValueError, OSError):
        continue
    pages = [p["page"] for p in mp.get("pages", []) if any(l.get("type") in ("diagram", "chart")
                                                          and isinstance(l.get("region"), dict) for l in p.get("lines", []))]
    if not pages:
        continue
    page = rng.choice(pages)
    pdf = os.path.join(LIB, d, d + ".pdf")
    tr = subprocess.run(["mutool", "trace", pdf, str(page)], capture_output=True, text=True).stdout
    info = subprocess.run(["pdfinfo", "-f", str(page), "-l", str(page), pdf], capture_output=True, text=True).stdout
    m = re.search(r"Page\s+%d size:\s+([\d.]+) x ([\d.]+)" % page, info)
    if not m:
        continue
    area = float(m[1]) * float(m[2])
    scan = False
    for t in re.findall(r'<fill_image[^>]*transform="([^"]+)"', tr):
        M = [float(v) for v in t.split()]
        if abs(M[0] * M[3] - M[1] * M[2]) > 0.9 * area:
            scan = True
    if scan or "<fill_text" not in tr:
        continue
    rows.append(("figure", d, page))
    if len(rows) >= N:
        break
with open(out, "w") as fh:
    fh.write("# figdraw.py seed %d: born-digital pages with MathPix figures, held out (see the docstring)\n" % seed)
    fh.write("stratum\tdoc\tpage\n")
    for r in rows:
        fh.write("%s\t%s\t%d\n" % r)
print(len(rows), "pages written to", out)

#!/usr/bin/env python3
"""vecfig_set.py <set.tsv> [N] -- vecfig.py's figure source on fresh pages (CONTRACT unit 7, figure step, held out).
The pages: the set's rows in file order whose library folder has its PDF and whose MathPix page has a diagram or chart
region, the first N (default 20) after --skip=K such pages; --born-digital leaves out a page drawn as one page-size image
(a scan); the rule is fixed before any run. Prints per page and the totals: figures whole
(IoU >= 0.5), body lines swallowed."""
import csv, hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vecfig as V
args = [a for a in sys.argv[1:] if not a.startswith("--")]
tsv, N = args[0], int(args[1]) if len(args) > 1 else 20
SKIP = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--skip=")), "0"))   # the first SKIP qualifying pages
BORN = "--born-digital" in sys.argv                # a page drawn as one page-size image (a scan) does not qualify
names = {"lib:" + hashlib.sha256(d).hexdigest()[:12]: os.fsdecode(d) for d in os.listdir(os.fsencode(V.LIBROOT))}
done = tot_hit = tot_fig = tot_sw = 0
for r in csv.reader(open(tsv), delimiter="\t"):
    if not r or r[0].startswith("#") or r[0] == "stratum":
        continue
    doc, page = r[1], int(r[2])
    folder = names.get(doc, doc)
    lib = os.path.join(V.LIBROOT, folder)
    pdf, lj = os.path.join(lib, folder + ".pdf"), os.path.join(lib, folder + ".lines.json")
    if not (os.path.exists(pdf) and os.path.exists(lj)):
        continue
    pg = next((p for p in json.load(open(lj))["pages"] if p.get("page") == page), None)
    if not pg or not any(l["type"] in ("diagram", "chart") and isinstance(l.get("region"), dict) for l in pg["lines"]):
        continue
    if BORN:
        imgs = V.paths_of(pdf, page, with_images=True)[1]
        mb = V.subprocess.run(["pdfinfo", "-f", str(page), "-l", str(page), pdf], capture_output=True, text=True).stdout
        m = V.re.search(r"Page\s+%d size:\s+([\d.]+) x ([\d.]+)" % page, mb)
        if m and any((b[2] - b[0]) * (b[3] - b[1]) > 0.9 * float(m[1]) * float(m[2]) for b in imgs):
            continue
    if SKIP:
        SKIP -= 1
        continue
    hit, nf, sw, nopath = V.measure(doc, 12.0, {page}, folder=folder)
    print(f"ROW {r[0]:10} {doc:22} p{page:<4} figures {hit}/{nf} swallowed {sw}{'  (no drawn path on the page)' if nopath else ''}")
    tot_hit += hit; tot_fig += nf; tot_sw += sw; done += 1
    if done >= N:
        break
print(f"TOTAL {done} pages: figures whole {tot_hit} of {tot_fig} = {tot_hit / max(1, tot_fig):.0%}; body lines swallowed {tot_sw}")

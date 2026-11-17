#!/usr/bin/env python3
"""prep.py <set.tsv> <outdir>: render each page of a set at MathPix's page size (poppler, crop box, gray) to
<outdir>/<stem>.pgm and write its MathPix page to <outdir>/<stem>.truth.json. arXiv ids without a library PDF are
downloaded once to <WORK>/arxiv. Prints one status row per page; pages without any PDF are SKIPPED."""
from paths import LIB, WORK, GEMMA, DRILLGROUP  # noqa: F401
import csv, json, os, re, subprocess, sys
from PIL import Image
AX = f"{WORK}/arxiv"
fold = json.load(open(f"{WORK}/sets/folders.json"))
os.makedirs(AX, exist_ok=True)
tsv, out = sys.argv[1], sys.argv[2]; os.makedirs(out, exist_ok=True)
ARX = re.compile(r"^(\d{4}\.\d{4,5}(v\d+)?|\d{7}(v\d+)?)$")
meta = []
for r in csv.reader(open(tsv), delimiter="\t"):
    if not r or r[0].startswith("#") or r[0] == "stratum": continue
    stratum, doc, page = r[0], r[1], int(r[2])
    stem = doc.replace(":", "-") + f"_p{page:04d}"
    f = fold.get(doc)
    if f is None: print("SKIP no-folder", stratum, doc); continue
    lj = os.path.join(LIB, f, f + ".lines.json")
    if not os.path.exists(lj): print("SKIP no-lines", stratum, doc); continue
    mp = json.load(open(lj))
    pg = next((p for p in mp["pages"] if p.get("page") == page), None)
    if pg is None: print("SKIP no-page", stratum, doc); continue
    pw, ph = int(pg["page_width"]), int(pg["page_height"])
    pdf = os.path.join(LIB, f, f + ".pdf"); src = "library"
    if not os.path.exists(pdf):
        if not ARX.match(doc): print("SKIP no-pdf", stratum, doc); continue
        pdf = os.path.join(AX, doc + ".pdf"); src = "arxiv"
        if not os.path.exists(pdf):
            aid = doc if "." in doc else "hep-th/" + doc
            subprocess.run(["curl", "-sSL", "-m", "120", "-o", pdf, "https://arxiv.org/pdf/" + aid])
        if not os.path.exists(pdf) or open(pdf, "rb").read(5) != b"%PDF-":
            print("SKIP arxiv-failed", stratum, doc); continue
    info = subprocess.run(["pdfinfo", "-f", str(page), "-l", str(page), pdf], capture_output=True, text=True).stdout
    n = re.search(r"Pages:\s+(\d+)", info); m = re.search(r"Page\s+%d size:\s+([\d.]+) x ([\d.]+)" % page, info)
    if m is None: print("SKIP no-such-page", stratum, doc, src); continue
    npages = int(n.group(1))
    if src == "arxiv" and npages != len(mp["pages"]):
        print("SKIP arxiv-version-differs", stratum, doc, npages, len(mp["pages"])); continue
    wpt, hpt = map(float, m.groups())
    rot = re.search(r"Page\s+%d rot:\s+(\d+)" % page, info)
    if rot and rot.group(1) in ("90", "270"): wpt, hpt = hpt, wpt
    dpi = pw * 72 / wpt
    base = os.path.join(out, stem)
    subprocess.run(["pdftoppm", "-cropbox", "-gray", "-r", f"{dpi:.3f}", "-f", str(page), "-l", str(page),
                    "-singlefile", pdf, base], check=True)
    im = Image.open(base + ".pgm")
    w, h = im.size
    if (w, h) != (pw, ph): im.resize((pw, ph), Image.LANCZOS).save(base + ".pgm")
    json.dump({"pages": [pg]}, open(base + ".truth.json", "w"))
    meta.append({"stratum": stratum, "doc": doc, "page": page, "stem": stem, "src": src, "pw": pw, "ph": ph,
                 "rendered": [w, h], "dpi": round(dpi, 1)})
    print("OK", stratum, stem, src, round(dpi, 1), (w, h), "->", (pw, ph), "aspect_err %.3f" % abs(w / h - pw / ph))
json.dump(meta, open(os.path.join(out, "meta.json"), "w"))
print(len(meta), "pages prepared")

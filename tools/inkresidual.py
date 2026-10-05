#!/usr/bin/env python3
"""Delete the equations whose typeset reading already matches the ink.

THE RESIDUAL IS THE PRODUCT. A table of 52,748 rows where a reader is ~30%
correct is mostly rows that are fine, and a human eye spent on those is
spent for nothing. This renders each row's LaTeX, describes BOTH pictures
with ink-only topology, and drops the row when the two agree -- so what
survives is the disagreement.

The criterion is inkdrill's alone and mentions no second reader: does OUR
typeset reading have the same topology as the page's own ink? A row where
our LaTeX differs from MathPix's but matches the ink has nothing for an eye
to fix, whichever reader's string is prettier.

MEASURED BEFORE BUILT (`tools/premise/measure.py residuals` holds the same
shape; this harness is out/694). 150 rows equal-after-normalising against
150 rows where the reference carries a relation symbol ours lacks -- the
28.3% defect, the class this must never delete:

    feature                      AGREE median   DISAGREE median
    ink - render components           0              +7
    elongation difference             0.25            3.67
    aspect difference                 0.03            0.33

    ink has MORE components than our render:
        6% of AGREE rows, 95% of DISAGREE rows

Rules, on that sample, with the cost that matters in the last column:

    rule                                  deletes AGREE   LOSES DISAGREE
    components equal                           89%            3.3%
    |dcomp|<=1                                 95%            5.3%
    |dcomp|<=1 & |dh|<=1 & aspect<.15          71%            1.3%
    |dcomp|<=1 & |dh|<=1 & aspect<.10 & elong<.8  61%         0.7%

The last is chosen: deleting a row that still holds a defect is the
expensive error, because the table exists for those rows. 61% of the work
removed for 0.7% of the findings is the trade taken, and both numbers are
over the labelled sample of 300, NOT over the 52,748 -- the proxy labels
come from comparing two readers, so a row neither label covers is scored by
the rule and not by this measurement.

The labels are PROXIES and the population is the labelled subset. What the
rule is applied to is every row; what it was validated on is 300 rows whose
agreement two readers happen to agree about.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import subprocess
import sys
import time

csv.field_size_limit(10 ** 7)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from inkdrill import aggregate, pnmio, raster, sweep        # noqa: E402

DPI = 300
THRESHOLD = 200
# The chosen rule, as named constants so the rule cannot be retuned in one
# place and reported from another.
MAX_DCOMP = 1
MAX_DHOLES = 1
MAX_DASPECT = 0.10
MAX_DELONG = 0.8
BATCH = 400


def describe(m):
    """Ink-only descriptors, scale-free.

    `cycle_count` is the hole count by the identity the sweep already checks
    (cycles == E - V + C); `Moments` already carries elongation, shear and
    the bounding box. Nothing here recomputes what the package defines once.
    """
    if m is None:
        return None
    ink = m.data.count(0xFF)
    if ink == 0:
        return None
    sw = sweep.sweep(m, conn=8, capture=sweep.Capture.GRAPH)
    mo = aggregate.moments_of_mask(m)
    w, h = mo.width, mo.height
    nruns = sum(1 for _ in raster.iter_runs(m))
    return {"ink": ink, "components": sw.component_count,
            "holes": sw.cycle_count,
            "aspect": (w / h) if h else 0.0,
            "fill": ink / (w * h) if w and h else 0.0,
            "elong": mo.elongation, "shear": mo.shear,
            "runs": nruns}


def agrees(r, i):
    """True when the rendered reading and the ink have the same shape."""
    if not r or not i:
        return False
    if abs(i["components"] - r["components"]) > MAX_DCOMP:
        return False
    if abs(i["holes"] - r["holes"]) > MAX_DHOLES:
        return False
    if abs(i["aspect"] - r["aspect"]) / max(r["aspect"], 0.01) > MAX_DASPECT:
        return False
    if abs(i["elong"] - r["elong"]) > MAX_DELONG:
        return False
    return True


def render_batch(latexes, work, preamble):
    """One page per equation. A page that fails to typeset still occupies its
    page, so page N is equation N -- which is the only reason the pairing
    below is an index and not a search."""
    parts = [preamble, r"\begin{document}"]
    for lx in latexes:
        parts.append(r"\[" + lx + r"\]" + "\n" + r"\newpage")
    parts.append(r"\end{document}")
    (work / "b.tex").write_text("\n".join(parts))
    for _ in (1, 2):
        subprocess.run(["xelatex", "-interaction=nonstopmode", "b.tex"],
                       cwd=work, capture_output=True, text=True, timeout=3600)
    pdf = work / "b.pdf"
    if not pdf.exists():
        return []
    proc = subprocess.run(
        ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pgmraw",
         f"-r{DPI}", "-sOutputFile=%stdout", str(pdf)],
        capture_output=True, timeout=7200)
    return list(pnmio.load_masks(proc.stdout, dpi=DPI, threshold=THRESHOLD))


def ink_mask(path):
    """A crop JPEG as a mask. ImageMagick v7 is `magick`; `convert` is
    deprecated and on this machine is the v6 shim."""
    try:
        r = subprocess.run(["magick", str(path), "-colorspace", "Gray",
                            "-compress", "none", "pgm:-"],
                           capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return pnmio.load_mask(r.stdout, dpi=DPI, threshold=THRESHOLD)
    except Exception:                                        # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rows-csv", type=pathlib.Path, required=True)
    ap.add_argument("--crops", type=pathlib.Path, required=True,
                    help="directory holding <document>-crops/")
    ap.add_argument("--preamble", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--work", type=pathlib.Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    have = {}
    for d in args.crops.glob("*-crops"):
        doc = d.name[:-6]
        have[doc] = {p.stem[len(doc) + 1:]: p for p in d.glob("*.jpg")}

    rows = []
    with args.rows_csv.open(newline="", encoding="utf-8",
                            errors="surrogateescape") as fh:
        for r in csv.DictReader(fh):
            crop = have.get(r["document"], {}).get(r["label"])
            lx = (r.get("latex") or "").strip()
            if crop is None or not lx:
                continue
            rows.append((r["document"], r["label"], lx, crop))
            if args.limit and len(rows) >= args.limit:
                break
    print(f"{len(rows):,} rows with ink and LaTeX", flush=True)

    args.work.mkdir(parents=True, exist_ok=True)
    preamble = args.preamble.read_text()
    done, counts = [], collections.Counter()
    t0 = time.time()
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        masks = render_batch([c[2] for c in chunk], args.work, preamble)
        for k, (doc, label, lx, crop) in enumerate(chunk):
            r = describe(masks[k]) if k < len(masks) else None
            i = describe(ink_mask(crop))
            ok = agrees(r, i)
            counts["agrees" if ok else "differs"] += 1
            if r is None:
                counts["render unusable"] += 1
            if i is None:
                counts["ink unusable"] += 1
            done.append({"document": doc, "label": label,
                         "agrees": ok, "rendered": r, "ink": i})
        args.out.write_text(json.dumps(done))
        el = time.time() - t0
        n = start + len(chunk)
        print(f"  {n:,}/{len(rows):,}  agrees {counts['agrees']:,}  "
              f"differs {counts['differs']:,}  {el:.0f}s  "
              f"eta {el/max(n,1)*(len(rows)-n)/60:.0f}m", flush=True)
    print(f"\n{len(done):,} rows described")
    for k, v in counts.most_common():
        print(f"  {k:18} {v:,}")
    keep = sum(1 for d in done if not d["agrees"])
    print(f"\nSURVIVE for the eye: {keep:,} / {len(done):,} "
          f"({100*keep/max(len(done),1):.1f}%)")
    print(f"DELETED as already matching the ink: {len(done)-keep:,}")


if __name__ == "__main__":
    main()

"""A second copy of an equation list, carrying MATHPIX's LaTeX.

    python3 tools/mathpixswap.py --list <eqlist.json> --out-dir <dir>
                                 [--iou 0.5] [--limit N]

The lists carry the pdf2mmd reader's LaTeX. Where a MathPix reading of
the same document exists, this emits a parallel list in the SAME schema
with each row's LaTeX replaced by MathPix's, and DROPS every row for
which no MathPix equation can be found. The result is a list of the
formulas both readers saw, read by the paid one.

THE JOIN IS GEOMETRIC, AND THE FRAME IS CHECKED, NOT ASSUMED. A list
row's region and a MathPix line's region are both in a 250 dpi
page-top-left frame, so they can be compared directly -- but only when
they describe the same page box. Every page is checked
(`page_width_pt * 250/72` against MathPix's own `page_width`) and a page
whose frames disagree is SKIPPED and counted, never nudged into
agreement. Guessing an offset is how a region ends up in the bottom
margin (out/691).

THE THRESHOLD IS MEASURED. Match quality is intersection-over-union
against the MathPix `math` lines on that page, and the cut is set where
the two readers stop describing the same object. Over 2,244 rows of ten
documents, the median agreement between the two readers' LaTeX against
the best-IoU band:

    IoU 0.0-0.3   agreement 0.00-0.08   different objects
    IoU 0.3-0.4   agreement 0.32
    IoU 0.5-0.6   agreement 0.58
    IoU 0.7-1.0   agreement 0.82-0.89   plainly the same equation

Default 0.5, above the collapse with margin. Agreement is NOT expected
to reach 1.0 even on a correct match -- the two readers differing is
the reason for having both.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

csv.field_size_limit(10 ** 7)

PX_PER_PT = 250.0 / 72.0
#: a page whose two frames differ by more than this FRACTION is skipped.
#: Relative, not absolute: measured over 1,041 readings, 935 agree to
#: within 4 px, 7 differ by under 10% (MathPix trimming), and ~11 raster
#: a DIFFERENT PAGE BOX at ratios 0.69-0.83. The last are dropped rather
#: than corrected, because the inset is unknown and guessing it is how a
#: region lands in the bottom margin (out/691).
FRAME_TOL_FRAC = 0.10
#: a reading whose page dimensions are ~1/(250/72) of the expected pixel
#: width is in POINTS -- an older pdfminer-derived reading, not a MathPix
#: raster. 88 documents, 13,132 rows. They have no `source` key either,
#: so "no source" alone does not identify MathPix.
POINTS_RATIO = (0.26, 0.31)
DEFAULT_IOU = 0.5
_DELIM = re.compile(r"^\s*\\\[|\\\]\s*$|^\s*\$\$|\$\$\s*$")


def strip_delimiters(text: str) -> str:
    """MathPix writes a display equation as `\\[ ... \\]`. The list's
    LaTeX column carries the body alone, so the delimiters go."""
    s = (text or "").strip()
    s = _DELIM.sub("", s)
    return s.strip()


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2]-a[0]) * (a[3]-a[1]) + (b[2]-b[0]) * (b[3]-b[1]) - inter
    return inter / union if union > 0 else 0.0


def mathpix_reading(doc: pathlib.Path):
    """The MathPix lines.json of a document, or None.

    pdf2mmd stamps `source` on every page; MathPix stamps none. The
    displaced-MathPix name is checked first, since a document whose
    canonical reading was replaced keeps the paid one there.
    """
    for name in (f"{doc.name}.lines.mathpix.bak.json", f"{doc.name}.lines.json"):
        p = doc / name
        if not p.exists():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        pages = j.get("pages") or []
        if pages and not any(pg.get("source") for pg in pages):
            return j
    return None


def index_math_lines(reading):
    """page -> [(box, latex, line_id)] and page -> page width in px."""
    by_page, widths = collections.defaultdict(list), {}
    for pg in reading.get("pages") or []:
        n = pg.get("page")
        widths[n] = pg.get("page_width")
        for ln in pg.get("lines") or []:
            if ln.get("type") != "math":
                continue
            g = ln.get("region") or {}
            if not g:
                continue
            box = (g["top_left_x"], g["top_left_y"],
                   g["top_left_x"] + g["width"], g["top_left_y"] + g["height"])
            by_page[n].append((box, strip_delimiters(ln.get("text")), ln.get("id")))
    return by_page, widths


def swap_document(rows, reading, min_iou):
    """Rows of one document -> (rows carrying MathPix LaTeX, why dropped)."""
    by_page, widths = index_math_lines(reading)
    kept, dropped = [], collections.Counter()
    for r in rows:
        try:
            page = int(r["page"])
            want_w = float(r["page_width_pt"]) * PX_PER_PT
            x, y = float(r["px_x"]), float(r["px_y"])
            w, h = float(r["px_w"]), float(r["px_h"])
        except (KeyError, ValueError):
            dropped["unparsable row"] += 1
            continue
        mw = widths.get(page)
        if mw is None:
            dropped["MathPix has no such page"] += 1
            continue
        ratio = float(mw) / want_w if want_w else 0.0
        if POINTS_RATIO[0] < ratio < POINTS_RATIO[1]:
            dropped["the reading is in POINTS, not a MathPix raster"] += 1
            continue
        if abs(ratio - 1.0) > FRAME_TOL_FRAC:
            dropped["MathPix rastered a different page box"] += 1
            continue
        a = (x, y, x + w, y + h)
        best, hit = 0.0, None
        for box, latex, lid in by_page.get(page, ()):
            v = iou(a, box)
            if v > best:
                best, hit = v, (latex, lid)
        if hit is None or best < min_iou:
            dropped["no MathPix equation at this region"] += 1
            continue
        if not hit[0]:
            dropped["MathPix line carries no LaTeX"] += 1
            continue
        out = dict(r)
        out["latex"] = hit[0]
        out["latex_origin"] = "mathpix"
        out["mathpix_line_id"] = hit[1] or ""
        out["mathpix_iou"] = f"{best:.3f}"
        kept.append(out)
    return kept, dropped


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rows-csv", type=pathlib.Path, required=True)
    ap.add_argument("--library", type=pathlib.Path,
                    default=pathlib.Path.home() / "pdfdrill-library")
    ap.add_argument("--out-dir", type=pathlib.Path, required=True)
    ap.add_argument("--name", default=None,
                    help="output stem; defaults to the input stem + .mathpix")
    ap.add_argument("--iou", type=float, default=DEFAULT_IOU)
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many documents (a shape check)")
    args = ap.parse_args()

    by_doc = collections.defaultdict(list)
    with args.rows_csv.open(newline="", encoding="utf-8",
                            errors="surrogateescape") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        for r in reader:
            by_doc[r["document"]].append(r)
    print(f"input: {sum(len(v) for v in by_doc.values()):,} rows, "
          f"{len(by_doc):,} documents")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or (args.rows_csv.name.split(".")[0] + ".mathpix")
    out_fields = fields + ["mathpix_line_id", "mathpix_iou"]
    kept_total, dropped = 0, collections.Counter()
    docs_with, docs_without = 0, 0
    csv_path = args.out_dir / f"{stem}.rows.csv"
    rows_out = []
    with csv_path.open("w", newline="", encoding="utf-8",
                       errors="surrogateescape") as fh:
        wr = csv.DictWriter(fh, fieldnames=out_fields, extrasaction="ignore")
        wr.writeheader()
        for i, (doc, rows) in enumerate(sorted(by_doc.items())):
            if args.limit and i >= args.limit:
                break
            reading = mathpix_reading(args.library / doc)
            if reading is None:
                docs_without += 1
                dropped["the document has no MathPix reading"] += len(rows)
                continue
            docs_with += 1
            kept, why = swap_document(rows, reading, args.iou)
            dropped.update(why)
            kept_total += len(kept)
            for k in kept:
                wr.writerow(k)
                rows_out.append(k)
            if i % 100 == 0:
                print(f"  {i}/{len(by_doc)} documents, {kept_total:,} rows kept",
                      flush=True)
    meta = {"schema": "inkdrill.eqlist.mathpix/1",
            "source_rows_csv": str(args.rows_csv),
            "latex_from": "mathpix",
            "join": {"frame": "250 dpi, y down, page top-left, CHECKED per page",
                     "frame_tolerance_frac": FRAME_TOL_FRAC,
                     "rule": "best intersection-over-union against MathPix "
                             "`math` lines on the same page",
                     "min_iou": args.iou},
            "counts": {"documents_with_mathpix": docs_with,
                       "documents_without": docs_without,
                       "rows_kept": kept_total,
                       "rows_dropped": dict(dropped)}}
    (args.out_dir / f"{stem}.meta.json").write_text(json.dumps(meta, indent=1))
    print(f"\ndocuments with a MathPix reading: {docs_with:,} "
          f"| without: {docs_without:,}")
    print(f"rows kept: {kept_total:,}")
    for k, v in dropped.most_common():
        print(f"   dropped {v:>8,}  {k}")
    print(f"-> {csv_path}")
    print(f"-> {args.out_dir / f'{stem}.meta.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

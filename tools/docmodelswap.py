"""The equation list, with each row's LaTeX taken from a MathPix DOCMODEL.

    python3 tools/docmodelswap.py --rows-csv <corpus-display.rows.csv> \
                                  --out-dir <dir> [--iou 0.5] [--limit N]

`model.docmodel.json` holds ONE reader's output, named in `meta.source`.
Where that is `mathpix`, its `Equation` objects carry MathPix's LaTeX
already macro-expanded, with the author's equation number and a crop
URL. This replaces each list row's LaTeX with it and DROPS every row no
MathPix equation can be matched to.

WHY THE MATCH IS BY OVERLAP AND NOT BY EQUALITY. The regions look like
they should be identical -- same 250 dpi frame, same origin -- and they
are not, because the list is built from `equations.json` (the pdf2mmd
reading of the PDF) while the docmodel's region is MathPix's own box.
Two readers, two opinions of where one equation sits. Measured on
0902.0431, over 1,064 rows matched at IoU >= 0.5:

    dx  median  -6   p10  -9   p90  -3
    dy  median  -2   p10  -8   p90 +20
    dw  median +10   p10  +6   p90 +16
    dh  median  +5   p10 -11   p90 +15

A frame offset would be a constant; dy spreading -8 to +20 is not an
offset, it is MathPix drawing a slightly different box. Exact matching
scores 0.0% on three documents tried; overlap scores a median IoU of
0.87. So equality is the wrong rule here and its failure is a fact
about the two readers, not a defect in either.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
csv.field_size_limit(10 ** 7)

#: pdfdrill's reader. Importing it is a TOOL-level dependency, not a
#: package one -- `inkdrill/` stays standard library. `load_docgraph` is
#: the documented read-only path and is ~3x faster than json.load on a
#: 67 MB model; plain json is the fallback so the tool still runs
#: without their tree.
PDFDRILL_SRC = pathlib.Path.home() / "MX" / "PDFDRILL" / "src"
if PDFDRILL_SRC.is_dir():
    sys.path.insert(0, str(PDFDRILL_SRC))

DEFAULT_IOU = 0.5

# Strings that ONLY our reader emits. `DEFERRED` is its deferral marker,
# `deferred_glyphs` and `gaps` its own structures; MathPix has no such
# concept and never writes one. Matched as bytes against the whole file.
PDF2MMD_MARKERS = (b"DEFERRED", b"deferred_glyphs", b'"gaps"')


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / union if union > 0 else 0.0


def equations_of(doc: pathlib.Path):
    """page -> [(box, props)] for a MathPix docmodel, or None.

    `meta.source` is a CLAIM, not evidence, and the gate needs both.

    A model with NO source key is UNKNOWN and is refused: absence of a
    field is not evidence of MathPix, the same mistake as reading "no
    `source` on a page" as "this is a MathPix reading" (out/692).

    And `meta.source == "mathpix"` is FALSE on 24 of 854 documents. The
    model is built from the live `<doc>.lines.json`, which is whatever
    wrote it last: a free reader displaced MathPix's reading, the model
    was rebuilt from ours, and `meta.source` still said mathpix because
    nothing re-derives it. Nobody lied -- the field was true when written.
    The rows that came through read like

        'L <!-- DEFERRED p3l33s1 rect=[...] reason=script-attachment --> $'
        '$\\gamma$ ð $\\lambda$ Þ ¼ 1L $\\ln$ ... (cid:2)'

    which is our own reader's deferral marker and pdfminer glyph garbage,
    published under `latex_origin: "mathpix-docmodel"`. 833 of vol12's
    2,952 rows would not typeset, 28%, clustered in the sigma26 papers --
    exactly the documents being compared against.

    So a pdf2mmd-only marker in the model is EVIDENCE of who produced it,
    and evidence beats the claim. A byte scan is enough: these markers do
    not occur in a MathPix reading at all, so a substring test needs no
    parse and cannot be fooled by where in the structure they sit.

    Such a document drops out of the MathPix population entirely rather
    than being repaired -- it has no usable MathPix reading, whatever the
    field says. One exception is recoverable and is NOT handled here: a
    document holding a `.lines.mathpix.bak.json` still has its real
    MathPix reading parked, and its model can be rebuilt from that by the
    tool that owns the document. Only sigma26-075 of the 24 has one.
    """
    path = doc / "model.docmodel.json"
    if not path.exists():
        return None, "no model.docmodel.json"
    try:
        from pdfdrill.model_io import load_docgraph
        g = load_docgraph(str(path))
        meta, nodes = g.meta, list(g.of_type("Equation"))
        props = [n.props for n in nodes]
    except Exception:
        try:
            j = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            return None, "the model will not parse"
        meta = j.get("meta") or {}
        props = [o.get("props") or {} for o in (j.get("objects") or [])
                 if (o.get("type") or o.get("kind")) == "Equation"]
    src = (meta or {}).get("source")
    if src != "mathpix":
        return None, (f"the model is {src!r}, not mathpix" if src
                      else "the model names no source: unknown, not MathPix")
    # EVIDENCE OVER CLAIM -- see the docstring. Read the bytes, not the
    # parsed structure: a marker anywhere in the model means our reader
    # wrote this, wherever it sits.
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "the model will not read"
    for mark in PDF2MMD_MARKERS:
        if mark in raw:
            return None, (f"the model declares mathpix but contains "
                          f"{mark.decode()!r}: our reader produced it")
    by_page = collections.defaultdict(list)
    for p in props:
        reg = p.get("region")
        if not reg or not p.get("latex"):
            continue
        by_page[p.get("page")].append(
            ((reg["top_left_x"], reg["top_left_y"],
              reg["top_left_x"] + reg["width"], reg["top_left_y"] + reg["height"]), p))
    return by_page, None


def swap(rows, by_page, min_iou):
    sys.path.insert(0, str(PDFDRILL_SRC))
    from pdfdrill import eqlist
    kept, dropped = [], collections.Counter()
    for r in rows:
        try:
            page = int(r["page"])
            a = (float(r["px_x"]), float(r["px_y"]),
                 float(r["px_x"]) + float(r["px_w"]),
                 float(r["px_y"]) + float(r["px_h"]))
        except (KeyError, ValueError):
            dropped["unparsable row"] += 1
            continue
        best, hit = 0.0, None
        for box, props in by_page.get(page, ()):
            v = iou(a, box)
            if v > best:
                best, hit = v, props
        if hit is None or best < min_iou:
            dropped["no MathPix equation at this region"] += 1
            continue
        out = dict(r)
        # The superseded reading's own origin, kept before it is
        # overwritten: v2's `source` block named itself "mathpix-docmodel"
        # inside a note saying it describes the LaTeX that was REPLACED,
        # because this column had already been overwritten when the block
        # was built. The note and the field contradicted each other.
        out["latex_origin_pdf2mmd"] = r.get("latex_origin") or "pdf2mmd"
        out["latex"] = hit["latex"]
        out["latex_origin"] = "mathpix-docmodel"
        # THE STABILITY HASH DESCRIBES THE TEXT IN THE ROW, so swapping the
        # LaTeX without recomputing it leaves the one field that detects a
        # stale mark pointing at text the row no longer carries. It read
        # wrong on 51,431 of 59,079 rows (87%) before this line existed, and
        # nothing raised: a 16-hex string is still a 16-hex string. The
        # superseded hash is kept rather than dropped, so a consumer holding
        # a pdf2mmd mark can still see which row it was made against.
        prior = r.get("latex_sha16") or ""
        out["latex_sha16"] = eqlist.latex_sha16(hit["latex"])
        out["latex_sha16_pdf2mmd"] = prior
        out["mathpix_iou"] = f"{best:.3f}"
        out["mathpix_refnum"] = hit.get("refnum") or ""
        out["mathpix_confidence"] = hit.get("confidence")
        out["mathpix_cdn_url"] = hit.get("cdn_url") or ""
        kept.append(out)
    return kept, dropped


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rows-csv", type=pathlib.Path, required=True)
    ap.add_argument("--library", type=pathlib.Path,
                    default=pathlib.Path.home() / "pdfdrill-library")
    ap.add_argument("--out-dir", type=pathlib.Path, required=True)
    ap.add_argument("--name", default="corpus-display-mathpix-docmodel")
    ap.add_argument("--iou", type=float, default=DEFAULT_IOU)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    by_doc = collections.defaultdict(list)
    with args.rows_csv.open(newline="", encoding="utf-8",
                            errors="surrogateescape") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        for r in reader:
            by_doc[r["document"]].append(r)
    print(f"input: {sum(len(v) for v in by_doc.values()):,} rows, "
          f"{len(by_doc):,} documents", flush=True)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    # A column missing from this list is silently DROPPED by the writer's
    # extrasaction="ignore", so a new field in swap() must be named here too.
    extra = ["mathpix_iou", "mathpix_refnum", "mathpix_confidence",
             "mathpix_cdn_url", "latex_sha16_pdf2mmd",
             "latex_origin_pdf2mmd"]
    out_fields = fields + [c for c in extra if c not in fields]
    csv_path = args.out_dir / f"{args.name}.rows.csv"
    dropped, kept_rows, docs_used = collections.Counter(), [], 0
    with csv_path.open("w", newline="", encoding="utf-8",
                       errors="surrogateescape") as fh:
        wr = csv.DictWriter(fh, fieldnames=out_fields, extrasaction="ignore")
        wr.writeheader()
        for i, (doc, rows) in enumerate(sorted(by_doc.items())):
            if args.limit and i >= args.limit:
                break
            by_page, why = equations_of(args.library / doc)
            if by_page is None:
                dropped[why] += len(rows)
                continue
            docs_used += 1
            kept, why_counts = swap(rows, by_page, args.iou)
            dropped.update(why_counts)
            for k in kept:
                wr.writerow(k)
            kept_rows.extend(kept)
            if i % 100 == 0:
                print(f"  {i}/{len(by_doc)} documents, {len(kept_rows):,} rows",
                      flush=True)
    meta = {"name": args.name, "latex_from": "mathpix (model.docmodel.json)",
            "source_rows_csv": str(args.rows_csv),
            "join": {"rule": "best IoU against the docmodel's Equation regions",
                     "min_iou": args.iou,
                     "why_not_equality": "the list is pdf2mmd's reading of the "
                                         "PDF and the docmodel region is "
                                         "MathPix's own box; exact match is "
                                         "0.0% and median IoU is 0.87"},
            "counts": {"documents_with_a_mathpix_docmodel": docs_used,
                       "rows_kept": len(kept_rows),
                       "rows_dropped": dict(dropped)}}
    (args.out_dir / f"{args.name}.meta.json").write_text(json.dumps(meta, indent=1))
    print(f"\ndocuments used: {docs_used:,} | rows kept: {len(kept_rows):,}")
    for k, v in dropped.most_common():
        print(f"   dropped {v:>8,}  {k}")
    print(f"-> {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

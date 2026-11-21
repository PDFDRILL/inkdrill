"""scanjudge.py -- the judge's scan route: a reading compared with a page that has no glyph layer (`inkcmp-judge
... --route scan`).

Why: a scan, or a scan under an invisible OCR layer, has no glyphs to pair a reading's characters with, so the glyph
route cannot judge it, and 9 of the 30 board pages had no residual in any column (board37). MathPix and Gemma4 read
those pages' pixels; this route puts them on the board.

How (the rule-6 experiment, work/run-files/scan-exp, gate passed 2026-11-21: a reading with 5% of its words dropped,
or 5% of all its characters substituted, scored worse on 10 of 10 born-digital pages; Spearman 0.867 against the
glyph route's residual on the same pages):
- A: the page rendered at 300 dpi grey, cut at 128, its ink components (nest) with their holes. The page's text layer
  is never read.
- B: the reading's lines set by the judge's own compile_group at 10 pt in Computer Modern -- a scan tells no font --
  one snippet page per line, rendered and cut alike.
- Each line's rendered components are mapped onto the page's ink inside the line's box by extent (x and y scaled
  separately), then counted by component: MISSING, text-like page ink no rendered component covers (outside the boxes
  too: a line the reading left out is missing ink); INVENTED, a rendered component with no page ink under it; HOLES, a
  rendered component over exactly one page component whose hole count differs.
- residual = (missing + invented + holes) / text-like page components (height 0.3-2.5 of the median component's,
  width at most 5 of it: figure ink is left out).

Guarantees:
- G1 The page's text layer is never read: neither `judge.chars` nor `judge.trace` is called.
- G2 A snippet document that does not come out sets no line: every page component is then missing (as the glyph
  route, where a failed document fails every line).
- G3 The output names the blind class on every page: a substitution with the same topology (t/n, n/u, l/1) is not
  seen, and a scan residual is not comparable with a glyph-route residual (they are never averaged).
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys

from . import nest, pnmio

DPI = 300
BLIND = ("BLIND a substitution with the same topology (t/n, n/u, l/1) is not seen; a scan residual is not comparable "
         "with a glyph-route residual")


def components(pgm: str) -> list:
    """a rendered page's ink components: (x0, y0, x1, y1, holes), half-open, at least 4 px"""
    n = nest.nest(pnmio.load_mask(pgm, dpi=DPI))
    return [(r.x0, r.y0, r.x1 + 1, r.y1 + 1, len(n.holes_of(r.id)))
            for r in n.regions.values() if r.kind is nest.Kind.INK and r.area >= 4]


def render(pdf: str, prefix: str, first: int | None = None) -> list:
    args = ["pdftoppm", "-gray", "-r", str(DPI)] + (["-f", str(first), "-l", str(first)] if first else [])
    subprocess.run(args + [pdf, prefix], check=True, capture_output=True)
    return sorted(glob.glob(prefix + "-*.pgm"), key=lambda p: int(re.search(r"-(\d+)\.pgm$", p)[1]))


def judge_scan(a_pdf: str, pno: int, lines_json: str, wd: str, out=sys.stdout) -> dict:
    from . import judge as J                         # the reading's lines and their setting are the glyph route's
    os.makedirs(wd, exist_ok=True)
    with open(lines_json, encoding="utf-8") as fh:
        doc = json.load(fh)
    pg = next((p for p in doc["pages"] if p.get("page") == pno), None) or doc["pages"][pno - 1]
    lv = J.leaves(pg)
    pw_pt, _ = J.page_size(a_pdf, pno)
    k = pw_pt / pg["page_width"] * DPI / 72 if pg.get("page_width") else 0.0   # reading px -> 300 dpi px
    A = components(render(a_pdf, os.path.join(wd, "scan-page"), pno)[0])
    hs = sorted(c[3] - c[1] for c in A)
    med = hs[len(hs) // 2] if hs else 1
    text_like = [j for j, c in enumerate(A) if 0.3 * med <= c[3] - c[1] <= 2.5 * med and c[2] - c[0] <= 5 * med]
    texts = [J.line_tex(ln) for ln in lv]
    failed, pages, bo = {}, [], 0
    if texts:
        snip, got = J.compile_group([(texts, [10.0] * len(texts))], "", wd)
        failed, _, bo = got[0]
        if os.path.exists(snip):                     # G2: no document, no line
            pages = render(snip, os.path.join(wd, "scan-snip"))
    covered, invented, holes, rendered, judged, not_set = set(), 0, 0, 0, 0, 0
    unplaced = 0
    for i, ln in enumerate(lv):
        r = ln.get("region")
        if not isinstance(r, dict):                  # no geometry: not set here, its ink stays missing
            print(f"N L{i} UNPLACED", file=out)
            unplaced += 1
            continue
        box = (r["top_left_x"] * k, r["top_left_y"] * k, (r["top_left_x"] + r["width"]) * k,
               (r["top_left_y"] + r["height"]) * k)
        under_box = [j for j, c in enumerate(A)
                     if box[0] <= (c[0] + c[2]) / 2 < box[2] and box[1] <= (c[1] + c[3]) / 2 < box[3]]
        if i in failed or bo + i >= len(pages):
            print(f"N L{i} NOT_SET", file=out)
            not_set += 1
            continue
        if not under_box:
            continue
        B = components(pages[bo + i])
        if not B:
            continue
        judged += 1
        rendered += len(B)
        ax0, ay0 = min(A[j][0] for j in under_box), min(A[j][1] for j in under_box)
        ax1, ay1 = max(A[j][2] for j in under_box), max(A[j][3] for j in under_box)
        bx0, by0 = min(c[0] for c in B), min(c[1] for c in B)
        bx1, by1 = max(c[2] for c in B), max(c[3] for c in B)
        sx, sy = (ax1 - ax0) / max(bx1 - bx0, 1), (ay1 - ay0) / max(by1 - by0, 1)
        for c in B:
            q = (ax0 + (c[0] - bx0) * sx - 2, ay0 + (c[1] - by0) * sy - 2, ax0 + (c[2] - bx0) * sx + 2,
                 ay0 + (c[3] - by0) * sy + 2)
            under = [j for j in under_box
                     if q[0] <= (A[j][0] + A[j][2]) / 2 < q[2] and q[1] <= (A[j][1] + A[j][3]) / 2 < q[3]]
            if not under:
                invented += 1
                print(f"S L{i} invented {round(q[0])} {round(q[1])} {round(q[2])} {round(q[3])}", file=out)
                continue
            covered.update(under)
            if len(under) == 1 and A[under[0]][4] != c[4]:
                holes += 1
                print(f"S L{i} holes {A[under[0]][4]} {c[4]} {A[under[0]][0]} {A[under[0]][1]}", file=out)
    missing = [j for j in text_like if j not in covered]
    for j in missing:
        print(f"S missing {A[j][0]} {A[j][1]} {A[j][2]} {A[j][3]}", file=out)
    n = len(text_like)
    res = dict(comps=n, missing=len(missing), invented=invented, holes=holes, rendered=rendered, lines=len(lv),
               lines_judged=judged, not_set=not_set, unplaced=unplaced)
    print(f"# page {pno}: scan route, {len(lv)} lines, {len(A)} ink components, {n} text-like", file=out)
    print("SCAN1 " + " ".join(f"{k} {v}" for k, v in sorted(res.items())) +
          (" residual %.6f" % ((len(missing) + invented + holes) / n) if n else " residual NOT_RUN"), file=out)
    print(BLIND, file=out)                           # G3
    print("STATUS OK" if n else "STATUS NO_INK", file=out)
    return res

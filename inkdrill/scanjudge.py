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
- B: the reading's lines set by the judge's own compile_group at 10 pt in Computer Modern, face commands dropped --
  a scan tells no font -- one snippet page per line, rendered at SNIP_DPI (600) and cut alike.
- Each line's rendered components are mapped onto the page's ink inside the line's box by extent (x and y scaled
  separately): word by word where the line has as many rendered words as page words, the whole line otherwise (changed
  2026-11-28: the scan floor, board 114: a reading equal to the reference, MathPix, scored 0.31-0.40 from the setting
  alone; 0.11-0.18 now). Then counted by component: MISSING, text-like page ink no rendered component covers (outside the boxes
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

import collections
import glob
import json
import math
import os
import re
import subprocess
import sys

from . import nest, pnmio

DPI = 300
#: the reading's snippet is rendered at twice the page's resolution: at 300 dpi the grey cut at 128 closes the small
#: eyes of 10 pt CM (e, a), so a correct e over the scan's open one was a HOLES finding (board 114 US6100897A p18:
#: 423 of MathPix's 624 holes were page 1 / rendered 0, nearly all e). The map onto the page scales extents anyway;
#: 600 and 900 dpi measure alike (0.1147 / 0.1126 on US61)
SNIP_DPI = 600
#: word gap: components further apart than this share of the line's median component height start a new word
WORD_GAP = 0.35
BLIND = ("BLIND a substitution with the same topology (t/n, n/u, l/1) is not seen; a scan residual is not comparable "
         "with a glyph-route residual")


def components(pgm: str, dpi: int = DPI) -> list:
    """a rendered page's ink components: (x0, y0, x1, y1, holes), half-open, at least 4 px at 300 dpi"""
    n = nest.nest(pnmio.load_mask(pgm, dpi=dpi))
    least = 4 * (dpi / 300) ** 2                  # 4 px at 300 dpi, whatever the resolution
    return [(r.x0, r.y0, r.x1 + 1, r.y1 + 1, len(n.holes_of(r.id)))
            for r in n.regions.values() if r.kind is nest.Kind.INK and r.area >= least]


def render(pdf: str, prefix: str, first: int | None = None, dpi: int = DPI) -> list:
    args = ["pdftoppm", "-gray", "-r", str(dpi)] + (["-f", str(first), "-l", str(first)] if first else [])
    subprocess.run(args + [pdf, prefix], check=True, capture_output=True)
    return sorted(glob.glob(prefix + "-*.pgm"), key=lambda p: int(re.search(r"-(\d+)\.pgm$", p)[1]))


FACE = re.compile(r"\\text(?:it|bf|sl|sc|sf|tt)\{")


def unface(t: str) -> str:
    """a line's LaTeX with its face commands dropped and their content kept: a scan tells no font, and the snippet's
    CM italic over a scan's own italic is another topology than its roman (board 114 lib:fe56cd934435 p56: Gemma4's
    correct \\textit caption, 43 holes findings where MathPix's roman reading of it had 16)"""
    while True:
        m = FACE.search(t)
        if not m:
            return t
        i, depth = m.end(), 1
        while i < len(t) and depth:
            if t[i - 1] != "\\":
                depth += {"{": 1, "}": -1}.get(t[i], 0)
            i += 1
        t = t[:m.start()] + t[m.end():i - 1] + t[i:]


def words(cs: list) -> list:
    """components (x0, y0, x1, y1, ...) in x order, split into words where the gap to the word so far exceeds the line's
    word gap: WORD_GAP of the median height, or the line's own split between letter and word gaps (Otsu's threshold over
    its gaps) where that is larger. A scan whose letters stand further apart than 0.35 of their height was cut into its
    letters (cam-cocuma87 p8 L20: 56 page words against 14 rendered)"""
    if not cs:
        return []
    cs = sorted(cs, key=lambda c: c[0])
    gap = WORD_GAP * sorted(c[3] - c[1] for c in cs)[len(cs) // 2]
    gaps, right = [], cs[0][2]
    for c in cs[1:]:
        gaps.append(c[0] - right)
        right = max(right, c[2])
    gap = max(gap, otsu(gaps))
    out = []
    for c in cs:
        if out and c[0] - max(x[2] for x in out[-1]) <= gap:
            out[-1].append(c)
        else:
            out.append([c])
    return out


def otsu(gs: list) -> float:
    """the threshold between a line's letter gaps and its word gaps: the cut of the sorted gaps with the largest
    between-class variance (Otsu); -inf with fewer than 4 gaps"""
    gs = sorted(gs)
    if len(gs) < 4:
        return float("-inf")
    n, tot = len(gs), sum(gs)
    best, cut, acc = -1.0, float("-inf"), 0.0
    for k in range(1, n):
        acc += gs[k - 1]
        m0, m1 = acc / k, (tot - acc) / (n - k)
        v = k * (n - k) * (m0 - m1) ** 2
        if v > best and gs[k] > gs[k - 1]:
            best, cut = v, (gs[k - 1] + gs[k]) / 2
    return cut


#: the alignment's costs: a pair of words differs by |ln| of their width ratio; joining two words on one side (the gap
#: rule merged or split them) adds JOIN; a word left without a partner costs SKIP (a width ratio of e, 2.7)
JOIN = 0.3
PAGE_JOIN = 12
SKIP = 1.0


def align(wb: list, wa: list, A: list) -> tuple:
    """the line's rendered words wb (component lists) aligned with its page words wa (index lists into A) by their
    widths, in order: a dynamic programme over pairings 1:1, 2:1 and 1:n (n <= PAGE_JOIN) and words left alone -- the cheap form of
    Document Image Decoding's Viterbi alignment of a transcription with the image. Returns the aligned groups as
    (rendered components, page indices) and the rendered components of words left alone (invented); a page word left
    alone stays missing.

    A line whose word counts differed was mapped whole by extent, and the map drifted along it: a correct reading of a
    hard line cost more than leaving it out (alex1 p21: MathPix's correct "(9.16), (9.22), (9.24) and (9.26)" line, 29
    findings, where a reading without it paid its missing ink only)."""
    if not wb or not wa:
        return [], [c for w in wb for c in w]
    ext = lambda cs: (min(c[0] for c in cs), min(c[1] for c in cs), max(c[2] for c in cs), max(c[3] for c in cs))
    pb = [ext(w) for w in wb]
    pa = [ext([A[j] for j in w]) for w in wa]
    # rendered widths in page units by the two lines' extents, not their heights (a face wider than CM, or a box with
    # another share of ascenders, made every pair look unequal) nor their summed word widths (gaps left out: a page
    # word cut into letters has far less of it than its extent)
    s = (max(e[2] for e in pa) - min(e[0] for e in pa)) / max(max(e[2] for e in pb) - min(e[0] for e in pb), 1)
    wid_b = lambda i, n: max((pb[i + n - 1][2] - pb[i][0]) * s, 1.0)
    wid_a = lambda j, n: max(pa[j + n - 1][2] - pa[j][0], 1.0)
    nb, na = len(wb), len(wa)
    mw = max(sorted(wid_b(i, 1) for i in range(nb))[nb // 2], 1.0)   # the line's median rendered word, page units
    INF = float("inf")
    cost = [[INF] * (na + 1) for _ in range(nb + 1)]
    back = [[None] * (na + 1) for _ in range(nb + 1)]
    cost[0][0] = 0.0
    for i in range(nb + 1):
        for j in range(na + 1):
            c0 = cost[i][j]
            if c0 == INF:
                continue
            # every cost in one unit, width in median-word widths: a word left alone costs its own width, a pairing the
            # difference of its two widths. Absorbing a dropped word into a neighbour so costs at least that word's ink,
            # as leaving it alone does; per piece, a word cut into letters cost one SKIP per letter and was absorbed
            # instead (lib:cf33914ffd33 p6, lib:d8a2bb86b4e5 p2: a reading with words dropped scored better)
            steps = []
            if j < na:
                steps.append((0, 1, SKIP * wid_a(j, 1) / mw))
            if i < nb:
                steps.append((1, 0, SKIP * wid_b(i, 1) / mw))
            # a rendered word may take up to PAGE_JOIN page words: a scan whose letters stand apart is cut into far more
            # "words" than its reading has (cam-cocuma87 p8 L6: 60 page words against 13), and with 1:2 at most half
            # its ink stayed in no group, missing
            for di, dj in [(1, 1), (2, 1)] + [(1, n) for n in range(2, PAGE_JOIN + 1)]:
                if i + di <= nb and j + dj <= na:
                    steps.append((di, dj, abs(wid_b(i, di) - wid_a(j, dj)) / mw + (JOIN if di + dj > 2 else 0)))
            for di, dj, c in steps:
                if i + di <= nb and j + dj <= na and c0 + c < cost[i + di][j + dj]:
                    cost[i + di][j + dj] = c0 + c
                    back[i + di][j + dj] = (di, dj)
    spans, lone, i, j = [], [], nb, na
    while i or j:
        di, dj = back[i][j]
        i, j = i - di, j - dj
        if di and dj:
            spans.append(([c for w in wb[i:i + di] for c in w], [x for w in wa[j:j + dj] for x in w]))
        elif di:
            lone += wb[i]
    return spans[::-1], lone


def mapped(A: list, js: list, bs: list):
    """the rendered components bs mapped onto the page components A[js], each as ("invented", box, []) with no page ink
    under it, ("holes", its holes, [j]) over one page component whose hole count differs, or ("ok", box, under).
    As many on each side: the k-th with the k-th in x order. A map by extent drifted by part of a letter across a word
    in a face wider than CM's, and a page e paired with a rendered h (holes 1 / 0, then 0 / 1 beside it: alex1 p21's
    MathPix 0.179 -> 0.070). Otherwise by extent, x and y scaled separately"""
    if len(js) == len(bs) > 1:
        for c, j in zip(sorted(bs, key=lambda c: c[0]), sorted(js, key=lambda j: A[j][0])):
            yield ("holes", c[4], [j]) if A[j][4] != c[4] else ("ok", (A[j][0], A[j][1], A[j][2], A[j][3]), [j])
        return
    ax0, ay0 = min(A[j][0] for j in js), min(A[j][1] for j in js)
    ax1, ay1 = max(A[j][2] for j in js), max(A[j][3] for j in js)
    bx0, by0 = min(c[0] for c in bs), min(c[1] for c in bs)
    bx1, by1 = max(c[2] for c in bs), max(c[3] for c in bs)
    sx, sy = (ax1 - ax0) / max(bx1 - bx0, 1), (ay1 - ay0) / max(by1 - by0, 1)
    for c in bs:
        q = (ax0 + (c[0] - bx0) * sx - 2, ay0 + (c[1] - by0) * sy - 2, ax0 + (c[2] - bx0) * sx + 2,
             ay0 + (c[3] - by0) * sy + 2)
        under = [j for j in js if q[0] <= (A[j][0] + A[j][2]) / 2 < q[2] and q[1] <= (A[j][1] + A[j][3]) / 2 < q[3]]
        if not under:
            yield "invented", q, []
        elif len(under) == 1 and A[under[0]][4] != c[4]:
            yield "holes", c[4], under
        else:
            yield "ok", q, under


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
    # the population, from the page alone: every ink component up to text size, small print and specks included.
    # It was text-like heights (0.3-2.5 of the median) plus the ink under the reading's own lines, so it moved with
    # the reading: a line taken out took its small print out of the count, and leaving lines out paid
    # (lib:d8a2bb86b4e5 p2; the per-line cap below holds only over a population no reading can change)
    # its size bound from the upper half of the heights: on a contents page the leader dots outnumber the letters, the
    # median was a dot, and the letters themselves fell outside 2.5 of it (lib:d8a2bb86b4e5 p2)
    top = hs[len(hs) // 2:]
    big = top[len(top) // 2] if top else 1
    text_like = [j for j, c in enumerate(A) if c[3] - c[1] <= 2.5 * big and c[2] - c[0] <= 5 * big]
    texts = [unface(J.line_tex(ln)) for ln in lv]
    failed, pages, bo, unset = {}, [], 0, set()
    if texts:
        snip, got = J.compile_group([(texts, [10.0] * len(texts))], "", wd)
        failed, dropped, bo = got[0]
        unset = {i for i, t in enumerate(texts) if any(ch in t for ch in dropped)}
        if os.path.exists(snip):                     # G2: no document, no line
            pages = render(snip, os.path.join(wd, "scan-snip"), dpi=SNIP_DPI)
    covered, invented, holes, rendered, judged, not_set = set(), 0, 0, 0, 0, 0
    beyond = set()                                   # page ink under a line whose characters TeX cannot set
    unplaced = 0
    own, found = {}, collections.Counter()           # line -> the page ink under its box; line -> its findings
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
        if i not in unset:
            own[i] = under_box
        if i in unset:                               # a script pdflatex drops (CJK): unmeasurable, not missing;
            print(f"N L{i} UNSETTABLE", file=out)    # it was missing, so a reading garbled into Latin scored better
            beyond.update(under_box)                 # (CN208060706U p3: 5 % of letters swapped, 0.386 -> 0.290)
            continue
        if i in failed or bo + i >= len(pages):
            print(f"N L{i} NOT_SET", file=out)
            not_set += 1
            continue
        if not under_box:
            continue
        B = components(pages[bo + i], SNIP_DPI)
        if not B:
            continue
        judged += 1
        rendered += len(B)
        # word by word where the line's rendered words and the page's words under its box are as many; the whole
        # line otherwise. One map for the line let 10 pt CM's widths drift against the scan's along it: a correct
        # word's components landed between the page's (board 114 US6100897A p18, MathPix: invented 539 -> 310)
        wb = words(B)
        wa = words([A[j] + (j,) for j in under_box])
        spans, lone = align(wb, [[c[5] for c in w] for w in wa], A)
        for c in lone:                               # a rendered word the alignment gave no page word: invented
            invented += 1
            found[i] += 1
            print(f"S L{i} invented-word {c[0]} {c[1]} {c[2]} {c[3]}", file=out)
        for bs, js in spans:
            for kind, q, j in mapped(A, js, bs):
                if kind == "invented":
                    invented += 1
                    found[i] += 1
                    print(f"S L{i} invented {round(q[0])} {round(q[1])} {round(q[2])} {round(q[3])}", file=out)
                    continue
                covered.update(j)
                if kind == "holes":
                    holes += 1
                    found[i] += 1
                    print(f"S L{i} holes {A[j[0]][4]} {q} {A[j[0]][0]} {A[j[0]][1]}", file=out)
    text_like = sorted(set(text_like) - beyond)
    missing = [j for j in text_like if j not in covered]
    mostly_unset = len(beyond) > len(text_like)       # more ink TeX cannot set than ink it can: not measured
    for j in missing:
        print(f"S missing {A[j][0]} {A[j][1]} {A[j][2]} {A[j][3]}", file=out)
    n = len(text_like)
    # the cap: a line costs at most what its absence would, the page ink it owns (each component owned by the first
    # line whose box holds it). A correct line the instrument maps badly cost more than leaving it out, so omission
    # paid (alex1 p21: MathPix's correct "(9.16), (9.22), (9.24) and (9.26)" line, 29 findings). A line at the cap
    # is one the instrument cannot tell from its absence: it is counted and named (`capped`), never hidden
    pop, owner = set(text_like), {}
    for i, js in own.items():
        for j in js:
            if j in pop:
                owner.setdefault(j, i)
    held = collections.Counter(owner.values())
    lost = collections.Counter(owner[j] for j in missing if j in owner)
    cost, capped, capped_comps = len(missing) - sum(lost.values()), 0, 0
    for i in set(held) | set(found):
        f = found[i] + lost[i]
        if held[i] and f >= held[i]:
            capped += 1
            capped_comps += held[i]
            print(f"N L{i} CAPPED {f} {held[i]}", file=out)
        cost += min(f, held[i]) if held[i] else f
    res = dict(comps=n, missing=len(missing), invented=invented, holes=holes, rendered=rendered, lines=len(lv),
               lines_judged=judged, not_set=not_set, unplaced=unplaced, unsettable=len(unset), capped=capped,
               capped_comps=capped_comps)
    print(f"# page {pno}: scan route, {len(lv)} lines, {len(A)} ink components, {n} text-like", file=out)
    print("SCAN1 " + " ".join(f"{k} {v}" for k, v in sorted(res.items())) +
          (" residual %.6f" % (cost / n) if n and judged and not mostly_unset
           else " residual NOT_RUN"),
          file=out)
    print(BLIND, file=out)                           # G3
    # a page whose every line TeX cannot set (CN208060706U p3: 37 of 39 lines Chinese) is not measured at all
    print("STATUS OK" if n and judged and not mostly_unset else "STATUS UNSETTABLE" if unset else "STATUS NO_INK",
          file=out)
    return res

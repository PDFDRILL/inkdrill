"""`inkcmp-judge --rects RECTS.tsv --work DIR`: one residual per rectangle of a page (T-0158).

Input, one row per rectangle, tab-separated, `#` lines skipped:

    source  doc  page  obj_id  x0  y0  x1  y1  latex  pdf

The box is in PDF points in the page's CropBox frame, y down from the top (MathPix's and the board's frame). `latex`
is the reader's LaTeX for the rectangle. Without math delimiters it is set as display maths (`\\[ ... \\]`), with
them as written. The LaTeX is one line: an exporter writes a tab or a newline in it as a space (no escapes are
decoded: LaTeX is full of `\\t`, `\\to`, `\\theta`).

Each rectangle is a one-line reading of its page: a lines.json holding one `math` line whose region is the rectangle.
`judge_batch` judges it on the glyph route, the board's judge: the page glyphs whose centre lies in the rectangle
are paired with the rendered LaTeX's glyphs, and `inkcmp glyphs`/`inkcmp rules` (the C core) score each pair and each
rule. A page rule counts when its centre lies in the rectangle; the page's other rules are no line's (`rules_inside`:
alone on its page, the line would otherwise take every rule nearest to it, and one over any glyph of it). All rows that share a preamble are set in one pdflatex run.

The residual is the board's, over one rectangle:

    (glyph findings + unpaired page glyphs + unpaired rendered glyphs + rules missing + rules invented)
      / (page glyphs in the rectangle + page rules in it)

A page glyph is in the rectangle when its centre is (the judge's line ownership). A pair the judge could not score (no
outline on the reading's side) counts as unpaired, as on the board.

A style finding (bold or italic lost, sa != sb) is not in it, as on the board. A glyph in a face TeX cannot set
(unmeasurable) has no shape finding and stays in the denominator (the board's rule as of 2026-11-22, CONTRACT). A rectangle whose LaTeX does not compile
is status NOT_COMPILED with no residual; a rectangle holding no page glyph is NO_TEXT.

Output: <DIR>/rects.out.tsv, the input's first nine columns with the box, then status, residual, the counts, the crop
(a 200-dpi PNG of the page's rectangle, <DIR>/crops/<n>.png) and the row's judge output (<DIR>/r<n>/judge.out).
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from . import judge

COLS = ("source", "doc", "page", "obj_id", "x0", "y0", "x1", "y1", "latex", "pdf")
OUT_COLS = ("source", "doc", "page", "obj_id", "x0", "y0", "x1", "y1", "status", "residual", "glyph", "unpaired_A",
            "unpaired_B", "rules_missing", "rules_invented", "a_glyphs", "rules_a", "unmeasurable", "style", "crop",
            "judge_out")
DELIMITED = re.compile(r"^\s*(?:\$|\\\[|\\\(|\\begin\{(?:equation|align|gather|multline|eqnarray|displaymath)\*?\})")
CROP_DPI = 200


def read_rows(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            f = line.split("\t")
            if f[0] == "source" and f[3] == "obj_id":
                continue                                   # a header row
            if len(f) != len(COLS):
                raise ValueError(f"{path}:{n}: {len(f)} columns, want {len(COLS)} ({' '.join(COLS)})")
            rows.append(dict(zip(COLS, f)))
    return rows


def reading(r: dict) -> dict:
    """the one-line lines.json of a rectangle: its page at 400 dpi, the rectangle as the line's region"""
    pno = int(r["page"])
    w, h = judge.page_size(r["pdf"], pno)
    s = judge.S
    x0, y0, x1, y1 = (float(r[k]) for k in ("x0", "y0", "x1", "y1"))
    tex = r["latex"].strip()
    text = tex if DELIMITED.match(tex) else "\\[\n" + tex + "\n\\]"
    return {"pages": [{"page": pno, "page_width": round(w * s), "page_height": round(h * s), "lines": [
        {"type": "math", "id": r["obj_id"], "text": text,
         "region": {"top_left_x": x0 * s, "top_left_y": y0 * s, "width": (x1 - x0) * s, "height": (y1 - y0) * s}}]}]}


def counts(out: str) -> dict:
    """JUDGE1's counts and STATUS from one row's judge.out"""
    c, status = {}, ""
    for l in out.splitlines():
        if l.startswith("JUDGE1 "):
            p = l.split()[1:]
            c = {p[i]: int(p[i + 1]) for i in range(0, len(p) - 1, 2)}
        elif l.startswith("STATUS "):
            status = l.split()[1]
    return c, status


def rect_glyphs(c: dict) -> int:
    """the page glyphs in the rectangle: those of its one line, paired (judged or without an outline on the reading's
    side) or not. JUDGE1's a_glyphs is the whole page's."""
    return c.get("glyphs", 0) + c.get("no_outline", 0) + c.get("unpaired_A", 0)


def residual(c: dict, status: str) -> tuple[str, str]:
    """(status, residual) by the board's definition (module docstring)"""
    if not c:
        return "ERROR", ""
    if c.get("not_compiled"):
        return "NOT_COMPILED", ""
    page = rect_glyphs(c)
    den = page + c.get("rules_a", 0)               # unmeasurable glyphs stay: their presence is measured
    if not page or den <= 0:
        return "NO_TEXT", ""
    num = (c.get("glyph", 0) + page - c.get("glyphs", 0) + c.get("unpaired_B", 0)
           + c.get("rules_missing", 0) + c.get("rules_invented", 0))
    return status or "OK", "%.4f" % (num / den)


def crop(r: dict, path: str) -> None:
    x0, y0, x1, y1 = (float(r[k]) for k in ("x0", "y0", "x1", "y1"))
    k = CROP_DPI / 72
    X, Y = int(x0 * k), int(y0 * k)
    subprocess.run(["pdftoppm", "-png", "-singlefile", "-r", str(CROP_DPI), "-f", r["page"], "-l", r["page"],
                    "-x", str(X), "-y", str(Y), "-W", str(max(1, int(x1 * k + 1) - X)),
                    "-H", str(max(1, int(y1 * k + 1) - Y)), r["pdf"], path[:-4]], check=True, capture_output=True)


def judge_rects(rects: str, wd: str) -> list[dict]:
    rows = read_rows(rects)
    os.makedirs(os.path.join(wd, "crops"), exist_ok=True)
    jobs, bad = [], {}
    for n, r in enumerate(rows):
        rw = os.path.join(wd, "r%d" % n)
        os.makedirs(rw, exist_ok=True)
        lj = os.path.join(rw, "reading.lines.json")
        try:
            doc = reading(r)
        except (OSError, ValueError, subprocess.CalledProcessError, TypeError) as e:   # its page cannot be read: this
            bad[n] = "%s: %s" % (type(e).__name__, e)                                   # row fails, the others run
            continue
        with open(lj, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        jobs.append((r["pdf"], int(r["page"]), lj, rw))
    if jobs:
        judge.judge_batch(jobs, route="glyph", bwd=os.path.join(wd, "batch"), rules_inside=True)
    out = []
    for n, r in enumerate(rows):
        jo = os.path.join(wd, "r%d" % n, "judge.out")
        if n in bad:
            with open(jo, "w", encoding="utf-8") as fh:
                fh.write("ERROR " + bad[n] + "\n")
        c, status = counts(open(jo, encoding="utf-8", errors="replace").read() if os.path.exists(jo) else "")
        st, res = residual(c, status)
        cp = os.path.join(wd, "crops", "%d.png" % n)
        try:
            crop(r, cp)
        except (OSError, subprocess.CalledProcessError):
            cp = ""
        out.append(dict({k: r[k] for k in COLS[:8]}, status=st, residual=res, glyph=c.get("glyph", ""),
                        unpaired_A=c.get("unpaired_A", ""), unpaired_B=c.get("unpaired_B", ""),
                        rules_missing=c.get("rules_missing", ""), rules_invented=c.get("rules_invented", ""),
                        a_glyphs=rect_glyphs(c) if c else "", rules_a=c.get("rules_a", ""),
                        unmeasurable=c.get("unmeasurable", ""), style=c.get("style", ""), crop=cp, judge_out=jo))
    with open(os.path.join(wd, "rects.out.tsv"), "w", encoding="utf-8") as fh:
        fh.write("\t".join(OUT_COLS) + "\n")
        for o in out:
            fh.write("\t".join(str(o[k]) for k in OUT_COLS) + "\n")
    return out

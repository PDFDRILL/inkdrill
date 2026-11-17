"""inkcmp judge, born-digital branch: is a lines.json reading of a PDF page the same ink as the page?

    inkcmp-judge <original.pdf> <page> <lines.json> [--work DIR]

Contract: c/inkcmp/CONTRACT.md units 4-5 (in-place registration, phase-exact per glyph). Steps:

1. Each lines.json leaf line (text, no children_ids, not page_info/diagram) becomes one page of a
   `standalone` document (env `pline`) set in the page's own font family (`font_preamble`) at the size
   the original uses there (mutool stext font size, bp -> TeX pt by 72.27/72). One pdflatex run.
2. Glyph origins come from the content streams (mutool stext `<char x y>`) of both PDFs. Within a line,
   the glyphs are paired in x order by difflib (equal runs, and replace runs of equal length).
3. Both sides are rendered unhinted by poppler (`-aa no -aaVector no -hide-annotations`) at UP x 400 dpi,
   with A cropped per line. They are point-sampled onto A's 400-dpi grid at each glyph's fractional origin,
   with the pixel-centre offset OX = OY = -1/16.
4. Per glyph, over its quad box + 2 px: XOR at ink < 154. The score is the "solid" residual, meaning XOR
   pixels whose 2x2 block is all residual. A glyph is flagged at solid >= THRESHOLD (7, frozen 2026-11-16
   before the fresh-page test).

Output, one line per paired glyph:
    G L<i> <A char> <B char> <equal> <xor> <solid> <largest piece> <flag> <A style> <B style> <A font>
Then `U L<i> A|B <char>` for unpaired glyphs, `N L<i> NOT_COMPILED <LaTeX error>` for a line pdflatex could not set
(the other lines still set and pair), a summary line `JUDGE1 lines .. glyphs .. flagged .. not_compiled ..
a_glyphs .. a_unpaired .. b_unpaired ..` (a_glyphs: all glyphs of the original page; a_unpaired: those that paired
with nothing; b_unpaired: rendered glyphs with no partner; the board's ink residual is
(findings + a_unpaired + b_unpaired) / a_glyphs), and
`STATUS OK|NOT_COMPILED|NO_TEXT|FONT_UNMAPPED`: a page with 0 paired glyphs has no residual. FONT_UNMAPPED: most of
the page's text glyphs are in a family TeX cannot set (e.g. a commercial font), so same-font XOR does not apply.
Line text goes through `latex_text` first: characters pdflatex cannot set (U+2212, Greek, arrows, ...) become LaTeX,
compatibility forms (ligatures, full-width) are NFKC-normalised. The engine stays pdflatex: the phase-exact scoring
was validated on its Type 1 fonts.
A style is x (maths font), m (mono), b (bold), i (italic) or `-`. A flag where the two styles differ is a
style finding (MathPix lost italic or bold). One where they are equal is a glyph finding.

Needs pdflatex, mutool and pdftoppm on PATH. Cost: about 22 s per page (8x crops dominate,
CONTRACT unit 5).
"""
from __future__ import annotations

import collections
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata

DPI, UP = 400, 8
S = DPI / 72
OX = OY = -0.0625
INK = 154
THRESHOLD = 7
CH = re.compile(r'<font name="([^"]*)"(?: size="([\d.]+)")?|<char c="(.*?)" quad="([^"]+)" x="([\d.\-]+)" y="([\d.\-]+)"')
PGS = re.compile(r"<page [^>]*>(.*?)</page>", re.S)
MATHFONT = r"CMSY|CMMI|CMEX|MSBM|MSAM|EU[RFSE]"
FAMILIES = [  # (regex on the PDF font name, preamble, family)
    (r"NimbusRom|Times|TeXGyreTermes|ptmr|utmr", r"\usepackage{times}", "times"),
    (r"NimbusSan|Helvetica|Arial|TeXGyreHeros|phvr|uhvr", r"\usepackage{helvet}\renewcommand{\familydefault}{\sfdefault}",
     "helvetica"),
    (r"Palatino|Palladio|TeXGyrePagella|pplr|uplr", r"\renewcommand{\rmdefault}{ppl}", "palatino"),
    (r"^CM|^LM|SFRM|LMRoman", "", "cm"),
    (r"LinLibertine|LinBiolinum|Libertine", r"\usepackage{libertine}", "libertine"),
]
MATHFAM = [(r"EURM|EUSM|EUFM|EUEX", r"\usepackage{eulervm}"), (r"NewTXMI|ntxmi|txmi|TXMI", r"\usepackage{newtxmath}"),
           (r"zplm|NewPXMI|pxmi", r"\usepackage{newpxmath}")]


def style(font: str) -> str:
    f = font.lower()
    lib = re.search(r"lin(?:libertine|biolinum)([a-z0-9]*)$", f)       # LinLibertineTI: T (Type 1), B bold, I italic
    if lib:
        suf = lib.group(1)
        return ("b" if "b" in suf else "") + ("i" if "i" in suf else "")
    return (("x" if re.search(r"eurm|eusm|eufm|euex|cmmi|cmsy|cmex|msbm|msam", f) else "")
            + ("m" if re.search(r"mono|monl|courier|cmtt|inconsolata|typewriter|tt\d", f) else "")
            + ("b" if re.search(r"bold|medi|bx|black|semibold|demi", f) else "")
            + ("i" if re.search(r"ital|obli|ti\d|mi\d|slant|it$", f) else ""))


def stext(pdf: str, pages: str) -> str:
    return subprocess.run(["mutool", "draw", "-F", "stext", "-o", "-", pdf, pages],
                          capture_output=True, text=True, check=True).stdout


def chars(pdf: str, first: int, last: int) -> list[list[tuple]]:
    """per page: (char, origin x, origin y, quad x0, y0, x1, y1, font, size bp)"""
    out = []
    for body in PGS.findall(stext(pdf, f"{first}-{last}")):
        cs, font, size = [], "", 0.0
        for fn, fs, c, q, ox, oy in CH.findall(body):
            if fn:
                font, size = fn, float(fs or 0)
                continue
            if c.strip() == "":
                continue
            qv = [float(v) for v in q.split()]
            cs.append((c, float(ox), float(oy), min(qv[0::2]), min(qv[1::2]), max(qv[0::2]), max(qv[1::2]), font, size))
        out.append(cs)
    return out


def font_preamble(pdf: str, page: int) -> tuple[str, str]:
    """the page's text family, chosen by glyph count, plus any maths family it uses; "unmapped" when most text
    glyphs are in fonts no FAMILIES entry sets (the judge then reports STATUS FONT_UNMAPPED, no residual)"""
    glyphs, font = collections.Counter(), ""
    for fn, c in re.findall(r'<font name="([^"]*)"|<char c="(.*?)"', stext(pdf, str(page))):
        if fn:
            font = fn.split("+")[-1]
        elif c.strip():
            glyphs[font] += 1
    fams, unmapped = collections.Counter(), 0
    for n, cnt in glyphs.items():
        if re.search(MATHFONT + r"|Inconsolata|Courier|CMTT|Mono|Symbol|Dingbat", n):
            continue
        hit = next((f for r, _, f in FAMILIES if re.search(r, n)), None)
        if hit:
            fams[hit] += cnt
        else:
            unmapped += cnt
    if unmapped > sum(fams.values()):     # most text glyphs are in a family TeX cannot set: no same-font XOR
        return "unmapped", ""
    fam = fams.most_common(1)[0][0] if fams else "cm"
    pre = [next(p for _, p, f in FAMILIES if f == fam)]
    pre += [p for r, p in MATHFAM if any(re.search(r, n) for n in glyphs)]
    return fam, "\n".join(x for x in pre if x)


def pgm(pdf: str, page: int, dpi: int, crop: tuple | None = None) -> tuple[int, int, bytes]:
    """poppler (FreeType, unhinted), anti-aliasing off, grey"""
    cmd = ["pdftoppm", "-r", str(dpi), "-gray", "-aa", "no", "-aaVector", "no", "-hide-annotations",
           "-f", str(page), "-l", str(page)]
    if crop:
        cmd += ["-x", str(crop[0]), "-y", str(crop[1]), "-W", str(crop[2]), "-H", str(crop[3])]
    out = subprocess.run(cmd + ["-singlefile", pdf], capture_output=True, check=True).stdout
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", out)
    return int(m[1]), int(m[2]), out[m.end():]


def page_size(pdf: str, page: int) -> tuple[float, float]:
    """CropBox width and height in pt (the frame poppler renders and MathPix measures)"""
    x = subprocess.run(["pdfinfo", "-box", "-f", str(page), "-l", str(page), pdf], capture_output=True, text=True,
                       check=True).stdout
    m = re.search(rf"Page\s+{page} CropBox:\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)", x)
    return float(m[3]) - float(m[1]), float(m[4]) - float(m[2])


def score(diff: set) -> tuple[int, int, int]:
    """(xor pixels, solid pixels, largest 8-connected piece)"""
    par = {p: p for p in diff}

    def f(p):
        while par[p] != p:
            par[p] = par[par[p]]
            p = par[p]
        return p
    for (x, y) in diff:
        for d in ((1, 0), (0, 1), (1, 1), (-1, 1)):
            q = (x + d[0], y + d[1])
            if q in diff:
                par[f((x, y))] = f(q)
    piece = max(collections.Counter(f(p) for p in diff).values(), default=0)
    solid = sum(1 for (x, y) in diff if (x + 1, y) in diff and (x, y + 1) in diff and (x + 1, y + 1) in diff)
    return len(diff), solid, piece


def leaves(page: dict) -> list[dict]:
    return [ln for ln in page["lines"]
            if ln.get("text") and not ln.get("children_ids") and ln["type"] not in ("page_info", "diagram")]

#: characters pdflatex (utf8, OT1/T1) cannot set, as LaTeX: (in text mode, in maths mode). Found in the 30 board
#: readings (2026-11-17): U+2212 alone dropped whole pages. Compatibility forms (ligatures, full-width) go through
#: NFKC instead; scripts pdflatex has no fonts for (Cyrillic, Thai, CJK, ...) are left alone and fail per line.
UNI_TEX = {
    "\u2212": ("$-$", "-"), "\u00d7": ("$\\times$", "\\times"), "\u2192": ("$\\rightarrow$", "\\rightarrow"),
    "\u2190": ("$\\leftarrow$", "\\leftarrow"), "\u2264": ("$\\leq$", "\\leq"), "\u2265": ("$\\geq$", "\\geq"),
    "\u2260": ("$\\neq$", "\\neq"), "\u00b1": ("$\\pm$", "\\pm"), "\u2248": ("$\\approx$", "\\approx"),
    "\u221e": ("$\\infty$", "\\infty"), "\u2208": ("$\\in$", "\\in"), "\u2211": ("$\\sum$", "\\sum"),
    "\u2202": ("$\\partial$", "\\partial"), "\u00b7": ("$\\cdot$", "\\cdot"), "\u2026": ("\\dots{}", "\\dots"),
    "\u2022": ("\\textbullet{}", "\\bullet"), "\u20ac": ("\\texteuro{}", "\\text{\\texteuro}"),
    "\u2122": ("\\texttrademark{}", "\\text{\\texttrademark}"), "\u00ae": ("\\textregistered{}", "\\text{\\textregistered}"),
    "\u00a7": ("\\S{}", "\\S"), "\u02c6": ("\\^{}", "\\hat{}"),
}
GREEK = {"alpha": "\u03b1", "beta": "\u03b2", "gamma": "\u03b3", "delta": "\u03b4", "epsilon": "\u03b5",
         "zeta": "\u03b6", "eta": "\u03b7", "theta": "\u03b8", "iota": "\u03b9", "kappa": "\u03ba", "lambda": "\u03bb",
         "mu": "\u03bc", "nu": "\u03bd", "xi": "\u03be", "pi": "\u03c0", "rho": "\u03c1", "sigma": "\u03c3",
         "tau": "\u03c4", "upsilon": "\u03c5", "phi": "\u03c6", "chi": "\u03c7", "psi": "\u03c8", "omega": "\u03c9",
         "Gamma": "\u0393", "Delta": "\u0394", "Theta": "\u0398", "Lambda": "\u039b", "Xi": "\u039e", "Pi": "\u03a0",
         "Sigma": "\u03a3", "Phi": "\u03a6", "Psi": "\u03a8", "Omega": "\u03a9"}
for _n, _c in GREEK.items():
    UNI_TEX[_c] = ("$\\%s$" % _n, "\\%s" % _n)
UNI_TEX["\u03bf"] = ("o", "o")                       # omicron has no command: it is an o


def latex_text(s: str) -> str:
    """A reading's line as pdflatex input: raw % and # (and & outside maths) escaped, mapped characters (maths mode
    tracked through $, \\( \\), \\[ \\]), compatibility forms through NFKC, everything else unchanged."""
    s = "".join(unicodedata.normalize("NFKC", ch)              # compatibility forms first, so a full-width ％
                if ord(ch) > 126 and unicodedata.decomposition(ch).startswith(("<compat>", "<wide>", "<narrow>"))
                else ch for ch in s)                           # is escaped like any %
    out, math, i = [], False, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nx = s[i + 1]
            if nx in "([":
                math = True
            elif nx in ")]":
                math = False
            out.append(s[i:i + 2])
            i += 2
            continue
        if ch == "$":
            math = not math
            out.append(ch)
        elif ch in "%#" or (ch == "&" and not math):     # plain-text readers write these raw (MathPix escapes):
            out.append("\\" + ch)                       # a raw % comments out the line's \end{pline}
        elif ch in UNI_TEX:
            out.append(UNI_TEX[ch][1] + " " if math else UNI_TEX[ch][0])
        else:
            out.append(ch)
        i += 1
    return "".join(out)


LOGERR = re.compile(r"^! (.*)$")
LOGLINE = re.compile(r"^l\.(\d+)")
INPUTLINE = re.compile(r"on input line (\d+)")


def compile_lines(texts: list, sizes: list, pre: str, wd: str, passes: int = 8) -> tuple[str, dict]:
    """All lines in one pdflatex run (one page per line). A line whose source raises a LaTeX error is set aside
    (an empty page keeps the numbering) and the run is repeated; returns (pdf path, {line index: error})."""
    failed: dict = {}
    for _ in range(passes):
        body, starts, n = [], [], 4 + pre.count("\n") + 1     # documentclass, usepackage, pre, begin{document}
        for i, (t, sz) in enumerate(zip(texts, sizes)):
            src = "\\mbox{}" if i in failed else latex_text(t)
            line = "\\begin{pline}\\fontsize{%.4f}{%.4f}\\selectfont %s\\end{pline}" % (sz * 72.27 / 72,
                                                                                  sz * 72.27 / 72 * 1.2, src)
            starts.append(n)
            n += line.count("\n") + 1
            body.append(line)
        with open(os.path.join(wd, "lines.tex"), "w", encoding="utf-8") as fh:
            fh.write("\\documentclass[multi=pline,border=2pt,varwidth=60cm]{standalone}\n"
                     "\\usepackage{amsmath,amssymb,amsfonts}\n" + pre + "\n\\begin{document}\n" + "\n".join(body)
                     + "\n\\end{document}\n")
        subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "lines.tex"], cwd=wd,
                       capture_output=True)
        log = open(os.path.join(wd, "lines.log"), encoding="utf-8", errors="replace").read().splitlines()
        new, msg = {}, None
        for ln in log:
            m = LOGERR.match(ln)
            if m:
                msg = m.group(1)
                continue
            m = LOGLINE.match(ln)
            if m and msg is not None:
                tl = int(m.group(1))
                src = INPUTLINE.search(msg)          # "\begin{pline} on input line 220 ended by ...": the culprit
                if src:
                    tl = int(src.group(1))
                idx = max((j for j, st in enumerate(starts) if st <= tl), default=None)
                if idx is not None and idx not in failed:
                    new.setdefault(idx, msg)
                msg = None
        if not new:
            break
        failed.update(new)
    return os.path.join(wd, "lines.pdf"), failed


def judge(a_pdf: str, pno: int, lines_json: str, wd: str, out=sys.stdout) -> collections.Counter:
    with open(lines_json, encoding="utf-8") as fh:
        doc = json.load(fh)
    pg = next((p for p in doc["pages"] if p.get("page") == pno), None) or doc["pages"][pno - 1]
    pw_pt, _ = page_size(a_pdf, pno)
    k = pw_pt * S / pg["page_width"]                  # lines.json px -> 400-dpi px
    lv = leaves(pg)
    fam, pre = font_preamble(a_pdf, pno)
    ac = chars(a_pdf, pno, pno)[0]

    def box(ln):
        r = ln["region"]
        return (r["top_left_x"] * k, r["top_left_y"] * k, (r["top_left_x"] + r["width"]) * k,
                (r["top_left_y"] + r["height"]) * k)

    def inside(c, b):
        return b[0] <= (c[3] + c[5]) / 2 * S < b[2] and b[1] <= (c[4] + c[6]) / 2 * S < b[3]

    sizes = []
    for ln in lv:
        b = box(ln)
        cs = [c[8] for c in ac if inside(c, b) and not re.search(MATHFONT, c[7])]
        sizes.append(collections.Counter(round(x, 4) for x in cs).most_common(1)[0][0] if cs else 10.0)
    if fam == "unmapped":
        print(f"# page {pno}: family unmapped, {len(lv)} lines, A chars {len(ac)}", file=out)
        print(f"JUDGE1 a_glyphs {len(ac)} a_unpaired {len(ac)} b_unpaired 0 flagged 0 glyph 0 glyphs 0 lines 0 "
              f"not_compiled 0 style 0 unpaired_A 0 unpaired_B 0", file=out)
        print("STATUS FONT_UNMAPPED", file=out)
        return collections.Counter(lines=0, glyphs=0, flagged=0, style=0, glyph=0, not_compiled=0)
    bpdf, failed = compile_lines([ln["text"] for ln in lv], sizes, pre, wd)
    bc = chars(bpdf, 1, len(lv)) if os.path.exists(bpdf) else []
    res = collections.Counter(lines=0, glyphs=0, flagged=0, style=0, glyph=0, not_compiled=0, unpaired_A=0,
                              unpaired_B=0)
    b_total = 0                                     # rendered glyphs of the lines that typeset
    print(f"# page {pno}: family {fam}, {len(lv)} lines, A chars {len(ac)}, snippet pages {len(bc)}", file=out)
    if len(bc) != len(lv):            # the document itself did not come out page for page: no line is trusted
        failed = {i: failed.get(i, "no snippet page (document failed)") for i in range(len(lv))}
        bc = []
    for i, e in sorted(failed.items()):
        print(f"N L{i} NOT_COMPILED {e}", file=out)
    res["not_compiled"] = len(failed)
    # each original glyph belongs to ONE line: the smallest box that holds its centre. Where a reading's boxes
    # overlap, a glyph was paired once per box (board 2312.11532 p7: 3396 pairs of 3325 glyphs)
    boxes = [box(ln) for ln in lv]
    owner = {}
    for ci, c in enumerate(ac):
        best = None
        for li, b in enumerate(boxes):
            if inside(c, b):
                a = (b[2] - b[0]) * (b[3] - b[1])
                if best is None or a < best[0]:
                    best = (a, li)
        if best is not None:
            owner[ci] = best[1]
    for i, ln in enumerate(lv[:len(bc)]):
        if i in failed:
            continue
        b_total += len(bc[i])
        b = boxes[i]
        ca = sorted((c for ci, c in enumerate(ac) if owner.get(ci) == i), key=lambda c: c[1])
        cb = sorted(bc[i], key=lambda c: c[1])
        if not ca or not cb:
            res["lines_empty"] += 1
            continue
        pairs = []
        sm = difflib.SequenceMatcher(a=[c[0] for c in ca], b=[c[0] for c in cb], autojunk=False)
        for op, a0, a1, b0, b1 in sm.get_opcodes():
            if op == "equal" or (op == "replace" and a1 - a0 == b1 - b0):
                pairs += list(zip(ca[a0:a1], cb[b0:b1]))
            else:
                res["unpaired_A"] += a1 - a0
                res["unpaired_B"] += b1 - b0
                for c in ca[a0:a1]:
                    print(f"U L{i} A {c[0]}", file=out)
                for c in cb[b0:b1]:
                    print(f"U L{i} B {c[0]}", file=out)
        res["lines"] += 1
        pw, ph, pb = pgm(bpdf, i + 1, DPI * UP)
        cx0, cy0 = max(0, int(b[0]) * UP - 60), max(0, int(b[1]) * UP - 60)
        aw, ah, ab = pgm(a_pdf, pno, DPI * UP, (cx0, cy0, int(b[2] - b[0]) * UP + 120, int(b[3] - b[1]) * UP + 120))
        for (t, ax, ay, qx0, qy0, qx1, qy1, fa, _), (tb, bx, by, _, _, _, _, fb, _) in pairs:
            X, Y = ax * S + OX, ay * S + OY             # A's origin on the 400 grid
            u, v = bx * S * UP, by * S * UP             # B's origin on the UP x 400 grid
            diff = set()
            for y in range(int(qy0 * S) - 2, int(qy1 * S) + 3):
                ty = y * UP + UP // 2 - cy0
                sy = int(v + (y * UP + UP // 2 - Y * UP))
                for x in range(int(qx0 * S) - 2, int(qx1 * S) + 3):
                    tx = x * UP + UP // 2 - cx0
                    a_ink = 0 <= ty < ah and 0 <= tx < aw and ab[ty * aw + tx] < INK
                    sx = int(u + (x * UP + UP // 2 - X * UP))
                    b_ink = 0 <= sx < pw and 0 <= sy < ph and pb[sy * pw + sx] < INK
                    if a_ink != b_ink:
                        diff.add((x, y))
            xo, so, pc = score(diff)
            flag = so >= THRESHOLD
            sa, sb = style(fa) or "-", style(fb) or "-"
            print(f"G L{i} {t} {tb} {int(t == tb)} {xo} {so} {pc} {int(flag)} {sa} {sb} {fa}", file=out)
            res["glyphs"] += 1
            if flag:
                res["flagged"] += 1
                res["style" if sa != sb else "glyph"] += 1
    # page-wide counts for the board's ink residual (coordinator, 2026-11-17): every original glyph that paired
    # with nothing (outside any line, in an empty or uncompiled line, or skipped by the alignment) and every
    # rendered glyph with no partner
    res["a_glyphs"] = len(ac)
    res["a_unpaired"] = len(ac) - res["glyphs"]
    res["b_unpaired"] = b_total - res["glyphs"]
    print("JUDGE1 " + " ".join(f"{k} {v}" for k, v in sorted(res.items())), file=out)
    status = "OK" if res["glyphs"] else ("NOT_COMPILED" if res["not_compiled"] else "NO_TEXT")
    print(f"STATUS {status}", file=out)          # a page with 0 pairs has no residual
    return res


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    wd = None
    if "--work" in argv:
        j = argv.index("--work")
        wd = argv[j + 1]
        del argv[j:j + 2]
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    a_pdf, pno, lj = argv[0], int(argv[1]), argv[2]
    if wd:
        os.makedirs(wd, exist_ok=True)
        judge(a_pdf, pno, lj, wd)
    else:
        with tempfile.TemporaryDirectory(prefix="inkcmp-judge-") as d:
            judge(a_pdf, pno, lj, d)
    return 0


if __name__ == "__main__":
    sys.exit(main())

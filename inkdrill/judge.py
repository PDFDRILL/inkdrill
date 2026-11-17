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
import concurrent.futures
import difflib
import html
import io
import json
import math
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


_STEXT = {}                                           # per judge() call: font_preamble and chars read one page


def stext(pdf: str, pages: str) -> str:
    key = (os.path.abspath(pdf), pages)
    if key not in _STEXT:
        _STEXT[key] = subprocess.run(["mutool", "draw", "-F", "stext", "-o", "-", pdf, pages],
                                     capture_output=True, text=True, check=True).stdout
    return _STEXT[key]


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
    """poppler (FreeType, unhinted), anti-aliasing off, grey, the CropBox: the frame mutool's stext and trace
    coordinates are in (without -cropbox poppler renders the MediaBox, and a page cropped inside it -- board
    lib_d68e0271bd4d p334, CropBox at 43 87 -- had every glyph compared against ink 43 x 95 pt away)"""
    cmd = ["pdftoppm", "-cropbox", "-r", str(dpi), "-gray", "-aa", "no", "-aaVector", "no", "-hide-annotations",
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


INKCMP = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "build", "inkcmp")
TR = re.compile(r'<page number="(\d+)"|<fill_text [^>]*transform="([^"]+)"|<span font="([^"]+)"[^>]*trm="([^"]+)"|'
                r'<g unicode="([^"]*)" glyph="([^"]*)" x="([^"]+)" y="([^"]+)"|</fill_text>|'
                r'<(?:stroke_text|clip_text|clip_stroke_text|ignore_text)\b')


def trace(pdf: str, first: int, last) -> dict:
    """page -> [(char, glyph name, origin x pt, origin y pt, font, (a, b, c, d) pt per em)] of the filled text,
    from `mutool trace`: the glyph names and matrices stext does not give"""
    x = subprocess.run(["mutool", "trace", pdf, f"{first}-{'N' if last is None else last}"], capture_output=True,
                       text=True, check=True).stdout
    out, cur, M, T, font, live = {}, None, None, None, None, False
    for m in TR.finditer(x):
        if m[1]:
            cur = out.setdefault(int(m[1]), [])
        elif m[2]:
            M, live = [float(v) for v in m[2].split()], True
        elif m[3]:
            font, T = m[3], [float(v) for v in m[4].split()]
        elif m[6] is not None and m[7] and live and cur is not None:
            gx, gy = float(m[7]), float(m[8])
            A = (T[0] * M[0] + T[1] * M[2], T[0] * M[1] + T[1] * M[3], T[2] * M[0] + T[3] * M[2], T[2] * M[1] + T[3] * M[3])
            cur.append((html.unescape(m[5]), m[6], gx * M[0] + gy * M[2] + M[4], gx * M[1] + gy * M[3] + M[5], font, A))
        else:
            live = False
    return out


class Origins:
    """the traced glyph at a stext origin: nearest within 0.02 pt, the same character first"""
    def __init__(self, glyphs):
        self.b = collections.defaultdict(list)
        for g in glyphs:
            self.b[(int(g[2] // 1), int(g[3] // 1))].append(g)

    def get(self, x, y, ch):
        best = None
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for g in self.b.get((int(x // 1) + i, int(y // 1) + j), ()):
                    d = abs(g[2] - x) + abs(g[3] - y)
                    if d <= 0.02 and (best is None or (g[0] != ch, d) < best[0]):
                        best = ((g[0] != ch, d), g)
        return best[1] if best else None


def font_name(path: str) -> str:
    """the name a font program gives itself: a CFF's first Name INDEX entry, a Type 1 program's /FontName"""
    with open(path, "rb") as fh:
        raw = fh.read(65536)
    if path.endswith(".cff") and len(raw) > 4:
        hs = raw[2]
        n, osz = int.from_bytes(raw[hs:hs + 2], "big"), raw[hs + 2] if len(raw) > hs + 2 else 0
        if n and 1 <= osz <= 4:
            o = [int.from_bytes(raw[hs + 3 + k * osz:hs + 3 + (k + 1) * osz], "big") for k in range(2)]
            base = hs + 3 + (n + 1) * osz - 1
            return raw[base + o[0]:base + o[1]].decode("latin-1")
        return ""
    m = re.search(rb"/FontName\s*/([^\s/{}\[\]()<>]+)", raw)
    return m[1].decode("latin-1") if m else ""


def pgm_write(path: str, w: int, h: int, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(b"P5\n%d %d\n255\n" % (w, h) + data)


def pgms(pdf: str, pages: list, dpi: int, wd: str) -> dict:
    """page -> (w, h, grey, file) for several pages, one poppler call over their range"""
    out, pages = {}, sorted(set(pages))
    for lo, hi in [(pages[0], pages[-1])] if pages else []:   # one call over the range: poppler's start-up costs
        want = set(pages)                                     # more than the small snippet pages between
        pre = os.path.join(wd, f"b{lo}")
        subprocess.run(["pdftoppm", "-cropbox", "-r", str(dpi), "-gray", "-aa", "no", "-aaVector", "no",
                        "-hide-annotations", "-f", str(lo), "-l", str(hi), pdf, pre], capture_output=True, check=True)
        for f in os.listdir(wd):
            m = re.fullmatch(rf"b{lo}-0*(\d+)\.pgm", f)
            if m and int(m[1]) in want:
                with open(os.path.join(wd, f), "rb") as fh:
                    raw = fh.read()
                h = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", raw)
                out[int(m[1])] = (int(h[1]), int(h[2]), raw[h.end():], os.path.join(wd, f))
    return out


def glyph_side_a(a_pdf: str, pno: int, wd: str) -> tuple:
    """what the glyph route needs of the original alone (it runs while pdflatex sets the reading): its font programs,
    its traced glyphs, and its 400-dpi render"""
    FA = Fonts(a_pdf, os.path.join(wd, "fontsA"), 0)
    TA = Origins(trace(a_pdf, pno, pno).get(pno, []))
    aw, ah, ab = pgm(a_pdf, pno, DPI)
    a400 = os.path.join(wd, "a400.pgm")
    pgm_write(a400, aw, ah, ab)
    return FA, TA, (aw, ah, ab), a400


def glyph_scores(side_a: tuple, side_b, bpdf: str, ac: list, work: list, wd: str, res) -> tuple:
    """the glyph-raster route (CONTRACT unit 5): every pair's (xor, solid, piece) from both glyphs' own font programs,
    rasterised by `inkcmp glyphs` at A's origin; an A side without an outline (no font program, a Type 3 or TrueType
    font, an accent the page drew as a rule) is the page's own ink from one 400-dpi render. Also returns that render
    and the 400-dpi renders of the snippet pages the formula lines' rule comparison reads."""
    FA, TA, (aw, ah, ab), a400 = side_a
    FB, tb = side_b[0].result(), side_b[1].result()
    need = [i + 1 for i, ln, b, ca, cb, pairs, _ in work if is_formula(ln) and pairs]
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    bjob = pool.submit(pgms, bpdf, need, DPI, wd) if need else None    # poppler renders while inkcmp rasterises
    # every glyph of the original as a placement, bucketed by its quad (+4 px): a pair's window sees its neighbours'
    # ink on both sides, as the rendered crops of the reference route do
    G = 64
    aplace, abox, abuck = {}, {}, collections.defaultdict(list)
    for c in ac:
        g = TA.get(c[1], c[2], html.unescape(c[0]))
        if g is None:
            continue
        aplace[id(c)] = (FA.find(g[4]), g[1], tuple(v * S for v in g[5]), c[1] * S + OX, c[2] * S + OY)
        q = (c[3] * S - 4, c[4] * S - 4, c[5] * S + 4, c[6] * S + 4)
        abox[id(c)] = q
        for gx in range(int(q[0] // G), int(q[2] // G) + 1):
            for gy in range(int(q[1] // G), int(q[3] // G) + 1):
                abuck[(gx, gy)].append(c)
    hit = lambda q, w: q[0] < w[2] and q[2] > w[0] and q[1] < w[3] and q[3] > w[1]
    jobs, keys = [], []
    for i, ln, b, ca, cb, pairs, _ in work:
        TB = Origins(tb.get(i + 1, []))
        bg = [(q, TB.get(q[1], q[2], html.unescape(q[0]))) for q in cb]
        for k, (pa, pbb) in enumerate(pairs):
            X, Y = pa[1] * S + OX, pa[2] * S + OY
            win = (int(pa[3] * S) - 2, int(pa[4] * S) - 2, int(pa[5] * S) + 3, int(pa[6] * S) + 3)
            sa = aplace.get(id(pa))
            gb = TB.get(pbb[1], pbb[2], html.unescape(pbb[0]))
            # a character with no glyph of its own (a ligature's second letter, a combining slash): its ink is its
            # neighbours', which the window collects
            sb = (FB.find(gb[4]), gb[1], tuple(v * S for v in gb[5]), X, Y) if gb else "none"
            if gb is None:
                res["b_no_glyph"] += 1
            if sa is None:
                res["a_page_ink"] += 1
            adds = []
            if sa is not None:
                seen = {id(pa)}
                for gx in range(win[0] // G, win[2] // G + 1):
                    for gy in range(win[1] // G, win[3] // G + 1):
                        for c in abuck.get((gx, gy), ()):
                            if id(c) not in seen and hit(abox[id(c)], win):
                                seen.add(id(c))
                                adds.append(("A",) + aplace[id(c)])
            dx, dy = X - pbb[1] * S, Y - pbb[2] * S             # B's frame -> A's window, by this pair's origins
            for q, g in bg:
                if q is pbb or g is None:
                    continue
                if hit((q[3] * S + dx - 4, q[4] * S + dy - 4, q[5] * S + dx + 4, q[6] * S + dy + 4), win):
                    adds.append(("B", FB.find(g[4]), g[1], tuple(v * S for v in g[5]), q[1] * S + dx, q[2] * S + dy))
            jobs.append((sa, sb, win, adds))
            keys.append(((i, k), len(jobs) - 1))
    r, _ = glyph_job([FA, FB], jobs, a400)
    redo = [j for j, v in enumerate(r) if isinstance(v, str) and v.startswith("A")]
    if redo:                                         # the page's ink where A's own program has no outline
        r2, _ = glyph_job([FA, FB], [(None, jobs[j][1], jobs[j][2], [a for a in jobs[j][3] if a[0] == "B"])
                                     for j in redo], a400)
        for j, v in zip(redo, r2):
            r[j] = v
        res["a_page_ink"] += len(redo)
    scores = {}
    for key, j in keys:
        scores[key] = r[j]
    bp = bjob.result() if bjob else {}
    pool.shutdown()
    return scores, (aw, ah, ab, a400), {p - 1: v for p, v in bp.items()}


class Fonts:
    """the font programs a PDF embeds (`mutool extract`), loaded once by `inkcmp glyphs`; a traced font name finds
    its file by full name, by name without the subset prefix, or by that name without trailing digits (mutool
    numbers a second instance of a name), among the files that hold the glyph"""
    def __init__(self, pdf: str, wd: str, base: int = 0):
        os.makedirs(wd, exist_ok=True)
        subprocess.run(["mutool", "extract", os.path.abspath(pdf)], cwd=wd, capture_output=True)
        self.files = sorted(os.path.join(wd, f) for f in os.listdir(wd) if f.endswith((".pfa", ".pfb", ".cff")))
        self.base = base
        self.names = {base + i: font_name(f) for i, f in enumerate(self.files)}

    def ids(self):
        return {self.base + i: f for i, f in enumerate(self.files)}

    def find(self, font: str):
        """candidate ids, best first"""
        if not hasattr(self, "_by"):
            self._by = collections.defaultdict(list)
            for i, nm in self.names.items():
                self._by[nm].append(i)
                self._by[nm.split("+")[-1]].append(i)
        out = list(self._by.get(font, ()))
        b = font.split("+")[-1]
        out += [i for i in self._by.get(b, ()) if i not in out]
        b2 = re.sub(r"\d+$", "", b)
        out += [i for i in self._by.get(b2, ()) if i not in out]
        return out


def glyph_job(fonts: list, pairs: list, page_pgm: str | None) -> tuple[list, dict]:
    """one `inkcmp glyphs` run. pairs: (A side, B side, window[, adds]); a side is (font ids, glyph name, matrix, X, Y)
    or None (A: the page's ink); adds: ("A"|"B", font ids, glyph name, matrix, X, Y), neighbours' ink in the window. Font candidates are tried in order: the first whose program holds the glyph.
    Returns per pair (xor, solid, piece) or the reason there is no outline, and the fonts' names."""
    job, names = ["INKGLYPHS1"], {}
    for fs in fonts:
        job += [f"font {i} {f}" for i, f in fs.ids().items()]
    if page_pgm:
        job.append(f"page {page_pgm} {INK}")
    def place(s):
        if s == "none":
            return "none - 0 0 0 0 0 0"
        ids, g, m, X, Y = s
        return f"{','.join(map(str, ids)) if ids else -2} {g} {m[0]!r} {m[1]!r} {m[2]!r} {m[3]!r} {X!r} {Y!r}"
    for a, b, w, *more in pairs:
        sides = ["-1 - 0 0 0 0 0 0" if s is None else place(s) for s in (a, b)]
        job.append(f"pair {sides[0]} {sides[1]} {w[0]} {w[1]} {w[2]} {w[3]}")
        for ad in (more[0] if more else ()):
            job.append(f"add {ad[0]} {place(ad[1:])}")
    job.append("end")
    p = subprocess.run([INKCMP, "glyphs"], input="\n".join(job) + "\n", capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("inkcmp glyphs: " + p.stderr.strip())
    res = []
    for ln in p.stdout.splitlines():
        f = ln.split(" ", 3)
        if f[0] == "font":
            if f[2] == "ok":
                names[int(f[1])] = f[3]
        elif f[0] == "P":
            res.append((int(f[1]), int(f[2]), int(f[3])))
        elif f[0] == "O":
            res.append(f[1] + " " + (f[2] if len(f) > 2 else "") + (" " + f[3] if len(f) > 3 else ""))
    return res, names


def cover_map(quads, x0: int, y0: int, w: int, h: int) -> bytearray:
    """1 where a pixel centre (x + 0.5, y + 0.5) lies in one of the float quads, edges included (rule_ink's
    `covered`), over the w x h pixels from (x0, y0)"""
    m = bytearray(w * h)
    for q in quads:
        a, b = max(math.ceil(q[0] - 0.5), x0), min(math.floor(q[2] - 0.5), x0 + w - 1)
        c, d = max(math.ceil(q[1] - 0.5), y0), min(math.floor(q[3] - 0.5), y0 + h - 1)
        if a > b:
            continue
        for y in range(c, d + 1):
            k = (y - y0) * w
            m[k + a - x0:k + b - x0 + 1] = b"\x01" * (b - a + 1)
    return m


INKTAB = bytes(1 if v < INK else 0 for v in range(256))


def ink_xs(row: bytes):
    """indices of ink samples in a row of grey values"""
    t = row.translate(INKTAB)
    i = t.find(1)
    while i >= 0:
        yield i
        i = t.find(1, i + 1)


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


UNMAPPABLE = {"\ufffd"}             # always dropped: a reader's "glyph I cannot name" (pdfdrill 660ea83c)


def latex_text(s: str, drop: frozenset = frozenset()) -> str:
    """A reading's line as pdflatex input: raw % and # (and & outside maths) escaped, mapped characters (maths mode
    tracked through $, \\( \\), \\[ \\]), compatibility forms through NFKC, characters in UNMAPPABLE or `drop`
    (those pdflatex reported it cannot set) removed, everything else unchanged."""
    s = "".join(unicodedata.normalize("NFKC", ch)              # compatibility forms first, so a full-width ％
                if ord(ch) > 126 and unicodedata.decomposition(ch).startswith(("<compat>", "<wide>", "<narrow>"))
                else ch for ch in s)                           # is escaped like any %
    s = "".join(ch for ch in s if ch not in UNMAPPABLE and ch not in drop)
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


ACCENTS = set("\u00af\u02c6\u02dc\u00b4\u0060\u02d9\u00a8\u02c7\u02d8\u02da\u02c9\u02ca\u02cb\u2192\u20d7\u007e\u005e")


def is_accent(c: str) -> bool:
    """a spacing or combining accent glyph as mutool reports it (entity-encoded or not)"""
    c = html.unescape(c)
    return len(c) == 1 and (c in ACCENTS or 0x0300 <= ord(c) <= 0x036f)


LOGERR = re.compile(r"^! (.*)$")
LOGLINE = re.compile(r"^l\.(\d+)")
INPUTLINE = re.compile(r"on input line (\d+)")


UNICODE_ERR = re.compile(r"Unicode character .* \(U\+([0-9A-Fa-f]{4,6})\)")


def compile_lines(texts: list, sizes: list, pre: str, wd: str, passes: int = 12) -> tuple[str, dict, set]:
    """All lines in one pdflatex run (one page per line). A character pdflatex reports it cannot set ("Unicode
    character ... (U+XXXX) not set up") is dropped from every line and the run is repeated; a line whose source raises
    any other LaTeX error is set aside (an empty page keeps the numbering) and the run is repeated.
    Returns (pdf path, {line index: error}, dropped characters)."""
    failed: dict = {}
    drop: set = set()
    for _ in range(passes):
        body, starts, n = [], [], 4 + pre.count("\n") + 1     # documentclass, usepackage, pre, begin{document}
        for i, (t, sz) in enumerate(zip(texts, sizes)):
            src = "\\mbox{}" if i in failed else latex_text(t, frozenset(drop))
            line = "\\begin{pline}\\fontsize{%.4f}{%.4f}\\selectfont %s\\end{pline}" % (sz * 72.27 / 72,
                                                                                  sz * 72.27 / 72 * 1.2, src)
            starts.append(n)
            n += line.count("\n") + 1
            body.append(line)
        with open(os.path.join(wd, "lines.tex"), "w", encoding="utf-8") as fh:
            fh.write("\\documentclass[multi=pline,border=2pt,varwidth=60cm]{standalone}\n"
                     "\\usepackage[T1]{fontenc}\\usepackage{amsmath,amssymb,amsfonts}\n" + pre + "\n\\begin{document}\n" + "\n".join(body)
                     + "\n\\end{document}\n")
        subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "lines.tex"], cwd=wd,
                       capture_output=True)
        with open(os.path.join(wd, "lines.log"), encoding="utf-8", errors="replace") as fh:
            log = fh.read().splitlines()
        new, msg, newdrop = {}, None, set()
        for ln in log:
            m = LOGERR.match(ln)
            if m:
                msg = m.group(1)
                u = UNICODE_ERR.search(msg)
                if u:                                # a character, not the line: drop it everywhere
                    newdrop.add(chr(int(u.group(1), 16)))
                    msg = None
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
        if newdrop - drop:                          # characters first: their lines may compile once they are gone
            drop |= newdrop
            continue
        if not new:
            break
        failed.update(new)
    return os.path.join(wd, "lines.pdf"), failed, drop


def is_formula(ln: dict) -> bool:
    """a line the rule-ink comparison applies to: maths by type, or inline maths in its text"""
    return ln.get("type") in ("math", "equation") or bool(re.search(r"\$|\\\(|\\\[", ln.get("text", "")))


def components(px: set) -> list:
    """8-connected components of a pixel set"""
    seen, out = set(), []
    for p in px:
        if p in seen:
            continue
        stack, comp = [p], []
        seen.add(p)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (x + dx, y + dy)
                    if q in px and q not in seen:
                        seen.add(q)
                        stack.append(q)
        out.append(comp)
    return out


def rule_like(comp: list) -> bool:
    """a horizontal rule -- fraction bar, overline, radical vinculum, underline: >= 8 px long, >= 3x as long as thick,
    at most 6 px thick (400-dpi grid). Vertical pieces are left out: in formulas they are almost always the edges of
    tall glyphs (parentheses, brackets) whose quads differ between the two fonts' metrics."""
    xs, ys = [p[0] for p in comp], [p[1] for p in comp]
    w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    return w >= 8 and w >= 3 * h and h <= 6


def runs01(t: bytes) -> list:
    """(first, last) of each run of 1 in a row of 0/1 bytes"""
    out, i = [], t.find(1)
    while i >= 0:
        j = t.find(0, i)
        if j < 0:
            j = len(t)
        out.append((i, j - 1))
        i = t.find(1, j)
    return out


def and_not(t: bytes, c) -> bytes:
    """t AND NOT c, bytewise, for rows of 0/1 bytes of one length"""
    return (int.from_bytes(t, "big") & ~int.from_bytes(c, "big")).to_bytes(len(t), "big")


def rule_components(rows: list) -> list:
    """the 8-connected components of row runs [(y, [(x0, x1), ...]), ...] (y increasing) that are rule_like, as
    pixel lists in the order of their first run: `[c for c in components(px) if rule_like(c)]` without visiting
    every pixel"""
    runs, par = [], []

    def f(k):
        while par[k] != k:
            par[k] = par[par[k]]
            k = par[k]
        return k
    prev, prev_y = [], None
    for y, rr in rows:
        cur = []
        for x0, x1 in rr:
            k = len(runs)
            runs.append((y, x0, x1))
            par.append(k)
            cur.append(k)
        if prev_y == y - 1:
            j = 0
            for k in cur:
                _, x0, x1 = runs[k]
                while j < len(prev) and runs[prev[j]][2] < x0 - 1:
                    j += 1
                t = j
                while t < len(prev) and runs[prev[t]][1] <= x1 + 1:
                    ra, rb = f(k), f(prev[t])
                    if ra != rb:
                        par[max(ra, rb)] = min(ra, rb)
                    t += 1
        prev, prev_y = cur, y
    box = {}                                          # root -> [x0, y0, x1, y1], one pass
    for k, (y, x0, x1) in enumerate(runs):
        r = f(k)
        b = box.get(r)
        if b is None:
            box[r] = [x0, y, x1, y]
        else:
            if x0 < b[0]:
                b[0] = x0
            if x1 > b[2]:
                b[2] = x1
            b[3] = y                                  # runs come in increasing y
    keep = {r for r, b in box.items() if (b[2] - b[0] + 1) >= 8 and (b[2] - b[0] + 1) >= 3 * (b[3] - b[1] + 1)
            and b[3] - b[1] + 1 <= 6}
    groups = {}
    for k, (y, x0, x1) in enumerate(runs):
        r = f(k)
        if r in keep:
            groups.setdefault(r, []).extend((x, y) for x in range(x0, x1 + 1))
    return [groups[r] for r in sorted(groups)]


def rule_pixels(acrop, bpage, up, acov, bcov, region) -> tuple:
    """rule_ink's pixel stage in Python, the reference for `inkcmp rules`: per side the rule-like components of the
    ink no quad covers (A: in the region; B: the whole snippet page), each with its touch count -- the pixels with a
    4-neighbour of covered ink. A rule stands free when touch <= 0.3 of its pixels: a glyph's edge outside its quad
    (TeX's CM under a page's Times) borders glyph ink along its length, a vinculum touches its radical sign only at
    one end."""
    aw, ah, ab, cx0, cy0 = acrop
    pw, ph, pb = bpage
    xa, ya, xb, yb = region
    AW = xb - xa + 1
    am = cover_map(acov, xa, ya, AW, yb - ya + 1)
    lo, hi = max(xa, -(-(cx0 - up // 2) // up)), min(xb, (aw - 1 - up // 2 + cx0) // up)
    a_rows = []
    for y in range(ya, yb + 1):
        ty = y * up + up // 2 - cy0
        if not 0 <= ty < ah or lo > hi:
            continue
        at = ty * aw + lo * up + up // 2 - cx0
        k = (y - ya) * AW + lo - xa
        a_rows.append((y, [(lo + x0, lo + x1) for x0, x1 in
                           runs01(and_not(ab[at:at + (hi - lo) * up + 1:up].translate(INKTAB), am[k:k + hi - lo + 1]))]))

    def a_cov(x, y):
        ty = y * up + up // 2 - cy0
        return (ya <= y <= yb and lo <= x <= hi and 0 <= ty < ah and am[(y - ya) * AW + x - xa] == 1
                and ab[ty * aw + x * up + up // 2 - cx0] < INK)
    PW, PH = pw // up, ph // up
    bm = cover_map(bcov, 0, 0, PW, PH)
    b_rows = []
    for y in range(PH):
        at = (y * up + up // 2) * pw + up // 2
        b_rows.append((y, runs01(and_not(pb[at:at + (PW - 1) * up + 1:up].translate(INKTAB), bm[y * PW:(y + 1) * PW]))))

    def b_cov(x, y):
        return 0 <= x < PW and 0 <= y < PH and bm[y * PW + x] == 1 and pb[(y * up + up // 2) * pw + x * up + up // 2] < INK

    def touch(comp, cov):
        return sum(1 for x, y in comp if any(cov(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))))
    return ([(c, touch(c, a_cov)) for c in rule_components(a_rows)],
            [(c, touch(c, b_cov)) for c in rule_components(b_rows)])


def rules_job(requests: list, wd: str) -> list:
    """`inkcmp rules` for many (page pgm, ink, up, cx0, cy0, quads, region) requests at once ->
    per request [(pixels, touch)]"""
    job, last = ["INKRULES1"], None
    for pg, quads, reg in requests:
        if pg != last:
            job.append("page %s %d %d %d %d" % pg)
            last = pg
        job.append("job %d %d %d %d" % reg)
        job += ["quad %r %r %r %r" % tuple(q) for q in quads]
    job.append("end")
    p = subprocess.run([INKCMP, "rules"], input="\n".join(job) + "\n", capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("inkcmp rules: " + p.stderr.strip())
    out, cur, comp = [], None, None
    for ln in p.stdout.splitlines():
        f = ln.split()
        if f[0] == "job":
            cur = []
            out.append(cur)
        elif f[0] == "comp":
            comp = []
            cur.append((comp, int(f[2])))
        elif f[0] == "run":
            y, x0, x1 = int(f[1]), int(f[2]), int(f[3])
            comp.extend((x, y) for x in range(x0, x1 + 1))
    return out


def rule_ink(i, b, ca, cb, pairs, acrop, bpage, res, out, pad=1, tol=2, boxes=None, glyphs=None, display=False,
             up=UP, stage=None):
    """Non-glyph ink of a formula line (rules, fraction bars, radical vincula), page against rendering.
    - page rules: the page's ink in the line box (+4 px) that none of the page's own glyph quads covers;
    - rendered rules: the snippet page's ink that no glyph covers, where a paired glyph's cover is its PARTNER's page
      quad mapped back by that pair's own offset (TeX's quads differ: its M reaches up over an \\overline) and an
      unpaired rendered glyph's cover is its own quad;
    - each rendered rule moves into the page's frame by the offset of the paired glyph nearest it in x (displayed
      maths is spaced differently, so one offset for the line misplaces its parts);
    - a page rule less than half explained by rendered rule ink within `tol` px is MISSING, a rendered rule with no
      page partner INVENTED. Rules are horizontal (`rule_like`). 400-dpi grid. A page rule over an accent already
      measured on ink (\\bar over a drawn \\overline) is that accent's partner and is skipped."""
    aw, ah, ab, cx0, cy0 = acrop
    pw, ph, pb = bpage
    ids_a = {(c[0], c[1]) for c in ca}
    real = [(pa, pbb) for pa, pbb in pairs if (pa[0], pa[1]) in ids_a]
    if not real:
        return
    offs = sorted(((pbb[3] + pbb[5]) / 2 * S, pa[1] * S - pbb[1] * S, pa[2] * S - pbb[2] * S) for pa, pbb in real)
    def offset_at(xb):                                   # the offset of the paired glyph nearest in x (B frame)
        return min(offs, key=lambda o: abs(o[0] - xb))[1:]
    covered = lambda q, x, y: any(r[0] <= x + 0.5 <= r[2] and r[1] <= y + 0.5 <= r[3] for r in q)
    acov = [(c[3] * S - pad, c[4] * S - pad, c[5] * S + pad, c[6] * S + pad) for c in ca]
    accq = []
    for pa, _ in pairs:
        if (pa[0], pa[1]) in ids_a:
            continue
        gh = max((pa[6] - pa[4]) * S, 4.0)
        acov.append((pa[3] * S - pad, pa[4] * S - pad, pa[5] * S + pad, pa[6] * S + pad))
        accq.append((pa[3] * S - tol, pa[4] * S - 1.5 * gh, pa[5] * S + tol, pa[6] * S + 1.5 * gh))
    used_b = {id(pbb) for _, pbb in pairs}
    bcov = []
    for pa, pbb in pairs:                                 # the partner's quad, in B's frame
        ox, oy = pa[1] * S - pbb[1] * S, pa[2] * S - pbb[2] * S
        bcov.append((pa[3] * S - ox - pad, pa[4] * S - oy - pad, pa[5] * S - ox + pad, pa[6] * S - oy + pad))
    bcov += [(c[3] * S - pad, c[4] * S - pad, c[5] * S + pad, c[6] * S + pad) for c in cb if id(c) not in used_b]
    # the page's ink in the line box (+4 px, + ext rows), and the whole snippet page, sampled at the 400 grid's
    # pixel centres of a render `up` times finer: the ink no glyph quad covers, as rule-like components with the
    # count of their pixels that border covered ink. `stage` (side, quads, region) -> [(pixels, touch)] does this
    # elsewhere (`inkcmp rules`); without it, here.
    ext = max(4, int(0.6 * (b[3] - b[1])))     # an overline over a tight box lies above it; owner() keeps the
    ya, yb, xa, xb = int(b[1]) - ext, int(b[3]) + ext, int(b[0]) - 4, int(b[2]) + 4   # neighbours' rules out
    PW, PH = pw // up, ph // up
    if stage is not None:
        a_comps = stage("A", acov, (xa, ya, xb, yb))
        b_comps = stage("B", bcov, (0, 0, PW - 1, PH - 1))
    else:
        a_comps, b_comps = rule_pixels(acrop, bpage, up, acov, bcov, (xa, ya, xb, yb))
    # inline maths: only rules over the maths -- overlapping a maths-font glyph in x (a footnote rule above a
    # "^3" line is not part of a formula); in display lines every rule counts
    mx_a = [(c[3] * S, c[5] * S) for c in ca if re.search(MATHFONT + r"|MSBM|MSAM|EUF|EUS", c[7])]
    mx_b = [(c[3] * S, c[5] * S) for c in cb if re.search(MATHFONT + r"|MSBM|MSAM|EUF|EUS", c[7])]
    over_maths = lambda comp, mx: display or any(r[0] < max(p[0] for p in comp) + 1 and r[1] > min(p[0] for p in comp)
                                                for r in mx)
    b_rules = []
    for comp, touch in b_comps:
        if touch <= 0.3 * len(comp) and over_maths(comp, mx_b):
            xs = [p[0] for p in comp]
            dx, dy = offset_at((min(xs) + max(xs)) / 2)
            b_rules.append([(round(x + dx), round(y + dy)) for x, y in comp])
    b_map = {p for comp in b_rules for p in comp}
    near = lambda pts, x, y: any((x + ddx, y + ddy) in pts for ddx in range(-tol, tol + 1) for ddy in range(-tol, tol + 1))
    a_rules = [c for c, touch in a_comps if touch <= 0.3 * len(c) and over_maths(c, mx_a)]
    a_map = {p for comp in a_rules for p in comp}
    def owner(comp):
        xs, ys = [p[0] for p in comp], [p[1] for p in comp]
        mnx, mxx, mxy = min(xs), max(xs), max(ys)
        cx, cy = (mnx + mxx + 1) / 2, (min(ys) + mxy + 1) / 2
        inside = [((q[2] - q[0]) * (q[3] - q[1]), li) for li, q in enumerate(boxes or [b])
                  if q[0] <= cx <= q[2] and q[1] <= cy <= q[3]]
        if inside:
            return min(inside)[1]
        below = []                                         # an overline/vinculum: the line of the glyph under it
        for (c, li) in glyphs or []:
            gx0, gy0, gx1, gy1 = c[3] * S, c[4] * S, c[5] * S, c[6] * S
            if gx1 <= mnx or gx0 >= mxx + 1:
                continue
            gap = gy0 - (mxy + 1)
            if -2 <= gap <= 1.5 * max(gy1 - gy0, 4.0):
                below.append((gap, li))
        if below:
            return min(below)[1]
        best = None
        for li, q in enumerate(boxes or [b]):
            if q[2] <= mnx or q[0] >= mxx + 1:
                continue
            d = max(q[1] - cy, 0, cy - q[3])
            key = (d, (q[2] - q[0]) * (q[3] - q[1]))
            if best is None or key < best[0]:
                best = (key, li)
        return best[1] if best else None
    for comp in a_rules:
        if boxes is not None and owner(comp) != i:
            continue                                       # another line's rule
        if any(covered(accq, x, y) for x, y in comp):
            continue                                       # the partner of an accent measured on ink
        res["rules_a"] += 1
        if sum(1 for x, y in comp if near(b_map, x, y)) < 0.5 * len(comp):
            res["rules_missing"] += 1
            xs, ys = [p[0] for p in comp], [p[1] for p in comp]
            print(f"R L{i} missing {min(xs)} {min(ys)} {max(xs) + 1} {max(ys) + 1} {len(comp)}", file=out)
    for comp in b_rules:
        if sum(1 for x, y in comp if near(a_map, x, y)) < 0.5 * len(comp):
            res["rules_invented"] += 1
            xs, ys = [p[0] for p in comp], [p[1] for p in comp]
            print(f"R L{i} invented {min(xs)} {min(ys)} {max(xs) + 1} {max(ys) + 1} {len(comp)}", file=out)

def judge(a_pdf: str, pno: int, lines_json: str, wd: str, out=sys.stdout, route: str = "render") -> collections.Counter:
    _STEXT.clear()
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
        print(f"JUDGE1 a_glyphs {len(ac)} a_unpaired {len(ac)} accents_on_ink 0 b_unpaired 0 flagged 0 glyph 0 rules_a 0 rules_invented 0 rules_missing 0 "
              f"glyphs 0 lines 0 not_compiled 0 style 0 unmappable 0 unpaired_A 0 unpaired_B 0", file=out)
        print("STATUS FONT_UNMAPPED", file=out)
        return collections.Counter(lines=0, glyphs=0, flagged=0, style=0, glyph=0, not_compiled=0)
    side_a = None
    if route == "glyph":                            # the original's side runs while pdflatex sets the reading
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(compile_lines, [ln["text"] for ln in lv], sizes, pre, wd)
            side_a = glyph_side_a(a_pdf, pno, wd)
            bpdf, failed, dropped = fut.result()
    else:
        bpdf, failed, dropped = compile_lines([ln["text"] for ln in lv], sizes, pre, wd)
    gone = UNMAPPABLE | dropped
    side_b = None
    if route == "glyph" and os.path.exists(bpdf):   # the reading's trace and fonts while its stext is read
        pool_b = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        side_b = (pool_b.submit(Fonts, bpdf, os.path.join(wd, "fontsB"), len(side_a[0].files)),
                  pool_b.submit(trace, bpdf, 1, None))
        pool_b.shutdown(wait=False)
    bc = chars(bpdf, 1, len(lv)) if os.path.exists(bpdf) else []
    res = collections.Counter(lines=0, glyphs=0, flagged=0, style=0, glyph=0, not_compiled=0, unpaired_A=0,
                              unpaired_B=0, accents_on_ink=0, unmappable=0, rules_a=0, rules_missing=0,
                              rules_invented=0)
    b_total = 0                                     # rendered glyphs of the lines that typeset
    print(f"# page {pno}: family {fam}, {len(lv)} lines, A chars {len(ac)}, snippet pages {len(bc)}", file=out)
    if len(bc) != len(lv):            # the document itself did not come out page for page: no line is trusted
        failed = {i: failed.get(i, "no snippet page (document failed)") for i in range(len(lv))}
        bc = []
    for i, e in sorted(failed.items()):
        print(f"N L{i} NOT_COMPILED {e}", file=out)
    res["not_compiled"] = len(failed)
    # characters dropped from the snippets (U+FFFD, and any pdflatex said it cannot set): the page's glyphs there
    # stay unpaired original ink, counted once
    res["unmappable"] = sum(1 for i, ln in enumerate(lv) if i not in failed for ch in ln["text"] if ch in gone)
    for ch in sorted(dropped):
        print(f"D U+{ord(ch):04X} dropped (pdflatex cannot set it)", file=out)
    # each original glyph belongs to ONE line: the smallest box that holds its centre. Where a reading's boxes
    # overlap, a glyph was paired once per box (board 2312.11532 p7: 3396 pairs of 3325 glyphs)
    boxes = [box(ln) for ln in lv]
    owner = {}
    cell = 256                                      # boxes by grid cell of the 400 grid, in line order
    grid = collections.defaultdict(list)
    for li, b in enumerate(boxes):
        for gx in range(int(b[0] // cell), int(b[2] // cell) + 1):
            for gy in range(int(b[1] // cell), int(b[3] // cell) + 1):
                grid[(gx, gy)].append(li)
    for ci, c in enumerate(ac):
        best = None
        for li in grid.get((int((c[3] + c[5]) / 2 * S // cell), int((c[4] + c[6]) / 2 * S // cell)), ()):
            b = boxes[li]
            if inside(c, b):
                a = (b[2] - b[0]) * (b[3] - b[1])
                if best is None or a < best[0]:
                    best = (a, li)
        if best is not None:
            owner[ci] = best[1]
    # an accent above its base often lies above a tight line box (\bar{M}: the macron at 201-202 px, the box from
    # 203): it belongs to the line of the nearest owned glyph directly below it, within one glyph height
    for ci, c in enumerate(ac):
        if ci in owner or not is_accent(c[0]):
            continue
        cx0, cy1, h = c[3], c[6], max(c[6] - c[4], 1.0)
        best = None
        for bi, d in enumerate(ac):
            if bi not in owner or bi == ci or d[5] <= cx0 or d[3] >= c[5]:
                continue                            # base glyphs overlapping the accent in x
            gap = d[4] - cy1                        # base top below accent bottom (pt, y down)
            dh = max(d[6] - d[4], 1.0)
            if -0.5 * h <= gap <= dh and (best is None or gap < best[0]):
                best = (gap, owner[bi])
        if best is not None:
            owner[ci] = best[1]
    of_line = collections.defaultdict(list)          # the page's glyphs per line, in page order
    for ci, c in enumerate(ac):
        if ci in owner:
            of_line[owner[ci]].append(c)
    work = []                                       # per judged line: (i, ln, b, ca, cb, pairs, U lines)
    for i, ln in enumerate(lv[:len(bc)]):
        if i in failed:
            continue
        b_total += len(bc[i])
        b = boxes[i]
        ca = sorted(of_line.get(i, ()), key=lambda c: c[1])
        cb = sorted(bc[i], key=lambda c: c[1])
        if not ca or not cb:
            res["lines_empty"] += 1
            continue
        pairs, ulines = [], []
        sm = difflib.SequenceMatcher(a=[c[0] for c in ca], b=[c[0] for c in cb], autojunk=False)
        for op, a0, a1, b0, b1 in sm.get_opcodes():
            if op == "equal" or (op == "replace" and a1 - a0 == b1 - b0):
                pairs += list(zip(ca[a0:a1], cb[b0:b1]))
            else:
                res["unpaired_A"] += a1 - a0
                res["unpaired_B"] += b1 - b0
                ulines += [f"U L{i} A {c[0]}" for c in ca[a0:a1]]
                ulines += [f"U L{i} B {c[0]}" for c in cb[b0:b1]]
        # a rendered accent the page has as ink but not as a glyph (\overline drawn as a rule where the reading
        # wrote \bar, board 2103.01507 p118): placed relative to its base's partner and XORed against the page's
        # ink there, like any glyph. A reading that drops the accent still leaves the page's ink unpaired.
        used_b = {id(q) for _, q in pairs}
        for acc in cb:
            if id(acc) in used_b or not is_accent(acc[0]):
                continue
            base = None
            for pa, pbb in pairs:
                if pbb[5] <= acc[3] or pbb[3] >= acc[5]:
                    continue
                gap = pbb[4] - acc[6]
                if -0.5 * max(acc[6] - acc[4], 1.0) <= gap <= max(pbb[6] - pbb[4], 1.0) and (base is None or gap < base[0]):
                    base = (gap, pa, pbb)
            if base is None:
                continue
            _, pa, pbb = base
            dx, dy = pa[1] - pbb[1], pa[2] - pbb[2]          # B frame -> A frame (pt)
            pseudo = (acc[0], acc[1] + dx, acc[2] + dy, acc[3] + dx, acc[4] + dy, acc[5] + dx, acc[6] + dy, acc[7], acc[8])
            pairs.append((pseudo, acc))
            res["unpaired_B"] -= 1
            res["accents_on_ink"] += 1
        res["lines"] += 1
        work.append((i, ln, b, ca, cb, pairs, ulines))
    scores, page400, bpages = {}, None, {}
    glyph_owners = [(c, owner[ci]) for ci, c in enumerate(ac) if ci in owner]
    if route == "glyph" and work:
        scores, page400, bpages = glyph_scores(side_a, side_b, bpdf, ac, work, wd, res)
    stages = {}
    if route == "glyph" and work:                   # rule_ink's pixel stage for every formula line in one C call:
        aw, ah, ab, a400 = page400                  # a first pass collects the requests, the second reads them
        reqs, where = [], {}

        def collect(i):
            def st(side, quads, reg):
                f = a400 if side == "A" else bpages[i][3]
                where[(i, side)] = len(reqs)
                reqs.append(((f, INK, 1, 0, 0), quads, reg))
                return []
            return st
        for i, ln, b, ca, cb, pairs, _ in work:
            if is_formula(ln) and pairs:
                rule_ink(i, b, ca, cb, pairs, (aw, ah, ab, 0, 0), bpages[i][:3], collections.Counter(), io.StringIO(),
                         boxes=boxes, glyphs=[], display=ln.get("type") in ("math", "equation"), up=1,
                         stage=collect(i))
        got = rules_job(reqs, wd) if reqs else []
        for i, *_ in work:
            stages[i] = (lambda i: lambda side, quads, reg: got[where[(i, side)]])(i)
    for i, ln, b, ca, cb, pairs, ulines in work:
        for u in ulines:
            print(u, file=out)
        if route == "glyph":
            for k, ((t, ax, ay, qx0, qy0, qx1, qy1, fa, _), (tb, bx, by, _, _, _, _, fb, _)) in enumerate(pairs):
                r = scores[(i, k)]
                if isinstance(r, str):                  # no outline on the reading's side: not judged
                    res["no_outline"] += 1
                    print(f"O L{i} {t} {tb} {r}", file=out)
                    continue
                xo, so, pc = r
                flag = so >= THRESHOLD
                sa, sb = style(fa) or "-", style(fb) or "-"
                print(f"G L{i} {t} {tb} {int(t == tb)} {xo} {so} {pc} {int(flag)} {sa} {sb} {fa}", file=out)
                res["glyphs"] += 1
                if flag:
                    res["flagged"] += 1
                    res["style" if sa != sb else "glyph"] += 1
            if is_formula(ln) and pairs:
                aw, ah, ab, _ = page400
                rule_ink(i, b, ca, cb, pairs, (aw, ah, ab, 0, 0), bpages[i][:3], res, out, boxes=boxes,
                         glyphs=glyph_owners, display=ln.get("type") in ("math", "equation"), up=1, stage=stages[i])
            continue
        pw, ph, pb = pgm(bpdf, i + 1, DPI * UP)
        mg = 240                                              # 30 px at 400 dpi: ink above a tight box (accents)
        cx0, cy0 = max(0, int(b[0]) * UP - mg), max(0, int(b[1]) * UP - mg)
        aw, ah, ab = pgm(a_pdf, pno, DPI * UP, (cx0, cy0, int(b[2] - b[0]) * UP + 2 * mg, int(b[3] - b[1]) * UP + 2 * mg))
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
        if is_formula(ln) and pairs:
            rule_ink(i, b, ca, cb, pairs, (aw, ah, ab, cx0, cy0), (pw, ph, pb), res, out, boxes=boxes,
                     glyphs=glyph_owners,
                     display=ln.get("type") in ("math", "equation"))
    # page-wide counts for the board's ink residual (coordinator, 2026-11-17): every original glyph that paired
    # with nothing (outside any line, in an empty or uncompiled line, or skipped by the alignment) and every
    # rendered glyph with no partner
    res["a_glyphs"] = len(ac)
    res["a_unpaired"] = len(ac) - (res["glyphs"] - res["accents_on_ink"])   # accents on ink pair no A glyph
    res["b_unpaired"] = b_total - res["glyphs"]
    print("JUDGE1 " + " ".join(f"{k} {v}" for k, v in sorted(res.items())), file=out)
    status = "OK" if res["glyphs"] else ("NOT_COMPILED" if res["not_compiled"] else "NO_TEXT")
    print(f"STATUS {status}", file=out)          # a page with 0 pairs has no residual
    return res


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    wd, route = None, "render"
    if "--work" in argv:
        j = argv.index("--work")
        wd = argv[j + 1]
        del argv[j:j + 2]
    if "--route" in argv:
        j = argv.index("--route")
        route = argv[j + 1]
        del argv[j:j + 2]
        if route not in ("glyph", "render"):
            print("inkcmp-judge: --route glyph|render", file=sys.stderr)
            return 2
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    a_pdf, pno, lj = argv[0], int(argv[1]), argv[2]
    if wd:
        os.makedirs(wd, exist_ok=True)
        judge(a_pdf, pno, lj, wd, route=route)
    else:
        with tempfile.TemporaryDirectory(prefix="inkcmp-judge-") as d:
            judge(a_pdf, pno, lj, d, route=route)
    return 0


if __name__ == "__main__":
    sys.exit(main())

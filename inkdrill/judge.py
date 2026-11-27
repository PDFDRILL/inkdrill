"""inkcmp judge, born-digital branch: is a lines.json reading of a PDF page the same ink as the page?

    inkcmp-judge <original.pdf> <page> <lines.json> [--work DIR] [--route glyph|render|scan] [--no-setter]
                 [--peers other.lines.json,...]   (the page's other judged readings: one route for all of them)
    inkcmp-judge --batch <jobs.tsv> [--work DIR] [--route glyph|render|scan]   (one pdflatex per preamble; judge_batch)
    inkcmp-judge --rects <rects.tsv> --work DIR   (one residual per rectangle, glyph route; inkdrill/rects.py)

Contract: c/inkcmp/CONTRACT.md units 4-5 (in-place registration, phase-exact per glyph). Steps:

1. Each lines.json leaf line (text, no children_ids, not diagram; a page_info line with text is judged like
   text, 2026-11-21) becomes one page of a `standalone` document (env `pline`) set in the page's own font
   family (`font_preamble`) at the size
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
    G L<i> <A char> <B char> <equal> <xor> <solid> <largest piece> <flag> <A style> <B style> <A font> <x0 y0 x1 y1> <place>
(place, appended 2026-11-24: how far the reading set the pair above (+) or below (-) where its page neighbours and its
line say it belongs, as a share of the page glyph's size, `placed`; over PLACE the pair is flagged and a glyph finding,
counted also as `misplaced` in JUDGE1) <kind> (appended 2026-11-27, J4: what JUDGE1 counted for the pair, g glyph
finding, s style finding, - none; a flagged pair in a face TeX cannot set is -)
Then `U L<i> A|B <char> <x0 y0 x1 y1>` for unpaired glyphs. The box is the glyph's quad box in the R lines' frame (the
page at 400 dpi, px): the page glyph's on G and side-A U lines, the rendered glyph's carried into the page by its line's
median pair offset on side-B U lines (fields appended 2026-11-21, so a parser by index from the front still reads), `N L<i> NOT_COMPILED <LaTeX error> <x0 y0 x1 y1>` (the line's region box, same frame: read it as the last four
fields) for a line pdflatex could not set
(the other lines still set and pair), a summary line `JUDGE1 lines .. glyphs .. flagged .. not_compiled ..
a_glyphs .. a_unpaired .. b_unpaired ..` (a_glyphs: all glyphs of the original page; a_unpaired: those that paired
with nothing; b_unpaired: rendered glyphs with no partner; the board's ink residual is
(findings + a_unpaired + b_unpaired) / a_glyphs; unmeasurable: paired glyphs whose original face TeX cannot
set (UNSETTABLE: MathTime, Optima, Myriad Pro, Helvetica Neue condensed). They stay in glyphs but are never a glyph finding, and the board takes
them out of the denominator; a face difference there is still a style finding), and
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
import statistics
import subprocess
import sys
import tempfile
import unicodedata

from . import setter as setter_mod
from . import textpos

DPI, UP = 400, 8
S = DPI / 72
OX = OY = -0.0625
INK = 154
THRESHOLD = 7
#: a pair set this share of its page glyph's size above or below where its page neighbours and its line say it
#: belongs is a glyph finding, however equal its ink (`placed`). The J1 cases read 0.84 (\\sin_2 for \\sin^2) and
#: 1.47 (\\bigr)_{2} for \\right)^2); on the 49 board jobs every pair not already a finding read 0.40 or less, where
#: the page's and TeX's metrics differ (a display sum's limits, 1805.01788 p3 L81, whose sum is itself a finding)
PLACE = 0.5
CH = re.compile(r'<font name="([^"]*)"(?: size="([\d.]+)")?|<char c="(.*?)"(?: hexc="([0-9a-fA-F]+)")? quad="([^"]+)" '
                r'x="([\d.\-]+)" y="([\d.\-]+)"')
PGS = re.compile(r"<page [^>]*>(.*?)</page>", re.S)
MATHFONT = r"CMSY|CMMI|CMEX|MSBM|MSAM|EU[RFSE]|MTMI|MTSY|MTEX|MT2(?:MI|SY|EX)"   # MathTime since 2026-11-21
#: the maths faces left out of a line's size vote: MATHFONT without MathTime. Leaving MathTime out too put display lines
#: on their script size (board18 lib:3fe71b4ef018 p328 L20: 32 glyphs at 9.963 pt, 22 at 6.974; without MathTime 19
#: and 20), so the line was set at 7 pt: MathPix glyph findings 33 -> 66 there.
#: CMMI and CMSY vote since T-0158 (2026-11-22): their glyphs report the size they are set at, and without them a display
#: of maths letters votes by its few text glyphs, mostly script digits (a fixture's \frac{a_{i1}}{r_{i1}} = \sum_{j=1}^n
#: x_j: 4 of its 5 CMR glyphs at 7 pt, so the line was set at 7 pt and all 14 pairs flagged). CMEX keeps its design
#: size at every text size, MSBM/MSAM and Euler are left out as before.
SIZEVOTE_SKIP = r"CMEX|MSBM|MSAM|EU[RFSE]"
#: faces TeX cannot set: no font program in the TeX tree (kpsewhich, 2026-11-21), so a glyph in one is unmeasurable --
#: its shape against the stand-in face measures the judge, not the reading (coordinator, 2026-11-21: the per-glyph
#: form of FONT_UNMAPPED). A list, not "what FAMILIES does not map": on the board18 pages that complement also held
#: SFBX/SFTI (cm-super), txsys/rtxr (newtx), BBOLD, esint and BLEX (Belleek, MathTime's free relative), all settable.
#: Helvetica Neue's condensed cuts (J8, lib:44e3840fd17e p378's running head, -LtCn / -BdCn): TeX's only condensed
#: Helvetica is URW's Helvetica Narrow (phv, series c), regular and bold only, and not metric-compatible -- the page's
#: Light Condensed digits advance 480/1000 where Narrow's are 456, its Bold Condensed I 258 where Narrow Bold's is 228
UNSETTABLE = re.compile(r"^R?MT2?(?:MI|SY|EX|MS)[A-Z0-9]{0,3}$|^Optima|^MyriadPro|^HelveticaNeue\w*-\w*(?:Cn|Cond)",
                        re.I)


def unsettable(font: str) -> bool:
    """a PDF font name (subset tag and obfuscation prefix allowed) in a face TeX cannot set"""
    return bool(UNSETTABLE.search(PREFIX.sub("", font.split("+")[-1])))

FAMILIES = [  # (regex on the PDF font name, preamble, family)
    (r"NimbusRom|Times|TeXGyreTermes|ptmr|utmr", r"\usepackage{times}", "times"),
    (r"NimbusSan|Helvetica|Arial|TeXGyreHeros|phvr|uhvr", r"\usepackage{helvet}\renewcommand{\familydefault}{\sfdefault}",
     "helvetica"),
    (r"Palatino|Palladio|TeXGyrePagella|pplr|uplr", r"\renewcommand{\rmdefault}{ppl}", "palatino"),
    (r"^CM|^LM|SFRM|LMRoman", "", "cm"),
    (r"LinLibertine|LinBiolinum|Libertine", r"\usepackage{libertine}", "libertine"),
    # a page wholly in DejaVu Sans, its code in DejaVu Sans Mono (board RE4B-DE p559: FONT_UNMAPPED before)
    (r"DejaVuSans(?!Mono|Condensed)", r"\usepackage{DejaVuSans}\usepackage{DejaVuSansMono}"
     r"\renewcommand{\familydefault}{\sfdefault}", "dejavusans"),
]
MATHFAM = [(r"EURM|EUSM|EUFM|EUEX", r"\usepackage{eulervm}"), (r"NewTXMI|ntxmi|txmi|TXMI", r"\usepackage{newtxmath}"),
           (r"zplm|NewPXMI|pxmi", r"\usepackage{newpxmath}"),
           (r"TeX-math[ab]\d|^math[ab]\d", r"\usepackage{mathabx}"),   # mathabx: its own designs of \in, \geq, ...
           (r"MTMI|MTSY|MTEX", r"\usepackage{mathptmx}"),  # MathTime: no free version; mathptmx is its Times relative
           (r"esint\d*$", r"\usepackage{esint}"),          # its integrals (board lib:6d90e8d27b8b p174), not CMEX's
           (r"BBOLD\d*$", r"\usepackage{bbold}"),
           (r"^LMRoman\d", r"\usepackage{lmodern}")]     # Latin Modern's own designs, not T1 cm-super's (J6)          # \mathbb from bbold, lowercase included (2103.01507 p118's k)
#: maths letters in Libertine (LinLibertineI 5 7 from an older newtx, LibertineMathMI from today's): newtxmath with
#: its libertine option. Plain newtxmath sets them in Times-like NewTXMI (board 1805.01788 p3: 20 maths italics
#: flagged with equal styles)
NEWTX_LIBERTINE = r"\usepackage[libertine]{newtxmath}"
#: \hbar as amsfonts sets it, MSBM's planckover2pi ("7E), on a page that prints that glyph: mathptmx (and others)
#: define their own, a macron over h, two glyphs against the page's one (board lib:3fe71b4ef018 p328: 6 page ℏ and 6
#: rendered macrons unpaired in both columns, whose readings wrote \hbar)
MSBM_HBAR = r'\AtBeginDocument{\let\hbar\relax\DeclareMathSymbol{\hbar}{\mathord}{AMSb}{"7E}}'



#: a line set in another family than its page's (a plot's labels, a running head): the NFSS family pdflatex sets it
#: in, by the PDF font name of most of the line's text glyphs (board 2312.11532 p7: matplotlib's DejaVuSerif on a Times
#: page; 0864 p4: a Utopia running head). FAMILIES' families by their NFSS names.
LINE_FAMILIES = [(r"DejaVuSerifCondensed", "DejaVuSerifCondensed-TLF"), (r"DejaVuSerif", "DejaVuSerif-TLF"),
                 (r"DejaVuSansCondensed", "DejaVuSansCondensed-TLF"), (r"DejaVuSans(?!Mono)", "DejaVuSans-TLF"),
                 (r"Utopia", "put"),
                 (r"LinBiolinum", "LinuxBiolinumT-TLF")]   # libertine's sans: board 1805.01788 p3, an acmart title
NFSS = {"times": "ptm", "helvetica": "phv", "palatino": "ppl", "cm": "cmr", "libertine": "LinuxLibertineT-TLF",
        "dejavusans": "DejaVuSans-TLF"}
#: \texttt on a page whose typewriter is CMTT: OT1 sets cmtt itself (CMTT8 at 7 pt on the page -> cmtt8), where the
#: judge's T1 sets cm-super's sftt, an outline of the same design whose m and - flag against CMTT
OT1_TT = r"\DeclareTextFontCommand{\texttt}{\fontencoding{OT1}\ttfamily}"
#: the double quotes on a page that prints OT1 Computer Modern (CMR, no cm-super): its TeX set `` and '' from cmr's
#: own glyphs (OT1 92 and 34); the judge's T1 sets them from cm-super's SFRM, another design (cmr10 against sfrm1000:
#: xor 88, solid 21 at 10 pt; board 2103.01507 p118: a reading's correct quotes flagged at solid 58 and 9). The single
#: quotes are the same design in both
OT1_QUOTES = (r"\AtBeginDocument{\renewcommand{\textquotedblleft}{{\fontencoding{OT1}\selectfont\char92}}"
              r"\renewcommand{\textquotedblright}{{\fontencoding{OT1}\selectfont\char34}}}")
NOTEXT = MATHFONT + r"|Inconsolata|Courier|CMTT|Mono|Symbol|Dingbat"


def line_family(fonts, fam: str) -> str | None:
    """the NFSS family a line is set in when most of its text glyphs (fonts: their PDF font names) are in a known
    family other than the page's `fam`; None otherwise (the line is set in the page's family, as before)"""
    n = collections.Counter()
    for f in fonts:
        f = f.split("+")[-1]
        if re.search(NOTEXT, f):
            continue
        n[next((nf for r, nf in LINE_FAMILIES if re.search(r, f)), None)
          or next((NFSS[x] for r, _, x in FAMILIES if re.search(r, f)), None)] += 1
    top = n.most_common(1)[0][0] if n else None
    return top if top is not None and top != NFSS.get(fam) else None


#: a producer's obfuscation prefix: capitalised six-letter words without a vowel ("Yvftdh", "Lxcbxs"), before the name
PREFIX = re.compile(r"^(?:[A-Z][b-df-hj-np-tv-z]{5})+(?=[A-Z])")


def style(font: str) -> str:
    """the face a font's name says: x maths, m mono, b bold, i italic, c small caps. Read from the name alone: the
    subset tag (ABCDEF+) and an obfuscation prefix are cut first -- "YvftdhLxcbxsTimes-Italic" read bold from "cbxs"."""
    f = PREFIX.sub("", font.split("+")[-1]).lower()
    lib = re.search(r"lin(?:libertine|biolinum)([a-z0-9]*)$", f)       # LinLibertineTI: T (Type 1), B bold, I italic
    if lib:
        suf = lib.group(1)
        return ("b" if "b" in suf else "") + ("i" if "i" in suf else "")
    mt = re.match(r"r?mt2?(mi|sy|ex|ms)([a-z0-9]{0,3})$", f)         # MathTime: MTMI, MTMIB, MTSYN, MTEX, MT2MIT
    if mt:
        return "x" + ("b" if re.match(r"[bh]", mt.group(2)) else "") + ("i" if mt.group(1) == "mi" else "")
    return (("x" if re.search(r"eurm|eusm|eufm|euex|cmmi|cmsy|cmex|msbm|msam", f) else "")
            + ("m" if re.search(r"mono|monl|courier|cmtt|inconsolata|typewriter|tt\d", f) else "")
            + ("b" if re.search(r"bold|medi|bx|black|semibold|demi", f) else "")
            + ("i" if re.search(r"ital|obli|ti\d|mi\d|slant|it$", f) else "")
            + ("c" if re.search(r"smallca|small-caps|csc\d", f) else ""))


def glyph_face(glyph: str) -> str:
    """the face a glyph's NAME adds to its font's: a small capital (e.sc, q.sc_u.sc) is c. Libertine keeps its small
    caps in the text font's own program, so the font name reads plain (board 1805.01788 p3: 56 small capitals of an
    acmart definition head, LinLibertineT, read as plain letters and flagged as glyph findings)"""
    return "c" if re.search(r"\.(?:sc|smcp|c2sc)(?:$|[._])", glyph or "") else ""


def name_letters(glyph: str) -> str:
    """the letters a composite glyph's NAME spells, one per part (q.sc_u.sc -> qu, f_f_i -> ffi), or "" when a part
    is not one letter (uni00E9, a.alt is one part)"""
    parts = [p.split(".")[0] for p in (glyph or "").split("_")]
    return "".join(parts) if len(parts) > 1 and all(len(p) == 1 for p in parts) else ""


#: what a glyph named so draws, for a character the PDF has no map for (U+FFFD): the base of a TeX extension font's
#: name, after its size suffix (CMEX, txexs, MTEX, BLEX: parenleftbig, summationdisplay.1, radicalBig, hatwide).
#: Pieces of an extensible delimiter (barex, parenlefttp, bracketleftbt, ...) draw no one character and stay U+FFFD.
GLYPH_NAMED = {
    "parenleft": "(", "parenright": ")", "bracketleft": "[", "bracketright": "]", "braceleft": "{",
    "braceright": "}", "slash": "/", "backslash": "\\", "angbracketleft": "&#x27e8;", "angbracketright": "&#x27e9;",
    "floorleft": "&#x230a;", "floorright": "&#x230b;", "ceilingleft": "&#x2308;", "ceilingright": "&#x2309;",
    "radical": "&#x221a;", "summation": "&#x2211;", "product": "&#x220f;", "coproduct": "&#x2210;",
    "integral": "&#x222b;", "contintegral": "&#x222e;", "union": "&#x22c3;", "intersection": "&#x22c2;",
    "unionmulti": "&#x2a04;", "logicaland": "&#x22c0;", "logicalor": "&#x22c1;", "circleplus": "&#x2a01;",
    "circlemultiply": "&#x2a02;", "circledot": "&#x2a00;", "hat": "&#x2c6;", "tilde": "&#x2dc;",
}
GLYPH_SIZE = re.compile(r"(?:big|Big|bigg|Bigg|text|display|wide|wider|widest)?(?:\.\d+)?$")


def glyph_named(name: str) -> str:
    """the character a TeX extension glyph's name draws, or "" (parenleftBig -> "(", summationdisplay.1 -> U+2211)"""
    return GLYPH_NAMED.get(GLYPH_SIZE.sub("", name or "", count=1), "")


def identified(cs: list, traced: list) -> list:
    """the characters with each U+FFFD read from its glyph's name where the name says what it draws: a page's "("
    and a rendered \\left( that the PDF gives no map for were two characters to the pairing (T-0158, lib:c57ac038ae5f
    p2: 6 of MathPix's 22 unpaired). Both sides go through it, so a pair is judged by its ink as any other."""
    if not any(c[0] == "&#xfffd;" for c in cs):
        return cs
    TA = Origins(traced)
    out = []
    for c in cs:
        g = TA.get(c[1], c[2], c[0]) if c[0] == "&#xfffd;" else None
        ch = glyph_named(g[1]) if g is not None else ""
        out.append((ch,) + tuple(c[1:]) if ch else c)
    return out


def spelled(cs: list, traced: list) -> list:
    """the page's characters with each composite glyph's letters, one character per part of its name. mutool gives
    a ligature's later letters from the ToUnicode map, as zero-width characters at the glyph's right edge (f_i: f,
    then i); a map that has only the first letter (Libertine's q.sc_u.sc reads q, board 1805.01788 p3) leaves the
    rest out, and a reading that writes them scored worse than one that drops them. The missing letters are added
    as zero-width characters within the glyph's advance: at its right edge they would sort past, and take the glyph
    of, the next letter, whose origin is there. Each carries its glyph's name as a tenth field, so its face is the
    composite's (q.sc_u.sc's u is a small capital). The letters mutool gives at the right edge are moved inside the
    advance too: 0.02 pt past the next letter's origin, an fi's i sorted after the n of "find" and was unpaired in
    every reading (board35 lib:c57ac038ae5f p293, two)."""
    cs = list(cs)
    j = 0
    while j < len(cs):
        c, k = cs[j], j + 1
        while (k < len(cs) and cs[k][3] == cs[k][5] and abs(cs[k][1] - c[5]) < 0.05 and abs(cs[k][2] - c[2]) < 0.05
               and c[5] - c[1] > 0.1):
            k += 1                                       # zero-width letters at this glyph's right edge
        n = k - j - 1
        for m in range(n):
            q = cs[j + 1 + m]
            x = c[1] + (c[5] - c[1]) * (m + 1) / (n + 1)
            cs[j + 1 + m] = (q[0], x, q[2], x, q[4], x, q[6]) + tuple(q[7:])
        j = k
    TA = Origins(traced)
    out = []
    for j, c in enumerate(cs):
        out.append(c)
        g = TA.get(c[1], c[2], c[0])
        word = name_letters(g[1]) if g is not None and g[0] == c[0] else ""
        if not word or word[0] != c[0]:
            continue
        rest, k = word[1:], j + 1
        while rest and k < len(cs) and cs[k][0] == rest[0]:     # the letters the map already gave
            rest, k = rest[1:], k + 1
        for m, ch in enumerate(rest):       # inside the glyph's advance: after it, before the next glyph's origin
            x = c[1] + (c[5] - c[1]) * (m + 1) / (len(rest) + 1)
            out.append((ch, x, c[2], x, c[4], x, c[6], c[7], c[8], g[1]))   # its glyph's name, for glyph_face
    return out


#: fonts whose glyph names do not say what the glyph is: every glyph of esint is an integral sign, whatever its name
#: (board lib:6d90e8d27b8b p174: a custom subset's `acute` and `circumflex` are the text and display integrals, and
#: with no ToUnicode map stext reads them as accents). The outline decides, so they read as U+222B.
NAMELESS = ((re.compile(r"esint\d*$", re.I), "&#x222b;"),)


def by_outline(cs: list) -> list:
    """the page's characters with those of a NAMELESS font replaced by what its outlines draw"""
    out = []
    for c in cs:
        hit = next((ch for r, ch in NAMELESS if r.search(c[7].split("+")[-1])), None)
        out.append(c if hit is None or c[0] == hit else (hit,) + tuple(c[1:]))
    return out


#: Computer Modern and its relatives: on a page set in another text family, a digit in one of these was set as maths
CM_DIGITS = r"^(?:CMR|CMMI|CMBX|CMSL|LMRoman|LMMath|SFRM)\d*"


def maths_digit(t: str, tb: str, fa: str, fb: str, fam: str) -> bool:
    """a digit the page set in maths (Computer Modern on a page whose text family is not CM) and the reading wrote as
    text (set in the page's text family): the same character in another face, so a style finding, not a glyph
    finding (board 0864 p4: "15 cases", "760", typed as $15$; 28 CMR10 digits flagged in all three columns)"""
    na, nb = fa.split("+")[-1], fb.split("+")[-1]
    return (t == tb and len(t) == 1 and t.isdigit() and fam not in ("cm", "unmapped")
            and bool(re.match(CM_DIGITS, na)) and not re.match(CM_DIGITS, nb))


def repair(pairs: list, ua: list, ub: list, ca: list, cb: list, near: float = 0.03) -> tuple:
    """the sequence pairing's leftovers matched again by position: an unpaired page glyph, or one paired with another
    character, takes an unpaired (or wrongly paired) rendered glyph of its own character at the same place along the
    line (its x as a share of the line's extent, within `near`). In two-dimensional maths the x order is not the
    reading order: a fraction's numerator and denominator interleave, and the substitute's widths reorder them (board
    lib:3fe71b4ef018 p328 L1, M^{*2} under alpha_e: the page's * paired with a rendered 2, the rendered * and the
    page's 2 left unpaired). Pairs of equal characters are never touched."""
    wrong = [(pa, pb) for pa, pb in pairs if pa[0] != pb[0]]
    pa_pool = ua + [pa for pa, _ in wrong]
    pb_pool = ub + [pb for _, pb in wrong]
    if not pa_pool or not pb_pool:
        return pairs, ua, ub
    xa0, xa1 = min(c[1] for c in ca), max(c[1] for c in ca)
    xb0, xb1 = min(c[1] for c in cb), max(c[1] for c in cb)
    nx_a = lambda c: (c[1] - xa0) / ((xa1 - xa0) or 1)
    nx_b = lambda c: (c[1] - xb0) / ((xb1 - xb0) or 1)
    cand = sorted((abs(nx_a(a) - nx_b(b)), ia, ib) for ia, a in enumerate(pa_pool) for ib, b in enumerate(pb_pool)
                  if a[0] == b[0] and abs(nx_a(a) - nx_b(b)) <= near)
    got_a, got_b, new = set(), set(), []
    for _, ia, ib in cand:
        if ia not in got_a and ib not in got_b:
            got_a.add(ia)
            got_b.add(ib)
            new.append((pa_pool[ia], pb_pool[ib]))
    if not new:
        return pairs, ua, ub
    used_a = {id(pa_pool[k]) for k in got_a}
    used_b = {id(pb_pool[k]) for k in got_b}
    kept = []
    for pa, pb in pairs:
        if pa[0] == pb[0] or (id(pa) not in used_a and id(pb) not in used_b):
            kept.append((pa, pb))
        else:                                        # a wrong pair broken up: its other member is left over
            if id(pa) not in used_a:
                ua = ua + [pa]
            if id(pb) not in used_b:
                ub = ub + [pb]
    ua = [c for c in ua if id(c) not in used_a]
    ub = [c for c in ub if id(c) not in used_b]
    return kept + new, ua, ub


def face(font: str, glyph: str = "") -> str:
    """a paired glyph's face for the style count: its font's (style) and its glyph name's (glyph_face)"""
    f, g = style(font), glyph_face(glyph)
    return (f + g if g not in f else f) or "-"


_STEXT = {}                                           # per judge() call: font_preamble and chars read one page
_MEMO = None                                          # judge_batch: the original's side, kept between its two passes


def memo(key, f, *a):
    if _MEMO is None:
        return f(*a)
    if key not in _MEMO:
        _MEMO[key] = f(*a)
    return _MEMO[key]


def stext(pdf: str, pages: str) -> str:
    key = (os.path.abspath(pdf), pages, os.stat(pdf).st_mtime_ns)
    if key not in _STEXT:
        _STEXT[key] = subprocess.run(["mutool", "draw", "-F", "stext", "-o", "-", pdf, pages],
                                     capture_output=True, text=True, check=True).stdout
    return _STEXT[key]


#: a variation selector (U+FE00-FE0F, U+E0100-E01EF) draws no ink: it is not a glyph. txexs's ToUnicode writes the
#: display sum as U+2211 U+FE01, and mutool gives the selector as a zero-width character; it stayed unpaired on the
#: reading's side (board 1805.01788 p3: 10 side-B U lines, MathPix and Gemma4)
VARIATION = re.compile(r"&#x(?:fe0[0-9a-f]|e01[0-9a-e][0-9a-f]);", re.I)
MATH_ALPHA = re.compile(r"&#x(1d[4-7][0-9a-f]{2});", re.I)


def math_alpha(c: str) -> str:
    """a Mathematical Alphanumeric Symbol (U+1D400-U+1D7FF) as its letter, in mutool's spelling (ASCII bare, others
    &#x..;): 𝑖 -> i, 𝛼 -> α. The style the code point carries is the face of the font that draws it, and the G line
    already compares faces by font name. The judge sets the reading's ASCII i in LinLibertineI, whose ToUnicode says
    U+1D456; the page (txfonts) says i for the same glyph (board 1805.01788 p3: 20 of 35 unpaired_B were these)"""
    m = MATH_ALPHA.fullmatch(c)
    if not m:
        return c
    f = unicodedata.normalize("NFKC", chr(int(m.group(1), 16)))
    if len(f) != 1:
        return c                                   # unassigned (U+1D455, a reserved hole: h is U+210E) stays
    return f if " " < f < "\x7f" else f"&#x{ord(f):x};"


def t1_text() -> dict:
    """T1 code -> its text in mutool's spelling (ASCII bare, else &#x..;), from the TeX tree's own tables:
    cm-super-t1.enc (code -> glyph name) and glyphtounicode.tex (name -> Unicode; fi -> f i)"""
    if not _T1:
        enc = subprocess.run(["kpsewhich", "cm-super-t1.enc", "glyphtounicode.tex"], capture_output=True, text=True,
                             check=True).stdout.split()
        with open(enc[0], encoding="latin-1") as fh:
            names = re.findall(r"^/([^\s\[]+)\s*$", re.sub(r"%.*", "", fh.read()), re.M)
        with open(enc[1], encoding="latin-1") as fh:
            uni = dict(re.findall(r"\\pdfglyphtounicode\{([^}]+)\}\{([0-9A-F ]+)\}", fh.read()))
        spell = lambda u: html.escape(chr(u), quote=True) if 32 < u < 127 else "&#x%x;" % u
        for code, n in enumerate(names[:256]):
            if n in uni:
                _T1[code] = [spell(int(h, 16)) for h in uni[n].split()]
    return _T1


_T1: dict = {}


def chars(pdf: str, first: int, last: int, codes: dict | None = None) -> list[list[tuple]]:
    """per page: (char, origin x, origin y, quad x0, y0, x1, y1, font, size bp); a maths alphanumeric is read as its
    letter (math_alpha), a variation selector is left out (VARIATION). `codes`: the text of a character mutool could
    only give as its code (`hexc`: a font with no ToUnicode, pdfTeX's PK bitmaps), several letters sharing its quad
    in equal parts as mutool splits a mapped ligature"""
    out = []
    for body in PGS.findall(stext(pdf, f"{first}-{last}")):
        cs, font, size = [], "", 0.0
        for fn, fs, c, hx, q, ox, oy in CH.findall(body):
            if fn:
                font, size = fn, float(fs or 0)
                continue
            qv = [float(v) for v in q.split()]
            x0, y0, x1, y1 = min(qv[0::2]), min(qv[1::2]), max(qv[0::2]), max(qv[1::2])
            ts = (codes.get(int(hx, 16)) if codes is not None and hx else None) or [c]
            for k, t in enumerate(ts):
                if t.strip() == "" or VARIATION.fullmatch(t):
                    continue
                w = (x1 - x0) / len(ts)
                cs.append((math_alpha(t), float(ox) + k * w, float(oy), x0 + k * w, y0, x0 + (k + 1) * w, y1, font, size))
        out.append(cs)
    return out


#: the reading's T1/TS1 text fonts set from METAFONT bitmaps, as dvips set a page whose nameless Type 3 fonts are
#: those bitmaps (`font_preamble`): the map pdfTeX reads leaves them out, so it falls back to PK at dvips's own
#: resolution and mode (config.ps: D 600, M ljfour). arxiv.0910.1368 p12's body text is ecrm1095 at 600 dpi: its V is
#: ecrm1095.600pk's to the pixel (63 x 64, offsets -2 61, advance 68); its fi is T1's code 28
PK_MAP = "judge-pk.map"
PK_PRE = "\\pdfmapfile{%s}\\pdfpkresolution=600 \\pdfpkmode{ljfour}" % PK_MAP
#: and in the page's own designs. A page whose bitmaps are T1 (EC: its ligatures at codes 27-31) has a design at each of
#: LaTeX's sizes, t1cmr.fd's (ecrm1095 at 10.95 pt), where fix-cm scales ecrm1000 over 10-12 pt (p12's a: ecrm1095.600pk's
#: 40 px at -4, ecrm1000.657pk's 41 at -3). An OT1 page's cmr10 at 10.95 is cmr10 scaled, as fix-cm's ecrm1000 is.
PK_T1 = "\\makeatletter\\input{t1cmr.fd}\\makeatother"


def t1_bitmaps(pdf: str, names) -> bool:
    """whether a nameless Type 3 font of the page ("Type3 (106 0 R)": mutool names it by its object) is T1-encoded:
    its encoding's Differences use T1's ligature codes 27-31 (ff fi fl ffi ffl) and none of OT1's 11-15"""
    codes = set()
    for n in names:
        m = re.fullmatch(r"Type3 \((\d+) 0 R\)", n)
        if not m:
            continue
        font = subprocess.run(["mutool", "show", pdf, m[1]], capture_output=True, text=True).stdout
        e = re.search(r"/Encoding\s+(\d+)\s+0\s+R", font)
        enc = subprocess.run(["mutool", "show", pdf, e[1]], capture_output=True, text=True).stdout if e else font
        d = re.search(r"/Differences\s*\[(.*?)\]", enc, re.S)
        if d:                                       # a code, then names that take it and the codes after it
            k = None
            for tok in re.findall(r"\d+|/[^\s/\[\]]+", d[1]):
                if tok[0] == "/":
                    if k is not None:
                        codes.add(k)
                        k += 1
                else:
                    k = int(tok)
    return bool(codes & set(range(27, 32))) and not codes & set(range(11, 16))
PK_FONTS = re.compile(r"(?:ec|tc)[a-z]+\d+$")


def pk_map() -> str:
    """pdftex.map without the lines of the fonts PK_FONTS names (they are then set from PK)"""
    src = subprocess.run(["kpsewhich", "pdftex.map"], capture_output=True, text=True, check=True).stdout.strip()
    with open(src, encoding="latin-1") as fh:
        return "".join(ln for ln in fh if not PK_FONTS.match(ln.split(" ", 1)[0]))


#: mutool's name for a Type 3 font with no BaseFont: dvips's bitmap fonts (PK glyphs as images). It names no family,
#: so it does not vote in `font_preamble`; its glyphs are compared against the page's ink like any Type 3 glyph.
#: Counted as an unmapped family it made arxiv.0910.1368 p12 FONT_UNMAPPED (870 body-text glyphs in "Type3 (106 0
#: R)" against CMR10's 124): T-0158 J2, its 6 rectangles NO_TEXT
TYPE3_NAMELESS = re.compile(r"Type3 \(\d+ \d+ R\)")


def font_preamble(pdf: str, page: int) -> tuple[str, str]:
    """the page's text family, chosen by glyph count, plus any maths family it uses; "unmapped" when most text
    glyphs are in fonts no FAMILIES entry sets (the judge then reports STATUS FONT_UNMAPPED, no residual)"""
    glyphs, font = collections.Counter(), ""
    for fn, c in re.findall(r'<font name="([^"]*)"|<char c="(.*?)"', stext(pdf, str(page))):
        if fn:
            font = fn.split("+")[-1]
        elif c.strip():
            glyphs[font] += 1
    fams, unmapped, nameless = collections.Counter(), 0, 0
    for n, cnt in glyphs.items():
        if re.search(MATHFONT + r"|Inconsolata|Courier|CMTT|Mono|Symbol|Dingbat", n):
            continue
        if TYPE3_NAMELESS.fullmatch(n):              # no name to map: the page's named fonts say the family
            nameless += cnt
            continue
        hit = next((f for r, _, f in FAMILIES if re.search(r, n)), None)
        if hit:
            fams[hit] += cnt
        else:
            unmapped += cnt
    if unmapped > sum(fams.values()) or (nameless and not fams):   # most text glyphs are in a family TeX cannot
        return "unmapped", ""                                           # set, or in fonts with no name: no XOR
    fam = fams.most_common(1)[0][0] if fams else "cm"
    pre = [next(p for _, p, f in FAMILIES if f == fam)]
    pre += [p for r, p in MATHFAM if any(re.search(r, n) for n in glyphs)]
    if any(re.match(r"(?:LinLibertineI|LibertineMathMI)\d*$", n) for n in glyphs):
        pre = [x for x in pre if x != r"\usepackage{newtxmath}"] + [NEWTX_LIBERTINE]
    if any(re.match(r"CMTT\d", n) for n in glyphs):   # the page's typewriter is OT1 Computer Modern: T1's is cm-super,
        pre.append(OT1_TT)                            # whose m and - differ (board lib:d68e0271bd4d p334, 10 glyphs)

    # text in nameless Type 3 (dvips's PK bitmaps) and no cm-super outline on the page: the reading's T1 text is set
    # from the same bitmaps, compared ink against ink (T-0158 J2; against cm-super's outlines 43 of p12's 590 flagged)
    if nameless and fam == "cm" and not any(re.match(r"SF[A-Z]{2}\d{4}$", n) for n in glyphs):
        pre.append(PK_PRE)
        if t1_bitmaps(pdf, [n for n in glyphs if TYPE3_NAMELESS.fullmatch(n)]):
            pre.append(PK_T1)
    # cmr's own `` and '' (board 2103.01507 p118) on a page whose text is OT1 CM; not where it is T1 (EC bitmaps)
    if (fam == "cm" and any(re.match(r"CMR\d", n) for n in glyphs) and PK_T1 not in pre
            and not any(re.match(r"SF[A-Z]{2}\d{4}$", n) for n in glyphs)):
        pre.append(OT1_QUOTES)
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


HOLE_MIN = 3                                    # inkcmp glyphs: a white region is a hole from this many px
# (board, 89131 pairs of one glyph name on the glyph route: at 1 px 77 pairs differ in holes without a flag -- raster
# pinholes where an arm meets a stem; from 3 px none does)
INKCMP = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "build", "inkcmp")
TR = re.compile(r'<page number="(\d+)"|<fill_text [^>]*transform="([^"]+)"|<span font="([^"]+)"[^>]*trm="([^"]+)"|'
                r'<g unicode="([^"]*)" glyph="([^"]*)" x="([^"]+)" y="([^"]+)"|</fill_text>|'
                r'<(?:stroke_text|clip_text|clip_stroke_text|ignore_text)\b')


def trace_text(pdf: str, first: int, last) -> str:
    return subprocess.run(["mutool", "trace", pdf, f"{first}-{'N' if last is None else last}"], capture_output=True,
                          text=True, check=True).stdout


def trace(pdf: str, first: int, last, text: str | None = None) -> dict:
    """page -> [(char, glyph name, origin x pt, origin y pt, font, (a, b, c, d) pt per em)] of the filled text,
    from `mutool trace` (or its output, `text`): the glyph names and matrices stext does not give"""
    x = text if text is not None else trace_text(pdf, first, last)
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


class PageInk:
    """the original's 400-dpi render, made once and only when something reads it (a formula line's rules, an A
    side with no outline, an accent the page drew as a rule): 0.15-0.27 CPU s, and some pages need none"""
    def __init__(self, a_pdf: str, pno: int, wd: str):
        self.a_pdf, self.pno, self.wd, self.v = a_pdf, pno, wd, None

    def get(self) -> tuple:
        if self.v is None:
            aw, ah, ab = pgm(self.a_pdf, self.pno, DPI)
            a400 = os.path.join(self.wd, "a400.pgm")
            pgm_write(a400, aw, ah, ab)
            self.v = (aw, ah, ab, a400)
        return self.v


def glyph_side_a(a_pdf: str, pno: int, wd: str, spec: "Spec", render: bool) -> tuple:
    """what the glyph route needs of the original alone (it runs while pdflatex sets the reading): its font programs,
    its traced glyphs (at the specification's origins), and -- when a formula line will read it -- its 400-dpi
    render"""
    FA = Fonts(a_pdf, spec.fontdir, 0, extract=False)
    TA = Origins(spec.traced(pno, trace(a_pdf, pno, pno, spec.trace_text).get(pno, [])))
    ink = PageInk(a_pdf, pno, wd)
    if render:
        ink.get()
    return FA, TA, ink


def turn(m) -> tuple | None:
    """a glyph matrix's direction of writing as a unit vector (x right, y down), or None when it writes left to right
    upright: a rotated label (a plot's tick, rotatebox; board 0864 p4: dir="0 -1") is compared with the reading's
    glyph turned the same way, and its line paired along that direction"""
    a, b = m[0], m[1]
    n = math.hypot(a, b)
    if n == 0 or (b == 0 and a > 0):
        return None
    return a / n, b / n


def turned(v, u) -> tuple:
    """the vector (x, y) (x right, y down) turned from left-to-right writing into the direction u"""
    return v[0] * u[0] - v[1] * u[1], v[0] * u[1] + v[1] * u[0]


def along(cs, TA) -> tuple | None:
    """a line's direction of writing: the one most of its glyphs' matrices write in (`turn`), None for left to right"""
    us = collections.Counter()
    for c in cs:
        g = TA.get(c[1], c[2], html.unescape(c[0]))
        if g is not None:
            u = turn(g[5])
            us[None if u is None else (round(u[0], 3), round(u[1], 3))] += 1
    return us.most_common(1)[0][0] if us else None


def type3_names(pdf: str, page: int | None) -> set:
    """the page's Type 3 fonts by name without the subset prefix (a name some other font of the page also carries
    is left out): poppler draws their glyphs from its glyph cache at the floor of the device origin. page None: the
    whole document's"""
    rng = [] if page is None else ["-f", str(page), "-l", str(page)]
    out = subprocess.run(["pdffonts"] + rng + [pdf], capture_output=True, text=True).stdout
    kinds = collections.defaultdict(set)
    for ln in out.splitlines()[2:]:
        m = re.match(r"(\S+)\s+(Type 3|Type 1C|Type 1|TrueType|CID \S+(?: \S+)?)\s", ln)
        if m:
            kinds[m[1].split("+")[-1]].add(m[2])
    return {n for n, k in kinds.items() if k == {"Type 3"}}


def snap(side, adds, X: float, Y: float):
    """B (and its neighbours) moved to where poppler draws a Type 3 glyph whose origin is (X, Y): the cached glyph
    at floor of the device origin, X - OX (board 2312.11532 p7, matplotlib's rotated DejaVuSerif labels: B 0.4-1.25
    px off the page's ink, by the origin's fraction, upright and rotated alike)"""
    dx, dy = math.floor(X - OX) + OX - X, math.floor(Y - OY) + OY - Y
    side = side if side == "none" else side[:3] + (side[3] + dx, side[4] + dy)
    return side, [a if a[0] != "B" else a[:4] + (a[4] + dx, a[5] + dy) for a in adds]


#: the largest horizontal font expansion (microtype, pdfTeX's \\pdfadjustspacing) a page glyph's matrix may carry
EXPANSION = 0.03


def expanded(ma, mb):
    """the rendered glyph's matrix mb with the page glyph's horizontal expansion: where ma is upright and its x scale
    differs from its y scale by at most EXPANSION, mb's x components take the same ratio. Only the ratio, never the
    size: a reading at the wrong size still differs. pdfTeX expands a justified line's glyphs by up to 2-3 % (board
    lib:c9daf79960c3 p68: LMRoman12 at x 11.716-12.194 against y 11.955; 11 of ours' 14 glyph findings were such
    a's, i and b at the right place, the judge setting them unexpanded)"""
    if not ma or not mb or abs(ma[3]) < 1e-9 or abs(ma[1]) > 1e-6 * abs(ma[0]) or abs(ma[2]) > 1e-6 * abs(ma[3]):
        return mb
    r = abs(ma[0]) / abs(ma[3])
    if not 1e-4 < abs(r - 1) <= EXPANSION:
        return mb
    return (mb[0] * r, mb[1], mb[2] * r, mb[3])


def glyph_scores(side_a: tuple, side_b, spec_b, bpdf: str, ac: list, work: list, wd: str, res, bo: int = 0) -> tuple:
    """the glyph-raster route (CONTRACT unit 5): every pair's (xor, solid, piece) from both glyphs' own font programs,
    rasterised by `inkcmp glyphs` at A's origin; an A side without an outline (no font program, a Type 3 or TrueType
    font, an accent the page drew as a rule) is the page's own ink from one 400-dpi render. Also returns that render
    and the 400-dpi renders of the snippet pages the formula lines' rule comparison reads."""
    FA, TA, ink = side_a
    FB, tb = side_b
    need = [bo + i + 1 for i, ln, b, ca, cb, pairs, _ in work if is_formula(ln) and pairs]
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
    jobs, keys, origin = [], [], []                 # origin: per job A's (X, Y) and font name
    names = {}                                      # (line, pair) -> the glyph names (A, B), for glyph_face
    fname = lambda f: f.split("+")[-1]
    t3c = []

    def t3():                                       # the page's Type 3 names, read once and only when needed
        if not t3c:
            t3c.append(type3_names(ink.a_pdf, ink.pno))
        return t3c[0]
    # the reading's own Type 3 fonts: the PK bitmaps it is set in on a bitmap page (PK_PRE); such a rendered glyph is
    # its page's ink, drawn as poppler draws the page's: from its glyph cache at the floor of the device origin
    t3b = type3_names(bpdf, None) if PK_MAP in os.listdir(os.path.dirname(bpdf) or ".") else set()
    inkb = {}                                       # reading page -> its index in the C job's `pageb` lines
    for i, ln, b, ca, cb, pairs, _ in work:
        TB = Origins(spec_b.traced(bo + i + 1, tb.get(bo + i + 1, [])))
        bg = [(q, TB.get(q[1], q[2], html.unescape(q[0]))) for q in cb]
        for k, (pa, pbb) in enumerate(pairs):
            X, Y = pa[1] * S + OX, pa[2] * S + OY
            win = (int(pa[3] * S) - 2, int(pa[4] * S) - 2, int(pa[5] * S) + 3, int(pa[6] * S) + 3)
            sa = aplace.get(id(pa))
            gb = TB.get(pbb[1], pbb[2], html.unescape(pbb[0]))
            # a character with no glyph of its own (a ligature's second letter, a combining slash): its ink is its
            # neighbours', which the window collects
            u = turn(sa[2]) if sa is not None else None     # A written rotated: B is turned to A's direction
            mb = None
            if gb:
                mb = tuple(v * S for v in gb[5])
                if u:
                    mb = turned(mb[:2], u) + turned(mb[2:], u)
            mb = expanded(sa[2], mb) if sa is not None and not u else mb   # the page line's font expansion
            sb = (FB.find(gb[4]), gb[1], mb, X, Y) if gb else "none"
            ink_b = fname(gb[4] if gb else pbb[7]) in t3b     # a ligature's second letter too: its ink is there
            if ink_b:
                k_b = inkb.setdefault(bo + i + 1, len(inkb))
                sb = (f"={k_b}", "-", (0.0, 0.0, 0.0, 0.0), math.floor(pbb[1] * S) - math.floor(pa[1] * S),
                      math.floor(pbb[2] * S) - math.floor(pa[2] * S))
            names[(i, k)] = (sa[1] if sa is not None else pa[9] if len(pa) > 9 else "",   # an added letter of a
                             gb[1] if gb else pbb[9] if len(pbb) > 9 else "")              # composite: its name
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
                if q is pbb or g is None or ink_b:     # an ink side holds its neighbours already
                    continue
                if u:                                    # its offset from the pair's origin, turned like the pair
                    cs = [turned(((x - pbb[1]) * S, (y - pbb[2]) * S), u) for x in (q[3], q[5]) for y in (q[4], q[6])]
                    o = turned(((q[1] - pbb[1]) * S, (q[2] - pbb[2]) * S), u)
                    if hit((X + min(c[0] for c in cs) - 4, Y + min(c[1] for c in cs) - 4,
                            X + max(c[0] for c in cs) + 4, Y + max(c[1] for c in cs) + 4), win):
                        m = tuple(v * S for v in g[5])
                        adds.append(("B", FB.find(g[4]), g[1], turned(m[:2], u) + turned(m[2:], u), X + o[0], Y + o[1]))
                    continue
                if hit((q[3] * S + dx - 4, q[4] * S + dy - 4, q[5] * S + dx + 4, q[6] * S + dy + 4), win):
                    m = tuple(v * S for v in g[5])
                    adds.append(("B", FB.find(g[4]), g[1], expanded(sa[2], m) if sa is not None else m,
                                 q[1] * S + dx, q[2] * S + dy))
            if sa is None and fname(pa[7]) in t3() and not ink_b:     # the page's ink from the start, drawn snapped
                sb, adds = snap(sb, adds, X, Y)
            jobs.append((sa, sb, win, adds))
            origin.append((X, Y, fname(pa[7])))
            keys.append(((i, k), len(jobs) - 1))
    need = any(j[0] is None for j in jobs)        # an A side that is the page's ink from the start
    pagebs = []
    if inkb:                                         # the reading's pages its ink sides read, at 400 dpi
        got = pgms(bpdf, list(inkb), DPI, wd)
        pagebs = [(k, got[p][3]) for p, k in inkb.items()]
    r, _ = glyph_job([FA, FB], jobs, ink.get()[3] if need else None, pagebs)
    redo = [j for j, v in enumerate(r) if isinstance(v, str) and v.startswith("A")]
    if redo:                                         # the page's ink where A's own program has no outline
        again = []
        for j in redo:
            sb, adds = jobs[j][1], [a for a in jobs[j][3] if a[0] == "B"]
            if origin[j][2] in t3() and not (isinstance(sb, tuple) and isinstance(sb[0], str)):
                sb, adds = snap(sb, adds, origin[j][0], origin[j][1])
            again.append((None, sb, jobs[j][2], adds))
        r2, _ = glyph_job([FA, FB], again, ink.get()[3], pagebs)
        for j, v in zip(redo, r2):
            r[j] = v
        res["a_page_ink"] += len(redo)
    scores = {}
    for key, j in keys:
        scores[key] = r[j]
    bp = bjob.result() if bjob else {}
    pool.shutdown()
    return scores, ink, {p - 1 - bo: v for p, v in bp.items()}, names


class Spec:
    """mutool origin -> the specification's origin (inkdrill.textpos), looked up within 0.002 pt; pages it could
    not place keep mutool's origins"""
    def __init__(self, pdf: str, first: int, last, wd: str, fontdir: str | None = None):
        os.makedirs(wd, exist_ok=True)
        self.trace_text = trace_text(pdf, first, last)        # one `mutool trace`, shared with the glyph route
        self.fontdir = fontdir                                 # where its `mutool run` wrote the font programs (a
        # batch keeps one Spec per page across jobs: read them there, not in each job's own folder)
        self.pages = textpos.origins(pdf, first, last, wd, self.trace_text, fontdir)
        self.b = collections.defaultdict(dict)
        for n, rec in self.pages.items():
            for (x, y), v in rec["map"].items():
                self.b[n].setdefault((int(x // 1), int(y // 1)), []).append((x, y, v))

    def get(self, page: int, x: float, y: float):
        cells = self.b.get(page)
        if not cells:
            return x, y
        best = None
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for X, Y, v in cells.get((int(x // 1) + i, int(y // 1) + j), ()):
                    d = abs(X - x) + abs(Y - y)
                    if d <= 0.002 and (best is None or d < best[0]):
                        best = (d, v)
        return best[1] if best else (x, y)

    def chars(self, page: int, cs: list) -> list:
        """stext characters moved to their specification origins, quads with them"""
        out = []
        for c in cs:
            sx, sy = self.get(page, c[1], c[2])
            dx, dy = sx - c[1], sy - c[2]
            out.append((c[0], sx, sy, c[3] + dx, c[4] + dy, c[5] + dx, c[6] + dy, c[7], c[8]))
        return out

    def traced(self, page: int, gl: list) -> list:
        return [(g[0], g[1]) + self.get(page, g[2], g[3]) + (g[4], g[5]) for g in gl]


class Fonts:
    """the font programs a PDF embeds (`mutool extract`), loaded once by `inkcmp glyphs`; a traced font name finds
    its file by full name, by name without the subset prefix, or by that name without trailing digits (mutool
    numbers a second instance of a name), among the files that hold the glyph"""
    def __init__(self, pdf: str, wd: str, base: int = 0, extract: bool = True):
        os.makedirs(wd, exist_ok=True)
        if extract:                                   # else Spec's `mutool run` wrote them
            subprocess.run(["mutool", "extract", os.path.abspath(pdf)], cwd=wd, capture_output=True)
        self.files = sorted(os.path.join(wd, f) for f in os.listdir(wd) if f.endswith((".pfa", ".pfb", ".cff")))
        self.base = base
        self.names = {base + i: font_name(f) for i, f in enumerate(self.files)}

    def ids(self):
        return {self.base + i: f for i, f in enumerate(self.files)}

    def find(self, font: str):
        """candidate ids, best first"""
        if not hasattr(self, "_found"):
            self._found = {}
        if font not in self._found:
            self._found[font] = self._find(font)
        return self._found[font]

    def _find(self, font: str):
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


def glyph_job(fonts: list, pairs: list, page_pgm: str | None, pagebs: list = ()) -> tuple[list, dict]:
    """one `inkcmp glyphs` run. pairs: (A side, B side, window[, adds]); a side is (font ids, glyph name, matrix, X, Y)
    or None (A: the page's ink); adds: ("A"|"B", font ids, glyph name, matrix, X, Y), neighbours' ink in the window. Font candidates are tried in order: the first whose program holds the glyph.
    Returns per pair (xor, solid, piece) or the reason there is no outline, and the fonts' names."""
    job, names = ["INKGLYPHS1"], {}
    for fs in fonts:
        job += [f"font {i} {f}" for i, f in fs.ids().items()]
    if page_pgm:
        job.append(f"page {page_pgm} {INK}")
    job += [f"pageb {k} {f} {INK}" for k, f in pagebs]   # the reading's pages, for B sides "=k"
    if HOLE_MIN != 1:                              # (the C default is 1)
        job.append(f"holes {HOLE_MIN}")
    def place(s):
        if s == "none":
            return "none - 0 0 0 0 0 0"
        ids, g, m, X, Y = s
        ids = ids if isinstance(ids, str) else ",".join(map(str, ids)) if ids else -2       # "=k": B's ink
        return f"{ids} {g} {m[0]!r} {m[1]!r} {m[2]!r} {m[3]!r} {X!r} {Y!r}"
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
            v = f[3].split()
            res.append((int(f[1]), int(f[2]), int(v[0])) + tuple(int(x) for x in v[1:3]))   # + holes A, B
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


def leaves(page: dict, placed: bool = True) -> list[dict]:
    """the judged lines: text, no children, not a diagram. page_info is judged like text (65f1cf4). With
    `placed`, a line with no region box is left out, whatever its type: Gemma4 writes page_info lines with
    region None (board18g 1805.01788 p3, lib:5a5c76391c9c p4), which 65f1cf4 let through to box()."""
    return [ln for ln in page["lines"]
            if ln.get("text") and not ln.get("children_ids") and ln["type"] != "diagram"
            and (not placed or isinstance(ln.get("region"), dict))]

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
    # dropped on board pages until 2026-11-18 (1511.08771 p2709: U+2283, U+2295, U+2032): the page's glyphs stayed
    # unpaired in every reading. The prime is the raised script prime the page draws (^\prime), not \prime.
    "\u2283": ("$\\supset$", "\\supset"), "\u2295": ("$\\oplus$", "\\oplus"), "\u2261": ("$\\equiv$", "\\equiv"),
    "\u2200": ("$\\forall$", "\\forall"), "\u2217": ("$\\ast$", "\\ast"), "\u2032": ("$'$", "'"),
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


def display_style(s: str) -> str:
    """\\displaystyle after every opening inline maths delimiter ($, \\(); \\[...\\], $$...$$ and an escaped \\$
    unchanged"""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append(s[i:i + 2])
            if s[i + 1] == "(":
                out.append("\\displaystyle ")
            i += 2
            continue
        if s.startswith("$$", i):
            out.append("$$")
            i += 2
            continue
        if s[i] == "$":
            out.append("$")
            j = s.find("$", i + 1)                   # the closing $ of this inline maths
            if j < 0:
                i += 1
                continue
            out.append("\\displaystyle " + s[i + 1:j] + "$")
            i = j + 1
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


CODE_SPECIAL = set("\\{}$&#^_%~")


def code_tex(t: str) -> str:
    """a code line's characters as pdflatex input (board RE4B-DE p559: C++ names, @TModule@ValidWindow$qp14..., did
    not compile at their $ in either column). A line typed code says nothing sure about its face or its markup:
    pdfdrill types table rows code and writes LaTeX in them (\\texttt{idt} 130 RT, $\\langle$: board
    lib:d68e0271bd4d p334, some of them roman on the page). So only a line WITHOUT a LaTeX command is taken as its
    characters, TeX's specials by \\char, in the line's own face; a line that is one \\texttt{...} with no command
    inside is that, inside its own \\texttt (OT1_TT applies); any other line is LaTeX, unchanged."""
    esc = lambda x: "".join("\\char%d{}" % ord(c) if c in CODE_SPECIAL else c for c in x)
    m = re.fullmatch(r"\\texttt\{([^{}]*)\}", t.strip(), re.S)
    if m and not re.search(r"\\[A-Za-z]", m[1]):
        return "\\texttt{" + esc(re.sub(r"\\([\\{}$&#^_%~])", r"\1", m[1])) + "}"
    if re.search(r"\\[A-Za-z]", t):
        return t
    return esc(t)


def line_tex(ln: dict) -> str:
    """a reading's line as the judge sets it. A line typed math or equation is a DISPLAY (the lines.json schema)
    and is set in display style whatever its delimiters, for every column alike: ours writes displays as $...$, and
    in text style every \\frac came out at script size (board lib:3fe71b4ef018 p328). A text line is unchanged."""
    if ln.get("type") == "code":
        return code_tex(ln["text"])
    return display_style(ln["text"]) if ln.get("type") in ("math", "equation") else ln["text"]


LIG_NEXT = "fil"                                   # f, then one of these: an f ligature in TeX's text fonts
_NOLIG_ARG = re.compile(r"\\(?:begin|end|label|ref|eqref|pageref|cite[a-z]*|url|href|includegraphics|input|"
                        r"fontfamily|usepackage)\*?\s*(?:\[[^\]]*\]\s*)?\{[^{}]*\}")


def f_pairs(cs: list) -> list:
    """a page line's f pairs in reading order: for each f followed by f, i or l, True where the page drew the two
    as one ligature glyph (the second letter zero-width inside the f's advance, as spelled() places it), False where
    as two glyphs (J7: lib:3c16fac66fdd p370 sets Auf|lage, German's no ligature across a word joint). `cs` in page
    order: a ligature's zero-width letter sits at the next letter's origin, and an x sort put that letter first"""
    cs = [c for c in cs if c[0].strip()]
    out = []
    for p, q in zip(cs, cs[1:]):
        if p[0] == "f" and q[0] in LIG_NEXT:
            out.append(q[3] == q[5])
    return out


def unligature(t: str, pairs: list) -> str:
    """a reading's text line with an f ligature broken (f{}l) where the page line drew the pair as two glyphs (J7).
    The reading's f pairs in text mode (not maths, not a command name, not a label or a URL) are matched to the
    page's in order; with no page ligature in the line, every one is broken; where the counts differ and the page
    has both, the line is left as it is"""
    if not pairs or all(pairs):
        return t
    skip = [False] * len(t)
    for m in _NOLIG_ARG.finditer(t):
        for k in range(m.start(), m.end()):
            skip[k] = True
    pos, math, i = [], False, 0
    while i < len(t):
        ch = t[i]
        if ch == "\\" and i + 1 < len(t):
            nx = t[i + 1]
            if nx in "([":
                math = True
            elif nx in ")]":
                math = False
            i += 2
            if nx.isalpha():
                while i < len(t) and t[i].isalpha():
                    i += 1                               # a command name: \\flat, \\fi
            continue
        if ch == "$":
            math = not math
        elif not math and not skip[i] and ch == "f" and i + 1 < len(t) and t[i + 1] in LIG_NEXT:
            pos.append(i)
        i += 1
    if not pos:
        return t
    if any(pairs):
        if len(pos) != len(pairs):
            return t
        cut = [p for p, lig in zip(pos, pairs) if not lig]
    else:
        cut = pos
    for p in reversed(cut):
        t = t[:p + 1] + "{}" + t[p + 1:]
    return t


def latex_text(s: str, drop: frozenset = frozenset()) -> str:
    """A reading's line as pdflatex input: raw % and # (and & outside maths) escaped, mapped characters (maths mode
    tracked through $, \\( \\), \\[ \\]), compatibility forms through NFKC, characters in UNMAPPABLE or `drop`
    (those pdflatex reported it cannot set) removed, everything else unchanged."""
    s = "".join(unicodedata.normalize("NFKC", ch)              # compatibility forms first, so a full-width ％
                if ord(ch) > 126 and unicodedata.decomposition(ch).startswith(("<compat>", "<wide>", "<narrow>"))
                else ch for ch in s)                           # is escaped like any %
    s = "".join(ch for ch in s if ch not in UNMAPPABLE and ch not in drop)
    # a line's own \\begin{document} or \\end{document} would end the snippet's document inside its pline: pdflatex
    # then spun at full CPU and never returned (found 2026-11-21 testing the scan route with "...}\\end{document}")
    s = re.sub(r"\\(?:begin|end)\s*\{document\}", "", s)
    out, math, i = [], False, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 == len(s):           # a trailing backslash would eat the snippet's own \end{pline}
            out.append("\\textbackslash{}")         # (MathPix "(solution*\\"): the group ended inside it
            i += 1
            continue
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


#: LaTeX's own sizes (pt). A line within SNAP of one is set at it: microtype's font expansion draws a 12 pt design
#: at 11.88 and 12.12 pt (board lib:c9daf79960c3 p68), and fix-cm would take the 10 pt design for 11.88. Any other
#: size is kept, and fix-cm chooses its design by range, as the page did (p174: 9.63 pt -> SFRM0900, scripts CMR6)
STANDARD_PT = (5, 6, 7, 8, 9, 10, 10.95, 12, 14.4, 17.28, 20.74, 24.88)
SNAP = 0.015
SNAP_MIN = 0.001


#: maths italic letters, whose sizes on a page are its text, script and scriptscript sizes
MATHLETTER = r"CMMI|MTMI|MT2MI|EURM|rtxmi|ntxmi|NewTXMI|zplm|pxmi|LinLibertineI\d*$|LibertineMathMI"


def math_sizes(ac: list) -> str:
    """the page's own maths sizes, as \\DeclareMathSizes for its body size: the commonest maths-letter size between 0.65
    and 0.95 of the body size is its script size, the one below 0.9 of that its scriptscript size (or half the body
    size where the page has none: 5.5 pt under 9 pt is 0.61 of it). A package's sizes change between versions: newtxmath sets 9 pt scripts at 6.6 pt
    today and set them at 7.3 pt in 2018 (board 1805.01788 p3: 25 maths italics in LibertineMathMI7 at 6.575 bp
    against the page's LinLibertineI7 at 7.273, flagged as glyph findings). "" when the page has too few to tell,
    or when its maths is MathTime, which mathptmx only stands in for: at the page's sizes its Symbol letters move
    against their neighbours (board35 lib:c57ac038ae5f p293: an alpha out of order, two pieces of glyphs read as
    rules, both columns worse)."""
    if any(re.search(r"MTMI|MT2MI|MTSY|MTEX", c[7]) for c in ac):
        return ""
    body = collections.Counter(round(c[8], 3) for c in ac if c[8] > 0)
    if not body:
        return ""
    T = body.most_common(1)[0][0]
    ms = collections.Counter(round(c[8], 3) for c in ac if re.search(MATHLETTER, c[7].split("+")[-1]))
    s1 = [(n, z) for z, n in ms.items() if 0.65 * T <= z < 0.95 * T]
    if not s1 or max(s1)[0] < 5:
        return ""
    s1 = max(s1)[1]
    s2 = [(n, z) for z, n in ms.items() if 0.35 * T <= z < 0.9 * s1]
    s2 = max(s2)[1] if s2 and max(s2)[0] >= 3 else 0.5 * T
    pt = lambda z: round(z * 72.27 / 72, 2)
    t = set_size(T)
    return "\\DeclareMathSizes{%g}{%g}{%g}{%g}" % (round(t, 2), round(t, 2), pt(s1), pt(s2))


def size_voters(ac: list) -> tuple:
    """which page glyphs vote on their line's size (`vote_size`), and the page's size for a line none of whose glyphs
    votes: its commonest size among named fonts (10.0 with none). A nameless Type 3 font's size is the Tf size times
    its FontMatrix (dvips's bitmaps through Ghostscript 9.22: 0.12 for 10.95 pt CM, the glyph procedures 100 units to
    the em); the PDF defines no em for it, so it does not vote (T-0158 J2, arxiv.0910.1368 p12: its lines were set
    at 0.12 pt, and 545 of 590 bitmap glyphs flagged)"""
    voters = [not re.search(SIZEVOTE_SKIP, c[7]) and not TYPE3_NAMELESS.fullmatch(c[7]) for c in ac]
    named = collections.Counter(round(c[8], 4) for c in ac if c[8] > 0 and not TYPE3_NAMELESS.fullmatch(c[7]))
    return voters, (named.most_common(1)[0][0] if named else 10.0)


def vote_size(cs: list, formula: bool, default: float = 10.0) -> float:
    """a line's size from its glyphs' (bp): the commonest; on a formula line its TEXT size, since its scripts can
    outnumber its base (board 1805.01788 p3 L4, a_{i1}/r_{i1}: 12 glyphs at the script size, 11 at the text size; set
    at the script size, its letters and scripts were all too small). The text size is the largest size two glyphs
    hold whose scripts are there: glyphs at 0.35 to 0.95 of it, the range math_sizes reads a page's script sizes in
    (LaTeX's 10/7/5, newtxmath-Libertine's 8.966/7.273 = 0.81). A quarter of the glyphs was the rule until CMMI and CMSY
    voted (T-0158): a \\tfrac numerator of 23 script letters then outvoted the base's 5."""
    if not cs:                  # no glyph votes (a line wholly in nameless Type 3): the page's size, `default`
        return default
    c = collections.Counter(round(x, 4) for x in cs)
    mode = c.most_common(1)[0][0]
    if formula:                 # a base above its scripts; never a second family at the same size (board35 0864 p4
        held = [z for z, n in c.items() if n >= 2 and any(0.35 * z <= y < 0.95 * z for y in c)]  # L41: Nimbus 8.903
        big = max(held) if held else mode                                                       # and CMR9 8.966)
        return big if big >= 1.15 * mode else mode
    return mode


def set_size(sz: float) -> float:
    """a line's size (bp, as mutool reports it) as the size the judge sets it at (pt)"""
    pt = sz * 72.27 / 72
    near = min(STANDARD_PT, key=lambda s: abs(pt / s - 1))
    d = abs(pt / near - 1)
    return near if SNAP_MIN < d <= SNAP else pt    # within SNAP_MIN: rounding, kept as it was (p328's 10.0004)


#: pdfTeX writes ONE font object per TFM, whatever sizes use it, with /Widths from the size the document first set it
#: at (one decimal, so 527.7 at 10 pt and 527.6 at 10.4 pt): a group's snippets moved with the other jobs set before
#: them, and poppler draws by those widths (board batch, reversed order: 10 of 49 judge.out 1 px apart). Each pass
#: logs its fonts (tracefnt) and the next sets one glyph of every TFM at its design size on a first page.
FONTLOG = re.compile(r"External font `(\S+?)(?: at ?[\d.]+pt| scaled \d+)?' loaded as")
FIRSTCHAR = ("\\makeatletter\\def\\inkfirst#1{\\begingroup\\count@=0 \\loop\\ifnum\\count@<256 \\iffontchar#1\\count@"
             " #1\\char\\count@\\count@=256 \\fi\\advance\\count@ by 1 \\repeat\\endgroup}\\makeatother")


def prologue(tfms) -> str:
    """the first page: one glyph of each TFM, at its design size, in a fixed order"""
    return "\\begin{pline}" + "".join("\\font\\inkf=%s \\inkfirst\\inkf" % t for t in sorted(tfms)) + "\\end{pline}"


def compile_lines(texts: list, sizes: list, pre: str, wd: str, passes: int = 12) -> tuple[str, dict, set]:
    """All lines in one pdflatex run (one page per line). A character pdflatex reports it cannot set ("Unicode
    character ... (U+XXXX) not set up") is dropped from every line and the run is repeated; a line whose source raises
    any other LaTeX error is set aside (an empty page keeps the numbering) and the run is repeated.
    Returns (pdf path, {line index: error}, dropped characters). A group of one (`compile_group`)."""
    pdf, got = compile_group([(texts, sizes)], pre, wd, passes)
    return pdf, got[0][0], got[0][1]


#: seconds one pdflatex run of a group may take: a batch group of fifty pages takes well under a minute
PDFLATEX_TIMEOUT = 600


def compile_group(jobs: list, pre: str, wd: str, passes: int = 12) -> tuple[str, list]:
    """Several pages' lines, all set in one preamble, in one pdflatex run per pass (`inkcmp-judge --batch`: pdflatex's
    start-up is most of its time). Each job keeps its own rules: its own dropped characters (a character is dropped
    from the job's lines whose line raised it), its own set-aside lines, its own pass count -- a job that has
    settled, or used its passes, no longer changes. jobs: [(texts, sizes)]. Returns (pdf path, [(failed, dropped,
    first page)]); job j's line i is page first + i + 1."""
    failed = [dict() for _ in jobs]
    drop = [set() for _ in jobs]
    used = [0] * len(jobs)
    live = [True] * len(jobs)
    setas = [(dict(), set()) for _ in jobs]          # what each job's lines are set with: a job stopped by its pass
    tfms, more, rounds = set(), True, 0               # the fonts' first page (`prologue`): until it holds them all
    while any(live) or more:                          # limit keeps its last set lines (as a run of its own would)
        body, starts, owner_of = [], [], []
        pre_run = pre
        n = 5 + pre_run.count("\n") + 1                   # fix-cm, documentclass, usepackage, pre, begin{document}
        if tfms:
            body.append(prologue(tfms))
            n += 1
        for j, (texts, sizes) in enumerate(jobs):
            for i, (t, sz) in enumerate(zip(texts, sizes)):
                src = "\\mbox{}" if i in setas[j][0] else latex_text(t, frozenset(setas[j][1]))
                pt = set_size(sz)
                line = "\\begin{pline}\\fontsize{%.4f}{%.4f}\\selectfont %s\\end{pline}" % (pt, pt * 1.2, src)
                starts.append(n)
                owner_of.append((j, i))
                n += line.count("\n") + 1
                body.append(line)
        with open(os.path.join(wd, "lines.tex"), "w", encoding="utf-8") as fh:
            fh.write("\\RequirePackage{fix-cm}\n"     # designs by size range, as the page's own (p174)
                     "\\documentclass[multi=pline,border=2pt,varwidth=60cm]{standalone}\n"
                     "\\usepackage[loading]{tracefnt}\\usepackage[T1]{fontenc}\\usepackage{amsmath,amssymb,amsfonts}"
                     # \mathscr: RSFS, as a page that prints it sets it (board lib:b21e554b9232's rsfs10); it only
                     # declares the alphabet, a line without it is set as before (glyphdrill3-e4, 2026-11-26)
                     "\\usepackage{mathrsfs}" + FIRSTCHAR + "\n" + pre_run + "\n\\begin{document}\n" + "\n".join(body)
                     + "\n\\end{document}\n")
        env = None
        if PK_PRE in pre_run:                        # its map beside lines.tex; METAFONT's bitmaps in the job's folder
            with open(os.path.join(wd, PK_MAP), "w", encoding="latin-1") as fh:
                fh.write(pk_map())
            env = dict(os.environ, VARTEXFONTS=os.path.join(os.path.abspath(wd), "pk"))
        try:
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "lines.tex"], cwd=wd,
                           capture_output=True, timeout=PDFLATEX_TIMEOUT, env=env)
        except subprocess.TimeoutExpired:           # whatever made it loop: no line of the group is trusted
            for j, (texts, _) in enumerate(jobs):
                failed[j] = {i: "pdflatex timed out" for i in range(len(texts))}
            if os.path.exists(os.path.join(wd, "lines.pdf")):
                os.remove(os.path.join(wd, "lines.pdf"))
            break
        with open(os.path.join(wd, "lines.log"), encoding="utf-8", errors="replace") as fh:
            log = fh.read().splitlines()
        new = [dict() for _ in jobs]
        newdrop = [set() for _ in jobs]
        msg, uni = None, None
        for ln in log:
            m = LOGERR.match(ln)
            if m:
                msg, uni = m.group(1), None
                u = UNICODE_ERR.search(msg)
                if u:                                # a character, not the line: drop it from the job's lines
                    uni, msg = chr(int(u.group(1), 16)), None
                    if len(jobs) == 1:               # one job: no need to wait for the line it came from
                        newdrop[0].add(uni)
                        uni = None
                continue
            m = LOGLINE.match(ln)
            if m and (msg is not None or uni is not None):
                tl = int(m.group(1))
                src = INPUTLINE.search(msg) if msg is not None else None   # "\begin{pline} on input line 220 ..."
                if src:
                    tl = int(src.group(1))
                # an error reported at \end{document} (after every line) is no line's own: a line left open earlier
                # is what reached it, and the line that happened to come last in the group was booked for it (board
                # 49-job batch: lib:4859edad9294 p1287). Unbooked, the group's PDF comes out short and is set again
                idx = None if tl >= n else max((k for k, st in enumerate(starts) if st <= tl), default=None)
                if idx is not None:
                    j, i = owner_of[idx]
                    if uni is not None:
                        newdrop[j].add(uni)
                    elif i not in failed[j]:          # the group's line number is no part of the line's error
                        new[j].setdefault(i, INPUTLINE.sub("on its input line", msg))
                msg, uni = None, None
        got_t = {m[1] for m in FONTLOG.finditer("\n".join(log))}
        rounds += 1
        more = not got_t <= tfms and rounds < passes
        tfms |= got_t
        for j in range(len(jobs)):
            if not live[j]:
                continue
            used[j] += 1
            if newdrop[j] - drop[j]:                # characters first: their lines may compile once they are gone
                drop[j] |= newdrop[j]
            elif new[j]:
                failed[j].update(new[j])
            else:
                live[j] = False
            if used[j] >= passes:
                live[j] = False
            if live[j]:
                setas[j] = (dict(failed[j]), set(drop[j]))
    first, out = (1 if tfms else 0), []               # the fonts' page comes first
    for j, (texts, _) in enumerate(jobs):
        out.append((failed[j], drop[j], first))
        first += len(texts)
    return os.path.join(wd, "lines.pdf"), out


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
    groups, members = {}, {}
    for k, (y, x0, x1) in enumerate(runs):
        r = f(k)
        members.setdefault(r, []).append(k)
        if r in keep:
            groups.setdefault(r, []).extend((x, y) for x in range(x0, x1 + 1))
    out = []
    for r in sorted(members):                         # in the order of the first run; a frame as its two edges
        if r in keep:
            out.append(groups[r])
            continue
        for y0, y1 in frame_edges(box[r], [runs[k] for k in members[r]]):
            out.append([(x, y) for k in members[r] for y, x0, x1 in (runs[k],) if y0 <= y <= y1 for x in range(x0, x1 + 1)])
    return out


def frame_edges(b, rr) -> list:
    """a closed frame (a \\boxed display, \\fbox) as its top and bottom edges [(y0, y1), (y0, y1)], else []: its top and
    bottom bands each 1..6 rows of >= 90 % of its width, its left and right columns (within 6 px of its edges) inked on
    >= 90 % of its rows. A rendered frame is one connected rectangle that rule_like drops whole, where the page often
    draws four rules (J5: c57a p293's (13.9), 2 rules missing on a faithful \\boxed reading). C: inkcmp rules, the same"""
    w, h = b[2] - b[0] + 1, b[3] - b[1] + 1
    if w < 40 or h < 30:                          # a frame encloses at least a character (5a5c p4: a 16 px glyph remnant)
        return []
    rw, lh, rh = collections.Counter(), set(), set()
    for y, x0, x1 in rr:
        rw[y] += x1 - x0 + 1
        if x0 <= b[0] + 6: lh.add(y)
        if x1 >= b[2] - 6: rh.add(y)
    top, bot = b[1], b[3]
    while top <= b[3] and 10 * rw[top] >= 9 * w: top += 1
    while bot >= b[1] and 10 * rw[bot] >= 9 * w: bot -= 1
    tb, bb = top - b[1], b[3] - bot
    if 1 <= tb <= 6 and 1 <= bb <= 6 and top <= bot and 10 * len(lh & rh) >= 9 * h:
        return [(b[1], top - 1), (bot + 1, b[3])]
    return []


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


def rule_owner(comp, boxes, glyphs, inside_only=False):
    """the reading line (index into `boxes`) a page rule belongs to: the smallest box holding its centre; else the line
    of the glyph under it (an overline); else the nearest box it overlaps in x. None: no line (`inside_only`: outside
    every box)"""
    xs, ys = [p[0] for p in comp], [p[1] for p in comp]
    mnx, mxx, mxy = min(xs), max(xs), max(ys)
    cx, cy = (mnx + mxx + 1) / 2, (min(ys) + mxy + 1) / 2
    inside = [((q[2] - q[0]) * (q[3] - q[1]), li) for li, q in enumerate(boxes)
              if q[0] <= cx <= q[2] and q[1] <= cy <= q[3]]
    if inside:
        return min(inside)[1]
    if inside_only:             # one rectangle judged alone (rects.py): a rule outside it is no line's, neither the
        return None             # nearest line's nor that of a glyph below it (2103.01507 p118: a rule of the text
    below = []                  # line above, 58 px over the rectangle, went to its "(")                                         # an overline/vinculum: the line of the glyph under it
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
    for li, q in enumerate(boxes):
        if q[2] <= mnx or q[0] >= mxx + 1:
            continue
        d = max(q[1] - cy, 0, cy - q[3])
        key = (d, (q[2] - q[0]) * (q[3] - q[1]))
        if best is None or key < best[0]:
            best = (key, li)
    return best[1] if best else None


def table_rules(rules: list, page) -> set:
    """indices of rules that rule a table: long (>= 300 px) rules sharing their x span (within 3 px) with two or more
    long rules, or with one unless the two are a frame's top and bottom edges (its left and right sides inked on >= 90 % of the rows
    between them: J5's \\boxed). A table rule sits next to maths cells and passed page_rules' maths test (board
    lib:d68e0271bd4d p334: 8 booktabs rules, 2312.11532 p7: 4)"""
    aw, ah, ab = page
    ext = []
    for k, comp in enumerate(rules):
        xs, ys = [p[0] for p in comp], [p[1] for p in comp]
        if max(xs) - min(xs) + 1 >= 300:            # 1511.08771 p2709: two equal 225 px overlines are no table
            ext.append((k, min(xs), max(xs), min(ys), max(ys)))

    def inked(x, y0, y1):
        n = sum(1 for y in range(y0, y1 + 1) if any(0 <= x + d < aw and 0 <= y < ah and ab[y * aw + x + d] < INK
                                                    for d in range(-3, 4)))
        return n >= 0.9 * (y1 - y0 + 1)
    out = set()
    for a in ext:
        same = [b for b in ext if b[0] != a[0] and abs(a[1] - b[1]) <= 3 and abs(a[2] - b[2]) <= 3]
        if len(same) > 1:                            # three or more: a table, ruled or not (a frame has two edges;
            out.add(a[0])                            # a ruled table's verticals ink every pair's sides: d68e p334)
        elif same:
            b = same[0]
            top, bot = (a, b) if a[3] < b[3] else (b, a)
            if not (inked(min(a[1], b[1]), top[4] + 1, bot[3] - 1) and inked(max(a[2], b[2]), top[4] + 1, bot[3] - 1)):
                out.add(a[0])
    return out


def page_rules(comps: list, ac: list, page=None) -> list:
    """the page's rules, from the page alone (J9): of the rule-like components of the ink no page glyph quad covers
    ([(pixels, touch)] over the whole page), those free of glyph ink (touch <= 30 %) that are maths -- a maths-font glyph
    overlaps them in x within 1.5 of its heights above or below (a fraction bar, an overline, a vinculum, a frame's
    edge), or glyphs of any face lie within 1.5 heights above and below (a fraction of text digits, Libertine's maths),
    or glyphs within half their height span >= 80 % of it (an overline over text italic). Before, a page rule was
    looked for only in the reading's own formula lines and boxes, so a reading that typed a formula as text was not
    charged for its rules (2103.01507 p118: MathPix rules_a 0, ours 26)"""
    mf = MATHFONT + r"|MSBM|MSAM|EUF|EUS"
    gl = [(c[3] * S, c[4] * S, c[5] * S, c[6] * S, bool(re.search(mf, c[7]))) for c in ac if c[5] > c[3]]
    out = []
    for comp, touch in comps:
        if touch > 0.3 * len(comp):
            continue
        xs, ys = [p[0] for p in comp], [p[1] for p in comp]
        x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
        maths, above, below, tight = False, [], [], []
        for gx0, gy0, gx1, gy1, m in gl:
            if gx1 <= x0 or gx0 >= x1:
                continue
            gh = max(gy1 - gy0, 4.0)
            gap = max(gy0 - y1, y0 - gy1)
            if m and gap <= 1.5 * gh:
                maths = True
                break
            if gap <= 1.5 * gh:                       # 1805.01788 p3: a Libertine \\frac's denominator 25 px under
                (above if gy1 <= (y0 + y1) / 2 else below).append((max(gx0, x0), min(gx1, x1)))   # its bar, 22 px tall
            if gap <= 0.5 * gh:
                tight.append((max(gx0, x0), min(gx1, x1)))

        def cover(spans):
            n, end = 0.0, x0
            for a, b in sorted(spans):
                if b > end:
                    n += b - max(a, end)
                    end = b
            return n
        # a fraction's numerator or denominator spans most of its bar; a running head's rule has the page number over
        # it and the first line under it, 5 % of its width (RE4B-DE p559)
        # an overline or underline over text letters: a glyph within half its height of it, spanning >= 80 % of it
        # (lib:3fe71b4ef018 p328: Times-Italic Q-bars, 8 px over a 47 px Q, 40 of 47 px)
        if (maths or (above and below and max(cover(above), cover(below)) >= 0.5 * (x1 - x0))
                or cover(tight) >= 0.8 * (x1 - x0)):
            out.append(comp)
    if page is not None:
        drop = table_rules(out, page)
        out = [c for k, c in enumerate(out) if k not in drop]
    return out


#: how far a rendered glyph's own ink may poke out of its quad (px, 400 grid) and still be that glyph's on the J10 path:
#: measured 1.25 (0864 p4 L168, small-caps R and E set upright for a rotated label) and 1.11 (its minus); at 1 px they
#: were invented rules in two columns (board102)
OWN_PAD = 2


def inside_quads(comp, quads, pad) -> bool:
    """a component's pixels all inside one quad (x0, y0, x1, y1), each widened by `pad` px"""
    xs, ys = [p[0] for p in comp], [p[1] for p in comp]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    return any(q[0] - pad <= x0 and x1 <= q[2] + pad and q[1] - pad <= y0 and y1 <= q[3] + pad for q in quads)


#: characters whose whole glyph is a horizontal bar: on the rendered side, ink inside one's own quad is the glyph (J10)
DASHES = frozenset("-\u2010\u2011\u2012\u2013\u2014\u2212_") | {"&#x2010;", "&#x2013;", "&#x2014;", "&#x2212;"}


def rule_ink(i, b, ca, cb, pairs, acrop, bpage, res, out, pad=1, tol=2, boxes=None, glyphs=None, display=False,
             up=UP, stage=None, inside_only=False, a_page=None, a_owner=None):
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
    # a rendered rule moves by the x offset of the paired glyph nearest in x, and by the y offset of the glyphs on the
    # line's baseline (the page's commonest origin y): a fraction bar sits on the maths axis, while its nearest glyph
    # is its own numerator or denominator, which a reading of the wrong style moves (board34 lib:6d90e8d27b8b p174
    # L14: the page's \tfrac read as \frac, its four bars set 17 px above or 14 px below the page's single axis)
    def baseline_dy(bar, bars):
        """the y offset of the row a rendered bar sits over: the glyphs under no other bar than the ones enclosing
        it, grouped by page origin y, and of their rows (two glyphs at least, where any has two) the one nearest the
        bar on the rendered side when it is nested, its commonest row when it is not. A numerator or denominator
        is never a top-level bar's baseline (board
        lib:3fe71b4ef018 p328 L20: its big numerator is the commonest row, and its offset put the main bar 14 px
        low), and a fraction nested in a numerator takes the numerator's row (the same line's 1/4)."""
        x0, x1, yb = bar
        inside = lambda pb, q: pb[3] * S < q[1] and pb[5] * S > q[0]
        enclosing = [q for q in bars if q is not bar and q[0] <= x0 and q[1] >= x1]
        others = [q for q in bars if q is not bar and q not in enclosing]
        free = [(pa, pb) for pa, pb in real if not inside(pb, bar) and not any(inside(pb, q) for q in others)
                and all(inside(pb, q) for q in enclosing)] or real
        rows = collections.defaultdict(list)
        for pa, pb in free:
            rows[round(pa[2], 1)].append((pa, pb))
        if enclosing:                                # nested: the enclosing numerator's (or denominator's) row
            big = [r for r in rows.values() if len(r) >= 2] or list(rows.values())
            row = min(big, key=lambda r: abs(statistics.median(pb[2] * S for _, pb in r) - yb))
        else:                                        # top level: the line's commonest row outside every bar (an
            row = max(rows.values(), key=len)        # overline's superscript neighbours are not its row: 2103.01507)
        return statistics.median(pa[2] * S - pb[2] * S for pa, pb in row)

    def offset_at(xb, bar=None, bars=()):
        return min(offs, key=lambda o: abs(o[0] - xb))[1], baseline_dy(bar, bars)
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
        a_comps = stage("A", acov, (xa, ya, xb, yb)) if a_page is None else []
        b_comps = stage("B", bcov, (0, 0, PW - 1, PH - 1))
    else:
        a_comps, b_comps = rule_pixels(acrop, bpage, up, acov, bcov, (xa, ya, xb, yb))
    # inline maths: only rules over the maths -- overlapping a maths-font glyph in x (a footnote rule above a
    # "^3" line is not part of a formula); in display lines every rule counts
    # The page decides what is maths: a rendered glyph whose page partner is a maths glyph is maths too, whatever its
    # own face (MathTime is set by mathptmx in Times italic and Symbol: board18 lib:c57ac038ae5f p293 L5 and L27 read
    # their \frac bars missing)
    mf = MATHFONT + r"|MSBM|MSAM|EUF|EUS"
    mx_a = [(c[3] * S, c[5] * S) for c in ca if re.search(mf, c[7])]
    mx_b = ([(c[3] * S, c[5] * S) for c in cb if re.search(mf, c[7])]
            + [(pbb[3] * S, pbb[5] * S) for pa, pbb in pairs if re.search(mf, pa[7])])
    over_maths = lambda comp, mx: display or any(r[0] < max(p[0] for p in comp) + 1 and r[1] > min(p[0] for p in comp)
                                                for r in mx)
    # a piece of a rendered glyph's own ink that its partner's quad leaves out: inside the glyph's own quad and joined
    # to covered ink (p293: StandardSymL's alpha reaches 4 px above MathTime's, a 14 x 3 cap touching on 7 px). A rule
    # stands free of glyph ink (an overline under TeX's tall M quad: touch 0) or leaves the quad (a fraction bar).
    op = OWN_PAD if a_page is not None else pad       # J10 path: OWN_PAD; the render route keeps its pad
    own_b = [(c[3] * S, c[4] * S, c[5] * S, c[6] * S) for c in cb]
    glyph_piece = lambda comp, touch: touch > 0 and inside_quads(comp, own_b, op)
    b_rules = []
    if a_page is None:
        kept = [comp for comp, touch in b_comps
                if touch <= 0.3 * len(comp) and over_maths(comp, mx_b) and not glyph_piece(comp, touch)]
    else:                                   # J10: the page side's own rule (page_rules) on the rendered side, with the
        partner = {id(pbb): pa for pa, pbb in pairs}       # reading's glyphs; one whose page partner is maths-font is
        gb = [c if not (id(c) in partner and re.search(mf, partner[id(c)][7]))   # maths, as before. Bars over text
              else c[:7] + (partner[id(c)][7],) + c[8:] for c in cb]              # digits paired with nothing
        dash_b = [(c[3] * S, c[4] * S, c[5] * S, c[6] * S) for c in cb if c[0] in DASHES]
        in_dash = lambda comp: inside_quads(comp, dash_b, op)    # a misplaced hyphen's own ink (c9da p68 MathPix L23)
        kept = page_rules([(comp, touch) for comp, touch in b_comps if not glyph_piece(comp, touch)
                           and not in_dash(comp)], gb,
                          (pw, ph, pb) if up == 1 else None)       # (1511.08771 p2709's 8 overlines: 8 missing)
    bars = [(min(p[0] for p in c), max(p[0] for p in c) + 1, (min(p[1] for p in c) + max(p[1] for p in c) + 1) / 2)
            for c in kept]
    for comp, bar in zip(kept, bars):
        xs = [p[0] for p in comp]
        dx, dy = offset_at((min(xs) + max(xs)) / 2, bar, bars)
        b_rules.append([(round(x + dx), round(y + dy)) for x, y in comp])
    b_map = {p for comp in b_rules for p in comp}
    near = lambda pts, x, y: any((x + ddx, y + ddy) in pts for ddx in range(-tol, tol + 1) for ddy in range(-tol, tol + 1))
    a_rules = ([c for c, touch in a_comps if touch <= 0.3 * len(c) and over_maths(c, mx_a)] if a_page is None
               else a_page)                          # a_page: the page's rules, found once from the page (J9)
    a_map = {p for comp in a_rules for p in comp}
    owner = lambda comp: rule_owner(comp, boxes or [b], glyphs, inside_only)
    for k, comp in enumerate(a_rules):
        if boxes is not None and (owner(comp) if a_owner is None else a_owner[k]) != i:
            continue                                       # another line's rule (a_owner: once per page, J9)
        if any(covered(accq, x, y) for x, y in comp):
            if a_page is not None:
                res["rules_a"] += 1                        # a page rule all the same: explained, not missing
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

def same_letter(t: str, tb: str, sa: str, sb: str) -> bool:
    """two characters the faces can tell apart only by style: one unknown (U+FFFD, a glyph without a character map: the
    faces decide, as before), or one letter in two cases where a side is small caps (the page's small capital t and a
    reading's T: small caps lost, board 1805.01788 p3, Gemma4, 11 such t)"""
    if "&#xfffd;" in (t, tb):
        return True
    return "c" in sa + sb and len(t) == len(tb) == 1 and t.lower() == tb.lower()


def finding(t: str, tb: str, sa: str, sb: str, um: int) -> str:
    """what a flagged pair counts as: "glyph", "style" or nothing. Another character is a glyph finding whatever the two
    faces (T-0158 gate: a reading's b for the page's a, the page in LinLibertineI and the snippet in LibertineMathMI,
    read as style and left the residual 0); the same character in another face is style (bold or italic lost); the
    same character in the same face is a glyph finding unless the face cannot be set (unmeasurable)."""
    if t != tb and not same_letter(t, tb, sa, sb):
        return "" if um else "glyph"
    if sa != sb:
        return "style"
    return "" if um else "glyph"


def twins(pairs: list, ua: list, ub: list) -> tuple:
    """pairs of one character re-paired by place. The sequence pairing reads a line in x order, so the same character
    in two rows at one x (a fraction's numerator and denominator, a sum's upper and lower limit, a matrix's rows)
    pairs in whichever order the x sort left it, and each takes the other's partner (board 1805.01788 p3 L19: the l of
    both limits; lib:4859edad9294 p1287 L19: the commas of three rows); and a page glyph of the row above, inside the
    region, can take a rendered glyph whose own page partner is left unpaired (p1287 L25: u, b, r of "number" 14.4 pt
    above it). Per character, over its paired and unpaired glyphs, the nearest page and rendered glyphs pair first,
    the rendered ones carried into the page by the line's median offset, as many pairs as the character had, each
    rendered glyph only with a page glyph in its partner's column (half the glyph's size in x); kept only where that
    shortens the character's total distance. Glyphs it leaves out are unpaired."""
    if not pairs:
        return pairs, ua, ub
    dx, dy = (statistics.median(a[1] - b[1] for a, b in pairs), statistics.median(a[2] - b[2] for a, b in pairs))
    dist = lambda a, b: math.hypot(a[1] - b[1] - dx, a[2] - b[2] - dy)
    by = collections.defaultdict(list)
    for k, (a, b) in enumerate(pairs):
        if a[0] == b[0]:
            by[a[0]].append(k)
    spare_a, spare_b = collections.defaultdict(list), collections.defaultdict(list)
    for c in ua:
        spare_a[c[0]].append(c)
    for c in ub:
        spare_b[c[0]].append(c)
    out, ua, ub = list(pairs), list(ua), list(ub)
    for t, ks in by.items():
        if len(ks) + len(spare_a[t]) + len(spare_b[t]) < 2:      # one pair and no spare: nothing to choose
            continue
        A = [pairs[k][0] for k in ks] + spare_a[t]
        B = [pairs[k][1] for k in ks] + spare_b[t]
        # only page glyphs stacked at one x trade partners: a rendered glyph keeps to the column of its partner (a
        # spare one, of where the offset carries it); along a line the x order is right, and the offset drifts
        # where the reading's words differ (board 0864 p4 Gemma4 L45: v, a, e taken from their own words)
        col = [pairs[k][0][1] for k in ks] + [b[1] + dx for b in spare_b[t]]
        cand = sorted((dist(a, b), i, j) for i, a in enumerate(A) for j, b in enumerate(B)
                      if abs(a[1] - col[j]) <= 0.5 * max(a[8], 1.0))
        got_a, got_b, to = set(), set(), []
        for d, i, j in cand:
            if len(to) == len(ks):
                break
            if i not in got_a and j not in got_b:
                got_a.add(i)
                got_b.add(j)
                to.append((i, j))
        if len(to) < len(ks) or sum(dist(A[i], B[j]) for i, j in to) >= sum(dist(pairs[k][0], pairs[k][1]) for k in ks) - 1e-6:
            continue
        for k, (i, j) in zip(ks, to):
            out[k] = (A[i], B[j])
        ids_a = {id(A[i]) for i, _ in to}
        ids_b = {id(B[j]) for _, j in to}
        ua = [c for c in ua if id(c) not in ids_a] + [c for c in A if id(c) not in ids_a and c[0] == t and
                                                      not any(c is s for s in spare_a[t])]
        ub = [c for c in ub if id(c) not in ids_b] + [c for c in B if id(c) not in ids_b and c[0] == t and
                                                      not any(c is s for s in spare_b[t])]
    return out, ua, ub


def placed(pairs: list, near: int = 4, window: int = 24) -> list:
    """per pair, how far the reading set it above or below where the page says it belongs, as a share of the page
    glyph's size. A pair's vertical origin offset (page minus rendered, pt) is compared with two references: the
    median offset of its `near` nearest pairs on the page (centre distance, among the `window` pairs either side in
    page x order), and the median offset of the whole line; the share is the smaller of the two differences, signed.
    Each pair is compared lying on its own origin, so without this a script on the wrong side of its base reads as
    equal ink (T-0158 J1, arxiv.1710.04811: \\bigr)_{2} for the page's \\right)^2 scored 0). Both references,
    because either alone blames the wrong glyph: a sum whose limits moved is off from its neighbours (the limits) but
    not from the line (board 1805.01788 p3 L18); a region's second row set at another leading is off from the line
    but not from its neighbours. A line of fewer than 3 pairs has no neighbours to say so: all 0."""
    if len(pairs) < 3:
        return [0.0] * len(pairs)
    cen = lambda c: ((c[3] + c[5]) / 2, (c[4] + c[6]) / 2)
    order = sorted(range(len(pairs)), key=lambda k: cen(pairs[k][0])[0])
    rank = {k: r for r, k in enumerate(order)}
    off = [a[2] - b[2] for a, b in pairs]
    whole = statistics.median(off)
    out = []
    for k, (a, _) in enumerate(pairs):
        x, y = cen(a)
        r = rank[k]
        cand = [j for j in order[max(0, r - window):r + window + 1] if j != k]
        cand.sort(key=lambda j: (cen(pairs[j][0])[0] - x) ** 2 + (cen(pairs[j][0])[1] - y) ** 2)
        local = statistics.median(off[j] for j in cand[:near])
        d = min(off[k] - local, off[k] - whole, key=abs)
        out.append(d / max(a[8], 1.0))
    return out


def count(res, t: str, tb: str, sa: str, sb: str, fa: str, flag: bool, pl: float) -> str:
    """one judged pair into the page's counts: flagged by its ink, a glyph or style finding by `finding`; a pair
    whose ink agrees but which sits more than PLACE of its size off its place (`placed`) is a glyph finding and
    `misplaced`, in an unmeasurable face too: its place is read from the page's text, not from a font program.
    Returns what was counted for the pair, the G line's last field: g (glyph finding), s (style finding), - (none;
    so a flagged pair in a face TeX cannot set, J4)"""
    res["glyphs"] += 1
    um = unsettable(fa)
    res["unmeasurable"] += um
    if flag:
        res["flagged"] += 1
        kind = finding(t, tb, sa, sb, um)
        if kind:
            res[kind] += 1
        return {"glyph": "g", "style": "s"}.get(kind, "-")
    if abs(pl) > PLACE:
        res["flagged"] += 1
        res["misplaced"] += 1
        res["glyph"] += 1
        return "g"
    return "-"


def pbox(c, dx: float = 0.0, dy: float = 0.0) -> str:
    """a glyph's quad box (pt, shifted by dx dy) as "x0 y0 x1 y1" in the R lines' frame: the page at 400 dpi, whole
    pixels, outward"""
    return (f"{math.floor((c[3] + dx) * S)} {math.floor((c[4] + dy) * S)} "
            f"{math.ceil((c[5] + dx) * S)} {math.ceil((c[6] + dy) * S)}")

def coincident(pairs, ua, ub, ca, cb) -> tuple:
    """the last pass over a line's leftovers (J11): a page glyph and a rendered one of the same character whose quads,
    the rendered one carried into the page by the line's offset (b_to_a), overlap by at least half their union are a
    pair. The order-based pairings can leave such a pair behind when the reading orders a formula's parts otherwise
    (lib:3fe71b4ef018 p328 L20: its 2s paired crosswise, and the page's 2 and the reading's, 1 px apart, both unpaired)"""
    if not ua or not ub:
        return pairs, ua, ub
    dx, dy = b_to_a(pairs, ca, cb)
    pairs, ua, ub = list(pairs), list(ua), list(ub)
    for a in list(ua):
        best = None
        for b in ub:
            if b[0] != a[0]:
                continue
            x0, y0, x1, y1 = b[3] + dx, b[4] + dy, b[5] + dx, b[6] + dy
            iw = min(a[5], x1) - max(a[3], x0)
            ih = min(a[6], y1) - max(a[4], y0)
            if iw <= 0 or ih <= 0:
                continue
            inter = iw * ih
            union = (a[5] - a[3]) * (a[6] - a[4]) + (x1 - x0) * (y1 - y0) - inter
            iou = inter / union if union > 0 else 0.0
            if iou >= 0.5 and (best is None or iou > best[0]):
                best = (iou, b)
        if best is not None:
            pairs.append((a, best[1]))
            ua.remove(a)
            ub.remove(best[1])
    return pairs, ua, ub


def b_to_a(pairs, ca, cb) -> tuple:
    """the reading's frame -> the page's (pt) for one line: the median origin offset of its pairs; with no pair, the
    offset that lays the rendered glyphs' extent's top left on the page glyphs'"""
    if pairs:
        return (statistics.median(a[1] - b[1] for a, b in pairs), statistics.median(a[2] - b[2] for a, b in pairs))
    return (min(c[3] for c in ca) - min(c[3] for c in cb), min(c[4] for c in ca) - min(c[4] for c in cb))

def bands(cs: list) -> list:
    """a line's glyphs cut into horizontal bands: glyphs whose quads overlap in y chain into one band (scripts join
    their base); the bands top to bottom, each its glyphs in x order"""
    out = []
    for c in sorted(cs, key=lambda c: c[4]):
        if out and c[4] < out[-1][1]:
            out[-1][1] = max(out[-1][1], c[6])
            out[-1][2].append(c)
        else:
            out.append([c[4], c[6], [c]])
    return [sorted(b[2], key=lambda c: c[1]) for b in out]


def sequence_pairs(ca: list, cb: list) -> tuple:
    """the sequence pairing of two glyph orders: equal runs pair, and a replaced run of equal length pairs glyph for
    glyph (a different character is a finding); the rest is unpaired. Returns (pairs, unpaired A, unpaired B)"""
    pairs, ua, ub = [], [], []
    sm = difflib.SequenceMatcher(a=[c[0] for c in ca], b=[c[0] for c in cb], autojunk=False)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and a1 - a0 == b1 - b0):
            pairs += list(zip(ca[a0:a1], cb[b0:b1]))
        else:
            ua += ca[a0:a1]
            ub += cb[b0:b1]
    return pairs, ua, ub


def choose_order(ca: list, cb: list, rows_allowed: bool = True) -> tuple:
    """the x order's sequence pairing, or the printed rows' (`printed_rows`) where that pairs more glyphs; ties keep
    the x order. Returns (ca, cb, pairs, unpaired A, unpaired B, by_row)"""
    pairs, ua, ub = sequence_pairs(ca, cb)
    rows = printed_rows(ca, cb) if rows_allowed else None
    if rows:
        p2, a2, b2 = sequence_pairs(*rows)
        if len(p2) > len(pairs):
            return rows[0], rows[1], p2, a2, b2, 1
    return ca, cb, pairs, ua, ub, 0


def printed_rows(ca: list, cb: list) -> tuple | None:
    """both sides in row order when the line is several printed rows: the same number of bands (>= 2) on both sides,
    band for band of similar size (glyph counts within 30 %, or 2). Sorted by x alone, rows interleave, and a print
    that packs one row tighter than another (or than the judge sets it) interleaves them otherwise than the rendered
    line, so the sequence pairing leaves glyphs of every row unpaired on both sides (1511.08771 p2709 Table 560, an
    aligned cell of 4 rows: 77 / 77 unpaired). Bands that differ between the sides (a script chaining on one side
    only) keep the x order. The caller keeps the row order only where it pairs more glyphs than the x order (board
    lib:1a36bfe66b9c p154 Gemma4 L3: a prime in the main band on the page and above it in the reading left the page's
    phi and the reading's, equal ink, unpaired)."""
    ra, rb = bands(ca), bands(cb)
    if len(ra) < 2 or len(ra) != len(rb):
        return None
    if any(abs(len(x) - len(y)) > max(2, 0.3 * max(len(x), len(y))) for x, y in zip(ra, rb)):
        return None
    return [c for r in ra for c in r], [c for r in rb for c in r]


def judge(a_pdf: str, pno: int, lines_json: str, wd: str, out=sys.stdout, route: str = "render",
          compiler=None, setter: bool = True, peers: list | None = None,
          rules_inside: bool = False) -> collections.Counter:
    """one page. compiler(texts, sizes, preamble, wd) -> (snippet pdf, failed, dropped, first page) sets the
    reading's lines; by default one pdflatex run of this page's lines alone (`judge_batch` sets many pages' lines
    in one run and hands each page its part)"""
    if route == "scan":                              # a page without a glyph layer (inkdrill/scanjudge.py)
        from . import scanjudge
        return collections.Counter(scanjudge.judge_scan(a_pdf, pno, lines_json, wd, out=out))
    if _MEMO is None:
        _STEXT.clear()
    with open(lines_json, encoding="utf-8") as fh:
        doc = json.load(fh)
    pg = next((p for p in doc["pages"] if p.get("page") == pno), None) or doc["pages"][pno - 1]
    pw_pt, _ = memo(("size", a_pdf, pno), page_size, a_pdf, pno)
    k = pw_pt * S / pg["page_width"]                  # lines.json px -> 400-dpi px
    lv = leaves(pg)
    fam, pre = memo(("pre", a_pdf, pno), font_preamble, a_pdf, pno)
    spec_a = memo(("spec", a_pdf, pno), Spec, a_pdf, pno, pno, os.path.join(wd, "specA"), os.path.join(wd, "fontsA"))
    ac = spec_a.chars(pno, memo(("chars", a_pdf, pno), chars, a_pdf, pno, pno)[0])   # origins as the PDF places
    tr_a = spec_a.traced(pno, trace(a_pdf, pno, pno, spec_a.trace_text).get(pno, []))
    ac = spelled(identified(ac, tr_a), tr_a)
    ac = by_outline(ac)
    ms = math_sizes(ac)                              # the page's maths sizes, after its packages' own
    if ms and fam != "unmapped":
        pre = pre + "\n" + ms if pre else ms
    if fam != "unmapped" and any(g[1] in ("planckover2pi", "planckover2pi1") and "MSBM" in g[4] for g in
                                 trace(a_pdf, pno, pno, spec_a.trace_text).get(pno, [])):
        pre = pre + "\n" + MSBM_HBAR if pre else MSBM_HBAR
    def own(texts, sizes, pre, wd):                 # one page alone: a group of one, the fonts' page included
        pdf, got = compile_group([(texts, sizes)], pre, wd)
        return pdf, got[0][0], got[0][1], got[0][2]
    comp = compiler or own                           # (origins as the PDF places them)

    def box(ln):
        r = ln["region"]
        return (r["top_left_x"] * k, r["top_left_y"] * k, (r["top_left_x"] + r["width"]) * k,
                (r["top_left_y"] + r["height"]) * k)

    def inside(c, b):
        return b[0] <= (c[3] + c[5]) / 2 * S < b[2] and b[1] <= (c[4] + c[6]) / 2 * S < b[3]

    sizes, lfams, lpairs = [], [], []              # per line: its size, its glyphs' font names, its f pairs
    cgrid = collections.defaultdict(list)          # glyphs by the cell of their centre, in page order
    for ci, c in enumerate(ac):
        cgrid[(int((c[3] + c[5]) / 2 * S // 256), int((c[4] + c[6]) / 2 * S // 256))].append(ci)
    nomath, page_size_pt = size_voters(ac)
    for ln in lv:
        b = box(ln)
        near = sorted(ci for gx in range(int(b[0] // 256), int(b[2] // 256) + 1)
                      for gy in range(int(b[1] // 256), int(b[3] // 256) + 1) for ci in cgrid.get((gx, gy), ()))
        cs = [ac[ci][8] for ci in near if nomath[ci] and inside(ac[ci], b)]
        sizes.append(vote_size(cs, is_formula(ln), page_size_pt))
        lfams.append([ac[ci][7] for ci in near if inside(ac[ci], b)])
        lpairs.append(f_pairs([ac[ci] for ci in near if inside(ac[ci], b) and not re.search(MATHFONT, ac[ci][7])]))
    set_by = "pdflatex"
    texts = []
    for ln, fs, fp in zip(lv, lfams, lpairs):
        t, lf = line_tex(ln), line_family(fs, fam)
        t = t if ln.get("type") in ("math", "equation", "code") else unligature(t, fp)
        texts.append(t if lf is None else "{\\fontfamily{%s}\\selectfont %s}" % (lf, t))

    def reading_of(path):
        """a peer reading of this page: its leaf lines, their boxes (400 grid) and sizes, as for the own reading"""
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        p2 = next((q for q in d.get("pages", []) if q.get("page") == pno), None) or (
            d["pages"][pno - 1] if len(d.get("pages", [])) >= pno else None)   # as the judge picks its own page
        if p2 is None or not p2.get("page_width"):
            return None
        l2 = leaves(p2)
        if not l2 or not all(isinstance(x.get("region"), dict)
                             for x in leaves(p2, placed=False) if x["type"] != "page_info"):
            return None                              # not judged (no geometry): it chooses nothing
        k2 = pw_pt * S / p2["page_width"]
        b2 = [(x["region"]["top_left_x"] * k2, x["region"]["top_left_y"] * k2,
               (x["region"]["top_left_x"] + x["region"]["width"]) * k2,
               (x["region"]["top_left_y"] + x["region"]["height"]) * k2) for x in l2]
        s2 = []
        for x, b in zip(l2, b2):
            cs = [c[8] for c, v in zip(ac, nomath) if v and inside(c, b)]
            s2.append(vote_size(cs, is_formula(x), page_size_pt))
        return l2, b2, s2

    def try_set(l2, b2, s2, d2):
        """the reading set in the page's own font program, or None (inkdrill/setter.py)"""
        sst = collections.Counter()
        traced = trace(a_pdf, pno, pno, spec_a.trace_text).get(pno, [])
        traced_spec = spec_a.traced(pno, traced)
        line_ligs, line_gaps, line_tcs = [], [], []  # per line box: the page's ligature glyphs, word gap and
        adv_em = setter_mod.advances(spec_a.trace_text)   # letter spacing (em)
        for b in b2:
            # 8 px of slack in x as in y: a glyph's origin is the left edge of its ink, and a reading's box drawn
            # tight to the ink starts a fraction later (lib:69bc5c71b881 p16 L34: f_i at 340.0, the box at 340.8, so
            # the line's own ligature was not seen and the setter set f and i apart, J11)
            inl = sorted((g for g in traced_spec if b[0] - 8 <= g[2] * S < b[2] + 8 and b[1] - 8 <= g[3] * S < b[3] + 8
                          and g[0].strip()), key=lambda g: g[2])     # a drawn space glyph is part of the gap
            line_ligs.append({setter_mod.lig_of(g[1]) for g in inl if setter_mod.lig_of(g[1])})
            gaps, small = [], []
            for p, q in zip(inl, inl[1:]):
                em = abs(p[5][0]) or abs(p[5][1]) or 1.0
                gap = (q[2] - p[2]) / em - adv_em.get((p[4], p[1]), 0.5)
                if 0.15 < gap < 1.0:
                    gaps.append(gap)
                elif -0.15 < gap <= 0.15:
                    small.append(gap)
            tc = sorted(small)[len(small) // 2] if small else 0.0    # letter spacing (a line justified by Tc)
            line_tcs.append(tc)
            line_gaps.append((sorted(gaps)[len(gaps) // 2] if gaps else 0.25) - tc)
        os.makedirs(d2, exist_ok=True)
        return setter_mod.build(spec_a.trace_text, traced, Fonts(a_pdf, spec_a.fontdir, 0, extract=False),
                                [x["text"] for x in l2], s2, d2, style, MATHFONT, sst, line_ligs, line_gaps, line_tcs)

    # the route is the page's, not the reading's: every judged reading of this page (this one and its peers) must be
    # free of formula lines and settable, or pdflatex sets them all (coordinator, 2026-11-17: one instrument per page)
    # the setter sets every line in the page's one regular font: a page with text in a second family it cannot set
    # (judged by the page's own glyphs, so every reading of it takes the same route) goes to pdflatex, which sets
    # each line in its own family (line_family)
    second = any(line_family([f], fam) for f in {c[7] for c in ac})
    if setter and lv and not second:
        mine = (lv, [box(ln) for ln in lv], sizes)
        others = [r for r in (reading_of(pth) for pth in (peers or [])) if r is not None]
        if not any(is_formula(x) and not (x.get("type") not in ("math", "equation") and setter_mod.text_line(x["text"]))
                   for r in [mine] + others for x in r[0]):    # a footnote marker's maths is text to the setter
            ok = all(try_set(*r, os.path.join(wd, "peer-%d" % n)) is not None for n, r in enumerate(others))
            sp = try_set(*mine, wd) if ok else None
            if sp is not None:
                comp, set_by = (lambda texts, sz, pre, w: (sp, {}, set(), 0)), "setter"
    # a family TeX cannot set: only the setter, from the page's own font programs, can (board lib:69bc5c71b881 p16,
    # CircularStd); where it cannot either, the page is not judged
    if fam == "unmapped" and set_by != "setter":
        print(f"# page {pno}: family unmapped, {len(lv)} lines, A chars {len(ac)}", file=out)
        print(f"JUDGE1 a_glyphs {len(ac)} a_unpaired {len(ac)} accents_on_ink 0 b_unpaired 0 misplaced 0 flagged 0 glyph 0 rules_a 0 rules_invented 0 rules_missing 0 "
              f"glyphs 0 lines 0 not_compiled 0 style 0 unmappable 0 unmeasurable 0 unpaired_A 0 unpaired_B 0", file=out)
        print("STATUS FONT_UNMAPPED", file=out)
        return collections.Counter(lines=0, glyphs=0, flagged=0, style=0, glyph=0, not_compiled=0)
    side_a = None
    if route == "glyph":                            # the original's side runs while pdflatex sets the reading
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(comp, texts, sizes, pre, wd)
            side_a = memo(("side_a", a_pdf, pno), glyph_side_a, a_pdf, pno, wd, spec_a,   # once per batch page
                          any(is_formula(ln) for ln in lv))
            bpdf, failed, dropped, bo = fut.result()
    else:
        bpdf, failed, dropped, bo = comp(texts, sizes, pre, wd)
    gone = UNMAPPABLE | dropped
    side_b, fut_b = None, None
    if os.path.exists(bpdf):                        # the reading's trace, origins (and fonts) while its stext is read
        pool_b = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut_b = pool_b.submit(Spec, bpdf, bo + 1, bo + len(lv), os.path.join(wd, "specB"),
                              os.path.join(wd, "fontsB") if route == "glyph" else None)
        pool_b.shutdown(wait=False)
    # the reading set in PK bitmaps (PK_PRE) has no ToUnicode: its T1 codes are read from the TeX tree's tables
    pk = os.path.exists(os.path.join(os.path.dirname(bpdf) or ".", PK_MAP))
    bc = chars(bpdf, bo + 1, bo + len(lv), t1_text() if pk else None) if os.path.exists(bpdf) else []
    spec_b = fut_b.result() if fut_b is not None else None
    if not bc:
        spec_b = None
    if route == "glyph" and spec_b is not None:
        side_b = (Fonts(bpdf, os.path.join(wd, "fontsB"), len(side_a[0].files), extract=False),
                  trace(bpdf, bo + 1, bo + len(lv), spec_b.trace_text))
    if spec_b:
        tr_b = side_b[1] if side_b else trace(bpdf, bo + 1, bo + len(lv), spec_b.trace_text)
        bc = [by_outline(spelled(identified(spec_b.chars(bo + n + 1, cs), t), t))
              for n, cs in enumerate(bc) for t in (spec_b.traced(bo + n + 1, tr_b.get(bo + n + 1, [])),)]
    res = collections.Counter(lines=0, glyphs=0, flagged=0, style=0, glyph=0, not_compiled=0, unpaired_A=0,
                              unpaired_B=0, accents_on_ink=0, misplaced=0, unmappable=0, unmeasurable=0, rules_a=0,
                              rules_missing=0, rules_invented=0)
    b_total = 0                                     # rendered glyphs of the lines that typeset
    print(f"# page {pno}: family {fam}, {len(lv)} lines, A chars {len(ac)}, snippet pages {len(bc)}, set by {set_by}",
          file=out)
    if set_by == "setter":
        res["setter"] = 1
    if len(bc) != len(lv):            # the document itself did not come out page for page: no line is trusted
        failed = {i: failed.get(i, "no snippet page (document failed)") for i in range(len(lv))}
        bc = []
    for i, e in sorted(failed.items()):              # the line's region box last (the error text has spaces)
        q = box(lv[i])
        print(f"N L{i} NOT_COMPILED {e} {math.floor(q[0])} {math.floor(q[1])} {math.ceil(q[2])} {math.ceil(q[3])}",
              file=out)
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
    # a box that ends at the baseline leaves out what hangs below it: a comma's centre lies under the box its origin
    # is in (board37 1805.01788 p3 L54: ours' box to y 520, the commas' centres at 521; 38 commas unpaired in ours
    # where MathPix's deeper boxes paired them). Such a glyph belongs to the smallest box holding its origin, to
    # within one of the reading's pixels (its integer coordinates: L5's box ends 0.8 px above the comma's origin).
    for ci, c in enumerate(ac):
        if ci in owner:
            continue
        cx, oy = (c[3] + c[5]) / 2 * S, c[2] * S
        best = None
        for li in grid.get((int(cx // cell), int(oy // cell)), ()):
            b = boxes[li]
            if b[0] <= cx < b[2] and b[1] - k <= oy < b[3] + k:     # k: one reading pixel, its coordinates' rounding
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
    work, places = [], {}                                      # per judged line: (i, ln, b, ca, cb, pairs, U lines)
    for i, ln in enumerate(lv[:len(bc)]):
        if i in failed:
            continue
        b_total += len(bc[i])
        b = boxes[i]
        ca = sorted(of_line.get(i, ()), key=lambda c: c[1])
        u = along(ca, side_a[1]) if side_a is not None else None
        if u:                                       # a rotated line: its glyphs in the order they are written
            ca = sorted(ca, key=lambda c: c[1] * u[0] + c[2] * u[1])
        cb = sorted(bc[i], key=lambda c: c[1])
        if not ca or not cb:
            res["lines_empty"] += 1
            continue
        ca, cb, pairs, ua, ub, by_row = choose_order(ca, cb, rows_allowed=not u)
        res["lines_by_row"] += by_row
        ulines = []
        if is_formula(ln):                           # maths only: in prose the x order is the reading order
            pairs, ua, ub = repair(pairs, ua, ub, ca, cb)
            pairs, ua, ub = twins(pairs, ua, ub)
        pairs, ua, ub = coincident(pairs, ua, ub, ca, cb)
        res["unpaired_A"] += len(ua)
        res["unpaired_B"] += len(ub)
        # positions in the R lines' frame (the page at 400 dpi, px): the page glyph's quad box; a rendered glyph's
        # quad box carried into the page by the line's median pair offset (by its glyphs' extents when none paired)
        dx, dy = b_to_a(pairs, ca, cb)
        ulines += [f"U L{i} A {c[0]} {pbox(c)}" for c in ua]
        ulines += [f"U L{i} B {c[0]} {pbox(c, dx, dy)}" for c in ub]
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
        places[i] = [0.0] * len(pairs) if u else placed(pairs)   # a rotated line: not measured
        work.append((i, ln, b, ca, cb, pairs, ulines))
    scores, page400, bpages = {}, None, {}
    glyph_owners = [(c, owner[ci]) for ci, c in enumerate(ac) if ci in owner]
    if route == "glyph" and work:
        scores, page400, bpages, gnames = glyph_scores(side_a, side_b, spec_b, bpdf, ac, work, wd, res, bo)
    stages = {}
    if route == "glyph" and work:                   # rule_ink's pixel stage for every formula line in one C call:
        aw, ah, ab, a400 = page400.get()
        # a first pass collects the requests, the second reads them; the page's own rules are one request (J9)
        reqs, where = [((a400, INK, 1, 0, 0), [(c[3] * S - 1, c[4] * S - 1, c[5] * S + 1, c[6] * S + 1) for c in ac],
                        (0, 0, aw - 1, ah - 1))], {}

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
                         stage=collect(i), inside_only=rules_inside, a_page=[])
        got = rules_job(reqs, wd)
        a_page = page_rules(got[0], ac, (aw, ah, ab))
        for i, *_ in work:
            stages[i] = (lambda i: lambda side, quads, reg: got[where[(i, side)]])(i)
        a_owner = [rule_owner(c, boxes, glyph_owners, rules_inside) for c in a_page]
    for i, ln, b, ca, cb, pairs, ulines in work:
        for u in ulines:
            print(u, file=out)
        if route == "glyph":
            for k, ((t, ax, ay, qx0, qy0, qx1, qy1, fa, *_), (tb, bx, by, _, _, _, _, fb, *_)) in enumerate(pairs):
                r = scores[(i, k)]
                if isinstance(r, str):                  # no outline on the reading's side: not judged
                    res["no_outline"] += 1
                    print(f"O L{i} {t} {tb} {r}", file=out)
                    continue
                xo, so, pc = r[:3]
                holes = r[3:5] if len(r) >= 5 and min(r[3:5]) >= 0 else None
                # a glyph whose outline has another number of holes is another glyph, however thin the stroke that
                # makes the difference (e -> c: the crossbar, solid 4 at 10 pt)
                flag = so >= THRESHOLD or (holes is not None and holes[0] != holes[1])
                ga, gb = gnames.get((i, k), ("", ""))
                sa, sb = face(fa, ga), face(fb, gb)
                if maths_digit(t, tb, fa, fb, fam) and "x" not in sa:
                    sa = "x" if sa == "-" else sa + "x"  # a digit the page set as maths, the reading as text
                pl = places[i][k]
                kind = count(res, t, tb, sa, sb, fa, flag, pl)
                print(f"G L{i} {t} {tb} {int(t == tb)} {xo} {so} {pc} {int(flag or abs(pl) > PLACE)} {sa} {sb} {fa} "
                      f"{pbox((t, ax, ay, qx0, qy0, qx1, qy1))} {round(pl, 3) + 0.0:.3f} {kind}", file=out)
            if is_formula(ln) and pairs:
                aw, ah, ab, _ = page400.get()
                rule_ink(i, b, ca, cb, pairs, (aw, ah, ab, 0, 0), bpages[i][:3], res, out, boxes=boxes,
                         glyphs=glyph_owners, display=ln.get("type") in ("math", "equation"), up=1, stage=stages[i],
                         inside_only=rules_inside, a_page=a_page, a_owner=a_owner)
            continue
        pw, ph, pb = pgm(bpdf, bo + i + 1, DPI * UP)
        mg = 240                                              # 30 px at 400 dpi: ink above a tight box (accents)
        cx0, cy0 = max(0, int(b[0]) * UP - mg), max(0, int(b[1]) * UP - mg)
        aw, ah, ab = pgm(a_pdf, pno, DPI * UP, (cx0, cy0, int(b[2] - b[0]) * UP + 2 * mg, int(b[3] - b[1]) * UP + 2 * mg))
        for k, ((t, ax, ay, qx0, qy0, qx1, qy1, fa, *_), (tb, bx, by, _, _, _, _, fb, *_)) in enumerate(pairs):
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
            pl = places[i][k]
            kind = count(res, t, tb, sa, sb, fa, flag, pl)
            print(f"G L{i} {t} {tb} {int(t == tb)} {xo} {so} {pc} {int(flag or abs(pl) > PLACE)} {sa} {sb} {fa} "
                  f"{pbox((t, ax, ay, qx0, qy0, qx1, qy1))} {round(pl, 3) + 0.0:.3f} {kind}", file=out)
        if is_formula(ln) and pairs:
            rule_ink(i, b, ca, cb, pairs, (aw, ah, ab, cx0, cy0), (pw, ph, pb), res, out, boxes=boxes,
                     glyphs=glyph_owners,
                     display=ln.get("type") in ("math", "equation"), inside_only=rules_inside)
    if route == "glyph" and work:                   # a page rule no formula line of the reading judged: missing (J9)
        judged = {i for i, ln, b, ca, cb, pairs, _ in work if is_formula(ln) and pairs}
        for comp, li in zip(a_page, a_owner):
            if li in judged or (li is None and rules_inside):
                continue
            res["rules_a"] += 1
            res["rules_missing"] += 1
            xs, ys = [p[0] for p in comp], [p[1] for p in comp]
            print(f"R L{'-' if li is None else li} missing {min(xs)} {min(ys)} {max(xs) + 1} {max(ys) + 1} {len(comp)}",
                  file=out)
    # page-wide counts for the board's ink residual (coordinator, 2026-11-17): every original glyph that paired
    # with nothing (outside any line, in an empty or uncompiled line, or skipped by the alignment) and every
    # rendered glyph with no partner
    res["a_glyphs"] = len(ac)
    res["a_unpaired"] = len(ac) - (res["glyphs"] - res["accents_on_ink"])   # accents on ink pair no A glyph
    res["b_unpaired"] = b_total - res["glyphs"]
    if route == "glyph" and _MEMO is None:         # the page renders were intermediate (about 15 MB a page); a
        for f in os.listdir(wd):                    # batch shares one page's render between jobs: removed at its end
            if re.fullmatch(r"a400\.pgm|b\d+-\d+\.pgm", f):
                os.remove(os.path.join(wd, f))
    print("JUDGE1 " + " ".join(f"{k} {v}" for k, v in sorted(res.items())), file=out)
    status = "OK" if res["glyphs"] else ("NOT_COMPILED" if res["not_compiled"] else "NO_TEXT")
    print(f"STATUS {status}", file=out)          # a page with 0 pairs has no residual
    return res


class _Need(Exception):
    """judge_batch's first pass: the page's lines are known, stop before setting them"""


def judge_batch(jobs: list, route: str = "render", bwd: str | None = None, setter: bool = True,
                rules_inside: bool = False) -> dict:
    """Many pages in one process (`inkcmp-judge --batch`): the reading's lines of all pages that share a preamble are
    set in ONE pdflatex run (compile_group); its start-up was most of the 0.40 s it cost per page. jobs:
    [(original pdf, page, lines.json, work dir)]; each job's judge output goes to <work dir>/judge.out, as one
    `inkcmp-judge ... --work <work dir>` would print it. A group whose PDF does not come out page for page is set again
    page by page. Returns counts."""
    global _MEMO
    _MEMO = {}
    stats = collections.Counter(jobs=len(jobs))
    bwd = bwd or tempfile.mkdtemp(prefix="inkcmp-judge-batch-")
    os.makedirs(bwd, exist_ok=True)

    def finish(j, compiler):
        a, p, lj, wd = jobs[j][:4]
        pe = jobs[j][4] if len(jobs[j]) > 4 else None
        buf = io.StringIO()
        err = ""
        try:
            judge(a, p, lj, wd, out=buf, route=route, compiler=compiler, setter=setter, peers=pe,
                  rules_inside=rules_inside)
        except _Need:
            raise
        except Exception:
            import traceback
            err = traceback.format_exc()
            stats["errors"] += 1
        with open(os.path.join(wd, "judge.out"), "w", encoding="utf-8") as fh:
            fh.write(buf.getvalue())
            if err:
                fh.write("\n--- stderr ---\n" + err)
    try:
        reqs = {}
        for j, job in enumerate(jobs):
            os.makedirs(job[3], exist_ok=True)

            def collect(texts, sizes, pre, w, j=j):
                reqs[j] = (texts, sizes, pre)
                raise _Need()
            try:
                finish(j, collect)                   # pages that need no snippet (family unmapped) finish here
            except _Need:
                pass
        groups = collections.defaultdict(list)
        for j, (texts, sizes, pre) in reqs.items():
            if texts:
                groups[pre].append(j)
        given = {}
        for g, (pre, js) in enumerate(groups.items()):
            gwd = os.path.join(bwd, "group-%d" % g)
            os.makedirs(gwd, exist_ok=True)
            pdf, got = compile_group([reqs[j][:2] for j in js], pre, gwd)
            want = sum(len(reqs[j][0]) for j in js) + (got[0][2] if got else 0)   # + the fonts' page
            info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout if os.path.exists(pdf) else ""
            m = re.search(r"Pages:\s+(\d+)", info)
            if not m or int(m[1]) != want:           # one page's fatal error took the group down: page by page
                stats["groups_set_again"] += 1
                continue
            stats["groups"] += 1
            for j, (failed, dropped, first) in zip(js, got):
                given[j] = (pdf, failed, dropped, first)
        for j in reqs:
            finish(j, (lambda *a, v=given[j]: v) if j in given else None)
            stats["batched" if j in given else "alone"] += 1
    finally:
        _MEMO = None
        for job in jobs:                            # the page renders the jobs shared (glyph route)
            if os.path.isdir(job[3]):
                for f in os.listdir(job[3]):
                    if re.fullmatch(r"a400\.pgm|b\d+-\d+\.pgm", f):
                        os.remove(os.path.join(job[3], f))
    return stats


#: the CLI's options that take a value, and their defaults
OPTIONS = {"--peers": "", "--rects": None, "--batch": None, "--work": None, "--route": "render"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opt = dict(OPTIONS)
    for name in OPTIONS:
        if name in argv:
            j = argv.index(name)
            if j + 1 == len(argv):
                print(f"inkcmp-judge: {name} needs a value", file=sys.stderr)
                return 2
            opt[name] = argv[j + 1]
            del argv[j:j + 2]
    use_setter = "--no-setter" not in argv
    argv = [a for a in argv if a != "--no-setter"]
    wd, route, batch = opt["--work"], opt["--route"], opt["--batch"]
    peers = [x for x in opt["--peers"].split(",") if x]
    if route not in ("glyph", "render", "scan"):
        print("inkcmp-judge: --route glyph|render|scan", file=sys.stderr)
        return 2
    if opt["--rects"] is not None:              # one residual per rectangle (T-0158, inkdrill/rects.py)
        if argv or not wd:
            print("inkcmp-judge --rects RECTS.tsv --work DIR: one row per rectangle, source doc page obj_id x0 y0 x1 y1"
                  " latex pdf (box in pt, CropBox frame, y down)", file=sys.stderr)
            return 2
        from . import rects
        out = rects.judge_rects(opt["--rects"], wd)
        st = collections.Counter(o["status"] for o in out)
        print("RECTS1 rows %d " % len(out) + " ".join(f"{k} {v}" for k, v in sorted(st.items()))
              + " out " + os.path.join(wd, "rects.out.tsv"))
        return 0
    if batch is not None:
        if argv:
            print("inkcmp-judge --batch JOBS.tsv [--route glyph|render] [--work DIR]: one job per line,"
                  " pdf<TAB>page<TAB>lines.json<TAB>work dir", file=sys.stderr)
            return 2
        with open(batch, encoding="utf-8") as fh:
            jobs = [(f[0], int(f[1]), f[2], f[3]) + ((f[4].split(","),) if len(f) > 4 and f[4] else ())
                    for f in (l.rstrip("\n").split("\t") for l in fh) if len(f) >= 4 and not f[0].startswith("#")]
        st = judge_batch(jobs, route=route, bwd=wd, setter=use_setter)
        print("BATCH1 " + " ".join(f"{k} {v}" for k, v in sorted(st.items())))
        return 0
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    a_pdf, pno, lj = argv[0], int(argv[1]), argv[2]
    if wd:
        os.makedirs(wd, exist_ok=True)
        judge(a_pdf, pno, lj, wd, route=route, setter=use_setter, peers=peers)
    else:
        with tempfile.TemporaryDirectory(prefix="inkcmp-judge-") as d:
            judge(a_pdf, pno, lj, d, route=route, setter=use_setter, peers=peers)
    return 0


if __name__ == "__main__":
    sys.exit(main())

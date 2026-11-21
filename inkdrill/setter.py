"""setter.py -- a reading's text lines set in the page's own font program, without pdflatex (CONTRACT.md unit 5).

Why: pdflatex sets a reading in TeX's fonts. Where the page is Times-Roman, TeX's Times is Nimbus, whose glyphs differ:
the judge flagged correct characters (board lib_88d76967110e p170: `l`, xor 270 against 0 in the page's own font).
On a page without formula lines this module sets each line in the page's regular text font instead (rule-6
experiment A, tools/judge_setter.py: 99.79% of flags equal on the 6 text-only board pages at 0.02-0.07 CPU s).

How: one PDF page per reading line, as compile_lines makes them, so the judge reads it unchanged.
- The font: the page's regular text font (style '-', not maths) with the most traced glyphs.
- A group in \\textbf, \\textit, \\texttt or \\textsc (pdfdrill 35340ad3 writes the face into the text): the
  page's font of that face with the most traced glyphs, as pdflatex honours the command; a face the page has no
  program of is set in the regular font, the command stripped and its argument kept. Never drawn as characters.
- A text superscript (a footnote marker, written as maths: ${}^{30}$, or \\textsuperscript): set in its face's font
  at the page's own superscript scale and rise (sup_metrics), so a line holding one is not a formula line.
- Each character: the glyph the page itself traces for it in that font, with the page's advance (trace `adv`) and
  the page's own kerning between glyph pairs (learned from consecutive traced glyphs); ligatures where the page uses
  them; a space advances 0.25 em.
- A character the subset program does not hold: the same glyph from the TeX tree's Type 1 of the same font
  (pdftex.map; U9's premise that the embedded outline is the on-disk one), with its own advance.
- A character neither holds (a commercial font has no TeX-tree copy, a name this table does not know): `build`
  returns None and the judge sets the page with pdflatex as before.
"""
from __future__ import annotations

import collections
import html
import os
import re
import subprocess

from . import charstring, type1

LIGS = ("ffi", "ffl", "ff", "fi", "fl")


def lig_of(name: str) -> str | None:
    """the ligature a glyph name stands for: fi, or f_i as fonts that name a ligature by its letters do (board
    lib:69bc5c71b881 p16, CircularStd: f_f, f_i, f_f_i, f_l); None for any other glyph"""
    if name in LIGS:
        return name
    parts = (name or "").split("_")
    j = "".join(parts)
    return j if len(parts) > 1 and all(len(q) == 1 for q in parts) and j in LIGS else None
NAME = {".": "period", ",": "comma", ":": "colon", ";": "semicolon", "-": "hyphen", "(": "parenleft",
        ")": "parenright", "[": "bracketleft", "]": "bracketright", "/": "slash", "?": "question", "!": "exclam",
        "'": "quoteright", "’": "quoteright", "‘": "quoteleft", "“": "quotedblleft",
        "”": "quotedblright", "–": "endash", "—": "emdash", "%": "percent", "&": "ampersand",
        "*": "asterisk", "+": "plus", "=": "equal", "#": "numbersign", "$": "dollar", "@": "at", '"': "quotedbl",
        "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
        "8": "eight", "9": "nine", "é": "eacute", "è": "egrave", "ä": "adieresis",
        "ö": "odieresis", "ü": "udieresis", "ß": "germandbls", "Ä": "Adieresis",
        "Ö": "Odieresis", "Ü": "Udieresis", "_": "underscore", "{": "braceleft", "}": "braceright",
        "<": "less", ">": "greater", "|": "bar", "\\": "backslash", "~": "asciitilde", "^": "asciicircum",
        "`": "grave", "\u2192": "arrowright", "\u00b7": "periodcentered", "\u2022": "bullet", "\u00a7": "section",
        "\u00b0": "degree", "\u00e1": "aacute", "\u00e0": "agrave", "\u00f3": "oacute", "\u00ed": "iacute",
        "\u00fa": "uacute", "\u00f1": "ntilde", "\u00e7": "ccedilla",
        "\u2122": "trademark", "\u00a9": "copyright", "\u00ae": "registered", "\u2026": "ellipsis", "\u20ac": "Euro"}
_MAP = None
#: the TeX-tree fonts pdflatex's family mapping uses for a page font with no copy of its own (judge.FAMILIES). Used
#: only for characters the page's subset lacks: such a character never pairs with the same character in the same
#: font (the page would have printed it), so the substitute meets only pairs that differ anyway.
SUBSTITUTE = [(r"Times|Nimbus.*Rom|ptmr|utmr", "NimbusRomNo9L-Regu"), (r"Helvetica|Arial|NimbusSan|phvr|uhvr",
              "NimbusSanL-Regu"), (r"Palatino|Palladio|pplr|uplr", "URWPalladioL-Roma")]


def glyph_name(c: str) -> str | None:
    if c.isascii() and c.isalpha():
        return c
    return NAME.get(c)


#: the substitute for a character the page's program lacks in a face SUBSTITUTE does not name. Like every substitute
#: it only meets pairs that differ anyway; without it one such character (MathPix's straight ' and its (TM) on a
#: CircularStd page) kept every reading of the page from being set, and the page from being judged at all.
LAST_RESORT = "NimbusSanL-Regu"


def tex_type1(font: str) -> str | None:
    """the TeX tree's Type 1 program of a font, by its PostScript name (pdftex.map, no re-encoded or extended
    variants)"""
    global _MAP
    if _MAP is None:
        _MAP = {}
        p = subprocess.run(["kpsewhich", "pdftex.map"], capture_output=True, text=True).stdout.strip()
        if p:
            for ln in open(p, encoding="latin-1"):
                f = ln.split()
                m = re.search(r"<<?(\S+\.pfb)", ln)
                if len(f) >= 2 and m and not re.search(r"ExtendFont|SlantFont", ln):   # glyphs are taken by name:
                    # a re-encoded entry is the same program (utmr8a.pfb behind every NimbusRomNo9L-Regu line)
                    _MAP.setdefault(f[1], m[1])
    base = font.split("+")[-1]
    names = [base, re.sub(r"\d+$", "", base)]
    for pat, sub in SUBSTITUTE:                     # no TeX-tree copy (a commercial font): what pdflatex sets instead
        if re.search(pat, base):
            names.append(sub)
    names.append(LAST_RESORT)                       # a face no entry names (CircularStd: board lib:69bc5c71b881 p16)
    for name in names:
        if name in _MAP:
            path = subprocess.run(["kpsewhich", _MAP[name]], capture_output=True, text=True).stdout.strip()
            return path or None
    return None


def advances(trace_text: str) -> dict:
    """(font, glyph) -> advance in em, as `mutool trace` reports it"""
    out, cur = {}, None
    for m in re.finditer(r'<span font="([^"]+)"|<g unicode="[^"]*" glyph="([^"]*)" x="[^"]+" y="[^"]+" adv="([^"]+)"',
                         trace_text):
        if m[1]:
            cur = m[1]
        else:
            out.setdefault((cur, m[2]), float(m[3]))
    return out


#: the text-face commands a reading writes (pdfdrill 35340ad3 writes \\textbf{..} and kin into lines.json text): the
#: setter sets the group in the page's font of that face, as pdflatex does
FACES = {"textbf": "b", "textit": "i", "texttt": "m", "textsc": "c", "textsuperscript": "s"}
#: a text superscript as readings write it: maths holding only a superscript of plain characters (a footnote marker
#: ${}^{30}$, \\({ }^{30}\\), $^{30}$; board lib:69bc5c71b881 p16, CircularStd: both columns write them so), set as
#: \\textsuperscript, which `faced` reads as the face "s"
SUP = re.compile(r"(?:\$|\\\()\s*(?:\{\s*\})?\s*\^\s*\{([^{}$\\]*)\}\s*(?:\$|\\\))")


def supered(t: str) -> str:
    """a line's text superscripts as \\textsuperscript{...}"""
    return SUP.sub(lambda m: "\\textsuperscript{%s}" % m[1].strip(), t)


def text_line(t: str) -> bool:
    """a line with no maths left once its text superscripts are read as text: the setter can set it"""
    return not re.search(r"\$|\\\(|\\\[", supered(t))


def faced(t: str) -> list:
    """a reading's text line as [(face, text)]: the four text-face commands read as faces (style() letters, nested
    faces combined: \\textbf{\\textit{..}} is "bi"), their braces gone; escapes and any other command stay text"""
    out, stack, buf, i = [], [], [], 0

    def face():
        return "".join(c for c in "mbics" if any(c in f for f in stack if f))

    def flush():
        if buf:
            out.append((face(), "".join(buf)))
            buf.clear()
    while i < len(t):
        m = re.match(r"\\(textbf|textit|texttt|textsc|textsuperscript)\s*\{", t[i:])
        if m:
            flush()
            stack.append(FACES[m[1]])
            i += m.end()
        elif t[i] == "\\" and i + 1 < len(t):
            buf.append(t[i:i + 2])
            i += 2
        elif t[i] == "{":
            flush()
            stack.append("")
            i += 1
        elif t[i] == "}" and stack:
            flush()
            stack.pop()
            i += 1
        else:
            buf.append(t[i])
            i += 1
    flush()
    return out


def plain(t: str) -> str:
    """a text line of the reading as characters (the escapes MathPix's text lines use, entities)"""
    t = re.sub(r"\\([%&$#_{}])", r"\1", t)
    t = t.replace("``", "“").replace("''", "”").replace("---", "—").replace("--", "–")
    return html.unescape(t)


def _pdf_string(codes) -> str:
    return "(" + "".join("\\%03o" % c for c in codes) + ")"


def sup_metrics(traced: list) -> tuple:
    """the page's text superscripts: (scale, rise in em of the body size), the medians over its glyphs at 0.45-0.85
    of the body size whose baseline is above that of the body-size glyphs beside them; (0.7, 0.35) on a page with
    none, where a reading's superscript has nothing to pair with anyway"""
    size = lambda g: abs(g[5][3]) or abs(g[5][0])
    sizes = collections.Counter(round(size(g), 2) for g in traced if g[0].strip())
    if not sizes:
        return 0.7, 0.35
    body = sizes.most_common(1)[0][0]
    big = [g for g in traced if abs(size(g) - body) <= 0.02 * body]
    scales, rises = [], []
    for g in traced:
        z = size(g)
        if not (0.45 * body <= z <= 0.85 * body) or not g[0].strip():
            continue
        near = sorted(b[3] for b in big if abs(b[2] - g[2]) < 3 * body and abs(b[3] - g[3]) < body)
        if not near:
            continue
        up = (g[3] - near[len(near) // 2]) * (1 if g[5][3] > 0 else -1)   # trace's y may run down: d's sign says
        if up > 0.1 * body:
            scales.append(z / body)
            rises.append(up / body)
    if not scales:
        return 0.7, 0.35
    return sorted(scales)[len(scales) // 2], sorted(rises)[len(rises) // 2]


def build(trace_text: str, traced: list, fonts, texts: list, sizes: list, wd: str, style, mathfont: str,
          stats: collections.Counter, line_ligs: list | None = None, line_gaps: list | None = None,
          line_tcs: list | None = None) -> str | None:
    """the snippet PDF (one page per line), or None when a character has no glyph in either program.
    traced: the page's glyphs [(char, glyph, x, y, font, matrix)]; fonts: judge.Fonts of the page (files written);
    line_ligs: per line, the ligature glyphs the page itself uses inside that line's box -- a ligature is set only
    where the page set one (2311.09533 p10 sets "f" "i" apart on lines 38 and 80 and "fi" elsewhere); line_gaps: per
    line, the page's median word gap there in em (a justified line's spaces are wider than 0.25 em, and a window
    holds its neighbour's ink: lib_88d76967110e p170 line 52, "repeated dosing"); line_tcs: per line, the page's
    letter spacing in em (the same line is also justified by Tc: d->o 5.20 pt on the page, 4.85 without it). The
    learned kerning is net of each baseline's own letter spacing."""
    count = collections.Counter(g[4] for g in traced)
    text_fonts = [f for f, _ in count.most_common() if not re.search(mathfont, f)]
    regular = [f for f in text_fonts if not style(f.split("+")[-1])]
    if not regular:
        return None
    R = regular[0]
    progs = {}

    def program(F):
        """a page font's own program and what the page shows of it: glyphs by character, advances, kerning"""
        if F in progs:
            return progs[F]
        ids = fonts.find(F)
        if not ids:
            progs[F] = None
            return None
        path = fonts.ids()[ids[0]]
        uni, lig_names, lig_glyph = {}, set(), {}
        for g in traced:
            if g[4] == F:
                if lig_of(g[1]):                     # traced under its first letter: kept apart, never for that letter
                    lig_names.add(lig_of(g[1]))
                    lig_glyph[lig_of(g[1])] = g[1]   # its own name in this program (fi or f_i)
                else:
                    uni.setdefault(html.unescape(g[0]), g[1])
        adv, kern_obs, prev, cur, em = {}, collections.defaultdict(collections.Counter), None, None, 1.0
        kern_raw = []
        for m in re.finditer(r'<span font="([^"]+)"[^>]*trm="([^"]+)"|<g unicode="[^"]*" glyph="([^"]*)" x="([^"]+)" '
                             r'y="([^"]+)" adv="([^"]+)"|</span>', trace_text):
            if m[1]:
                cur, prev = m[1], None
                t = [float(v) for v in m[2].split()]
                em = abs(t[0]) or abs(t[1]) or 1.0
            elif m[3] is not None:
                x, y, a = float(m[4]), float(m[5]), float(m[6])
                if cur == F:
                    adv.setdefault(m[3], a)
                    if prev and abs(y - prev[2]) < 1e-3:
                        k = (x - prev[1]) / em - prev[3]
                        if abs(k) < 0.15:
                            kern_raw.append((round(y, 2), (prev[0], m[3]), k))
                prev = (m[3], x, y, a)
            else:
                prev = None
        by_base = collections.defaultdict(list)
        for yb, _, k in kern_raw:
            by_base[yb].append(k)
        base_tc = {yb: sorted(v)[len(v) // 2] for yb, v in by_base.items()}
        for yb, pq, k in kern_raw:
            kern_obs[pq][round(k - base_tc[yb], 3)] += 1
        kern = {pq: c.most_common(1)[0][0] for pq, c in kern_obs.items() if abs(c.most_common(1)[0][0]) >= 0.005}
        progs[F] = {"path": path, "uni": uni, "ligs": lig_names, "lig_glyph": lig_glyph, "adv": adv, "kern": kern,
                    "fb": {"path": None, "font": None, "adv": {}}}
        return progs[F]
    if program(R) is None:
        return None
    face_font = {"": R}

    def font_of(face):
        """the page's font of a face (its most traced); a face the page has no font program of is set in R, the
        command stripped and its argument kept"""
        if face not in face_font:
            face_font[face] = next((f for f in text_fonts if style(f.split("+")[-1]) == face and program(f)), None)
            if face_font[face] is None:
                stats["setter_face_stripped"] += 1
                face_font[face] = R
        return face_font[face]

    def fallback(F, name):
        fb = progs[F]["fb"]
        if fb["font"] is None:
            fb["path"] = tex_type1(F)
            fb["font"] = type1.load(fb["path"]) if fb["path"] else False
        f = fb["font"]
        if not f or name not in f.charstrings:
            return None
        if name not in fb["adv"]:
            fb["adv"][name] = charstring.outline(f, name).width * f.font_matrix[0]
        return fb["adv"][name]
    slots, slot_of, codes, glyphs = [], {}, [], []     # (page font, False): its own program; True: the TeX tree's

    def slot(F, tex):
        if (F, tex) not in slot_of:
            slot_of[(F, tex)] = len(slots)
            slots.append((F, tex))
            codes.append({})
            glyphs.append([])
        return slot_of[(F, tex)]
    slot(R, False)                                     # /F1

    def code(f, g):
        if g not in codes[f]:
            codes[f][g] = len(glyphs[f]) + 1
            glyphs[f].append(g)
        return codes[f][g]
    pages = []
    sc, rise = sup_metrics(traced)
    for li, (text, size) in enumerate(zip(texts, sizes)):
        items = []                                   # (slot, code, advance em, text) or (None, None, 0.25, " ")
        for face, part in faced(supered(text)):
            sup = "s" in face
            F = font_of(face.replace("s", ""))
            pr = progs[F]
            here = pr["ligs"] if line_ligs is None else pr["ligs"] & line_ligs[li]
            s, i = plain(part), 0
            while i < len(s):
                lig = next((l for l in LIGS if s.startswith(l, i) and l in here), None)
                if lig:
                    f = slot(F, False)
                    lg = pr["lig_glyph"].get(lig, lig)
                    items.append((f, code(f, lg), pr["adv"].get(lg, 0.5), lig, sup))
                    i += len(lig)
                    continue
                c = s[i]
                i += 1
                if c.isspace():
                    items.append((None, None, line_gaps[li] if line_gaps else 0.25, " ", sup))
                    continue
                g = pr["uni"].get(c)
                if g is not None:
                    f = slot(F, False)
                    items.append((f, code(f, g), pr["adv"].get(g, 0.5), c, sup))
                    continue
                name = glyph_name(c)
                a = fallback(F, name) if name else None
                if a is None:
                    stats["setter_missing"] += 1
                    return None
                stats["setter_fallback"] += 1
                f = slot(F, True)
                items.append((f, code(f, name), a, c, sup))
        pages.append((items, size))
    objs = []

    def obj(b):
        objs.append(b if isinstance(b, bytes) else b.encode("latin-1"))
        return len(objs)

    def font_obj(prog_path, base, names, advs, cff, uni):
        with open(prog_path, "rb") as fh:
            prog = fh.read()
        ff = obj(b"<< /Length %d %s>>\nstream\n" % (len(prog), b"/Subtype /Type1C " if cff else b"") + prog
                 + b"\nendstream")
        fd = obj("<< /Type /FontDescriptor /FontName /%s /Flags 32 /FontBBox [0 -300 1000 1000] /ItalicAngle 0 "
                 "/Ascent 900 /Descent -300 /CapHeight 700 /StemV 80 /%s %d 0 R >>"
                 % (base, "FontFile3" if cff else "FontFile", ff))
        rev = {}
        for u, g in list(uni.items()) + [(k, glyph_name(k)) for k in NAME] + [(chr(c), chr(c)) for c in range(65, 123)]:
            rev.setdefault(g, u)
        cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /R def 1 begincodespacerange "
                "<00> <FF> endcodespacerange %d beginbfchar\n" % len(names))
        bf = []
        for k, g in enumerate(names):
            if g in LIGS:                           # no entry: mutool splits a ligature from its glyph name, as on the
                continue                            # page (the first letter takes the width, the rest none)
            if lig_of(g):                           # f_i: its letters, which mutool splits the same way
                bf.append("<%02X> <%s>\n" % (k + 1, "".join("%04X" % ord(ch) for ch in lig_of(g))))
                continue
            bf.append("<%02X> <%s>\n" % (k + 1, "".join("%04X" % ord(ch) for ch in rev.get(g, "?"))))
        cmap = cmap.replace("%d beginbfchar" % len(names), "%d beginbfchar" % len(bf)) + "".join(bf)
        cmap += "endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
        tu = obj(b"<< /Length %d >>\nstream\n" % len(cmap) + cmap.encode() + b"\nendstream")
        return obj("<< /Type /Font /Subtype /Type1 /BaseFont /%s /FirstChar 1 /LastChar %d /Widths [%s] /Encoding << "
                   "/Type /Encoding /Differences [1 %s] >> /FontDescriptor %d 0 R /ToUnicode %d 0 R >>"
                   % (base, max(1, len(names)), " ".join("%g" % (advs(g) * 1000) for g in names) or "0",
                      " ".join("/" + g for g in names), fd, tu))
    fids = []
    for F, tex in slots:
        pr = progs[F]
        if tex:
            fb = pr["fb"]
            fids.append(font_obj(fb["path"], os.path.basename(fb["path"]).split(".")[0].upper(), glyphs[len(fids)],
                                 lambda g, fb=fb: fb["adv"][g], False, pr["uni"]))
        else:
            fids.append(font_obj(pr["path"], F.split("+")[-1].replace(" ", ""), glyphs[len(fids)],
                                 lambda g, pr=pr: pr["adv"].get(g, 0.5), pr["path"].endswith(".cff"), pr["uni"]))
    kids = []
    pages_id = len(objs) + 2 * len(pages) + 1
    for li, (items, size) in enumerate(pages):
        w = (8 + sum(a * (sc if up else 1) for _, _, a, _, up in items) * size * 1.05
             + len(items) * abs(line_tcs[li] if line_tcs else 0) * size)
        h = size * 1.4 + 4
        parts, run, last, cur_f = [], [], None, 0

        def flush():
            if run:
                parts.append(_pdf_string(run))
                run.clear()
        tc = (line_tcs[li] if line_tcs else 0.0) * size
        ops = ["BT /F1 %.4f Tf %.4f Tc 2 %.4f Td" % (size, tc, 2 + size * 0.35)]
        cur_up = False
        for f, c, a, _, up in items:
            if f is None:
                flush()
                parts.append("%d" % round(-a * 1000))
                last = None
                continue
            if f != cur_f or up != cur_up:              # a superscript: the page's scale and rise (sup_metrics)
                flush()
                ops.append("[%s] TJ /F%d %.4f Tf %.4f Ts" % (" ".join(parts), f + 1, size * (sc if up else 1),
                                                             size * rise if up else 0))
                parts, cur_f, cur_up, last = [], f, up, None
            g = glyphs[f][c - 1]
            F, tex = slots[f]
            k = progs[F]["kern"].get((last[1], g)) if (last and last[0] == f) else None
            if k:
                flush()
                parts.append("%d" % round(-k * 1000))
            run.append(c)
            last = None if tex else (f, g)
        flush()
        ops.append("[%s] TJ ET" % " ".join(parts))
        cs = " ".join(ops)
        st = obj(b"<< /Length %d >>\nstream\n" % len(cs) + cs.encode("latin-1") + b"\nendstream")
        res = " ".join("/F%d %d 0 R" % (n + 1, fid) for n, fid in enumerate(fids))
        kids.append(obj("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.3f %.3f] /Resources << /Font << %s >> >> "
                        "/Contents %d 0 R >>" % (pages_id, w, h, res, st)))
    assert len(objs) + 1 == pages_id
    obj("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids), len(kids)))
    cat = obj("<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, b in enumerate(objs):
        offs.append(len(out))
        out += b"%d 0 obj\n" % (i + 1) + b + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, cat, x)
    pdf = os.path.join(wd, "set.pdf")
    with open(pdf, "wb") as fh:
        fh.write(out)
    stats["setter_font_" + ("cff" if progs[R]["path"].endswith(".cff") else "t1")] += 1
    return pdf

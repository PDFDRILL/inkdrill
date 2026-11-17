"""tools/judge_setter.py -- rule-6 experiment A (CONTRACT unit 5, 2026-11-17): set the reading's text lines in the page's own embedded font program (no pdflatex)
and judge them through the unchanged judge (its `compiler` hook). One PDF page per line, like compile_lines.

The font: the page's regular text font with the most traced glyphs (style '-', not maths), the role pdflatex's
family mapping plays. Glyph names and advances: what the page itself traces in that font (unicode -> glyph, adv);
ligatures fi/fl/ff/ffi/ffl where the page uses them. A character the subset program does not hold is left out of
the line and counted (`missing`). Spaces advance 0.25 em. The font program goes in unchanged (decoded, as textpos
wrote it), with /Differences, /Widths and a ToUnicode map for the codes this PDF uses."""
import collections, html, os, re, sys, time, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inkdrill import judge as J

LIGS = [("ffi", "ffi"), ("ffl", "ffl"), ("ff", "ff"), ("fi", "fi"), ("fl", "fl")]
STATS = collections.Counter()


def plain(t: str) -> str:
    """the reading's text line as characters: the few escapes these readings use, entities, NFKC like latex_text"""
    t = re.sub(r"\\([%&$#_{}])", r"\1", t)
    t = t.replace("``", "“").replace("''", "”").replace("---", "—").replace("--", "–")
    return html.unescape(t)


def pdf_string(codes):
    return "(" + "".join("\\%03o" % c for c in codes) + ")"


def build(a_pdf, pno, wd, texts, sizes):
    spec = J.Spec(a_pdf, pno, pno, os.path.join(wd, "sspec"), os.path.join(wd, "sfonts"))   # the judge has it already
    t0 = time.process_time()
    F = J.Fonts(a_pdf, os.path.join(wd, "sfonts"), 0, extract=False)
    tr = J.trace(a_pdf, pno, pno, spec.trace_text).get(pno, [])
    # traced glyphs per font: unicode -> glyph name, glyph name -> advance (em), from the raw trace (adv attribute)
    adv = {}
    for m in re.finditer(r'<span font="([^"]+)"[^>]*>|<g unicode="([^"]*)" glyph="([^"]*)" x="[^"]+" y="[^"]+" adv="([^"]+)"', spec.trace_text):
        if m[1]:
            cur = m[1]
        else:
            adv[(cur, m[3])] = float(m[4])
    uni = collections.defaultdict(dict)
    count = collections.Counter()
    for g in tr:
        count[g[4]] += 1
        uni[g[4]].setdefault(html.unescape(g[0]), g[1])
    regular = [f for f, _ in count.most_common() if J.style(f.split("+")[-1]) in ("", None) and not re.search(J.MATHFONT, f)]
    R = regular[0] if regular else count.most_common(1)[0][0]
    ids = F.find(R)
    path = F.ids()[ids[0]] if ids else None
    STATS["font_" + ("cff" if path and path.endswith(".cff") else "t1")] += 1
    names = uni[R]
    # the page's own kerning in R: the gap between consecutive traced glyphs of R on one baseline beyond the first's
    # advance, in em, by glyph pair (the most frequent value; word gaps and line ends are > 0.15 em and left out)
    kern_obs = collections.defaultdict(collections.Counter)
    prev = None
    for m in re.finditer(r'<span font="([^"]+)"[^>]*trm="([^"]+)"|<g unicode="[^"]*" glyph="([^"]*)" x="([^"]+)" y="([^"]+)" adv="([^"]+)"|</span>', spec.trace_text):
        if m[1]:
            curf, size_em = m[1], abs(float(m[2].split()[0])) or abs(float(m[2].split()[1])); prev = None
        elif m[3] is not None:
            x, y, a = float(m[4]), float(m[5]), float(m[6])
            if prev and curf == R and abs(y - prev[2]) < 1e-3:
                k = (x - prev[1]) / size_em - prev[3]
                if abs(k) < 0.15:
                    kern_obs[(prev[0], m[3])][round(k, 3)] += 1
            prev = (m[3], x, y, a)
        else:
            prev = None
    kern = {pq: c.most_common(1)[0][0] for pq, c in kern_obs.items() if abs(c.most_common(1)[0][0]) >= 0.005}
    STATS["kern_pairs"] = len(kern)
    # code table for the glyphs this document uses
    codes, glyphs = {}, []

    def code(gname):
        if gname not in codes:
            codes[gname] = len(glyphs) + 1
            glyphs.append(gname)
        return codes[gname]
    pages = []
    for text, size in zip(texts, sizes):
        s = plain(text)
        items, i = [], 0                     # (code or None for a space, advance em, unicode text)
        while i < len(s):
            lig = next((l for l, g in LIGS if s.startswith(l, i) and g in [n for n in names.values()]), None)
            if lig:
                items.append((code(lig), adv.get((R, lig), 0.5), lig)); i += len(lig); continue
            c = s[i]; i += 1
            if c.isspace():
                items.append((None, 0.25, " ")); continue
            g = names.get(c)
            if g is None:
                STATS["missing"] += 1
                continue
            items.append((code(g), adv.get((R, g), 0.5), c))
        STATS["chars"] += sum(1 for it in items if it[0])
        pages.append((items, size))
    # the PDF
    objs = []

    def obj(b):
        objs.append(b if isinstance(b, bytes) else b.encode("latin-1"))
        return len(objs)
    prog = open(path, "rb").read() if path else b""
    cff = bool(path and path.endswith(".cff"))
    ff = obj(b"<< /Length %d %s>>\nstream\n" % (len(prog), b"/Subtype /Type1C " if cff else b"") + prog + b"\nendstream")
    fd = obj("<< /Type /FontDescriptor /FontName /%s /Flags 32 /FontBBox [0 -300 1000 1000] /ItalicAngle 0 /Ascent 900 "
             "/Descent -300 /CapHeight 700 /StemV 80 /%s %d 0 R >>" % (R.replace(" ", ""), "FontFile3" if cff else "FontFile", ff))
    cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /R def 1 begincodespacerange <00> <FF> "
            "endcodespacerange %d beginbfchar\n" % len(glyphs))
    rev = {v: k for k, v in names.items()}
    for g in glyphs:
        u = rev.get(g, g if g in [l for l, _ in LIGS] else "?")
        cmap += "<%02X> <%s>\n" % (codes[g], "".join("%04X" % ord(ch) for ch in u))
    cmap += "endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    tu = obj(b"<< /Length %d >>\nstream\n" % len(cmap) + cmap.encode() + b"\nendstream")
    widths = " ".join("%g" % (adv.get((R, g), 0.5) * 1000) for g in glyphs)
    font = obj("<< /Type /Font /Subtype /Type1 /BaseFont /%s /FirstChar 1 /LastChar %d /Widths [%s] /Encoding << "
               "/Type /Encoding /Differences [1 %s] >> /FontDescriptor %d 0 R /ToUnicode %d 0 R >>"
               % (R.replace(" ", ""), len(glyphs), widths, " ".join("/" + g for g in glyphs), fd, tu))
    kids = []
    pages_id = len(objs) + 2 * len(pages) + 1
    for items, size in pages:
        w = 8 + sum(a for _, a, _ in items) * size * 1.05
        h = size * 1.4 + 4
        parts, run, last = [], [], None
        for c, a, _ in items:
            if c is None:
                if run:
                    parts.append(pdf_string(run)); run = []
                parts.append("%d" % round(-a * 1000))
                last = None
            else:
                g = glyphs[c - 1]
                kk = kern.get((last, g)) if last else None
                if kk:                                   # TJ moves left by n/1000 em: a kern of +k em is -1000 k
                    if run:
                        parts.append(pdf_string(run)); run = []
                    parts.append("%d" % round(-kk * 1000))
                run.append(c)
                last = g
        if run:
            parts.append(pdf_string(run))
        cs = "BT /F1 %.4f Tf 2 %.4f Td [%s] TJ ET" % (size, 2 + size * 0.35, " ".join(parts))
        st = obj(b"<< /Length %d >>\nstream\n" % len(cs) + cs.encode("latin-1") + b"\nendstream")
        kids.append(obj("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.3f %.3f] /Resources << /Font << /F1 %d 0 R >> >> "
                        "/Contents %d 0 R >>" % (pages_id, w, h, font, st)))
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
    open(pdf, "wb").write(out)
    STATS["setter_cpu"] += time.process_time() - t0
    return pdf


def judge_set(a_pdf, pno, lj, wd, out, route="glyph"):
    os.makedirs(wd, exist_ok=True)

    def comp(texts, sizes, pre, w):
        return build(a_pdf, pno, wd, texts, sizes), {}, set(), 0
    return J.judge(a_pdf, pno, lj, wd, out=out, route=route, compiler=comp)


if __name__ == "__main__":
    # usage: judge_setter.py <original.pdf> <page> <lines.json> <work dir> -> <work dir>/judge.out, counts on stdout
    import io
    a_pdf, pno, lj, wd = sys.argv[1], int(sys.argv[2]), sys.argv[3], os.path.abspath(sys.argv[4])
    buf = io.StringIO()
    judge_set(a_pdf, pno, lj, wd, buf)
    with open(os.path.join(wd, "judge.out"), "w") as fh:
        fh.write(buf.getvalue())
    print(dict(STATS))

#!/usr/bin/env python3
"""inkcmp unit 7 (inkcmp/CONTRACT.md unit 7): `inkcmp lines`, line geometry and types from ink. Standard library only.

The page is real. pdflatex sets a paragraph of ten lines, a numbered display, a drawn figure (diagonal lines, a
circle) with a label inside it, a framed note, and a page number; gs renders it at 300 dpi to PGM. The oracle is independent of the ink: pdftotext -bbox-layout
gives each text line's box and the "(1)" word box, in pt, scaled to pixels. Every type must fire, each one on its
own object. A second case puts a scanner's gutter shadow (a grey ramp with a black core at the right edge) and dust
specks on the same page, and the lines must not change. Skips (prints SKIP) without pdflatex, gs or pdftotext.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from inkcmp_util import INKCMP, run

DPI = 300
TEX = r"""\documentclass[11pt]{article}
\usepackage[a5paper,margin=1.8cm]{geometry}
\begin{document}
\noindent The judge reads a page by its ink, not by its text layer. A line is a band of ink that the
white between lines separates, and a word gap is far narrower than the white around a display. The
first scan finds the line height, and everything after it is measured in that unit, so the same rules
hold at any resolution. A display stands indented on both sides, and its number stands at the right
edge of the column, far from the formula. A figure is one large drawn object, and the labels inside it
belong to the figure rather than to the text. These eight lines are enough for the median of the line
heights, and their width defines the column.
\begin{equation}
  E = \sum_{k=1}^{n} a_k x^k
\end{equation}
and two short parts of one display, far apart:
\begin{equation}
  x = 1, \qquad\qquad\qquad\qquad y = 2
\end{equation}
\begin{center}\setlength{\unitlength}{1mm}\begin{picture}(70,40)
  \put(0,0){\line(2,1){70}}\put(0,35){\line(2,-1){70}}\put(35,17){\circle{24}}\put(31,30){Label}
\end{picture}\end{center}
\begin{center}\setlength{\fboxsep}{8pt}\fbox{\parbox{7cm}{Framed note inside a ruled box.}}\end{center}
\end{document}
"""
TOOLS = ("pdflatex", "gs", "pdftotext")
LINE = re.compile(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', re.S)
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u else 0


TWOCOL = r"""\documentclass[10pt,twocolumn]{article}
\usepackage[a4paper,margin=2cm]{geometry}
\begin{document}
""" + "\n\n".join(["The left and the right column are read apart: a line ends at the gutter, and the next "
                    "column starts a new set of lines. Paragraph %d of the test, written to fill both columns of "
                    "the page with plain text that wraps." % k for k in range(1, 23)]) + "\n\\end{document}\n"


def make_page(tex=TEX):
    d = tempfile.mkdtemp(prefix="test-inkcmp-lines-")
    (Path(d) / "p.tex").write_text(tex)
    subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "p.tex"], cwd=d, capture_output=True,
                   check=True)
    pdf = str(Path(d) / "p.pdf")
    pg = subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pgmraw", f"-r{DPI}",
                         "-sOutputFile=%stdout", pdf], capture_output=True, check=True).stdout
    x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
    shutil.rmtree(d)
    k = DPI / 72
    lines = [(tuple(float(v) * k for v in m[:4]), re.sub(r"<[^>]+>", " ", m[4]).split())
             for m in LINE.findall(x)]
    words = [(tuple(float(v) * k for v in m[:4]), m[4]) for m in WORD.findall(x)]
    return pg, lines, words


def parse_pgm(b):
    m = re.match(rb"P5\s+(?:#[^\n]*\n\s*)*(\d+)\s+(\d+)\s+255\s", b)      # gs writes a comment line
    return int(m[1]), int(m[2]), bytearray(b[m.end():])


def lines_of(pgm_bytes):
    p = run([INKCMP, "lines"], pgm_bytes)
    assert p.returncode == 0, p.stderr
    out = p.stdout.decode().splitlines()
    assert out[0].startswith("INKLINES1 page 1 ") and out[-1] == "end", out[:2]
    return [(f[0], tuple(int(v) for v in f[1:])) for f in (ln.split() for ln in out[1:-1])]


def shadowed(pgm_bytes):
    """the same page with a gutter shadow at the right edge and dust specks in the margins"""
    w, h, px = parse_pgm(pgm_bytes)
    band = w // 20
    for x in range(w - band, w):
        t = (x - (w - band)) / band                       # 0 .. 1 towards the edge
        v = 0 if t > 0.6 else int(235 - 200 * t / 0.6)    # grey ramp, then black
        for y in range(h):
            px[y * w + x] = min(px[y * w + x], v)
    for i in range(40):                                   # 2x2 specks
        x, y = 30 + (i * 97) % (w // 10), 40 + (i * 211) % (h - 80)
        for dy in (0, 1):
            for dx in (0, 1):
                px[(y + dy) * w + x + dx] = 0
    return b"P5\n%d %d\n255\n" % (w, h) + bytes(px)


def check(got, lines, words):
    types = {t for t, _ in got}
    assert {"text", "math", "equation_number", "diagram", "page_info"} <= types, types
    text = [b for t, b in got if t == "text"]
    # pdftotext's paragraph lines (above the display): each one is a text line here, and nothing else is text
    num = next(b for b, w in words if w == "(1)")
    para = [b for b, ws in lines if b[3] < num[1] - 30]
    assert len(para) >= 8, len(para)
    for b in para:
        best = max(iou(b, t) for t in text)
        assert best >= 0.7, (b, best)
    assert len(text) == len(para) + 2, (len(text), len(para))     # + the line before display (2), the framed note
    # the numbers "(1)" and "(2)": exactly these two, at the column edge. Display (2)'s two narrow parts, far apart,
    # are neither numbers nor two displays
    nums = [b for b, w in words if w in ("(1)", "(2)")]
    eq = [b for t, b in got if t == "equation_number"]
    assert len(nums) == 2 and len(eq) == 2 and all(max(iou(e, n) for e in eq) >= 0.6 for n in nums), (eq, nums)
    disp = sorted(b for t, b in got if t == "math")
    disp.sort(key=lambda b: b[1])
    assert len(disp) == 2 and all(d[2] < min(e[0] for e in eq) for d in disp), disp
    tol = 0.3 * (para[0][3] - para[0][1])                 # pdftotext boxes are font boxes, the ink sits inside
    # display (1): one box from the upper limit to the lower one
    sigma = [b for b, w in lines if w == ["n"] or w == ["k=1"]]
    assert len(sigma) == 2 and all(disp[0][1] <= b[1] + tol and b[3] - tol <= disp[0][3] for b in sigma), (disp, sigma)
    # display (2): one box over both parts
    parts = [b for b, w in words if w in ("x", "=2") and b[1] > disp[0][3]]          # the first and last word
    assert len(parts) == 2 and all(disp[1][0] - tol <= p[0] and p[2] <= disp[1][2] + tol for p in parts), (disp, parts)
    # the figure holds its label: no text line inside the frame
    fig = [b for t, b in got if t == "diagram"]
    assert len(fig) == 1, fig
    lab = next(b for b, w in words if w == "Label")
    f = fig[0]
    assert f[0] <= lab[0] and lab[2] <= f[2] and f[1] <= lab[1] and lab[3] <= f[3], (f, lab)
    assert not [b for t, b in got if t != "diagram" and f[0] <= (b[0] + b[2]) / 2 <= f[2]
                and f[1] <= (b[1] + b[3]) / 2 <= f[3]]
    # the framed note is text: its frame is a thin rule (step 2b), not a figure
    note = next(b for b, ws in lines if ws[:1] == ["Framed"])
    best = max((iou(note, b), b) for t, b in got if t == "text")
    assert best[0] >= 0.7, (note, best)                    # the line's own ink, not the frame 8 pt around it
    # the page number, at the bottom
    pi = [b for t, b in got if t == "page_info"]
    pnum = next(b for b, w in words if w == "1")
    inside = lambda a, b, t: b[0] - t <= a[0] and a[2] <= b[2] + t and b[1] - t <= a[1] and a[3] <= b[3] + t
    assert len(pi) == 1 and inside(pi[0], pnum, tol), (pi, pnum)       # a narrow digit: font box >> ink


def test_L1_types_and_boxes_on_a_real_page(page):
    pg, lines, words = page
    check(lines_of(pg), lines, words)
    return 1


def test_L2_gutter_shadow_and_dust_change_nothing(page):
    pg, lines, words = page
    clean = lines_of(pg)
    dirty = lines_of(shadowed(pg))
    check(dirty, lines, words)
    assert dirty == clean, (sorted(set(dirty) ^ set(clean)))[:8]
    return 1


def test_L3_two_pages_in_one_stream(page):
    pg, _, _ = page
    p = run([INKCMP, "lines"], pg + shadowed(pg))
    out = p.stdout.decode().splitlines()
    assert p.returncode == 0 and sum(ln.startswith("INKLINES1 page ") for ln in out) == 2 and out[-1] == "end"
    p = run([INKCMP, "lines"], pg + b"P5\n10 10\n255\n" + b"\0" * 5)        # a short second page is an error
    assert p.returncode == 4, p.returncode
    return 1


RULED = r"""\documentclass[11pt]{article}
\usepackage[a4paper,margin=2.5cm]{geometry}
\begin{document}
\noindent A ruled table of eight rows: its rules cross each other at a quarter, a half and three quarters of their length.
The line height must still come from the text lines, never from a rule.

\begin{center}\begin{tabular}{|l|c|c|c|r|}\hline
""" + "\n".join("Row %d & a%d & b%d & c%d & %d.5 \\\\ \\hline" % (k, k, k, k, k) for k in range(1, 9)) + r"""
\end{tabular}\end{center}
\noindent After the table the paragraph goes on with more plain text, written to give the page a few lines.
\end{document}
"""


def test_L5_ruled_table_cells_and_line_height(page):
    """The rules of a ruled table are erased even though its crossings sit at their sample points; each cell is a
    line, and the line height comes from text, never from a rule."""
    pg, lines, words = make_page(RULED)
    p = run([INKCMP, "lines"], pg)
    head = p.stdout.decode().splitlines()[0].split()
    m = int(head[head.index("m") + 1])
    text_h = sorted(b[3] - b[1] for b, ws in lines if len(ws) > 6)
    assert text_h and m >= 0.5 * text_h[len(text_h) // 2], (m, text_h)
    got = lines_of(pg)
    assert not [b for t, b in got if t in ("diagram", "math")], [(t, b) for t, b in got if t in ("diagram", "math")][:3]
    cells = [b for b, w in words if re.fullmatch(r"[abc][1-8]", w)]
    assert len(cells) == 24, len(cells)
    for c in cells:
        assert max(iou(c, b) for t, b in got) >= 0.6, c
    return len(cells)


def test_L4_two_columns_are_read_apart(page):
    pg, lines, _ = make_page(TWOCOL)
    w = int(re.match(rb"P5\s+(?:#[^\n]*\n\s*)*(\d+)", pg)[1])
    got = [b for t, b in lines_of(pg) if t == "text"]
    left = [b for b, ws in lines if b[2] < w / 2]
    right = [b for b, ws in lines if b[0] > w / 2]
    assert len(left) >= 20 and len(right) >= 20, (len(left), len(right))
    across = [b for b in got if b[0] < w / 2 - 20 and b[2] > w / 2 + 20]
    assert not across, across[:3]                                     # no text line crosses the gutter
    for b in left + right:
        assert max(iou(b, t) for t in got) >= 0.7, b
    return len(left) + len(right)


if __name__ == "__main__":
    if not all(shutil.which(t) for t in TOOLS):
        print("test_inkcmp_lines: SKIP (needs %s)" % ", ".join(TOOLS))
    else:
        page = make_page()
        for name, f in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
            print("test_inkcmp_lines %s: ok (%d cases)" % (name[5:], f(page)))

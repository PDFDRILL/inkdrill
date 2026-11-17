#!/usr/bin/env python3
"""inkcmp glyphs (inkcmp/CONTRACT.md unit 5, glyph-raster route): glyph outlines from font programs, rasterised at a
page origin, scored per pair. Standard library only. Four oracles, none of them a recorded output:

G1 the Type 1 half equals U9 (inkdrill type1 -> charstring -> scan) pixel for pixel, over every glyph of TeX fonts
   (cmr10: hint replacement; cmsy10; utmr8a: seac accents), at fractional origins, upright, rotated and sheared.
   Python's raster is painted into a page; C rasterises the same glyph and is paired against that page's ink: xor 0.
G2 the CFF half: pdflatex embeds Type 1, Ghostscript's pdfwrite re-embeds the same fonts as Type 1C; every glyph read
   from the CFF must rasterise exactly as read from the Type 1 (two parsers, two charstring languages, one outline).
G3 the score: xor, solid and largest piece of two DIFFERENT glyphs equal judge.score() on the Python rasters' XOR.
G4 both sides of each refusal: a missing glyph, an unloaded font, the page side without a page, a candidate list.
R1 `inkcmp rules` equals judge.rule_pixels (the Python it replaces) on a real page with fractions, overlines and a
   radical, rendered at 400 dpi, with its glyph quads as cover, sampled at 1x and from an 8x render.
Skips (prints SKIP) without kpsewhich and the TeX fonts (G1, G3), or pdflatex, gs and mutool (G2).
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from inkdrill import charstring, judge, scan, type1  # noqa: E402

INKCMP = ROOT / "build" / "inkcmp"
CELL = 160


def kpse(name):
    if not shutil.which("kpsewhich"):
        return None
    p = subprocess.run(["kpsewhich", name], capture_output=True, text=True).stdout.strip()
    return p or None


def glyphs(job):
    p = subprocess.run([str(INKCMP), "glyphs"], input="\n".join(job + ["end"]) + "\n", capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines()


def py_mask(ft, name, m, X, Y, x0, y0, w, h):
    """U9's raster of a glyph in a window, with the transform inkcmp glyphs documents"""
    F = ft.font_matrix
    g = charstring.outline(ft, name)

    def tf(u, v):
        fu = u * F[0] + v * F[2] + F[4]
        fv = u * F[1] + v * F[3] + F[5]
        return (X + fu * m[0] + fv * m[2] - x0, Y + fu * m[1] + fv * m[3] - y0)
    return scan.rasterize(scan.flatten(g.contours, tf), w, h).data


def side(fid, name, m, X, Y):
    return f"{fid} {name} {m[0]!r} {m[1]!r} {m[2]!r} {m[3]!r} {X!r} {Y!r}"


def test_G1_type1_equals_u9(tmp):
    s = 10 * 400 / 72
    mats = [((s, 0.0, 0.0, -s), (0.3125, 0.71875)), ((0.7 * s, 0.0, 0.0, -0.7 * s), (0.8, 0.05)),
            ((0.0, -s, -s, 0.0), (0.5, 0.25)), ((s, 0.0, 0.21 * s, -s), (0.1, 0.9))]
    cases = 0
    for fi, fname in enumerate(("cmr10.pfb", "cmsy10.pfb", "utmr8a.pfb")):
        path = kpse(fname)
        assert path, fname
        ft = type1.load(path)
        names = sorted(ft.charstrings)
        place = [(n, m) for k, (m, fr) in enumerate(mats) for n in (names if k < 2 else names[::7])]
        cols = 40
        W, H = cols * CELL, ((len(place) + cols - 1) // cols) * CELL
        page = bytearray(b"\xff" * (W * H))
        job = ["INKGLYPHS1", f"font 0 {path}", f"page {tmp}/g1-{fi}.pgm 128"]
        inked = 0
        for k, (n, m) in enumerate(place):
            x0, y0 = (k % cols) * CELL, (k // cols) * CELL
            fr = next(fr for mm, fr in mats if mm is m)
            X, Y = x0 + 60 + fr[0], y0 + 100 + fr[1]
            d = py_mask(ft, n, m, X, Y, x0, y0, CELL, CELL)
            if any(d):
                inked += 1
            for j in range(CELL):
                row = d[j * CELL:(j + 1) * CELL]
                if any(row):
                    for i in range(CELL):
                        if row[i]:
                            page[(y0 + j) * W + x0 + i] = 0
            job.append(f"pair {side(0, n, m, X, Y)} -1 - 0 0 0 0 0 0 {x0} {y0} {x0 + CELL} {y0 + CELL}")
        Path(f"{tmp}/g1-{fi}.pgm").write_bytes(b"P5\n%d %d\n255\n" % (W, H) + bytes(page))
        out = [l for l in glyphs(job) if l[:2] in ("P ", "O ")]
        assert len(out) == len(place)
        bad = [(n, r) for (n, _), r in zip(place, out) if r != "P 0 0 0"]
        assert not bad, (fname, bad[:5])
        assert inked > 0.9 * len(place), (fname, inked, len(place))     # the oracle compares ink, not two blanks
        cases += len(place)
    return cases


def test_G2_cff_equals_type1(tmp):
    tex = r"""\documentclass{article}\usepackage[T1]{fontenc}\begin{document}
ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789 .,;:!?()[]-- fi fl ff ffi
\'e \`a \"o \^u \~n \c{c} \AA\ \ae\ \oe\ \ss\ \o\ \L
$\alpha\beta\gamma\int_0^\infty \sum_{i=1}^n x_i^2 \sqrt{y} \leq \partial\nabla\aleph \left(\frac{a}{b}\right)$
\[ \Biggl( \int\!\!\int \prod \bigcup \bigoplus \Biggr) \]
{\fontfamily{ptm}\selectfont Times ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz \'e\`a\"o\^u\~n\c{c} \textit{italic Wxyz}}
\end{document}"""
    Path(f"{tmp}/t.tex").write_text(tex)
    subprocess.run(["pdflatex", "-interaction=batchmode", "t.tex"], cwd=tmp, capture_output=True)
    subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pdfwrite", "-sOutputFile=t1c.pdf", "t.pdf"],
                   cwd=tmp, capture_output=True, check=True)
    for d, pdf in (("x1", "t.pdf"), ("x2", "t1c.pdf")):
        os.makedirs(f"{tmp}/{d}", exist_ok=True)
        subprocess.run(["mutool", "extract", f"../{pdf}"], cwd=f"{tmp}/{d}", capture_output=True)
    t1 = sorted(str(p) for p in Path(f"{tmp}/x1").glob("*.pfa"))
    cf = sorted(str(p) for p in Path(f"{tmp}/x2").glob("*.cff"))
    assert len(t1) >= 6 and len(cf) == len(t1), (len(t1), len(cf))
    files = t1 + cf
    by = {}
    for i, f in enumerate(files):
        by.setdefault(judge.font_name(f).split("+")[-1], {})["cff" if f.endswith(".cff") else "t1"] = i
    assert all(len(v) == 2 for v in by.values()), by
    job = ["INKGLYPHS1"] + [f"font {i} {f}" for i, f in enumerate(files)]
    names = []
    for base, d in sorted(by.items()):
        for n in sorted(type1.load(files[d["t1"]]).charstrings):
            if n == ".notdef":
                continue
            for size, (fx, fy) in ((10 * 400 / 72, (0.3125, 0.71875)), (24 * 400 / 72, (0.6, 0.2))):
                m = (size, 0.0, 0.0, -size)
                job.append(f"pair {side(d['cff'], n, m, 100 + fx, 300 + fy)} {side(d['t1'], n, m, 100 + fx, 300 + fy)} 0 0 400 450")
                names.append((base, n))
    out = glyphs(job)
    assert sum(l.startswith("font ") and " ok " in l for l in out) == len(files)
    res = [l for l in out if l[:2] in ("P ", "O ")]
    bad = [(b, r) for b, r in zip(names, res) if r != "P 0 0 0"]
    assert not bad, bad[:5]
    assert len(names) > 300
    return len(names)


def test_G3_score_equals_judge_score(tmp):
    ft = type1.load(kpse("cmr10.pfb"))
    path = kpse("cmr10.pfb")
    s = 10 * 400 / 72
    m = (s, 0.0, 0.0, -s)
    pairs = [("a", "o"), ("e", "c"), ("l", "one"), ("n", "u"), ("O", "Q"), ("i", "j"), ("comma", "period"), ("B", "B")]
    W = H = 120
    job = ["INKGLYPHS1", f"font 0 {path}", f"page {tmp}/g3.pgm 128"]
    page = bytearray(b"\xff" * (W * H * len(pairs)))
    want = []
    for k, (a, b) in enumerate(pairs):
        y0 = k * H
        X, Y = 40.375, y0 + 80.625
        da = py_mask(ft, a, m, X, Y, 0, y0, W, H)
        db = py_mask(ft, b, m, X + (0.5 if a == b else 0), Y, 0, y0, W, H)
        for j in range(H):
            for i in range(W):
                if da[j * W + i]:
                    page[(y0 + j) * W + i] = 0
        diff = {(i, j) for j in range(H) for i in range(W) if bool(da[j * W + i]) != bool(db[j * W + i])}
        want.append("P %d %d %d" % judge.score(diff))
        job.append(f"pair -1 - 0 0 0 0 0 0 {side(0, b, m, X + (0.5 if a == b else 0), Y)} 0 {y0} {W} {y0 + H}")
    Path(f"{tmp}/g3.pgm").write_bytes(b"P5\n%d %d\n255\n" % (W, H * len(pairs)) + bytes(page))
    got = [l for l in glyphs(job) if l[:2] in ("P ", "O ")]
    assert got == want, list(zip(pairs, got, want))
    assert all(int(w.split()[1]) > 0 for w in want)            # every case has a residual to score
    assert any(int(w.split()[2]) >= judge.THRESHOLD for w in want) and any(int(w.split()[2]) < judge.THRESHOLD for w in want)
    return len(pairs)


def test_G4_refusals_both_sides(tmp):
    r10, sy = kpse("cmr10.pfb"), kpse("cmsy10.pfb")
    s = 10 * 400 / 72
    m = f"{s!r} 0 0 {-s!r}"
    Path(f"{tmp}/bad.pfa").write_bytes(b"%!PS-AdobeFont-1.0: nothing\n")
    Path(f"{tmp}/g4.pgm").write_bytes(b"P5\n200 200\n255\n" + b"\xff" * 40000)
    out = glyphs(["INKGLYPHS1", f"font 0 {r10}", f"font 1 {sy}", f"font 2 {tmp}/bad.pfa",
                  f"pair 0 a {m} 50.5 120.5 0 a {m} 50.5 120.5 0 0 200 200",           # accepted: equal glyphs
                  f"pair 0 nosuchglyph {m} 50.5 120.5 0 a {m} 50.5 120.5 0 0 200 200",  # refused: no glyph (A)
                  f"pair 0 a {m} 50.5 120.5 2 a {m} 50.5 120.5 0 0 200 200",            # refused: font not loaded (B)
                  f"pair -1 - 0 0 0 0 0 0 0 a {m} 50.5 120.5 0 0 200 200",              # refused: no page yet
                  f"pair 1,0 a {m} 50.5 120.5 0 a {m} 50.5 120.5 0 0 200 200",          # candidates: cmsy10 has no a
                  f"pair 1 a {m} 50.5 120.5 0 a {m} 50.5 120.5 0 0 200 200"])           # refused without the list
    assert out[0].startswith("font 0 ok CMR10") and out[1].startswith("font 1 ok CMSY10"), out[:2]
    assert out[2].startswith("font 2 fail"), out[2]
    res = [l for l in out if l[:2] in ("P ", "O ")]
    assert res[0] == "P 0 0 0" and res[1].startswith("O A") and res[2].startswith("O B"), res
    assert res[3].startswith("O A") and res[4] == "P 0 0 0" and res[5].startswith("O A"), res
    out = glyphs(["INKGLYPHS1", f"font 0 {r10}", f"page {tmp}/g4.pgm 128",
                  f"pair -1 - 0 0 0 0 0 0 0 a {m} 50.5 120.5 0 0 200 200"])        # accepted: a white page
    p = [l for l in out if l[:2] in ("P ", "O ")][0].split()
    assert p[0] == "P" and int(p[1]) > 100, p                      # the glyph's whole ink is residual
    return 8


def test_R1_rules_equal_python(tmp):
    tex = r"""\documentclass[12pt]{article}\pagestyle{empty}\begin{document}
Text $\frac{a+b}{c}$ and $\overline{xy} + \sqrt{z+1}$ then $\underline{u} \frac{1}{2}$.
\[ \frac{\overline{A} + B}{\sqrt{C^2 + D^2}} = \overline{\overline{E}} \]
A rule \rule{3cm}{0.4pt} and a thick one \rule{1cm}{3pt} and text.
\end{document}"""
    Path(f"{tmp}/r.tex").write_text(tex)
    subprocess.run(["pdflatex", "-interaction=batchmode", "r.tex"], cwd=tmp, capture_output=True)
    pdf = f"{tmp}/r.pdf"
    cs = judge.chars(pdf, 1, 1)[0]
    S = judge.S
    quads = [(c[3] * S - 1, c[4] * S - 1, c[5] * S + 1, c[6] * S + 1) for c in cs]
    cases = 0
    for up in (1, 8):
        w, h, g = judge.pgm(pdf, 1, 400 * up)
        f = f"{tmp}/r{up}.pgm"
        judge.pgm_write(f, w, h, g)
        x0, y0 = int(min(c[3] for c in cs) * S) - 40, int(min(c[4] for c in cs) * S) - 40
        x1, y1 = int(max(c[5] for c in cs) * S) + 40, int(max(c[6] for c in cs) * S) + 40
        for k, (region, qs) in enumerate((((x0, y0, x1, y1), quads), ((x0, y0, x1, y1), quads[::2]),
                                          ((0, 0, w // up - 1, (y0 + y1) // 2), []))):
            ref_a, _ = judge.rule_pixels((w, h, g, 0, 0), (8, 8, b"\xff" * 64), up, qs, [], region)
            got = judge.rules_job([((f, judge.INK, up, 0, 0), qs, region)], tmp)[0]
            assert got == ref_a, (up, k, len(got), len(ref_a))
            cases += 1
            if k == 0:
                assert len(ref_a) >= 6, len(ref_a)          # the page's rules are found, not two empty lists
    return cases


if __name__ == "__main__":
    have_tex = all(kpse(f) for f in ("cmr10.pfb", "cmsy10.pfb", "utmr8a.pfb"))
    have_tools = all(shutil.which(t) for t in ("pdflatex", "gs", "mutool"))
    with tempfile.TemporaryDirectory(prefix="inkcmp-glyphs-", dir=os.environ.get("TMPDIR")) as tmp:
        for name, f in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
            need_tools = name.startswith(("test_G2", "test_R1"))
            if not have_tex or (need_tools and not have_tools):
                print("test_inkcmp_glyphs %s: SKIP (needs the TeX Type 1 fonts%s)" % (
                    name[5:], ", pdflatex, gs and mutool" if need_tools else ""))
                continue
            print("test_inkcmp_glyphs %s: ok (%d cases)" % (name[5:], f(tmp)))

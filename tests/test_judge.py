"""inkcmp judge (inkdrill/judge.py, bin/inkcmp-judge): a born-digital page and its own reading judge clean;
single-letter plants in the reading are each flagged at the frozen threshold.

The page is made here. pdflatex sets eight lines of plain text in Computer Modern. The reading is a
MathPix-shaped lines.json: one region per printed line (pdftotext -bbox-layout, CropBox px at 200 dpi),
holding the source text of that line. Needs pdflatex, mutool, pdftoppm, pdfinfo and pdftotext; skips when
any of them is missing.
"""
import collections
import copy
import io
import itertools
import json
import random
import math
import os
import re
import time
import shutil
import subprocess
import tempfile
import unittest

from inkdrill import judge, rects, scanjudge

TOOLS = ("pdflatex", "mutool", "pdftoppm", "pdfinfo", "pdftotext")
LINES = [
    "The judge renders each line of a reading in place",
    "and compares the ink of every glyph with the page",
    "at its own origin, so a single wrong letter shows",
    "as a residual that clean glyphs never reach here.",
    "Numbers like 2024 and 31 belong to the same test,",
    "with capitals such as Hilbert, Killing and Noether",
    "beside lowercase words: brief, hold, under, rather.",
    "Eight lines are enough for the plants to land well.",
]
PLANTS = [("b", "d"), ("l", "1"), ("n", "u"), ("e", "c"), ("h", "b"), ("r", "t")]
THIN = {("e", "c"), ("n", "u")}   # thin-stroke plants: a crossbar or a hairline join, 2-3 px at 400 dpi
WORD = re.compile(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">')


def plant(text, old, new):
    """replace `old` inside the first word of >= 4 lowercase letters that holds it"""
    for m in re.finditer(r"(?<![\\\w])[a-z]{4,}", text):
        if old in m.group(0):
            p = m.start() + m.group(0).index(old)
            return text[:p] + new + text[p + 1:]
    return None


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeBornDigital(unittest.TestCase):
    ROUTE = "render"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-")
        d = cls.tmp.name
        tex = ("\\documentclass[10pt]{article}\n\\usepackage[margin=2.5cm]{geometry}\n\\pagestyle{empty}\n"
               "\\begin{document}\n\\noindent\\raggedright\n" + "\\\\\n".join(LINES) + "\n\\end{document}\n")
        with open(os.path.join(d, "page.tex"), "w") as fh:
            fh.write(tex)
        subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                       capture_output=True, check=True)
        cls.pdf = os.path.join(d, "page.pdf")
        x = subprocess.run(["pdftotext", "-bbox-layout", cls.pdf, "-"], capture_output=True, text=True,
                           check=True).stdout
        boxes = [tuple(float(v) for v in m) for m in WORD.findall(x)]
        assert len(boxes) == len(LINES), boxes
        wpt, hpt = judge.page_size(cls.pdf, 1)
        k = 200 / 72
        cls.doc = {"pages": [{"page": 1, "page_width": round(wpt * k), "page_height": round(hpt * k), "lines": [
            {"type": "text", "text": t, "region": {"top_left_x": int(b[0] * k) - 2, "top_left_y": int(b[1] * k) - 2,
                                                  "width": int((b[2] - b[0]) * k) + 4,
                                                  "height": int((b[3] - b[1]) * k) + 4}}
            for t, b in zip(LINES, boxes)]}]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_judge(self, doc, name):
        lj = os.path.join(self.tmp.name, name + ".lines.json")
        with open(lj, "w") as fh:
            json.dump(doc, fh)
        wd = os.path.join(self.tmp.name, name)
        os.makedirs(wd)
        out = io.StringIO()
        res = judge.judge(self.pdf, 1, lj, wd, out=out, route=self.ROUTE)
        rows = [ln.split() for ln in out.getvalue().splitlines() if ln.startswith("G ")]
        return res, rows

    def test_clean_reading_has_no_flag(self):
        res, rows = self.run_judge(self.doc, "clean")
        self.assertEqual(res["lines"], len(LINES))
        self.assertGreater(len(rows), 300)
        self.assertTrue(all(r[4] == "1" for r in rows), "every glyph pairs with an equal one")
        worst = max(int(r[6]) for r in rows)
        self.assertLess(worst, judge.THRESHOLD, f"a clean glyph reached solid {worst}")
        self.assertEqual(res["flagged"], 0)

    def planted_run(self):
        if "_planted" not in self.__class__.__dict__:
            doc = copy.deepcopy(self.doc)
            planted = []
            for i, ln in enumerate(doc["pages"][0]["lines"]):
                for j in range(len(PLANTS)):     # the first kind, from line i's own on, that the line holds
                    old, new = PLANTS[(i + j) % len(PLANTS)]
                    t = plant(ln["text"], old, new)
                    if t:
                        ln["text"] = t
                        planted.append((old, new))
                        break
            self.__class__._planted = (planted,) + self.run_judge(doc, "planted")
        return self.__class__._planted

    def test_plants_flagged_except_thin_strokes(self):
        """Every plant is flagged at threshold 7, except e->c, the open thin-stroke miss (contract unit 5, the 9 pt
        miss). Here it is the 10 pt CM crossbar, solid 4. All 7 other plants and nothing else are flagged."""
        planted, res, rows = self.planted_run()
        self.assertEqual(len(planted), len(LINES))
        diff = [r for r in rows if r[4] == "0"]
        self.assertEqual(len(diff), len(planted), "each plant pairs as a one-glyph replace")
        missed = [(r[2], r[3]) for r in diff if r[8] != "1"]
        self.assertTrue(set(missed) <= THIN, f"a plant outside the thin-stroke class was missed: {missed}")
        self.assertLessEqual(len(missed), 1)
        self.assertEqual(res["flagged"], len(planted) - len(missed), "only plants are flagged")

    @unittest.expectedFailure
    def test_every_plant_is_flagged(self):
        """The goal. It fails today on e->c, the thin-stroke miss. A fix turns this into an unexpected success."""
        _, _, rows = self.planted_run()
        self.assertEqual([(r[2], r[3], r[6]) for r in rows if r[4] == "0" and r[8] != "1"], [])

    def test_findings_carry_their_place_on_the_page(self):
        """G and U lines end in a glyph's quad box in the R lines' frame (the page at 400 dpi): the page glyph's for G
        and side-A U lines, the rendered glyph's carried into the page for side-B U lines. Every box lies inside its
        line's region (board_run attributes a finding to a MathPix region by this place, not by its line)."""
        doc = copy.deepcopy(self.doc)
        lines = doc["pages"][0]["lines"]
        lines[0]["text"] = lines[0]["text"].replace(" ", " a ", 1)         # an invented word: side-B U lines
        lines[2]["text"] = lines[2]["text"].rsplit(" ", 1)[0]               # a dropped word: side-A U lines
        lj = os.path.join(self.tmp.name, "placed.lines.json")
        with open(lj, "w") as fh:
            json.dump(doc, fh)
        wd = os.path.join(self.tmp.name, "placed")
        os.makedirs(wd)
        out = io.StringIO()
        judge.judge(self.pdf, 1, lj, wd, out=out, route=self.ROUTE)
        rows = [ln.split() for ln in out.getvalue().splitlines() if ln[:2] in ("G ", "U ")]
        k, tol = 400 / 200, 8                     # region px (200 dpi) -> 400 dpi; 2 pt of slack for quad boxes
        seen = collections.Counter()
        for r in rows:
            want = 18 if r[0] == "G" else 8               # G: the box, the place (2026-11-24), the kind (J4)
            self.assertEqual(len(r), want, r)
            x0, y0, x1, y1 = map(int, r[12:16] if r[0] == "G" else r[-4:])
            if r[0] == "G":
                self.assertLessEqual(abs(float(r[16])), judge.PLACE, r)
                self.assertIn(r[17], ("g", "s", "-"), r)
            g = lines[int(r[1][1:])]["region"]
            rx0, ry0 = g["top_left_x"] * k, g["top_left_y"] * k
            rx1, ry1 = rx0 + g["width"] * k, ry0 + g["height"] * k
            self.assertTrue(x0 < x1 and y0 < y1, r)
            self.assertTrue(rx0 - tol <= x0 and x1 <= rx1 + tol and ry0 - tol <= y0 and y1 <= ry1 + tol,
                            (r, (rx0, ry0, rx1, ry1)))
            seen[r[0] if r[0] == "G" else r[0] + r[2]] += 1
        self.assertGreater(seen["G"], 300)
        self.assertGreaterEqual(seen["UA"], 3, "the dropped word")
        self.assertGreaterEqual(seen["UB"], 1, "the invented word")

    def test_overlapping_boxes_pair_each_glyph_once(self):
        """a reading with a line boxed twice (overlapping regions) must not pair the page's glyphs twice"""
        clean, _ = self.run_judge(self.doc, "clean2")
        doc = copy.deepcopy(self.doc)
        doc["pages"][0]["lines"].insert(1, copy.deepcopy(doc["pages"][0]["lines"][0]))
        res, _ = self.run_judge(doc, "dup")
        self.assertLessEqual(res["glyphs"], res["a_glyphs"])
        self.assertLessEqual(res["glyphs"], clean["glyphs"])
        self.assertEqual(res["a_glyphs"], res["glyphs"] + res["a_unpaired"])

    def test_page_info_line_is_judged_as_text(self):
        """a running head typed page_info is the same ink as typed text (0864 p4, pdfdrill 68f2b43e: the IJCAI
        head typed page_info cost ours +0.022 when the judge dropped page_info leaves). Diagram lines stay out."""
        clean, _ = self.run_judge(self.doc, "as-text")
        doc = copy.deepcopy(self.doc)
        doc["pages"][0]["lines"][0].update(type="page_info", subtype="header")
        res, _ = self.run_judge(doc, "as-page-info")
        keys = ("lines", "glyphs", "a_unpaired", "b_unpaired", "flagged", "glyph", "a_glyphs")
        self.assertEqual({k: res[k] for k in keys}, {k: clean[k] for k in keys})
        self.assertEqual(res["a_unpaired"], 0)
        doc["pages"][0]["lines"][0]["type"] = "diagram"
        dia, _ = self.run_judge(doc, "as-diagram")
        self.assertEqual(dia["lines"], len(LINES) - 1)
        self.assertGreater(dia["a_unpaired"], 30)

    def test_page_info_line_without_region_stays_out(self):
        """a page_info line with text but region None (Gemma4 board18g, 1805.01788 p3 and lib:5a5c76391c9c p4:
        TypeError at 65f1cf4) is left out, as every line without a region was before; the rest is judged"""
        clean, _ = self.run_judge(self.doc, "with-all")
        doc = copy.deepcopy(self.doc)
        doc["pages"][0]["lines"].append({"type": "page_info", "text": "SIGIR '18, July 8-12", "region": None})
        res, _ = self.run_judge(doc, "noregion-page-info")
        keys = ("lines", "glyphs", "a_unpaired", "b_unpaired", "flagged", "glyph", "a_glyphs")
        self.assertEqual({k: res[k] for k in keys}, {k: clean[k] for k in keys})
        self.assertEqual(res["lines"], len(LINES))

    def test_unmeasurable_face_is_never_a_glyph_finding(self):
        """a paired glyph in a face TeX cannot set (judge.UNSETTABLE; board18 lib:c57ac038ae5f p293: 26 MathTime
        glyphs flagged against CMEX/CMSY/CMMI) is counted unmeasurable and is never a glyph finding; a page without
        such a face keeps every count. Here the page's own CMR is declared unsettable for one run."""
        _, res, _ = self.planted_run()
        self.assertEqual(res["unmeasurable"], 0)
        self.assertGreater(res["glyph"], 0)
        planted_doc = copy.deepcopy(self.doc)
        for i, ln in enumerate(planted_doc["pages"][0]["lines"]):
            for j in range(len(PLANTS)):
                old, new = PLANTS[(i + j) % len(PLANTS)]
                t = plant(ln["text"], old, new)
                if t:
                    ln["text"] = t
                    break
        keep = judge.UNSETTABLE
        judge.UNSETTABLE = re.compile(r"^CMR")
        try:
            um, _ = self.run_judge(planted_doc, "unsettable-cmr")
        finally:
            judge.UNSETTABLE = keep
        self.assertEqual(um["glyph"], 0)
        self.assertEqual(um["unmeasurable"], um["glyphs"])
        for k in ("glyphs", "flagged", "style", "a_unpaired", "b_unpaired"):
            self.assertEqual(um[k], res[k], k)

    def test_threshold_is_the_frozen_value(self):
        self.assertEqual(judge.THRESHOLD, 7)
        self.assertEqual(judge.UP, 8)


if __name__ == "__main__":
    unittest.main()


UNI = [
    "The temperature fell to $-5$ degrees and rose to $-2$ again,",
    "while this line has a macro no package defines: \\nosuchmacro here,",
    "and this last line reads 70% of the plain text as written.",
]


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeLinesThatDoNotCompile(unittest.TestCase):
    """A reading's U+2212 is set as $-$ and pairs; a line pdflatex cannot set is NOT_COMPILED and the other lines
    still typeset and pair (board pages 0864 p4 and lib:88d76967110e p170 had lost every line to one)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-uni-")
        d = cls.tmp.name
        src = [UNI[0], "while this line has a macro no package defines: here,", UNI[2].replace("%", "\\%")]
        with open(os.path.join(d, "page.tex"), "w") as fh:
            fh.write("\\documentclass[10pt]{article}\n\\usepackage[margin=2.5cm]{geometry}\n\\pagestyle{empty}\n"
                     "\\begin{document}\n\\noindent\\raggedright\n" + "\\\\\n".join(src) + "\n\\end{document}\n")
        subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                       capture_output=True, check=True)
        cls.pdf = os.path.join(d, "page.pdf")
        x = subprocess.run(["pdftotext", "-bbox-layout", cls.pdf, "-"], capture_output=True, text=True,
                           check=True).stdout
        boxes = [tuple(float(v) for v in m) for m in WORD.findall(x)]
        assert len(boxes) == 3, boxes
        wpt, hpt = judge.page_size(cls.pdf, 1)
        k = 200 / 72
        # the reading as a text-layer reader writes it: the real U+2212, a raw %, and one line that cannot compile
        texts = ["The temperature fell to \u22125 degrees and rose to \u22122 again,", UNI[1], UNI[2]]
        doc = {"pages": [{"page": 1, "page_width": round(wpt * k), "page_height": round(hpt * k), "lines": [
            {"type": "text", "text": t, "region": {"top_left_x": int(b[0] * k) - 2, "top_left_y": int(b[1] * k) - 2,
                                                  "width": int((b[2] - b[0]) * k) + 4,
                                                  "height": int((b[3] - b[1]) * k) + 4}}
            for t, b in zip(texts, boxes)]}]}
        cls.doc = doc
        lj = os.path.join(d, "r.lines.json")
        with open(lj, "w") as fh:
            json.dump(doc, fh)
        out = io.StringIO()
        os.makedirs(os.path.join(d, "w"))
        cls.res = judge.judge(cls.pdf, 1, lj, os.path.join(d, "w"), out=out)
        cls.out = out.getvalue().splitlines()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_minus_pairs(self):
        minus = [ln.split() for ln in self.out if ln.startswith("G L0 &#x2212; ")]   # mutool writes the entity
        self.assertEqual(len(minus), 2, "both U+2212 glyphs pair")
        self.assertTrue(all(r[8] == "0" for r in minus), minus)

    def test_broken_line_is_reported_and_the_others_pair(self):
        self.assertEqual([ln for ln in self.out if ln.startswith("N ")][:1][0].split()[:3], ["N", "L1", "NOT_COMPILED"])
        self.assertEqual(self.res["not_compiled"], 1)
        paired = {ln.split()[1] for ln in self.out if ln.startswith("G ")}
        self.assertEqual(paired, {"L0", "L2"}, "lines 0 and 2 typeset and pair")
        self.assertEqual(self.res["flagged"], 0)
        self.assertIn("STATUS OK", self.out)

    def test_broken_line_carries_its_region_box(self):
        """an N line ends in its line's region box in the G/U/R frame (the page at 400 dpi), so board_run places it
        by position, not by L-number"""
        n = [ln.split() for ln in self.out if ln.startswith("N ")][0]
        g = self.doc["pages"][0]["lines"][1]["region"]
        k = 400 / 200
        want = [math.floor(g["top_left_x"] * k), math.floor(g["top_left_y"] * k),
                math.ceil((g["top_left_x"] + g["width"]) * k), math.ceil((g["top_left_y"] + g["height"]) * k)]
        self.assertEqual([int(v) for v in n[-4:]], want, n)

    def test_unpaired_ink_is_counted(self):
        """the board's ink residual needs every original glyph that paired with nothing: the broken line's own"""
        r = self.res
        self.assertEqual(r["a_glyphs"], r["glyphs"] + r["a_unpaired"])
        line1 = len("whilethislinehasamacronopackagedefines:here,")        # its glyphs on the page, no spaces
        self.assertGreaterEqual(r["a_unpaired"], line1)
        self.assertGreaterEqual(r["b_unpaired"], 0)

    def test_latex_text(self):
        self.assertEqual(judge.latex_text("70% of"), "70\\% of")
        self.assertEqual(judge.latex_text("5\\% stays"), "5\\% stays")
        self.assertEqual(judge.latex_text("\u22121 and $a\u2212b$"), "$-$1 and $a- b$")
        self.assertEqual(judge.latex_text("\ufb01nd \uff05"), "find \\%")
        self.assertEqual(judge.latex_text("\u03b1"), "$\\alpha$")

    def test_style_of_libertine_names(self):
        self.assertEqual(judge.style("ABCDEF+LinLibertineTI"), "i")
        self.assertEqual(judge.style("ABCDEF+LinLibertineTB"), "b")
        self.assertEqual(judge.style("ABCDEF+LinLibertineT"), "")


def _accent_case(tmp, name, page_line, read_line, preamble="", route="render", typ="text"):
    """one-line page set by pdflatex, read back as `read_line`; returns (res, judge output lines)"""
    d = os.path.join(tmp, name)
    os.makedirs(d)
    with open(os.path.join(d, "page.tex"), "w") as fh:
        fh.write("\\documentclass[10pt]{article}\n\\usepackage[margin=2.5cm]{geometry}\n" + preamble +
                 "\\pagestyle{empty}\n\\begin{document}\n\\noindent " + page_line + "\n\\end{document}\n")
    subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                   capture_output=True, check=True)
    pdf = os.path.join(d, "page.pdf")
    x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
    bs = [tuple(float(v) for v in m) for m in WORD.findall(x)]          # pdftotext splits tall maths into "lines":
    b = (min(v[0] for v in bs), min(v[1] for v in bs), max(v[2] for v in bs), max(v[3] for v in bs))   # one line
    wpt, hpt = judge.page_size(pdf, 1)
    k = 200 / 72
    doc = {"pages": [{"page": 1, "page_width": round(wpt * k), "page_height": round(hpt * k), "lines": [
        {"type": typ, "text": read_line, "region": {"top_left_x": int(b[0] * k) - 2, "top_left_y": int(b[1] * k) - 2,
                                                      "width": int((b[2] - b[0]) * k) + 4,
                                                      "height": int((b[3] - b[1]) * k) + 4}}]}]}
    lj = os.path.join(d, "r.lines.json")
    with open(lj, "w") as fh:
        json.dump(doc, fh)
    out = io.StringIO()
    os.makedirs(os.path.join(d, "w"))
    res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out)
    return res, out.getvalue().splitlines()


#: maths symbols a text-layer reading writes as Unicode that pdflatex (utf8) cannot set: the judge dropped them and the
#: page's glyphs stayed unpaired in every reading (board 1511.08771 p2709: U+2283, U+2295, U+2032; U+2217, U+2261,
#: U+2200 on other board pages, 2026-11-18)
MATH_SYMBOLS = {"⊃": "\\supset", "⊕": "\\oplus", "≡": "\\equiv", "∀": "\\forall", "∗": "\\ast"}


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeMathSymbols(unittest.TestCase):
    """⊃ ⊕ ≡ ∀ ∗ ′ in a reading are set and pair with the page's glyphs, in text and in maths; none is dropped."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-sym-")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    PAGE = ("The chain $su_7\\supset so_7$ with $(1)\\oplus(2)$, $a\\equiv b$ for $\\forall x$, $a\\ast b$ and $f'$ "
            "holds.")

    def check(self, name, read_line):
        res, out = _accent_case(self.tmp.name, name, self.PAGE, read_line)
        self.assertEqual([ln for ln in out if ln.startswith("D U+")], [], "no character is dropped")
        self.assertEqual(res["unmappable"], 0)
        for ch in list(MATH_SYMBOLS) + ["′"]:
            ent = "&#x%04x;" % ord(ch)
            hit = [ln for ln in out if ln.startswith("G L0 ") and ln.split()[2].lower() in (ch, ent)]
            self.assertTrue(hit, f"U+{ord(ch):04X} on the page pairs")

    def test_text_mode(self):          # as a text-layer reader writes the line
        self.check("text", "The chain su7⊃so7 with (1)⊕(2), a≡b for ∀x, a∗b and f′ holds.")

    def test_maths_mode(self):         # as the three-page reading of 1511.08771 p2709 wraps it
        self.check("maths", "The chain $su_7⊃so_7$ with $(1)⊕(2)$, $a≡b$ for $∀x$, $a∗b$ "
                            "and $f′$ holds.")

    def test_latex_text(self):
        for ch, cmd in MATH_SYMBOLS.items():
            self.assertEqual(judge.latex_text("a" + ch + "b"), "a$" + cmd + "$b")
            self.assertEqual(judge.latex_text("$a" + ch + "b$"), "$a" + cmd + " b$")
        self.assertEqual(judge.latex_text("f′"), "f$'$")
        self.assertEqual(judge.latex_text("$f′$"), "$f' $")


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeAccents(unittest.TestCase):
    """An accent above a tight line box belongs to its base's line; a rendered accent the page has as ink only is
    measured against that ink (board 2103.01507 p118: MathPix's \\bar over the page's \\overline)."""
    ROUTE = "render"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-acc-")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_bar_pairs_clean(self):
        res, out = _accent_case(self.tmp.name, "bar" + self.ROUTE, "The mean value $\\bar{M}$ of the sample in question.",
                                "The mean value \\(\\bar{M}\\) of the sample in question.", route=self.ROUTE)
        self.assertTrue([ln for ln in out if ln.startswith("G L0 &#xaf; &#xaf; ")], "the macron pairs")
        self.assertEqual((res["a_unpaired"], res["b_unpaired"], res["flagged"]), (0, 0, 0))

    def test_dropped_bar_leaves_the_page_macron_unpaired(self):
        res, out = _accent_case(self.tmp.name, "nobar" + self.ROUTE, "The mean value $\\bar{M}$ of the sample in question.",
                                "The mean value \\(M\\) of the sample in question.", route=self.ROUTE)
        self.assertIn("U L0 A &#xaf;", [" ".join(ln.split()[:4]) for ln in out])
        self.assertGreaterEqual(res["a_unpaired"], 1)

    def test_bar_over_a_drawn_overline_is_measured_on_ink(self):
        res, out = _accent_case(self.tmp.name, "over" + self.ROUTE, "The mean value $\\overline{M}$ of the sample in question.",
                                "The mean value \\(\\bar{M}\\) of the sample in question.", route=self.ROUTE)
        self.assertEqual(res["accents_on_ink"], 1)
        self.assertEqual(res["b_unpaired"], 0, "the rendered bar is not invented ink")


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeUnmappable(unittest.TestCase):
    """A character the judge cannot set is dropped from the snippet and counted (`unmappable`); the line compiles,
    its other glyphs pair, and the page's glyph stays unpaired original ink (pdfdrill 660ea83c writes U+FFFD)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-fffd-")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_replacement_character(self):
        res, out = _accent_case(self.tmp.name, "fffd", "The total of the sample is 42 units in all.",
                                "The total of the sample is 4� units in all.")
        self.assertEqual(res["not_compiled"], 0)
        self.assertEqual(res["unmappable"], 1)
        self.assertIn("U L0 A 2", [" ".join(ln.split()[:4]) for ln in out])                      # the page's glyph, unpaired, once
        self.assertEqual(res["flagged"], 0)
        self.assertGreaterEqual(res["glyphs"], 30)

    def test_character_pdflatex_cannot_set(self):
        res, out = _accent_case(self.tmp.name, "cyr", "The total of the sample is 42 units in all.",
                                "The total of the sample is 42 units in all ж.")
        self.assertEqual(res["not_compiled"], 0)
        self.assertIn("D U+0436 dropped (pdflatex cannot set it)", out)
        self.assertEqual(res["unmappable"], 1)
        self.assertEqual(res["flagged"], 0)


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeRuleInk(unittest.TestCase):
    """Non-glyph ink in formula lines (rules, fraction bars, vincula): a page rule the reading does not draw is
    MISSING, a rendered rule the page does not have is INVENTED (board 2103.01507 p118: \\overline, fractions)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-rule-")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def case(self, name, page, read):
        res, _ = _accent_case(self.tmp.name, name, page, read)
        return res["rules_a"], res["rules_missing"], res["rules_invented"]

    def test_overline_dropped_loses(self):
        self.assertEqual(self.case("o1", "The mean value $\\overline{M}$ of the sample in question.",
                                   "The mean value \\(M\\) of the sample in question."), (1, 1, 0))

    def test_overline_read_is_clean(self):
        self.assertEqual(self.case("o2", "The mean value $\\overline{M}$ of the sample in question.",
                                   "The mean value \\(\\overline{M}\\) of the sample in question."), (1, 0, 0))

    def test_fraction_bar(self):
        self.assertEqual(self.case("f1", "The ratio $\\frac{a+b}{c}$ of the sample in question.",
                                   "The ratio \\(\\frac{a+b}{c}\\) of the sample in question."), (1, 0, 0))
        self.assertEqual(self.case("s1", "The root $\\sqrt{x+y}$ of the sample in question.",
                                   "The root \\(x+y\\) of the sample in question.")[1], 1)

    def test_invented_overline(self):
        self.assertEqual(self.case("i1", "The mean value $M$ of the sample in question.",
                                   "The mean value \\(\\overline{M}\\) of the sample in question."), (0, 0, 1))

    def test_text_lines_are_not_examined(self):
        self.assertEqual(self.case("t1", "The mean value of the sample in question.",
                                   "The mean value of the sample in question."), (0, 0, 0))


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeRuleOwner(unittest.TestCase):
    """A page rule belongs to one line: an overline just under the line above is that lower line's (board
    2103.01507 p118 ours L26/L27)."""

    def test_overline_of_the_next_line(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-own-") as d:
            lines = ["The value $x+y$ is the sum of the two given parts here,",
                     "and $\\overline{M}$ is its closure in the space we use."]
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[10pt]{article}\n\\usepackage[margin=2.5cm]{geometry}\n\\pagestyle{empty}\n"
                         "\\begin{document}\n\\noindent\\raggedright\\baselineskip=10.5pt\n"
                         + "\\\\\n".join(lines) + "\n\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            # tight boxes from the glyphs themselves (mutool quads), as a MathPix-like reader draws them
            cs = judge.chars(pdf, 1, 1)[0]
            rows = {}
            for c in cs:
                rows.setdefault(round(c[2]), []).append(c)
            ys = sorted(rows)
            self.assertEqual(len(ys), 2, ys)
            wpt, hpt = judge.page_size(pdf, 1)
            k = 200 / 72
            reads = ["The value \\(x+y\\) is the sum of the two given parts here,",
                     "and \\(\\overline{M}\\) is its closure in the space we use."]
            doc = {"pages": [{"page": 1, "page_width": round(wpt * k), "page_height": round(hpt * k), "lines": []}]}
            for y, t in zip(ys, reads):
                g = rows[y]
                x0, y0 = min(c[3] for c in g), min(c[4] for c in g)
                x1, y1 = max(c[5] for c in g), max(c[6] for c in g)
                doc["pages"][0]["lines"].append({"type": "text", "text": t, "region": {
                    "top_left_x": int(x0 * k), "top_left_y": int(y0 * k), "width": int((x1 - x0) * k) + 1,
                    "height": int((y1 - y0) * k) + 1}})
            lj = os.path.join(d, "r.lines.json")
            with open(lj, "w") as fh:
                json.dump(doc, fh)
            os.makedirs(os.path.join(d, "w"))
            out = io.StringIO()
            res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out)
            self.assertEqual((res["rules_a"], res["rules_missing"]), (1, 0), out.getvalue()[-400:])


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeRuleNotMaths(unittest.TestCase):
    """The footnote rule above a footnote line read as "\\(^{1}\\) ..." is not a formula's rule (040123 p2 had it as
    one missing rule): inline maths only counts rules over a maths-font glyph."""

    def test_footnote_rule(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-fn-") as d:
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[10pt]{article}\n\\usepackage[margin=2.5cm,paperheight=8cm]{geometry}\n"
                         "\\pagestyle{empty}\n\\begin{document}\n\\noindent A plain line of text with a note."
                         "\\footnote{The note text in the footer of the page.}\n\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            cs = judge.chars(pdf, 1, 1)[0]
            rows = {}
            for c in cs:
                rows.setdefault(round(c[2] / 3), []).append(c)          # baselines; the superscripts join their row
            keys = sorted(rows, key=lambda r: min(c[2] for c in rows[r]))
            body = [c for c in cs if c[2] < 100]
            foot = [c for c in cs if c[2] >= 100]
            self.assertTrue(body and foot)
            wpt, hpt = judge.page_size(pdf, 1)
            k = 200 / 72
            def region(g):
                x0, y0 = min(c[3] for c in g), min(c[4] for c in g)
                x1, y1 = max(c[5] for c in g), max(c[6] for c in g)
                return {"top_left_x": int(x0 * k), "top_left_y": int(y0 * k), "width": int((x1 - x0) * k) + 1,
                        "height": int((y1 - y0) * k) + 1}
            doc = {"pages": [{"page": 1, "page_width": round(wpt * k), "page_height": round(hpt * k), "lines": [
                {"type": "text", "text": "A plain line of text with a note.\\(^{1}\\)", "region": region(body)},
                {"type": "text", "text": "\\(^{1}\\)The note text in the footer of the page.", "region": region(foot)}]}]}
            lj = os.path.join(d, "r.lines.json")
            with open(lj, "w") as fh:
                json.dump(doc, fh)
            os.makedirs(os.path.join(d, "w"))
            out = io.StringIO()
            res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out)
            self.assertEqual(res["rules_missing"], 0, [l for l in out.getvalue().splitlines() if l.startswith("R ")])


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeT1(unittest.TestCase):
    """The snippet is T1: \\guillemetright and \\TH are unavailable in OT1 (board lib:c9daf79960c3 p68, 3 lines did
    not compile), and an OT1 \\~N is a tilde glyph over an N where the page has one composed glyph."""

    def test_guillemets_thorn_and_composed_letters_pair(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-t1-") as d:
            text = "Der Preis »gut« und Þing in España, NIÑO hier."
            res, out = _accent_case(d, "t1", text, text, preamble="\\usepackage[T1]{fontenc}\n")
            self.assertEqual(res["not_compiled"], 0, [l for l in out if l.startswith("N ")])
            self.assertEqual(res["unmappable"], 0)
            self.assertEqual(res["flagged"], 0, [l for l in out if l.startswith("G ") and l.split()[8] == "1"])
            self.assertEqual(res["b_unpaired"], 0, [l for l in out if l.startswith("U ")])


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and os.path.exists(judge.INKCMP),
                     "needs " + ", ".join(TOOLS) + " and build/inkcmp")
class JudgeBornDigitalGlyph(JudgeBornDigital):
    """The glyph-raster route (CONTRACT unit 5) on the same page: every assertion of the rendered route holds, and
    the two routes flag the same glyphs of the planted reading."""
    ROUTE = "glyph"

    def test_routes_flag_the_same_glyphs(self):
        _, _, rows = self.planted_run()
        doc = copy.deepcopy(self.doc)
        for ln, (old, new) in zip(doc["pages"][0]["lines"], self.planted_run()[0]):
            ln["text"] = plant(ln["text"], old, new)
        lj = os.path.join(self.tmp.name, "routes.lines.json")
        with open(lj, "w") as fh:
            json.dump(doc, fh)
        os.makedirs(os.path.join(self.tmp.name, "routes"))
        out = io.StringIO()
        judge.judge(self.pdf, 1, lj, os.path.join(self.tmp.name, "routes"), out=out, route="render")
        render = [ln.split() for ln in out.getvalue().splitlines() if ln.startswith("G ")]
        # the routes differ where only the glyph route can see it: a plant whose outline has another number of
        # holes (e -> c), flagged here by the hole count, not reached by the render route's solid residual
        holes = {("e", "c")}
        self.assertEqual([r[1:5] + [r[8]] for r in rows if (r[2], r[3]) not in holes],
                         [r[1:5] + [r[8]] for r in render if (r[2], r[3]) not in holes])
        self.assertEqual([r[8] for r in rows if (r[2], r[3]) in holes], ["1"] * sum((r[2], r[3]) in holes for r in rows))

    def test_every_plant_is_flagged(self):
        """the goal the render route still misses (e -> c): the glyph route reaches it by the hole count"""
        _, _, rows = self.planted_run()
        self.assertEqual([(r[2], r[3], r[6]) for r in rows if r[4] == "0" and r[8] != "1"], [])


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and os.path.exists(judge.INKCMP),
                     "needs " + ", ".join(TOOLS) + " and build/inkcmp")
class JudgeAccentsGlyph(JudgeAccents):
    """the accent cases on the glyph-raster route: a bar over a drawn overline is measured on the page's ink"""
    ROUTE = "glyph"


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeBatch(unittest.TestCase):
    """`inkcmp-judge --batch` sets several pages' readings in one pdflatex run (compile_group); each page's output
    must equal its own run's, line for line -- also when one page holds a line that does not compile and a character
    pdflatex cannot set, which must not leak into the other page (each job keeps its own failures and drops)."""

    def test_batch_equals_one_page_at_a_time(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-batch-") as d:
            cases = [("clean", "The mean value of the sample, and 70\\% of it.",
                      "The mean value of the sample, and 70% of it."),
                     ("broken", "A line \\textbf{bold} with a macro and a sign here.",
                      "A line \\textbf{bold} with \\nosuchmacro and a sign ⟁ here."),
                     ("accent", "The mean value $\\overline{M}$ of the sample in question.",
                      "The mean value \\(\\bar{M}\\) of the sample in question.")]
            single, jobs = {}, []
            for name, page, read in cases:
                _accent_case(d, name, page, read)
                pdf, lj = os.path.join(d, name, "page.pdf"), os.path.join(d, name, "r.lines.json")
                wd1, wd2 = os.path.join(d, name, "one"), os.path.join(d, name, "batch")
                os.makedirs(wd1)
                out = io.StringIO()
                judge.judge(pdf, 1, lj, wd1, out=out, setter=False)   # pdflatex grouping is what this tests
                single[name] = out.getvalue()
                jobs.append((pdf, 1, lj, wd2))
            st = judge.judge_batch(jobs, bwd=os.path.join(d, "b"), setter=False)
            self.assertEqual(st["batched"], 3)
            self.assertEqual(st["groups"], 1, "one preamble, one pdflatex run")
            for (name, _, _), job in zip(cases, jobs):
                with open(os.path.join(job[3], "judge.out"), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), single[name], name)
            self.assertIn("NOT_COMPILED", single["broken"])
            self.assertIn("D U+", single["broken"], "the character pdflatex cannot set is dropped there")
            self.assertNotIn("NOT_COMPILED", single["clean"] + single["accent"])
            self.assertNotIn("D U+", single["clean"] + single["accent"], "and nowhere else")

    def test_trailing_backslash_does_not_leak(self):
        """a reading line that ends in a backslash (MathPix "(solution*\\") swallowed its own \\end{pline}: the group
        ended inside it, and the error pdflatex reports at \\end{document} was booked on whichever job's line came
        last in the group (board 49-job batch: lib:4859edad9294 p1287 line 25 NOT_COMPILED, ours 0.0435 -> 0.0625).
        Every job's judge.out is the same alone, in a group of two, and with the order reversed."""
        with tempfile.TemporaryDirectory(prefix="test-judge-batch-bs-") as d:
            cases = [("slash", "Results of the solution and of the rest.", "Results of the solution and of the rest\\"),
                     ("last", "The mean value of the sample, and the rest of it.",
                      "The mean value of the sample, and the rest of it.")]
            single, jobs = {}, []
            for name, page, read in cases:
                _accent_case(d, name, page, read)
                pdf, lj = os.path.join(d, name, "page.pdf"), os.path.join(d, name, "r.lines.json")
                os.makedirs(os.path.join(d, name, "one"))
                out = io.StringIO()
                judge.judge(pdf, 1, lj, os.path.join(d, name, "one"), out=out, setter=False)
                single[name] = out.getvalue()
                jobs.append((pdf, 1, lj))
            self.assertNotIn("NOT_COMPILED", single["slash"] + single["last"])
            for tag, order in (("fwd", jobs), ("rev", jobs[::-1])):
                js = [j + (os.path.join(os.path.dirname(j[0]), tag),) for j in order]
                st = judge.judge_batch(js, bwd=os.path.join(d, "b-" + tag), setter=False)
                self.assertEqual((st["batched"], st["groups"]), (2, 1))
                for j in js:
                    name = os.path.basename(os.path.dirname(j[0]))
                    with open(os.path.join(j[3], "judge.out"), encoding="utf-8") as fh:
                        self.assertEqual(fh.read(), single[name], (tag, name))

    def test_latex_text_trailing_backslash(self):
        self.assertEqual(judge.latex_text("solution*\\"), "solution*\\textbackslash{}")
        self.assertEqual(judge.latex_text("a \\(x\\) b"), "a \\(x\\) b")


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeSetter(unittest.TestCase):
    """A page without formula lines is set in the page's own font program (inkdrill/setter.py), not by pdflatex:
    a clean reading has no flag, a planted letter is flagged, a character the page never printed comes from the TeX
    tree's Type 1 of the same font, and a page with a formula line is still set by pdflatex."""

    def run_case(self, d, name, page, read, preamble=""):
        res, out = _accent_case(d, name, page, read, preamble)
        return res, "\n".join(out)

    def test_setter_clean_planted_fallback_and_formula(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-setter-") as d:
            line = "Hilbert and Noether wrote about rings, fields and their ideals here."
            res, out = self.run_case(d, "clean", line, line)
            self.assertEqual(res.get("setter"), 1, out[:300])
            self.assertIn("set by setter", out)
            self.assertEqual((res["flagged"], res["a_unpaired"], res["b_unpaired"]), (0, 0, 0))
            res, out = self.run_case(d, "plant", line, line.replace("rings", "rinqs"))
            self.assertEqual(res.get("setter"), 1)
            self.assertGreaterEqual(res["flagged"], 1, "the planted q is flagged")
            # 'Q' and 'z' are not on the page: from cmr10.pfb (the page's font is a subset)
            res, out = self.run_case(d, "fallback", line, line.replace("rings", "Qings").replace("fields", "fieldz"))
            self.assertEqual(res.get("setter"), 1, "the subset gap is filled from the TeX tree")
            self.assertGreaterEqual(res["flagged"], 2)
            res, out = self.run_case(d, "formula", "Let $x$ be a ring here.", "Let \\(x\\) be a ring here.")
            self.assertNotIn("setter", res)
            self.assertIn("set by pdflatex", out)

    def test_text_face_commands_set_in_the_face(self):
        """a reading's \\textbf{..} (pdfdrill 35340ad3 writes the face into the text) is set in the page's bold font,
        never as the characters \\ t e x t b f { }: board 2311.09533v3 p10 gained 9 or 18 unpaired B glyphs on 7
        lines (b_unpaired 5 -> 86). The same line read without the command pairs as many glyphs, and is flagged in
        style only where the face was not read."""
        with tempfile.TemporaryDirectory(prefix="test-judge-faces-") as d:
            page = "\\textbf{Query:} where did the rings and \\textit{fields} go here."
            times = "\\usepackage{times}\n"                 # the board page's Nimbus: its TeX-tree copy has a backslash
            res, out = self.run_case(d, "plain", page, "Query: where did the rings and fields go here.", times)
            self.assertEqual(res.get("setter"), 1, out[:300])
            self.assertGreaterEqual(res["style"], 6, "read without its face, Query: and fields are style findings")
            nested = "\\textbf{Query\\textit{:}} where did the rings and \\textit{fields} go here."
            for name, read, style in (("faced", page, 0), ("nested", nested, 1)):   # the colon read bold italic
                rf, out = self.run_case(d, name, page, read, times)
                self.assertEqual(rf.get("setter"), 1, out[:300])
                self.assertEqual((rf["a_unpaired"], rf["b_unpaired"]), (res["a_unpaired"], res["b_unpaired"]), name)
                self.assertEqual((rf["glyphs"], rf["glyph"], rf["style"]), (res["glyphs"], 0, style), name)

    def test_texttt_on_a_cmtt_page_is_set_in_cmtt(self):
        """a CMTT page's \\texttt is set in OT1 cmtt, not T1's cm-super sftt: board lib:d68e0271bd4d p334 (CMTT8
        at 7 pt, pdflatex route) had 10 glyph findings, m and -, in typewriter lines that read the face right"""
        with tempfile.TemporaryDirectory(prefix="test-judge-tt-") as d:
            fam, pre = judge.font_preamble(*self.page(d, "cmtt"))
            self.assertIn(judge.OT1_TT, pre)
            page = "{\\scriptsize \\texttt{metadata dc-element m-m mm--m} 92 DC $x$}"
            res, out = self.run_case(d, "tt", page, "\\texttt{metadata dc-element m-m mm--m} 92 DC \\(x\\)")
            self.assertIn("set by pdflatex", out)
            self.assertEqual((res["a_unpaired"], res["b_unpaired"], res["glyph"], res["style"]), (0, 0, 0, 0),
                             [ln for ln in out.split("\n") if ln.startswith("G ") and ln.split()[8] == "1"])

    def page(self, d, name):
        _accent_case(d, name, "Some \\texttt{typewriter} text.", "Some text.")
        return os.path.join(d, name, "page.pdf"), 1

    def test_no_ot1_tt_without_cmtt(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-tt-") as d:
            _accent_case(d, "rm", "Some plain text here.", "Some plain text here.")
            self.assertNotIn(judge.OT1_TT, judge.font_preamble(os.path.join(d, "rm", "page.pdf"), 1)[1])

    def test_one_route_per_page(self):
        """the route is the page's: a text-only reading goes to pdflatex when a peer reading of the same page has a
        formula line (coordinator, 2026-11-17: the columns of a page are judged by one instrument)"""
        with tempfile.TemporaryDirectory(prefix="test-judge-peers-") as d:
            line = "Hilbert and Noether wrote about rings, fields and their ideals here."
            res, out = self.run_case(d, "alone", line, line)
            self.assertEqual(res.get("setter"), 1)
            lj = os.path.join(d, "alone", "r.lines.json")
            with open(lj) as fh:
                doc = json.load(fh)
            doc["pages"][0]["lines"][0]["text"] = "Hilbert and \\(x\\) wrote about rings, fields and their ideals here."
            peer = os.path.join(d, "peer.lines.json")
            with open(peer, "w") as fh:
                json.dump(doc, fh)
            for name, peers, want in (("withpeer", [peer], "pdflatex"), ("textpeer", [lj], "setter")):
                wd = os.path.join(d, name)
                os.makedirs(wd)
                out = io.StringIO()
                res = judge.judge(os.path.join(d, "alone", "page.pdf"), 1, lj, wd, out=out, peers=peers)
                self.assertIn("set by " + want, out.getvalue(), name)


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeSmallCapsByGlyphName(unittest.TestCase):
    """Libertine's small capitals are glyphs of the text font's own program (t.sc in LinLibertineT): read plain, they
    are style findings, never glyph findings; read with \\textsc, nothing is flagged (board 1805.01788 p3, 56)."""

    def run_case(self, d, name, read):
        _accent_case(d, name, "Definition 1 (\\textsc{fairness of attention}). A ranking offers this.", read,
                     "\\usepackage{libertine}\n")
        out = io.StringIO()
        res = judge.judge(os.path.join(d, name, "page.pdf"), 1, os.path.join(d, name, "r.lines.json"),
                          os.path.join(d, name, "g"), out=out, route="glyph", setter=False)
        return res, out.getvalue()

    def test_small_caps_read_plain_are_style(self):
        self.assertEqual((judge.glyph_face("t.sc"), judge.glyph_face("q.sc_u.sc"), judge.glyph_face("t"),
                          judge.glyph_face("f_i"), judge.glyph_face("scaron")), ("c", "c", "", "", ""))
        self.assertEqual((judge.face("LinLibertineT", "e.sc"), judge.face("CMCSC10", "e"), judge.face("CMR10")),
                         ("c", "c", "-"))
        with tempfile.TemporaryDirectory(prefix="test-judge-sc-") as d:
            res, out = self.run_case(d, "plain", "Definition 1 (fairness of attention). A ranking offers this.")
            self.assertGreaterEqual(res["style"], 15, out[-800:])
            self.assertEqual(res["glyph"], 0, [ln for ln in out.splitlines() if ln.startswith("G ")
                                               and ln.split()[8] == "1"])
            res, out = self.run_case(d, "sc", "Definition 1 (\\textsc{fairness of attention}). A ranking offers this.")
            self.assertEqual((res["glyph"], res["style"]), (0, 0), out[-800:])

    def test_composite_glyph_pairs_one_letter_per_name_part(self):
        """q.sc_u.sc is one glyph whose ToUnicode reads q (board 1805.01788 p3, Equity): the correct reading pairs
        both letters, and the reading that drops the u leaves the page's u unpaired, never the reverse"""
        self.assertEqual((judge.name_letters("q.sc_u.sc"), judge.name_letters("f_f_i"), judge.name_letters("q.sc"),
                          judge.name_letters("uni0041_uni0042")), ("qu", "ffi", "", ""))
        with tempfile.TemporaryDirectory(prefix="test-judge-qu-") as d:
            page = "Definition 1 (\\textsc{equity of attention}). A ranking offers this."
            got = {}
            for name, read in (("qu", "Definition 1 (\\textsc{equity of attention}). A ranking offers this."),
                               ("q", "Definition 1 (\\textsc{eqity of attention}). A ranking offers this.")):
                _accent_case(d, name, page, read, "\\usepackage{libertine}\n")
                out = io.StringIO()
                res = judge.judge(os.path.join(d, name, "page.pdf"), 1, os.path.join(d, name, "r.lines.json"),
                                  os.path.join(d, name, "g"), out=out, route="glyph", setter=False)
                trace = judge.trace_text(os.path.join(d, name, "page.pdf"), 1, 1)
                self.assertIn('glyph="q.sc_u.sc"', trace)          # the fixture holds the composite
                got[name] = (res["unpaired_A"], res["unpaired_B"])
            self.assertEqual(got, {"qu": (0, 0), "q": (1, 0)}, got)
            # read plain, the composite's u is a small capital on the page's side, like its q
            _accent_case(d, "plain", page, "Definition 1 (equity of attention). A ranking offers this.",
                         "\\usepackage{libertine}\n")
            out = io.StringIO()
            judge.judge(os.path.join(d, "plain", "page.pdf"), 1, os.path.join(d, "plain", "r.lines.json"),
                        os.path.join(d, "plain", "g"), out=out, route="glyph", setter=False)
            qu = [ln.split() for ln in out.getvalue().splitlines() if ln.startswith(("G L0 q ", "G L0 u "))]
            self.assertEqual([(g[2], g[9]) for g in qu], [("q", "c"), ("u", "c")], qu)


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeCommaUnderABaselineBox(unittest.TestCase):
    """a reading's box that ends at the baseline (board37 1805.01788 p3 L54, ours): the commas' centres lie below
    it, their origins inside; they belong to the line and pair"""

    def test_commas_pair(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-comma-") as d:
            line = "for one-time rankings, and, as any static model, will steadily accumulate"
            _accent_case(d, "c", line, line)
            pdf, lj = os.path.join(d, "c", "page.pdf"), os.path.join(d, "c", "r.lines.json")
            with open(lj) as fh:
                doc = json.load(fh)
            pg = doc["pages"][0]
            k = pg["page_width"] / judge.page_size(pdf, 1)[0]                  # pt -> lines.json px
            base = max(c[2] for c in judge.chars(pdf, 1, 1)[0])               # the baseline, pt
            r = pg["lines"][0]["region"]
            top = r["top_left_y"]
            for n, end in enumerate((int(base * k) + 1, int(base * k))):    # at the baseline, as ours' L54; one
                r["height"] = end - top                                      # reading pixel above it, as its L5
                with open(lj, "w") as fh:
                    json.dump(doc, fh)
                commas = [c for c in judge.chars(pdf, 1, 1)[0] if c[0] == ","]
                self.assertEqual(len(commas), 3)
                self.assertTrue(all((c[4] + c[6]) / 2 * k > end for c in commas))         # centres below the box
                out = io.StringIO()
                res = judge.judge(pdf, 1, lj, os.path.join(d, "c", "g%d" % n), out=out, route="glyph", setter=False)
                self.assertEqual((res["unpaired_A"], res["unpaired_B"]), (0, 0), (end, out.getvalue()[-600:]))


class SetterSuperscriptsAndLigatureNames(unittest.TestCase):
    """the setter reads a footnote marker written as maths as a text superscript, at the page's own scale and rise,
    and a ligature named by its letters (f_i) as a ligature (board lib:69bc5c71b881 p16, CircularStd)"""

    def test_units(self):
        from inkdrill import setter
        self.assertEqual([setter.lig_of(n) for n in ("fi", "f_i", "f_f_i", "f_l", "f", "q.sc_u.sc", "a_b")],
                         ["fi", "fi", "ffi", "fl", None, None, None])
        self.assertEqual(setter.supered("systems. ${}^{30}  $ Next"), "systems. \\textsuperscript{30} Next")
        self.assertEqual(setter.supered("risk. \\({ }^{31}\\) S"), "risk. \\textsuperscript{31} S")
        self.assertTrue(setter.text_line("a ${}^{3}$ b") and not setter.text_line("a $x^{2}$ b"))
        self.assertEqual(setter.faced(setter.supered("x ${}^{3}$")), [("", "x "), ("s", "3")])
        body = [("x", "x", 10.0 * n, 700.0, "F", (10.0, 0, 0, 10.0)) for n in range(10)]
        sup = [("3", "three", 101.0, 703.5, "F", (7.0, 0, 0, 7.0))]
        self.assertEqual(setter.sup_metrics(body + sup), (0.7, 0.35))
        sc, rise = setter.sup_metrics(body + [("3", "three", 101.0, 704.0, "F", (6.0, 0, 0, 6.0))])
        self.assertAlmostEqual(sc, 0.6) and self.assertAlmostEqual(rise, 0.4)
        self.assertEqual(setter.sup_metrics(body), (0.7, 0.35))                     # none on the page


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeUnmappedFamilySetter(unittest.TestCase):
    """a page in a family FAMILIES has no entry for is set by the setter from its own program, its footnote marker
    included, instead of being left unjudged (board lib:69bc5c71b881 p16: STATUS FONT_UNMAPPED in both columns)"""

    def test_charter_page_is_judged(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-unmapped-") as d:
            _accent_case(d, "u", "Fine fluffy waffles\\textsuperscript{3} are offered here.",
                         "Fine fluffy waffles${}^{3}$ are offered here.", "\\usepackage{charter}\n")
            pdf = os.path.join(d, "u", "page.pdf")
            self.assertEqual(judge.font_preamble(pdf, 1)[0], "unmapped")
            out = io.StringIO()
            res = judge.judge(pdf, 1, os.path.join(d, "u", "r.lines.json"), os.path.join(d, "u", "g"), out=out,
                              route="glyph")
            text = out.getvalue()
            self.assertIn("set by setter", text)
            self.assertIn("STATUS OK", text)
            self.assertEqual((res["unpaired_A"], res["unpaired_B"], res["glyph"]), (0, 0, 0), text[-900:])


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeScanRoute(unittest.TestCase):
    """--route scan (inkdrill/scanjudge.py): a page judged by its ink alone"""
    LINE = "Wenn also die Koefficienten gegeben sind, so findet man leicht die Wurzeln der Gleichung."

    def run_scan(self, d, name, read):
        _accent_case(d, name, self.LINE, read)                  # the fixture (its own glyph-route run included)
        real = (judge.chars, judge.trace)

        def refuse(*a, **k):
            raise AssertionError("the scan route read the text layer")
        judge.chars = judge.trace = refuse                       # G1: only around the scan route
        try:
            out = io.StringIO()
            res = judge.judge(os.path.join(d, name, "page.pdf"), 1, os.path.join(d, name, "r.lines.json"),
                              os.path.join(d, name, "g"), out=out, route="scan")
        finally:
            judge.chars, judge.trace = real
        return res, out.getvalue()

    def residual(self, text):
        return float(re.search(r"^SCAN1 .* residual (\S+)$", text, re.M)[1])

    def test_scan_route(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-scan-") as d:
            res, good = self.run_scan(d, "good", self.LINE)
            _, worse = self.run_scan(d, "drop", "Wenn also die gegeben sind, so findet man die Wurzeln.")
            _, broken = self.run_scan(d, "broken", "Wenn also \\input{no-such-file-anywhere}")   # fatal at once
            for t in (good, worse, broken):
                self.assertIn(scanjudge.BLIND, t)                               # G3
            self.assertIn("STATUS OK", good)
            self.assertGreater(res["comps"], 50)
            self.assertLess(self.residual(good), self.residual(worse))
            self.assertIn("N L0 NOT_SET", broken)                                # G2: nothing set, all missing
            real = judge.compile_group                         # G2's other door: no snippet PDF at all (the
            judge.compile_group = lambda jobs, pre, wd, *a: (os.path.join(wd, "none.pdf"), [({}, set(), 0)])
            try:                                               # experiment's three documents that broke whole)
                out = io.StringIO()
                judge.judge(os.path.join(d, "good", "page.pdf"), 1, os.path.join(d, "good", "r.lines.json"),
                            os.path.join(d, "good", "g2"), out=out, route="scan")
            finally:
                judge.compile_group = real
            self.assertIn("N L0 NOT_SET", out.getvalue())
            summary = re.search(r"^SCAN1 .*$", broken, re.M)[0]
            self.assertEqual(int(re.search(r" missing (\d+)", summary)[1]), int(re.search(r" comps (\d+)", summary)[1]))


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgePdflatexNeverHangs(unittest.TestCase):
    """a line holding \\end{document} made pdflatex spin without end (found testing the scan route, 2026-11-21): it is
    stripped; and whatever else makes a run loop, the run times out and no line of the group is trusted"""

    def test_end_document_in_a_line(self):
        self.assertNotIn("document", judge.latex_text("Wenn also }\\end{document} x \\begin {document}"))
        with tempfile.TemporaryDirectory(prefix="test-judge-hang-") as d:
            t0 = time.monotonic()
            pdf, got = judge.compile_group([(["Wenn also }\\end{document}", "und so weiter"], [10.0, 10.0])], "", d)
            self.assertLess(time.monotonic() - t0, 60)
            self.assertTrue(os.path.exists(pdf))
            self.assertNotIn(1, got[0][0])                                  # the plain line is set

    def test_timeout_fails_every_line(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-timeout-") as d:
            keep = judge.PDFLATEX_TIMEOUT
            judge.PDFLATEX_TIMEOUT = 0.01
            try:
                pdf, got = judge.compile_group([(["one", "two"], [10.0, 10.0])], "", d)
            finally:
                judge.PDFLATEX_TIMEOUT = keep
            self.assertEqual(got[0][0], {0: "pdflatex timed out", 1: "pdflatex timed out"})
            self.assertFalse(os.path.exists(pdf))


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeMathsDigitsReadAsText(unittest.TestCase):
    """numbers the author typed as maths on a Times page ($15$ cases, board 0864 p4) and a reading wrote as text: the
    same digits in another face, style findings, never glyph findings"""

    def test_maths_digits_are_style(self):
        self.assertTrue(judge.maths_digit("7", "7", "ABCDEF+CMR10", "NimbusRomNo9L-Regu", "times"))
        self.assertFalse(judge.maths_digit("7", "7", "CMR10", "CMR10", "times"))       # set as maths on both sides
        self.assertFalse(judge.maths_digit("7", "7", "CMR10", "NimbusRomNo9L-Regu", "cm"))   # a CM page: text too
        self.assertFalse(judge.maths_digit("a", "a", "CMR10", "NimbusRomNo9L-Regu", "times"))
        with tempfile.TemporaryDirectory(prefix="test-judge-mdig-") as d:
            line = "In particular, $15$ cases have at least a factor of $2$ and $760$ more."
            _accent_case(d, "m", line, "In particular, 15 cases have at least a factor of 2 and 760 more.",
                         "\\usepackage{times}\n")
            out = io.StringIO()
            res = judge.judge(os.path.join(d, "m", "page.pdf"), 1, os.path.join(d, "m", "r.lines.json"),
                              os.path.join(d, "m", "g"), out=out, route="glyph", setter=False)
            digits = [ln.split() for ln in out.getvalue().splitlines() if ln.startswith("G ") and ln.split()[2].isdigit()]
            self.assertEqual(len(digits), 6)
            self.assertTrue(all(g[9] == "x" and g[10] == "-" for g in digits), digits)
            self.assertEqual(res["glyph"], 0, [g for g in digits if g[8] == "1"])


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeMsbmHbar(unittest.TestCase):
    """a page whose \\hbar is MSBM's planckover2pi (amsfonts) is judged with that \\hbar, whatever package defines its
    own: mathptmx's is a macron over h (board lib:3fe71b4ef018 p328: 6 page glyphs and 6 macrons unpaired)"""

    def names(self, d, pre):
        _, got = judge.compile_group([(["$x\\hbar y$"], [10.0])], pre, d)
        return re.findall(r'glyph="([^"]*)"', judge.trace_text(os.path.join(d, "lines.pdf"), got[0][2] + 1, got[0][2] + 1))

    def test_hbar(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-hbar-") as d:
            os.makedirs(os.path.join(d, "a"))
            os.makedirs(os.path.join(d, "b"))
            self.assertIn("macron", self.names(os.path.join(d, "a"), "\\usepackage{mathptmx}"))     # the defect
            got = self.names(os.path.join(d, "b"), "\\usepackage{mathptmx}\n" + judge.MSBM_HBAR)
            self.assertIn("planckover2pi1", got)
            self.assertNotIn("macron", got)
            for name, page, want in (("m", "Planck: $E = \\hbar\\omega$ here.", True),
                                     ("n", "Planck: $E = h\\omega$ here.", False)):
                _accent_case(d, name, page, "Planck: \\(E = \\hbar\\omega\\) here.", "\\usepackage{amsfonts}\n")
                judge.judge(os.path.join(d, name, "page.pdf"), 1, os.path.join(d, name, "r.lines.json"),
                            os.path.join(d, name, "g"), out=io.StringIO(), route="glyph", setter=False)
                with open(os.path.join(d, name, "g", "lines.tex")) as fh:
                    self.assertEqual(judge.MSBM_HBAR in fh.read(), want, name)


class RepairByPosition(unittest.TestCase):
    """the sequence pairing's leftovers matched again by character and place along the line (board
    lib:3fe71b4ef018 p328 L1: a fraction's numerator and denominator interleave differently on the two sides)"""

    def c(self, ch, x):
        return (ch, x, 0.0, x, 0.0, x + 1, 1.0, "F", 10.0)

    def test_repair(self):
        a = [self.c("M", 0), self.c("*", 10), self.c("2", 11), self.c("c", 20)]
        b = [self.c("M", 0), self.c("2", 10.7), self.c("*", 10.2), self.c("c", 20)]   # read in another x order
        pairs = [(a[0], b[0]), (a[1], b[1]), (a[3], b[3])]                   # * paired with 2, as difflib left it
        got, ua, ub = judge.repair(pairs, [a[2]], [b[2]], a, b)
        self.assertEqual(sorted((p[0][0], p[1][0]) for p in got), [("*", "*"), ("2", "2"), ("M", "M"), ("c", "c")])
        self.assertEqual((ua, ub), ([], []))
        far = [self.c("M", 0), self.c("*", 10), self.c("c", 20)]           # an equal character too far away stays
        got, ua, ub = judge.repair([(a[0], b[0])], [a[1]], [self.c("*", 19)], a, b + [self.c("*", 19)])
        self.assertEqual((len(got), len(ua), len(ub)), (1, 1, 1))
        same = [(a[0], b[0]), (a[3], b[3])]                                  # pairs of equal characters untouched
        self.assertEqual(judge.repair(same, [], [], a, b)[0], same)


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeBarBaselineOutsideFractions(unittest.TestCase):
    """the baseline a rendered bar moves by is taken outside every fraction: a display whose numerator holds most of
    its glyphs (board lib:3fe71b4ef018 p328 L20) put the bar by the numerator's offset, 14 px low there"""

    def test_numerator_heavy_display(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-numbar-") as d:
            page = "$\\displaystyle A = \\tfrac{abcdefghijklmnop + qrstuvw}{x + y}$"
            read = "\\[ A = \\frac{abcdefghijklmnop + qrstuvw}{x + y} \\]"
            _accent_case(d, "n", page, read, "\\usepackage{amsmath}\n", typ="math")
            out = io.StringIO()
            res = judge.judge(os.path.join(d, "n", "page.pdf"), 1, os.path.join(d, "n", "r.lines.json"),
                              os.path.join(d, "n", "g"), out=out, route="glyph", setter=False)
            rl = [ln for ln in out.getvalue().splitlines() if ln[:2] == "R "]
            self.assertEqual(res["rules_a"], 1, rl)
            self.assertEqual(res["rules_missing"], 0, rl)                     # the page's bar is met
            long = [r for r in rl if r.split()[2] == "invented" and int(r.split()[5]) - int(r.split()[3]) > 40]
            self.assertEqual(long, [])                                        # and no rendered bar is left over

    def test_prose_is_not_repaired(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-prose-") as d:
            rest = " cat sat on a mat with care and then it went off to find another quiet place to rest"
            _accent_case(d, "p", "the" + rest, "teh" + rest)          # long: 3 % of it is wider than a letter
            out = io.StringIO()
            judge.judge(os.path.join(d, "p", "page.pdf"), 1, os.path.join(d, "p", "r.lines.json"),
                        os.path.join(d, "p", "g"), out=out, route="glyph", setter=False)
            text = out.getvalue()
            self.assertIn("U L0 A e", text)                                    # the swap stays visible: the e is
            self.assertIn("U L0 B e", text)                                    # not matched again on a prose line


class CodeLineUnit(unittest.TestCase):
    """a code line is the characters it is (board RE4B-DE p559)"""

    def test_code_tex(self):
        self.assertEqual(judge.code_tex("@V$qp14 a_b"), "@V\\char36{}qp14 a\\char95{}b")
        self.assertEqual(judge.code_tex("\\texttt{x\\_y$z}"), "\\texttt{x\\char95{}y\\char36{}z}")
        self.assertEqual(judge.line_tex({"type": "code", "text": "a%b"}), "a\\char37{}b")
        # LaTeX in a code-typed line stays LaTeX (board lib:d68e0271bd4d p334's table rows)
        for t in ("\\texttt{idt} 130 RT \\texttt{style,}", "m:math 114 – $\\langle$CMel$\\rangle$+"):
            self.assertEqual(judge.code_tex(t), t)
        self.assertEqual(judge.code_tex("hint 209 QUIZ xml:id, style, + CMP*"), "hint 209 QUIZ xml:id, style, + CMP*")
        self.assertEqual(judge.line_tex({"type": "text", "text": "a $x$ b"}), "a $x$ b")     # text lines unchanged


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeDejaVuCodePage(unittest.TestCase):
    """a page wholly in DejaVu Sans, with a code line in DejaVu Sans Mono holding a $ (board RE4B-DE p559: STATUS
    FONT_UNMAPPED in both columns; then six code lines did not compile)"""

    def test_dejavu_page_code_line(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-dejavu-") as d:
            pre = "\\usepackage{DejaVuSans}\\usepackage{DejaVuSansMono}\\renewcommand{\\familydefault}{\\sfdefault}\n"
            _accent_case(d, "c", "Symbole: \\texttt{@TModule@ValidWindow\\$qp14TWindowsObject}",    # sans words vote
                         "Symbole: @TModule@ValidWindow$qp14TWindowsObject", pre, typ="code")    # the family
            pdf = os.path.join(d, "c", "page.pdf")
            self.assertEqual(judge.font_preamble(pdf, 1)[0], "dejavusans")
            out = io.StringIO()
            res = judge.judge(pdf, 1, os.path.join(d, "c", "r.lines.json"), os.path.join(d, "c", "g"), out=out,
                              route="glyph", setter=False)
            self.assertEqual((res["not_compiled"], res["unpaired_A"], res["unpaired_B"], res["glyph"]), (0, 0, 0, 0),
                             out.getvalue()[-1200:])


class LigatureLetterOrder(unittest.TestCase):
    """mutool's later letter of a ligature is a zero-width character at the glyph's right edge, which can lie past
    the next letter's origin (board35 lib:c57ac038ae5f p293: "find", the fi's i at 186.12459, the n at 186.10567):
    it is moved inside the ligature's advance, so it sorts before the next letter"""

    def test_fi_sorts_before_the_next_letter(self):
        f = ("f", 180.58539, 145.4, 180.58539, 138.0, 186.12459, 146.0, "Times-Roman", 9.963)
        i = ("i", 186.12459, 145.4, 186.12459, 140.0, 186.12459, 146.0, "Times-Roman", 9.963)
        n = ("n", 186.10567, 145.4, 186.10567, 140.0, 191.14575, 146.0, "Times-Roman", 9.963)
        d = ("d", 191.14575, 145.4, 191.14575, 138.0, 196.1, 146.0, "Times-Roman", 9.963)
        self.assertEqual("".join(c[0] for c in sorted([f, i, n, d], key=lambda c: c[1])), "fnid")   # the defect
        got = judge.spelled([f, i, n, d], [])
        self.assertEqual("".join(c[0] for c in sorted(got, key=lambda c: c[1])), "find")
        self.assertEqual([c[1] for c in got if c[0] != "i"], [f[1], n[1], d[1]])       # the others stay put
        self.assertTrue(f[1] < got[1][1] < n[1], got[1])


class MathSizesUnit(unittest.TestCase):
    """the page's maths sizes and a formula line's size vote (board 1805.01788 p3)"""

    def glyphs(self, *spec):
        return [("x", 0, 0, 0, 0, 1, 1, font, size) for font, size, n in spec for _ in range(n)]

    def test_script_size_measured_from_the_maths_letters(self):
        ac = self.glyphs(("ABCDEF+LinLibertineT", 8.966, 200), ("GMLQBI+LinLibertineI", 8.966, 35),
                         ("IVFFFD+LinLibertineI7", 7.273, 49), ("XYZ+LinLibertineT", 7.273, 30))
        self.assertEqual(judge.math_sizes(ac), r"\DeclareMathSizes{9}{9}{7.3}{4.5}")
        ac += self.glyphs(("ZDIDON+LinLibertineI5", 5.479, 4))
        self.assertEqual(judge.math_sizes(ac), r"\DeclareMathSizes{9}{9}{7.3}{5.5}")
        self.assertEqual(judge.math_sizes(self.glyphs(("CMR10", 9.963, 300), ("CMMI10", 9.963, 20),
                                                       ("CMMI7", 6.974, 12))), r"\DeclareMathSizes{10}{10}{7}{5}")
        self.assertEqual(judge.math_sizes(self.glyphs(("CMR10", 9.963, 300), ("CMMI7", 6.974, 4))), "")
        self.assertEqual(judge.math_sizes(self.glyphs(("CMR10", 9.963, 300))), "")
        self.assertEqual(judge.math_sizes(self.glyphs(("Times-Roman", 9.963, 300), ("QtxpctLqshvdMTMI", 7.572, 93),
                                                       ("QtxpctLqshvdMTMI", 9.963, 83))), "")    # MathTime: none
        self.assertEqual(judge.math_sizes(self.glyphs(("CMR10", 10.062, 300), ("CMMI7", 6.974, 26),
                                                       ("CMMI6", 5.978, 6))), r"\DeclareMathSizes{10}{10}{7}{6}")

    def test_formula_line_takes_its_base_size(self):
        cs = [8.966] * 11 + [7.273] * 12
        self.assertEqual((judge.vote_size(cs, True), judge.vote_size(cs, False)), (8.966, 7.273))
        self.assertEqual(judge.vote_size([9.963] * 30 + [4.981] * 2, True), 9.963)    # scripts at 0.5: the base
        self.assertEqual(judge.vote_size([9.963] * 30, True), 9.963)                  # no scripts: the commonest
        self.assertEqual(judge.vote_size([6.974] * 23 + [9.963] * 5, True), 9.963)     # a \tfrac numerator
        self.assertEqual(judge.vote_size([6.974] * 23 + [9.963] * 1, True), 6.974)     # one glyph is no base
        self.assertEqual(judge.vote_size([], True), 10.0)
        self.assertEqual(judge.vote_size([8.903] * 26 + [8.966] * 11 + [5.978] * 2, True), 8.903)  # two families


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgePageMathSizes(unittest.TestCase):
    """a page whose maths sizes are not its package's (2018's newtxmath set 9 pt scripts at 7.3 pt, today's at 6.6:
    board 1805.01788 p3): the scripts are set at the page's sizes, so a correct reading's scripts pair cleanly"""

    def test_scripts_at_the_page_size(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-msz-") as d:
            line = "Here $a_{i} + b_{j}^{k} = c_{m} + d_{n} + e_{p} + f_{q}$ holds for all of them."
            _accent_case(d, "s", line, "Here \\(a_{i} + b_{j}^{k} = c_{m} + d_{n} + e_{p} + f_{q}\\) holds for all of them.",
                         "\\DeclareMathSizes{10}{10}{8}{6}\n")
            pdf = os.path.join(d, "s", "page.pdf")
            self.assertIn("CMMI8", subprocess.run(["pdffonts", pdf], capture_output=True, text=True).stdout)
            out = io.StringIO()
            res = judge.judge(pdf, 1, os.path.join(d, "s", "r.lines.json"), os.path.join(d, "s", "g"), out=out,
                              route="glyph", setter=False)
            flagged = [ln for ln in out.getvalue().splitlines() if ln.startswith("G ") and ln.split()[8] == "1"]
            self.assertEqual((res["glyph"], res["style"]), (0, 0), flagged)


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeFractionBarOnTheAxis(unittest.TestCase):
    """a display line's text-style fractions read as display-style \\frac (board34 lib:6d90e8d27b8b p174 L14): the
    numerals are glyph findings, the bars lie on the same maths axis and match; a bar placed by its own numeral's
    offset was a missing and an invented rule per fraction"""

    def test_style_of_a_fraction_moves_glyphs_not_its_bar(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-axis-") as d:
            tail = ", \\mathrm{also} \\ln |x| = c + \\ln |t + 1| - t + \\ln |t + 1|"     # p174's baseline, as there
            page = "$\\displaystyle \\int \\tfrac{dx}{x} = \\int \\tfrac{t^2}{t+1}\\, dt = \\tfrac{1}{2} t^2 + c" + tail + "$"
            read = "\\[ \\int \\frac{dx}{x} = \\int \\frac{t^2}{t+1}\\, dt = \\frac{1}{2} t^2 + c" + tail + " \\]"
            _accent_case(d, "f", page, read, "\\usepackage{amsmath}\n", typ="math")
            buf = io.StringIO()
            res = judge.judge(os.path.join(d, "f", "page.pdf"), 1, os.path.join(d, "f", "r.lines.json"),
                              os.path.join(d, "f", "g"), out=buf, route="glyph", setter=False)
            out = buf.getvalue().splitlines()
            self.assertEqual(res["rules_a"], 3, out[-12:])                    # the fixture holds three bars
            self.assertGreater(res["glyph"], 0, out[-12:])                    # and the wrong style is found
            self.assertEqual((res["rules_missing"], res["rules_invented"]), (0, 0), [ln for ln in out if ln[:2] == "R "])


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeEsintIntegrals(unittest.TestCase):
    """esint's integrals are named acute and circumflex and have no ToUnicode map (board lib:6d90e8d27b8b p174): the
    outline decides, so both sides read them as integrals and a correct \\int reading pairs them as glyphs"""

    def test_esint_integral_reads_as_integral(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-esint-") as d:
            line = "Es gilt $\\int x\\,dx = \\int t\\,dt$ hier und dort."
            _accent_case(d, "e", line, "Es gilt \\(\\int x\\,dx = \\int t\\,dt\\) hier und dort.", "\\usepackage{esint}\n")
            pdf = os.path.join(d, "e", "page.pdf")
            self.assertIn('glyph="acute"', judge.trace_text(pdf, 1, 1))     # the fixture holds the named integral
            self.assertIn(r"\usepackage{esint}", judge.font_preamble(pdf, 1)[1])
            out = io.StringIO()
            res = judge.judge(pdf, 1, os.path.join(d, "e", "r.lines.json"), os.path.join(d, "e", "g"), out=out,
                              route="glyph", setter=False)
            text = out.getvalue()
            ints = [ln for ln in text.splitlines() if ln.startswith("G L0 &#x222b; &#x222b; 1 ")]
            self.assertEqual(len(ints), 2, text[-1500:])
            self.assertEqual((res["unpaired_A"], res["unpaired_B"], res["glyph"]), (0, 0, 0), text[-1500:])
            self.assertNotIn("&#xb4;", text)


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeBiolinumLine(unittest.TestCase):
    """a Biolinum line on a Libertine page (acmart's title, board 1805.01788 p3) is set in Biolinum, not in the
    page's Libertine serif"""

    def test_biolinum_line_is_set_in_biolinum(self):
        self.assertEqual(judge.line_family(["ABCDEF+LinBiolinumT"] * 3, "libertine"), "LinuxBiolinumT-TLF")
        self.assertIsNone(judge.line_family(["ABCDEF+LinLibertineT"], "libertine"))
        with tempfile.TemporaryDirectory(prefix="test-judge-bio-") as d:
            line = "{\\biolinum Equity of Attention: Amortizing Fairness} $x$"
            _accent_case(d, "bio", line, "Equity of Attention: Amortizing Fairness \\(x\\)", "\\usepackage{libertine}\n")
            out = io.StringIO()
            res = judge.judge(os.path.join(d, "bio", "page.pdf"), 1, os.path.join(d, "bio", "r.lines.json"),
                              os.path.join(d, "bio", "g"), out=out, route="glyph", setter=False)
            fonts = subprocess.run(["pdffonts", os.path.join(d, "bio", "g", "lines.pdf")], capture_output=True,
                                   text=True).stdout
            self.assertIn("LinBiolinumT", fonts)
            self.assertEqual((res["glyph"], res["style"]), (0, 0), [ln for ln in out.getvalue().splitlines()
                                                                    if ln.startswith("G ") and ln.split()[8] == "1"])


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeNewtxLibertine(unittest.TestCase):
    """a page set with newtxmath[libertine] (maths letters in LinLibertineI, board 1805.01788 p3) is judged with that
    option: plain newtxmath sets the letters in NewTXMI and flags them"""

    def test_libertine_maths_letters(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-ntxl-") as d:
            line = "Let $x_i + y \\leq a b$ hold for every $n$ here."
            _accent_case(d, "m", line, "Let \\(x_i + y \\leq a b\\) hold for every \\(n\\) here.",
                         "\\usepackage{libertine}\\usepackage[libertine]{newtxmath}\n")
            pdf = os.path.join(d, "m", "page.pdf")
            fam, pre = judge.font_preamble(pdf, 1)
            self.assertEqual((fam, pre.count("newtxmath")), ("libertine", 1), pre)
            self.assertIn(judge.NEWTX_LIBERTINE, pre)
            out = io.StringIO()
            res = judge.judge(pdf, 1, os.path.join(d, "m", "r.lines.json"), os.path.join(d, "m", "g"), out=out,
                              route="glyph", setter=False)
            self.assertEqual((res["glyph"], res["style"]), (0, 0), [ln for ln in out.getvalue().splitlines()
                                                                    if ln.startswith("G ") and ln.split()[8] == "1"])

    def test_plain_newtx_page_keeps_plain_newtxmath(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-ntx-") as d:
            _accent_case(d, "t", "Let $x_i + y$ hold.", "Let \\(x_i + y\\) hold.",
                         "\\usepackage{newtxtext}\\usepackage{newtxmath}\n")
            pre = judge.font_preamble(os.path.join(d, "t", "page.pdf"), 1)[1]
            self.assertIn("\\usepackage{newtxmath}", pre)
            self.assertNotIn(judge.NEWTX_LIBERTINE, pre)


class FindingKind(unittest.TestCase):
    """a flagged pair: another character is a glyph finding whatever the faces (T-0158 gate, 1805.01788 p3: a reading's
    b for the page's a, LinLibertineI against LibertineMathMI, had read as style and left the residual 0)"""

    def test_kinds(self):
        self.assertEqual(judge.finding("a", "b", "i", "xi", 0), "glyph")
        self.assertEqual(judge.finding("a", "b", "i", "i", 0), "glyph")
        self.assertEqual(judge.finding("a", "b", "i", "xi", 1), "")         # a face TeX cannot set: unmeasurable
        self.assertEqual(judge.finding("a", "a", "b", "-", 0), "style")     # bold lost
        self.assertEqual(judge.finding("a", "a", "b", "-", 1), "style")
        self.assertEqual(judge.finding("a", "a", "-", "-", 0), "glyph")
        self.assertEqual(judge.finding("a", "a", "-", "-", 1), "")
        self.assertEqual(judge.finding("t", "T", "c", "-", 0), "style")     # small caps lost
        self.assertEqual(judge.finding("E", "e", "-", "c", 0), "style")
        self.assertEqual(judge.finding("t", "T", "-", "-", 0), "glyph")     # a case error in one face
        self.assertEqual(judge.finding("t", "T", "i", "-", 0), "glyph")     # no small caps: another letter
        self.assertEqual(judge.finding("(", "&#xfffd;", "-", "x", 0), "style")   # unknown: the faces decide
        self.assertEqual(judge.finding("(", "&#xfffd;", "-", "-", 0), "glyph")


class GlyphNamed(unittest.TestCase):
    """a U+FFFD character read from its glyph's name (T-0158): the base of a TeX extension font's name after its size
    suffix; pieces and unknown names stay U+FFFD"""

    def test_names(self):
        self.assertEqual([judge.glyph_named(n) for n in ("parenleftbig", "parenrightBigg", "summationdisplay.1",
                                                          "summationtext", "radicalBig", "bracketleftbigg", "slashbig",
                                                          "intersectiontext", "hatwide", "tildewider")],
                         ["(", ")", "&#x2211;", "&#x2211;", "&#x221a;", "[", "/", "&#x22c2;", "&#x2c6;", "&#x2dc;"])
        for n in ("barex", "parenlefttp", "bracketleftbt", "SI", "", None, "a"):
            self.assertEqual(judge.glyph_named(n), "", n)

    def test_identified(self):
        cs = [("&#xfffd;", 10.0, 20.0, 9, 10, 12, 22, "CMEX10", 10.0), ("x", 15.0, 20.0, 14, 15, 17, 21, "CMMI10", 10.0),
              ("&#xfffd;", 30.0, 20.0, 29, 10, 32, 22, "CMEX10", 10.0)]
        traced = [("&#xfffd;", "parenleftbig", 10.0, 20.0, 0, 0), ("x", "x", 15.0, 20.0, 0, 0),    # (char, glyph name,
                  ("&#xfffd;", "barex", 30.0, 20.0, 0, 0)]                                          # origin, ...)
        got = judge.identified(cs, traced)
        self.assertEqual([c[0] for c in got], ["(", "x", "&#xfffd;"])
        self.assertEqual(got[0][1:], cs[0][1:])


class RectsUnit(unittest.TestCase):
    """inkcmp-judge --rects: the TSV, the reading it makes, and the residual's definition"""

    def test_latex_column_is_not_unescaped(self):
        with tempfile.TemporaryDirectory(prefix="test-rects-") as d:
            p = os.path.join(d, "r.tsv")
            with open(p, "w") as fh:
                fh.write("source\tdoc\tpage\tobj_id\tx0\ty0\tx1\ty1\tlatex\tpdf\n")
                fh.write("s\td\t1\to1\t1\t2\t3\t4\tX \\to \\theta\tx.pdf\n")
            rows = rects.read_rows(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["latex"], "X \\to \\theta")      # \to stays \to (it was a tab and "o")
            with open(p, "a") as fh:
                fh.write("s\td\t1\to1\t1\t2\t3\n")
            with self.assertRaises(ValueError):
                rects.read_rows(p)

    def test_unreadable_page_is_an_error_row(self):
        """a row whose page cannot be read is status ERROR; it does not stop the batch"""
        with tempfile.TemporaryDirectory(prefix="test-rects-bad-") as d:
            p = os.path.join(d, "r.tsv")
            with open(p, "w") as fh:
                fh.write("s\td\t1\to1\t1\t2\t3\t4\tx\t" + os.path.join(d, "missing.pdf") + "\n")
            out = rects.judge_rects(p, os.path.join(d, "w"))
            self.assertEqual([(o["status"], o["residual"], o["crop"]) for o in out], [("ERROR", "", "")])
            self.assertTrue(open(out[0]["judge_out"]).read().startswith("ERROR "))

    def test_delimiters(self):
        for t in ("$x$", "\\[x\\]", "\\(x\\)", "\\begin{equation}x\\end{equation}", "\\begin{align*}x\\end{align*}"):
            self.assertTrue(rects.DELIMITED.match(t), t)
        for t in ("x = 1", "\\frac{a}{b}", "\\begin{aligned}x\\end{aligned}"):
            self.assertFalse(rects.DELIMITED.match(t), t)

    def test_residual(self):
        c = dict(glyphs=20, no_outline=1, unpaired_A=2, glyph=3, unpaired_B=4, rules_a=2, rules_missing=1,
                 rules_invented=1, unmeasurable=0, a_glyphs=5000)           # a_glyphs is the whole page's: unused
        self.assertEqual(rects.rect_glyphs(c), 23)
        self.assertEqual(rects.residual(c, "OK"), ("OK", "%.4f" % ((3 + 3 + 4 + 1 + 1) / 25)))
        # unmeasurable glyphs stay in the denominator (CONTRACT, 2026-11-22): 22 unpaired over 58, not over 58 - 45
        mt = dict(glyphs=36, no_outline=0, unpaired_A=22, glyph=0, unpaired_B=22, rules_a=0, unmeasurable=45)
        self.assertEqual(rects.residual(mt, "OK"), ("OK", "%.4f" % (44 / 58)))
        self.assertEqual(rects.residual(dict(c, not_compiled=1), "OK"), ("NOT_COMPILED", ""))
        self.assertEqual(rects.residual(dict(glyphs=0, rules_a=0), "NO_TEXT"), ("NO_TEXT", ""))
        self.assertEqual(rects.residual({}, ""), ("ERROR", ""))


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS + ("pdftoppm",)), "needs " + ", ".join(TOOLS))
class JudgeRects(unittest.TestCase):
    """one rectangle judged alone: its own LaTeX reads 0, one symbol changed reads more, and a rule outside the
    rectangle (a centred inline fraction's bar above it) is no finding of it"""

    def test_own_latex_and_one_symbol(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-rects-") as d:
            eq = "\\frac{a_{i1}}{r_{i1}} = \\sum_{j=1}^{n} x_j"
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[10pt]{article}\n\\usepackage{amsmath}\\pagestyle{empty}\n\\begin{document}\n"
                         "Text above.\n\n\\centerline{$\\frac{1}{2}$}\n\\[" + eq + "\\]\nMore text.\n\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True,
                               check=True).stdout
            bs = [tuple(float(v) for v in m) for m in WORD.findall(x)]
            # pdftotext's lines: "Text above.", the fraction's 1 and 2, the display in 7 pieces, "More text."
            self.assertEqual(len(bs), 11, bs)
            mid = bs[3:-1]
            self.assertGreater(min(v[1] for v in mid), bs[2][3], "the fraction lies above the display")
            b = (min(v[0] for v in mid) - 2, min(v[1] for v in mid) - 2, max(v[2] for v in mid) + 2,
                 max(v[3] for v in mid) + 2)
            tsv = os.path.join(d, "r.tsv")
            with open(tsv, "w") as fh:
                for src, t in (("own", eq), ("one", eq.replace("a_{i1}", "b_{i1}"))):
                    fh.write("\t".join([src, "fx", "1", src] + ["%.2f" % v for v in b] + [t, pdf]) + "\n")
            out = rects.judge_rects(tsv, os.path.join(d, "w"))
            own, one = out
            self.assertEqual((own["status"], own["residual"]), ("OK", "0.0000"), own)
            self.assertEqual(own["rules_missing"], 0, "the fraction bar above is not the rectangle's")
            self.assertGreaterEqual(own["rules_a"], 1, "the fraction bar is")
            self.assertGreater(float(one["residual"]), 0.02, one)            # b for a: a finding, paired or not
            self.assertGreater(one["glyph"] + one["unpaired_A"] + one["unpaired_B"], 0, one)
            self.assertTrue(os.path.getsize(own["crop"]) > 0)


class JudgeScriptPlace(unittest.TestCase):
    """T-0158 J1 (arxiv.1710.04811): a script set on the wrong side of its base is a finding, though its ink is the
    page's. Each pair is compared lying on its own origin, so before `placed` the reading's \\bigr)_{2} for the page's
    \\right)^2 scored residual 0, and \\sin_2 for \\sin^2 too."""

    g = staticmethod(lambda c, x, y, size=10.0: (c, x, y, x, y - 5, x + 4, y, "F", size))

    def test_placed_blames_the_moved_glyph_only(self):
        g = self.g
        a = [g("x", 0, 10), g("2", 5, 6, 7.0), g("+", 10, 10), g("y", 15, 10)]
        b = [g("x", 0, 20), g("2", 5, 22, 7.0), g("+", 10, 20), g("y", 15, 20)]      # the 2 set 6 pt too low
        pl = judge.placed(list(zip(a, b)))
        self.assertGreater(abs(pl[1]), judge.PLACE)
        self.assertEqual([round(v, 6) for v in (pl[0], pl[2], pl[3])], [0, 0, 0])
        # a sum whose limits moved is not itself misplaced: the line says where it belongs
        a = [g("a", 0, 10), g("=", 5, 10), g("S", 10, 10), g("m", 10, 4, 7.0), g("i", 11, 15, 7.0), g("b", 20, 10)]
        b = [g("a", 0, 10), g("=", 5, 10), g("S", 10, 10), g("m", 13, 8, 7.0), g("i", 13, 11, 7.0), g("b", 20, 10)]
        pl = judge.placed(list(zip(a, b)))
        self.assertLess(abs(pl[2]), judge.PLACE, pl)
        self.assertGreater(min(abs(pl[3]), abs(pl[4])), judge.PLACE, pl)
        self.assertEqual(judge.placed(list(zip(a[:2], b[:2]))), [0.0, 0.0])

    def test_twins_take_their_own_partners(self):
        g = self.g
        # a fraction's numerator and denominator l at one x, paired crosswise by the x sort (1805.01788 p3 L19)
        a = [g("a", 0, 10), g("l", 5, 4), g("l", 5, 16), g("b", 10, 10)]
        b = [g("a", 0, 30), g("l", 5, 36), g("l", 5, 24), g("b", 10, 30)]
        pairs, ua, ub = judge.twins(list(zip(a, b)), [], [])
        self.assertEqual([(p[0][2], p[1][2]) for p in pairs], [(10, 30), (4, 24), (16, 36), (10, 30)])
        self.assertEqual((ua, ub), ([], []))
        # a page glyph of the row above took the rendered u; its own page u was left unpaired (lib:4859edad9294
        # p1287 L25, "number")
        a = [g("n", 72, 687.4), g("u", 78.2, 673.0), g("m", 84.7, 687.4)]
        b = [g("n", 2, 12.8), g("u", 8.2, 12.8), g("m", 14.7, 12.8)]
        pairs, ua, ub = judge.twins(list(zip(a, b)), [g("u", 78.2, 687.4)], [])
        self.assertEqual([p[0][2] for p in pairs], [687.4, 687.4, 687.4])
        self.assertEqual([c[2] for c in ua], [673.0])
        self.assertEqual(judge.placed(pairs), [0.0, 0.0, 0.0])
        # a character never loses a pair to the column rule: \\cdots 4.4 pt apart, whose offset differs from the
        # line's by 4.3 pt, slid one dot along and left the last without a partner in its column (board
        # lib:5a5c76391c9c p4 MathPix: unpaired 2 more on each side, glyphs counted twice)
        a = [g(c, 300 + 20 * n, 519.26) for n, c in enumerate("rstuvwx")] + \
            [g("&#xb7;", 452.47, 519.26), g("&#xb7;", 456.9, 519.26), g("&#xb7;", 461.33, 519.26)]
        b = [g(c, 166.61 + 20 * n, 10.37) for n, c in enumerate("rstuvwx")] + \
            [g("&#xb7;", 314.81, 10.37), g("&#xb7;", 319.24, 10.37), g("&#xb7;", 323.66, 10.37)]
        pairs, ua, ub = judge.twins(list(zip(a, b)), [], [])
        self.assertEqual(pairs, list(zip(a, b)))
        self.assertEqual((ua, ub), ([], []))
        # along a misread line the offset drifts: an unpaired page twin where the drift points is in another column,
        # and does not take the rendered glyph (board 0864 p4 Gemma4 L45: v, a, e taken from their own words)
        a = [g(c, 20 * n, 10) for n, c in enumerate("rstuw")] + [g("v", 100, 10)]
        b = [g(c, 20 * n, 10) for n, c in enumerate("rstuw")] + [g("v", 125, 10)]
        pairs, ua, ub = judge.twins(list(zip(a, b)), [g("v", 124, 10)], [])
        self.assertEqual(pairs, list(zip(a, b)))
        # pairs that are already right are left alone
        a = [g("l", 5, 4), g("l", 5, 16), g("b", 10, 10)]
        b = [g("l", 5, 4), g("l", 5, 16), g("b", 10, 10)]
        self.assertEqual(judge.twins(list(zip(a, b)), [], [])[0], list(zip(a, b)))

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_script_on_the_wrong_side_is_a_finding(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-place-") as d:
            eq = "\\left(S_R^2\\right)^2\\left(S_L^2 S_R^2+\\sin^2\\theta_H\\right)^2=0"
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[12pt]{article}\n\\usepackage{amsmath}\\pagestyle{empty}\n\\begin{document}\n"
                         "Text.\n\\[" + eq + "\\]\nMore.\n\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True,
                               check=True).stdout
            bs = [tuple(float(v) for v in m) for m in WORD.findall(x)]
            bs.sort(key=lambda v: v[1])                          # Text., the display in pieces, More.
            mid = bs[1:-1]
            self.assertEqual(len(mid), 5, bs)
            b = (min(v[0] for v in mid) - 2, min(v[1] for v in mid) - 1, max(v[2] for v in mid) + 2,
                 max(v[3] for v in mid) + 1)
            readings = {"own": eq,
                        "sub": "\\bigl(S_{R}^{2} \\bigr)_{2} \\bigl(S_{L}^{2} S_{R}^{2} + \\sin^{2} \\theta_{H} \\bigr)_{2} = 0",
                        "sin": eq.replace("\\sin^2", "\\sin_2")}
            tsv = os.path.join(d, "r.tsv")
            with open(tsv, "w") as fh:
                for k, t in readings.items():
                    fh.write("\t".join([k, "fx", "1", k] + ["%.2f" % v for v in b] + [t, pdf]) + "\n")
            own, sub, sin = rects.judge_rects(tsv, os.path.join(d, "w"))
            for r in (own, sub, sin):
                self.assertEqual(r["status"], "OK", r)
            got = {}
            for r in (own, sub, sin):
                with open(r["judge_out"]) as fh:
                    got[r["obj_id"]] = [ln.split() for ln in fh if ln.startswith("G ")]
            moved = lambda k: [f[2] for f in got[k] if abs(float(f[16])) > judge.PLACE]
            self.assertEqual(moved("own"), [])
            self.assertEqual(moved("sub"), ["2", "2"])                  # the two outer exponents
            self.assertEqual(moved("sin"), ["2"])
            self.assertTrue(all(f[8] == "1" for k in ("sub", "sin") for f in got[k] if abs(float(f[16])) > judge.PLACE))
            self.assertGreater(float(sub["residual"]), float(own["residual"]) + 0.05, (own, sub))
            self.assertGreater(float(sin["residual"]), float(own["residual"]) + 0.02, (own, sin))


    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_twins_on_a_page(self):
        """the page's denominator l lies right of its numerator l, the reading's left of it: the x sort pairs them
        crosswise, and only `twins` gives each its own partner (else both read misplaced)"""
        with tempfile.TemporaryDirectory(prefix="test-judge-twins-") as d:
            _accent_case(d, "t", "We have $a = \\frac{l}{\\,l} + b$ here.", "We have \\(a = \\frac{\\,l}{l} + b\\) here.")
            out = io.StringIO()
            res = judge.judge(os.path.join(d, "t", "page.pdf"), 1, os.path.join(d, "t", "r.lines.json"),
                              os.path.join(d, "t", "g"), out=out, route="glyph", setter=False)
            ls = [ln.split() for ln in out.getvalue().splitlines() if ln.startswith("G ") and ln.split()[2] == "l"]
            self.assertEqual(len(ls), 2, out.getvalue())
            self.assertEqual([f[16] for f in ls], ["0.000", "0.000"], ls)
            self.assertEqual(res["misplaced"], 0)


FIXTURE_T3_DVIPS = os.path.join(os.path.dirname(__file__), "fixtures", "type3-dvips-bitmap.pdf")


class JudgeNamelessType3(unittest.TestCase):
    """T-0158 J2 and its remainder (2026-11-25): dvips + Ghostscript bitmap fonts are Type 3 with no name (mutool: "Type3 (21 0 R)"). Counted as an
    unmapped family they made a CM page FONT_UNMAPPED: arxiv.0910.1368 p12, 870 body-text glyphs in "Type3 (106 0 R)",
    its 6 T-0158 rectangles NO_TEXT. The fixture is that case made small: `latex` (12pt article) of two lines, "Some
    text set from bitmaps, \\textbf{bold words}, and $x+y$ in maths." and "A second line all in the bitmap text
    font.", `dvips -Ppk -u nocmr12.map` (pdftex.map without its cmr12 line, so cmr12 comes as a PK bitmap), `ps2pdf`:
    the text in a nameless Type 3, the bold and the maths named Type 1."""

    @unittest.skipUnless(os.path.exists(judge.INKCMP), "needs build/inkcmp (make)")
    def test_ink_side_is_registered(self):
        """a B side "=k" is reading page k's ink, at the shift given and within 2 px of it: both renderers floor a
        device origin known here only to a fraction of a pixel (0910.1368 p12: 17 of 80 pairs at the shift given)"""
        def pgm(path, dots):
            px = bytearray(b"\xff" * 40 * 40)
            for x, y in dots:
                px[y * 40 + x] = 0
            with open(path, "wb") as fh:
                fh.write(b"P5 40 40 255\n" + bytes(px))
        blob = [(x, y) for x in range(10, 16) for y in range(10, 20) if not (12 <= x <= 13 and 12 <= y <= 16)]
        with tempfile.TemporaryDirectory(prefix="test-judge-pageb-") as d:
            a, b = os.path.join(d, "a.pgm"), os.path.join(d, "b.pgm")
            pgm(a, blob)
            pgm(b, [(x + 1, y + 5) for x, y in blob])               # one px right of the shift given (0, 5)
            ink = lambda dx, dy: judge.glyph_job([], [(None, ("=0", "-", (0.0, 0.0, 0.0, 0.0), dx, dy),
                                                       (5, 5, 25, 25), [])], a, [(0, b)])[0][0]
            self.assertEqual(ink(0, 5)[:3], (0, 0, 0))
            self.assertEqual(ink(1, 5)[:3], (0, 0, 0))
            self.assertGreater(ink(4, 5)[0], 0, "3 px off: not registered")
            pgm(b, [(x + 1, y + 5) for x, y in blob if x != 10])    # one column less: a difference at any shift
            self.assertGreater(ink(0, 5)[0], 0)

    def test_names(self):
        self.assertTrue(judge.TYPE3_NAMELESS.fullmatch("Type3 (106 0 R)"))
        for n in ("F32", "DejaVuSerif", "Type3", "CMR10"):
            self.assertFalse(judge.TYPE3_NAMELESS.fullmatch(n), n)

    def test_nameless_type3_sizes_do_not_vote(self):
        g = lambda f, z: ("a", 0, 0, 0, 0, 1, 1, f, z)
        # arxiv.0910.1368 p12's sizes: body text in "Type3 (106 0 R)" at 0.12, digits in CMR10 and maths at 10.9091
        ac = [g("Type3 (106 0 R)", 0.12)] * 5 + [g("CMR10", 10.9091)] * 2 + [g("CMMI10", 10.9091), g("CMEX10", 10.9091)]
        voters, page = judge.size_voters(ac)
        self.assertEqual(voters, [False] * 5 + [True, True, True, False])
        self.assertEqual(page, 10.9091)
        self.assertEqual(judge.size_voters([g("Type3 (1 0 R)", 0.12)]), ([False], 10.0))

    def test_a_line_with_no_size_takes_the_page_size(self):
        self.assertEqual(judge.vote_size([], False, 10.9091), 10.9091)
        self.assertEqual(judge.vote_size([], True), 10.0)
        self.assertEqual(judge.vote_size([9.0, 9.0], False, 10.9091), 9.0)

    def judged(self, pdf: str, readings: dict) -> dict:
        """each reading (two lines) judged against the page; the line boxes from the glyphs' own quads, by baseline:
        pdftotext's lines are taller for Type 3 text (its FontBBox), and the second line's box reached over the first
        line's p"""
        rows = collections.defaultdict(list)
        for c in judge.chars(pdf, 1, 1)[0]:
            rows[round(c[2])].append(c)
        bs = [(min(c[3] for c in r), min(c[4] for c in r), max(c[5] for c in r), max(c[6] for c in r))
              for _, r in sorted(rows.items())]
        self.assertEqual(len(bs), 2, bs)
        w, h = judge.page_size(pdf, 1)
        k = 200 / 72
        got = {}
        with tempfile.TemporaryDirectory(prefix="test-judge-t3d-") as d:
            for name, texts in readings.items():
                doc = {"pages": [{"page": 1, "page_width": round(w * k), "page_height": round(h * k), "lines": [
                    {"type": "text", "text": t, "region": {
                        "top_left_x": int(b[0] * k) - 2, "top_left_y": int(b[1] * k) - 2,
                        "width": int((b[2] - b[0]) * k) + 4, "height": int((b[3] - b[1]) * k) + 4}}
                    for t, b in zip(texts, bs)]}]}
                lj = os.path.join(d, name + ".lines.json")
                with open(lj, "w") as fh:
                    json.dump(doc, fh)
                out = io.StringIO()
                res = judge.judge(pdf, 1, lj, os.path.join(d, name), out=out, route="glyph", setter=False)
                got[name] = (res, out.getvalue().splitlines())
                # METAFONT's bitmaps go to the job's folder, never to the user's texmf-var (house rule: no file
                # outside the project)
                pks = [f for _, _, fs in os.walk(os.path.join(d, name, "pk")) for f in fs if f.endswith("pk")]
                self.assertTrue(pks, "no PK bitmap in the job's folder")
        return got

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_t1_bitmaps_are_set_in_their_own_designs(self):
        """an 11pt T1 page (`latex`, `dvips -Ppk -u noec.map`: pdftex.map without its ec*/tc* lines, `ps2pdf`): its
        body text is ecrm1095.600pk. fix-cm would set ecrm1000 scaled to 10.95 pt, whose bitmaps differ"""
        pdf = os.path.join(os.path.dirname(__file__), "fixtures", "type3-dvips-ec.pdf")
        self.assertEqual(judge.font_preamble(pdf, 1), ("cm", judge.PK_PRE + "\n" + judge.PK_T1))
        self.assertFalse(judge.t1_bitmaps(FIXTURE_T3_DVIPS, ["Type3 (21 0 R)"]), "the OT1 fixture's cmr12")
        first = "A first line of text, {\\fontencoding{OT1}\\selectfont 1234} and \\(x+y\\) in maths."
        second = "The second line is all in the bitmap text font."
        res, lines = self.judged(pdf, {"own": (first, second)})["own"]
        self.assertEqual(lines[-1], "STATUS OK")
        self.assertEqual((res["unpaired_A"], res["unpaired_B"]), (0, 0))
        t3 = [ln.split() for ln in lines if ln.startswith("G ") and "Type3" in ln]
        self.assertGreater(len(t3), 60)
        self.assertEqual([f for f in t3 if f[8] == "1"], [])

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_the_page_is_judged(self):
        self.assertEqual(judge.font_preamble(FIXTURE_T3_DVIPS, 1), ("cm", judge.PK_PRE))   # set in the same bitmaps
        first = "Some text set from bitmaps, \\textbf{bold words}, and \\(x+y\\) in maths."
        second = "A second line all in the bitmap text font."
        got = self.judged(FIXTURE_T3_DVIPS, {"own": (first, second), "one": (first.replace("maths.", "mathe."), second)})
        res, lines = got["own"]
        self.assertEqual(lines[-1], "STATUS OK")
        self.assertEqual((res["unpaired_A"], res["unpaired_B"]), (0, 0))
        g = [ln.split() for ln in lines if ln.startswith("G ")]
        t3 = [f for f in g if "Type3" in " ".join(f)]
        self.assertEqual(len(t3), sum(1 for f in g if f[1] == "L1") + 36, "the bitmap text of both lines is paired")
        # the second line has no named glyph: it is set at the page's 11.955 pt, not at 10
        l1 = [f for f in g if f[1] == "L1"]
        self.assertEqual(len(l1), 34)
        # the reading's text is set in the page's own PK bitmaps (PK_PRE) and compared ink against ink: no clean
        # bitmap glyph flags (against cm-super's outlines 14 of the 70 did, solid up to 41)
        self.assertEqual([f for f in t3 if f[8] == "1"], [])
        self.assertEqual(res["glyph"], 0)
        res1, lines1 = got["one"]
        (plant,) = [ln.split() for ln in lines1 if ln.startswith("G L0 s e ")]
        self.assertEqual(plant[8], "1", plant)
        self.assertEqual(res1["glyph"], 1)


class JudgeMathScript(unittest.TestCase):
    """\\mathscr (mathrsfs) is in the judge's setup: a correct RSFS reading compiled to NOT_COMPILED ("Undefined
    control sequence") before (glyphdrill3-e4, 2026-11-26); board lib:b21e554b9232's PDF sets rsfs10"""

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_mathscr_line_compiles_and_pairs(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-rsfs-") as d:
            res, lines = _accent_case(d, "s", "The space $\\mathscr{L}(X)$ here.", "The space \\(\\mathscr{L}(X)\\) here.",
                                      "\\usepackage{mathrsfs}\n", route="glyph")
            self.assertEqual(res["not_compiled"], 0, lines)
            self.assertEqual([ln for ln in lines if ln.startswith("N ")], [])
            (g,) = [ln.split() for ln in lines if ln.startswith("G L0 L L ")]
            self.assertEqual(g[8], "0", g)                     # RSFS L against RSFS L: equal ink
            self.assertIn("RSFS", g[11].upper())


class JudgePrintedRows(unittest.TestCase):
    """A line of several printed rows pairs row by row (1511.08771 p2709 Table 560: an aligned cell of 4 rows, the
    print's first row packed tight, our setting natural; sorted by x the rows interleaved otherwise on the two sides
    and 77 / 77 glyphs went unpaired, the page's residual 0.6490)"""

    g = staticmethod(lambda c, x, y0, y1: (c, x, y1, x, y0, x + 5, y1, "F", 10.0))

    def test_bands_and_the_guard(self):
        g = self.g
        a = [g("a", 0, 0, 10), g("b", 6, 0, 10), g("c", 0, 20, 30), g("d", 9, 20, 30), g("2", 6, -3, 4)]
        self.assertEqual([[c[0] for c in r] for r in judge.bands(a)], [["a", "2", "b"], ["c", "d"]])
        b = [g("a", 0, 0, 10), g("b", 4, 0, 10), g("c", 0, 20, 30), g("d", 3, 20, 30), g("2", 4, -3, 4)]
        ra, rb = judge.printed_rows(sorted(a, key=lambda c: c[1]), sorted(b, key=lambda c: c[1]))
        self.assertEqual([c[0] for c in ra], ["a", "2", "b", "c", "d"])
        self.assertEqual([c[0] for c in rb], ["a", "2", "b", "c", "d"])
        self.assertIsNone(judge.printed_rows(a[:2], b[:2]), "one row: the x order")
        self.assertIsNone(judge.printed_rows(a, b[:2] + [g("c", 0, 5, 30)]), "bands differ: the x order")
        big = [g("x", k, 0, 10) for k in range(10)] + [g("y", 0, 20, 30)]
        self.assertIsNone(judge.printed_rows(big, big[:3] + [g("y", 0, 20, 30)]), "10 against 3 glyphs: not the same row")

    def test_row_order_only_where_it_pairs_more(self):
        """board lib:1a36bfe66b9c p154 Gemma4 L3: the page's prime touches the main band, the reading's sits above
        it; in row order the page's phi (U+03D5) and the reading's (U+03C6), equal ink, met a 2-against-1 replace and
        went unpaired. The x order paired them, so it is kept"""
        g = self.g
        a = [g("v", 0, 0, 8), g("S", 0, 10, 30), g("'", 4, 9, 14), g("&#x3d5;", 8, 12, 30), g("b", 12, 12, 30),
             g("d", 0, 32, 40)]
        b = [g("v", 0, 0, 8), g("S", 0, 10, 30), g("'", 4, 2, 6), g("&#x3c6;", 8, 12, 30), g("b", 12, 12, 30),
             g("d", 0, 32, 40)]
        xa, xb = sorted(a, key=lambda c: c[1]), sorted(b, key=lambda c: c[1])
        rows = judge.printed_rows(xa, xb)
        self.assertIsNotNone(rows, "the bands match in number and size")
        self.assertLess(len(judge.sequence_pairs(*rows)[0]), len(judge.sequence_pairs(xa, xb)[0]))
        ca, cb, pairs, ua, ub, by_row = judge.choose_order(xa, xb)
        self.assertEqual(by_row, 0)
        self.assertIn(("&#x3d5;", "&#x3c6;"), [(p[0][0], p[1][0]) for p in pairs])
        # where rows pair more, they are taken
        ca, cb, pairs, ua, ub, by_row = judge.choose_order(*[sorted(v, key=lambda c: c[1]) for v in (
            [g("a", 0, 0, 10), g("b", 6, 0, 10), g("c", 0, 20, 30), g("d", 9, 20, 30)],
            [g("a", 0, 0, 10), g("b", 4, 0, 10), g("c", 0, 20, 30), g("d", 3, 20, 30)])])
        self.assertEqual((by_row, len(pairs)), (1, 4))

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_tight_row_pairs(self):
        page = ("$\\begin{aligned} x &= a\\!+\\!b\\!+\\!c\\!+\\!d\\!+\\!e\\!+\\!f\\!+\\!g\\!+\\!h \\\\ "
                "y &= p+q+r+s+t+u+v+w+z \\end{aligned}$")
        read = ("\\(\\begin{aligned} x &= a+b+c+d+e+f+g+h \\\\ y &= p+q+r+s+t+u+v+w+z \\end{aligned}\\)")
        with tempfile.TemporaryDirectory(prefix="test-judge-rows-") as d:
            res, lines = _accent_case(d, "r", page, read, "\\usepackage{amsmath}\n", route="glyph", typ="math")
            self.assertEqual(res["lines_by_row"], 1, lines)
            self.assertEqual((res["unpaired_A"], res["unpaired_B"]), (0, 0), [ln for ln in lines if ln.startswith("U ")])


class JudgeOT1Quotes(unittest.TestCase):
    """A CM page prints its quotes from OT1 cmr10 (quotedblleft 0.5 em wide); the judge's T1 set them from cm-super's
    SFRM, another design 0.333 em wide, so a reading that wrote the right "\u201c" flagged (board 2103.01507 p118:
    ours' \u201c and \u201d at solid 58 and 9, its last 2 glyph findings)"""

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_curly_quotes_pair_clean(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-quotes-") as d:
            _accent_case(d, "q", "as the ``constant'' and `single' sections here.",
                         "as the \u201cconstant\u201d and \u2018single\u2019 sections here.", route="glyph")
            out = io.StringIO()                          # pdflatex's setting, as on the board page (not the setter)
            res = judge.judge(os.path.join(d, "q", "page.pdf"), 1, os.path.join(d, "q", "r.lines.json"),
                              os.path.join(d, "q", "p"), out=out, route="glyph", setter=False)
            lines = out.getvalue().splitlines()
            self.assertIn("set by pdflatex", lines[0])
            q = [ln.split() for ln in lines if ln.startswith("G ") and ln.split()[2] in
                 ("&#x201c;", "&#x201d;", "&#x2018;", "&#x2019;")]
            self.assertEqual(len(q), 4, lines)
            self.assertEqual([f[8] for f in q], ["0"] * 4, q)


class JudgeFindingKind(unittest.TestCase):
    """J4: each G line's last field is what JUDGE1 counted for the pair, so a reader of the G lines (board_run's
    finding_boxes) counts as the residual does: a flagged pair in a face TeX cannot set (MathTime) is no glyph finding,
    a pair in the wrong place is one in any face"""

    def test_count_returns_the_kind(self):
        res = collections.Counter()
        self.assertEqual(judge.count(res, "a", "b", "-", "-", "CMR10", True, 0.0), "g")
        self.assertEqual(judge.count(res, "a", "a", "-", "b", "CMR10", True, 0.0), "s")
        self.assertEqual(judge.count(res, "[", "[", "-", "-", "YtllkcRjyfrxMTEX", True, 0.0), "-")   # c57a p293
        self.assertEqual(judge.count(res, "2", "2", "-", "-", "YtllkcRjyfrxMTEX", False, 1.4), "g")  # misplaced
        self.assertEqual(judge.count(res, "a", "a", "-", "-", "CMR10", False, 0.0), "-")
        self.assertEqual((res["glyph"], res["style"], res["unmeasurable"], res["glyphs"]), (2, 1, 2, 5))

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_kinds_add_up_to_judge1(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-kind-") as d:
            res, lines = _accent_case(d, "k", "The value $x_2$ and some \\textit{italic} text.",
                                      "The valve \\(x^2\\) and some italic text.", route="glyph")
            g = [ln.split() for ln in lines if ln.startswith("G ")]
            kinds = collections.Counter(f[17] for f in g)
            self.assertEqual((kinds["g"], kinds["s"]), (res["glyph"], res["style"]), g)
            self.assertGreater(kinds["g"], 0)
            self.assertGreater(kinds["s"], 0)


class JudgeFontExpansion(unittest.TestCase):
    """J6: a page set with microtype's font expansion draws a justified line's glyphs up to 2-3 % wider or narrower
    (the glyph matrix's x scale against its y scale), and a page in Latin Modern draws LM's designs; the judge set the
    reading unexpanded, in T1 cm-super. Board lib:c9daf79960c3 p68: 11 of ours' 14 glyph findings were LMRoman12 a's,
    i and b at the right place; with lmodern and the page's expansion, 1 is left"""

    def test_expanded(self):
        e = judge.expanded
        self.assertEqual(e((50.82, 0, 0, -49.81), (49.81, 0, 0, -49.81))[0], 50.82)            # +2 %: taken
        self.assertEqual(e((49.81, 0, 0, -49.81), (60, 0, 0, -60)), (60, 0, 0, -60))           # none, any size
        self.assertAlmostEqual(e((50.82, 0, 0, -49.81), (60, 0, 0, -60))[0], 60 * 50.82 / 49.81)   # the ratio, not the size
        self.assertEqual(e((52, 0, 0, -49.81), (49.81, 0, 0, -49.81)), (49.81, 0, 0, -49.81))  # 4.4 %: not expansion
        self.assertEqual(e((49.81, 3, 0, -49.81), (49.81, 0, 0, -49.81)), (49.81, 0, 0, -49.81))  # slanted: not
        self.assertEqual(e(None, (1, 0, 0, 1)), (1, 0, 0, 1))

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_expanded_lm_line_pairs(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-expansion-") as d:
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[12pt]{article}\\usepackage[T1]{fontenc}\\usepackage{lmodern}"
                         "\\usepackage[expansion=true,stretch=30,shrink=30,step=5]{microtype}\\pagestyle{empty}\n"
                         "\\begin{document}\n\\noindent WWW MMM mmm WWM wmw MWM www mmw WMW MMW www mmm WWW MWM mmw wmw "
                         "MMM WWW mmm WMW mmw www.\n\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            self.assertEqual(judge.font_preamble(pdf, 1), ("cm", "\\usepackage{lmodern}"))
            x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
            L = re.findall(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', x, re.S)
            x0, y0, x1, y1, body = L[0]                  # the first line: justified, expanded 3 %
            w, h = judge.page_size(pdf, 1)
            k = 200 / 72
            doc = {"pages": [{"page": 1, "page_width": round(w * k), "page_height": round(h * k), "lines": [
                {"type": "text", "text": " ".join(re.findall(r">([^<]+)</word>", body)), "region": {
                    "top_left_x": int(float(x0) * k) - 2, "top_left_y": int(float(y0) * k) - 2,
                    "width": int((float(x1) - float(x0)) * k) + 4, "height": int((float(y1) - float(y0)) * k) + 4}}]}]}
            lj = os.path.join(d, "r.lines.json")
            with open(lj, "w") as fh:
                json.dump(doc, fh)
            res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=io.StringIO(), route="glyph", setter=False)
            self.assertGreater(res["glyphs"], 30)
            self.assertEqual(res["glyph"], 0)                    # 23 unexpanded


class JudgeFrames(unittest.TestCase):
    """J5: a closed frame (\\boxed, \\fbox) is one connected rectangle that rule_like drops whole; it is taken as its
    top and bottom edges on both sides (judge.frame_edges, inkcmp rules). c57a p293's (13.9), a faithful \\boxed
    reading: 2 rules missing -> 0"""

    def test_frame_edges(self):
        frame = [(0, 0, 99)] + [(y, 0, 1) for y in range(1, 39)] + [(y, 98, 99) for y in range(1, 39)] + [(39, 0, 99)]
        self.assertEqual(judge.frame_edges((0, 0, 99, 39), frame), [(0, 0), (39, 39)])
        sigma = [(0, 0, 99)] + [(y, y, y + 3) for y in range(1, 39)] + [(39, 0, 99)]      # top and bottom bars, no sides
        self.assertEqual(judge.frame_edges((0, 0, 99, 39), sigma), [])
        self.assertEqual(judge.frame_edges((0, 0, 99, 9), frame[:1] + [(y, 0, 1) for y in range(1, 9)] + [(9, 0, 99)]), [])
        small = [(0, 0, 15)] + [(y, 0, 1) for y in range(1, 15)] + [(y, 14, 15) for y in range(1, 15)] + [(15, 0, 15)]
        self.assertEqual(judge.frame_edges((0, 0, 15, 15), small), [], "a glyph's remnant (5a5c p4: 16 px), not a frame")

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_boxed_formula_pairs_its_frame(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-boxed-") as d:
            res, lines = _accent_case(d, "b", "The law $\\boxed{a+b=c}$ holds.", "The law \\(\\boxed{a+b=c}\\) holds.",
                                      "\\usepackage{amsmath}\n", route="glyph", typ="math")
            self.assertGreaterEqual(res["rules_a"], 2, lines)                # the frame's top and bottom
            self.assertEqual((res["rules_missing"], res["rules_invented"]), (0, 0), [l for l in lines if l.startswith("R ")])


class JudgeLigatureBreaks(unittest.TestCase):
    """J7: a page that draws an f pair as two glyphs (German Auf|lage: no ligature across a word joint) is judged
    against a setting that breaks the ligature there too; lib:3c16fac66fdd p370's four Auflage, 7 glyph findings"""

    def test_unligature(self):
        u = judge.unligature
        self.assertEqual(u("Auflage, 3. Auflage", [False, False]), "Auf{}lage, 3. Auf{}lage")
        self.assertEqual(u("Auflage flach", [False, True]), "Auf{}lage flach")        # in order, page by page
        self.assertEqual(u("Auflage flach", [True]), "Auflage flach")                  # counts differ: as it is
        self.assertEqual(u("Auflage", [True]), "Auflage")                              # the page's ligature
        self.assertEqual(u("Auflage", []), "Auflage")
        self.assertEqual(u(r"\flat $f l$ \ref{fl} \begin{flalign} offen", [False]),
                         r"\flat $f l$ \ref{fl} \begin{flalign} of{}fen")              # not maths, a name, a label

    def test_f_pairs(self):
        f = lambda ch, x0, x1: (ch, x0, 0, x0, 0, x1, 1, "F", 10)
        self.assertEqual(judge.f_pairs([f("f", 0, 3), f("l", 3, 3), f("a", 3, 6)]), [True])    # fl: l zero-width
        self.assertEqual(judge.f_pairs([f("f", 0, 3), f("l", 3.1, 5), f("a", 5, 8)]), [False])
        self.assertEqual(judge.f_pairs([f("a", 0, 3), f("x", 3.1, 5)]), [])

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_broken_ligature_pairs(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-lig-") as d:
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[12pt]{article}\\usepackage[T1]{fontenc}\\pagestyle{empty}\n\\begin{document}\n"
                         "\\noindent Die Auf{}lage und die Auf{}lage, schief und flach.\n\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
            bs = [tuple(float(v) for v in m) for m in WORD.findall(x)]
            b = (min(v[0] for v in bs), min(v[1] for v in bs), max(v[2] for v in bs), max(v[3] for v in bs))
            w, h = judge.page_size(pdf, 1)
            k = 200 / 72
            doc = {"pages": [{"page": 1, "page_width": round(w * k), "page_height": round(h * k), "lines": [
                {"type": "text", "text": "Die Auflage und die Auflage, schief und flach.", "region": {
                    "top_left_x": int(b[0] * k) - 2, "top_left_y": int(b[1] * k) - 2,
                    "width": int((b[2] - b[0]) * k) + 4, "height": int((b[3] - b[1]) * k) + 4}}]}]}
            lj = os.path.join(d, "r.lines.json")
            with open(lj, "w") as fh:
                json.dump(doc, fh)
            out = io.StringIO()
            res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out, route="glyph", setter=False)
            self.assertGreater(res["glyphs"], 35)
            self.assertEqual(res["glyph"], 0, [l for l in out.getvalue().splitlines() if l.startswith("G ")
                                               and l.split()[-1] == "g"])


class JudgePageRules(unittest.TestCase):
    """J9: the page's rules are found once from the page, not in the reading's formula lines and boxes; a reading that
    types a formula as text is charged for its rules (2103.01507 p118: MathPix rules_a 0, ours 26)"""

    @staticmethod
    def bar(x0, x1, y):
        return [(x, yy) for x in range(x0, x1) for yy in (y, y + 1)]

    def test_page_rules_maths_test(self):
        g = lambda ch, x0, y0, x1, y1, f: (ch, x0 / judge.S, y1 / judge.S, x0 / judge.S, y0 / judge.S, x1 / judge.S,
                                           y1 / judge.S, f, 10)
        bar = self.bar(100, 160, 200)
        frac_maths = [g("a", 110, 170, 130, 195, "CMMI10")]
        self.assertEqual(len(judge.page_rules([(bar, 0)], frac_maths)), 1)                  # a maths glyph over it
        frac_text = [g("1", 102, 170, 158, 195, "CMR10"), g("2", 102, 210, 158, 235, "CMR10")]   # 1805 p3: 757-817
                                                                                    # over a 759-819 bar
        self.assertEqual(len(judge.page_rules([(bar, 0)], frac_text)), 1)                   # text above and below
        self.assertEqual(judge.page_rules([(bar, 0)], [g("1", 102, 160, 158, 185, "CMR10")]), [])   # above only,
                                                                                    # 15 px off: no fraction, no underline
        head = [g("5", 150, 170, 160, 195, "CMR10"), g("x", 100, 210, 110, 235, "CMR10")]   # 1/6 of the bar each
        self.assertEqual(judge.page_rules([(bar, 0)], head), [])                            # a running head's rule
        q = [g("Q", 105, 210, 156, 257, "Times-Italic")]                         # 3fe7 p328: 8 px under, 85 %
        self.assertEqual(len(judge.page_rules([(bar, 0)], q)), 1)                           # an overline on text
        self.assertEqual(judge.page_rules([(bar, 0)], [g("Q", 105, 240, 156, 287, "Times-Italic")]), [])  # 38 px: no
        self.assertEqual(judge.page_rules([(bar, 0)], [g("a", 110, 100, 130, 125, "CMMI10")]), [])   # too far
        self.assertEqual(judge.page_rules([(bar, len(bar))], frac_maths), [])               # glyph ink: not a rule

    def test_table_rules(self):
        w, h = 800, 200
        grey = bytearray(b"\xff" * (w * h))
        for y in range(20, 101):                                       # a frame's sides between y 20 and 100
            for x in (10, 11, 410, 411):
                grey[y * w + x] = 0
        page = (w, h, bytes(grey))
        frame = [self.bar(10, 412, 20), self.bar(10, 412, 100)]
        self.assertEqual(judge.table_rules(frame, page), set())                     # a \boxed frame's two edges
        table2 = [self.bar(450, 790, 20), self.bar(450, 790, 100)]
        self.assertEqual(judge.table_rules(table2, page), {0, 1})                  # two edges, no sides: a table
        table3 = [self.bar(10, 412, 20), self.bar(10, 412, 60), self.bar(10, 412, 100)]
        self.assertEqual(judge.table_rules(table3, page), {0, 1, 2})               # three: a table, sides or not
        self.assertEqual(judge.table_rules([self.bar(450, 675, 20), self.bar(450, 675, 100)], page), set())  # two
        # equal 225 px overlines (1511.08771 p2709's 138600 and 138600'): no table

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_rules_a_is_the_pages(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-pagerules-") as d:
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[12pt]{article}\\pagestyle{empty}\n\\begin{document}\n"
                         "\\noindent The ratio $\\frac{a}{b}$ and $\\overline{xy}$ hold.\n\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
            bs = [tuple(float(v) for v in m) for m in WORD.findall(x)]
            b = (min(v[0] for v in bs), min(v[1] for v in bs), max(v[2] for v in bs), max(v[3] for v in bs))
            w, h = judge.page_size(pdf, 1)
            k = 200 / 72
            got = {}
            for name, text in (("maths", "The ratio $\\frac{a}{b}$ and $\\overline{xy}$ hold."),
                               ("text", "The ratio a/b and xy hold.")):
                doc = {"pages": [{"page": 1, "page_width": round(w * k), "page_height": round(h * k), "lines": [
                    {"type": "text", "text": text, "region": {
                        "top_left_x": int(b[0] * k) - 2, "top_left_y": int(b[1] * k) - 2,
                        "width": int((b[2] - b[0]) * k) + 4, "height": int((b[3] - b[1]) * k) + 4}}]}]}
                lj = os.path.join(d, name + ".lines.json")
                with open(lj, "w") as fh:
                    json.dump(doc, fh)
                got[name] = judge.judge(pdf, 1, lj, os.path.join(d, "w-" + name), out=io.StringIO(), route="glyph",
                                        setter=False)
            self.assertEqual(got["maths"]["rules_a"], 2)                       # the bar and the overline
            self.assertEqual(got["text"]["rules_a"], got["maths"]["rules_a"])   # the page's, whatever the reading
            self.assertEqual(got["maths"]["rules_missing"], 0)
            self.assertEqual(got["text"]["rules_missing"], 2)                  # before J9: rules_a 0, nothing missing


class JudgeRenderedRulesSameTest(unittest.TestCase):
    """J10: a rendered rule is kept by the page side's own rule (page_rules, the reading's glyphs); before, only a bar
    over a maths-font glyph was, so a faithful reading's bars over text digits could never pair (1511.08771 p2709's 8
    overlines over CMBX numbers: 8 missing, 0 invented)"""

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_bars_over_text_digits_pair(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-j10-") as d:
            line = "Half $\\tfrac{1}{2}$ and $\\overline{\\mathbf{138600}}$ here."
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[12pt]{article}\\usepackage{amsmath}\\pagestyle{empty}\n\\begin{document}\n"
                         "\\noindent " + line + "\n\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
            bs = [tuple(float(v) for v in m) for m in WORD.findall(x)]
            b = (min(v[0] for v in bs), min(v[1] for v in bs), max(v[2] for v in bs), max(v[3] for v in bs))
            w, h = judge.page_size(pdf, 1)
            k = 200 / 72
            doc = {"pages": [{"page": 1, "page_width": round(w * k), "page_height": round(h * k), "lines": [
                {"type": "text", "text": line, "region": {
                    "top_left_x": int(b[0] * k) - 2, "top_left_y": int(b[1] * k) - 2,
                    "width": int((b[2] - b[0]) * k) + 4, "height": int((b[3] - b[1]) * k) + 4}}]}]}
            lj = os.path.join(d, "r.lines.json")
            with open(lj, "w") as fh:
                json.dump(doc, fh)
            out = io.StringIO()
            res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out, route="glyph", setter=False)
            rl = [l for l in out.getvalue().splitlines() if l.startswith("R ")]
            self.assertEqual(res["rules_a"], 2, rl)                         # the \tfrac bar and the overline
            self.assertEqual((res["rules_missing"], res["rules_invented"]), (0, 0), rl)   # before J10: 2 missing

    def test_own_quad_pad(self):
        """0864 p4 L168 (board102): a rotated label set upright; the small-caps R's top stroke (rows 26-27) pokes 1.25 px
        out of its own quad (top 27.25) and was an invented rule in two columns. With OWN_PAD it is the glyph's ink"""
        stroke = [(x, y) for x in range(125, 137) for y in (26, 27)]
        r_quad = (124.0, 27.25, 144.0, 48.0)
        self.assertTrue(judge.inside_quads(stroke, [r_quad], judge.OWN_PAD))
        self.assertFalse(judge.inside_quads(stroke, [r_quad], 1))                   # the 1 px pad: outside
        bar = [(x, y) for x in range(125, 137) for y in (20, 21)]                   # 6 px above it: an overline
        self.assertFalse(judge.inside_quads(bar, [r_quad], judge.OWN_PAD))

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_misplaced_hyphen_is_no_rule(self):
        """c9da p68 MathPix L23: the reading sets "-valued functor." inside a superscript; its hyphen, away from the
        page's, is the hyphen glyph's own ink, not an invented rule"""
        with tempfile.TemporaryDirectory(prefix="test-judge-j10h-") as d:
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[12pt]{article}\\usepackage{amsmath}\\pagestyle{empty}\n\\begin{document}\n"
                         "\\noindent the $\\operatorname{Mod}_{\\mathbf{Z}}^{\\mathrm{cn}}$-valued functor.\n"
                         "\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
            bs = [tuple(float(v) for v in m) for m in WORD.findall(x)]
            b = (min(v[0] for v in bs), min(v[1] for v in bs), max(v[2] for v in bs), max(v[3] for v in bs))
            w, h = judge.page_size(pdf, 1)
            k = 200 / 72
            doc = {"pages": [{"page": 1, "page_width": round(w * k), "page_height": round(h * k), "lines": [
                {"type": "text", "text": "the \\(\\operatorname{Mod}_{\\mathbf{Z}}^{\\text {cn-valued functor. }}\\)",
                 "region": {"top_left_x": int(b[0] * k) - 2, "top_left_y": int(b[1] * k) - 2,
                            "width": int((b[2] - b[0]) * k) + 4, "height": int((b[3] - b[1]) * k) + 4}}]}]}
            lj = os.path.join(d, "r.lines.json")
            with open(lj, "w") as fh:
                json.dump(doc, fh)
            out = io.StringIO()
            res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out, route="glyph", setter=False)
            self.assertGreater(res["glyphs"], 10)
            self.assertEqual(res["rules_invented"], 0, [l for l in out.getvalue().splitlines() if l.startswith("R ")])


class JudgeJ11(unittest.TestCase):
    """J11: (a) a ligature the page JOINS at the very start of a line is the line's, though a reading's box drawn tight
    to the ink starts a fraction right of the glyph's origin (lib:69bc5c71b881 p16 L34: f_i at 340.0, the box at
    340.8; the setter set f and i apart and both were flagged); (b) a page glyph and a rendered one of one character in
    the same place pair however the order-based passes left them (lib:3fe71b4ef018 p328 L20's 2)"""

    def test_coincident(self):
        g = lambda ch, x0, y0, x1, y1: (ch, x0, y1, x0, y0, x1, y1, "F", 10)
        a2, b2 = g("2", 100, 50, 105, 57), g("2", 100, 50.2, 105, 57.2)
        a3, b3 = g("3", 200, 50, 205, 57), g("2", 200, 50, 205, 57)            # another character in that place
        b_far = g("2", 120, 50, 125, 57)
        pairs, ua, ub = judge.coincident([], [a2, a3], [b3, b2, b_far], [a2, a3], [b3, b2, b_far])
        self.assertIn((a2, b2), pairs)                                          # same character, same place: a pair
        self.assertEqual(ua, [a3])
        self.assertEqual(sorted(ub), sorted([b3, b_far]))
        self.assertEqual(judge.coincident([], [], [b2], [], [b2]), ([], [], [b2]))

    def test_coincident_is_a_best_matching(self):
        """audit A17: greedy took A1-B1 and left A2; the best matching is A1-B2 and A2-B1. Quads from intervals that
        give the auditor's IoUs: A1-B1 .667, A1-B2 .538, A2-B1 .667, A2-B2 .176"""
        g = lambda x0, x1: ("x", x0, 10, x0, 0, x1, 10, "F", 10)
        A1, A2, B1, B2 = g(0, 10), g(4, 14), g(2, 12), g(-3, 7)
        self.assertAlmostEqual(judge.iou_quads(A1, B1), 2 / 3, 3)
        self.assertAlmostEqual(judge.iou_quads(A1, B2), 7 / 13, 3)
        self.assertAlmostEqual(judge.iou_quads(A2, B2), 3 / 17, 3)
        anchor = (g(50, 60), g(50, 60))                          # a pair already made: the line's offset is 0
        for ua in ([A1, A2], [A2, A1]):                          # the leftovers' order does not matter
            for ub in ([B1, B2], [B2, B1]):
                pairs, la, lb = judge.coincident([anchor], ua, ub, [], [])
                self.assertEqual((la, lb), ([], []), (ua, ub))
                self.assertEqual({(id(a), id(b)) for a, b in pairs[1:]}, {(id(A1), id(B2)), (id(A2), id(B1))})

    def test_best_matching_against_brute_force(self):
        rng = random.Random(17)

        def brute(na, nb, E):
            for k in range(min(na, nb), 0, -1):
                ws = [sum(E[e] for e in zip(As, Bs)) for As in itertools.combinations(range(na), k)
                      for Bs in itertools.permutations(range(nb), k) if all(e in E for e in zip(As, Bs))]
                if ws:
                    return k, max(ws)
            return 0, 0.0
        for _ in range(200):
            na, nb = rng.randint(1, 5), rng.randint(1, 5)
            E = {(i, j): round(rng.uniform(0.5, 1), 3) for i in range(na) for j in range(nb) if rng.random() < 0.5}
            got = judge.best_matching(na, nb, E)
            self.assertEqual(len({i for i, _ in got}), len(got))
            self.assertEqual(len({j for _, j in got}), len(got))
            self.assertTrue(all(e in E for e in got))
            k, w = brute(na, nb, E)
            self.assertEqual((len(got), round(sum(E[e] for e in got), 6)), (k, round(w, 6)), E)

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_line_start_ligature_is_the_lines(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-j11-") as d:
            with open(os.path.join(d, "page.tex"), "w") as fh:
                fh.write("\\documentclass[12pt]{article}\\usepackage[T1]{fontenc}\\pagestyle{empty}\n"
                         "\\begin{document}\n\\noindent field and other words\n\\end{document}\n")
            subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                           capture_output=True, check=True)
            pdf = os.path.join(d, "page.pdf")
            tr = subprocess.run(["mutool", "trace", pdf, "1"], capture_output=True, text=True, check=True).stdout
            first = re.findall(r'glyph="([^"]*)" x="([\d.]+)"', tr)[0]
            self.assertEqual(first[0], "fi")                                    # the fixture holds the case
            ox = float(first[1]) * judge.S
            x = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
            bs = [tuple(float(v) for v in m) for m in WORD.findall(x)]
            b = (min(v[0] for v in bs), min(v[1] for v in bs), max(v[2] for v in bs), max(v[3] for v in bs))
            w, h = judge.page_size(pdf, 1)
            x0 = math.floor(ox) + 1                     # the box starts right of the ligature's origin (69bc: 0.8 px)
            doc = {"pages": [{"page": 1, "page_width": round(w * 400 / 72), "page_height": round(h * 400 / 72),
                              "lines": [{"type": "text", "text": "field and other words", "region": {
                                  "top_left_x": x0, "top_left_y": int(b[1] * judge.S) - 4,
                                  "width": int(b[2] * judge.S) + 4 - x0, "height": int((b[3] - b[1]) * judge.S) + 8}}]}]}
            lj = os.path.join(d, "r.lines.json")
            with open(lj, "w") as fh:
                json.dump(doc, fh)
            out = io.StringIO()
            res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out, route="glyph")
            self.assertIn("set by setter", out.getvalue().splitlines()[0])
            self.assertEqual(res["glyph"], 0, [l for l in out.getvalue().splitlines() if l.startswith("G ")][:3])   # 2


class JudgeMathAlphanumerics(unittest.TestCase):
    """A Mathematical Alphanumeric Symbol pairs as its letter. The judge sets a reading's $i$ in LibertineMathMI, whose
    ToUnicode says U+1D456; a page whose map says i for the same letter (board 1805.01788 p3, LinLibertineI7: 20 of
    35 unpaired_B) left both sides unpaired. Here the page sets i and j as Libertine text italics, mapped ASCII."""

    def test_fold(self):
        self.assertEqual([judge.math_alpha(c) for c in ("&#x1d456;", "&#x1D457;", "&#x1d6fc;", "&#x1d7d9;", "&#x1d455;",
                                                         "&#x2212;", "i")],
                         ["i", "j", "&#x3b1;", "1", "&#x1d455;", "&#x2212;", "i"])

    def test_variation_selectors_are_not_glyphs(self):
        for c in ("&#xfe01;", "&#xFE0F;", "&#xe0100;", "&#xe01ef;"):
            self.assertTrue(judge.VARIATION.fullmatch(c), c)
        for c in ("&#x2211;", "&#xfe10;", "&#xe01f0;", "i"):
            self.assertFalse(judge.VARIATION.fullmatch(c), c)

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_display_sum_pairs_alone(self):
        """the reading's display sum set from txexs reads U+2211 U+FE01: the selector is no unpaired glyph"""
        with tempfile.TemporaryDirectory(prefix="test-judge-vs-") as d:
            pre = "\\usepackage{libertine}\\usepackage[libertine]{newtxmath}\n"
            _accent_case(d, "s", "Let $x = \\displaystyle\\sum_i y_i$ here.",
                         "Let \\(x = \\displaystyle\\sum_i y_i\\) here.", pre)
            out = io.StringIO()
            res = judge.judge(os.path.join(d, "s", "page.pdf"), 1, os.path.join(d, "s", "r.lines.json"),
                              os.path.join(d, "s", "g"), out=out, route="glyph", setter=False)
            lines = out.getvalue().splitlines()
            self.assertEqual([ln for ln in lines if "fe0" in ln.lower()], [])
            self.assertTrue([ln for ln in lines if ln.startswith("G L0 &#x2211; &#x2211; 1 ")], lines)

    @unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
    def test_libertine_maths_letters_pair_with_ascii(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-malpha-") as d:
            pre = "\\usepackage{libertine}\\usepackage[libertine]{newtxmath}\n"
            _accent_case(d, "a", "For every $x$, \\textit{i} and \\textit{j} here.",   # $x$: the page's maths font
                         "For every \\(x\\), \\(i\\) and \\(j\\) here.", pre)
            out = io.StringIO()
            res = judge.judge(os.path.join(d, "a", "page.pdf"), 1, os.path.join(d, "a", "r.lines.json"),
                              os.path.join(d, "a", "g"), out=out, route="glyph", setter=False)
            lines = out.getvalue().splitlines()
            self.assertEqual([ln for ln in lines if ln.startswith("U ")], [])
            ij = sorted(ln.split()[2] for ln in lines if ln.startswith("G ") and ln.split()[2] in "ij"
                        and ln.split()[4] == "1")
            self.assertEqual(ij, ["i", "j"], lines)
            self.assertEqual((res["unpaired_A"], res["unpaired_B"]), (0, 0))


class JudgeMathFamilies(unittest.TestCase):
    """A page set with mathabx (fonts TeX-matha/TeX-mathb, or matha/mathb) is judged with mathabx loaded: its \\in
    and \\geq are its own designs, and set from Computer Modern they differ in shape -- board lib:c9daf79960c3 p68
    counted each \\in twice, once as a glyph finding and once as an invented rule (6 rules on every column)."""

    def pick(self, font):
        return [p for r, p in judge.MATHFAM if re.search(r, font)]

    def test_mathabx_fonts_load_mathabx(self):
        for f in ("TeX-matha12", "TeX-matha6", "TeX-mathb10", "matha10", "mathb8"):
            self.assertEqual(self.pick(f), [r"\usepackage{mathabx}"], f)

    def test_other_maths_fonts_do_not(self):
        for f in ("CMSY10", "CMMI10", "MSBM10", "TeX-mathx12", "LMMathSymbols10-Regular", "TeX-mathit10", "mathpazo"):
            self.assertNotIn(r"\usepackage{mathabx}", self.pick(f), f)

    def test_mathtime_loads_mathptmx(self):
        """MathTime (MTMI, MTMIB, MTSYN, MTEX; board lib:c57ac038ae5f p293, lib:3fe71b4ef018 p328) has no free
        version; mathptmx is its Times-based relative: set from CMMI its italics and its \\mathrm words (Times on the
        page) differ in shape (p293: MathTime flags 127 -> 58, Times-Roman 66 -> 31)"""
        for f in ("QtxpctLqshvdMTMI", "HcxldrTdxpbjMTMIB", "JnskryTvgfgxMTSYN", "YtllkcRjyfrxMTEX", "MTMI", "MTSY"):
            self.assertEqual(self.pick(f), [r"\usepackage{mathptmx}"], f)
        for f in ("CMMI10", "CMSY10", "Times-Roman", "NimbusRomNo9L-Regu", "TeX-matha10"):
            self.assertNotIn(r"\usepackage{mathptmx}", self.pick(f), f)


class JudgeStyleNames(unittest.TestCase):
    """style() reads the face from the font's own name: a subset tag (ABCDEF+) and a producer's obfuscation prefix
    (one or two capitalised consonant-only six-letter words: "Lxcbxs", board lib:c57ac038ae5f) are not part of it --
    "YvftdhLxcbxsTimes-Italic" read "bi" because "cbxs" holds "bx". Small caps are a face of their own ("c")."""

    def test_prefixes_are_not_the_name(self):
        self.assertEqual(judge.style("YvftdhLxcbxsTimes-Italic"), "i")
        self.assertEqual(judge.style("KXBANU+YvftdhLxcbxsTimes-Italic"), "i")
        self.assertEqual(judge.style("XBHUGF+VctcqrYchftkTimes-BoldItalic"), "bi")
        self.assertEqual(judge.style("FjpnnhJgjywlTimes-Bold"), "b")
        self.assertEqual(judge.style("HffcmrYvygwvTimes-Roman"), "")
        self.assertEqual(judge.style("JSLHNA+XtrrcfCMMI6"), "xi")

    def test_real_names_keep_their_words(self):     # a name made of real words is not a prefix
        self.assertEqual(judge.style("NimbusRomNo9L-MediItal"), "bi")
        self.assertEqual(judge.style("Helvetica-Bold"), "b")
        self.assertEqual(judge.style("MediumItalic"), "bi")
        self.assertEqual(judge.style("CMBX10"), "b")

    def test_small_caps(self):
        self.assertEqual(judge.style("LnrnjhTimes-RomanSmallCaps"), "c")
        self.assertEqual(judge.style("LnrnjhTimes-RomanSmallCa"), "c")     # mutool's stext cuts names at 24 (p328)
        self.assertEqual(judge.style("CMCSC10"), "c")
        self.assertEqual(judge.style("Times-Roman"), "")

    def test_mathtime_names(self):
        """MathTime subsets (board18 lib:c57ac038ae5f p293) are maths fonts: MTMI math italic, MTMIB bold math
        italic, MTSYN symbols, MTEX the extension font. They read "" until 2026-11-21, so 285 of the page's paired
        glyphs compared a plain A face against an italic or maths B face."""
        self.assertEqual(judge.style("QtxpctLqshvdMTMI"), "xi")
        self.assertEqual(judge.style("HcxldrTdxpbjMTMIB"), "xbi")
        self.assertEqual(judge.style("JnskryTvgfgxMTSYN"), "x")
        self.assertEqual(judge.style("YtllkcRjyfrxMTEX"), "x")
        self.assertEqual(judge.style("ABCDEF+MT2MIT"), "xi")
        self.assertEqual(judge.style("MT2SYT"), "x")
        self.assertEqual(judge.style("MTSYB"), "xb")
        for name in ("Mtpro", "MTimes-Roman", "Smtmi", "HffcmrYvygwvTimes-Roman"):   # not MathTime
            self.assertNotIn("x", judge.style(name), name)

    def test_unsettable_faces(self):
        """faces with no font program in the TeX tree: MathTime, Optima, Myriad Pro. The free faces that FAMILIES
        does not map either (cm-super, newtx, bbold, esint, Belleek) are settable and stay measured."""
        for name in ("QtxpctLqshvdMTMI", "HcxldrTdxpbjMTMIB", "JnskryTvgfgxMTSYN", "YtllkcRjyfrxMTEX", "MT2SYT",
                     "ABCDEF+Optima-Bold", "MyriadPro-Bold", "PDBEHA+HelveticaNeueLTStd-LtCn",
                     "PDBEAN+HelveticaNeueLTStd-BdCn", "HelveticaNeue-CondensedBold"):     # J8: Neue condensed
            self.assertTrue(judge.unsettable(name), name)
        for name in ("BLEX", "SFBX1000", "txsys", "rtxr", "BBOLD7", "Gdvdkgesint10", "MTimes-Roman",
                     "HffcmrYvygwvTimes-Roman", "CMMI10", "HelveticaNeueLTStd-Roman", "HelveticaNeue-Bold",
                     "Helvetica-Narrow"):
            self.assertFalse(judge.unsettable(name), name)
        self.assertTrue(re.search(judge.MATHFONT, "QtxpctLqshvdMTMI"))
        self.assertFalse(re.search(judge.MATHFONT, "HffcmrYvygwvTimes-Roman"))
        # MathTime stays in a line's size vote: without it a display line votes for its script size (p328 L20)
        self.assertFalse(re.search(judge.SIZEVOTE_SKIP, "QtxpctLqshvdMTMI"))
        # CMMI and CMSY vote since T-0158 (their glyphs report the size they are set at); CMEX does not
        self.assertFalse(re.search(judge.SIZEVOTE_SKIP, "JSLHNA+XtrrcfCMMI6"))
        self.assertTrue(re.search(judge.SIZEVOTE_SKIP, "JSLHNA+XtrrcfCMEX10"))


class JudgeDesignSize(unittest.TestCase):
    """The snippets are set with fix-cm: at a size that is not one of LaTeX's own (board lib:6d90e8d27b8b p174: body
    9.63 pt, scripts 6.74 and 4.82 -- LaTeX's 0.7/0.5) the page took its designs by size range (SFRM0900, CMR9, CMR6,
    CMR5), and the judge's .fd files, which list exact sizes only, took SFRM1000, CMR7 or substituted 6 pt for 6.74.
    fix-cm declares the same ranges, so the judge sets the page's own fonts at the page's own sizes. (It replaced
    15e09d9's magnification, which set the 9 pt design scaled by 1.07: scripts 6.42 pt against 6.74.)"""

    def test_set_size_snaps_to_latex_sizes(self):
        """a size within 1.5% of one of LaTeX's own is that size: microtype's font expansion sets a 12 pt design
        at 11.88 and 12.12 pt (board lib:c9daf79960c3 p68), and fix-cm took the 10 pt design for 11.88 (0.0147 ->
        0.0346); p174's 9.63 pt is 3.7% from 10 and stays"""
        bp = 72 / 72.27
        self.assertEqual(judge.set_size(11.879 * bp), 12)
        self.assertEqual(judge.set_size(12.119 * bp), 12)
        # within 0.1% of a LaTeX size: the size as it is, as before fix-cm (board lib:3fe71b4ef018 p328: 10.0004 pt
        # set as exactly 10 moved three pairs of a display, 0.0790 -> 0.0828)
        self.assertEqual(judge.set_size(9.963), 9.963 * 72.27 / 72)
        self.assertAlmostEqual(judge.set_size(10.909), 10.95, places=2)    # 11pt class, in bp
        self.assertAlmostEqual(judge.set_size(9.594), 9.63, places=2)   # p174: not snapped
        self.assertEqual(judge.set_size(6.0), 6)                     # 6.02 pt: 0.4% from 6, snapped

    def test_snippets_load_fix_cm_first(self):
        with tempfile.TemporaryDirectory(prefix="test-judge-fixcm-") as wd:
            if not shutil.which("pdflatex"):
                self.skipTest("needs pdflatex")
            judge.compile_lines(["a $x^{2}$ b"], [9.6301], "", wd)
            with open(os.path.join(wd, "lines.tex"), encoding="utf-8") as fh:
                self.assertTrue(fh.read().startswith("\\RequirePackage{fix-cm}\n\\documentclass"))


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeMagnifiedPage(unittest.TestCase):
    """end to end, p174's shape: a page set at 9.63 pt with fix-cm (T1, scripts at LaTeX's ratios), read back exactly,
    pairs with no glyph finding -- its body in SFRM0900 and its scripts in CMR6/CMMI6 at 6.74 pt"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-mag-")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_page_at_a_non_standard_size(self):
        text = ("Die Integration liefert $x_{k}^{2} = c_{n} - t_{m}^{2} + a_{ij}^{pq} b_{kl}^{rs} + 12^{34}_{56}$ und so "
                "fort.")
        res, out = _accent_case(self.tmp.name, "mag", "\\fontsize{9.63}{11.5}\\selectfont " + text, text,
                                preamble="\\usepackage{fix-cm}\\usepackage[T1]{fontenc}\n")
        self.assertGreater(res["glyphs"], 50)
        fonts = {ln.split()[11].split("+")[-1] for ln in out if ln.startswith("G ")}
        self.assertTrue({"SFRM0900", "CMR6"} <= fonts, fonts)            # the fixture is the case it claims
        self.assertEqual(res["glyph"], 0, [ln for ln in out if ln.startswith("G ") and ln.split()[8] == "1"])


class JudgeDisplayStyleRule(unittest.TestCase):
    """A line typed math or equation is a display (the lines.json schema), set in display style whatever its
    delimiters, for every column alike: ours writes its displays as $...$ and they were set in text style, every
    \\frac at script size (board lib:3fe71b4ef018 p328 L20: 34 -> 57 items once the reader built the fraction
    right). \\[...\\] and $$...$$ are display already and come through unchanged."""

    def test_display_style(self):
        ds = judge.display_style
        self.assertEqual(ds("$\\frac{a}{b}$"), "$\\displaystyle \\frac{a}{b}$")
        self.assertEqual(ds("\\(\\frac{a}{b}\\)"), "\\(\\displaystyle \\frac{a}{b}\\)")
        self.assertEqual(ds("$x = 1$ (14.4.14)"), "$\\displaystyle x = 1$ (14.4.14)")
        self.assertEqual(ds("$a$ and $b$"), "$\\displaystyle a$ and $\\displaystyle b$")
        for same in ("\\[\\frac{a}{b}\\]", "$$\\frac{a}{b}$$", "costs 5\\$ each", "no maths here"):
            self.assertEqual(ds(same), same)

    def test_by_type_not_by_reader(self):
        lt = judge.line_tex
        self.assertEqual(lt({"type": "math", "text": "$\\frac{a}{b}$"}), "$\\displaystyle \\frac{a}{b}$")
        self.assertEqual(lt({"type": "equation", "text": "\\(x\\)"}), "\\(\\displaystyle x\\)")
        self.assertEqual(lt({"type": "math", "text": "\\[\\frac{a}{b}\\]"}), "\\[\\frac{a}{b}\\]")   # MathPix: unchanged
        self.assertEqual(lt({"type": "text", "text": "the ratio $\\frac{a}{b}$ of"}), "the ratio $\\frac{a}{b}$ of")


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "needs " + ", ".join(TOOLS))
class JudgeDisplayStyleCases(unittest.TestCase):
    """end to end, both ways: a display read as a math line written $...$ pairs; inline maths in a text line stays
    text style and pairs"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-disp-")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def flagged(self, out):
        return [ln for ln in out if ln.startswith("G ") and ln.split()[8] == "1"]

    def test_display_line_typed_math(self):
        res, out = _accent_case(self.tmp.name, "disp", "$\\displaystyle \\frac{a+b}{c+d} = \\frac{x}{y}$",
                                "$\\frac{a+b}{c+d} = \\frac{x}{y}$", typ="math")
        self.assertEqual(res["glyphs"], 9, out)
        self.assertEqual(res["glyph"], 0, self.flagged(out))

    def test_inline_in_text_line(self):
        res, out = _accent_case(self.tmp.name, "inl", "The ratio $\\frac{a+b}{c+d}$ is small.",
                                "The ratio $\\frac{a+b}{c+d}$ is small.", typ="text")
        self.assertGreater(res["glyphs"], 15)
        self.assertEqual(res["glyph"], 0, self.flagged(out))


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and os.path.exists(judge.INKCMP),
                     "needs " + ", ".join(TOOLS) + " and build/inkcmp")
class JudgeRotatedGlyph(unittest.TestCase):
    """a rotated label (a plot's tick, \\rotatebox; board 0864 p4 stext dir="0 -1") read correctly pairs clean on the
    glyph route: the reading's glyph is compared turned to the page's direction and the line paired along it. Before,
    every glyph of a 90 degree label was flagged and a 180 degree one paired 3 of 16. A planted letter still flags.
    Both setters: the page's own font program and pdflatex."""
    TEXT = "Rotated label 2024"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-rot-")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, name, angle, read, setter):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.join(d, "w"))
        with open(os.path.join(d, "page.tex"), "w") as fh:
            fh.write("\\documentclass[10pt]{article}\n\\usepackage[margin=2.5cm]{geometry}\n\\usepackage{graphicx}\n"
                     "\\pagestyle{empty}\n\\begin{document}\n\\noindent\\rotatebox{%d}{\\mbox{%s}}\n\\end{document}\n"
                     % (angle, self.TEXT))
        subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                       capture_output=True, check=True)
        pdf = os.path.join(d, "page.pdf")
        cs = judge.chars(pdf, 1, 1)[0]
        x0, y0 = min(c[3] for c in cs), min(c[4] for c in cs)
        x1, y1 = max(c[5] for c in cs), max(c[6] for c in cs)
        wpt, hpt = judge.page_size(pdf, 1)
        k = 200 / 72
        doc = {"pages": [{"page": 1, "page_width": round(wpt * k), "page_height": round(hpt * k), "lines": [
            {"type": "text", "text": read, "region": {"top_left_x": int(x0 * k) - 2, "top_left_y": int(y0 * k) - 2,
                                                      "width": int((x1 - x0) * k) + 4,
                                                      "height": int((y1 - y0) * k) + 4}}]}]}
        lj = os.path.join(d, "r.lines.json")
        with open(lj, "w") as fh:
            json.dump(doc, fh)
        out = io.StringIO()
        res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out, route="glyph", setter=setter)
        return res, [ln.split() for ln in out.getvalue().splitlines() if ln.startswith("G ")]

    def test_rotated_label_pairs_clean(self):
        for angle in (90, -90, 180):
            for setter in (True, False):
                with self.subTest(angle=angle, setter=setter):
                    res, rows = self.run_case("clean%d%s" % (angle, setter), angle, self.TEXT, setter)
                    self.assertEqual((res["glyphs"], res["unpaired_A"], res["unpaired_B"]), (16, 0, 0))
                    self.assertEqual([r[2] for r in rows if r[8] == "1"], [])

    def test_planted_letter_still_flags(self):
        for setter in (True, False):
            with self.subTest(setter=setter):
                res, rows = self.run_case("plant%s" % setter, 90, self.TEXT.replace("ted", "tcd"), setter)
                self.assertEqual([(r[2], r[3]) for r in rows if r[8] == "1"], [("e", "c")])


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and os.path.exists(judge.INKCMP),
                     "needs " + ", ".join(TOOLS) + " and build/inkcmp")
class JudgeLineFamily(unittest.TestCase):
    """a line in another family than its page's (a plot's DejaVuSerif labels on a Times page, board 2312.11532 p7; a
    Utopia running head, 0864 p4) is set in that family, by the page's own font names; before, every glyph of it was
    set in Times and flagged. The page's Times line is set as before, and a planted letter still flags. The setter
    sets one font per page, so such a page goes to pdflatex whichever route was asked for."""
    LINES = [("times", "The body of the page is set in Times Roman here."),
             ("times", "Most of its glyphs are Times, as on the board pages,"),
             ("times", "so the page family is Times and the rest is a second."),
             ("put", "Proceedings of the Conference on Reading"),
             ("DejaVuSerif-TLF", "Diversity and coherence 0.15")]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-judge-fam-")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, name, reads, setter):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.join(d, "w"))
        body = "\\\\\n".join(t if f == "times" else "{\\fontfamily{%s}\\selectfont %s}" % (f, t) for f, t in self.LINES)
        with open(os.path.join(d, "page.tex"), "w") as fh:
            fh.write("\\documentclass[10pt]{article}\n\\usepackage[T1]{fontenc}\\usepackage{times}\n"
                     "\\usepackage[margin=2.5cm]{geometry}\n\\pagestyle{empty}\n\\begin{document}\n\\noindent "
                     + body + "\n\\end{document}\n")
        subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "page.tex"], cwd=d,
                       capture_output=True, check=True)
        pdf = os.path.join(d, "page.pdf")
        rows = {}
        for c in judge.chars(pdf, 1, 1)[0]:
            rows.setdefault(round(c[2]), []).append(c)
        wpt, hpt = judge.page_size(pdf, 1)
        k = 200 / 72
        lines = []
        for (_, cs), read in zip(sorted(rows.items()), reads):
            x0, y0 = min(c[3] for c in cs), min(c[4] for c in cs)
            x1, y1 = max(c[5] for c in cs), max(c[6] for c in cs)
            lines.append({"type": "text", "text": read, "region": {
                "top_left_x": int(x0 * k) - 2, "top_left_y": int(y0 * k) - 2,
                "width": int((x1 - x0) * k) + 4, "height": int((y1 - y0) * k) + 4}})
        lj = os.path.join(d, "r.lines.json")
        with open(lj, "w") as fh:
            json.dump({"pages": [{"page": 1, "page_width": round(wpt * k), "page_height": round(hpt * k),
                                  "lines": lines}]}, fh)
        out = io.StringIO()
        res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out, route="glyph", setter=setter)
        return res, [ln.split() for ln in out.getvalue().splitlines() if ln.startswith("G ")]

    def test_each_line_in_its_family(self):
        for setter in (True, False):
            with self.subTest(setter=setter):
                res, rows = self.run_case("clean%s" % setter, [t for _, t in self.LINES], setter)
                self.assertEqual(res["glyphs"], sum(len(t.replace(" ", "")) for _, t in self.LINES))
                self.assertNotIn("setter", res, "a page with a second family is set by pdflatex")
                self.assertEqual([(r[1], r[2], r[11]) for r in rows if r[8] == "1"], [])

    def test_planted_letter_still_flags(self):
        reads = [t for _, t in self.LINES]
        reads[3] = reads[3].replace("Conference", "Conforence")
        res, rows = self.run_case("plant", reads, False)
        self.assertEqual([(r[2], r[3]) for r in rows if r[8] == "1"], [("e", "o")])


FIXTURE_T3 = os.path.join(os.path.dirname(__file__), "fixtures", "type3-mpl.pdf")


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS + ("pdffonts",)) and os.path.exists(judge.INKCMP),
                     "needs " + ", ".join(TOOLS) + ", pdffonts and build/inkcmp")
class JudgeType3Snap(unittest.TestCase):
    """poppler draws a Type 3 glyph from its glyph cache at the floor of the device origin, so the page's ink of it
    sits up to a pixel from where the outline's exact origin puts it (board 2312.11532 p7: matplotlib's DejaVuSerif
    labels, rotated and upright, B 0.4-1.25 px off). The judge places the reading's glyph there too. Fixture: a
    matplotlib page (tools/make_type3_fixture.py), DejaVuSerif "Divers" at every quarter-pixel fraction of origin,
    upright and rotated, read correctly. Snapped, 4 of its 192 DejaVuSerif glyphs flag (outline differences between
    TeX's DejaVuSerif and matplotlib's); placed at the exact origin, 46."""

    def flags(self):
        d = tempfile.mkdtemp(prefix="test-judge-t3-", dir=os.environ.get("TMPDIR"))
        try:
            st = judge.stext(FIXTURE_T3, "1")
            wpt, hpt = judge.page_size(FIXTURE_T3, 1)
            k = 200 / 72
            lines = []
            for blk in re.findall(r"<line [^>]*>.*?</line>", st, re.S):
                cs = [(c, [float(v) for v in q.split()]) for c, q in re.findall(r'<char c="(.*?)" quad="([^"]+)"', blk)]
                xs = [v for _, q in cs for v in q[0::2]]
                ys = [v for _, q in cs for v in q[1::2]]
                lines.append({"type": "text", "text": "".join(c for c, _ in cs).strip(), "region": {
                    "top_left_x": int(min(xs) * k) - 2, "top_left_y": int(min(ys) * k) - 2,
                    "width": int((max(xs) - min(xs)) * k) + 4, "height": int((max(ys) - min(ys)) * k) + 4}})
            lj = os.path.join(d, "r.lines.json")
            with open(lj, "w") as fh:
                json.dump({"pages": [{"page": 1, "page_width": round(wpt * k), "page_height": round(hpt * k),
                                      "lines": lines}]}, fh)
            out = io.StringIO()
            res = judge.judge(FIXTURE_T3, 1, lj, os.path.join(d, "w"), out=out, route="glyph", setter=False)
            rows = [ln.split() for ln in out.getvalue().splitlines() if ln.startswith("G ")]
            return res, sum(1 for r in rows if r[11] == "DejaVuSerif"), \
                sum(1 for r in rows if r[11] == "DejaVuSerif" and r[8] == "1")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_type3_names(self):
        self.assertEqual(judge.type3_names(FIXTURE_T3, 1), {"DejaVuSerif", "NimbusRoman-Regular"})

    def test_snapped(self):
        res, n, flagged = self.flags()
        self.assertEqual((res["glyphs"], res["a_page_ink"], n), (552, 552, 192))
        self.assertLessEqual(flagged, 4)

    def test_exact_origin_flags(self):
        """the other side: B at the exact origin (snap made the identity) flags ten times as many"""
        real = judge.snap
        judge.snap = lambda side, adds, X, Y: (side, adds)
        try:
            _, _, flagged = self.flags()
        finally:
            judge.snap = real
        self.assertGreaterEqual(flagged, 40)


@unittest.skipUnless(shutil.which("pdflatex") and shutil.which("mutool"), "needs pdflatex and mutool")
class JudgeFontPrologue(unittest.TestCase):
    """pdfTeX writes one font object per TFM with /Widths from the size the document first set it at (one decimal:
    SFRM1000's v is 527.7 at 10 pt, 527.6 at 10.4 pt), and poppler draws by them: a page's snippet moved with the
    jobs set before it in a batch (board, reversed order: 10 of 49 judge.out 1 px apart). compile_group's first page
    sets every TFM at its design size, so a page's widths are the same alone, after a 10.4 pt page and before it."""

    @staticmethod
    def widths(pdf, page):
        out = {}
        res = subprocess.run(["mutool", "show", pdf, "pages/%d/Resources/Font" % page], capture_output=True,
                             text=True).stdout
        for o in re.findall(r"/F\d+ (\d+) 0 R", res):
            d = subprocess.run(["mutool", "show", pdf, o], capture_output=True, text=True).stdout
            w = re.search(r"/Widths (\d+) 0 R", d)
            if w:
                body = subprocess.run(["mutool", "show", pdf, w[1]], capture_output=True, text=True).stdout
                out[re.search(r"/BaseFont /\w+\+(\S+)", d)[1]] = body.split("\n", 1)[1]
        return out

    def test_widths_do_not_depend_on_the_group(self):
        bp = 72 / 72.27
        ten, big = (["kqvxy However"], [10 * bp]), (["kqvxy However"], [10.4 * bp])
        got = {}
        with tempfile.TemporaryDirectory(prefix="test-judge-prologue-") as d:
            for tag, jobs, at in (("alone", [ten], 0), ("after", [big, ten], 1), ("before", [ten, big], 0)):
                wd = os.path.join(d, tag)
                os.makedirs(wd)
                pdf, res = judge.compile_group(jobs, "", wd)
                got[tag] = self.widths(pdf, res[at][2] + 1)
        self.assertIn("SFRM1000", got["alone"])
        self.assertEqual(got["after"], got["alone"])
        self.assertEqual(got["before"], got["alone"])


class JudgeRuleOverPageMaths(unittest.TestCase):
    """rule_ink on a MathTime page (board18 lib:c57ac038ae5f p293, L5 d/dalpha and L27 r/r): the reading's \\frac bar is
    drawn, but the judge sets MathTime maths with mathptmx (NimbusRomNo9L-ReguItal, StandardSymL), which no MATHFONT
    entry names, so the bar was not "over maths" on the reading's side and the page's bar read MISSING. A rendered
    glyph whose page partner is a maths glyph is maths. Sizes from that page at 400 dpi: page bar 50 x 4 px (L5),
    rendered bar 55 x 3 px."""

    W, H = 400, 200

    def draw(self, rects):
        img = bytearray(b"\xff" * self.W * self.H)
        for x0, y0, x1, y1 in rects:
            for y in range(y0, y1):
                img[y * self.W + x0:y * self.W + x1] = b"\x00" * (x1 - x0)
        return bytes(img)

    def run_rules(self, font_a, font_b, taller=0):
        """taller: the rendered numerator's ink and own quad reach that many px above its page partner's quad (p293
        L4/L5: StandardSymL's alpha top at y32 where MathTime's quad, mapped back, starts at y36)"""
        S = judge.S
        num, den, bar_a, bar_b = (160, 40, 190, 90), (160, 110, 195, 150), (150, 97, 200, 101), (148, 98, 203, 101)
        num_b = (num[0], num[1] - taller, num[2], num[3])
        # the rendered ink: its body, and over it a 14 x 3 cap that meets the body along 7 px (p293: touch 7 of 42)
        ink_b = [num] if not taller else [(160, 39, 175, 90), (175, 40, 190, 90), (168, 36, 182, 39)]
        def ch(cid, q, font):
            return (cid, q[0] / S, q[3] / S, q[0] / S, q[1] / S, q[2] / S, q[3] / S, font, 9.96)
        ca = [ch(0, num, font_a), ch(1, den, font_a)]
        cb = [ch(0, num_b, font_b), ch(1, den, font_b)]
        acrop = (self.W, self.H, self.draw([num, den, bar_a]), 0, 0)
        bpage = (self.W, self.H, self.draw(ink_b + [den, bar_b]))
        res, out = collections.Counter(), io.StringIO()
        judge.rule_ink(5, (100, 30, 300, 160), ca, cb, list(zip(ca, cb)), acrop, bpage, res, out, up=1)
        return res

    def test_bar_over_page_maths_is_found(self):
        res = self.run_rules("QtxpctLqshvdMTMI", "StandardSymL-Slant_167")
        self.assertEqual((res["rules_a"], res["rules_missing"], res["rules_invented"]), (1, 0, 0))

    def test_bar_over_text_on_both_sides_stays_out(self):
        res = self.run_rules("HffcmrYvygwvTimes-Roman", "NimbusRomNo9L-Regu")
        self.assertEqual((res["rules_a"], res["rules_missing"], res["rules_invented"]), (0, 0, 0))

    def test_rendered_glyph_top_past_its_partner_quad_is_not_a_rule(self):
        res = self.run_rules("QtxpctLqshvdMTMI", "StandardSymL-Slant_167", taller=4)
        self.assertEqual((res["rules_a"], res["rules_missing"], res["rules_invented"]), (1, 0, 0))

    def test_bar_over_maths_on_both_sides_is_found(self):
        res = self.run_rules("CMMI10", "CMMI10")
        self.assertEqual((res["rules_a"], res["rules_missing"], res["rules_invented"]), (1, 0, 0))

"""inkcmp judge (inkdrill/judge.py, bin/inkcmp-judge): a born-digital page and its own reading judge clean;
single-letter plants in the reading are each flagged at the frozen threshold.

The page is made here. pdflatex sets eight lines of plain text in Computer Modern. The reading is a
MathPix-shaped lines.json: one region per printed line (pdftotext -bbox-layout, CropBox px at 200 dpi),
holding the source text of that line. Needs pdflatex, mutool, pdftoppm, pdfinfo and pdftotext; skips when
any of them is missing.
"""
import copy
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from inkdrill import judge

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

    def test_overlapping_boxes_pair_each_glyph_once(self):
        """a reading with a line boxed twice (overlapping regions) must not pair the page's glyphs twice"""
        clean, _ = self.run_judge(self.doc, "clean2")
        doc = copy.deepcopy(self.doc)
        doc["pages"][0]["lines"].insert(1, copy.deepcopy(doc["pages"][0]["lines"][0]))
        res, _ = self.run_judge(doc, "dup")
        self.assertLessEqual(res["glyphs"], res["a_glyphs"])
        self.assertLessEqual(res["glyphs"], clean["glyphs"])
        self.assertEqual(res["a_glyphs"], res["glyphs"] + res["a_unpaired"])

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


def _accent_case(tmp, name, page_line, read_line, preamble="", route="render"):
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
        {"type": "text", "text": read_line, "region": {"top_left_x": int(b[0] * k) - 2, "top_left_y": int(b[1] * k) - 2,
                                                      "width": int((b[2] - b[0]) * k) + 4,
                                                      "height": int((b[3] - b[1]) * k) + 4}}]}]}
    lj = os.path.join(d, "r.lines.json")
    with open(lj, "w") as fh:
        json.dump(doc, fh)
    out = io.StringIO()
    os.makedirs(os.path.join(d, "w"))
    res = judge.judge(pdf, 1, lj, os.path.join(d, "w"), out=out)
    return res, out.getvalue().splitlines()


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
        self.assertIn("U L0 A &#xaf;", out)
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
        self.assertIn("U L0 A 2", out)                      # the page's glyph, unpaired, once
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


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS) and shutil.which("kpsewhich"), "needs " + ", ".join(TOOLS))
class JudgeSetter(unittest.TestCase):
    """A page without formula lines is set in the page's own font program (inkdrill/setter.py), not by pdflatex:
    a clean reading has no flag, a planted letter is flagged, a character the page never printed comes from the TeX
    tree's Type 1 of the same font, and a page with a formula line is still set by pdflatex."""

    def run_case(self, d, name, page, read):
        res, out = _accent_case(d, name, page, read)
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

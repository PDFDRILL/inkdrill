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
                     "ABCDEF+Optima-Bold", "MyriadPro-Bold"):
            self.assertTrue(judge.unsettable(name), name)
        for name in ("BLEX", "SFBX1000", "txsys", "rtxr", "BBOLD7", "Gdvdkgesint10", "MTimes-Roman",
                     "HffcmrYvygwvTimes-Roman", "CMMI10"):
            self.assertFalse(judge.unsettable(name), name)
        self.assertTrue(re.search(judge.MATHFONT, "QtxpctLqshvdMTMI"))
        self.assertFalse(re.search(judge.MATHFONT, "HffcmrYvygwvTimes-Roman"))
        # MathTime stays in a line's size vote: without it a display line votes for its script size (p328 L20)
        self.assertFalse(re.search(judge.SIZEVOTE_SKIP, "QtxpctLqshvdMTMI"))
        self.assertTrue(re.search(judge.SIZEVOTE_SKIP, "JSLHNA+XtrrcfCMMI6"))


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
        fonts = {ln.split()[-1].split("+")[-1] for ln in out if ln.startswith("G ")}
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

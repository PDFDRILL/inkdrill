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
        res = judge.judge(self.pdf, 1, lj, wd, out=out)
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
        if not hasattr(self.__class__, "_planted"):
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

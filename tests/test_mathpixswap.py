"""`tools/mathpixswap.py` -- the MathPix copy of an equation list.

The lists carry the free reader's LaTeX. This builds the parallel list
carrying the paid reader's, by matching regions, and DROPS what MathPix
did not see. Every rule below is one that can quietly corrupt the
output rather than fail: a wrong frame puts a region in the wrong part
of the page, a loose threshold pairs an equation with its neighbour,
and a row kept with empty LaTeX looks like a reading rather than a gap.
"""
import importlib.util
import pathlib
import sys
import unittest


def _tool():
    p = pathlib.Path(__file__).resolve().parent.parent / "tools" / "mathpixswap.py"
    spec = importlib.util.spec_from_file_location("_mps", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_mps"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _tool()


def reading(page_width=2125, lines=()):
    return {"pages": [{"page": 1, "page_width": page_width,
                       "page_height": 2750, "lines": list(lines)}]}


def mathline(x, y, w, h, text, lid="L1"):
    return {"type": "math", "id": lid,
            "region": {"top_left_x": x, "top_left_y": y, "width": w, "height": h},
            "text": text}


def row(x=100, y=200, w=300, h=40, page_width_pt=612.0, latex="pdf2mmd"):
    return {"document": "d", "label": "EQ0001", "page": "1",
            "page_width_pt": str(page_width_pt), "page_height_pt": "792.0",
            "px_x": str(x), "px_y": str(y), "px_w": str(w), "px_h": str(h),
            "latex": latex, "latex_origin": "pdf2mmd"}


class TM_1_Delimiters(unittest.TestCase):
    def test_display_delimiters_are_stripped(self):
        self.assertEqual(M.strip_delimiters("\\[\nx = y\n\\]"), "x = y")

    def test_dollar_dollar_too(self):
        self.assertEqual(M.strip_delimiters("$$ x = y $$"), "x = y")

    def test_a_bare_body_is_unchanged(self):
        self.assertEqual(M.strip_delimiters("x = y"), "x = y")


class TM_2_TheJoin(unittest.TestCase):
    def test_an_overlapping_equation_is_swapped_in(self):
        rd = reading(lines=[mathline(100, 200, 300, 40, "\\[a=b\\]")])
        kept, dropped = M.swap_document([row()], rd, 0.5)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["latex"], "a=b")
        self.assertEqual(kept[0]["latex_origin"], "mathpix")
        self.assertEqual(kept[0]["mathpix_line_id"], "L1")
        self.assertEqual(dropped, {})

    def test_a_row_MathPix_did_not_see_is_DROPPED(self):
        """The point of the list: no MathPix equation, no row."""
        rd = reading(lines=[mathline(1500, 2400, 200, 40, "\\[q=r\\]")])
        kept, dropped = M.swap_document([row()], rd, 0.5)
        self.assertEqual(kept, [])
        self.assertEqual(dropped["no MathPix equation at this region"], 1)

    def test_a_neighbouring_equation_is_not_accepted(self):
        """Half a line away is a DIFFERENT equation. Measured: below IoU
        0.3 the two readers' LaTeX agrees 0.00-0.08."""
        rd = reading(lines=[mathline(100, 232, 300, 40, "\\[other\\]")])
        kept, _ = M.swap_document([row()], rd, 0.5)
        self.assertEqual(kept, [])

    def test_the_best_of_several_candidates_wins(self):
        rd = reading(lines=[mathline(100, 260, 300, 40, "\\[near\\]", "A"),
                            mathline(100, 200, 300, 40, "\\[exact\\]", "B")])
        kept, _ = M.swap_document([row()], rd, 0.5)
        self.assertEqual(kept[0]["latex"], "exact")
        self.assertEqual(kept[0]["mathpix_line_id"], "B")

    def test_a_MathPix_line_with_no_latex_is_dropped_not_emptied(self):
        """A row kept with an empty LaTeX cell reads as a reading that
        found nothing, rather than as a row that should not be here."""
        rd = reading(lines=[mathline(100, 200, 300, 40, "\\[\\]")])
        kept, dropped = M.swap_document([row()], rd, 0.5)
        self.assertEqual(kept, [])
        self.assertEqual(dropped["MathPix line carries no LaTeX"], 1)


class TM_3_TheFrameIsCheckedNotAssumed(unittest.TestCase):
    """Both regions are 250 dpi from the page top-left -- but only when
    they describe the same page box. Nudging a disagreeing frame into
    line is how a region lands in the bottom margin (out/691)."""

    def test_matching_frames_pass(self):
        rd = reading(page_width=2125, lines=[mathline(100, 200, 300, 40, "\\[a\\]")])
        kept, _ = M.swap_document([row(page_width_pt=612.0)], rd, 0.5)
        self.assertEqual(len(kept), 1)      # 612pt * 250/72 = 2125 px

    def test_a_reading_in_POINTS_is_not_a_mathpix_raster(self):
        """88 documents carry an older pdfminer-derived reading whose
        page and regions are in points. They have no `source` key
        either, so "no source" alone does not identify MathPix."""
        rd = reading(page_width=612.0, lines=[mathline(100, 200, 300, 40, "\\[a\\]")])
        kept, dropped = M.swap_document([row(page_width_pt=612.0)], rd, 0.5)
        self.assertEqual(kept, [])
        self.assertEqual(dropped["the reading is in POINTS, not a MathPix raster"], 1)

    def test_a_DIFFERENT_page_box_is_dropped_not_corrected(self):
        """Ratio 0.69-0.83: MathPix rastered a trimmed box. The inset is
        unknown and guessing it puts regions in the margin."""
        rd = reading(page_width=1500, lines=[mathline(100, 200, 300, 40, "\\[a\\]")])
        kept, dropped = M.swap_document([row(page_width_pt=612.0)], rd, 0.5)
        self.assertEqual(kept, [])
        self.assertEqual(dropped["MathPix rastered a different page box"], 1)

    def test_a_small_difference_is_ACCEPTED_and_left_to_the_overlap(self):
        """7 documents differ by under 10%, MathPix trimming. The IoU
        test is the real gate, so a near frame is admitted."""
        rd = reading(page_width=2040, lines=[mathline(100, 200, 300, 40, "\\[a\\]")])
        kept, _ = M.swap_document([row(page_width_pt=612.0)], rd, 0.5)
        self.assertEqual(len(kept), 1)

    def test_a_page_MathPix_does_not_have_is_skipped(self):
        rd = reading(lines=[])
        rd["pages"][0]["page"] = 7
        kept, dropped = M.swap_document([row()], rd, 0.5)
        self.assertEqual(dropped["MathPix has no such page"], 1)


class TM_4_WhichReadingIsMathPix(unittest.TestCase):
    """pdf2mmd stamps `source` on every page and MathPix stamps none,
    which is how a displaced paid layer is told from a free one."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.doc = pathlib.Path(self.tmp.name) / "d"
        self.doc.mkdir()

    def write(self, name, obj):
        import json
        (self.doc / name).write_text(json.dumps(obj))

    def test_a_pdf2mmd_reading_is_not_mathpix(self):
        self.write("d.lines.json", {"pages": [{"page": 1, "source": "pdfminer-docmodel"}]})
        self.assertIsNone(M.mathpix_reading(self.doc))

    def test_a_reading_with_no_source_is(self):
        self.write("d.lines.json", {"pages": [{"page": 1}]})
        self.assertIsNotNone(M.mathpix_reading(self.doc))

    def test_the_displaced_backup_is_preferred(self):
        self.write("d.lines.json", {"pages": [{"page": 1, "source": "pdfminer-docmodel"}]})
        self.write("d.lines.mathpix.bak.json", {"pages": [{"page": 1, "page_width": 9}]})
        got = M.mathpix_reading(self.doc)
        self.assertEqual(got["pages"][0]["page_width"], 9)


if __name__ == "__main__":
    unittest.main()

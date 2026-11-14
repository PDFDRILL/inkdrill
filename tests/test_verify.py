"""`verify` A2: refusals, states, closed lists, the pixel rule (T-0146).

Every refusal is tested on both sides: a pairs file that is valid except
for one field must be refused, and the same file with that field
corrected (the base document) must pass. Every state and every reason
is reached at least once, so a class that cannot occur is not mistaken
for one that did not.

The PDFs are written byte by byte by `tests.test_ghost.make_pdf`; the
gs-dependent cases skip when gs is not on PATH.
"""

import contextlib
import copy
import io
import json
import shutil
import unittest
from unittest import mock

from inkdrill import verify
from inkdrill.__main__ import main
from inkdrill.raster import Rect
from tests.test_ghost import BOX, BOX_PT, HAVE_GS, LETTER, make_pdf, \
    scratch_dir


def base_doc(orig_sha="0" * 64, cand_sha="1" * 64):
    """A valid pairs file: one measurable pair, one per not_attempted
    reason, one compile failure."""
    return {
        "contract": "inkverify/1", "units": "pt", "dpi": 400,
        "language": "de", "container_types": ["Table", "Figure"],
        "original": {"path": "x.pdf", "sha256": orig_sha, "pages": 1},
        "candidates": {"path": "c.pdf", "sha256": cand_sha, "pages": 1},
        "pairs": [
            {"id": "e1", "type": "Equation", "parent": None, "page": 1,
             "rect_pt": list(BOX_PT), "candidate_page": 1,
             "latex_sha256": "a" * 64, "reason": None},
            {"id": "f1", "type": "Formula", "parent": "e1", "page": 1,
             "rect_pt": None, "candidate_page": None, "reason": "no_region"},
            {"id": "t1", "type": "Text", "parent": None, "page": 1,
             "rect_pt": [10, 10, 20, 20], "candidate_page": None,
             "reason": "no_latex"},
            {"id": "c1", "type": "Equation", "parent": None, "page": 1,
             "rect_pt": [30, 30, 40, 40], "candidate_page": None,
             "reason": "compile_failed"},
        ],
    }


class PixelRule(unittest.TestCase):
    """G5, amendment 3."""

    def test_exact_boundaries(self):
        # 9 pt = 50 px at 400 dpi: nothing to round
        self.assertEqual(verify.rect_px(BOX_PT), Rect(400, 900, 650, 1050))

    def test_floor_low_ceil_high_half_open(self):
        # 72.4 pt = 402.2 px -> 402; 117.1 pt = 650.56 px -> 651
        r = verify.rect_px([72.4, 162.0, 117.1, 189.0])
        self.assertEqual(r, Rect(402, 900, 651, 1050))
        # half-open: the rectangle covers 249 columns, not 250
        self.assertEqual(r.width, 651 - 402)

    @unittest.skipUnless(HAVE_GS, "gs not on PATH")
    def test_the_rule_rectangle_is_where_gs_puts_the_ink(self):
        # G5 against gs itself: the rule's rectangle IS the rendered ink
        from inkdrill import ghost
        from tests.test_ghost import ink_box
        d = scratch_dir(self)
        p = d / "plain.pdf"
        p.write_bytes(make_pdf([{"media": LETTER, "content": BOX}]))
        (m,) = list(ghost.render_masks(p))
        r = verify.rect_px(BOX_PT)
        self.assertEqual(ink_box(m), (r.x0, r.y0, r.x1, r.y1))

    def test_a_rect_inside_one_pixel_covers_it(self):
        r = verify.rect_px([0.01, 0.01, 0.02, 0.02])
        self.assertEqual(r, Rect(0, 0, 1, 1))


class ReadPairs(unittest.TestCase):

    def setUp(self):
        self.dir = scratch_dir(self)

    def write(self, doc):
        p = self.dir / "verify.pairs.json"
        p.write_text(json.dumps(doc) if not isinstance(doc, str) else doc)
        return p

    def refused(self, doc, needle):
        with self.assertRaises(verify.Refusal) as cm:
            verify.read_pairs(self.write(doc))
        self.assertIn(needle, cm.exception.cause)
        self.assertIn("Fix:", cm.exception.line())
        self.assertEqual(len(cm.exception.line().splitlines()), 1)

    def test_the_base_document_passes(self):
        doc = verify.read_pairs(self.write(base_doc()))
        self.assertEqual(len(doc["pairs"]), 4)

    def test_not_json(self):
        self.refused("{not json", "not JSON")

    def test_missing_file(self):
        with self.assertRaises(verify.Refusal):
            verify.read_pairs(self.dir / "absent.json")

    def mutate(self, fn, needle):
        d = base_doc()
        fn(d)
        self.refused(d, needle)

    def test_contract(self):
        self.mutate(lambda d: d.update(contract="inkverify/2"), "contract")

    def test_units(self):
        self.mutate(lambda d: d.update(units="px"), "units")

    def test_dpi(self):
        self.mutate(lambda d: d.update(dpi=300), "dpi")

    def test_container_types_missing(self):
        self.mutate(lambda d: d.pop("container_types"), "container_types")

    def test_container_types_not_strings(self):
        self.mutate(lambda d: d.update(container_types=[1]),
                    "container_types")

    def test_empty_container_types_is_accepted(self):
        # the list may be empty -- a model with no containers -- but present
        d = base_doc()
        d["container_types"] = []
        verify.read_pairs(self.write(d))

    def test_original_sha_missing(self):
        self.mutate(lambda d: d["original"].pop("sha256"), "original.sha256")

    def test_candidates_sha_missing(self):
        self.mutate(lambda d: d["candidates"].pop("sha256"),
                    "candidates.sha256")

    def test_pairs_missing(self):
        self.mutate(lambda d: d.pop("pairs"), "pairs list")

    def test_duplicate_id(self):
        self.mutate(lambda d: d["pairs"].append(copy.deepcopy(d["pairs"][0])),
                    "twice")

    def test_bad_page(self):
        self.mutate(lambda d: d["pairs"][0].update(page=0), "page")

    def test_unknown_reason(self):
        self.mutate(lambda d: d["pairs"][1].update(reason="tired"),
                    "not one of the contract's")

    def test_top_level_not_an_object(self):
        self.refused("[]", "top level")

    def test_pair_not_an_object(self):
        self.mutate(lambda d: d["pairs"].append("e9"), "not an object")

    def test_pair_without_id(self):
        self.mutate(lambda d: d["pairs"][0].pop("id"), "has no id")

    def test_pair_with_empty_id(self):
        self.mutate(lambda d: d["pairs"][0].update(id=""), "has no id")

    def test_a_measured_reason_from_unit_1(self):
        self.mutate(lambda d: d["pairs"][2].update(reason="truth_empty"),
                    "only the judge")

    def test_no_rect_without_no_region(self):
        self.mutate(lambda d: d["pairs"][0].update(rect_pt=None), "rect_pt")

    def test_rect_with_no_region(self):
        self.mutate(lambda d: d["pairs"][1].update(rect_pt=[1, 1, 2, 2]),
                    "no_region")

    def test_rect_inverted(self):
        self.mutate(lambda d: d["pairs"][0].update(rect_pt=[5, 5, 5, 9]),
                    "x0 < x1")

    def test_rect_not_four_numbers(self):
        self.mutate(lambda d: d["pairs"][0].update(rect_pt=[1, 2, 3]),
                    "rect_pt")

    def test_no_candidate_and_no_reason(self):
        self.mutate(lambda d: d["pairs"][0].update(candidate_page=None),
                    "candidate_page")

    def test_bad_candidate_page(self):
        self.mutate(lambda d: d["pairs"][0].update(candidate_page=0),
                    "candidate_page")


@unittest.skipUnless(HAVE_GS, "gs not on PATH")
class Preconditions(unittest.TestCase):
    """The refusals that need the files (G2), both sides each."""

    def setUp(self):
        self.dir = scratch_dir(self)
        self.orig = self.dir / "x.pdf"
        self.orig.write_bytes(make_pdf([{"media": LETTER, "content": BOX}]))
        self.cand = self.dir / "c.pdf"
        self.cand.write_bytes(make_pdf([{"media": (0, 0, 200, 100),
                                         "content": BOX}]))
        self.doc = base_doc(verify.sha256_of(self.orig),
                            verify.sha256_of(self.cand))

    def refused(self, doc, needle, orig=None, cand=None):
        with self.assertRaises(verify.Refusal) as cm:
            verify.check_preconditions(doc, orig or self.orig,
                                       cand or self.cand)
        self.assertIn(needle, cm.exception.cause)

    def test_the_base_passes_and_reports_the_facts(self):
        facts = verify.check_preconditions(self.doc, self.orig, self.cand)
        self.assertEqual(facts["original"]["pages"], 1)
        self.assertEqual(facts["candidates"]["pages"], 1)
        self.assertEqual(facts["original"]["sha256"],
                         self.doc["original"]["sha256"])

    def test_no_pair_carries_a_region(self):
        d = self.doc
        d["pairs"] = [p for p in d["pairs"] if p["rect_pt"] is None]
        self.refused(d, "carries a region")

    def test_original_missing(self):
        self.refused(self.doc, "does not exist", orig=self.dir / "nope.pdf")

    def test_original_sha_differs(self):
        self.doc["original"]["sha256"] = "f" * 64
        self.refused(self.doc, "original's sha256")

    def test_candidates_sha_differs(self):
        self.doc["candidates"]["sha256"] = "f" * 64
        self.refused(self.doc, "candidates's sha256")

    def test_page_absent_from_original(self):
        self.doc["pairs"][0]["page"] = 2
        self.refused(self.doc, "has 1 pages")

    def test_candidate_page_absent(self):
        self.doc["pairs"][0]["candidate_page"] = 2
        self.refused(self.doc, "has 1 pages")

    def test_gs_not_on_path(self):
        with mock.patch.object(verify, "gs_available", lambda: False):
            self.refused(self.doc, "not on PATH")

    def test_unreadable_pdf_is_a_refusal_not_a_traceback(self):
        bad = self.dir / "bad.pdf"
        bad.write_bytes(b"not a pdf\n")
        self.doc["original"]["sha256"] = verify.sha256_of(bad)
        self.refused(self.doc, "gs cannot read", orig=bad)


class States(unittest.TestCase):
    """G3: every state fires, and only through `decide_state`."""

    def test_each_reason_gives_its_state(self):
        for reason, state in verify.REASONS.items():
            self.assertEqual(verify.decide_state(reason), state, reason)

    def test_every_state_is_reached(self):
        got = {
            verify.decide_state("no_region"),
            verify.decide_state("compile_failed"),
            verify.decide_state(None, distance=3, located=True),
            verify.decide_state(None, distance=0, located=True),
            verify.decide_state(None, distance=0, located=False),
        }
        self.assertEqual(got, set(verify.STATES))

    def test_floor_boundary(self):
        # at the floor agrees; one above differs
        self.assertEqual(verify.decide_state(None, distance=2, located=True,
                                             floor=2), "agree")
        self.assertEqual(verify.decide_state(None, distance=3, located=True,
                                             floor=2), "differ")

    def test_default_floor_is_zero_before_g0(self):
        self.assertEqual(verify.FLOOR, 0)
        self.assertEqual(verify.decide_state(None, distance=1,
                                             located=True), "differ")

    def test_refusing_inputs(self):
        with self.assertRaises(ValueError):
            verify.decide_state("tired")
        with self.assertRaises(ValueError):
            verify.decide_state(None)
        with self.assertRaises(ValueError):
            verify.decide_state(None, distance=-1, located=True)
        with self.assertRaises(ValueError):
            verify.decide_state(None, distance=0)


class ClosedLists(unittest.TestCase):
    """G4 and amendment 1."""

    def test_the_amended_kinds_are_listed(self):
        for k in ("straddling_ink", "overlapping_regions", "speckle"):
            self.assertIn(k, verify.KINDS)

    def test_speckle_is_not_emitted(self):
        with self.assertRaises(ValueError):
            verify.finding("speckle", None, {}, "scan", "x")

    def test_every_other_kind_is_accepted(self):
        for k in verify.KINDS:
            if k in verify.NOT_EMITTED:
                continue
            self.assertEqual(verify.finding(k, None, {}, "t", "s")["kind"], k)

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            verify.finding("vibes", None, {}, "t", "s")

    def test_differ_carries_a_finding(self):
        p = base_doc()["pairs"][0]
        with self.assertRaises(ValueError):
            verify.pair_record(p, "differ")
        f = verify.finding("hole_count", None, {}, "glyph", "s")
        self.assertEqual(verify.pair_record(p, "differ",
                                            findings=[f])["state"], "differ")

    def test_a_raw_finding_of_a_closed_out_kind_is_refused(self):
        # pair_record checks findings that did not come through finding()
        p = base_doc()["pairs"][0]
        for kind in ("speckle", "vibes"):
            with self.assertRaises(ValueError):
                verify.pair_record(p, "differ", findings=[{"kind": kind}])

    def test_unknown_state_is_refused(self):
        with self.assertRaises(ValueError):
            verify.pair_record(base_doc()["pairs"][0], "maybe")


class FindingsDocument(unittest.TestCase):
    """G3 and G6."""

    def setUp(self):
        self.doc = base_doc()
        self.facts = {"original": {"path": "x.pdf", "sha256": "0" * 64,
                                   "pages": 1},
                      "candidates": {"path": "c.pdf", "sha256": "1" * 64,
                                     "pages": 1}}
        ps = self.doc["pairs"]
        self.records = [
            verify.pair_record(ps[0], "agree", distance=0),
            verify.pair_record(ps[1], "not_attempted", reason="no_region"),
            verify.pair_record(ps[2], "not_attempted", reason="no_latex"),
            verify.pair_record(ps[3], "unmeasurable",
                               reason="compile_failed"),
        ]

    def test_counts_sum_to_the_pairs(self):
        out = verify.findings_document(self.doc, self.facts, self.records, [])
        self.assertEqual(sum(out["state_counts"].values()), out["pairs"])
        self.assertEqual(out["state_counts"],
                         {"agree": 1, "differ": 0, "displaced": 0,
                          "unmeasurable": 1, "not_attempted": 2})

    def test_header_states_the_assumptions(self):
        out = verify.findings_document(self.doc, self.facts, self.records, [])
        self.assertEqual(out["contract"], "inkverify/1")
        self.assertEqual((out["dpi"], out["threshold"]), (400, 200))
        self.assertEqual(out["polarity"], {"original": "auto",
                                           "claim": "dark-on-white"})
        self.assertEqual((out["floor"], out["floor_source"]),
                         (0, "G-0 not run"))
        self.assertTrue(out["inkdrill"]["revision"])
        json.dumps(out)

    def test_a_missing_record_is_refused(self):
        with self.assertRaises(ValueError):
            verify.findings_document(self.doc, self.facts, self.records[:3],
                                     [])

    def test_a_repeated_record_is_refused(self):
        recs = self.records[:3] + [self.records[0]]
        with self.assertRaises(ValueError):
            verify.findings_document(self.doc, self.facts, recs, [])


class Cli(unittest.TestCase):
    """G1: exit 2 means one line on stderr and no findings file."""

    def setUp(self):
        self.dir = scratch_dir(self)
        self.out = self.dir / "verify.findings.json"

    def run_cli(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["verify"] + argv)
        return rc, err.getvalue()

    def assert_refused(self, rc, err):
        self.assertEqual(rc, 2)
        lines = err.strip().splitlines()
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith("inkdrill verify: refused: "))
        self.assertIn("Fix:", lines[0])
        self.assertFalse(self.out.exists())

    def test_bad_arguments(self):
        self.assert_refused(*self.run_cli(["--pairs", "p.json"]))

    def test_malformed_pairs(self):
        p = self.dir / "p.json"
        p.write_text("{")
        self.assert_refused(*self.run_cli(
            ["--pairs", str(p), "--original", "x.pdf", "--candidates",
             "c.pdf", "-o", str(self.out)]))

    def test_output_directory_missing(self):
        rc, err = self.run_cli(["--pairs", "p.json", "--original", "x.pdf",
                                "--candidates", "c.pdf", "-o",
                                str(self.dir / "no" / "f.json")])
        self.assertEqual(rc, 2)
        self.assertIn("output directory", err)

    @unittest.skipUnless(HAVE_GS, "gs not on PATH")
    def test_valid_inputs_are_refused_until_the_judges_exist(self):
        orig = self.dir / "x.pdf"
        orig.write_bytes(make_pdf([{"media": LETTER, "content": BOX}]))
        cand = self.dir / "c.pdf"
        cand.write_bytes(make_pdf([{"media": (0, 0, 200, 100),
                                    "content": BOX}]))
        p = self.dir / "p.json"
        p.write_text(json.dumps(base_doc(verify.sha256_of(orig),
                                         verify.sha256_of(cand))))
        rc, err = self.run_cli(["--pairs", str(p), "--original", str(orig),
                                "--candidates", str(cand), "-o",
                                str(self.out)])
        self.assert_refused(rc, err)
        self.assertIn("not built", err)

    def test_an_existing_file_is_left_as_it_was(self):
        self.out.write_text("earlier run\n")
        p = self.dir / "p.json"
        p.write_text("{")
        rc, _ = self.run_cli(["--pairs", str(p), "--original", "x.pdf",
                              "--candidates", "c.pdf", "-o", str(self.out)])
        self.assertEqual(rc, 2)
        self.assertEqual(self.out.read_text(), "earlier run\n")


if __name__ == "__main__":
    unittest.main()

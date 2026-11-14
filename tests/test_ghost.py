"""`ghost`: Ghostscript to masks on a pipe (T-0146, inkverify/1 amendment 3).

The fixtures are PDFs written byte by byte here, so the geometry is known
exactly: one black box at (72, 162) pt top-left, 45 x 27 pt, on a
612 x 792 pt (US Letter) page. Every edge is a multiple of 9 pt, and
9 pt at 400 dpi is exactly 50 px, so the box covers exactly
[400, 650) x [900, 1050) px with no rounding left for gs to decide.

Skipped when `gs` is not on PATH: the default suite stays runnable
without it, and the judge refuses (exit 2) in that case anyway.
"""

import os
import pathlib
import shutil
import unittest

from inkdrill import ghost

HAVE_GS = shutil.which("gs") is not None

#: 72 pt from the left, 162 pt from the top of a 792 pt page: in PDF's
#: y-up frame the box's bottom edge is 792 - 162 - 27 = 603.
BOX_PT = (72, 162, 117, 189)
BOX = b"0 0 0 rg 72 603 45 27 re f"
LETTER = (0, 0, 612, 792)


def make_pdf(pages) -> bytes:
    """A minimal PDF. `pages`: dicts with media, content, and optional
    crop and rotate. Offsets in the xref are computed, so gs reads it
    without repair."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None]
    kids = []
    for p in pages:
        pid = len(objs) + 1
        kids.append(pid)
        extra = b""
        if p.get("crop"):
            extra += b" /CropBox [%d %d %d %d]" % tuple(p["crop"])
        if p.get("rotate"):
            extra += b" /Rotate %d" % p["rotate"]
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [%d %d %d %d]"
                    % tuple(p["media"]) + extra
                    + b" /Contents %d 0 R >>" % (pid + 1))
        c = p.get("content", b"")
        objs.append(b"<< /Length %d >>\nstream\n" % len(c) + c
                    + b"\nendstream")
    objs[1] = (b"<< /Type /Pages /Kids [%s] /Count %d >>"
               % (b" ".join(b"%d 0 R" % k for k in kids), len(kids)))
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs):
        offs.append(len(out))
        out += b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    return out


def ink_box(m):
    """Half-open ink extent [x0, x1) x [y0, y1) of a mask, or None."""
    xs, ys = [], []
    for y in range(m.height):
        row = m.data[y * m.width:(y + 1) * m.width]
        i = row.find(b"\xff")
        if i >= 0:
            ys.append(y)
            xs += [i, row.rfind(b"\xff")]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def scratch_dir(test) -> pathlib.Path:
    """A per-test folder under the project's work/run-files (house rule:
    no /tmp). Removed after the test."""
    root = pathlib.Path(__file__).resolve().parents[1] / "work" / \
        "run-files" / "tests" / test.id()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    test.addCleanup(shutil.rmtree, root, True)
    return root


@unittest.skipUnless(HAVE_GS, "gs not on PATH")
class GhostRender(unittest.TestCase):

    def setUp(self):
        self.dir = scratch_dir(self)

    def pdf(self, name, pages):
        p = self.dir / name
        p.write_bytes(make_pdf(pages))
        return p

    def test_page_count(self):
        p = self.pdf("two.pdf", [{"media": LETTER}, {"media": LETTER,
                                                     "content": BOX}])
        self.assertEqual(ghost.page_count(p), 2)

    def test_box_lands_on_exact_pixel_boundaries(self):
        p = self.pdf("plain.pdf", [{"media": LETTER, "content": BOX}])
        (m,) = list(ghost.render_masks(p))
        self.assertEqual((m.width, m.height), (3400, 4400))
        self.assertEqual(ink_box(m), (400, 900, 650, 1050))
        self.assertEqual(m.ink_count, 250 * 150)

    def test_cropbox_is_the_frame(self):
        # G2. Crop away 36 pt on the left and 72 pt on top: the box is at
        # (36, 90) pt in the crop frame = (200, 500) px. Rendering the
        # MediaBox (no -dUseCropBox) would put it at (400, 900).
        p = self.pdf("crop.pdf", [{"media": LETTER,
                                   "crop": (36, 0, 612, 720),
                                   "content": BOX}])
        (m,) = list(ghost.render_masks(p))
        self.assertEqual((m.width, m.height), (3200, 4000))
        self.assertEqual(ink_box(m), (200, 500, 450, 650))
        self.assertNotEqual(ink_box(m)[:2], (400, 900))

    def test_rotate_is_applied(self):
        # G3: /Rotate 90 renders the visible, rotated page
        p = self.pdf("rot.pdf", [{"media": LETTER, "rotate": 90,
                                  "content": BOX}])
        (m,) = list(ghost.render_masks(p))
        self.assertEqual((m.width, m.height), (4400, 3400))
        self.assertEqual(ink_box(m), (3350, 400, 3500, 650))

    def test_first_last_selects_pages(self):
        # three pages, so stopping at `last` is visible: without
        # -dLastPage, page 3 would come back too
        p = self.pdf("three.pdf", [{"media": LETTER}, {"media": LETTER,
                                                       "content": BOX},
                                   {"media": LETTER}])
        masks = list(ghost.render_masks(p, first=2, last=2))
        self.assertEqual(len(masks), 1)
        self.assertEqual(masks[0].ink_count, 250 * 150)
        every = list(ghost.render_masks(p))
        self.assertEqual([m.ink_count for m in every], [0, 250 * 150, 0])

    def test_asking_past_the_end_raises_not_a_short_iterator(self):
        # G6: one page asked beyond the end is an error, never fewer pages
        p = self.pdf("two.pdf", [{"media": LETTER}, {"media": LETTER}])
        with self.assertRaises(ghost.GhostError):
            list(ghost.render_masks(p, first=2, last=3))

    def test_not_a_pdf_raises(self):
        # gs 10.08 exits 0 here and prints "0" after its error ON STDOUT;
        # the count must not be read as a zero-page PDF
        p = self.dir / "junk.pdf"
        p.write_bytes(b"this is not a PDF\n")
        with self.assertRaises(ghost.GhostError) as cm:
            ghost.page_count(p)
        # the error carries gs's own first line, not the placeholder
        said = str(cm.exception).split(": ", 1)[1]
        self.assertTrue(said.strip())
        self.assertNotIn("(no message)", said)
        with self.assertRaises(ghost.GhostError):
            list(ghost.render_masks(p))

    def test_a_corrupt_stream_from_a_clean_exit_raises(self):
        # G6: gs exits 0 but the pipe is not PGM -> GhostError, not the
        # reader's exception and not a short iterator
        from unittest import mock
        from inkdrill.pnmio import CorruptPNM
        p = self.pdf("plain.pdf", [{"media": LETTER, "content": BOX}])

        def broken(fh, **kw):
            fh.read()
            raise CorruptPNM("truncated")
            yield                                   # a generator
        with mock.patch.object(ghost, "stream_masks", broken):
            with self.assertRaises(ghost.GhostError) as cm:
                list(ghost.render_masks(p))
        self.assertIn("not a PGM stream", str(cm.exception))

    def test_the_accepting_side_of_the_count_check(self):
        p = self.pdf("one.pdf", [{"media": LETTER}])
        self.assertEqual(ghost.page_count(p), 1)

    def test_no_file_is_written(self):
        # G1: the render is a pipe; neither the PDF's folder nor TMPDIR
        # gains a file
        p = self.pdf("plain.pdf", [{"media": LETTER, "content": BOX}])
        tmp = pathlib.Path(os.environ.get("TMPDIR") or self.dir)
        before = (set(self.dir.iterdir()), set(tmp.iterdir()))
        list(ghost.render_masks(p))
        ghost.page_count(p)
        self.assertEqual((set(self.dir.iterdir()), set(tmp.iterdir())),
                         before)


if __name__ == "__main__":
    unittest.main()

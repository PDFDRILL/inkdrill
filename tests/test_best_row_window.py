"""`mathstruct.best_row_window` held directly (T-0146).

The four `locate` CLI tests reach this body, but a scratch mutation run
over it on dc76d77 left three mutants alive: the strict tie-break
(`<` -> `<=`), the n-2..n+2 size range (-> n only), and the box's lower
edge (`max(y1)` -> `min(y1)`). A fixture whose members all start at
y = 0 then let a fourth live (`min(y0)` -> `max(y0)`), hence the
ascender case. Each test below is named for the line it holds.

Fixture numbers are derived, not chosen: 10 pt type at 400 dpi has a
Computer Modern x-height of ~4.3 pt = 24 px, a letter gap of ~0.5 pt =
3 px, and 12 pt leading = 67 px from one line's top to the next. Boxes
are inclusive pixel boxes, as `cmd_locate`'s cached components are.
"""

import collections
import unittest

from inkdrill.mathstruct import best_row_window

C = collections.namedtuple("C", "id x0 y0 x1 y1 area holes")

XH = 24          # x-height, px
GAP = 3          # letter gap, px
LEAD = 67        # line pitch, px


def letter(i, x, top, holes=0, h=XH):
    # area of a ring-ish letter: well under the 0.8 fill `is_rule` needs,
    # so no letter here is mistaken for a rule
    return C(i, x, top, x + XH - 1, top + h - 1, (XH * h) // 2, holes)


def line(first_id, top, holes_seq):
    x, out = 0, []
    for j, h in enumerate(holes_seq):
        out.append(letter(first_id + j, x, top, h))
        x += XH + GAP
    return out


class BestRowWindow(unittest.TestCase):

    def test_exact_window_found_with_distance_zero(self):
        comps = line(0, 0, [0, 1, 0])
        d, box, k = best_row_window(comps, (3, 1, 0, 0, 0))
        self.assertEqual((d, k), (0, 3))
        self.assertEqual(box, (0, 0, 2 * (XH + GAP) + XH - 1, XH - 1))

    def test_strict_tie_break_keeps_the_first_row(self):
        # `d < best[0]`: two rows hold the same exact window, and the
        # upper one -- the first `rows()` yields -- is the answer. With
        # `<=` the lower row would replace it.
        comps = line(0, 0, [1, 0]) + line(10, LEAD, [1, 0])
        d, box, k = best_row_window(comps, (2, 1, 0, 0, 0))
        self.assertEqual(d, 0)
        self.assertEqual(box[1], 0)
        self.assertNotEqual(box[1], LEAD)

    def test_window_two_smaller_than_the_candidate_is_searched(self):
        # n-2..n+2: the page row holds 2 components, the candidate 4.
        # With the range cut to n alone no window exists and the caller
        # would report NO MATCH for a row that is in range.
        comps = line(0, 0, [0, 0])
        got = best_row_window(comps, (4, 0, 0, 0, 0))
        self.assertIsNotNone(got)
        d, _, k = got
        self.assertEqual((k, d), (2, 2))

    def test_window_three_smaller_is_not_searched(self):
        # the other side of the range: n-3 is out, so None (NO MATCH)
        comps = line(0, 0, [0, 0])
        self.assertIsNone(best_row_window(comps, (5, 0, 0, 0, 0)))

    def test_box_lower_edge_is_the_lowest_member(self):
        # a descender (p, q: ~2 pt = 11 px below the baseline) makes one
        # member taller; the window's y1 is the max over members
        a = letter(0, 0, 0)
        b = letter(1, XH + GAP, 0, h=XH + 11)
        _, box, _ = best_row_window([a, b], (2, 0, 0, 0, 0))
        self.assertEqual(box[3], XH + 11 - 1)
        self.assertEqual(box[1], 0)

    def test_box_upper_edge_is_the_highest_member(self):
        # an ascender (b, d: ~2 pt = 11 px above the x-height) starts
        # higher than its neighbour; the window's y0 is the min
        a = letter(0, 0, 11)
        b = letter(1, XH + GAP, 0, h=XH + 11)
        _, box, _ = best_row_window([a, b], (2, 0, 0, 0, 0))
        self.assertEqual(box[1], 0)
        self.assertEqual(box[3], 11 + XH - 1)


if __name__ == "__main__":
    unittest.main()

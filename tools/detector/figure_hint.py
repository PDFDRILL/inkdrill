#!/usr/bin/env python3
"""figure_hint.py <dly320x256.onnx> <dly640x512.onnx> <doc.pdf> [first [last]] -- the detector cascade's figure boxes
for `inkcmp lines --hint FILE --figures hint` (CONTRACT unit 7; opt-in: it FAILS the held-out figure gate by one
swallowed line on seed 9007 -- 42 of 51 whole, 1 swallowed -- and is not the default). Per page: the 320 x 256 graph;
where it finds a figure, the 640 x 512 graph, whose figure boxes are the page's (board mean 0.486 CPU s/page, the 640
pass on 4 of 30 pages). Prints `n figure x0 y0 x1 y1` (fractions of the page), n counted from 1 at `first`: the page's
position in the PGM stream inkcmp lines reads when the same range is rendered."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout_hint as L

FIGURE = 3


def figure_boxes(s320, s640, img):
    if not any(c == FIGURE for c, *_ in L.run_graph(s320, img, 320, 256)):
        return []
    return [tuple(b) for c, *b in L.run_graph(s640, img, 640, 512) if c == FIGURE]


if __name__ == "__main__":
    m320, m640, pdf = sys.argv[1:4]
    first = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    last = int(sys.argv[5]) if len(sys.argv) > 5 else first
    s320, s640 = L.session(m320), L.session(m640)
    for p in range(first, last + 1):
        for x0, y0, x1, y1 in figure_boxes(s320, s640, L.page_image(pdf, p)):
            print(f"{p - first + 1} figure {x0:.5f} {y0:.5f} {x1:.5f} {y1:.5f}")

"""inkcmp lines step 2a: side stamps (CONTRACT unit 7; T-0154). Fixtures are synthetic pages whose dimensions come
from the two measured stamps at 250 dpi (2312.11532 p1, 2209.02970 p1): a lane 58 px wide at x 64, about 1200 px
tall, grey 127 (lighter than the page's ink threshold), ink on 74.5-78.7% of its rows, the densest column 46 px from
the lane's left edge (the baseline side); body text 120 px further in; and the measured negative, a column of line
numbers with ink on 22.5% of its rows. Each class fires: a stamp read bottom-to-top (90), one read top-to-bottom
(270, the mirrored lane), a dark stamp (erased from the ink, no line reaches into it), and no stamp for line numbers
or a page without one."""
from inkcmp_util import INKCMP, pgm, run

W, H = 2125, 2750                                  # the measured page at 250 dpi
BODY_X0 = 185


def page(lane=None, grey=127, rot=90, right=False):
    px = bytearray(b"\xff" * (W * H))

    def block(x0, y0, x1, y1, g):
        for y in range(y0, y1):
            px[y * W + x0:y * W + x1] = bytes([g]) * (x1 - x0)
    # body text: 20 lines of 16 x 22 px glyphs, 4 px apart, 38 px pitch (the column's ink, its m and lh)
    for k in range(20):
        y = 300 + 38 * k
        for x in range(BODY_X0, 1000, 20):
            block(x, y, x + 16, y + 22, 0)
    if lane == "stamp":                       # rotated glyphs: 20 px along y, 6 px apart (77% of rows)
        for i, y in enumerate(range(250, 1450, 26)):
            if rot == 90:                     # baseline right: an x-height band at the right, ascenders to the left
                xs = (90, 112) if i % 3 else (64, 112)
            else:                             # top-to-bottom: the mirror image
                xs = (74, 96) if i % 3 else (74, 122)
            if right:
                xs = (W - xs[1], W - xs[0])
            block(xs[0], y, xs[1], y + 20, grey)
    elif lane == "numbers":                   # line numbers: 12 px digits at the body's pitch (about 25% of rows)
        for k in range(30):
            y = 250 + 40 * k
            block(120, y, 140, y + 10, 0)
    return pgm(W, H, px)


def lines(data):
    r = run([INKCMP, "lines"], data)
    assert r.returncode == 0, r.stderr
    recs = []
    for ln in r.stdout.decode().splitlines()[1:-1]:
        f = ln.split()
        recs.append((f[0], tuple(int(v) for v in f[1:5]), int(f[f.index("rot") + 1]) if "rot" in f else 0))
    return recs


def stamps(recs):
    return [r for r in recs if r[2]]


def test_S1_grey_stamp_bottom_to_top():
    recs = lines(page("stamp"))
    st = stamps(recs)
    assert len(st) == 1 and st[0][0] == "page_info" and st[0][2] == 90, st
    x0, y0, x1, y1 = st[0][1]
    assert (x0, y0, x1, y1) == (64, 250, 112, 1466), st          # the lane's ink, exactly
    return 1


def test_S2_top_to_bottom_is_270():
    st = stamps(lines(page("stamp", rot=270)))
    assert len(st) == 1 and st[0][2] == 270, st
    return 1


def test_S3_right_edge():
    st = stamps(lines(page("stamp", right=True)))
    assert len(st) == 1 and st[0][1][0] >= W - 112 and st[0][2] == 270, st     # mirrored in x: the baseline is left
    return 1


def test_S4_dark_stamp_is_erased_from_the_lines():
    recs = lines(page("stamp", grey=0))
    st = stamps(recs)
    assert len(st) == 1, st
    s = st[0][1]
    reach = [r for r in recs if not r[2] and r[1][0] < s[2] and r[1][2] > s[0] and r[1][1] < s[3] and r[1][3] > s[1]]
    assert not reach, reach
    assert all(r[1][0] >= BODY_X0 - 2 for r in recs if r[0] == "text"), [r for r in recs if r[0] == "text"][:3]
    return 1


def test_S5_line_numbers_and_plain_pages_have_none():
    assert not stamps(lines(page("numbers")))
    assert not stamps(lines(page(None)))
    return 2


if __name__ == "__main__":
    for name, f in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        print("test_inkcmp_stamp %s: ok (%d cases)" % (name[5:], f()))

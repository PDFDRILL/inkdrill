"""inkcmp lines --hint (CONTRACT unit 7: author blocks typed as maths). A centred line between body lines is a display
candidate (indented on both sides of the body text near it); a layout detector's text region makes it text, a formula
region over it keeps it maths, no hint leaves it maths. Fixture dimensions from the measured page (2125 x 2750 at
250 dpi, glyphs 16 x 22 px, line pitch 38 px, as test_inkcmp_stamp.py); a refusal is tested on both sides."""
import os
import tempfile
from inkcmp_util import INKCMP, pgm, run

W, H = 2125, 2750
CENTRED = (800, 1325)                       # the centred line's x-range: indented >= 1.5 lh on both sides


def page():
    px = bytearray(b"\xff" * (W * H))

    def line(y, x0, x1):
        for x in range(x0, x1, 20):
            for yy in range(y, y + 22):
                px[yy * W + x:yy * W + x + 16] = b"\x00" * 16
    for k in range(10):                     # body text above, the centred line, body text below
        line(400 + 38 * k, 185, 1945)
    line(400 + 38 * 11, *CENTRED)
    for k in range(13, 23):
        line(400 + 38 * k, 185, 1945)
    return pgm(W, H, px)


def centred_type(hint_text=None):
    args = [INKCMP, "lines"]
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
        if hint_text is not None:
            hf = os.path.join(d, "hint.txt")
            open(hf, "w").write(hint_text)
            args += ["--hint", hf]
        r = run(args, page())
    if r.returncode:
        return None, r
    y = 400 + 38 * 11
    recs = [ln.split() for ln in r.stdout.decode().splitlines()[1:-1]]
    hit = [f[0] for f in recs if int(f[2]) <= y + 11 <= int(f[4]) and int(f[1]) <= 1000 <= int(f[3])]
    return hit, r


TEXT = "1 text %.5f %.5f %.5f %.5f\n" % (700 / W, 805 / H, 1500 / W, 855 / H)        # the line: y 818-840
FORMULA = "1 formula %.5f %.5f %.5f %.5f\n" % (780 / W, 810 / H, 1345 / W, 850 / H)


def test_H1_no_hint_the_centred_line_is_a_display():
    hit, _ = centred_type()
    assert hit == ["math"], hit                      # the defect the hint exists for: the branch must fire without it
    return 1


def test_H2_text_hint_makes_it_text():
    hit, _ = centred_type(TEXT)
    assert hit == ["text"], hit
    return 1


def test_H3_formula_hint_keeps_it_maths():
    hit, _ = centred_type(TEXT + FORMULA)
    assert hit == ["math"], hit
    return 1


def test_H4_hint_for_another_page_changes_nothing():
    hit, _ = centred_type(TEXT.replace("1 text", "2 text", 1))
    assert hit == ["math"], hit
    return 1


def test_H5_refusals_and_acceptance():
    for bad in ("1 figure 0 0 1 1\n", "1 text 0 0 1\n", "one text 0 0 1 1\n"):
        hit, r = centred_type(bad)
        assert hit is None and r.returncode == 2, (bad, r.returncode, r.stderr)
    hit, r = centred_type("")                         # an empty file is accepted: no hints
    assert r.returncode == 0 and hit == ["math"], hit
    return 4


if __name__ == "__main__":
    for name, f in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        print("test_inkcmp_hint %s: ok (%d cases)" % (name[5:], f()))

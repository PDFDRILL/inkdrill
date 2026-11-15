#!/usr/bin/env python3
"""inkcmp unit 2 (inkcmp/CONTRACT.md S1): `inkcmp blocks` prints what gd3-adapter | gd3-blocks
prints. Standard library only. The paragraph scan itself is GLYPHDRILL3's (tests/test_blocks.py);
here only the identity of the two programs is checked, plus that the pages are not trivial."""
import random
from inkcmp_util import ADAPTER, BLOCKS, INKCMP, Canvas, pgm, random_page, run


def reference(data, t, opts):
    a = run([ADAPTER, '--pgm', t], data); assert a.returncode == 0, a.stderr
    b = run([BLOCKS] + opts, a.stdout); assert b.returncode == 0, b.stderr
    return b.stdout


def text_pages():
    rng = random.Random(11)
    c = Canvas(600, 800); c.text(rng, 60, 60, 540, 740, 12); yield 'one column', c.data(), 1
    c = Canvas(700, 800); c.text(rng, 50, 60, 320, 740, 12); c.text(rng, 380, 60, 650, 740, 12); yield 'two columns', c.data(), 2
    c = Canvas(700, 900); c.text(rng, 50, 50, 650, 200, 16); c.text(rng, 50, 280, 320, 850, 10); c.text(rng, 380, 280, 650, 850, 10)
    c.box(50, 240, 650, 243); yield 'title, rule, two columns', c.data(), 3


def test_S1_text_pages():
    n = 0
    for name, (w, h, px), min_paras in text_pages():
        data = pgm(w, h, px)
        for opts in ([], ['--gx', '30'], ['--gy', '4'], ['--gx', '8', '--gy', '40']):
            want = reference(data, 154, opts)
            r = run([INKCMP, 'blocks'] + opts, data)
            assert r.returncode == 0, r.stderr
            assert r.stdout == want, (name, opts)
            if not opts: assert want.count(b'paragraph ') >= min_paras, (name, want.count(b'paragraph '))
            n += 1
    return n


def test_S1_random_pages():
    rng = random.Random(12); n = 0
    for w, h in ((1, 1), (9, 9), (60, 40), (150, 90)):
        for d in (0.0, 0.02, 0.3, 1.0):
            px = random_page(rng, w, h, d); data = pgm(w, h, px)
            for t in (128, 154):
                r = run([INKCMP, 'blocks', '--threshold', t], data)
                assert r.returncode == 0, r.stderr
                assert r.stdout == reference(data, t, []), (w, h, d, t)
                n += 1
    return n


def test_S1_usage():
    data = pgm(2, 2, bytes(4))
    for opts in (['--gx'], ['--gx', '-1'], ['--gy', '1000001'], ['--lh', '5']):
        r = run([INKCMP, 'blocks'] + opts, data)
        assert r.returncode == 2 and r.stdout == b'', (opts, r.returncode)
    return 4


if __name__ == '__main__':
    for name, f in sorted((k, v) for k, v in globals().items() if k.startswith('test_')):
        print('test_inkcmp_blocks %s: ok (%d cases)' % (name[5:], f()))

#!/usr/bin/env python3
"""inkcmp unit 3 (inkcmp/CONTRACT.md W1-W6): maximal white rectangles, content box, margins and
separators. Standard library only.

Two independent references: gd3-white (byte identity, W1) and a brute-force oracle written from
the definitions in the contract (every rectangle of a small page is tried; W1-W4). W5 is checked
on drawn pages whose gutters are known from the drawing."""
import math
import random
from inkcmp_util import ADAPTER, INKCMP, WHITE, Canvas, from_rows, pgm, random_page, run

T = 154


def oracle_maximal(w, h, px):
    ink = [[1 if px[y * w + x] < T else 0 for x in range(w)] for y in range(h)]
    S = [[0] * (w + 1) for _ in range(h + 1)]
    for y in range(h):
        for x in range(w):
            S[y + 1][x + 1] = S[y][x + 1] + S[y + 1][x] - S[y][x] + ink[y][x]
    n = lambda x0, y0, x1, y1: S[y1][x1] - S[y0][x1] - S[y1][x0] + S[y0][x0]
    out = set()
    for y0 in range(h):
        for y1 in range(y0 + 1, h + 1):
            for x0 in range(w):
                for x1 in range(x0 + 1, w + 1):
                    if n(x0, y0, x1, y1): break            # wider ones hold the same ink
                    if x0 > 0 and not n(x0 - 1, y0, x0, y1): continue
                    if x1 < w and not n(x1, y0, x1 + 1, y1): continue
                    if y0 > 0 and not n(x0, y0 - 1, x1, y0): continue
                    if y1 < h and not n(x0, y1, x1, y1 + 1): continue
                    out.add((x0, y0, x1, y1))
    return out


def oracle_space(w, h, px, lh, vsep_w=1.0, vsep_h=4.0, hsep_w=4.0, hsep_h=1.0, overlap=0.2, max_sep=64):
    inkxy = [(x, y) for y in range(h) for x in range(w) if px[y * w + x] < T]
    rects = oracle_maximal(w, h, px)
    if not inkxy: return dict(blank=True, white=len(rects))
    cx0 = min(x for x, _ in inkxy); cx1 = max(x for x, _ in inkxy) + 1
    cy0 = min(y for _, y in inkxy); cy1 = max(y for _, y in inkxy) + 1
    q = lambda f: max(1, math.ceil(f * lh))
    def cls(r):
        x0, y0, x1, y1 = r
        if x0 >= x1 or y0 >= y1: return 0
        rw, rh = x1 - x0, y1 - y0
        if rh >= rw: return 1 if rw >= q(vsep_w) and rh >= q(vsep_h) and x0 > cx0 and x1 < cx1 else 0
        return 2 if rw >= q(hsep_w) and rh >= q(hsep_h) and y0 > cy0 and y1 < cy1 else 0
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    cand = [(max(x0, cx0), max(y0, cy0), min(x1, cx1), min(y1, cy1)) for x0, y0, x1, y1 in rects]
    cand = sorted((r for r in cand if cls(r)), key=lambda r: (-area(r), r[1], r[0], r[3], r[2]))
    kept = []
    for c in cand:
        if len(kept) >= max_sep: break
        again = True
        while again and cls(c):
            again = False
            for k in kept:
                ix0, iy0, ix1, iy1 = max(c[0], k[0]), max(c[1], k[1]), min(c[2], k[2]), min(c[3], k[3])
                if ix0 >= ix1 or iy0 >= iy1: continue
                if k[0] <= c[0] and k[2] >= c[2]:          # k spans the width: cut rows
                    up, low = iy0 - c[1], c[3] - iy1
                    c = (c[0], c[1], c[2], c[1] + up) if up >= low else (c[0], c[3] - low, c[2], c[3])
                    again = True; break
                if k[1] <= c[1] and k[3] >= c[3]:          # k spans the height: cut columns
                    left, right = ix0 - c[0], c[2] - ix1
                    c = (c[0], c[1], c[0] + left, c[3]) if left >= right else (c[2] - right, c[1], c[2], c[3])
                    again = True; break
                if (ix1 - ix0) * (iy1 - iy0) > overlap * area(c): c = (0, 0, 0, 0); break
        if cls(c): kept.append(c)
    v = sorted((r for r in kept if cls(r) == 1), key=lambda r: (r[0], r[1], r[2], r[3]))
    hz = sorted((r for r in kept if cls(r) == 2), key=lambda r: (r[1], r[0], r[3], r[2]))
    return dict(blank=False, white=len(rects), content=(cx0, cy0, cx1, cy1), margin=(cy0, h - cy1, cx0, w - cx1), vsep=v, hsep=hz)


def space(data, opts=()):
    r = run([INKCMP, 'space'] + [str(o) for o in opts], data)
    assert r.returncode == 0, r.stderr
    L = [l.split() for l in r.stdout.decode().splitlines()]
    assert L[0][0] == 'INKSPACE1' and L[-1][0] == 'end'
    out = dict(lh=int(L[0][4]), white=int(L[0][6]), blank=False, vsep=[], hsep=[])
    for f in L[1:-1]:
        if f[0] == 'blank': out['blank'] = True
        elif f[0] in ('content', 'margin'): out[f[0]] = tuple(map(int, f[1:]))
        else: out[f[0]].append(tuple(map(int, f[1:])))
    assert (int(L[-1][1]), int(L[-1][2])) == (len(out['vsep']), len(out['hsep']))
    return out


def small_pages():
    rng = random.Random(31)
    for w, h in ((1, 1), (3, 2), (8, 6), (13, 9), (16, 12)):
        for d in (0.0, 0.03, 0.08, 0.2, 0.6, 1.0):
            for _ in range(4): yield w, h, random_page(rng, w, h, d)
    # structured small pages: two columns, a frame, an L shape
    yield from_rows(['##....##', '##....##', '##....##', '##....##', '##....##'])
    yield from_rows(['########', '#......#', '#..##..#', '#......#', '########'])
    yield from_rows(['#.......', '#.......', '#.......', '#.......', '########'])


def test_W1_same_bytes_as_gd3_white():
    rng = random.Random(30); n = 0
    pages = list(small_pages()) + [(w, h, random_page(rng, w, h, d)) for w, h in ((120, 80), (301, 97)) for d in (0.001, 0.02, 0.3)]
    c = Canvas(500, 400); c.text(rng, 30, 30, 230, 370, 10); c.text(rng, 270, 30, 470, 370, 10); pages.append(c.data())
    for w, h, px in pages:
        data = pgm(w, h, px)
        a = run([ADAPTER, '--pgm', T], data); g = run([WHITE], a.stdout); assert g.returncode == 0, g.stderr
        r = run([INKCMP, 'white'], data); assert r.returncode == 0, r.stderr
        assert r.stdout == g.stdout, (w, h)
        n += 1
    return n


def test_W1_brute_force_set():
    n = 0
    for w, h, px in small_pages():
        r = run([INKCMP, 'white'], pgm(w, h, px)); assert r.returncode == 0
        L = r.stdout.decode().splitlines()
        got = [tuple(map(int, l.split())) for l in L[1:-1]]
        want = oracle_maximal(w, h, px)
        assert len(got) == len(set(got)), 'a rectangle twice'
        assert set(got) == want, (w, h, sorted(set(got) ^ want)[:5])
        assert L[-1] == 'end %d' % len(want)
        n += 1
    return n


def test_W2_W3_W4_space_equals_definition():
    n = 0; nonempty = 0
    variants = [dict(), dict(vsep_w=0.5, vsep_h=1.5, hsep_w=1.5, hsep_h=0.5), dict(overlap=0.0), dict(overlap=1.0), dict(max_sep=1),
                dict(vsep_w=0, vsep_h=0, hsep_w=0, hsep_h=0)]
    for w, h, px in small_pages():
        for lh in (1, 2, 3):
            for kw in variants:
                opts = ['--lh', lh] + [o for k, v in kw.items() for o in ('--' + k.replace('_', '-'), v)]
                got = space(pgm(w, h, px), opts); want = oracle_space(w, h, px, lh, **kw)
                assert got['lh'] == lh
                for k, v in want.items(): assert got.get(k) == v, (w, h, lh, kw, k, got.get(k), v)
                nonempty += bool(want.get('vsep')) + bool(want.get('hsep'))
                n += 1
    assert nonempty > 100, ('the pages hardly have separators: the test would prove little', nonempty)
    return n


def check_W4(w, h, px, s, lh, vw=1.0, vh=4.0, hw=4.0, hh=1.0):
    cx0, cy0, cx1, cy1 = s['content']
    for kind in ('vsep', 'hsep'):
        for x0, y0, x1, y1 in s[kind]:
            assert cx0 <= x0 < x1 <= cx1 and cy0 <= y0 < y1 <= cy1, (kind, 'outside the content box')
            assert all(px[y * w + x] >= T for y in range(y0, y1) for x in range(x0, x1)), (kind, 'ink inside', (x0, y0, x1, y1))
            if kind == 'vsep': assert y1 - y0 >= x1 - x0 >= math.ceil(vw * lh) and y1 - y0 >= math.ceil(vh * lh) and x0 > cx0 and x1 < cx1
            else: assert x1 - x0 > y1 - y0 >= math.ceil(hh * lh) and x1 - x0 >= math.ceil(hw * lh) and y0 > cy0 and y1 < cy1


def test_W3_blank_page():
    for w, h in ((1, 1), (40, 30)):
        s = space(pgm(w, h, b'\xff' * (w * h)), ['--lh', 5])
        assert s['blank'] and s['white'] == 1 and s['vsep'] == [] and s['hsep'] == [] and 'content' not in s
        s = space(pgm(w, h, b'\xff' * (w * h)))         # the line height from layout/blocks on a blank page
        assert s['blank']
    return 4


def test_W5_two_columns():
    n = 0
    for seed, lh, gutter in ((1, 12, 60), (2, 10, 24), (3, 16, 16), (4, 20, 200)):
        rng = random.Random(seed)
        xa0, xa1 = 50, 320; xb0 = xa1 + gutter; xb1 = xb0 + 270
        c = Canvas(xb1 + 50, 800); ya = c.text(rng, xa0, 60, xa1, 740, lh); yb = c.text(rng, xb0, 60, xb1, 740, lh); assert ya == yb
        w, h, px = c.data()
        for opts in (['--lh', lh], []):                 # W6: with the line height given; S2: from layout/blocks
            s = space(pgm(w, h, px), opts)
            assert s['content'] == (xa0, 60, xb1, ya) and s['margin'] == (60, h - ya, xa0, w - xb1), s
            assert s['vsep'] == [(xa1, 60, xb0, ya)], (seed, opts, s['lh'], s['vsep'])
            assert s['hsep'] == [], s['hsep']           # line gaps (0.5 lh) are below hsep_h
            check_W4(w, h, px, s, s['lh'])
            n += 1
    return n


def test_W5_gutter_below_the_width_is_not_reported():
    rng = random.Random(5); lh = 12
    c = Canvas(700, 800); c.text(rng, 50, 60, 320, 740, lh); c.text(rng, 320 + 8, 60, 650, 740, lh)   # 8 px < 1.0 x lh
    w, h, px = c.data()
    assert space(pgm(w, h, px), ['--lh', lh])['vsep'] == []
    # At 0.5 x lh (= the word gap of the drawn text) the gutter is found, and so are "rivers": word gaps that
    # line up over >= 4 lines. They satisfy the definition; the check here is that the gutter is among the
    # results over all rows and every river is much shorter. (Seen 2026-11-14: two rivers of 82 and 60 rows.)
    s = space(pgm(w, h, px), ['--lh', lh, '--vsep-w', 0.5])
    check_W4(w, h, px, s, lh, vw=0.5)
    cy0, cy1 = s['content'][1], s['content'][3]
    assert (320, cy0, 328, cy1) in s['vsep'], s['vsep']
    assert all(2 * (r[3] - r[1]) < cy1 - cy0 for r in s['vsep'] if r != (320, cy0, 328, cy1)), s['vsep']
    return 2


def test_W5_three_columns_title_and_rule():
    rng = random.Random(6); lh = 10
    c = Canvas(900, 1000)
    t_bot = c.text(rng, 50, 50, 850, 120, 20)
    c.box(50, 160, 850, 163)
    cols = [(50, 290), (330, 570), (610, 850)]
    bots = [c.text(rng, a, 220, b, 950, lh) for a, b in cols]; assert len(set(bots)) == 1
    w, h, px = c.data()
    s = space(pgm(w, h, px), ['--lh', lh])
    check_W4(w, h, px, s, lh)
    assert s['vsep'] == [(290, 220, 330, bots[0]), (570, 220, 610, bots[0])], s['vsep']
    assert (50, t_bot, 850, 160) in s['hsep'] and (50, 163, 850, 220) in s['hsep'], s['hsep']
    return 1


def test_W5_ragged_right_column_edge():
    rng = random.Random(7); lh = 12
    c = Canvas(700, 800); bot = c.text(rng, 50, 60, 320, 740, lh, ragged=0.3); c.text(rng, 380, 60, 650, 740, lh)
    w, h, px = c.data()
    s = space(pgm(w, h, px), ['--lh', lh])
    check_W4(w, h, px, s, lh)
    full = [r for r in s['vsep'] if r[1] == 60 and r[3] == bot and r[2] == 380]
    assert len(full) == 1 and full[0][0] <= 320, s['vsep']      # the gutter over all rows, at least its clean width
    return 1


if __name__ == '__main__':
    for name, f in sorted((k, v) for k, v in globals().items() if k.startswith('test_')):
        print('test_inkcmp_white %s: ok (%d cases)' % (name[5:], f()))

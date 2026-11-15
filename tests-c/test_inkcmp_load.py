#!/usr/bin/env python3
"""inkcmp unit 1 (inkcmp/CONTRACT.md L1-L4): PGM -> ink runs. Standard library only.

L1 against an oracle that takes the runs straight from the pixels; L2 byte for byte against
gd3-adapter; L3 the refusals; L4 two pages in one process (build/inkcmp-selftest-two)."""
import os
import random
import subprocess
from inkcmp_util import ADAPTER, INKCMP, ROOT, parse_rle, pgm, random_page, run, runs_of

TWO = ROOT / 'build' / 'inkcmp-selftest-two'


def pages():
    rng = random.Random(20261114)
    sizes = [(1, 1), (1, 7), (7, 1), (2, 2), (17, 5), (64, 48), (97, 31), (200, 120)]
    for w, h in sizes:
        for d in (0.0, 0.05, 0.5, 0.95, 1.0):
            yield w, h, random_page(rng, w, h, d)
    # grey values around the thresholds
    yield 16, 4, bytes(rng.choice([0, 1, 127, 128, 153, 154, 155, 254, 255]) for _ in range(64))


def test_L1_runs_from_pixels():
    n = 0
    for w, h, px in pages():
        for t in (1, 128, 154, 256):
            r = run([INKCMP, 'rle', '--threshold', t], pgm(w, h, px))
            assert r.returncode == 0, r.stderr
            pw, ph, rows = parse_rle(r.stdout)
            assert (pw, ph) == (w, h)
            assert rows == runs_of(w, h, px, t), (w, h, t)
            for row in rows:
                assert all(a < b <= w for a, b in row)
                assert all(row[i][1] < row[i + 1][0] for i in range(len(row) - 1)), 'adjacent or unsorted runs'
            n += 1
    return n


def test_L2_same_bytes_as_adapter():
    n = 0
    for w, h, px in pages():
        for t in (1, 128, 154, 256):
            for head in (None, b'P5\n# a comment\n%d %d # another\n255\n' % (w, h), b'P5 %d\t%d 255 ' % (w, h)):
                data = pgm(w, h, px, head)
                a = run([ADAPTER, '--pgm', t], data); b = run([INKCMP, 'rle', '--threshold', t], data)
                assert a.returncode == 0 and b.returncode == 0, (a.stderr, b.stderr)
                assert a.stdout == b.stdout, (w, h, t, head)
                n += 1
    return n


def test_L2_default_threshold_is_154():
    px = bytes([153, 154, 155, 0])
    a = run([ADAPTER, '--pgm', 154], pgm(4, 1, px)); b = run([INKCMP, 'rle'], pgm(4, 1, px))
    assert a.stdout == b.stdout and parse_rle(b.stdout)[2] == [[(0, 1), (3, 4)]]
    return 1


def test_L3_refusals():
    px = bytes(12)
    cases = [
        ('magic P2', b'P2\n4 3\n255\n' + px, [], 2),
        ('maxval 65535', b'P5\n4 3\n65535\n' + px + px, [], 2),
        ('maxval 15', b'P5\n4 3\n15\n' + px, [], 2),
        ('zero width', b'P5\n0 3\n255\n', [], 2),
        ('no header', b'', [], 2),
        ('size over the limit', b'P5\n1000001 1\n255\n', [], 2),
        ('short page', b'P5\n4 3\n255\n' + px[:11], [], 4),
        ('trailing byte', b'P5\n4 3\n255\n' + px + b'\0', [], 4),
        ('second page', b'P5\n4 3\n255\n' + px + b'P5\n4 3\n255\n' + px, [], 4),
        ('threshold 0', b'P5\n4 3\n255\n' + px, ['--threshold', '0'], 2),
        ('threshold 257', b'P5\n4 3\n255\n' + px, ['--threshold', '257'], 2),
        ('threshold not a number', b'P5\n4 3\n255\n' + px, ['--threshold', '1x'], 2),
        ('unknown option', b'P5\n4 3\n255\n' + px, ['--nope'], 2),
    ]
    for name, data, opts, want in cases:
        r = run([INKCMP, 'rle'] + opts, data)
        assert r.returncode == want, (name, r.returncode, r.stderr)
        assert r.stdout == b'', (name, 'output on a refused page')
        assert r.stderr, (name, 'no message')
    return len(cases)


def test_L4_two_pages_in_one_process():
    rng = random.Random(4)
    a = (40, 30, random_page(rng, 40, 30, 0.3)); b = (25, 60, random_page(rng, 25, 60, 0.6))
    fds = []
    for w, h, px in (a, b):
        r, wr = os.pipe(); os.write(wr, pgm(w, h, px)); os.close(wr); fds.append(r)
    p = subprocess.run([str(TWO), '154', '/dev/fd/%d' % fds[0], '/dev/fd/%d' % fds[1]], capture_output=True, pass_fds=fds)
    for f in fds: os.close(f)
    assert p.returncode == 0, p.returncode
    want = b''.join(run([ADAPTER, '--pgm', 154], pgm(w, h, px)).stdout for w, h, px in (a, b))
    assert p.stdout == want
    return 1


if __name__ == '__main__':
    for name, f in sorted((k, v) for k, v in globals().items() if k.startswith('test_')):
        print('test_inkcmp_load %s: ok (%d cases)' % (name[5:], f()))

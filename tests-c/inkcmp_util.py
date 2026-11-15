"""Shared helpers of the inkcmp tests. Standard library only. Pages are made in memory and go to
the programs through pipes (no image or stream files)."""
import random
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INKCMP = ROOT / 'build' / 'inkcmp'
ADAPTER = ROOT / 'build' / 'gd3-adapter'
BLOCKS = ROOT / 'build' / 'gd3-blocks'
WHITE = ROOT / 'build' / 'gd3-white'


def pgm(w, h, pixels, header=None):
    """pixels: bytes of length w * h (grey values)"""
    assert len(pixels) == w * h
    return (header if header is not None else b'P5\n%d %d\n255\n' % (w, h)) + bytes(pixels)


def from_rows(rows):
    """ASCII page ('#' = ink) -> (w, h, pixels)"""
    h = len(rows); w = len(rows[0]); assert all(len(r) == w for r in rows)
    return w, h, bytes(0 if c == '#' else 255 for r in rows for c in r)


def run(cmd, data):
    return subprocess.run([str(c) for c in cmd], input=data, capture_output=True)


def runs_of(w, h, pixels, t):
    """the oracle: runs per row straight from the pixels"""
    out = []
    for y in range(h):
        row = pixels[y * w:(y + 1) * w]; r = []; x = 0
        while x < w:
            if row[x] < t:
                a = x
                while x < w and row[x] < t: x += 1
                r.append((a, x))
            else:
                x += 1
        out.append(r)
    return out


def parse_rle(b):
    assert b[:8] == b'GD3RLE1\0', b[:8]
    w, h = struct.unpack_from('<II', b, 8); p = 16; rows = []
    for _ in range(h):
        n, = struct.unpack_from('<I', b, p); p += 4
        v = struct.unpack_from('<%dI' % (2 * n), b, p); p += 8 * n
        rows.append([(v[2 * i], v[2 * i + 1]) for i in range(n)])
    assert p == len(b), 'trailing bytes in the stream'
    return w, h, rows


def random_page(rng, w, h, density):
    return bytes(rng.randrange(0, 100) if rng.random() < density else rng.randrange(200, 256) for _ in range(w * h))


class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = bytearray(b'\xff' * (w * h))

    def box(self, x0, y0, x1, y1, v=0):
        for y in range(max(0, y0), min(self.h, y1)):
            self.px[y * self.w + max(0, x0):y * self.w + min(self.w, x1)] = bytes([v]) * (min(self.w, x1) - max(0, x0))

    def text(self, rng, x0, y0, x1, y1, lh, leading=None, ragged=0.0):
        """Lines of glyph-like boxes filling [x0, x1) x [y0, y1): glyph height lh (some 0.7 lh),
        width 0.3-0.6 lh, letter gap 0.15 lh, word gap 0.5 lh, line pitch `leading` (default 1.5 lh).
        Every line starts at x0 and its last glyph ends at x1 exactly unless ragged > 0."""
        leading = leading or int(1.5 * lh)
        y = y0
        while y + lh <= y1:
            end = x1 - (int(rng.random() * ragged * (x1 - x0)) if ragged else 0)
            x = x0; n = 0
            while x < end:
                gw = max(2, int(lh * rng.uniform(0.3, 0.6)))
                if x + gw > end or end - (x + gw) < lh: gw = end - x      # the last glyph closes the line
                gh = lh if rng.random() < 0.5 else max(2, int(0.7 * lh))
                self.box(x, y + lh - gh, x + gw, y + lh)
                x += gw; n += 1
                x += max(1, int(0.5 * lh)) if n % 5 == 0 else max(1, int(0.15 * lh))
            y += leading
        return y - leading + lh      # bottom of the last line

    def data(self):
        return self.w, self.h, bytes(self.px)

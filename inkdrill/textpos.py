"""textpos.py -- glyph origins as the PDF specification places them (CONTRACT.md unit 5, judge).

Why: MuPDF stores a simple font's /Widths as whole thousandths of an em (496.9 becomes 497), and places each glyph
with that rounded advance. Along a text run the error adds up: on board 1511.08771's snippet page 12 mutool's
origins stand 0.122 pt (0.68 px at 400 dpi) right of where /Widths puts the last glyph, while poppler and Ghostscript
draw it where /Widths puts it. The judge took glyph origins from mutool and pixels from poppler, so a long line read
as residual towards its end.

What: `origins(pdf, pages, traced)` interprets each page's text operators with the exact widths and returns, per
page, the specification's origin of every glyph that `mutool trace` lists (same order), in mutool's page frame. The
frame (user space -> mutool page coordinates: one of eight axis-aligned linear parts and a translation) is not
reconstructed from boxes and /Rotate: it is fitted to the glyphs that follow an absolute positioning (BT, Td, TD, Tm, T*, ' and "), whose mutool origin has no error yet.
A glyph more than 1 pt from mutool's origin keeps that origin; a page where the glyph counts differ (a Type 0 font
this does not read, a form it cannot follow), the fit is not exact, or more than 5% of the glyphs are that far keeps
mutool's origins throughout, and says so (board BH1: an invisible OCR layer).
Standard library only; one `mutool run` per PDF reads the content streams and the fonts' widths.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

DUMP_JS = r"""
function hex(buf) {
  var s = [], n = buf.length;
  for (var i = 0; i < n; i++) { var b = buf.readByte(i); s.push((b < 16 ? "0" : "") + b.toString(16)); }
  return s.join("");
}
function num(o) { return o && o.isNumber() ? o.valueOf() : null; }
function nums(a) { var r = []; if (a && a.isArray()) for (var i = 0; i < a.length; i++) { var v = a.get(i); r.push(v.isArray() ? nums(v) : v.valueOf()); } return r; }
function fonts(res) {
  var out = {};
  var f = res ? res.get("Font") : null;
  if (f && f.isDictionary()) f.forEach(function (v, k) {
    var d = {sub: String(v.get("Subtype")), first: num(v.get("FirstChar")), widths: nums(v.get("Widths")),
             fm: nums(v.get("FontMatrix")), missing: null, dw: null, w: null};
    var fd = v.get("FontDescriptor");
    if (fd && fd.isDictionary()) d.missing = num(fd.get("MissingWidth"));
    if (d.sub == "/Type0") {
      var df = v.get("DescendantFonts");
      if (df && df.isArray() && df.length) { var c = df.get(0); d.dw = num(c.get("DW")); d.w = nums(c.get("W")); }
      d.enc = String(v.get("Encoding"));
    }
    out[k] = d;
  });
  return out;
}
function forms(res, depth) {
  var out = {};
  var x = res ? res.get("XObject") : null;
  if (depth < 4 && x && x.isDictionary()) x.forEach(function (v, k) {
    if (String(v.get("Subtype")) != "/Form") return;
    var r = v.get("Resources");
    out[k] = {data: hex(v.readStream()), matrix: nums(v.get("Matrix")), fonts: fonts(r), forms: forms(r, depth + 1)};
  });
  return out;
}
var doc = Document.openDocument(scriptArgs[0]);
var first = parseInt(scriptArgs[1]), last = parseInt(scriptArgs[2]);
if (last < 0 || last > doc.countPages()) last = doc.countPages();
var pages = [];
for (var n = first; n <= last; n++) {
  var o = doc.loadPage(n - 1).getObject();
  var res = o.get("Resources"), c = o.get("Contents"), data = "";
  if (c.isArray()) { for (var i = 0; i < c.length; i++) data += hex(c.get(i).readStream()) + "0a"; }
  else if (c.isStream()) data = hex(c.readStream());
  pages.push({page: n, data: data, fonts: fonts(res), forms: forms(res, 0)});
}
print(JSON.stringify(pages));
"""

TOKEN = re.compile(rb"\s*(?:(%[^\r\n]*)|(\()|(<<|>>)|(<[0-9A-Fa-f\s]*>)|(\[|\])|(/[^\s/\[\]()<>{}%]*)|"
                   rb"([+-]?(?:\d+\.?\d*|\.\d+))|([^\s/\[\]()<>{}%]+))")
ESC = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12, ord("("): 40, ord(")"): 41, ord("\\"): 92}


def _literal(b: bytes, i: int):
    """a literal string from just after '(' -> (bytes, index after ')')"""
    out, depth = bytearray(), 1
    while i < len(b):
        c = b[i]
        if c == 92 and i + 1 < len(b):                   # backslash
            d = b[i + 1]
            if d in ESC:
                out.append(ESC[d]); i += 2
            elif 48 <= d <= 55:
                j = i + 1
                while j < len(b) and j < i + 4 and 48 <= b[j] <= 55:
                    j += 1
                out.append(int(b[i + 1:j], 8) & 255); i = j
            elif d in (10, 13):
                i += 2 + (d == 13 and i + 2 < len(b) and b[i + 2] == 10)
            else:
                out.append(d); i += 2
            continue
        if c == 40:
            depth += 1
        elif c == 41:
            depth -= 1
            if not depth:
                return bytes(out), i + 1
        out.append(c)
        i += 1
    return bytes(out), i


def tokens(b: bytes):
    """content-stream tokens: ('n', float) ('s', bytes) ('name', str) ('[',) (']',) ('op', str); inline images
    (BI .. ID <binary> EI) are skipped"""
    i, n = 0, len(b)
    while i < n:
        m = TOKEN.match(b, i)
        if not m or m.end() == i:
            i += 1
            continue
        i = m.end()
        if m[1] is not None:
            continue
        if m[2]:
            s, i = _literal(b, i)
            yield ("s", s)
        elif m[3]:
            yield ("dict", m[3])
        elif m[4]:
            h = re.sub(rb"\s", b"", m[4][1:-1])
            yield ("s", bytes.fromhex((h + b"0" * (len(h) % 2)).decode()))
        elif m[5]:
            yield (m[5].decode(),)
        elif m[6]:
            yield ("name", m[6][1:].decode("latin-1"))
        elif m[7]:
            yield ("n", float(m[7]))
        elif m[8]:
            op = m[8].decode("latin-1")
            if op == "BI":                                   # inline image: skip to EI after ID
                j = b.find(b"ID", i)
                k = j
                while k >= 0:
                    k = b.find(b"EI", k + 2)
                    if k < 0 or (k + 2 >= n or b[k + 2] in b" \t\r\n") and b[k - 1] in b" \t\r\n":
                        break
                i = n if k < 0 else k + 2
                continue
            yield ("op", op)


def mul(a, b):
    """3x3 affine [a b c d e f] product a * b (row vectors: apply a, then b)"""
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def width(font: dict, code: int) -> float:
    """glyph width in text space units (1000ths for non-Type 3)"""
    if font.get("sub") == "/Type0":
        w = font.get("w") or []
        k = 0
        while k + 1 < len(w):
            if isinstance(w[k + 1], list):
                if w[k] <= code < w[k] + len(w[k + 1]):
                    return w[k + 1][int(code - w[k])] / 1000
                k += 2
            else:
                if k + 2 < len(w) and w[k] <= code <= w[k + 1]:
                    return w[k + 2] / 1000
                k += 3
        return (font.get("dw") if font.get("dw") is not None else 1000) / 1000
    first, ws = font.get("first"), font.get("widths") or []
    if first is not None and 0 <= code - first < len(ws):
        w = ws[int(code - first)]
    else:
        w = font.get("missing") or 0
    if font.get("sub") == "/Type3" and font.get("fm"):
        return w * font["fm"][0]
    return w / 1000


def run(data: bytes, fonts: dict, forms: dict, ctm: list, out: list, depth: int = 0) -> bool:
    """interpret one content stream; append (user x, user y, fresh) per shown glyph. False if something here cannot
    be followed (an unknown font)"""
    gs = [ctm[:]]
    ctm = ctm[:]
    st = {"font": None, "size": 0.0, "tc": 0.0, "tw": 0.0, "th": 1.0, "tl": 0.0, "rise": 0.0}
    tm = tlm = [1, 0, 0, 1, 0, 0]
    fresh, ok = True, True
    stack = []

    def show(s: bytes):
        nonlocal tm, fresh
        f = fonts.get(st["font"])
        if f is None:
            return False
        two = f.get("sub") == "/Type0"
        codes = [s[k] << 8 | s[k + 1] for k in range(0, len(s) - 1, 2)] if two else list(s)
        for code in codes:
            trm = mul([1, 0, 0, 1, 0, st["rise"]], mul(tm, ctm))
            out.append((trm[4], trm[5], fresh))
            fresh = False
            tx = (width(f, code) * st["size"] + st["tc"] + (st["tw"] if (code == 32 and not two) else 0)) * st["th"]
            tm = mul([1, 0, 0, 1, tx, 0], tm)
        return True
    for t in tokens(data):
        if t[0] != "op":
            if t[0] == "[":
                stack.append("[")
            elif t[0] == "]":
                arr = []
                while stack and stack[-1] != "[":
                    arr.append(stack.pop())
                if stack:
                    stack.pop()
                stack.append(("arr", arr[::-1]))
            else:
                stack.append(t)
            continue
        op, a = t[1], stack
        n = [x[1] for x in a if isinstance(x, tuple) and x[0] == "n"]
        try:
            if op == "q":
                gs.append(ctm[:])
            elif op == "Q":
                ctm = gs.pop() if len(gs) > 1 else gs[0][:]
            elif op == "cm" and len(n) >= 6:
                ctm = mul(n[-6:], ctm)
            elif op == "BT":
                tm = tlm = [1, 0, 0, 1, 0, 0]; fresh = True
            elif op == "Tf" and len(a) >= 2 and a[-2][0] == "name":
                st["font"], st["size"] = a[-2][1], a[-1][1]
            elif op == "Tc" and n:
                st["tc"] = n[-1]
            elif op == "Tw" and n:
                st["tw"] = n[-1]
            elif op == "Tz" and n:
                st["th"] = n[-1] / 100
            elif op == "TL" and n:
                st["tl"] = n[-1]
            elif op == "Ts" and n:
                st["rise"] = n[-1]
            elif op in ("Td", "TD") and len(n) >= 2:
                if op == "TD":
                    st["tl"] = -n[-1]
                tlm = mul([1, 0, 0, 1, n[-2], n[-1]], tlm); tm = tlm; fresh = True
            elif op == "Tm" and len(n) >= 6:
                tlm = tm = n[-6:]; fresh = True
            elif op == "T*":
                tlm = mul([1, 0, 0, 1, 0, -st["tl"]], tlm); tm = tlm; fresh = True
            elif op in ("Tj", "'", '"'):
                if op != "Tj":
                    if op == '"' and len(n) >= 2:
                        st["tw"], st["tc"] = n[-2], n[-1]
                    tlm = mul([1, 0, 0, 1, 0, -st["tl"]], tlm); tm = tlm; fresh = True
                s = [x for x in a if x[0] == "s"]
                if s and not show(s[-1][1]):
                    ok = False
            elif op == "TJ" and a and a[-1][0] == "arr":
                for x in a[-1][1]:
                    if x[0] == "s":
                        if not show(x[1]):
                            ok = False
                    elif x[0] == "n":
                        tm = mul([1, 0, 0, 1, -x[1] / 1000 * st["size"] * st["th"], 0], tm)
            elif op == "Do" and a and a[-1][0] == "name" and a[-1][1] in forms and depth < 4:
                fo = forms[a[-1][1]]
                m = fo.get("matrix") or [1, 0, 0, 1, 0, 0]
                sub = fo.get("fonts") or {}
                if not run(bytes.fromhex(fo["data"]), {**fonts, **sub}, fo.get("forms") or {}, mul(m, ctm), out, depth + 1):
                    ok = False
        except (IndexError, TypeError, KeyError, ValueError):
            ok = False
        stack = []
    return ok


TRACE_G = re.compile(r'<page number="(\d+)"|<g unicode="[^"]*" glyph="[^"]*" x="([^"]+)" y="([^"]+)"|'
                     r'<(fill_text|stroke_text|clip_text|clip_stroke_text|ignore_text) [^>]*transform="([^"]+)"')


LINEAR = ((1, 0, 0, -1), (0, 1, 1, 0), (-1, 0, 0, 1), (0, -1, -1, 0), (1, 0, 0, 1), (0, -1, 1, 0), (-1, 0, 0, -1),
          (0, 1, -1, 0))


def solve(pts):
    """the page frame: one of the eight axis-aligned linear parts (the page's rotation, y flipped or not) and the
    translation that fits [(x, y, X, Y)] best -> (a, b, c, d, e, f), max residual. A one-line snippet page has all its
    anchor glyphs on one baseline; a general affine fit is singular there, this one is not."""
    import math
    best = (None, math.inf)
    if not pts:
        return best
    for a, b, c, d in LINEAR:
        e = sum(X - (a * x + c * y) for x, y, X, Y in pts) / len(pts)
        f = sum(Y - (b * x + d * y) for x, y, X, Y in pts) / len(pts)
        res = max(abs(a * x + c * y + e - X) + abs(b * x + d * y + f - Y) for x, y, X, Y in pts)
        if res < best[1]:
            best = ((a, b, c, d, e, f), res)
    return best


def origins(pdf: str, first: int, last, wd: str) -> dict:
    """page -> {"ok": bool, "why": str, "map": {(mutool x, mutool y) rounded to 1e-4: (spec x, spec y)}} for the
    glyphs mutool traces, in mutool's page frame (the frame of stext and trace)"""
    js = os.path.join(wd, "textpos.js")
    with open(js, "w") as fh:
        fh.write(DUMP_JS)
    p = subprocess.run(["mutool", "run", js, os.path.abspath(pdf), str(first), str(-1 if last is None else last)],
                       capture_output=True, text=True)
    tr = subprocess.run(["mutool", "trace", pdf, f"{first}-{'N' if last is None else last}"], capture_output=True,
                        text=True).stdout
    traced, cur, M = {}, None, None
    for m in TRACE_G.finditer(tr):
        if m[1]:
            cur = traced.setdefault(int(m[1]), [])
        elif m[4]:
            M = [float(v) for v in m[5].split()]
        elif m[2] is not None and cur is not None:
            gx, gy = float(m[2]), float(m[3])
            cur.append((gx * M[0] + gy * M[2] + M[4], gx * M[1] + gy * M[3] + M[5]))
    out = {}
    try:
        pages = json.loads(p.stdout) if p.returncode == 0 else []
    except ValueError:
        pages = []
    for pg in pages:
        n = pg["page"]
        mine = []
        ok = run(bytes.fromhex(pg["data"]), pg["fonts"], pg["forms"], [1, 0, 0, 1, 0, 0], mine)
        mu = traced.get(n, [])
        rec = {"ok": False, "why": "", "map": {}}
        out[n] = rec
        if not ok:
            rec["why"] = "a font or form this does not read"
            continue
        if len(mine) != len(mu):
            rec["why"] = f"glyph counts differ ({len(mine)} here, {len(mu)} traced)"
            continue
        fresh = [(x, y, X, Y) for (x, y, f), (X, Y) in zip(mine, mu) if f]
        T, res = solve(fresh)
        if T is None or res > 0.01:
            rec["why"] = f"frame fit residual {res:.3g} pt"
            continue
        dev, far = 0.0, 0
        for (x, y, _), (X, Y) in zip(mine, mu):
            sx, sy = T[0] * x + T[2] * y + T[4], T[1] * x + T[3] * y + T[5]
            d = abs(sx - X) + abs(sy - Y)
            if d > 1.0:                      # not the same glyph, or a state this does not follow: mutool's origin
                far += 1
                continue
            dev = max(dev, d)
            rec["map"][(round(X, 4), round(Y, 4))] = (sx, sy)
        if far > 0.05 * len(mu):
            rec["map"], rec["why"] = {}, f"{far} of {len(mu)} glyphs > 1 pt from mutool's origin"
            continue
        rec["ok"], rec["why"] = True, f"max shift {dev:.3f} pt, {far} glyphs kept at mutool's origin"
    return out

"""tools/lines-audit/vecfig_0864.py, rule-6 experiment for inkcmp lines' figure step (2026-11-17): figures as clusters of the PDF's own drawn paths (mutool trace), against MathPix's
figure regions on 0864. Paths: stroke/fill (no clip), each its bbox in page pt; a page-size fill (background) is left
out. Boxes closer than TOL pt join; a cluster >= 30 pt on a side is a figure candidate; then the text glyphs within
TOL of it join (labels, tick numbers). IoU against each MathPix diagram/chart (and table) region."""
import json, os, re, subprocess, sys
# usage: vecfig_0864.py [TOL pt] (12 is the best of 4/8/12/20)
TOL = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
LIB = os.path.expanduser("~/pdfdrill-library/0864")
mp = json.load(open(f"{LIB}/0864.lines.json"))
def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - ix*iy
    return ix*iy/u if u > 0 else 0
PATH = re.compile(r'<(fill_path|stroke_path|clip_path|clip_stroke_path|fill_text|stroke_text|clip_text|ignore_text)\b[^>]*?transform="([^"]+)"|<(moveto|lineto|curveto) ([^/]*)/>|<g unicode="[^"]*" glyph="[^"]*" x="([^"]+)" y="([^"]+)"|</(fill_path|stroke_path|clip_path|clip_stroke_path)>')
def cluster(boxes, tol):
    par = list(range(len(boxes)))
    def f(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    for i in range(len(boxes)):
        for j in range(i):
            a, b = boxes[i], boxes[j]
            if a[0] - tol <= b[2] and b[0] - tol <= a[2] and a[1] - tol <= b[3] and b[1] - tol <= a[3]:
                par[f(i)] = f(j)
    g = {}
    for i, b in enumerate(boxes):
        r = f(i); c = g.get(r)
        g[r] = b if c is None else (min(c[0], b[0]), min(c[1], b[1]), max(c[2], b[2]), max(c[3], b[3]))
    return list(g.values())
tot = []
for pg in mp["pages"]:
    n = pg["page"]; k = pg["page_width"] / 612.0
    tr = subprocess.run(["mutool", "trace", f"{LIB}/0864.pdf", str(n)], capture_output=True, text=True).stdout
    paths, glyphs, cur, kind, M = [], [], None, None, None
    for m in PATH.finditer(tr):
        if m[1]:
            kind, M = m[1], [float(v) for v in m[2].split()]
            if kind.endswith("path"): cur = []
        elif m[3] and cur is not None:
            v = [float(x.split("=")[1].strip('"')) for x in m[4].split()]
            for i in range(0, len(v), 2):
                cur.append((v[i] * M[0] + v[i+1] * M[2] + M[4], v[i] * M[1] + v[i+1] * M[3] + M[5]))
        elif m[7]:
            if cur and kind in ("fill_path", "stroke_path"):
                xs, ys = [p[0] for p in cur], [p[1] for p in cur]
                b = (min(xs), min(ys), max(xs), max(ys))
                if not (b[2] - b[0] > 550 and b[3] - b[1] > 700): paths.append(b)
            cur = None
        elif m[5] is not None and kind in ("fill_text",):
            x, y = float(m[5]), float(m[6]); X = x*M[0] + y*M[2] + M[4]; Y = x*M[1] + y*M[3] + M[5]
            glyphs.append((X, Y - 6, X + 4, Y + 1))
    cl = [c for c in cluster(paths, TOL) if c[2] - c[0] >= 30 and c[3] - c[1] >= 30]
    figs = []
    for c in cl:                                   # labels: glyphs within TOL join, repeated until stable
        while True:
            add = [g for g in glyphs if g[0] - TOL <= c[2] and c[0] - TOL <= g[2] and g[1] - TOL <= c[3] and c[1] - TOL <= g[3]
                   and not (c[0] <= g[0] and g[2] <= c[2] and c[1] <= g[1] and g[3] <= c[3])]
            if not add: break
            c = (min([c[0]] + [g[0] for g in add]), min([c[1]] + [g[1] for g in add]), max([c[2]] + [g[2] for g in add]), max([c[3]] + [g[3] for g in add]))
        figs.append(tuple(v * k for v in c))
    for l in pg["lines"]:
        if l["type"] in ("diagram", "chart", "table"):
            r = l["region"]; b = (r["top_left_x"], r["top_left_y"], r["top_left_x"] + r["width"], r["top_left_y"] + r["height"])
            best = max(((iou(b, f), f) for f in figs), default=(0, None))
            tot.append((l["type"], best[0]))
            print(f"p{n} MathPix {l['type']:8} {[int(v) for v in b]}  best vector cluster IoU {best[0]:.2f} {[int(v) for v in best[1]] if best[1] else ''}")
    print(f"p{n}: {len(paths)} paths, {len(figs)} figure clusters")
fig = [v for t, v in tot if t != "table"]
print(f"TOL {TOL}: figures with IoU >= 0.5: {sum(v >= 0.5 for v in fig)} of {len(fig)}; >= 0.8: {sum(v >= 0.8 for v in fig)}")

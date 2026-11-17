/* inkcmp unit 7: lines of one grey page (CONTRACT.md unit 7). A port of the T-0148 prototype
   (work/run-files/blob-lines/blob_lines.py, PIL); where the prototype used PIL box averages, the same rounding is
   kept here. It agreed with the prototype on all 5501 lines of Killing, Hilbert and Penev before two measured
   changes (contract unit 7): the edge-column blanking is gone, and a display's rows merge across its number.
   Steps are numbered as in the contract. */
#define _POSIX_C_SOURCE 199309L
#include "lines.h"
#include <time.h>
#include "layout/blocks.h"
#include "white.h"
#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

typedef struct { int32_t x0, y0, x1, y1; } Box;
typedef struct { Box *b; size_t n, cap; } Boxes;

static int push(Boxes *v, Box b) {
  if (v->n == v->cap) {
    size_t c = v->cap ? 2 * v->cap : 64;
    Box *p = realloc(v->b, c * sizeof *p);
    if (!p) return 0;
    v->b = p; v->cap = c;
  }
  v->b[v->n++] = b;
  return 1;
}

/* ---- PGM ---- */
static int token(FILE *in, char *s, size_t n, int *eof) {
  int c; size_t p = 0;
  do {
    c = fgetc(in);
    if (c == '#') { while (c != '\n' && c != EOF) c = fgetc(in); }
  } while (c != EOF && isspace((unsigned char)c));
  if (c == EOF) { *eof = 1; return 0; }
  do {
    if (p + 1 >= n) return 0;
    s[p++] = (char)c; c = fgetc(in);
  } while (c != EOF && !isspace((unsigned char)c) && c != '#');
  if (c == '#') while (c != '\n' && c != EOF) c = fgetc(in);
  s[p] = 0;
  return 1;
}
static int number(const char *s, uint32_t *v) {
  char *end; errno = 0; unsigned long a = strtoul(s, &end, 10);
  if (errno || *end || a == 0 || a > 1000000) return 0;
  *v = (uint32_t)a; return 1;
}
int ic_grey_from_pgm(FILE *in, uint8_t **grey, uint32_t *w, uint32_t *h) {
  char tok[64]; uint32_t mv = 0; int eof = 0;
  *grey = NULL;
  if (!token(in, tok, sizeof tok, &eof)) return eof ? 1 : 2;
  if (strcmp(tok, "P5") || !token(in, tok, sizeof tok, &eof) || !number(tok, w)
      || !token(in, tok, sizeof tok, &eof) || !number(tok, h)
      || !token(in, tok, sizeof tok, &eof) || !number(tok, &mv) || mv != 255) return 2;
  size_t n = (size_t)*w * *h;
  uint8_t *g = malloc(n);
  if (!g) return 5;
  if (fread(g, 1, n, in) != n) { free(g); return 4; }
  *grey = g;
  return 0;
}

/* ---- step 1: threshold ---- */
static uint32_t auto_threshold(const uint8_t *g, size_t n) {
  double hist[256] = {0};
  for (size_t i = 0; i < n; i++) hist[g[i]] += 1;
  double tot = 0, N = (double)n;
  for (int i = 0; i < 256; i++) tot += i * hist[i];
  double wb = 0, sb = 0, best = 0, var[256] = {0}; int t = 0;
  for (int i = 0; i < 256; i++) {
    wb += hist[i];
    if (wb == 0 || wb == N) continue;
    sb += i * hist[i];
    double mb = sb / wb, mf = (tot - sb) / (N - wb), v = wb * (N - wb) * (mb - mf) * (mb - mf);
    var[i] = v;
    if (v > best) { best = v; t = i; }
  }
  {   /* a page of few grey levels (a rendered PDF) has a flat maximum: every split in the empty gap between ink and
         paper is equally good. Take the middle of that range, not its first value, which sits at the ink. */
    int lo = t, hi = t;
    while (hi + 1 < 256 && var[hi + 1] >= best * (1 - 1e-9)) hi++;
    t = (lo + hi) / 2;
  }
  if (t <= 1) return 128;
  double s = 0, c = 0;
  for (int i = 0; i <= t; i++) { s += i * hist[i]; c += hist[i]; }
  double dark = s / (c > 0 ? c : 1);
  return (uint32_t)((dark + t) / 2) + 1;
}

/* ---- components over row runs (8-connected): label per run, union-find ---- */
typedef struct {
  uint32_t *rs; uint64_t *row_start; size_t nrun;   /* runs: rs[2k], rs[2k+1] half-open */
  uint32_t *par;
} Runs;
static void runs_free(Runs *r) { free(r->rs); free(r->row_start); free(r->par); memset(r, 0, sizeof *r); }
static int runs_of(const uint8_t *ink, uint32_t w, uint32_t h, Runs *r) {
  memset(r, 0, sizeof *r);
  size_t cap = 1024;
  r->rs = malloc(2 * cap * sizeof *r->rs);
  r->row_start = malloc(((size_t)h + 1) * sizeof *r->row_start);
  if (!r->rs || !r->row_start) return 0;
  for (uint32_t y = 0; y < h; y++) {
    r->row_start[y] = r->nrun;
    const uint8_t *row = ink + (size_t)y * w;
    uint32_t x = 0;
    while (x < w) {
      while (x < w && !row[x]) x++;
      if (x == w) break;
      uint32_t a = x;
      while (x < w && row[x]) x++;
      if (r->nrun == cap) {
        cap *= 2;
        uint32_t *p = realloc(r->rs, 2 * cap * sizeof *p);
        if (!p) return 0;
        r->rs = p;
      }
      r->rs[2 * r->nrun] = a; r->rs[2 * r->nrun + 1] = x; r->nrun++;
    }
  }
  r->row_start[h] = r->nrun;
  return 1;
}
static uint32_t find(uint32_t *par, uint32_t u) {
  while (par[u] != u) { par[u] = par[par[u]]; u = par[u]; }
  return u;
}
/* comp[k] = root of run k; boxes of roots in cb (indexed by root) */
static int label(Runs *r, uint32_t h, Box **cbox) {
  r->par = malloc((r->nrun ? r->nrun : 1) * sizeof *r->par);
  Box *cb = malloc((r->nrun ? r->nrun : 1) * sizeof *cb);
  if (!r->par || !cb) { free(cb); return 0; }
  for (size_t k = 0; k < r->nrun; k++) r->par[k] = (uint32_t)k;
  for (uint32_t y = 1; y < h; y++) {
    uint64_t p0 = r->row_start[y - 1], p1 = r->row_start[y], j = p0;
    for (uint64_t i = p1; i < r->row_start[y + 1]; i++) {
      uint32_t a = r->rs[2 * i], b = r->rs[2 * i + 1];
      while (j < p1 && r->rs[2 * j + 1] < a) j++;
      for (uint64_t k = j; k < p1 && r->rs[2 * k] <= b; k++) {
        uint32_t u = find(r->par, (uint32_t)i), v = find(r->par, (uint32_t)k);
        if (u != v) r->par[u] = v;
      }
    }
  }
  for (size_t k = 0; k < r->nrun; k++) cb[k].x0 = -1;
  for (uint32_t y = 0; y < h; y++)
    for (uint64_t k = r->row_start[y]; k < r->row_start[y + 1]; k++) {
      uint32_t u = find(r->par, (uint32_t)k);
      int32_t a = (int32_t)r->rs[2 * k], b = (int32_t)r->rs[2 * k + 1];
      Box *c = &cb[u];
      if (c->x0 < 0) { c->x0 = a; c->x1 = b; c->y0 = (int32_t)y; c->y1 = (int32_t)y + 1; }
      else { if (a < c->x0) c->x0 = a; if (b > c->x1) c->x1 = b; c->y1 = (int32_t)y + 1; }
    }
  *cbox = cb;
  return 1;
}

static double g_t0;
static void tick(const char *what) {
  if (!getenv("INKCMP_DEBUG")) return;
  struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
  double now = t.tv_sec * 1e3 + t.tv_nsec / 1e6;
  if (what) fprintf(stderr, "time %-12s %6.1f ms\n", what, now - g_t0);
  g_t0 = now;
}
static int cmp_i32(const void *a, const void *b) {
  int32_t x = *(const int32_t *)a, y = *(const int32_t *)b;
  return (x > y) - (x < y);
}

/* tight box of ink inside [x0,x1) x [y0,y1); 0 when empty */
static int tight(const uint8_t *ink, uint32_t w, int32_t x0, int32_t y0, int32_t x1, int32_t y1, Box *o) {
  int32_t bx0 = INT32_MAX, by0 = INT32_MAX, bx1 = -1, by1 = -1;
  for (int32_t y = y0; y < y1; y++) {
    const uint8_t *row = ink + (size_t)y * w;
    for (int32_t x = x0; x < x1; x++)
      if (row[x]) {
        if (x < bx0) bx0 = x;
        if (x + 1 > bx1) bx1 = x + 1;
        if (y < by0) by0 = y;
        by1 = y + 1;
      }
  }
  if (bx1 < 0) return 0;
  o->x0 = bx0; o->y0 = by0; o->x1 = bx1; o->y1 = by1;
  return 1;
}

/* step 3: a blocks line cut at white column runs >= need; a column is ink when PIL's box average of the band's
   column rounds to > 0, i.e. count * 255 / h >= 0.5 */
/* does an erased table rule (>= 3 lh long) span rows [y0, y1) at a column in [x0, x1)? */
static int ruled(const Boxes *vr, int32_t x0, int32_t x1, int32_t y0, int32_t y1, double lh) {
  for (size_t k = 0; k < vr->n; k++) {
    const Box *v = &vr->b[k];
    if (v->x1 <= x0 || v->x0 >= x1 || v->y1 - v->y0 < 3 * lh) continue;
    if (v->y0 <= y0 && v->y1 >= y1) return 1;
  }
  return 0;
}

static int pieces_of(const uint8_t *ink, uint32_t w, Box r, uint32_t need, double lhc, const Boxes *vr, Boxes *out) {
  int32_t bw = r.x1 - r.x0, bh = r.y1 - r.y0;
  if (bw <= 0 || bh <= 0) return 1;
  uint32_t *cnt = calloc((size_t)bw, sizeof *cnt);
  uint8_t *cut = calloc((size_t)bw, 1);
  if (!cnt || !cut) { free(cnt); free(cut); return 0; }
  for (size_t k = 0; k < vr->n; k++) {               /* an erased vertical rule through the band is a cell boundary */
    const Box *v = &vr->b[k];
    if (v->x1 <= r.x0 || v->x0 >= r.x1) continue;
    int32_t ov = (v->y1 < r.y1 ? v->y1 : r.y1) - (v->y0 > r.y0 ? v->y0 : r.y0);
    if (ov < bh || v->y1 - v->y0 < 3 * lhc) continue;    /* a table's rule: spans the band and >= 3 lh */
    for (int32_t x = v->x0 > r.x0 ? v->x0 : r.x0; x < v->x1 && x < r.x1; x++) cut[x - r.x0] = 1;
  }
  for (int32_t y = r.y0; y < r.y1; y++) {
    const uint8_t *row = ink + (size_t)y * w + r.x0;
    for (int32_t x = 0; x < bw; x++) cnt[x] += row[x];
  }
  int32_t start = -1, end = 0; uint32_t white = 0;
  for (int32_t i = 0; i < bw + (int32_t)need; i++) {
    int on = i < bw && (double)cnt[i] * 255.0 / bh >= 0.5;
    int hardcut = i < bw && cut[i];
    if (on && !hardcut) {
      if (start < 0) start = i;
      white = 0; end = i + 1;
    } else if (start >= 0) {
      if (hardcut) white = need;                     /* end the piece here */
      else white++;
      if (white >= need) {
        Box t;
        if (tight(ink, w, r.x0 + start, r.y0, r.x0 + end, r.y1, &t) && !push(out, t)) { free(cnt); free(cut); return 0; }
        start = -1;
      }
    }
  }
  free(cnt); free(cut);
  return 1;
}

/* step 4: split a piece taller than 1.6 m at its least-ink row (PIL's rounded row average) */
static int vsplit(const uint8_t *ink, uint32_t w, Box r, double m, Boxes *out) {
  int32_t h = r.y1 - r.y0, bw = r.x1 - r.x0;
  if (h <= 1.6 * m) return push(out, r);
  int32_t lo = (int32_t)(0.6 * m), hi = h - (int32_t)(0.6 * m);
  if (hi <= lo) return push(out, r);
  int *prof = malloc((size_t)h * sizeof *prof);
  if (!prof) return 0;
  int mx = 0;
  for (int32_t y = 0; y < h; y++) {
    const uint8_t *row = ink + (size_t)(r.y0 + y) * w + r.x0;
    uint32_t c = 0;
    for (int32_t x = 0; x < bw; x++) c += row[x];
    prof[y] = (int)floor(c * 255.0 / bw + 0.5);
    if (prof[y] > mx) mx = prof[y];
  }
  int32_t cut = lo;
  for (int32_t y = lo; y < hi; y++) if (prof[y] < prof[cut]) cut = y;
  int deep = !(prof[cut] > 0.25 * mx);
  free(prof);
  if (!deep) return push(out, r);
  int32_t ys[3] = {r.y0, r.y0 + cut, r.y1};
  for (int k = 0; k < 2; k++) {
    Box t;
    if (tight(ink, w, r.x0, ys[k], r.x1, ys[k + 1], &t) && !vsplit(ink, w, t, m, out)) return 0;
  }
  return 1;
}

/* step 5: figure boxes from components of the 4x box-reduced ink */
static int figures(const uint8_t *ink, uint32_t W, uint32_t H, double m, Boxes *figs) {
  const uint32_t k = 4;
  uint32_t w = (W + k - 1) / k, h = (H + k - 1) / k;
  uint8_t *s = calloc((size_t)w * h, 1);
  if (!s) return 0;
  for (uint32_t y = 0; y < h; y++)
    for (uint32_t x = 0; x < w; x++) {
      uint32_t c = 0, n = 0;
      for (uint32_t yy = y * k; yy < y * k + k && yy < H; yy++)
        for (uint32_t xx = x * k; xx < x * k + k && xx < W; xx++) { c += ink[(size_t)yy * W + xx]; n++; }
      s[(size_t)y * w + x] = floor(c * 255.0 / n + 0.5) > 40;
    }
  Runs r; Box *cb = NULL; int ok = runs_of(s, w, h, &r) && label(&r, h, &cb);
  free(s);
  if (!ok) { runs_free(&r); free(cb); return 0; }
  for (size_t i = 0; i < r.nrun; i++) {
    if (r.par[i] != i) continue;
    Box c = cb[i];
    int32_t x0 = c.x0 * (int32_t)k, y0 = c.y0 * (int32_t)k, x1 = c.x1 * (int32_t)k, y1 = c.y1 * (int32_t)k;
    if (y1 - y0 > 3 * m && x1 - x0 > 4 * m && x0 > (int32_t)(2 * k) && x1 < (int32_t)(W - 2 * k)
        && y0 > (int32_t)(2 * k) && y1 < (int32_t)(H - 2 * k)) {
      Box f = {x0, y0, x1, y1};
      if (!push(figs, f)) { runs_free(&r); free(cb); return 0; }
    }
  }
  runs_free(&r); free(cb);
  return 1;
}

/* step 3a: columns before lines. A gutter is a vertical white separator of unit 3, >= 0.75 lh wide and >= 10 lh
   tall, strictly inside the ink's x-range, with a column >= 10 lh wide on each side. Its ends move to the nearest
   white row within 1 lh. The page is cut at the gutters' ends into bands; in each band the gutters that cover it all
   cut it at their middles into cells, and neighbouring bands with the same cuts are one band. gd3_blocks finds lines
   in each cell alone, and step 6 never joins across a gutter, so a line never crosses one, however narrow.
   (A test for ink on both sides of a gutter was redundant beside the width test on both development sets.) */
static int cmp_u32(const void *a, const void *b) {
  uint32_t p = *(const uint32_t *)a, q = *(const uint32_t *)b;
  return (p > q) - (p < q);
}
/* The vertical white separators of a page, tallest first. Unit 3's ic_space keeps candidates by decreasing AREA
   over both classes, and a kept rectangle that spans a candidate's width cuts it: a column gutter is crossed by
   every paragraph gap that lines up in both columns and by the white after each short line, all wider than the
   gutter, so the gutter came back in pieces (postscriptexamples23 p2: the gutter runs from y 294 to 2568 and was
   reported from 992). A gutter is the TALLEST white rectangle at its place, so here the candidates (maximal white
   rectangles >= wf lh wide and >= hf lh tall, clipped to the content box, strictly inside it) are kept by
   decreasing height; one that lies > 20% inside rectangles already kept is dropped. Fills S->vsep/nvsep/content/blank. */
static int by_height(const void *a, const void *b) {
  const IcRect *p = a, *q = b;
  uint32_t hp = p->y1 - p->y0, hq = q->y1 - q->y0;
  if (hp != hq) return hp > hq ? -1 : 1;
  if (p->x0 != q->x0) return p->x0 < q->x0 ? -1 : 1;
  if (p->y0 != q->y0) return p->y0 < q->y0 ? -1 : 1;
  if (p->x1 != q->x1) return p->x1 < q->x1 ? -1 : 1;
  return 0;
}
/* candidates: every maximal white rectangle >= wf lh wide and >= hf lh tall, clipped to the content box in y and
   strictly inside it in x. One scan of the page (ic_white); both uses below select from this list. */
typedef struct { IcRect *r; size_t n; IcRect content; int blank; } White;
static int white_candidates(const IcPage *pg, double lh, double wf, double hf, White *Wt) {
  memset(Wt, 0, sizeof *Wt);
  uint32_t w = pg->width, h = pg->height, cx0 = w, cx1 = 0, cy0 = h, cy1 = 0;
  for (uint32_t y = 0; y < h; y++) {
    uint64_t i0 = pg->row_start[y], i1 = pg->row_start[y + 1];
    if (i0 == i1) continue;
    if (cy0 == h) cy0 = y;
    cy1 = y + 1;
    if (pg->spans[2 * i0] < cx0) cx0 = pg->spans[2 * i0];
    if (pg->spans[2 * i1 - 1] > cx1) cx1 = pg->spans[2 * i1 - 1];
  }
  if (cy0 == h) { Wt->blank = 1; return 1; }
  Wt->content = (IcRect){cx0, cy0, cx1, cy1};
  uint32_t mw = (uint32_t)ceil(wf * lh), mh = (uint32_t)ceil(hf * lh);
  if (mw < 1) mw = 1;
  if (mh < 1) mh = 1;
  IcRects R;
  /* The scan costs O(width x height) and was the largest step (43 of 109 ms on a 6 Mpx page). The rectangles
     wanted are large, so at lh >= 16 px it runs on the page reduced 2 x 2 (a cell is ink when any of its four
     pixels is). A white cell is white in all four pixels, so every rectangle found is white on the page; a side
     may fall short of the true rectangle by up to 2 px. The size tests are not relaxed for that: on the dev set
     the results equal the full-resolution scan's with no allowance, and are slightly worse with one. */
  uint32_t k = lh >= 16 ? 2 : 1;
  if (k == 1) {
    if (ic_white(pg, mw, mh, &R)) return 0;
  } else {
    uint32_t cw = (w + 1) / 2, ch = (h + 1) / 2;
    uint64_t *rs = malloc(((size_t)ch + 1) * sizeof *rs);
    uint32_t *sp = malloc((pg->nruns ? pg->nruns : 1) * 2 * sizeof *sp);
    if (!rs || !sp) { free(rs); free(sp); return 0; }
    uint64_t n = 0;
    for (uint32_t Y = 0; Y < ch; Y++) {              /* merge the runs of rows 2Y and 2Y + 1, halved */
      rs[Y] = n;
      uint64_t a = pg->row_start[2 * Y], ae = pg->row_start[2 * Y + 1];
      uint64_t b = 2 * Y + 1 < h ? ae : 0, be = 2 * Y + 1 < h ? pg->row_start[2 * Y + 2] : 0;
      uint64_t first = n;
      while (a < ae || b < be) {
        int ta = b >= be || (a < ae && pg->spans[2 * a] <= pg->spans[2 * b]);
        uint64_t i = ta ? a++ : b++;
        uint32_t x0 = pg->spans[2 * i] / 2, x1 = (pg->spans[2 * i + 1] + 1) / 2;
        if (n > first && x0 <= sp[2 * n - 1]) { if (x1 > sp[2 * n - 1]) sp[2 * n - 1] = x1; }
        else { sp[2 * n] = x0; sp[2 * n + 1] = x1; n++; }
      }
    }
    rs[ch] = n;
    IcPage cp = {cw, ch, rs, sp, n};
    uint32_t cmw = mw > 3 ? (mw - 2) / 2 : 1, cmh = mh > 3 ? (mh - 2) / 2 : 1;
    int rc = ic_white(&cp, cmw, cmh, &R);
    free(rs); free(sp);
    if (rc) return 0;
    for (size_t i = 0; i < R.n; i++) {
      R.r[i].x0 *= 2; R.r[i].y0 *= 2; R.r[i].x1 *= 2; R.r[i].y1 *= 2;
      if (R.r[i].x1 > w) R.r[i].x1 = w;
      if (R.r[i].y1 > h) R.r[i].y1 = h;
    }
    mh = mh > 2 ? mh - 2 : 1;
  }
  size_t nc = 0;
  for (size_t i = 0; i < R.n; i++) {
    IcRect q = R.r[i];
    if (q.y0 < cy0) q.y0 = cy0;
    if (q.y1 > cy1) q.y1 = cy1;
    if (q.x0 <= cx0 || q.x1 >= cx1 || q.y1 <= q.y0 || q.y1 - q.y0 < mh) continue;
    R.r[nc++] = q;
  }
  if (nc) qsort(R.r, nc, sizeof *R.r, by_height);
  Wt->r = R.r; Wt->n = nc;
  return 1;
}
/* rows of c with ink within d px on its left, and on its right; the smaller count is returned. A separator
   separates: the white beside a display and down an empty half page is tall and has text on neither side for most of
   its rows. The two sides are counted apart, because the lines of two columns need not share rows
   (postscriptexamples23 p2: 834 rows left, 656 right, 253 with both, of 2382). */
static uint32_t flanked_rows(const IcPage *pg, IcRect c, uint32_t d) {
  uint32_t nl = 0, nr = 0;
  for (uint32_t y = c.y0; y < c.y1; y++) {
    uint64_t lo = pg->row_start[y], hi = pg->row_start[y + 1];
    if (lo == hi) continue;
    while (lo < hi) {                                /* the first run that ends after c.x0 */
      uint64_t mid = (lo + hi) / 2;
      if (pg->spans[2 * mid + 1] <= c.x0) lo = mid + 1; else hi = mid;
    }
    /* c is white, so run lo starts at or after c.x1, and run lo - 1 ends at or before c.x0 */
    nl += lo > pg->row_start[y] && pg->spans[2 * (lo - 1) + 1] + d >= c.x0;
    nr += lo < pg->row_start[y + 1] && pg->spans[2 * lo] <= c.x1 + d;
  }
  return nl < nr ? nl : nr;
}
/* the candidates >= wf lh wide and >= hf lh tall, tallest first, that have ink within 3 lh on each side in at
   least a fifth of their rows; one lying > 20% inside those kept is dropped */
static int tall_white(const IcPage *pg, const White *Wt, double lh, double wf, double hf, IcSpace *S) {
  memset(S, 0, sizeof *S);
  S->blank = Wt->blank; S->content = Wt->content;
  IcRect *v = malloc((Wt->n ? Wt->n : 1) * sizeof *v);
  if (!v) return 0;
  size_t nv = 0;
  for (size_t i = 0; i < Wt->n && nv < 1024; i++) {
    IcRect c = Wt->r[i];
    if (c.x1 - c.x0 < wf * lh || c.y1 - c.y0 < hf * lh) continue;
    if (5 * flanked_rows(pg, c, (uint32_t)(3 * lh)) < c.y1 - c.y0) continue;
    uint64_t in = 0, a = (uint64_t)(c.x1 - c.x0) * (c.y1 - c.y0);
    for (size_t j = 0; j < nv; j++) {
      uint32_t x0 = c.x0 > v[j].x0 ? c.x0 : v[j].x0, x1 = c.x1 < v[j].x1 ? c.x1 : v[j].x1;
      uint32_t y0 = c.y0 > v[j].y0 ? c.y0 : v[j].y0, y1 = c.y1 < v[j].y1 ? c.y1 : v[j].y1;
      if (x0 < x1 && y0 < y1) in += (uint64_t)(x1 - x0) * (y1 - y0);
    }
    if (5 * in > a) continue;
    v[nv++] = c;
  }
  S->vsep = v; S->nvsep = nv;
  return 1;
}

static int column_cells(const Runs *r, const White *Wt, uint32_t W, uint32_t H, double lh, Boxes *cells, Boxes *gutters) {
  IcSpace S;
  IcPage pg = {W, H, r->row_start, r->rs, r->nrun};
  if (!tall_white(&pg, Wt, lh, 0.75, 10, &S)) return 0;
  int ok = 0;
  IcRect *g = malloc((S.nvsep ? S.nvsep : 1) * sizeof *g);
  uint32_t *ys = malloc((2 * S.nvsep + 2) * sizeof *ys), *cut = NULL, *pcut = NULL;
  size_t ng = 0;
  if (!g || !ys) goto out;
  if (!S.blank)
    for (size_t i = 0; i < S.nvsep; i++)
      if (S.vsep[i].x0 > S.content.x0 && S.vsep[i].x1 < S.content.x1) g[ng++] = S.vsep[i];
  /* two white rectangles side by side, < 1 lh apart and sharing >= 80% of the taller one's rows, are one gutter
     with a speck in it (a footnote mark, dust, a rule's end): without this each made the other's column 0 wide */
  for (int again = 1; again;) {
    again = 0;
    for (size_t i = 0; i < ng && !again; i++)
      for (size_t j = 0; j < ng && !again; j++) {
        if (i == j || g[j].x0 < g[i].x1 || g[j].x0 - g[i].x1 >= lh) continue;
        uint32_t y0 = g[i].y0 > g[j].y0 ? g[i].y0 : g[j].y0, y1 = g[i].y1 < g[j].y1 ? g[i].y1 : g[j].y1;
        uint32_t hi = g[i].y1 - g[i].y0, hj = g[j].y1 - g[j].y0, hs = hi > hj ? hi : hj;
        if (y1 <= y0 || 10 * (uint64_t)(y1 - y0) < 8 * (uint64_t)hs) continue;
        g[i].x1 = g[j].x1; g[i].y0 = y0; g[i].y1 = y1;
        g[j] = g[--ng];
        again = 1;
      }
  }
  if (getenv("INKCMP_DEBUG")) for (size_t i = 0; i < ng; i++) fprintf(stderr, "cand %u %u %u %u\n", g[i].x0, g[i].y0, g[i].x1, g[i].y1);
  size_t k = 0;
  /* a column is >= 10 lh wide: from each gutter to the next gutter beside it (overlapping in y), or to the edge of
     the ink, on both sides (not the narrow strips of a scanner bed's noise or a table's columns) */
  int *keep = malloc((ng ? ng : 1) * sizeof *keep);
  if (!keep) goto out;
  /* tallest first: a gutter is measured against the page edge and the gutters already kept, never against a
     shorter white rectangle beside it (the white inside a figure 26 px from the real gutter vetoed it) */
  qsort(g, ng, sizeof *g, by_height);
  for (size_t i = 0; i < ng; i++) {
    int32_t lx = (int32_t)S.content.x0, rx = (int32_t)S.content.x1;
    for (size_t j = 0; j < ng; j++) {
      if (j == i || g[j].y1 <= g[i].y0 || g[j].y0 >= g[i].y1) continue;
      if (j < i ? !keep[j] : 10 * (uint64_t)(g[j].y1 - g[j].y0) < 8 * (uint64_t)(g[i].y1 - g[i].y0)) continue;
      if (g[j].x1 <= g[i].x0 && (int32_t)g[j].x1 > lx) lx = (int32_t)g[j].x1;
      if (g[j].x0 >= g[i].x1 && (int32_t)g[j].x0 < rx) rx = (int32_t)g[j].x0;
      if (g[j].x0 < g[i].x1 && g[j].x1 > g[i].x0) lx = rx = (int32_t)g[i].x0;   /* overlaps a kept one */
    }
    keep[i] = (int32_t)g[i].x0 - lx >= 10 * lh && rx - (int32_t)g[i].x1 >= 10 * lh;
  }
  k = 0;
  for (size_t i = 0; i < ng; i++) if (keep[i]) g[k++] = g[i];
  ng = k;
  free(keep);
  for (size_t i = 0; i < 2 * ng; i++) {             /* a gutter's end moves to the nearest white row within 1 lh */
    uint32_t *e = i % 2 ? &g[i / 2].y1 : &g[i / 2].y0, y = *e;
    for (uint32_t d = 0; d <= (uint32_t)lh; d++) {
      if (y >= d && y - d < H && r->row_start[y - d] == r->row_start[y - d + 1]) { *e = y - d; break; }
      if (y + d < H && r->row_start[y + d] == r->row_start[y + d + 1]) { *e = y + d; break; }
    }
  }
  for (size_t i = 0; i < ng; i++) {
    Box b = {(int32_t)g[i].x0, (int32_t)g[i].y0, (int32_t)g[i].x1, (int32_t)g[i].y1};
    if (getenv("INKCMP_DEBUG")) fprintf(stderr, "gutter %d %d %d %d\n", b.x0, b.y0, b.x1, b.y1);
    if (!push(gutters, b)) goto out;
  }
  size_t ny = 0;
  ys[ny++] = 0; ys[ny++] = H;
  for (size_t i = 0; i < ng; i++) { ys[ny++] = g[i].y0; ys[ny++] = g[i].y1; }
  qsort(ys, ny, sizeof *ys, cmp_u32);
  cut = malloc((ng + 2) * sizeof *cut); pcut = malloc((ng + 2) * sizeof *pcut);
  if (!cut || !pcut) goto out;
  size_t npc = (size_t)-1;
  uint32_t band0 = 0;
  for (size_t a = 0; a + 1 <= ny; a++) {
    size_t nc = 0;
    int last = a + 1 == ny;
    uint32_t y0 = ys[a], y1 = last ? H : ys[a + 1];
    if (!last && y1 <= y0) continue;
    if (!last)
      for (size_t i = 0; i < ng; i++)
        if (g[i].y0 <= y0 && g[i].y1 >= y1) cut[nc++] = (g[i].x0 + g[i].x1) / 2;
    qsort(cut, nc, sizeof *cut, cmp_u32);
    int same = !last && npc == nc && (nc == 0 || !memcmp(cut, pcut, nc * sizeof *cut));
    if (same) continue;
    if (npc != (size_t)-1 && y0 > band0)             /* close the previous band [band0, y0) at its cuts */
      for (size_t c = 0; c <= npc; c++) {
        Box b = {c ? (int32_t)pcut[c - 1] : 0, (int32_t)band0, c < npc ? (int32_t)pcut[c] : (int32_t)W, (int32_t)y0};
        if (!push(cells, b)) goto out;
      }
    if (last) break;
    memcpy(pcut, cut, nc * sizeof *cut); npc = nc; band0 = y0;
  }
  ok = 1;
out:
  free(g); free(ys); free(cut); free(pcut);
  ic_space_free(&S);
  return ok;
}

static int cmp_box_yx(const void *a, const void *b) {
  const Box *p = a, *q = b;
  if (p->y0 != q->y0) return (p->y0 > q->y0) - (p->y0 < q->y0);
  if (p->x0 != q->x0) return (p->x0 > q->x0) - (p->x0 < q->x0);
  if (p->x1 != q->x1) return (p->x1 > q->x1) - (p->x1 < q->x1);
  return (p->y1 > q->y1) - (p->y1 < q->y1);
}
static const Box *g_sortb;                           /* for cmp_ord_yx only */
static int cmp_ord_yx(const void *a, const void *b) {
  size_t i = *(const size_t *)a, j = *(const size_t *)b;
  int c = cmp_box_yx(&g_sortb[i], &g_sortb[j]);
  return c ? c : (i > j) - (i < j);
}
static int emit(IcLines *o, size_t *cap, Box b, int type) {
  if (o->n == *cap) {
    size_t c = *cap ? 2 * *cap : 64;
    IcLine *p = realloc(o->l, c * sizeof *p);
    if (!p) return 0;
    o->l = p; *cap = c;
  }
  IcLine L = {(uint32_t)b.x0, (uint32_t)b.y0, (uint32_t)b.x1, (uint32_t)b.y1, type};
  o->l[o->n++] = L;
  return 1;
}

/* step 6: one line per piece, column-agnostic. Rows: pieces overlapping vertically by >= half the smaller height.
   page_info: the top row in the outer 7%, the bottom row likewise. A display (math): a piece indented >= 1.5 lh on
   both sides of the widest piece that overlaps it in x within 10 lh above or below (the body text of its column).
   An equation number: a piece <= 4 lh wide, >= 2 lh beside a display in its row. Pieces of a row then join unless
   a vertical white separator of unit 3 lies in the gap (a gutter, a table's column gap); equation numbers and
   page_info never join. Display pieces that overlap in x and lie < 0.5 lh apart become one display. */
static int typed(const Boxes *lines, const Box *parent, double m, const Boxes *figs, uint32_t H, double lh, const IcRect *vs, size_t nvs,
                 const Boxes *vr, const Boxes *hr, IcLines *o) {
  size_t cap = 0, n = lines->n;
  const Box *b = lines->b;
  int *type = calloc(n ? n : 1, sizeof *type);
  long *refs = malloc((n ? n : 1) * sizeof *refs);
  size_t *row = malloc((n ? n : 1) * sizeof *row), *last = malloc((n ? n : 1) * sizeof *last), nrows = 0;
  if (!type || !row || !last || !refs) { free(type); free(row); free(last); free(refs); return 0; }
  for (size_t i = 0; i < n; i++) refs[i] = -1;
  for (size_t i = 0; i < n; i++) {                   /* rows */
    size_t k;
    for (k = 0; k < nrows; k++) {
      Box u = b[last[k]];
      int32_t ov = (u.y1 < b[i].y1 ? u.y1 : b[i].y1) - (u.y0 > b[i].y0 ? u.y0 : b[i].y0);
      int32_t mh = (u.y1 - u.y0) < (b[i].y1 - b[i].y0) ? (u.y1 - u.y0) : (b[i].y1 - b[i].y0);
      if (ov >= 0.5 * mh) break;
    }
    if (k == nrows) nrows++;
    row[i] = k; last[k] = i;
  }
  int32_t top = INT32_MAX, bot = -1; size_t rtop = 0, rbot = 0;
  for (size_t i = 0; i < n; i++) {
    if (b[i].y0 < top) { top = b[i].y0; rtop = row[i]; }
    if (b[i].y1 > bot) { bot = b[i].y1; rbot = row[i]; }
  }
  /* header and footer blocks: the rows beyond the outermost white gap >= G lh, wholly within the outer F of the page */
  const double F = 0.12, G = 3;                      /* dev: scan precision 0.544 -> 0.563, recall -0.2 points */
  int32_t foot_y = INT32_MAX, head_y = -1;           /* pieces with y0 >= foot_y or y1 <= head_y are page_info */
  if (n) {
    int32_t *ys = malloc(2 * n * sizeof *ys);        /* intervals sorted by y0: merge, then look for gaps */
    if (ys) {
      size_t *ord = malloc(n * sizeof *ord);
      if (ord) {
        for (size_t i = 0; i < n; i++) ord[i] = i;
        for (size_t a = 1; a < n; a++) { size_t v = ord[a], c = a; while (c > 0 && b[ord[c - 1]].y0 > b[v].y0) { ord[c] = ord[c - 1]; c--; } ord[c] = v; }
        int32_t cy1 = b[ord[0]].y1;
        for (size_t k = 1; k < n; k++) {
          Box q = b[ord[k]];
          if (q.y0 - cy1 >= G * lh) {                  /* a white band from cy1 to q.y0 */
            if (q.y0 >= (1 - F) * H && q.y0 < foot_y) foot_y = q.y0;
            if (cy1 <= F * H) head_y = cy1;
          }
          if (q.y1 > cy1) cy1 = q.y1;
        }
        free(ord);
      }
      free(ys);
    }
  }
  for (size_t i = 0; i < n; i++) {
    int pi = (row[i] == rtop && b[i].y1 < 0.07 * H) || (row[i] == rbot && b[i].y0 > 0.93 * H)
             || b[i].y0 >= foot_y || b[i].y1 <= head_y;
    if (pi) { type[i] = IC_PAGEINFO; continue; }
    long ref = -1;         /* the widest piece overlapping in x within 10 lh above or below: the body text */
    for (size_t j = 0; j < n; j++) {
      if (j == i || row[j] == row[i] || b[j].x1 <= b[i].x0 || b[j].x0 >= b[i].x1) continue;
      double dy = b[j].y1 <= b[i].y0 ? b[i].y0 - b[j].y1 : b[j].y0 >= b[i].y1 ? b[j].y0 - b[i].y1 : 0;
      if (dy > 10 * lh) continue;
      if (ref < 0 || b[j].x1 - b[j].x0 > b[ref].x1 - b[ref].x0) ref = (long)j;
    }
    type[i] = IC_TEXT;
    refs[i] = ref;
    /* a display is indented on both sides and not anchored at the left: a short text line (a paragraph's first
       line "If ..., then", "This completes the proof.") has a small left indent and a large right one */
    int32_t rl = b[i].x0, rr = b[i].x1;              /* the row's extent inside the reference, without a narrow
                                                        piece at either edge (an equation number): a display of
                                                        two parts far apart is centred as a whole */
    if (ref >= 0)
      for (size_t j = 0; j < n; j++) {
        if (j == i || row[j] != row[i] || b[j].x0 < b[ref].x0 || b[j].x1 > b[ref].x1) continue;
        if (b[j].x1 - b[j].x0 <= 4 * lh && (b[j].x1 >= b[ref].x1 - 1.5 * lh || b[j].x0 <= b[ref].x0 + 1.5 * lh)) continue;
        if (b[j].x0 < rl) rl = b[j].x0;
        if (b[j].x1 > rr) rr = b[j].x1;
      }
    if (ref >= 0 && b[i].x0 >= b[ref].x0 + 1.5 * lh && b[i].x1 <= b[ref].x1 - 1.5 * lh
        && ((rl - b[ref].x0) >= 0.35 * (b[ref].x1 - rr)
            || parent[i].y1 - parent[i].y0 >= 1.4 * m)) type[i] = IC_MATH;   /* or tall: a flush-left display's
                                                                                fractions, sums and integrals */
  }
  for (size_t i = 0; i < n; i++) {                   /* equation numbers beside a display, at the column's edge */
    if (type[i] == IC_PAGEINFO || b[i].x1 - b[i].x0 > 4 * lh) continue;
    long rf = refs[i];
    if (rf < 0 || !(b[i].x1 >= b[rf].x1 - 1.5 * lh || b[i].x0 <= b[rf].x0 + 1.5 * lh)) continue;
    for (size_t j = 0; j < n; j++) {
      if (j == i || row[j] != row[i] || type[j] != IC_MATH) continue;
      int32_t gap = b[i].x0 >= b[j].x1 ? b[i].x0 - b[j].x1 : b[j].x0 - b[i].x1;
      if (gap >= 2 * lh) { type[i] = IC_EQNUM; break; }
    }
    if (type[i] == IC_EQNUM) continue;
  }
  /* pieces of one row join unless a vertical white separator (unit 3: a gutter, a table's column gap)
     lies in the gap between them; equation numbers and page_info never join */
  int *gone = calloc(n ? n : 1, sizeof *gone);
  Box *bb = malloc((n ? n : 1) * sizeof *bb);
  if (!gone || !bb) { free(gone); free(bb); free(type); free(row); free(last); free(refs); return 0; }
  memcpy(bb, b, n * sizeof *bb);
  for (size_t i = 0; i < n; i++) {
      if (gone[i] || type[i] == IC_EQNUM || type[i] == IC_PAGEINFO) continue;
      for (;;) {                                       /* the nearest piece to the right in the row */
        long nx = -1;
        for (size_t j = 0; j < n; j++) {
          if (j == i || gone[j] || row[j] != row[i] || type[j] == IC_EQNUM || type[j] == IC_PAGEINFO) continue;
          if (bb[j].x0 >= bb[i].x1 && (nx < 0 || bb[j].x0 < bb[nx].x0)) nx = (long)j;
        }
        if (nx < 0) break;
        int32_t g0 = bb[i].x1, g1 = bb[nx].x0, y0 = bb[i].y0 < bb[nx].y0 ? bb[i].y0 : bb[nx].y0,
                y1 = bb[i].y1 > bb[nx].y1 ? bb[i].y1 : bb[nx].y1;
        int sep = 0;
        for (size_t v = 0; v < nvs && !sep; v++)
          sep = (int32_t)vs[v].x0 >= g0 - 1 && (int32_t)vs[v].x1 <= g1 + 1 && (int32_t)vs[v].y0 <= y0 && (int32_t)vs[v].y1 >= y1;
        if (!sep) sep = ruled(vr, g0, g1, y0, y1, lh);
        /* a gap of 3 lh is never a word gap (dev sweep 2/3/4/6: 3); the parts of one display may lie further apart */
        if (!sep && !(type[i] == IC_MATH && type[nx] == IC_MATH)) sep = g1 - g0 >= 3 * lh;
        if (sep) break;
        if (bb[nx].x1 > bb[i].x1) bb[i].x1 = bb[nx].x1;
        if (bb[nx].y0 < bb[i].y0) bb[i].y0 = bb[nx].y0;
        if (bb[nx].y1 > bb[i].y1) bb[i].y1 = bb[nx].y1;
        if (type[nx] != IC_MATH) type[i] = IC_TEXT;
        gone[nx] = 1;
      }
    }
  /* a row that keeps >= 3 boxes after joining is a table row: its pieces are text, never a display */
  size_t *cnt = calloc(nrows ? nrows : 1, sizeof *cnt);
  if (!cnt) { free(gone); free(bb); free(type); free(row); free(last); free(refs); return 0; }
  for (size_t i = 0; i < n; i++) if (!gone[i] && type[i] != IC_EQNUM && type[i] != IC_PAGEINFO) cnt[row[i]]++;
  /* ... when the row lies in a ruled table: between two horizontal rules, one above and one below within 30 lh,
     each over at least half of the box's width. A matrix or an aligned
     display has rows of >= 3 pieces too, and no such rules (a single line over it does not make a table: the
     Coxeter-Dynkin diagram above hep-th/9411188 p6's matrix); its pieces stay maths and become one display. */
  for (size_t i = 0; i < n; i++) {
    if (gone[i] || cnt[row[i]] < 3 || type[i] != IC_MATH) continue;
    int above = 0, below = 0, tab = 0;
    for (size_t k = 0; k < hr->n; k++) {
      const Box *q = &hr->b[k];
      int32_t ov = (q->x1 < bb[i].x1 ? q->x1 : bb[i].x1) - (q->x0 > bb[i].x0 ? q->x0 : bb[i].x0);
      if (2 * ov < bb[i].x1 - bb[i].x0) continue;
      if (q->y1 <= bb[i].y0 + lh && bb[i].y0 - q->y1 <= 30 * lh) above = 1;
      if (q->y0 >= bb[i].y1 - lh && q->y0 - bb[i].y1 <= 30 * lh) below = 1;
    }
    tab = above && below;
    if (tab) type[i] = IC_TEXT;
  }
  free(cnt);
  for (size_t i = 0; i < n; i++)
    if (!gone[i] && !emit(o, &cap, bb[i], type[i])) { free(gone); free(bb); free(type); free(row); free(last); free(refs); return 0; }
  free(gone); free(bb);
  free(type); free(row); free(last); free(refs);
  for (size_t i = 0; i < figs->n; i++) if (!emit(o, &cap, figs->b[i], IC_DIAGRAM)) return 0;
  /* displays. MathPix gives one box to a whole display: the rows of an aligned block, a matrix with its left side,
     consecutive numbered equations. Maths pieces that overlap in x and lie < 1 lh apart vertically, or that share
     rows and lie < 3 lh apart, are one display; a text or maths piece whose centre lies in a display belongs to it. */
  int merged = 1;
  while (merged) {
    merged = 0;
    for (size_t i = 0; i < o->n && !merged; i++) {
      if (o->l[i].type != IC_MATH) continue;
      for (size_t j = 0; j < o->n && !merged; j++) {
        IcLine *p = &o->l[i], *c = &o->l[j];
        if (j == i || (c->type != IC_MATH && c->type != IC_TEXT)) continue;
        int join = 0;
        double cx = (c->x0 + c->x1) / 2.0, cy = (c->y0 + c->y1) / 2.0;
        if (cx >= p->x0 && cx <= p->x1 && cy >= p->y0 && cy <= p->y1) join = 1;
        else if (c->type == IC_MATH) {
          double gy = c->y0 >= p->y1 ? (double)c->y0 - p->y1 : p->y0 >= c->y1 ? (double)p->y0 - c->y1 : 0;
          double gx = c->x0 >= p->x1 ? (double)c->x0 - p->x1 : p->x0 >= c->x1 ? (double)p->x0 - c->x1 : 0;
          join = (gx == 0 && gy < 1.0 * lh) || (gy == 0 && gx < 3 * lh);
        }
        if (!join) continue;
        if (c->x0 < p->x0) p->x0 = c->x0;
        if (c->y0 < p->y0) p->y0 = c->y0;
        if (c->x1 > p->x1) p->x1 = c->x1;
        if (c->y1 > p->y1) p->y1 = c->y1;
        memmove(c, c + 1, (o->n - j - 1) * sizeof *c);
        o->n--;
        merged = 1;
      }
    }
  }
  return 1;
}

void ic_lines_free(IcLines *x) { free(x->l); memset(x, 0, sizeof *x); }
const char *ic_line_type(int t) {
  static const char *names[] = {"text", "math", "equation_number", "page_info", "diagram"};
  return t >= 0 && t <= IC_DIAGRAM ? names[t] : "?";
}

int ic_lines(const uint8_t *grey, uint32_t W, uint32_t H, uint32_t threshold, IcLines *out) {
  memset(out, 0, sizeof *out);
  out->width = W; out->height = H;
  size_t N = (size_t)W * H;
  uint32_t th = threshold ? threshold : auto_threshold(grey, N);
  out->threshold = th;
  uint8_t *ink = malloc(N);
  Runs r = {0}; Box *cb = NULL;
  Boxes vrules = {0}, hrules = {0}, pcs = {0}, kept = {0}, figs = {0}, fin = {0}, finp = {0}, cells = {0}, gutters = {0};
  int32_t *hv = NULL;
  double hv_med = 10;
  GD3Blocks B; int have_b = 0;
  White Wt = {0};
  int ok = 0;
  if (!ink) goto done;
  tick(NULL);
  {
    size_t dark = 0;
    for (size_t i = 0; i < N; i++) { ink[i] = grey[i] < th; dark += ink[i]; }
    /* step 1b: polarity. Ink is the minority colour: on a page that is mostly dark (light text on a dark
       ground) the roles are swapped and the threshold is taken again on the inverted page. */
    if (!threshold && 2 * dark > N) {
      uint8_t *inv = malloc(N);
      if (!inv) goto done;
      for (size_t i = 0; i < N; i++) inv[i] = (uint8_t)(255 - grey[i]);
      th = auto_threshold(inv, N);
      for (size_t i = 0; i < N; i++) ink[i] = inv[i] < th;
      free(inv);
      out->threshold = th; out->inverted = 1;
    }
  }
  tick("threshold");
  /* step 2: components: specks, edge-touching (a gutter shadow's core), taller than half the page. (The
     prototype's column-density blanking before this changed nothing on Killing, 7 lines on Penev, and cost Hilbert
     precision 0.857 -> 0.821; removed 2026-11-17.) */
  if (!runs_of(ink, W, H, &r) || !label(&r, H, &cb)) goto done;
  {
    size_t nc = 0;
    hv = malloc((r.nrun ? r.nrun : 1) * sizeof *hv);
    if (!hv) goto done;
    for (size_t i = 0; i < r.nrun; i++) if (r.par[i] == i && cb[i].y1 - cb[i].y0 > 3) hv[nc++] = cb[i].y1 - cb[i].y0;
    qsort(hv, nc, sizeof *hv, cmp_i32);
    double med = nc ? hv[nc / 2] : 10;
    hv_med = med;
    for (uint32_t y = 0; y < H; y++)
      for (uint64_t k = r.row_start[y]; k < r.row_start[y + 1]; k++) {
        Box c = cb[find(r.par, (uint32_t)k)];
        int32_t cw = c.x1 - c.x0, ch = c.y1 - c.y0;
        if ((cw > ch ? cw : ch) < 0.25 * med || c.x0 <= 1 || c.x1 >= (int32_t)W - 1 || ch > 0.5 * H)
          memset(ink + (size_t)y * W + r.rs[2 * k], 0, r.rs[2 * k + 1] - r.rs[2 * k]);
      }
  }
  runs_free(&r); free(cb); cb = NULL;
  tick("components");
  /* step 2b: thin rules: horizontal runs >= 12 med and vertical runs >= 8 med whose thickness at their middle
     is <= 0.5 med are erased (table grids, frames around text, underlines) */
  {
    double med = hv_med;
    uint8_t *er = calloc(N, 1);
    if (!er) goto done;
    int32_t thin = (int32_t)(0.5 * med) > 1 ? (int32_t)(0.5 * med) : 1;
    /* runs are found on two-pixel strips (ink in y or y + 1, x or x + 1): a rule of 1-2 px at 300 dpi wanders
       between neighbouring pixel rows/columns, and a one-pixel scan sees it in pieces */
    uint8_t *strip = malloc((size_t)W + 1);
    if (!strip) { free(er); goto done; }
    for (uint32_t y = 0; y < H; y++) {
      const uint8_t *r0 = ink + (size_t)y * W, *r1 = y + 1 < H ? r0 + W : r0;
      for (uint32_t x = 0; x < W; x++) strip[x] = r0[x] | r1[x];   /* ink in row y or y + 1 */
      strip[W] = 0;
      for (uint32_t x = 0; x < W;) {
        const uint8_t *q = memchr(strip + x, 1, W - x);
        if (!q) break;
        uint32_t a = (uint32_t)(q - strip);
        x = a;
        while (strip[x]) x++;
        if (x - a >= 12 * med) {           /* a rule when at least half of its positions are <= thin thick: the
                                              crossings of a table's grid do not keep it (sampled points can all
                                              land on crossings) */
          uint32_t nthin = 0;
          for (uint32_t mx = a; mx < x; mx++) {
            int32_t tq = 0;
            for (int32_t yy = (int32_t)y; yy >= 0 && ink[(size_t)yy * W + mx] && tq <= thin; yy--) tq++;
            for (uint32_t yy = y + 1; yy < H && ink[(size_t)yy * W + mx] && tq <= thin; yy++) tq++;
            nthin += tq <= thin;
          }
          if (2 * nthin >= x - a) {
            for (uint32_t xx = a; xx < x; xx++) {
              er[(size_t)y * W + xx] = 1;
              if (y + 1 < H) er[(size_t)(y + 1) * W + xx] = 1;
            }
            Box hr = {(int32_t)a, (int32_t)y, (int32_t)x, (int32_t)y + 2};   /* a table's rule, for step 6 */
            if (!push(&hrules, hr)) { free(strip); free(er); goto done; }
          }
        }
      }
    }
    /* vertical runs, found row by row in memory order: vlen[x] = rows of the strip (x, x + 1) with ink ending
       above row y; a run ends where the strip is white, or at the page's bottom. The per-row update has no branch
       (ends[x] = the length of a run >= 4 med that ends here, else 0); the rare ends are then looked at. */
    {
      uint32_t *vlen = calloc((size_t)W, sizeof *vlen), *ends = malloc((size_t)W * sizeof *ends);
      if (!vlen || !ends) { free(vlen); free(ends); free(strip); free(er); goto done; }
      uint32_t minlen = (uint32_t)ceil(4 * med);
      for (uint32_t y = 0; y <= H; y++) {
        uint32_t any = 0;
        if (y < H) {
          const uint8_t *row = ink + (size_t)y * W;
          for (uint32_t x = 0; x + 1 < W; x++) strip[x] = row[x] | row[x + 1];
          strip[W - 1] = row[W - 1];
        } else memset(strip, 0, W);
        for (uint32_t x = 0; x < W; x++) {
          uint32_t old = vlen[x], on = strip[x];
          vlen[x] = on ? old + 1 : 0;
          uint32_t e = (!on && old >= minlen) ? old : 0;
          ends[x] = e; any |= e;
        }
        if (!any) continue;
        for (uint32_t x = 0; x < W; x++) {
          uint32_t len = ends[x];
          if (!len) continue;
          uint32_t a = y - len, nthin = 0;
          for (uint32_t my = a; my < y; my++) {
            int32_t tq = 0;
            for (int32_t xx = (int32_t)x; xx >= 0 && ink[(size_t)my * W + xx] && tq <= thin; xx--) tq++;
            for (uint32_t xx = x + 1; xx < W && ink[(size_t)my * W + xx] && tq <= thin; xx++) tq++;
            nthin += tq <= thin;
          }
          if (2 * nthin >= len) {
            for (uint32_t yy = a; yy < y; yy++) {
              er[(size_t)yy * W + x] = 1;
              if (x + 1 < W) er[(size_t)yy * W + x + 1] = 1;
            }
            Box vr = {(int32_t)x, (int32_t)a, (int32_t)x + 2, (int32_t)y};   /* a cell boundary from here on */
            if (!push(&vrules, vr)) { free(vlen); free(ends); free(strip); free(er); goto done; }
          }
        }
      }
      free(ends);
      free(vlen);
    }
    free(strip);
    for (size_t i = 0; i < N; i++) if (er[i]) ink[i] = 0;
    free(er);
    /* a vertical rule is a table's cell boundary only when a horizontal rule meets it (a grid). A norm bar, a
       determinant's bar, a tall bracket or an integral's stem is thin and tall too and meets none: it is still
       erased, but it cuts no piece and blocks no join. */
    {
      size_t kv = 0;
      int32_t tol = (int32_t)med > 2 ? (int32_t)med : 2;
      for (size_t i = 0; i < vrules.n; i++) {
        const Box *v = &vrules.b[i];
        int met = 0;
        for (size_t k = 0; k < hrules.n && !met; k++) {
          const Box *q = &hrules.b[k];
          met = q->x0 - tol <= v->x0 && q->x1 + tol >= v->x1 && q->y0 >= v->y0 - tol && q->y1 <= v->y1 + tol;
        }
        if (met) vrules.b[kv++] = *v;
      }
      vrules.n = kv;
    }
  }
  tick("rules");
  /* step 3: blocks on the cleaned page, then pieces */
  if (!runs_of(ink, W, H, &r)) goto done;
  if (gd3_blocks_opt(W, H, r.row_start, r.rs, 0, 0, 0, &B)) goto done;
  have_b = 1;
  out->lh = B.lh;
  double lh = B.lh;
  uint32_t need = (uint32_t)(1.5 * lh) > 1 ? (uint32_t)(1.5 * lh) : 1;
  tick("blocks");
  {                                                  /* one scan for all white rectangles the page needs */
    IcPage pgw = {W, H, r.row_start, r.rs, r.nrun};
    if (!white_candidates(&pgw, lh, 0.75, 8, &Wt)) goto done;
  }
  tick("white scan");
  if (!column_cells(&r, &Wt, W, H, lh, &cells, &gutters)) goto done;
  tick("gutters");
  if (cells.n <= 1) {
    for (size_t p = 0; p < B.npara; p++) {
      const GD3BPara *P = &B.para[p];
      for (uint32_t l = P->first_line; l < P->first_line + P->nlines; l++) {
        Box b = {(int32_t)B.line[l].x0, (int32_t)B.line[l].y0, (int32_t)B.line[l].x1, (int32_t)B.line[l].y1};
        if (!pieces_of(ink, W, b, need, lh, &vrules, &pcs)) goto done;
      }
    }
  } else {
    for (size_t c = 0; c < cells.n; c++) {           /* lines of each cell alone, at the page's lh */
      Box cb0 = cells.b[c];
      uint32_t cw = (uint32_t)(cb0.x1 - cb0.x0), ch = (uint32_t)(cb0.y1 - cb0.y0);
      uint8_t *sub = malloc((size_t)cw * ch);
      if (!sub) goto done;
      for (uint32_t y = 0; y < ch; y++) memcpy(sub + (size_t)y * cw, ink + (size_t)(cb0.y0 + y) * W + cb0.x0, cw);
      Runs rc;
      GD3Blocks C;
      int okc = runs_of(sub, cw, ch, &rc);
      free(sub);
      if (!okc) { runs_free(&rc); goto done; }
      if (!rc.nrun) { runs_free(&rc); continue; }
      uint32_t lq = (uint32_t)lh ? (uint32_t)lh : 1;
      if (gd3_blocks_opt(cw, ch, rc.row_start, rc.rs, 0, lq, lq, &C)) { runs_free(&rc); goto done; }
      runs_free(&rc);
      for (size_t p = 0; p < C.npara && okc; p++) {
        const GD3BPara *P = &C.para[p];
        for (uint32_t l = P->first_line; l < P->first_line + P->nlines && okc; l++) {
          Box b = {cb0.x0 + (int32_t)C.line[l].x0, cb0.y0 + (int32_t)C.line[l].y0,
                   cb0.x0 + (int32_t)C.line[l].x1, cb0.y0 + (int32_t)C.line[l].y1};
          okc = pieces_of(ink, W, b, need, lh, &vrules, &pcs);
        }
      }
      gd3_blocks_free(&C);
      if (!okc) goto done;
    }
  }
  tick("lines+pieces");
  /* step 4: m, clip, specks */
  free(hv); hv = malloc((pcs.n ? pcs.n : 1) * sizeof *hv);
  if (!hv) goto done;
  size_t nw = 0;
  for (size_t i = 0; i < pcs.n; i++)          /* wide pieces, not rules: a rule's thickness is no line height */
    if (pcs.b[i].x1 - pcs.b[i].x0 > 0.3 * W && pcs.b[i].y1 - pcs.b[i].y0 > 0.8 * hv_med) hv[nw++] = pcs.b[i].y1 - pcs.b[i].y0;
  qsort(hv, nw, sizeof *hv, cmp_i32);
  double m = nw ? hv[nw / 5] : lh;
  out->m = (uint32_t)m;
  if (pcs.n) {
    nw = 0;
    /* the right edge of the rightmost column: the 80th percentile of the right ends of pieces > 0.2 W wide that
       end in the right half */
    for (size_t i = 0; i < pcs.n; i++)
      if (pcs.b[i].x1 - pcs.b[i].x0 > 0.2 * W && pcs.b[i].x1 > 0.5 * W) hv[nw++] = pcs.b[i].x1;
    if (!nw) hv[nw++] = (int32_t)W;
    qsort(hv, nw, sizeof *hv, cmp_i32);
    double xr = hv[(size_t)(0.8 * (nw - 1) + 0.5)] + 0.5 * lh;   /* 80th percentile: the median cut equation
                                                                     numbers off pages of short lines */
    if (xr < 0.9 * W) xr = 0.9 * W;          /* the clip is for edge fringe: it never cuts into the inner 90% */
    for (size_t i = 0; i < pcs.n; i++) {
      Box b = pcs.b[i];
      if (b.x0 >= xr) continue;
      if (b.x1 > xr) {
        Box t;
        if (!tight(ink, W, b.x0, b.y0, b.x1 < (int32_t)xr ? b.x1 : (int32_t)xr, b.y1, &t)) continue;
        b = t;
      }
      int32_t bw = b.x1 - b.x0, bh = b.y1 - b.y0;
      if ((bw > bh ? bw : bh) < 0.4 * m) continue;
      if (!push(&kept, b)) goto done;
    }
  }
  tick("step4");
  /* step 5: figures, and the pieces they hold */
  if (!figures(ink, W, H, m, &figs)) goto done;
  for (size_t i = 0; i < kept.n; i++) {
    Box b = kept.b[i];
    double cx = (b.x0 + b.x1) / 2.0, cy = (b.y0 + b.y1) / 2.0;
    int in = 0;
    for (size_t f = 0; f < figs.n && !in; f++)
      in = figs.b[f].x0 <= cx && cx <= figs.b[f].x1 && figs.b[f].y0 <= cy && cy <= figs.b[f].y1;
    if (in) continue;
    size_t n0 = fin.n;
    if (!vsplit(ink, W, b, m, &fin)) goto done;
    for (size_t q = n0; q < fin.n; q++) if (!push(&finp, b)) goto done;   /* the piece each line was cut from */
  }
  {                                                  /* sort the lines by (y0, x0, ...), their parents with them */
    size_t *ord = malloc((fin.n ? fin.n : 1) * sizeof *ord);
    Box *t1 = malloc((fin.n ? fin.n : 1) * sizeof *t1), *t2 = malloc((fin.n ? fin.n : 1) * sizeof *t2);
    if (!ord || !t1 || !t2) { free(ord); free(t1); free(t2); goto done; }
    for (size_t i = 0; i < fin.n; i++) ord[i] = i;
    g_sortb = fin.b;
    qsort(ord, fin.n, sizeof *ord, cmp_ord_yx);
    for (size_t i = 0; i < fin.n; i++) { t1[i] = fin.b[ord[i]]; t2[i] = finp.b[ord[i]]; }
    memcpy(fin.b, t1, fin.n * sizeof *t1); memcpy(finp.b, t2, fin.n * sizeof *t2);
    free(ord); free(t1); free(t2);
  }
  /* step 6 */
  tick("figures+split");
  /* step 6: the vertical white separators of unit 3 (gutters, table column gaps), then types */
  IcSpace S;                                         /* separators: >= 1 lh wide and >= 8 lh tall (dev sweep) */
  IcPage pg6 = {W, H, r.row_start, r.rs, r.nrun};
  if (!tall_white(&pg6, &Wt, lh, 1.0, 8.0, &S)) goto done;
  /* the gutters of step 3a are separators too: a row's pieces never join across a column's gutter */
  IcRect *sep = malloc((S.nvsep + gutters.n + 1) * sizeof *sep);
  if (!sep) { ic_space_free(&S); goto done; }
  memcpy(sep, S.vsep, S.nvsep * sizeof *sep);
  for (size_t i = 0; i < gutters.n; i++) {
    IcRect q = {(uint32_t)gutters.b[i].x0, (uint32_t)gutters.b[i].y0, (uint32_t)gutters.b[i].x1, (uint32_t)gutters.b[i].y1};
    sep[S.nvsep + i] = q;
  }
  tick("ic_space");
  int okt = typed(&fin, finp.b, m, &figs, H, lh, sep, S.nvsep + gutters.n, &vrules, &hrules, out);
  free(sep);
  ic_space_free(&S);
  tick("typed");
  if (!okt) goto done;
  ok = 1;
done:
  free(ink); free(cb); free(hv); free(Wt.r);
  runs_free(&r);
  if (have_b) gd3_blocks_free(&B);
  free(vrules.b); free(hrules.b); free(pcs.b); free(kept.b); free(figs.b); free(fin.b); free(finp.b); free(cells.b); free(gutters.b);
  if (!ok) { ic_lines_free(out); return 5; }
  return 0;
}

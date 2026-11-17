/* inkcmp unit 7: lines of one grey page (CONTRACT.md unit 7). A port of the T-0148 prototype
   (work/run-files/blob-lines/blob_lines.py, PIL); where the prototype used PIL box averages, the same rounding is
   kept here. It agreed with the prototype on all 5501 lines of Killing, Hilbert and Penev before two measured
   changes (contract unit 7): the edge-column blanking is gone, and a display's rows merge across its number.
   Steps are numbered as in the contract. */
#include "lines.h"
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
  double wb = 0, sb = 0, best = 0; int t = 0;
  for (int i = 0; i < 256; i++) {
    wb += hist[i];
    if (wb == 0 || wb == N) continue;
    sb += i * hist[i];
    double mb = sb / wb, mf = (tot - sb) / (N - wb), v = wb * (N - wb) * (mb - mf) * (mb - mf);
    if (v > best) { best = v; t = i; }
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
static int pieces_of(const uint8_t *ink, uint32_t w, Box r, uint32_t need, Boxes *out) {
  int32_t bw = r.x1 - r.x0, bh = r.y1 - r.y0;
  if (bw <= 0 || bh <= 0) return 1;
  uint32_t *cnt = calloc((size_t)bw, sizeof *cnt);
  if (!cnt) return 0;
  for (int32_t y = r.y0; y < r.y1; y++) {
    const uint8_t *row = ink + (size_t)y * w + r.x0;
    for (int32_t x = 0; x < bw; x++) cnt[x] += row[x];
  }
  int32_t start = -1, end = 0; uint32_t white = 0;
  for (int32_t i = 0; i < bw + (int32_t)need; i++) {
    int on = i < bw && (double)cnt[i] * 255.0 / bh >= 0.5;
    if (on) {
      if (start < 0) start = i;
      white = 0; end = i + 1;
    } else if (start >= 0) {
      if (++white >= need) {
        Box t;
        if (tight(ink, w, r.x0 + start, r.y0, r.x0 + end, r.y1, &t) && !push(out, t)) { free(cnt); return 0; }
        start = -1;
      }
    }
  }
  free(cnt);
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

static int cmp_box_yx(const void *a, const void *b) {
  const Box *p = a, *q = b;
  if (p->y0 != q->y0) return (p->y0 > q->y0) - (p->y0 < q->y0);
  if (p->x0 != q->x0) return (p->x0 > q->x0) - (p->x0 < q->x0);
  if (p->x1 != q->x1) return (p->x1 > q->x1) - (p->x1 < q->x1);
  return (p->y1 > q->y1) - (p->y1 < q->y1);
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
static int typed(const Boxes *lines, const Boxes *figs, uint32_t H, double lh, const IcRect *vs, size_t nvs,
                    IcLines *o) {
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
    if (ref >= 0 && b[i].x0 >= b[ref].x0 + 1.5 * lh && b[i].x1 <= b[ref].x1 - 1.5 * lh) type[i] = IC_MATH;
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
  for (size_t i = 0; i < n; i++) if (!gone[i] && cnt[row[i]] >= 3 && type[i] == IC_MATH) type[i] = IC_TEXT;
  free(cnt);
  for (size_t i = 0; i < n; i++)
    if (!gone[i] && !emit(o, &cap, bb[i], type[i])) { free(gone); free(bb); free(type); free(row); free(last); free(refs); return 0; }
  free(gone); free(bb);
  free(type); free(row); free(last); free(refs);
  for (size_t i = 0; i < figs->n; i++) if (!emit(o, &cap, figs->b[i], IC_DIAGRAM)) return 0;
  /* display rows: math pieces that overlap in x and are < 0.5 lh apart vertically are one display */
  int merged = 1;
  while (merged) {
    merged = 0;
    for (size_t i = 0; i < o->n && !merged; i++) {
      if (o->l[i].type != IC_MATH) continue;
      for (size_t j = i + 1; j < o->n && !merged; j++) {
        IcLine *p = &o->l[i], *c = &o->l[j];
        if (c->type != IC_MATH || c->x1 <= p->x0 || c->x0 >= p->x1) continue;
        double gap = c->y0 >= p->y1 ? (double)c->y0 - p->y1 : p->y0 >= c->y1 ? (double)p->y0 - c->y1 : 0;
        if (gap < 0.5 * lh) {
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
  Boxes pcs = {0}, kept = {0}, figs = {0}, fin = {0};
  int32_t *hv = NULL;
  double hv_med = 10;
  GD3Blocks B; int have_b = 0;
  int ok = 0;
  if (!ink) goto done;
  for (size_t i = 0; i < N; i++) ink[i] = grey[i] < th;
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
  /* step 2b: thin rules: horizontal runs >= 12 med and vertical runs >= 8 med whose thickness at their middle
     is <= 0.5 med are erased (table grids, frames around text, underlines) */
  {
    double med = hv_med;
    uint8_t *er = calloc(N, 1);
    if (!er) goto done;
    int32_t thin = (int32_t)(0.5 * med) > 1 ? (int32_t)(0.5 * med) : 1;
    /* runs are found on two-pixel strips (ink in y or y + 1, x or x + 1): a rule of 1-2 px at 300 dpi wanders
       between neighbouring pixel rows/columns, and a one-pixel scan sees it in pieces */
#define INK2H(xx, yy) (ink[(size_t)(yy) * W + (xx)] || ((yy) + 1 < H && ink[(size_t)((yy) + 1) * W + (xx)]))
#define INK2V(xx, yy) (ink[(size_t)(yy) * W + (xx)] || ((xx) + 1 < W && ink[(size_t)(yy) * W + (xx) + 1]))
    for (uint32_t y = 0; y < H; y++) {
      for (uint32_t x = 0; x < W;) {
        if (!INK2H(x, y)) { x++; continue; }
        uint32_t a = x;
        while (x < W && INK2H(x, y)) x++;
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
          if (2 * nthin >= x - a)
            for (uint32_t xx = a; xx < x; xx++) {
              er[(size_t)y * W + xx] = 1;
              if (y + 1 < H) er[(size_t)(y + 1) * W + xx] = 1;
            }
        }
      }
    }
    for (uint32_t x = 0; x < W; x++)
      for (uint32_t y = 0; y < H;) {
        if (!INK2V(x, y)) { y++; continue; }
        uint32_t a = y;
        while (y < H && INK2V(x, y)) y++;
        if (y - a >= 4 * med) {
          uint32_t nthin = 0;
          for (uint32_t my = a; my < y; my++) {
            int32_t tq = 0;
            for (int32_t xx = (int32_t)x; xx >= 0 && ink[(size_t)my * W + xx] && tq <= thin; xx--) tq++;
            for (uint32_t xx = x + 1; xx < W && ink[(size_t)my * W + xx] && tq <= thin; xx++) tq++;
            nthin += tq <= thin;
          }
          if (2 * nthin >= y - a)
            for (uint32_t yy = a; yy < y; yy++) {
              er[(size_t)yy * W + x] = 1;
              if (x + 1 < W) er[(size_t)yy * W + x + 1] = 1;
            }
        }
      }
#undef INK2H
#undef INK2V
    for (size_t i = 0; i < N; i++) if (er[i]) ink[i] = 0;
    free(er);
  }
  /* step 3: blocks on the cleaned page, then pieces */
  if (!runs_of(ink, W, H, &r)) goto done;
  if (gd3_blocks_opt(W, H, r.row_start, r.rs, 0, 0, 0, &B)) goto done;
  have_b = 1;
  out->lh = B.lh;
  double lh = B.lh;
  uint32_t need = (uint32_t)(1.5 * lh) > 1 ? (uint32_t)(1.5 * lh) : 1;
  for (size_t p = 0; p < B.npara; p++) {
    const GD3BPara *P = &B.para[p];
    for (uint32_t l = P->first_line; l < P->first_line + P->nlines; l++) {
      Box b = {(int32_t)B.line[l].x0, (int32_t)B.line[l].y0, (int32_t)B.line[l].x1, (int32_t)B.line[l].y1};
      if (!pieces_of(ink, W, b, need, &pcs)) goto done;
    }
  }
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
  /* step 5: figures, and the pieces they hold */
  if (!figures(ink, W, H, m, &figs)) goto done;
  for (size_t i = 0; i < kept.n; i++) {
    Box b = kept.b[i];
    double cx = (b.x0 + b.x1) / 2.0, cy = (b.y0 + b.y1) / 2.0;
    int in = 0;
    for (size_t f = 0; f < figs.n && !in; f++)
      in = figs.b[f].x0 <= cx && cx <= figs.b[f].x1 && figs.b[f].y0 <= cy && cy <= figs.b[f].y1;
    if (!in && !vsplit(ink, W, b, m, &fin)) goto done;
  }
  qsort(fin.b, fin.n, sizeof *fin.b, cmp_box_yx);
  /* step 6 */
  /* step 6: the vertical white separators of unit 3 (gutters, table column gaps), then types */
  IcSpace S;
  IcPage pg = {W, H, r.row_start, r.rs, r.nrun};
  IcSpaceParams sp; ic_space_defaults(&sp); sp.max_sep = 1024;
  if (ic_space(&pg, (uint32_t)lh ? (uint32_t)lh : 1, &sp, &S)) goto done;
  int okt = typed(&fin, &figs, H, lh, S.vsep, S.nvsep, out);
  ic_space_free(&S);
  if (!okt) goto done;
  ok = 1;
done:
  free(ink); free(cb); free(hv);
  runs_free(&r);
  if (have_b) gd3_blocks_free(&B);
  free(pcs.b); free(kept.b); free(figs.b); free(fin.b);
  if (!ok) { ic_lines_free(out); return 5; }
  return 0;
}

/* inkcmp unit 3: maximal white rectangles and the separators derived from them.

   ic_white is the scan of src/gd3_white.c on the page in memory: h[x] = white pixels in column x
   ending at row y; one stack pass per row gives every rectangle that cannot move left, right or
   up; it is maximal when row y + 1 has ink under it (or y is the last row). O(width) per row.

   ic_space: content box and margins from the runs; separator candidates are the maximal
   rectangles clipped to the content box; a greedy cover by decreasing area keeps the
   non-redundant ones (candidates x max_sep comparisons). */
#include "white.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

void ic_rects_free(IcRects *r) { free(r->r); memset(r, 0, sizeof *r); }

int ic_white(const IcPage *pg, uint32_t min_w, uint32_t min_h, IcRects *out) {
  memset(out, 0, sizeof *out);
  uint32_t w = pg->width, h = pg->height;
  uint8_t *cur = malloc(w), *nxt = malloc(w);
  uint32_t *hh = calloc((size_t)w + 1, 4), *below = malloc(((size_t)w + 1) * 4);
  uint32_t *st_x = malloc(((size_t)w + 1) * 4), *st_h = malloc(((size_t)w + 1) * 4);
  int rc = 0;
  if (!cur || !nxt || !hh || !below || !st_x || !st_h) rc = 5;
  if (!rc) {
    memset(cur, 0, w);
    for (uint64_t i = pg->row_start[0]; i < pg->row_start[1]; i++)
      memset(cur + pg->spans[2 * i], 1, pg->spans[2 * i + 1] - pg->spans[2 * i]);
  }
  for (uint32_t y = 0; !rc && y < h; y++) {
    for (uint32_t x = 0; x < w; x++) hh[x] = cur[x] ? 0 : hh[x] + 1;
    if (y + 1 < h) {
      memset(nxt, 0, w);
      for (uint64_t i = pg->row_start[y + 1]; i < pg->row_start[y + 2]; i++)
        memset(nxt + pg->spans[2 * i], 1, pg->spans[2 * i + 1] - pg->spans[2 * i]);
      uint32_t b = w;
      below[w] = w;
      for (uint32_t x = w; x-- > 0;) { if (nxt[x]) b = x; below[x] = b; }
    } else {
      for (uint32_t x = 0; x <= w; x++) below[x] = x;
    }
    uint32_t sp = 0;
    for (uint32_t x = 0; x <= w; x++) {
      uint32_t hx = hh[x], start = x;
      while (sp && st_h[sp - 1] > hx) {
        sp--;
        uint32_t bx = st_x[sp], bh = st_h[sp];
        if (below[bx] < x) {
          out->total++;
          if (x - bx >= min_w && bh >= min_h) {
            if (out->n == out->cap) {
              size_t c = out->cap ? 2 * out->cap : 1024;
              IcRect *q = realloc(out->r, c * sizeof *q);
              if (!q) { rc = 5; break; }
              out->r = q; out->cap = c;
            }
            out->r[out->n++] = (IcRect){ bx, y + 1 - bh, x, y + 1 };
          }
        }
        start = bx;
      }
      if (rc) break;
      if (hx && (!sp || st_h[sp - 1] < hx)) { st_x[sp] = start; st_h[sp] = hx; sp++; }
    }
    uint8_t *t = cur; cur = nxt; nxt = t;
  }
  free(cur); free(nxt); free(hh); free(below); free(st_x); free(st_h);
  if (rc) ic_rects_free(out);
  return rc;
}

void ic_space_defaults(IcSpaceParams *p) {
  p->vsep_w = 1.0; p->vsep_h = 4.0; p->hsep_w = 4.0; p->hsep_h = 1.0; p->overlap = 0.2; p->max_sep = 64;
}
void ic_space_free(IcSpace *s) { free(s->vsep); free(s->hsep); memset(s, 0, sizeof *s); }

static uint64_t area(const IcRect *r) { return (uint64_t)(r->x1 - r->x0) * (r->y1 - r->y0); }
static int by_area(const void *a, const void *b) {
  const IcRect *p = a, *q = b;
  uint64_t ap = area(p), aq = area(q);
  if (ap != aq) return ap > aq ? -1 : 1;
  if (p->y0 != q->y0) return p->y0 < q->y0 ? -1 : 1;
  if (p->x0 != q->x0) return p->x0 < q->x0 ? -1 : 1;
  if (p->y1 != q->y1) return p->y1 < q->y1 ? -1 : 1;
  if (p->x1 != q->x1) return p->x1 < q->x1 ? -1 : 1;
  return 0;
}
static int by_x(const void *a, const void *b) {
  const IcRect *p = a, *q = b;
  if (p->x0 != q->x0) return p->x0 < q->x0 ? -1 : 1;
  if (p->y0 != q->y0) return p->y0 < q->y0 ? -1 : 1;
  if (p->x1 != q->x1) return p->x1 < q->x1 ? -1 : 1;
  if (p->y1 != q->y1) return p->y1 < q->y1 ? -1 : 1;
  return 0;
}
static int by_y(const void *a, const void *b) {
  const IcRect *p = a, *q = b;
  if (p->y0 != q->y0) return p->y0 < q->y0 ? -1 : 1;
  if (p->x0 != q->x0) return p->x0 < q->x0 ? -1 : 1;
  if (p->y1 != q->y1) return p->y1 < q->y1 ? -1 : 1;
  if (p->x1 != q->x1) return p->x1 < q->x1 ? -1 : 1;
  return 0;
}
static uint32_t px(double f, uint32_t lh) {
  double v = ceil(f * lh);
  return v < 1 ? 1 : v > 4e9 ? 4000000000u : (uint32_t)v;
}
typedef struct { uint32_t vw, vh, hw, hh, cx0, cy0, cx1, cy1; } Rules;
/* 0 none, 1 vertical, 2 horizontal: orientation, sizes and sides (CONTRACT.md, unit 3) */
static int classify(const IcRect *r, const Rules *u) {
  if (r->x0 >= r->x1 || r->y0 >= r->y1) return 0;
  uint32_t rw = r->x1 - r->x0, rh = r->y1 - r->y0;
  if (rh >= rw) return rw >= u->vw && rh >= u->vh && r->x0 > u->cx0 && r->x1 < u->cx1 ? 1 : 0;
  return rw >= u->hw && rh >= u->hh && r->y0 > u->cy0 && r->y1 < u->cy1 ? 2 : 0;
}

int ic_space(const IcPage *pg, uint32_t lh, const IcSpaceParams *P, IcSpace *S) {
  memset(S, 0, sizeof *S);
  uint32_t w = pg->width, h = pg->height;
  /* content box: first and last row with runs; min x0 and max x1 over the rows' end runs */
  uint32_t cx0 = w, cx1 = 0, cy0 = h, cy1 = 0;
  for (uint32_t y = 0; y < h; y++) {
    uint64_t i0 = pg->row_start[y], i1 = pg->row_start[y + 1];
    if (i0 == i1) continue;
    if (cy0 == h) cy0 = y;
    cy1 = y + 1;
    if (pg->spans[2 * i0] < cx0) cx0 = pg->spans[2 * i0];
    if (pg->spans[2 * i1 - 1] > cx1) cx1 = pg->spans[2 * i1 - 1];
  }
  if (cy0 == h) {                               /* no ink: one maximal rectangle, the page */
    S->blank = 1; S->white_rects = 1;
    return 0;
  }
  S->content = (IcRect){ cx0, cy0, cx1, cy1 };
  S->top = cy0; S->bottom = h - cy1; S->left = cx0; S->right = w - cx1;

  Rules u = { px(P->vsep_w, lh), px(P->vsep_h, lh), px(P->hsep_w, lh), px(P->hsep_h, lh), cx0, cy0, cx1, cy1 };
  IcRects R;
  if (ic_white(pg, u.vw < u.hw ? u.vw : u.hw, u.vh < u.hh ? u.vh : u.hh, &R)) return 5;
  S->white_rects = R.total;
  /* candidates: clipped to the content box, of one of the two classes; in place */
  size_t nc = 0;
  for (size_t i = 0; i < R.n; i++) {
    IcRect r = R.r[i];
    if (r.x0 < cx0) r.x0 = cx0;
    if (r.x1 > cx1) r.x1 = cx1;
    if (r.y0 < cy0) r.y0 = cy0;
    if (r.y1 > cy1) r.y1 = cy1;
    if (classify(&r, &u)) R.r[nc++] = r;        /* nc <= i */
  }
  if (nc) qsort(R.r, nc, sizeof *R.r, by_area);   /* R.r is NULL when the page has no rectangle of the size */
  /* one greedy cover over both classes, by decreasing area. Against a kept rectangle k a candidate c is
       cut in y when k spans c's full width, else cut in x when k spans c's full height (the larger remaining
       part stays; ties: the upper / left one), and the comparison starts again with the cut c;
       otherwise c is dropped when the overlap is > overlap x area(c).
     A cut c must still be of a class. Cost per candidate: at most kept x (cuts + 1), cuts <= 2 x kept. */
  uint32_t max = P->max_sep ? P->max_sep : 1;
  IcRect *k = malloc(max * sizeof *k), *v = malloc(max * sizeof *v), *hz = malloc(max * sizeof *hz);
  if (!k || !v || !hz) { free(k); free(v); free(hz); ic_rects_free(&R); return 5; }
  size_t nk = 0, nv = 0, nh = 0;
  for (size_t i = 0; i < nc && nk < max; i++) {
    IcRect c = R.r[i];
    int cls = classify(&c, &u);
    for (size_t j = 0; cls && j < nk; j++) {
      uint32_t x0 = c.x0 > k[j].x0 ? c.x0 : k[j].x0, x1 = c.x1 < k[j].x1 ? c.x1 : k[j].x1;
      uint32_t y0 = c.y0 > k[j].y0 ? c.y0 : k[j].y0, y1 = c.y1 < k[j].y1 ? c.y1 : k[j].y1;
      if (x0 >= x1 || y0 >= y1) continue;
      if (k[j].x0 <= c.x0 && k[j].x1 >= c.x1) {
        uint32_t up = y0 - c.y0, low = c.y1 - y1;
        if (up >= low) c.y1 = c.y0 + up; else c.y0 = c.y1 - low;
      } else if (k[j].y0 <= c.y0 && k[j].y1 >= c.y1) {
        uint32_t left = x0 - c.x0, right = c.x1 - x1;
        if (left >= right) c.x1 = c.x0 + left; else c.x0 = c.x1 - right;
      } else {
        if ((double)((uint64_t)(x1 - x0) * (y1 - y0)) > P->overlap * (double)area(&c)) cls = 0;
        continue;
      }
      cls = classify(&c, &u);
      j = (size_t)-1;                           /* again from the first kept rectangle */
    }
    if (!cls) continue;
    k[nk++] = c;
    if (cls == 1) v[nv++] = c; else hz[nh++] = c;
  }
  free(k); ic_rects_free(&R);
  qsort(v, nv, sizeof *v, by_x);
  qsort(hz, nh, sizeof *hz, by_y);
  S->vsep = v; S->nvsep = nv; S->hsep = hz; S->nhsep = nh;
  return 0;
}

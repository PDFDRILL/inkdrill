/* inkcmp rules: the pixel stage of the judge's rule comparison (inkdrill/judge.py rule_ink, CONTRACT.md unit 5).
   Per job: a grey page sampled on the 400 grid, the ink no glyph quad covers in a region, its 8-connected
   components that are rule-like (judge.rule_like: >= 8 px wide, >= 3x as wide as tall, <= 6 px tall), each with
   the number of its pixels that have a 4-neighbour of COVERED ink (judge.rule_ink free()). Equal to the Python it
   replaces (tests-c/test_inkcmp_glyphs.py R1 holds the two against each other on random pages).

   stdin:
     INKRULES1
     page <path.pgm> <ink> <up> <cx0> <cy0>   400-grid pixel (x, y) is the sample (x*up + up/2 - cx0, y*up + up/2 - cy0)
     job <xa> <ya> <xb> <yb>                  a region, inclusive; the quads that follow belong to it
     quad <x0> <y0> <x1> <y1>                 a pixel is covered when its centre lies in the quad, edges included
     ...                                      (more page / job / quad lines)
     end
   stdout, per job in input order: `job <k> <components>`, then per component (in the order of its first run,
   rows top-down) `comp <pixels> <touch> <runs>` and its runs `run <y> <x0> <x1>`; then `end`. */
#define _POSIX_C_SOURCE 200809L
#include "glyphs.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

typedef struct { int w, h; uint8_t *g; } Img;
static int load_pgm(const char *path, Img *im) {
  FILE *fp = fopen(path, "rb");
  if (!fp) return 0;
  int w, h, mx, c;
  if (fscanf(fp, "P5 %d %d %d", &w, &h, &mx) != 3 || mx != 255 || w <= 0 || h <= 0) { fclose(fp); return 0; }
  c = fgetc(fp);
  (void)c;
  im->g = malloc((size_t)w * h);
  if (!im->g || fread(im->g, 1, (size_t)w * h, fp) != (size_t)w * h) { fclose(fp); free(im->g); im->g = NULL; return 0; }
  fclose(fp);
  im->w = w; im->h = h;
  return 1;
}

typedef struct { int y, x0, x1; } Run;
static int find(int *par, int k) {
  while (par[k] != k) { par[k] = par[par[k]]; k = par[k]; }
  return k;
}

typedef struct { double x0, y0, x1, y1; } Quad;
typedef struct {
  Img im; int ink, up, cx0, cy0;
  int xa, ya, xb, yb;
  Quad *q; size_t nq, cq;
} Job;

/* the sample of 400-grid pixel (x, y): 1 ink, 0 white, -1 outside the image */
static int sample(const Job *j, int x, int y) {
  long tx = (long)x * j->up + j->up / 2 - j->cx0, ty = (long)y * j->up + j->up / 2 - j->cy0;
  if (tx < 0 || ty < 0 || tx >= j->im.w || ty >= j->im.h) return -1;
  return j->im.g[(size_t)ty * j->im.w + tx] < j->ink;
}

static int run_job(Job *j, long k, FILE *out) {
  int W = j->xb - j->xa + 1, H = j->yb - j->ya + 1;
  if (W <= 0 || H <= 0) { fprintf(out, "job %ld 0\n", k); return 1; }
  /* cover map: quads painted, as judge.cover_map */
  uint8_t *cov = calloc((size_t)W * H, 1), *ng = malloc((size_t)W * H);
  if (!cov || !ng) { free(cov); free(ng); return 0; }
  for (size_t i = 0; i < j->nq; i++) {
    const Quad *q = &j->q[i];
    long a = (long)ceil(q->x0 - 0.5), b = (long)floor(q->x1 - 0.5), c = (long)ceil(q->y0 - 0.5), d = (long)floor(q->y1 - 0.5);
    if (a < j->xa) a = j->xa;
    if (b > j->xb) b = j->xb;
    if (c < j->ya) c = j->ya;
    if (d > j->yb) d = j->yb;
    for (long y = c; y <= d && a <= b; y++) memset(cov + (size_t)(y - j->ya) * W + (a - j->xa), 1, (size_t)(b - a + 1));
  }
  /* ink with no cover: 1; covered ink: 2 (for touch) */
  for (int y = 0; y < H; y++)
    for (int x = 0; x < W; x++) {
      int s = sample(j, j->xa + x, j->ya + y);
      ng[(size_t)y * W + x] = s == 1 ? (cov[(size_t)y * W + x] ? 2 : 1) : 0;
    }
  free(cov);
  /* runs of 1, rows top-down */
  Run *rr = NULL; size_t nr = 0, cr = 0;
  size_t *rowstart = malloc(((size_t)H + 1) * sizeof *rowstart);
  if (!rowstart) { free(ng); return 0; }
  for (int y = 0; y < H; y++) {
    rowstart[y] = nr;
    const uint8_t *row = ng + (size_t)y * W;
    for (int x = 0; x < W;) {
      if (row[x] != 1) { x++; continue; }
      int a = x;
      while (x < W && row[x] == 1) x++;
      if (nr == cr) { cr = cr ? 2 * cr : 1024; Run *t = realloc(rr, cr * sizeof *t); if (!t) { free(rr); free(rowstart); free(ng); return 0; } rr = t; }
      rr[nr++] = (Run){y, a, x - 1};
    }
  }
  rowstart[H] = nr;
  int *par = malloc((nr ? nr : 1) * sizeof *par);
  int *bx0 = malloc((nr ? nr : 1) * sizeof *bx0), *bx1 = malloc((nr ? nr : 1) * sizeof *bx1);
  int *by0 = malloc((nr ? nr : 1) * sizeof *by0), *by1 = malloc((nr ? nr : 1) * sizeof *by1);
  if (!par || !bx0 || !bx1 || !by0 || !by1) { free(rr); free(rowstart); free(ng); free(par); free(bx0); free(bx1); free(by0); free(by1); return 0; }
  for (size_t i = 0; i < nr; i++) par[i] = (int)i;
  for (int y = 1; y < H; y++) {                      /* 8-connected: runs of adjacent rows within one column */
    size_t p = rowstart[y - 1], pe = rowstart[y];
    for (size_t i = rowstart[y]; i < rowstart[y + 1]; i++) {
      while (p < pe && rr[p].x1 < rr[i].x0 - 1) p++;
      for (size_t t = p; t < pe && rr[t].x0 <= rr[i].x1 + 1; t++) {
        int a = find(par, (int)i), b = find(par, (int)t);
        if (a != b) { if (a < b) par[b] = a; else par[a] = b; }
      }
    }
  }
  for (size_t i = 0; i < nr; i++) { bx0[i] = by0[i] = 1 << 30; bx1[i] = by1[i] = -1; }
  for (size_t i = 0; i < nr; i++) {
    int r = find(par, (int)i);
    if (rr[i].x0 < bx0[r]) bx0[r] = rr[i].x0;
    if (rr[i].x1 > bx1[r]) bx1[r] = rr[i].x1;
    if (rr[i].y < by0[r]) by0[r] = rr[i].y;
    if (rr[i].y > by1[r]) by1[r] = rr[i].y;
  }
  long ncomp = 0;
  for (size_t i = 0; i < nr; i++) {
    if (find(par, (int)i) != (int)i) continue;
    int w = bx1[i] - bx0[i] + 1, h = by1[i] - by0[i] + 1;
    if (w >= 8 && w >= 3 * h && h <= 6) ncomp++;
  }
  fprintf(out, "job %ld %ld\n", k, ncomp);
  for (size_t i = 0; i < nr; i++) {                  /* roots in increasing index = order of the first run */
    if (find(par, (int)i) != (int)i) continue;
    int w = bx1[i] - bx0[i] + 1, h = by1[i] - by0[i] + 1;
    if (!(w >= 8 && w >= 3 * h && h <= 6)) continue;
    long pix = 0, touch = 0, runs = 0;
    for (size_t t = i; t < nr && rr[t].y <= by1[i]; t++) {
      if (find(par, (int)t) != (int)i) continue;
      runs++;
      pix += rr[t].x1 - rr[t].x0 + 1;
      for (int x = rr[t].x0; x <= rr[t].x1; x++) {
        int y = rr[t].y, hit = 0;
        static const int D[4][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
        for (int d = 0; d < 4 && !hit; d++) {
          int nx = x + D[d][0], ny = y + D[d][1];
          hit = nx >= 0 && ny >= 0 && nx < W && ny < H && ng[(size_t)ny * W + nx] == 2;
        }
        touch += hit;
      }
    }
    fprintf(out, "comp %ld %ld %ld\n", pix, touch, runs);
    for (size_t t = i; t < nr && rr[t].y <= by1[i]; t++)
      if (find(par, (int)t) == (int)i) fprintf(out, "run %d %d %d\n", j->ya + rr[t].y, j->xa + rr[t].x0, j->xa + rr[t].x1);
  }
  free(rr); free(rowstart); free(ng); free(par); free(bx0); free(bx1); free(by0); free(by1);
  return 1;
}

int ir_main(FILE *in, FILE *out, FILE *errf) {
  char line[4096];
  Job j; memset(&j, 0, sizeof j);
  int have_job = 0, rc = 2;
  long k = 0;
  if (!fgets(line, sizeof line, in) || strncmp(line, "INKRULES1", 9)) { fprintf(errf, "inkcmp rules: no INKRULES1 header\n"); return 2; }
  while (fgets(line, sizeof line, in)) {
    int end = !strncmp(line, "end", 3), isjob = !strncmp(line, "job ", 4), ispage = !strncmp(line, "page ", 5);
    if ((end || isjob || ispage) && have_job) {
      if (!run_job(&j, k++, out)) goto done;
      have_job = 0; j.nq = 0;
    }
    if (end) { rc = 0; break; }
    if (ispage) {
      char path[3000];
      free(j.im.g); j.im.g = NULL;
      if (sscanf(line + 5, "%2999s %d %d %d %d", path, &j.ink, &j.up, &j.cx0, &j.cy0) != 5 || j.up < 1 || !load_pgm(path, &j.im)) {
        fprintf(errf, "inkcmp rules: cannot read page\n"); goto done;
      }
      continue;
    }
    if (isjob) {
      if (!j.im.g || sscanf(line + 4, "%d %d %d %d", &j.xa, &j.ya, &j.xb, &j.yb) != 4) { fprintf(errf, "inkcmp rules: bad job line\n"); goto done; }
      have_job = 1;
      continue;
    }
    if (!strncmp(line, "quad ", 5)) {
      Quad q;
      if (!have_job || sscanf(line + 5, "%lf %lf %lf %lf", &q.x0, &q.y0, &q.x1, &q.y1) != 4) { fprintf(errf, "inkcmp rules: bad quad line\n"); goto done; }
      if (j.nq == j.cq) { j.cq = j.cq ? 2 * j.cq : 64; Quad *t = realloc(j.q, j.cq * sizeof *t); if (!t) goto done; j.q = t; }
      j.q[j.nq++] = q;
      continue;
    }
    fprintf(errf, "inkcmp rules: unknown line\n");
    goto done;
  }
  if (rc) fprintf(errf, "inkcmp rules: no end line\n");
  else fprintf(out, "end %ld\n", k);
done:
  free(j.im.g); free(j.q);
  return rc;
}

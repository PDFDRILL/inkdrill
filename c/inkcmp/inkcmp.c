/* inkcmp: the C judge of inkdrill, built on GLYPHDRILL3. Contracts: inkcmp/CONTRACT.md.
   Subcommands so far are the test surfaces of units 1-3; each reads one PGM page from stdin. */
#define _POSIX_C_SOURCE 199309L
#include "page.h"
#include "white.h"
#include "layout/blocks.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now_ms(void) {
  struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}
static int usage(void) {
  fputs("usage: inkcmp rle    [--threshold T] < page.pgm\n"
        "       inkcmp blocks [--threshold T] [--gx PX] [--gy PX] < page.pgm\n"
        "       inkcmp white  [--threshold T] < page.pgm\n"
        "       inkcmp space  [--threshold T] [--lh PX] [--vsep-w F] [--vsep-h F] [--hsep-w F] [--hsep-h F]\n"
        "                     [--overlap F] [--max-sep N] < page.pgm\n"
        "       T: 1..256 (default 154), ink = pixel < T\n", stderr);
  return 2;
}
/* a whole decimal number 0..max and nothing else */
static int whole(const char *s, uint32_t max, uint32_t *v) {
  if (*s < '0' || *s > '9') return 0;
  char *e; errno = 0; unsigned long x = strtoul(s, &e, 10);
  if (errno || *e || x > max) return 0;
  *v = (uint32_t)x; return 1;
}
static int real(const char *s, double *v) {
  if (!((*s >= '0' && *s <= '9') || *s == '.')) return 0;
  char *e; errno = 0; double x = strtod(s, &e);
  if (errno || *e || !(x >= 0) || x > 1e6) return 0;
  *v = x; return 1;
}
static int load_error(int rc) {
  if (rc == 2) fputs("inkcmp: invalid P5 pgm header (requires maxval=255) or threshold\n", stderr);
  else if (rc == 4) fputs("inkcmp: short input, trailing bytes or a second page\n", stderr);
  else fputs("inkcmp: out of memory\n", stderr);
  return rc;
}

int main(int argc, char **argv) {
  if (argc < 2) return usage();
  const char *cmd = argv[1];
  int is_rle = !strcmp(cmd, "rle"), is_blocks = !strcmp(cmd, "blocks"), is_white = !strcmp(cmd, "white"),
      is_space = !strcmp(cmd, "space");
  if (!is_rle && !is_blocks && !is_white && !is_space) return usage();
  uint32_t t = 154, gx = 0, gy = 0, lh = 0;
  IcSpaceParams sp; ic_space_defaults(&sp);
  for (int i = 2; i < argc; i++) {
    int ok = 0, more = i + 1 < argc;
    if (!strcmp(argv[i], "--threshold") && more) ok = whole(argv[++i], 256, &t) && t > 0;
    else if (is_blocks && !strcmp(argv[i], "--gx") && more) ok = whole(argv[++i], 1000000, &gx);
    else if (is_blocks && !strcmp(argv[i], "--gy") && more) ok = whole(argv[++i], 1000000, &gy);
    else if (is_space && !strcmp(argv[i], "--lh") && more) ok = whole(argv[++i], 1000000, &lh) && lh > 0;
    else if (is_space && !strcmp(argv[i], "--vsep-w") && more) ok = real(argv[++i], &sp.vsep_w);
    else if (is_space && !strcmp(argv[i], "--vsep-h") && more) ok = real(argv[++i], &sp.vsep_h);
    else if (is_space && !strcmp(argv[i], "--hsep-w") && more) ok = real(argv[++i], &sp.hsep_w);
    else if (is_space && !strcmp(argv[i], "--hsep-h") && more) ok = real(argv[++i], &sp.hsep_h);
    else if (is_space && !strcmp(argv[i], "--overlap") && more) ok = real(argv[++i], &sp.overlap) && sp.overlap <= 1;
    else if (is_space && !strcmp(argv[i], "--max-sep") && more) ok = whole(argv[++i], 100000, &sp.max_sep) && sp.max_sep > 0;
    if (!ok) return usage();
  }
  double t0 = now_ms();
  IcPage pg;
  int rc = ic_page_from_pgm(stdin, t, &pg);
  if (rc) return load_error(rc);
  double t1 = now_ms();
  static char obuf[1 << 16]; setvbuf(stdout, obuf, _IOFBF, sizeof obuf);

  if (is_rle) {
    rc = ic_page_write_rle(&pg, stdout);
    if (rc) { fputs("inkcmp: write error\n", stderr); return rc; }
  } else if (is_blocks) {
    /* the output of src/gd3_blocks.c, line for line (contract S1) */
    GD3Blocks B;
    if (gd3_blocks_opt(pg.width, pg.height, pg.row_start, pg.spans, 0, gx, gy, &B)) return load_error(5);
    printf("GD3BLOCKS1 %u %u gx %u gy %u lh %u\n", pg.width, pg.height, B.gx, B.gy, B.lh);
    for (size_t p = 0; p < B.npara; p++) {
      const GD3BPara *P = &B.para[p];
      printf("paragraph %u %u %u %u %u %u\n", P->id, P->x0, P->y0, P->x1, P->y1, P->nlines);
      for (uint32_t l = P->first_line; l < P->first_line + P->nlines; l++)
        printf("line %u %u %u %u %u\n", B.line[l].x0, B.line[l].y0, B.line[l].x1, B.line[l].y1, P->id);
    }
    printf("end %zu %zu\n", B.npara, B.nline);
    gd3_blocks_free(&B);
  } else if (is_white) {
    IcRects R;
    if (ic_white(&pg, 1, 1, &R)) return load_error(5);
    printf("GD3WHITE1 %u %u\n", pg.width, pg.height);
    for (size_t i = 0; i < R.n; i++) printf("%u %u %u %u\n", R.r[i].x0, R.r[i].y0, R.r[i].x1, R.r[i].y1);
    printf("end %llu\n", (unsigned long long)R.total);
    ic_rects_free(&R);
  } else {
    if (!lh) {                                    /* contract S2: the line height of layout/blocks */
      GD3Blocks B;
      if (gd3_blocks_opt(pg.width, pg.height, pg.row_start, pg.spans, 0, 0, 0, &B)) return load_error(5);
      lh = B.lh ? B.lh : 1;
      gd3_blocks_free(&B);
    }
    IcSpace S;
    if (ic_space(&pg, lh, &sp, &S)) return load_error(5);
    printf("INKSPACE1 %u %u lh %u white %llu\n", pg.width, pg.height, lh, (unsigned long long)S.white_rects);
    if (S.blank) puts("blank");
    else {
      printf("content %u %u %u %u\n", S.content.x0, S.content.y0, S.content.x1, S.content.y1);
      printf("margin %u %u %u %u\n", S.top, S.bottom, S.left, S.right);
      for (size_t i = 0; i < S.nvsep; i++) printf("vsep %u %u %u %u\n", S.vsep[i].x0, S.vsep[i].y0, S.vsep[i].x1, S.vsep[i].y1);
      for (size_t i = 0; i < S.nhsep; i++) printf("hsep %u %u %u %u\n", S.hsep[i].x0, S.hsep[i].y0, S.hsep[i].x1, S.hsep[i].y1);
    }
    printf("end %zu %zu\n", S.nvsep, S.nhsep);
    ic_space_free(&S);
  }
  if (fflush(stdout) || ferror(stdout)) { fputs("inkcmp: write error\n", stderr); return 3; }
  double t2 = now_ms();
  fprintf(stderr, "inkcmp %s: %u x %u, %llu runs, load %.1f ms, %s %.1f ms\n", cmd, pg.width, pg.height,
          (unsigned long long)pg.nruns, t1 - t0, cmd, t2 - t1);
  ic_page_free(&pg);
  return 0;
}

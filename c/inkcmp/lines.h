/* inkcmp unit 7: MathPix-shaped line geometry of one grey page, from ink alone. Contract: CONTRACT.md unit 7. */
#ifndef INKCMP_LINES_H
#define INKCMP_LINES_H
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

enum { IC_TEXT, IC_MATH, IC_EQNUM, IC_PAGEINFO, IC_DIAGRAM };
typedef struct { uint32_t x0, y0, x1, y1; int type; } IcLine;     /* half-open box, page pixels */
typedef struct {
  IcLine *l; size_t n;
  uint32_t width, height, threshold, lh, m;   /* ink threshold used, blocks' lh, the line height m (step 4) */
} IcLines;

/* one P5 PGM (maxval 255) as grey bytes: 0, 1 at a clean end of input (no page), 2 bad header, 4 short, 5 memory */
int ic_grey_from_pgm(FILE *in, uint8_t **grey, uint32_t *w, uint32_t *h);
/* threshold 0 = automatic (step 1). 0, or 5 when out of memory. */
int ic_lines(const uint8_t *grey, uint32_t w, uint32_t h, uint32_t threshold, IcLines *out);
void ic_lines_free(IcLines *x);
const char *ic_line_type(int t);
#endif

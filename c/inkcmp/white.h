/* inkcmp unit 3: white space of one page. Contract: inkcmp/CONTRACT.md, W1-W6. */
#ifndef INKCMP_WHITE_H
#define INKCMP_WHITE_H
#include "page.h"

typedef struct { uint32_t x0, y0, x1, y1; } IcRect;      /* half-open */
typedef struct { IcRect *r; size_t n, cap; uint64_t total; } IcRects;   /* total: all maximal rectangles of the page */

/* The maximal white rectangles with width >= min_w and height >= min_h, in the order gd3-white
   finds them (min 1, 1: all of them). 0, or 5 when out of memory. */
int ic_white(const IcPage *page, uint32_t min_w, uint32_t min_h, IcRects *out);
void ic_rects_free(IcRects *r);

typedef struct {
  double vsep_w, vsep_h;   /* vertical separator: width >= vsep_w x lh, height >= vsep_h x lh */
  double hsep_w, hsep_h;   /* horizontal separator: width >= hsep_w x lh, height >= hsep_h x lh */
  double overlap;          /* the greedy cover's overlap limit (CONTRACT.md) */
  uint32_t max_sep;        /* at most this many in total */
} IcSpaceParams;
void ic_space_defaults(IcSpaceParams *p);

typedef struct {
  int blank;                              /* no ink on the page: nothing else is set */
  IcRect content;                         /* bounding box of all ink */
  uint32_t top, bottom, left, right;      /* margins */
  IcRect *vsep; size_t nvsep;             /* sorted by x0, y0, x1, y1 */
  IcRect *hsep; size_t nhsep;             /* sorted by y0, x0, y1, x1 */
  uint64_t white_rects;                   /* count of all maximal white rectangles */
} IcSpace;
int ic_space(const IcPage *page, uint32_t lh, const IcSpaceParams *params, IcSpace *out);
void ic_space_free(IcSpace *s);
#endif

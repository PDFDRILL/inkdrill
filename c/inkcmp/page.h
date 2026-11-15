/* inkcmp unit 1: one PGM page as ink runs. Contract: inkcmp/CONTRACT.md, L1-L4. */
#ifndef INKCMP_PAGE_H
#define INKCMP_PAGE_H
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef struct {
  uint32_t width, height;
  uint64_t *row_start;   /* height + 1 entries; row y has runs [row_start[y], row_start[y + 1]) */
  uint32_t *spans;       /* x0, x1 pairs, half-open, sorted per row */
  uint64_t nruns;
} IcPage;

/* 0, or 2 invalid header / threshold, 4 short input or trailing bytes, 5 out of memory.
   On an error the page is empty (nothing to free). */
int ic_page_from_pgm(FILE *in, uint32_t threshold, IcPage *page);
/* the page as a GD3RLE1 stream; 0, or 3 on a write error */
int ic_page_write_rle(const IcPage *page, FILE *out);
void ic_page_free(IcPage *page);
#endif

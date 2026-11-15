/* inkcmp unit 1: PGM (P5, maxval 255) -> ink runs. The header rules and the ink rule
   (pixel < threshold) are those of src/gd3_adapter.c; tests/test_inkcmp_load.py compares the
   two programs byte for byte. No file-scope state, so two pages fit in one process. */
#include "page.h"
#include <ctype.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

static int token(FILE *in, char *s, size_t n) {
  int c; size_t p = 0;
  do {
    c = fgetc(in);
    if (c == '#') { while (c != '\n' && c != EOF) c = fgetc(in); }
  } while (c != EOF && isspace((unsigned char)c));
  if (c == EOF) return 0;
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
  if (errno || *end || a > UINT32_MAX || a == 0) return 0;
  *v = (uint32_t)a; return 1;
}

void ic_page_free(IcPage *p) { free(p->row_start); free(p->spans); memset(p, 0, sizeof *p); }

int ic_page_from_pgm(FILE *in, uint32_t t, IcPage *p) {
  memset(p, 0, sizeof *p);
  char tok[64]; uint32_t w = 0, h = 0, maxval = 0;
  if (t == 0 || t > 256 || !token(in, tok, sizeof tok) || strcmp(tok, "P5")
      || !token(in, tok, sizeof tok) || !number(tok, &w)
      || !token(in, tok, sizeof tok) || !number(tok, &h)
      || !token(in, tok, sizeof tok) || !number(tok, &maxval) || maxval != 255
      || w > 1000000 || h > 1000000) return 2;
  unsigned char *row = malloc(w);
  uint64_t *rs = malloc(((size_t)h + 1) * sizeof *rs);
  size_t cap = 4096 + (size_t)w, n = 0;          /* in runs; a row has at most (w + 1) / 2 */
  uint32_t *sp = malloc(cap * 2 * sizeof *sp);
  int rc = 0;
  if (!row || !rs || !sp) rc = 5;
  for (uint32_t y = 0; !rc && y < h; y++) {
    rs[y] = n;
    if (n + w > cap) {
      cap = 2 * cap + w;
      uint32_t *q = realloc(sp, cap * 2 * sizeof *sp);
      if (!q) { rc = 5; break; }
      sp = q;
    }
    if (fread(row, 1, w, in) != w) { rc = 4; break; }
    uint32_t x = 0;
    while (x < w) {
      while (x < w && row[x] >= t) x++;
      if (x == w) break;
      uint32_t a = x;
      while (x < w && row[x] < t) x++;
      sp[2 * n] = a; sp[2 * n + 1] = x; n++;
    }
  }
  if (!rc && fgetc(in) != EOF) rc = 4;           /* trailing bytes or a second page */
  free(row);
  if (rc) { free(rs); free(sp); return rc; }
  rs[h] = n;
  p->width = w; p->height = h; p->row_start = rs; p->spans = sp; p->nruns = n;
  return 0;
}

int ic_page_write_rle(const IcPage *p, FILE *out) {
  /* little-endian u32 fields, one buffer per row */
  size_t cap = 4 + 8 * ((size_t)p->width / 2 + 1);
  unsigned char *b = malloc(cap < 16 ? 16 : cap);
  if (!b) return 5;
#define PUT32(q, v) ((q)[0] = (unsigned char)(v), (q)[1] = (unsigned char)((v) >> 8), \
                     (q)[2] = (unsigned char)((v) >> 16), (q)[3] = (unsigned char)((v) >> 24))
  memcpy(b, "GD3RLE1\0", 8); PUT32(b + 8, p->width); PUT32(b + 12, p->height);
  int rc = fwrite(b, 1, 16, out) == 16 ? 0 : 3;
  for (uint32_t y = 0; !rc && y < p->height; y++) {
    uint64_t i0 = p->row_start[y], i1 = p->row_start[y + 1];
    uint32_t n = (uint32_t)(i1 - i0);
    unsigned char *q = b;
    PUT32(q, n); q += 4;
    for (uint64_t i = i0; i < i1; i++) {
      uint32_t a = p->spans[2 * i], e = p->spans[2 * i + 1];
      PUT32(q, a); PUT32(q + 4, e); q += 8;
    }
    if (fwrite(b, 1, (size_t)(q - b), out) != (size_t)(q - b)) rc = 3;
  }
#undef PUT32
  free(b);
  if (!rc && fflush(out)) rc = 3;
  return rc;
}

/* Test program for contract L4: two pages loaded in one process.
   usage: inkcmp-selftest-two THRESHOLD PGM_A PGM_B   (paths, e.g. /dev/fd/N of two pipes)
   It loads A, then B, and writes A's GD3RLE1 stream followed by B's to stdout. Both pages are
   in memory at the same time when the first is written. */
#include "page.h"
#include <stdlib.h>

int main(int argc, char **argv) {
  if (argc != 4) return 2;
  uint32_t t = (uint32_t)strtoul(argv[1], 0, 10);
  FILE *fa = fopen(argv[2], "rb"), *fb = fopen(argv[3], "rb");
  if (!fa || !fb) return 2;
  IcPage a, b;
  int rc = ic_page_from_pgm(fa, t, &a);
  if (rc) return rc;
  rc = ic_page_from_pgm(fb, t, &b);
  if (rc) return rc;
  if (ic_page_write_rle(&a, stdout) || ic_page_write_rle(&b, stdout)) return 3;
  ic_page_free(&a); ic_page_free(&b);
  fclose(fa); fclose(fb);
  return 0;
}

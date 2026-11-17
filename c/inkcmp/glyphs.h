/* inkcmp glyphs: glyph outlines from embedded font programs, rasterised at a page origin (CONTRACT.md unit 5,
   "glyph-raster route"). The C port of U9 (inkdrill/type1.py -> charstring.py -> scan.py) plus a CFF reader with
   Type 2 charstrings for the fonts a producer embedded as Type 1C. C17, no library beyond libc and libm. */
#ifndef INKCMP_GLYPHS_H
#define INKCMP_GLYPHS_H
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef struct IgFont IgFont;

/* A Type 1 program (PFB, or PFA/raw with hex or binary eexec) or a bare CFF font (as `mutool extract` writes it).
   NULL on failure with a reason in err. */
IgFont *ig_font_load(const char *path, char *err, size_t errn);
void ig_font_free(IgFont *f);
int ig_has_glyph(const IgFont *f, const char *name);
const char *ig_font_name(const IgFont *f);
int ig_font_is_cff(const IgFont *f);
const double *ig_font_matrix(const IgFont *f);       /* six values */

/* The named glyph, placed with its origin at device (X, Y): a font-unit point (u, v) goes through the FontMatrix to
   (fu, fv), then to (X + fu*m[0] + fv*m[2] - x0, Y + fu*m[1] + fv*m[3] - y0) in a w x h window whose top-left page
   pixel is (x0, y0). Pixels are filled by U9's rule (scan.py): cubics flattened to 0.25 px, non-zero winding,
   sampled at pixel centres. mask gets 1 for ink, 0 else. Returns 0, or -1 when the font has no such glyph or its
   charstring fails (the reason in err). */
int ig_glyph_mask(IgFont *f, const char *name, const double m[4], double X, double Y, int x0, int y0, int w, int h,
                  uint8_t *mask, char *err, size_t errn);

/* (xor pixels, solid pixels: XOR pixels whose 2x2 block lies in the XOR, largest 8-connected XOR piece) of two
   w x h masks; judge.py score() */
void ig_score(const uint8_t *a, const uint8_t *b, int w, int h, long *xor_px, long *solid, long *piece);

/* `inkcmp glyphs`: the job protocol (CONTRACT.md); returns the exit code */
int ig_main(FILE *in, FILE *out, FILE *errf);
/* `inkcmp rules` (rules.c): the pixel stage of the judge's rule comparison */
int ir_main(FILE *in, FILE *out, FILE *errf);
#endif

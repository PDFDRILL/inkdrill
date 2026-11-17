/* inkcmp glyphs (glyphs.h). The Type 1 half is U9 ported operation for operation: type1.py's parse (PFB segments,
   PFA hex, eexec 55665/4, lenIV, `dup i n RD`, `/name n RD`), charstring.py's interpreter (its closepath, flex,
   othersubr and seac behaviour included), scan.py's flatten (0.25 px, 16 splits) and rasterize (non-zero winding,
   pixel centres, int(x + 0.5) span ends). Equality with the Python on whole fonts is tests-c/test_inkcmp_glyphs.py's
   first oracle. The CFF half has no Python counterpart; its oracle is the same glyph read both ways (a TeX Type 1
   font, and the Type 1C that Ghostscript's pdfwrite makes of it). */
#define _POSIX_C_SOURCE 200809L
#include "glyphs.h"
#include "glyphtab.h"
#include <math.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

static void say(char *err, size_t n, const char *fmt, ...) {
  if (!err || !n) return;
  va_list ap; va_start(ap, fmt); vsnprintf(err, n, fmt, ap); va_end(ap);
}

/* ---- outlines: contours of segments in font units (charstring.Segment) ---- */
typedef struct { double x, y, c1x, c1y, c2x, c2y; int curve; } Seg;
typedef struct { Seg *s; size_t n, cap; size_t *start; size_t nc, ccap; } Outline;   /* contour k: start[k]..start[k+1] */

static int seg_push(Outline *o, Seg s) {
  if (o->n == o->cap) {
    size_t c = o->cap ? 2 * o->cap : 64; Seg *p = realloc(o->s, c * sizeof *p);
    if (!p) return 0;
    o->s = p; o->cap = c;
  }
  o->s[o->n++] = s;
  return 1;
}
static int contour_push(Outline *o, size_t at) {
  if (o->nc + 1 >= o->ccap) {
    size_t c = o->ccap ? 2 * o->ccap : 16; size_t *p = realloc(o->start, c * sizeof *p);
    if (!p) return 0;
    o->start = p; o->ccap = c;
  }
  o->start[o->nc++] = at;
  return 1;
}
static void outline_free(Outline *o) { free(o->s); free(o->start); memset(o, 0, sizeof *o); }

/* the open contour lives in `cur`; _close moves it into the glyph */
typedef struct {
  Outline g;
  Seg *cur; size_t ncur, capcur;
  double x, y;
} Pen;
static int cur_push(Pen *p, Seg s) {
  if (p->ncur == p->capcur) {
    size_t c = p->capcur ? 2 * p->capcur : 32; Seg *q = realloc(p->cur, c * sizeof *q);
    if (!q) return 0;
    p->cur = q; p->capcur = c;
  }
  p->cur[p->ncur++] = s;
  return 1;
}
static Seg pt(double x, double y) { Seg s = {x, y, 0, 0, 0, 0, 0}; return s; }
/* charstring._Runner._close: a contour of > 1 segment is kept, closed back to its first point */
static int pen_close(Pen *p) {
  if (p->ncur > 1) {
    if (p->cur[p->ncur - 1].x != p->cur[0].x || p->cur[p->ncur - 1].y != p->cur[0].y)
      if (!cur_push(p, pt(p->cur[0].x, p->cur[0].y))) return 0;
    if (!contour_push(&p->g, p->g.n)) return 0;
    for (size_t i = 0; i < p->ncur; i++) if (!seg_push(&p->g, p->cur[i])) return 0;
  }
  p->ncur = 0;
  return 1;
}
static int pen_move(Pen *p) { if (!pen_close(p)) return 0; return cur_push(p, pt(p->x, p->y)); }
static int pen_line(Pen *p, double dx, double dy) { p->x += dx; p->y += dy; return cur_push(p, pt(p->x, p->y)); }
static int pen_curve(Pen *p, double dx1, double dy1, double dx2, double dy2, double dx3, double dy3) {
  Seg s; s.curve = 1;
  s.c1x = p->x + dx1; s.c1y = p->y + dy1;
  s.c2x = s.c1x + dx2; s.c2y = s.c1y + dy2;
  p->x = s.c2x + dx3; p->y = s.c2y + dy3;
  s.x = p->x; s.y = p->y;
  return cur_push(p, s);
}
static void pen_free(Pen *p) { outline_free(&p->g); free(p->cur); memset(p, 0, sizeof *p); }

/* ---- fonts ---- */
typedef struct { const char *name; uint32_t gid; } NameIx;
typedef struct { const uint8_t *p; size_t n; } Bytes;
struct IgFont {
  int cff;
  char name[256];
  double fm[6];
  uint8_t *raw;                          /* the file (CFF), or the decrypted private part (Type 1) */
  uint8_t *dec;                          /* Type 1: charstring and Subr bodies decrypted, at their offsets in raw */
  size_t nraw;
  /* glyphs: name -> index into cs[] */
  NameIx *ix; size_t nix;
  Bytes *cs; size_t ncs;                 /* charstrings (Type 1: decrypted, lenIV dropped) */
  Bytes *subrs; size_t nsubrs;           /* Type 1 Subrs, CFF local Subrs */
  Bytes *gsubrs; size_t ngsubrs;         /* CFF global Subrs */
  double nominal_w;
  char **names;                          /* owned name strings (Type 1, CFF custom strings) */
  size_t nnames;
  Outline *cache; uint8_t *cached;       /* per glyph index: outline, and 0 none / 1 ok / 2 failed */
};

static int cmp_ix(const void *a, const void *b) { return strcmp(((const NameIx *)a)->name, ((const NameIx *)b)->name); }
static long find_glyph(const IgFont *f, const char *name) {
  NameIx k = {name, 0};
  const NameIx *r = bsearch(&k, f->ix, f->nix, sizeof *f->ix, cmp_ix);
  return r ? (long)r->gid : -1;
}

static uint8_t *slurp(const char *path, size_t *n) {
  FILE *fp = fopen(path, "rb");
  if (!fp) return NULL;
  size_t cap = 1 << 16, len = 0;
  uint8_t *b = malloc(cap);
  while (b) {
    if (len == cap) { uint8_t *q = realloc(b, cap *= 2); if (!q) { free(b); b = NULL; break; } b = q; }
    size_t r = fread(b + len, 1, cap - len, fp);
    if (!r) break;
    len += r;
  }
  fclose(fp);
  *n = len;
  return b;
}

/* ---- Type 1 (type1.py) ---- */
static void t1_decrypt(const uint8_t *in, size_t n, uint16_t r, uint8_t *out) {
  for (size_t i = 0; i < n; i++) {
    uint8_t c = in[i];
    out[i] = (uint8_t)(c ^ (r >> 8));
    r = (uint16_t)((c + r) * 52845u + 22719u);
  }
}
static const uint8_t *memfind(const uint8_t *h, size_t hn, const char *needle) {
  size_t k = strlen(needle);
  if (k > hn) return NULL;
  for (size_t i = 0; i + k <= hn; i++) if (h[i] == (uint8_t)needle[0] && !memcmp(h + i, needle, k)) return h + i;
  return NULL;
}
static int is_ws(uint8_t c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f' || c == 0; }
static int is_hex(uint8_t c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }
static int hexv(uint8_t c) { return c <= '9' ? c - '0' : (c | 32) - 'a' + 10; }
static int is_delim(uint8_t c) { return is_ws(c) || c == '/' || c == '{' || c == '}' || c == '[' || c == ']' || c == '(' || c == ')' || c == '<' || c == '>'; }

/* [ \t]+ then digits; returns pointer past them or NULL */
static const uint8_t *sp_digits(const uint8_t *p, const uint8_t *e, long *v) {
  const uint8_t *q = p;
  while (q < e && (*q == ' ' || *q == '\t')) q++;
  if (q == p || q >= e || *q < '0' || *q > '9') return NULL;
  long x = 0;
  while (q < e && *q >= '0' && *q <= '9') x = x * 10 + (*q++ - '0');
  *v = x;
  return q;
}
/* [ \t]+ then 1-12 non-space characters then exactly one ' ': the binary starts after it (type1.py _ENTRY) */
static const uint8_t *sp_token_sp(const uint8_t *p, const uint8_t *e) {
  const uint8_t *q = p;
  while (q < e && (*q == ' ' || *q == '\t')) q++;
  if (q == p) return NULL;
  const uint8_t *t = q;
  while (q < e && !is_ws(*q) && q - t < 13) q++;
  if (q - t < 1 || q - t > 12 || q >= e || *q != ' ') return NULL;
  return q + 1;
}

static int t1_load(IgFont *f, const uint8_t *raw, size_t n, char *err, size_t errn) {
  uint8_t *clear = NULL, *enc = NULL; size_t nclear = 0, nenc = 0;
  if (n && raw[0] == 0x80) {                         /* PFB segments */
    clear = malloc(n); enc = malloc(n);
    if (!clear || !enc) { free(clear); free(enc); say(err, errn, "out of memory"); return 0; }
    size_t i = 0;
    while (i + 1 < n && raw[i] == 0x80) {
      int kind = raw[i + 1];
      if (kind == 3) break;
      if (i + 6 > n) break;
      size_t len = raw[i + 2] | (size_t)raw[i + 3] << 8 | (size_t)raw[i + 4] << 16 | (size_t)raw[i + 5] << 24;
      if (i + 6 + len > n) { free(clear); free(enc); say(err, errn, "PFB segment past the end"); return 0; }
      if (kind == 2) { memcpy(enc + nenc, raw + i + 6, len); nenc += len; }
      else if (!nenc) { memcpy(clear + nclear, raw + i + 6, len); nclear += len; }
      i += 6 + len;
    }
    if (!nenc) { free(clear); free(enc); say(err, errn, "PFB has no binary segment"); return 0; }
  } else {
    const uint8_t *at = memfind(raw, n, "eexec");
    if (!at) { say(err, errn, "no eexec section"); return 0; }
    nclear = (size_t)(at - raw);
    clear = malloc(nclear + 1); enc = malloc(n + 1);
    if (!clear || !enc) { free(clear); free(enc); say(err, errn, "out of memory"); return 0; }
    memcpy(clear, raw, nclear);
    const uint8_t *b = at + 5, *e = raw + n;
    while (b < e && (*b == ' ' || *b == '\t' || *b == '\r' || *b == '\n')) b++;
    if (e - b >= 4 && is_hex(b[0]) && is_hex(b[1]) && is_hex(b[2]) && is_hex(b[3])) {
      int hi = -1;
      for (; b < e; b++) {
        if (!is_hex(*b)) continue;
        if (hi < 0) hi = hexv(*b); else { enc[nenc++] = (uint8_t)(hi << 4 | hexv(*b)); hi = -1; }
      }
    } else { memcpy(enc, b, (size_t)(e - b)); nenc = (size_t)(e - b); }
  }
  int ok = 0;
  size_t lim = nclear < 64 ? nclear : 64;
  if (!memfind(clear, lim, "%!")) { say(err, errn, "no PostScript header before eexec"); goto out; }
  /* FontMatrix, FontName from the clear text */
  {
    double d[6] = {0.001, 0, 0, 0.001, 0, 0};
    const uint8_t *m = memfind(clear, nclear, "/FontMatrix");
    if (m) {
      const uint8_t *q = m + 11, *e = clear + nclear;
      while (q < e && (*q == ' ' || *q == '\t')) q++;
      if (q < e && *q == '[') {
        const uint8_t *c = memchr(q, ']', (size_t)(e - q));
        if (c) {
          char buf[256]; size_t k = (size_t)(c - q - 1) < 255 ? (size_t)(c - q - 1) : 255;
          memcpy(buf, q + 1, k); buf[k] = 0;
          double v[6]; int nv = 0; char *s = buf, *t;
          while (nv < 7) { double x = strtod(s, &t); if (t == s) break; if (nv < 6) v[nv] = x; nv++; s = t; }
          while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') s++;
          if (nv == 6 && !*s) memcpy(d, v, sizeof d);
        }
      }
    }
    memcpy(f->fm, d, sizeof d);
    const uint8_t *fn = memfind(clear, nclear, "/FontName");
    if (fn) {
      const uint8_t *q = fn + 9, *e = clear + nclear;
      while (q < e && (*q == ' ' || *q == '\t')) q++;
      if (q < e && *q == '/') {
        q++; size_t k = 0;
        while (q < e && !is_delim(*q) && k < sizeof f->name - 1) f->name[k++] = (char)*q++;
        f->name[k] = 0;
      }
    }
  }
  /* the private part */
  if (nenc < 4) { say(err, errn, "eexec section too short"); goto out; }
  f->raw = malloc(nenc);
  if (!f->raw) goto out;
  t1_decrypt(enc, nenc, 55665, f->raw);
  memmove(f->raw, f->raw + 4, nenc - 4);
  f->nraw = nenc - 4;
  const uint8_t *P = f->raw, *E = f->raw + f->nraw;
  f->dec = malloc(f->nraw ? f->nraw : 1);
  if (!f->dec) goto out;
  long leniv = 4;
  {
    const uint8_t *m = memfind(P, f->nraw, "/lenIV");
    if (m) {
      const uint8_t *q = m + 6;
      while (q < E && (*q == ' ' || *q == '\t')) q++;
      int neg = q < E && *q == '-'; if (neg) q++;
      if (q < E && *q >= '0' && *q <= '9') {
        long v = 0; while (q < E && *q >= '0' && *q <= '9') v = v * 10 + (*q++ - '0');
        leniv = neg ? -v : v;
      }
    }
    if (leniv < 0) { say(err, errn, "negative lenIV"); goto out; }
  }
  /* Subrs: `dup <i> <n> <tok> <binary>` from /Subrs on, sequentially */
  const uint8_t *sat = memfind(P, f->nraw, "/Subrs");
  if (sat) {
    size_t cap = 0;
    const uint8_t *q = sat;
    for (;;) {
      const uint8_t *d = memfind(q, (size_t)(E - q), "dup");
      const uint8_t *hit = NULL, *body = NULL; long idx = 0, len = 0;
      while (d) {                                      /* the first `dup` that parses as an entry */
        const uint8_t *a = sp_digits(d + 3, E, &idx), *b2 = a ? sp_digits(a, E, &len) : NULL, *c = b2 ? sp_token_sp(b2, E) : NULL;
        if (c) { hit = d; body = c; break; }
        d = memfind(d + 1, (size_t)(E - d - 1), "dup");
      }
      if (!hit || body + len > E) break;
      if ((size_t)idx >= cap) {
        size_t nc = cap ? cap : 64; while (nc <= (size_t)idx) nc *= 2;
        Bytes *s2 = realloc(f->subrs, nc * sizeof *s2);
        if (!s2) goto out;
        memset(s2 + cap, 0, (nc - cap) * sizeof *s2);
        f->subrs = s2; cap = nc;
      }
      if ((size_t)idx + 1 > f->nsubrs) f->nsubrs = (size_t)idx + 1;
      uint8_t *dec = f->dec + (body - P);           /* the private dict stays as type1.py scans it */
      t1_decrypt(body, (size_t)len, 4330, dec);
      size_t skip = (size_t)leniv < (size_t)len ? (size_t)leniv : (size_t)len;
      f->subrs[idx].p = dec + skip; f->subrs[idx].n = (size_t)len - skip;
      q = body + len;
    }
  }
  /* CharStrings: `/name <n> <tok> <binary>` from past `begin` */
  const uint8_t *cat = memfind(P, f->nraw, "/CharStrings");
  if (!cat) { say(err, errn, "no /CharStrings in the private dict"); goto out; }
  const uint8_t *beg = memfind(cat, (size_t)(E - cat), "begin");
  const uint8_t *q = beg ? beg + 5 : cat + 12;
  size_t cap = 0;
  for (;;) {
    const uint8_t *s = memchr(q, '/', (size_t)(E - q)), *body = NULL; long len = 0;
    const uint8_t *nm = NULL, *ne = NULL;
    while (s) {
      const uint8_t *a = s + 1;
      while (a < E && !is_delim(*a)) a++;
      if (a > s + 1) {
        const uint8_t *b2 = sp_digits(a, E, &len), *c = b2 ? sp_token_sp(b2, E) : NULL;
        if (c) { nm = s + 1; ne = a; body = c; break; }
      }
      s = memchr(s + 1, '/', (size_t)(E - s - 1));
    }
    if (!body || body + len > E) break;
    if (f->ncs == cap) {
      size_t nc = cap ? 2 * cap : 256;
      Bytes *c2 = realloc(f->cs, nc * sizeof *c2); NameIx *i2 = realloc(f->ix, nc * sizeof *i2);
      if (c2) f->cs = c2;
      if (i2) f->ix = i2;
      if (!c2 || !i2) goto out;
      cap = nc;
    }
    char *name = malloc((size_t)(ne - nm) + 1);
    if (!name) goto out;
    memcpy(name, nm, (size_t)(ne - nm)); name[ne - nm] = 0;
    char **n2 = realloc(f->names, (f->nnames + 1) * sizeof *n2);
    if (!n2) { free(name); goto out; }
    f->names = n2; f->names[f->nnames++] = name;
    uint8_t *dec = f->dec + (body - P);
    t1_decrypt(body, (size_t)len, 4330, dec);
    size_t skip = (size_t)leniv < (size_t)len ? (size_t)leniv : (size_t)len;
    f->cs[f->ncs].p = dec + skip; f->cs[f->ncs].n = (size_t)len - skip;
    f->ix[f->ncs].name = name; f->ix[f->ncs].gid = (uint32_t)f->ncs;
    f->ncs++;
    q = body + len;
  }
  if (!f->ncs) { say(err, errn, "/CharStrings present but empty"); goto out; }
  /* type1.py keeps the LAST of two entries with one name (a dict); the stable sort keeps gid order, take the last */
  f->nix = f->ncs;
  for (size_t i = 0; i < f->nix; i++) f->ix[i].gid = (uint32_t)i;
  qsort(f->ix, f->nix, sizeof *f->ix, cmp_ix);
  {
    size_t w = 0;
    for (size_t i = 0; i < f->nix; i++) {
      if (w && !strcmp(f->ix[w - 1].name, f->ix[i].name)) { if (f->ix[i].gid > f->ix[w - 1].gid) f->ix[w - 1] = f->ix[i]; }
      else f->ix[w++] = f->ix[i];
    }
    f->nix = w;
  }
  ok = 1;
out:
  free(clear); free(enc);
  return ok;
}

/* Type 1 charstrings (charstring.py _Runner) */
typedef struct {
  const IgFont *f; Pen pen;
  double st[64]; int ns;
  double ps[64]; int nps;
  int flex; double fx[16], fy[16]; int nflex;
  int seac; double sv[5];
  double sbx, sby;
  const char *why;
} T1Run;
#define T1_MAX_DEPTH 10
static int t1_moveto(T1Run *r, double dx, double dy) {
  r->pen.x += dx; r->pen.y += dy;
  if (r->flex) { if (r->nflex < 16) { r->fx[r->nflex] = r->pen.x; r->fy[r->nflex] = r->pen.y; r->nflex++; } return 1; }
  return pen_move(&r->pen);
}
/* returns 1 = finished (endchar/seac), 0 = continue/return, -1 = error */
static int t1_run(T1Run *r, const uint8_t *code, size_t n, int depth);
static int t1_othersubr(T1Run *r) {
  if (r->ns < 2) { r->why = "callothersubr needs othersubr# and count"; return -1; }
  int idx = (int)r->st[--r->ns];
  int cnt = (int)r->st[--r->ns];
  int k = cnt < r->ns ? cnt : r->ns;
  double args[64];
  for (int i = 0; i < k; i++) args[k - 1 - i] = r->st[--r->ns];
  if (idx == 1) { r->flex = 1; r->nflex = 0; }
  else if (idx == 2) {}
  else if (idx == 0) {
    int was = r->flex;
    r->flex = 0;
    if (was && r->nflex >= 7) {
      double *X = r->fx + r->nflex - 6, *Y = r->fy + r->nflex - 6;
      Seg a = {X[2], Y[2], X[0], Y[0], X[1], Y[1], 1}, b = {X[5], Y[5], X[3], Y[3], X[4], Y[4], 1};
      if (!cur_push(&r->pen, a) || !cur_push(&r->pen, b)) return -1;
      r->pen.x = X[5]; r->pen.y = Y[5];
    }
    if (r->nps + 2 > 64) return -1;
    r->ps[r->nps++] = r->pen.y; r->ps[r->nps++] = r->pen.x;
  } else if (idx == 3) {
    if (r->nps + 1 > 64) return -1;
    r->ps[r->nps++] = 3;
  } else {
    for (int i = 0; i < k; i++) { if (r->nps >= 64) return -1; r->ps[r->nps++] = args[i]; }
  }
  return 0;
}
static int t1_run(T1Run *r, const uint8_t *c, size_t n, int depth) {
  if (depth > T1_MAX_DEPTH) { r->why = "subroutine nesting too deep"; return -1; }
  size_t i = 0;
  double *s = r->st;
  while (i < n) {
    int b = c[i];
    if (b >= 32) {
      double v;
      if (b <= 246) { v = b - 139; i += 1; }
      else if (b <= 250) { if (i + 1 >= n) { r->why = "truncated number"; return -1; } v = (b - 247) * 256 + c[i + 1] + 108; i += 2; }
      else if (b <= 254) { if (i + 1 >= n) { r->why = "truncated number"; return -1; } v = -((b - 251) * 256) - c[i + 1] - 108; i += 2; }
      else { if (i + 4 >= n) { r->why = "truncated 32-bit number"; return -1; }
             v = (int32_t)((uint32_t)c[i + 1] << 24 | (uint32_t)c[i + 2] << 16 | (uint32_t)c[i + 3] << 8 | c[i + 4]); i += 5; }
      if (r->ns >= 64) { r->why = "stack overflow"; return -1; }
      s[r->ns++] = v;
      continue;
    }
    if (b == 12) {
      if (i + 1 >= n) { r->why = "truncated escape"; return -1; }
      int op = c[i + 1]; i += 2;
      if (op == 12) {                                  /* div */
        if (r->ns < 2) { r->why = "div needs 2 arguments"; return -1; }
        double bb = s[--r->ns], a = s[--r->ns];
        if (bb == 0) { r->why = "div by zero"; return -1; }
        s[r->ns++] = a / bb;
      } else if (op == 6) {                            /* seac */
        if (r->ns < 5) { r->why = "seac needs 5 arguments"; return -1; }
        memcpy(r->sv, s, sizeof r->sv); r->seac = 1; r->ns = 0;
        if (!pen_close(&r->pen)) return -1;
        return 1;
      } else if (op == 7) {                            /* sbw */
        if (r->ns < 4) { r->why = "sbw needs 4 arguments"; return -1; }
        r->sbx = s[0]; r->sby = s[1]; r->pen.x = s[0]; r->pen.y = s[1]; r->ns = 0;
      } else if (op == 16) { if (t1_othersubr(r) < 0) { if (!r->why) r->why = "othersubr"; return -1; } }
      else if (op == 17) {                             /* pop */
        if (r->ns >= 64) return -1;
        s[r->ns++] = r->nps ? r->ps[--r->nps] : 0.0;
      } else if (op == 33) { if (r->ns >= 2) { r->pen.x = s[0]; r->pen.y = s[1]; } r->ns = 0; }
      else if (op == 0 || op == 1 || op == 2) r->ns = 0;
      else { r->why = "unimplemented escape operator"; return -1; }
      continue;
    }
    i += 1;
    switch (b) {
    case 13:                                           /* hsbw */
      if (r->ns < 2) { r->why = "hsbw needs 2 arguments"; return -1; }
      r->sbx = s[0]; r->pen.x = s[0]; r->pen.y = 0.0; r->ns = 0; break;
    case 21: { double dx = r->ns >= 2 ? s[r->ns - 2] : 0.0, dy = r->ns >= 2 ? s[r->ns - 1] : 0.0;
               if (!t1_moveto(r, dx, dy)) return -1;
                 r->ns = 0; break; }
    case 22: { double dx = r->ns ? s[r->ns - 1] : 0.0; if (!t1_moveto(r, dx, 0.0)) return -1;
      r->ns = 0; break; }
    case 4:  { double dy = r->ns ? s[r->ns - 1] : 0.0; if (!t1_moveto(r, 0.0, dy)) return -1;
      r->ns = 0; break; }
    case 5:  if (r->ns < 2) { r->why = "rlineto"; return -1; } if (!pen_line(&r->pen, s[0], s[1])) return -1; r->ns = 0; break;
    case 6:  if (r->ns < 1) { r->why = "hlineto"; return -1; } if (!pen_line(&r->pen, s[0], 0.0)) return -1; r->ns = 0; break;
    case 7:  if (r->ns < 1) { r->why = "vlineto"; return -1; } if (!pen_line(&r->pen, 0.0, s[0])) return -1; r->ns = 0; break;
    case 8:  if (r->ns < 6) { r->why = "rrcurveto"; return -1; }
             if (!pen_curve(&r->pen, s[0], s[1], s[2], s[3], s[4], s[5])) return -1;
               r->ns = 0; break;
    case 30: if (r->ns < 4) { r->why = "vhcurveto"; return -1; }
             if (!pen_curve(&r->pen, 0.0, s[0], s[1], s[2], s[3], 0.0)) return -1;
               r->ns = 0; break;
    case 31: if (r->ns < 4) { r->why = "hvcurveto"; return -1; }
             if (!pen_curve(&r->pen, s[0], 0.0, s[1], s[2], 0.0, s[3])) return -1;
               r->ns = 0; break;
    case 9:                                            /* closepath: the current point stays */
      if (r->pen.ncur > 1) {
        if (!pen_close(&r->pen)) return -1;
        if (!cur_push(&r->pen, pt(r->pen.x, r->pen.y))) return -1;
      }
      r->ns = 0; break;
    case 10: {
      if (!r->ns) { r->why = "callsubr with no subr number"; return -1; }
      long k = (long)s[--r->ns];
      if (k < 0 || (size_t)k >= r->f->nsubrs) { r->why = "subr out of range"; return -1; }
      int rc = t1_run(r, r->f->subrs[k].p, r->f->subrs[k].n, depth + 1);
      if (rc) return rc;
      break;
    }
    case 11: return 0;
    case 14: if (!pen_close(&r->pen)) return -1; return 1;
    case 1: case 3: r->ns = 0; break;
    default: r->why = "unimplemented operator"; return -1;
    }
  }
  return 0;
}

/* ---- CFF ---- */
static uint32_t be(const uint8_t *p, int k) { uint32_t v = 0; for (int i = 0; i < k; i++) v = v << 8 | p[i]; return v; }
typedef struct { const uint8_t *data; uint32_t count; const uint8_t *offs; int osz; size_t end; } Index;
/* an INDEX at p; returns the offset past it, 0 on error */
static size_t cff_index(const uint8_t *base, size_t n, size_t at, Index *ix) {
  memset(ix, 0, sizeof *ix);
  if (at + 2 > n) return 0;
  ix->count = be(base + at, 2);
  if (!ix->count) { ix->end = at + 2; return at + 2; }
  if (at + 3 > n) return 0;
  ix->osz = base[at + 2];
  if (ix->osz < 1 || ix->osz > 4) return 0;
  ix->offs = base + at + 3;
  size_t ob = at + 3 + (size_t)(ix->count + 1) * ix->osz;
  if (ob > n) return 0;
  ix->data = base + ob - 1;                          /* offsets are 1-based */
  size_t last = be(ix->offs + (size_t)ix->count * ix->osz, ix->osz);
  if (ob - 1 + last > n) return 0;
  ix->end = ob - 1 + last;
  return ix->end;
}
static Bytes cff_item(const Index *ix, uint32_t i) {
  Bytes b = {0, 0};
  if (i >= ix->count) return b;
  uint32_t a = be(ix->offs + (size_t)i * ix->osz, ix->osz), z = be(ix->offs + (size_t)(i + 1) * ix->osz, ix->osz);
  if (z < a) return b;
  b.p = ix->data + a; b.n = z - a;
  return b;
}
/* DICT: the operands of `op` (12 x as 1200 + x), up to 6 */
static int cff_dict(Bytes d, int want, double *v, int maxv) {
  double st[48]; int ns = 0;
  size_t i = 0;
  while (i < d.n) {
    int b = d.p[i];
    if (b <= 21) {
      int op = b;
      i++;
      if (b == 12) { if (i >= d.n) return -1; op = 1200 + d.p[i++]; }
      if (op == want) { int k = ns < maxv ? ns : maxv; memcpy(v, st, (size_t)k * sizeof *v); return k; }
      ns = 0;
      continue;
    }
    double x;
    if (b == 28) { if (i + 2 >= d.n) return -1; x = (int16_t)be(d.p + i + 1, 2); i += 3; }
    else if (b == 29) { if (i + 4 >= d.n) return -1; x = (int32_t)be(d.p + i + 1, 4); i += 5; }
    else if (b == 30) {                              /* real: nibbles */
      char buf[64]; int k = 0; i++;
      for (int done = 0; i < d.n && !done; i++) {
        for (int h = 0; h < 2 && !done; h++) {
          int nb = h ? d.p[i] & 15 : d.p[i] >> 4;
          if (nb <= 9) { if (k < 60) buf[k++] = (char)('0' + nb); }
          else if (nb == 10) { if (k < 60) buf[k++] = '.'; }
          else if (nb == 11) { if (k < 60) buf[k++] = 'E'; }
          else if (nb == 12) { if (k < 59) { buf[k++] = 'E'; buf[k++] = '-'; } }
          else if (nb == 14) { if (k < 60) buf[k++] = '-'; }
          else if (nb == 15) done = 1;
        }
      }
      buf[k] = 0; x = strtod(buf, NULL);
    }
    else if (b >= 32 && b <= 246) { x = b - 139; i++; }
    else if (b >= 247 && b <= 250) { if (i + 1 >= d.n) return -1; x = (b - 247) * 256 + d.p[i + 1] + 108; i += 2; }
    else if (b >= 251 && b <= 254) { if (i + 1 >= d.n) return -1; x = -(b - 251) * 256 - d.p[i + 1] - 108; i += 2; }
    else return -1;
    if (ns < 48) st[ns++] = x;
  }
  return 0;
}
static int cff_load(IgFont *f, char *err, size_t errn) {
  const uint8_t *B = f->raw; size_t n = f->nraw;
  if (n < 4 || B[0] != 1) { say(err, errn, "not a CFF (major %d)", n ? B[0] : -1); return 0; }
  Index names, top, strs, gsub;
  size_t at = cff_index(B, n, B[2], &names);
  if (!at || !(at = cff_index(B, n, at, &top)) || !(at = cff_index(B, n, at, &strs)) || !cff_index(B, n, at, &gsub)) {
    say(err, errn, "CFF header INDEX past the end"); return 0;
  }
  if (names.count < 1 || top.count < 1) { say(err, errn, "CFF without a font"); return 0; }
  Bytes nm = cff_item(&names, 0);
  size_t k = nm.n < sizeof f->name - 1 ? nm.n : sizeof f->name - 1;
  memcpy(f->name, nm.p, k); f->name[k] = 0;
  Bytes td = cff_item(&top, 0);
  double v[6];
  if (cff_dict(td, 1230, v, 6) > 0) { say(err, errn, "CID-keyed CFF not supported"); return 0; }
  int cst = cff_dict(td, 1206, v, 1) == 1 ? (int)v[0] : 2;
  if (cst != 2) { say(err, errn, "CharstringType %d", cst); return 0; }
  double fm[6] = {0.001, 0, 0, 0.001, 0, 0};
  if (cff_dict(td, 1207, v, 6) == 6) memcpy(fm, v, sizeof fm);
  memcpy(f->fm, fm, sizeof fm);
  if (cff_dict(td, 17, v, 1) != 1) { say(err, errn, "CFF without CharStrings"); return 0; }
  Index cs;
  if (!cff_index(B, n, (size_t)v[0], &cs) || !cs.count) { say(err, errn, "CharStrings INDEX"); return 0; }
  f->ncs = cs.count;
  f->cs = calloc(f->ncs, sizeof *f->cs);
  if (!f->cs) return 0;
  for (uint32_t i = 0; i < cs.count; i++) f->cs[i] = cff_item(&cs, i);
  f->ngsubrs = gsub.count;
  if (gsub.count) {
    f->gsubrs = calloc(gsub.count, sizeof *f->gsubrs);
    if (!f->gsubrs) return 0;
    for (uint32_t i = 0; i < gsub.count; i++) f->gsubrs[i] = cff_item(&gsub, i);
  }
  if (cff_dict(td, 18, v, 2) == 2) {
    size_t psz = (size_t)v[0], poff = (size_t)v[1];
    if (poff + psz > n) { say(err, errn, "Private DICT past the end"); return 0; }
    Bytes pd = {B + poff, psz};
    double w[1];
    if (cff_dict(pd, 21, w, 1) == 1) f->nominal_w = w[0];
    if (cff_dict(pd, 19, w, 1) == 1) {
      Index ls;
      if (cff_index(B, n, poff + (size_t)w[0], &ls) && ls.count) {
        f->nsubrs = ls.count;
        f->subrs = calloc(ls.count, sizeof *f->subrs);
        if (!f->subrs) return 0;
        for (uint32_t i = 0; i < ls.count; i++) f->subrs[i] = cff_item(&ls, i);
      }
    }
  }
  /* charset: glyph -> SID -> name */
  uint32_t *sid = calloc(f->ncs, sizeof *sid);
  if (!sid) return 0;
  size_t csoff = cff_dict(td, 15, v, 1) == 1 ? (size_t)v[0] : 0;
  if (csoff == 0) for (uint32_t g = 0; g < f->ncs; g++) sid[g] = g;      /* ISOAdobe: SID = gid */
  else if (csoff <= 2) { free(sid); say(err, errn, "predefined Expert charset"); return 0; }
  else {
    if (csoff >= n) { free(sid); say(err, errn, "charset past the end"); return 0; }
    int fmt = B[csoff];
    size_t p = csoff + 1; uint32_t g = 1;
    if (fmt == 0) { for (; g < f->ncs && p + 2 <= n; g++, p += 2) sid[g] = be(B + p, 2); }
    else if (fmt == 1 || fmt == 2) {
      while (g < f->ncs) {
        size_t rec = fmt == 1 ? 3 : 4;
        if (p + rec > n) break;
        uint32_t first = be(B + p, 2), left = fmt == 1 ? B[p + 2] : be(B + p + 2, 2);
        for (uint32_t j = 0; j <= left && g < f->ncs; j++) sid[g++] = first + j;
        p += rec;
      }
    } else { free(sid); say(err, errn, "charset format %d", fmt); return 0; }
  }
  f->ix = calloc(f->ncs, sizeof *f->ix);
  f->names = calloc(f->ncs, sizeof *f->names);
  if (!f->ix || !f->names) { free(sid); return 0; }
  for (uint32_t g = 0; g < f->ncs; g++) {
    const char *s = NULL; char *own = NULL;
    if (sid[g] < 391) s = ig_cff_std_strings[sid[g]];
    else {
      Bytes b = cff_item(&strs, sid[g] - 391);
      own = malloc(b.n + 1);
      if (!own) { free(sid); return 0; }
      memcpy(own, b.p, b.n); own[b.n] = 0; s = own;
      f->names[f->nnames++] = own;
    }
    f->ix[f->nix].name = s; f->ix[f->nix].gid = g; f->nix++;
  }
  free(sid);
  qsort(f->ix, f->nix, sizeof *f->ix, cmp_ix);
  return 1;
}

/* Type 2 charstrings (Adobe TN 5177) */
typedef struct {
  const IgFont *f; Pen pen;
  double st[48]; int ns;
  int nstems, width_done, open;
  double tr[32];
  int seac; double sv[4];
  const char *why;
} T2Run;
static int bias(size_t n) { return n < 1240 ? 107 : n < 33900 ? 1131 : 32768; }
static int t2_move(T2Run *r, double dx, double dy) {
  r->pen.x += dx; r->pen.y += dy;
  r->open = 1;
  return pen_move(&r->pen);
}
static int t2_run(T2Run *r, const uint8_t *c, size_t n, int depth) {
  if (depth > 10) { r->why = "subroutine nesting too deep"; return -1; }
  size_t i = 0;
  double *s = r->st;
  Pen *p = &r->pen;
  while (i < n) {
    int b = c[i];
    if (b >= 32 || b == 28) {
      double v;
      if (b == 28) { if (i + 2 >= n) { r->why = "truncated"; return -1; } v = (int16_t)be(c + i + 1, 2); i += 3; }
      else if (b <= 246) { v = b - 139; i++; }
      else if (b <= 250) { if (i + 1 >= n) { r->why = "truncated"; return -1; } v = (b - 247) * 256 + c[i + 1] + 108; i += 2; }
      else if (b <= 254) { if (i + 1 >= n) { r->why = "truncated"; return -1; } v = -(b - 251) * 256 - c[i + 1] - 108; i += 2; }
      else { if (i + 4 >= n) { r->why = "truncated"; return -1; } v = (int32_t)be(c + i + 1, 4) / 65536.0; i += 5; }
      if (r->ns >= 48) { r->why = "stack overflow"; return -1; }
      s[r->ns++] = v;
      continue;
    }
    i++;
    int a = 0;                                         /* first argument after a width */
#define WIDTH(cond) do { if (!r->width_done) { r->width_done = 1; if (cond) a = 1; } } while (0)
    switch (b) {
    case 1: case 3: case 18: case 23:                  /* stems */
      WIDTH(r->ns % 2);
      r->nstems += (r->ns - a) / 2; r->ns = 0; break;
    case 19: case 20:                                  /* hintmask, cntrmask: implied vstems first */
      WIDTH(r->ns % 2);
      r->nstems += (r->ns - a) / 2; r->ns = 0;
      i += (size_t)(r->nstems + 7) / 8;
      break;
    case 21: WIDTH(r->ns > 2); if (r->ns - a < 2) { r->why = "rmoveto"; return -1; }
      if (!t2_move(r, s[a], s[a + 1])) return -1;
        r->ns = 0; break;
    case 22: WIDTH(r->ns > 1); if (r->ns - a < 1) { r->why = "hmoveto"; return -1; }
      if (!t2_move(r, s[a], 0)) return -1;
        r->ns = 0; break;
    case 4: WIDTH(r->ns > 1); if (r->ns - a < 1) { r->why = "vmoveto"; return -1; }
      if (!t2_move(r, 0, s[a])) return -1;
        r->ns = 0; break;
    case 5: for (int k = 0; k + 1 < r->ns; k += 2) if (!pen_line(p, s[k], s[k + 1])) return -1; r->ns = 0; break;
    case 6: case 7: {
      int h = b == 6;
      for (int k = 0; k < r->ns; k++, h = !h) if (!pen_line(p, h ? s[k] : 0, h ? 0 : s[k])) return -1;
      r->ns = 0; break;
    }
    case 8: for (int k = 0; k + 5 < r->ns; k += 6) if (!pen_curve(p, s[k], s[k + 1], s[k + 2], s[k + 3], s[k + 4], s[k + 5])) return -1;
      r->ns = 0; break;
    case 24: {                                         /* rcurveline */
      int k = 0;
      for (; k + 5 < r->ns - 2; k += 6) if (!pen_curve(p, s[k], s[k + 1], s[k + 2], s[k + 3], s[k + 4], s[k + 5])) return -1;
      if (k + 1 < r->ns && !pen_line(p, s[k], s[k + 1])) return -1;
      r->ns = 0; break;
    }
    case 25: {                                         /* rlinecurve */
      int k = 0;
      for (; k + 1 < r->ns - 6; k += 2) if (!pen_line(p, s[k], s[k + 1])) return -1;
      if (k + 5 < r->ns && !pen_curve(p, s[k], s[k + 1], s[k + 2], s[k + 3], s[k + 4], s[k + 5])) return -1;
      r->ns = 0; break;
    }
    case 26: {                                         /* vvcurveto */
      int k = 0; double dx1 = 0;
      if (r->ns % 4 == 1) dx1 = s[k++];
      for (; k + 3 < r->ns; k += 4) { if (!pen_curve(p, dx1, s[k], s[k + 1], s[k + 2], 0, s[k + 3])) return -1; dx1 = 0; }
      r->ns = 0; break;
    }
    case 27: {                                         /* hhcurveto */
      int k = 0; double dy1 = 0;
      if (r->ns % 4 == 1) dy1 = s[k++];
      for (; k + 3 < r->ns; k += 4) { if (!pen_curve(p, s[k], dy1, s[k + 1], s[k + 2], s[k + 3], 0)) return -1; dy1 = 0; }
      r->ns = 0; break;
    }
    case 30: case 31: {                                /* vhcurveto, hvcurveto */
      int h = b == 31, k = 0;
      while (k + 3 < r->ns) {
        int last = k + 4 >= r->ns - 1;
        double e = last && r->ns - k == 5 ? s[k + 4] : 0;
        if (h) { if (!pen_curve(p, s[k], 0, s[k + 1], s[k + 2], e, s[k + 3])) return -1; }
        else   { if (!pen_curve(p, 0, s[k], s[k + 1], s[k + 2], s[k + 3], e)) return -1; }
        k += 4; h = !h;
      }
      r->ns = 0; break;
    }
    case 10: case 29: {                                /* callsubr, callgsubr */
      if (!r->ns) { r->why = "call without a number"; return -1; }
      const Bytes *sub = b == 10 ? r->f->subrs : r->f->gsubrs;
      size_t ns = b == 10 ? r->f->nsubrs : r->f->ngsubrs;
      long k = (long)s[--r->ns] + bias(ns);
      if (k < 0 || (size_t)k >= ns) { r->why = "subr out of range"; return -1; }
      int rc = t2_run(r, sub[k].p, sub[k].n, depth + 1);
      if (rc) return rc;
      break;
    }
    case 11: return 0;
    case 14:                                           /* endchar; 4 more arguments are seac */
      WIDTH(r->ns == 1 || r->ns == 5);
      if (r->ns - a == 4) { memcpy(r->sv, s + a, sizeof r->sv); r->seac = 1; }
      if (!pen_close(p)) return -1;
      r->ns = 0;
      return 1;
    case 12: {
      if (i >= n) { r->why = "truncated escape"; return -1; }
      int op = c[i++];
      double *q = s;
      switch (op) {
      case 35:                                         /* flex */
        if (r->ns < 13) { r->why = "flex"; return -1; }
        if (!pen_curve(p, q[0], q[1], q[2], q[3], q[4], q[5]) || !pen_curve(p, q[6], q[7], q[8], q[9], q[10], q[11])) return -1;
        break;
      case 34: {                                       /* hflex */
        if (r->ns < 7) { r->why = "hflex"; return -1; }
        double y0 = p->y;
        if (!pen_curve(p, q[0], 0, q[1], q[2], q[3], 0)) return -1;
        if (!pen_curve(p, q[4], 0, q[5], y0 - (p->y + 0), q[6], 0)) return -1;
        break;
      }
      case 36: {                                       /* hflex1 */
        if (r->ns < 9) { r->why = "hflex1"; return -1; }
        double y0 = p->y;
        if (!pen_curve(p, q[0], q[1], q[2], q[3], q[4], 0)) return -1;
        double dy = y0 - (p->y + q[7]);
        if (!pen_curve(p, q[5], 0, q[6], q[7], q[8], dy)) return -1;
        break;
      }
      case 37: {                                       /* flex1 */
        if (r->ns < 11) { r->why = "flex1"; return -1; }
        double x0 = p->x, y0 = p->y, dx = 0, dy = 0;
        for (int k = 0; k < 10; k += 2) { dx += q[k]; dy += q[k + 1]; }
        double ex, ey;
        if (fabs(dx) > fabs(dy)) { ex = q[10]; ey = -dy; } else { ex = -dx; ey = q[10]; }
        if (!pen_curve(p, q[0], q[1], q[2], q[3], q[4], q[5])) return -1;
        if (!pen_curve(p, q[6], q[7], q[8], q[9], ex, ey)) return -1;
        (void)x0; (void)y0;
        break;
      }
      case 9: if (r->ns) s[r->ns - 1] = fabs(s[r->ns - 1]); goto keep;
      case 10: if (r->ns >= 2) { s[r->ns - 2] += s[r->ns - 1]; r->ns--; } goto keep;
      case 11: if (r->ns >= 2) { s[r->ns - 2] -= s[r->ns - 1]; r->ns--; } goto keep;
      case 12: if (r->ns >= 2) { s[r->ns - 2] = s[r->ns - 1] ? s[r->ns - 2] / s[r->ns - 1] : 0; r->ns--; } goto keep;
      case 14: if (r->ns) s[r->ns - 1] = -s[r->ns - 1]; goto keep;
      case 24: if (r->ns >= 2) { s[r->ns - 2] *= s[r->ns - 1]; r->ns--; } goto keep;
      case 26: if (r->ns) s[r->ns - 1] = sqrt(fabs(s[r->ns - 1])); goto keep;
      case 18: if (r->ns) r->ns--; goto keep;
      case 27: if (r->ns && r->ns < 48) { s[r->ns] = s[r->ns - 1]; r->ns++; } goto keep;
      case 28: if (r->ns >= 2) { double t = s[r->ns - 1]; s[r->ns - 1] = s[r->ns - 2]; s[r->ns - 2] = t; } goto keep;
      case 20: if (r->ns >= 2) { int k = (int)s[r->ns - 1]; if (k >= 0 && k < 32) r->tr[k] = s[r->ns - 2]; r->ns -= 2; } goto keep;
      case 21: if (r->ns) { int k = (int)s[r->ns - 1]; s[r->ns - 1] = k >= 0 && k < 32 ? r->tr[k] : 0; } goto keep;
      case 0: break;                                   /* dotsection */
      default: r->why = "unimplemented Type 2 escape"; return -1;
      }
      r->ns = 0;
      break;
    keep:
      break;
    }
    default: r->why = "unimplemented Type 2 operator"; return -1;
    }
#undef WIDTH
  }
  return 0;
}

/* ---- glyph outlines, cached per font ---- */
static int append_shifted(Outline *dst, const Outline *src, double dx, double dy) {
  for (size_t k = 0; k < src->nc; k++) {
    size_t a = src->start[k], z = k + 1 < src->nc ? src->start[k + 1] : src->n;
    if (!contour_push(dst, dst->n)) return 0;
    for (size_t i = a; i < z; i++) {
      Seg s = src->s[i];
      s.x += dx; s.y += dy;
      if (s.curve) { s.c1x += dx; s.c1y += dy; s.c2x += dx; s.c2y += dy; }
      if (!seg_push(dst, s)) return 0;
    }
  }
  return 1;
}
static int outline_of(IgFont *f, long g, Outline *out, int depth, const char **why);
static int run_glyph(IgFont *f, long g, Outline *out, int depth, const char **why) {
  if (depth > 2) { *why = "seac nesting"; return 0; }
  memset(out, 0, sizeof *out);
  if (!f->cff) {
    T1Run r; memset(&r, 0, sizeof r); r.f = f;
    int rc = t1_run(&r, f->cs[g].p, f->cs[g].n, 0);
    if (rc < 0) { *why = r.why ? r.why : "charstring failed"; pen_free(&r.pen); return 0; }
    if (r.seac) {
      int bc = (int)r.sv[3], ac = (int)r.sv[4];
      const char *bn = bc >= 0 && bc < 256 ? ig_standard_encoding[bc] : NULL, *an = ac >= 0 && ac < 256 ? ig_standard_encoding[ac] : NULL;
      long bg = bn ? find_glyph(f, bn) : -1, ag = an ? find_glyph(f, an) : -1;
      if (bg < 0 || ag < 0) { *why = "seac names a glyph the font lacks"; pen_free(&r.pen); return 0; }
      Outline base, acc;
      if (!outline_of(f, bg, &base, depth + 1, why) || !outline_of(f, ag, &acc, depth + 1, why)) { pen_free(&r.pen); return 0; }
      double dx = r.sbx + r.sv[1] - r.sv[0], dy = r.sv[2];
      int ok = append_shifted(out, &base, 0, 0) && append_shifted(out, &acc, dx, dy);
      pen_free(&r.pen);
      return ok;
    }
    if (r.pen.ncur && !pen_close(&r.pen)) { pen_free(&r.pen); return 0; }
    *out = r.pen.g; free(r.pen.cur);
    return 1;
  }
  T2Run r; memset(&r, 0, sizeof r); r.f = f;
  int rc = t2_run(&r, f->cs[g].p, f->cs[g].n, 0);
  if (rc < 0) { *why = r.why ? r.why : "charstring failed"; pen_free(&r.pen); return 0; }
  if (r.pen.ncur && !pen_close(&r.pen)) { pen_free(&r.pen); return 0; }
  if (r.seac) {
    int bc = (int)r.sv[2], ac = (int)r.sv[3];
    const char *bn = bc >= 0 && bc < 256 ? ig_standard_encoding[bc] : NULL, *an = ac >= 0 && ac < 256 ? ig_standard_encoding[ac] : NULL;
    long bg = bn ? find_glyph(f, bn) : -1, ag = an ? find_glyph(f, an) : -1;
    if (bg < 0 || ag < 0) { *why = "seac names a glyph the font lacks"; pen_free(&r.pen); return 0; }
    Outline base, acc;
    if (!outline_of(f, bg, &base, depth + 1, why) || !outline_of(f, ag, &acc, depth + 1, why)) { pen_free(&r.pen); return 0; }
    int ok = append_shifted(out, &base, 0, 0) && append_shifted(out, &acc, r.sv[0], r.sv[1]);
    pen_free(&r.pen);
    return ok;
  }
  *out = r.pen.g; free(r.pen.cur);
  return 1;
}
static int outline_of(IgFont *f, long g, Outline *out, int depth, const char **why) {
  if (f->cached[g] == 2) { *why = "charstring failed before"; return 0; }
  if (f->cached[g] == 1) { *out = f->cache[g]; return 1; }
  if (!run_glyph(f, g, &f->cache[g], depth, why)) { f->cached[g] = 2; return 0; }
  f->cached[g] = 1;
  *out = f->cache[g];
  return 1;
}

IgFont *ig_font_load(const char *path, char *err, size_t errn) {
  size_t n = 0;
  uint8_t *raw = slurp(path, &n);
  if (!raw) { say(err, errn, "cannot read %s", path); return NULL; }
  IgFont *f = calloc(1, sizeof *f);
  if (!f) { free(raw); return NULL; }
  int ok;
  if (n >= 4 && raw[0] == 1 && raw[1] == 0 && raw[2] >= 4) {   /* CFF header: major 1, minor 0, hdrSize >= 4 */
    f->cff = 1; f->raw = raw; f->nraw = n;
    ok = cff_load(f, err, errn);
  } else {
    ok = t1_load(f, raw, n, err, errn);
    free(raw);
  }
  if (ok) {
    f->cache = calloc(f->ncs, sizeof *f->cache);
    f->cached = calloc(f->ncs, 1);
    if (!f->cache || !f->cached) ok = 0;
  }
  if (!ok) { ig_font_free(f); return NULL; }
  return f;
}
void ig_font_free(IgFont *f) {
  if (!f) return;
  if (f->cache) for (size_t i = 0; i < f->ncs; i++) outline_free(&f->cache[i]);
  free(f->cache); free(f->cached);
  for (size_t i = 0; i < f->nnames; i++) free(f->names[i]);
  free(f->names); free(f->ix); free(f->cs); free(f->subrs); free(f->gsubrs); free(f->raw); free(f->dec);
  free(f);
}
const char *ig_font_name(const IgFont *f) { return f->name; }
int ig_font_is_cff(const IgFont *f) { return f->cff; }
const double *ig_font_matrix(const IgFont *f) { return f->fm; }

/* ---- scan.py ---- */
typedef struct { double x, y; } P2;
typedef struct { P2 *p; size_t n, cap; } Poly;
static int poly_push(Poly *q, P2 v) {
  if (q->n == q->cap) { size_t c = q->cap ? 2 * q->cap : 64; P2 *t = realloc(q->p, c * sizeof *t); if (!t) return 0; q->p = t; q->cap = c; }
  q->p[q->n++] = v;
  return 1;
}
static int flatten_cubic(P2 p0, P2 p1, P2 p2, P2 p3, Poly *out, int depth) {
  double d1 = fabs(p1.x - p0.x - (p3.x - p0.x) / 3) + fabs(p1.y - p0.y - (p3.y - p0.y) / 3);
  double d2 = fabs(p2.x - p3.x + (p3.x - p0.x) / 3) + fabs(p2.y - p3.y + (p3.y - p0.y) / 3);
  if (depth >= 16 || d1 + d2 <= 0.25) return poly_push(out, p3);
  P2 ab = {(p0.x + p1.x) / 2, (p0.y + p1.y) / 2}, bc = {(p1.x + p2.x) / 2, (p1.y + p2.y) / 2}, cd = {(p2.x + p3.x) / 2, (p2.y + p3.y) / 2};
  P2 abc = {(ab.x + bc.x) / 2, (ab.y + bc.y) / 2}, bcd = {(bc.x + cd.x) / 2, (bc.y + cd.y) / 2};
  P2 mid = {(abc.x + bcd.x) / 2, (abc.y + bcd.y) / 2};
  return flatten_cubic(p0, ab, abc, mid, out, depth + 1) && flatten_cubic(mid, bcd, cd, p3, out, depth + 1);
}
typedef struct { double y0, y1, x0, x1; } Edge;
typedef struct { double x; int d; } Cross;
static int cmp_cross(const void *a, const void *b) {
  const Cross *p = a, *q = b;
  if (p->x != q->x) return p->x < q->x ? -1 : 1;
  return (p->d > q->d) - (p->d < q->d);
}

int ig_has_glyph(const IgFont *f, const char *name) { return find_glyph(f, name) >= 0; }

int ig_glyph_mask(IgFont *f, const char *name, const double m[4], double X, double Y, int x0, int y0, int w, int h,
                  uint8_t *mask, char *err, size_t errn) {
  if (w > 0 && h > 0) memset(mask, 0, (size_t)w * h);
  long g = find_glyph(f, name);
  if (g < 0) { say(err, errn, "no glyph %s", name); return -1; }
  Outline o; const char *why = NULL;
  if (!outline_of(f, g, &o, 0, &why)) { say(err, errn, "%s: %s", name, why ? why : "failed"); return -1; }
  if (w <= 0 || h <= 0) return 0;
  const double *F = f->fm;
#define TF(u, v) (P2){ X + ((u) * F[0] + (v) * F[2] + F[4]) * m[0] + ((u) * F[1] + (v) * F[3] + F[5]) * m[2] - x0, \
                       Y + ((u) * F[0] + (v) * F[2] + F[4]) * m[1] + ((u) * F[1] + (v) * F[3] + F[5]) * m[3] - y0 }
  Poly all = {0}; size_t *cst = NULL, ncst = 0;
  Edge *E = NULL; size_t ne = 0;
  Cross *xs = NULL;
  int rc = -1;
  cst = malloc((o.nc + 1) * sizeof *cst);
  if (!cst) goto done;
  for (size_t k = 0; k < o.nc; k++) {
    size_t a = o.start[k], z = k + 1 < o.nc ? o.start[k + 1] : o.n;
    if (z - a < 2) continue;
    cst[ncst++] = all.n;
    if (!poly_push(&all, TF(o.s[a].x, o.s[a].y))) goto done;
    for (size_t i = a + 1; i < z; i++) {
      P2 end = TF(o.s[i].x, o.s[i].y);
      if (!o.s[i].curve) { if (!poly_push(&all, end)) goto done; }
      else if (!flatten_cubic(all.p[all.n - 1], TF(o.s[i].c1x, o.s[i].c1y), TF(o.s[i].c2x, o.s[i].c2y), end, &all, 0)) goto done;
    }
  }
  cst[ncst] = all.n;
#undef TF
  E = malloc((all.n ? all.n : 1) * sizeof *E);
  if (!E) goto done;
  for (size_t k = 0; k < ncst; k++) {
    size_t a = cst[k], z = cst[k + 1], cnt = z - a;
    for (size_t i = 0; i < cnt; i++) {
      P2 p0 = all.p[a + i], p1 = all.p[a + (i + 1) % cnt];
      if (p0.y != p1.y) E[ne++] = (Edge){p0.y, p1.y, p0.x, p1.x};
    }
  }
  rc = 0;
  if (!ne) goto done;
  xs = malloc(ne * sizeof *xs);
  if (!xs) { rc = -1; goto done; }
  for (int j = 0; j < h; j++) {
    double yc = j + 0.5;
    size_t nx = 0;
    for (size_t e = 0; e < ne; e++) {
      const Edge *q = &E[e];
      if ((q->y0 <= yc && yc < q->y1) || (q->y1 <= yc && yc < q->y0)) {
        double t = (yc - q->y0) / (q->y1 - q->y0);
        xs[nx++] = (Cross){q->x0 + t * (q->x1 - q->x0), q->y1 > q->y0 ? 1 : -1};
      }
    }
    if (!nx) continue;
    qsort(xs, nx, sizeof *xs, cmp_cross);
    int wind = 0; double start = 0;
    uint8_t *row = mask + (size_t)j * w;
    for (size_t k = 0; k < nx; k++) {
      if (wind == 0) start = xs[k].x;
      wind += xs[k].d;
      if (wind == 0) {
        long a = (long)(start + 0.5), b = (long)(xs[k].x + 0.5);   /* Python int(): toward zero */
        if (b > a) {
          if (a < 0) a = 0;
          if (b > w) b = w;
          if (b > a) memset(row + a, 1, (size_t)(b - a));
        }
      }
    }
  }
done:
  free(all.p); free(cst); free(E); free(xs);
  if (rc < 0 && err && !*err) say(err, errn, "out of memory");
  return rc;
}

void ig_score(const uint8_t *a, const uint8_t *b, int w, int h, long *xor_px, long *solid, long *piece) {
  size_t N = (size_t)w * h;
  uint8_t *d = malloc(N ? N : 1);
  int *stack = malloc((N ? N : 1) * sizeof *stack);
  long x = 0, s = 0, best = 0;
  if (!d || !stack) { free(d); free(stack); *xor_px = *solid = *piece = -1; return; }
  for (size_t i = 0; i < N; i++) { d[i] = (uint8_t)((a[i] != 0) != (b[i] != 0)); x += d[i]; }
  for (int j = 0; j + 1 < h; j++)
    for (int i = 0; i + 1 < w; i++) {
      size_t k = (size_t)j * w + i;
      s += d[k] && d[k + 1] && d[k + w] && d[k + w + 1];
    }
  for (size_t i = 0; i < N; i++) {                  /* largest 8-connected piece; visited pixels become 2 */
    if (d[i] != 1) continue;
    long n = 0; int sp = 0;
    stack[sp++] = (int)i; d[i] = 2;
    while (sp) {
      int k = stack[--sp], cx = k % w, cy = k / w;
      n++;
      for (int dy = -1; dy <= 1; dy++)
        for (int dx = -1; dx <= 1; dx++) {
          int nx = cx + dx, ny = cy + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          int q = ny * w + nx;
          if (d[q] == 1) { d[q] = 2; stack[sp++] = q; }
        }
    }
    if (n > best) best = n;
  }
  free(d); free(stack);
  *xor_px = x; *solid = s; *piece = best;
}

/* ---- `inkcmp glyphs`: one job per page (CONTRACT.md unit 5, glyph-raster route) ----
   stdin:
     INKGLYPHS1
     font <id> <path>                      ids 0.., a Type 1 or CFF program
     page <path.pgm> <ink>                 optional: the original page at 400 dpi; ink = grey < <ink>
     holes <min area>                      optional: a white region counts as a hole from this many pixels (default 1)
     pair <fa> <ga> <a b c d> <X> <Y> <fb> <gb> <a b c d> <X> <Y> <x0> <y0> <x1> <y1>
     (a font field may list candidate ids, comma-separated: the first whose program holds the glyph is used)
     add A|B <ids> <glyph> <a b c d> <X> <Y>   more ink on one side of the pair above (a neighbour's glyph in the
                                           window); a neighbour without an outline adds nothing
     end
   A side is a glyph (font id, glyph name, 2x2 matrix in device px per em, origin), or, with font id -1, the page's
   ink, or, with font id `none`, no ink but what its `add` lines bring (name and matrix ignored for both). The window [x0, x1) x [y0, y1) is page px.
   stdout: `font <id> ok <name>` or `font <id> fail <reason>`, then per pair `P <xor> <solid> <piece> <holes A> <holes B>`
   (holes of the two sides' own ink before any `add`) or
   `O A|B <reason>` (no outline), then `end <pairs> <no outline>`. */
typedef struct { int w, h; uint8_t *g; int ink; } Pg;
static int read_pgm(const char *path, Pg *pg) {
  size_t n = 0; uint8_t *b = slurp(path, &n);
  if (!b) return 0;
  size_t i = 0; long v[3]; int k = 0;
  if (n < 2 || b[0] != 'P' || b[1] != '5') { free(b); return 0; }
  i = 2;
  while (k < 3 && i < n) {
    while (i < n && (is_ws(b[i]) || b[i] == '#')) { if (b[i] == '#') while (i < n && b[i] != '\n') i++; else i++; }
    long x = 0; int d = 0;
    while (i < n && b[i] >= '0' && b[i] <= '9') { x = x * 10 + (b[i++] - '0'); d = 1; }
    if (!d) { free(b); return 0; }
    v[k++] = x;
  }
  i++;
  if (k < 3 || v[2] != 255 || i + (size_t)v[0] * v[1] > n) { free(b); return 0; }
  pg->w = (int)v[0]; pg->h = (int)v[1];
  pg->g = malloc((size_t)pg->w * pg->h);
  if (!pg->g) { free(b); return 0; }
  memcpy(pg->g, b + i, (size_t)pg->w * pg->h);
  free(b);
  return 1;
}
/* a glyph from the first candidate font (comma-separated ids) that holds it; 0, or -1 with the reason */
static int place(IgFont **F, size_t nf, char *ids, const char *name, const double m[4], double X, double Y,
                 int x0, int y0, int w, int h, uint8_t *mask, char *why, size_t nwhy) {
  char *save = NULL;
  for (char *t = strtok_r(ids, ",", &save); t; t = strtok_r(NULL, ",", &save)) {
    char *e; long id = strtol(t, &e, 10);
    if (*e || id < 0 || (size_t)id >= nf || !F[id] || !ig_has_glyph(F[id], name)) continue;
    return ig_glyph_mask(F[id], name, m, X, Y, x0, y0, w, h, mask, why, nwhy);
  }
  snprintf(why, nwhy, "no font holds the glyph");
  return -1;
}

/* holes of a mask: 4-connected background regions that do not touch the window's edge (8-connected ink) */
static long hole_min = 1;                            /* `holes <min area>`: smaller white regions are not holes */
static long holes_of(const uint8_t *m, int w, int h) {
  size_t N = (size_t)w * h;
  uint8_t *seen = calloc(N ? N : 1, 1);
  int *st = malloc((N ? N : 1) * sizeof *st);
  long holes = 0;
  if (!seen || !st) { free(seen); free(st); return -1; }
  for (size_t i = 0; i < N; i++) {
    if (m[i] || seen[i]) continue;
    int sp = 0, edge = 0;
    long area = 0;
    st[sp++] = (int)i; seen[i] = 1;
    while (sp) {
      int k = st[--sp], x = k % w, y = k / w;
      area++;
      if (x == 0 || y == 0 || x == w - 1 || y == h - 1) edge = 1;
      static const int D[4][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
      for (int d = 0; d < 4; d++) {
        int nx = x + D[d][0], ny = y + D[d][1];
        if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
        int q = ny * w + nx;
        if (!m[q] && !seen[q]) { seen[q] = 1; st[sp++] = q; }
      }
    }
    holes += !edge && area >= hole_min;
  }
  free(seen); free(st);
  return holes;
}

typedef struct { int live, bad, w, h, x0, y0; long ha, hb; char why[300]; } Pending;
static int flush(Pending *q, const uint8_t *ma, const uint8_t *mb, FILE *out, long *none) {
  if (!q->live) return 1;
  q->live = 0;
  if (q->bad >= 0) { (*none)++; fprintf(out, "O %c %s\n", q->bad ? 'B' : 'A', q->why); return 1; }
  long xo, so, pc;
  ig_score(ma, mb, q->w, q->h, &xo, &so, &pc);
  if (xo < 0) return 0;
  fprintf(out, "P %ld %ld %ld %ld %ld\n", xo, so, pc, q->ha, q->hb);
  return 1;
}

int ig_main(FILE *in, FILE *out, FILE *errf) {
  char line[4096];
  IgFont **F = NULL; size_t nf = 0;
  Pg page = {0, 0, NULL, 0};
  uint8_t *ma = NULL, *mb = NULL, *mt = NULL; size_t mcap = 0;
  long pairs = 0, none = 0;
  int rc = 2;
  Pending q = {0, -1, 0, 0, 0, 0, -1, -1, ""};
  if (!fgets(line, sizeof line, in) || strncmp(line, "INKGLYPHS1", 10)) { fprintf(errf, "inkcmp glyphs: no INKGLYPHS1 header\n"); return 2; }
  while (fgets(line, sizeof line, in)) {
    if (!strncmp(line, "end", 3)) { if (!flush(&q, ma, mb, out, &none)) goto done; rc = 0; break; }
    if (!strncmp(line, "font ", 5)) {
      long id; char path[3000];
      if (sscanf(line + 5, "%ld %2999[^\n]", &id, path) != 2 || id < 0 || id > 100000) { fprintf(errf, "inkcmp glyphs: bad font line\n"); goto done; }
      if ((size_t)id >= nf) {
        IgFont **g = realloc(F, (size_t)(id + 1) * sizeof *g);
        if (!g) goto done;
        for (size_t k = nf; k <= (size_t)id; k++) g[k] = NULL;
        F = g; nf = (size_t)id + 1;
      }
      char err[256] = "";
      F[id] = ig_font_load(path, err, sizeof err);
      if (F[id]) fprintf(out, "font %ld ok %s\n", id, ig_font_name(F[id]));
      else fprintf(out, "font %ld fail %s\n", id, err);
      continue;
    }
    if (!strncmp(line, "holes ", 6)) {
      if (sscanf(line + 6, "%ld", &hole_min) != 1 || hole_min < 1) { fprintf(errf, "inkcmp glyphs: bad holes line\n"); goto done; }
      continue;
    }
    if (!strncmp(line, "page ", 5)) {
      char path[3000]; int ink;
      if (sscanf(line + 5, "%2999s %d", path, &ink) != 2 || !read_pgm(path, &page)) { fprintf(errf, "inkcmp glyphs: cannot read page\n"); goto done; }
      page.ink = ink;
      continue;
    }
    if (!strncmp(line, "add ", 4)) {                 /* another glyph's ink on one side of the pair above */
      char sd, ids[256], nm[512]; double m[4], X, Y;
      if (sscanf(line + 4, " %c %255s %511s %lf %lf %lf %lf %lf %lf", &sd, ids, nm, &m[0], &m[1], &m[2], &m[3], &X, &Y) != 9
          || (sd != 'A' && sd != 'B') || !q.live) { fprintf(errf, "inkcmp glyphs: bad add line\n"); goto done; }
      if (q.bad >= 0) continue;
      char why[300];
      if (place(F, nf, ids, nm, m, X, Y, q.x0, q.y0, q.w, q.h, mt, why, sizeof why) < 0) continue;   /* no outline: no ink */
      uint8_t *d = sd == 'A' ? ma : mb;
      for (size_t i = 0, N = (size_t)q.w * q.h; i < N; i++) d[i] |= mt[i];
      continue;
    }
    if (strncmp(line, "pair ", 5)) { fprintf(errf, "inkcmp glyphs: unknown line\n"); goto done; }
    if (!flush(&q, ma, mb, out, &none)) goto done;
    char ids[2][256]; char nm[2][512]; double m[2][4], O[2][2]; int x0, y0, x1, y1;
    int got = sscanf(line + 5, "%255s %511s %lf %lf %lf %lf %lf %lf %255s %511s %lf %lf %lf %lf %lf %lf %d %d %d %d",
                     ids[0], nm[0], &m[0][0], &m[0][1], &m[0][2], &m[0][3], &O[0][0], &O[0][1],
                     ids[1], nm[1], &m[1][0], &m[1][1], &m[1][2], &m[1][3], &O[1][0], &O[1][1], &x0, &y0, &x1, &y1);
    if (got != 20 || x1 < x0 || y1 < y0) { fprintf(errf, "inkcmp glyphs: bad pair line\n"); goto done; }
    int w = x1 - x0, h = y1 - y0;
    size_t N = (size_t)w * h;
    if (N > mcap) {
      uint8_t *a = realloc(ma, N), *b = realloc(mb, N), *t = realloc(mt, N);
      if (a) ma = a;
      if (b) mb = b;
      if (t) mt = t;
      if (!a || !b || !t) goto done;
      mcap = N;
    }
    pairs++;
    q.live = 1; q.bad = -1; q.w = w; q.h = h; q.x0 = x0; q.y0 = y0; q.why[0] = 0; q.ha = q.hb = -1;
    uint8_t *mk[2] = {ma, mb};
    for (int sd = 0; sd < 2 && q.bad < 0; sd++) {
      if (!strcmp(ids[sd], "-1")) {
        if (!page.g) { q.bad = sd; snprintf(q.why, sizeof q.why, "no page"); break; }
        for (int j = 0; j < h; j++)
          for (int i = 0; i < w; i++) {
            int X = x0 + i, Y = y0 + j;
            mk[sd][(size_t)j * w + i] = X >= 0 && Y >= 0 && X < page.w && Y < page.h && page.g[(size_t)Y * page.w + X] < page.ink;
          }
        continue;
      }
      if (!strcmp(ids[sd], "none")) { memset(mk[sd], 0, N); continue; }      /* no glyph of its own: adds only */
      if (place(F, nf, ids[sd], nm[sd], m[sd], O[sd][0], O[sd][1], x0, y0, w, h, mk[sd], q.why, sizeof q.why) < 0) q.bad = sd;
    }
    if (q.bad < 0) { q.ha = holes_of(ma, w, h); q.hb = holes_of(mb, w, h); }   /* the pair's own glyphs, no neighbours */
  }
  if (rc) fprintf(errf, "inkcmp glyphs: no end line\n");
  else fprintf(out, "end %ld %ld\n", pairs, none);
done:
  for (size_t k = 0; k < nf; k++) ig_font_free(F[k]);
  free(F); free(page.g); free(ma); free(mb); free(mt);
  return rc;
}

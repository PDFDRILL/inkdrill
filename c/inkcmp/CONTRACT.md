# inkcmp — interface contracts (written before the code)

`inkcmp` is the C version of inkdrill's judge: it compares two rendered pages by their ink and
reports residuals with enough detail to change the docmodel or a pdfdrill setting. It links
GLYPHDRILL3's sources and does not change them.

This file holds the contracts of units 1–3 and the draft of units 4–6 (registration, matching,
findings). Units 7–8 (docmodel join, LLM branch) get their contracts when they are started.

Common rules (GLYPHDRILL3 CLAUDE.md): coordinates 0-based, half-open, in page pixels; no image
or stream files, pages arrive on stdin through a pipe; no blob labelling on a full page; no
function call per pixel or per run; no n² search; fail loudly on malformed input.

Exit codes: 0 ok, 2 usage or invalid PGM header, 4 short input or trailing bytes, 5 out of memory.

## Unit 1 — load (`inkcmp/page.h`)

    int ic_page_from_pgm(FILE *in, uint32_t threshold, IcPage *page);
    int ic_page_write_rle(const IcPage *page, FILE *out);
    void ic_page_free(IcPage *page);

Input: one binary PGM (P5, maxval 255, `#` comments allowed in the header), exactly one page.
`threshold` 1..256; a pixel is ink when `pixel < threshold` (the rule of `gd3-adapter`).

Guarantees
- L1 `IcPage` holds `width`, `height`, `row_start[0..height]` and `spans` (x0, x1 pairs): row y
  has the runs `row_start[y] .. row_start[y+1]`, sorted, disjoint, non-empty, never adjacent.
  This is the layout `gd3_blocks()` and the sniffer's `GD3Page` take.
- L2 `ic_page_write_rle` writes a GD3RLE1 stream byte-identical to `gd3-adapter --pgm T`.
- L3 A bad header returns 2; a short page or any byte after the page returns 4. Nothing is
  repaired or guessed.
- L4 Two pages can be loaded in one process (no file-scope state).

CLI: `inkcmp rle [--threshold T] < page.pgm > page.gd3rle1` (test surface for L2/L3).

## Unit 2 — structure (GLYPHDRILL3 `src/layout/blocks.h`, called unchanged)

    gd3_blocks_opt(width, height, row_start, spans, opts, gx, gy, &blocks)

Guarantees
- S1 `inkcmp blocks` prints exactly what `gd3-adapter --pgm T | gd3-blocks` prints for the
  same page and the same `--gx/--gy` (byte-identical stdout).
- S2 The line height `lh` that unit 3 uses is `GD3Blocks.lh` of this call.

Not in this unit: lines, words and blobs from `layout/lines.c` and `sniff/`. Their only printer
in GLYPHDRILL3 is `gd3-bands`, which needs model files that are not in git, so they get a
unit of their own with a pixel oracle.

CLI: `inkcmp blocks [--threshold T] [--gx PX] [--gy PX] < page.pgm`.

## Unit 3 — white space (`inkcmp/white.h`)

    int ic_white(const IcPage *page, uint32_t min_w, uint32_t min_h, IcRects *out);
    int ic_space(const IcPage *page, uint32_t lh, const IcSpaceParams *params, IcSpace *out);

Definitions
- A white rectangle is maximal when none of its four sides can move outward (each side touches
  ink or the page edge). The set is a property of the page.
- Content box: the bounding box of all ink. Margins: the four distances from it to the page edge.
- Separator candidates: maximal white rectangles clipped to the content box.
  - vertical (`vsep`, a column gutter): height >= width, width >= `vsep_w` x lh,
    height >= `vsep_h` x lh, and it touches neither the left nor the right side of the content box.
  - horizontal (`hsep`, a gap between paragraphs or sections): width > height,
    width >= `hsep_w` x lh, height >= `hsep_h` x lh, and it touches neither the top nor the
    bottom of the content box.
- Selection: one greedy cover over both classes. Candidates in order of decreasing area (ties:
  y0, x0, y1, x1). A candidate c is compared with the rectangles already kept, in the order
  they were kept:
  - a kept rectangle spans c's full width: c loses the overlapping rows;
  - else it spans c's full height: c loses the overlapping columns;
    (the larger remaining part stays; ties: the upper / left one; the comparison starts again
    with the cut c, which must still be of a class);
  - else: c is dropped when the overlap is > `overlap` x area(c).
  At most `max_sep` rectangles are kept in total.
  Reason for the cuts, measured on drawn pages: without them a wide white band is reported
  again as many narrow rectangles that reach a few rows into the text below it, and a gutter is
  cut apart by the white after short lines.
- A horizontal separator is any wide white rectangle of the given size. That includes the white
  after a short last line joined with the line gaps above and below it, not only gaps between
  paragraphs.

Defaults: vsep_w 1.0, vsep_h 4.0, hsep_w 4.0, hsep_h 1.0, overlap 0.2, max_sep 64 (in total).
These numbers are assumptions until they are measured on real pages.

Guarantees
- W1 `ic_white(page, 1, 1)` returns every maximal white rectangle exactly once, in the order
  `gd3-white` prints them; `inkcmp white` is byte-identical to `gd3-white` on stdout.
- W2 With `min_w`/`min_h` it returns exactly those of W1 with width >= min_w and
  height >= min_h; `out->total` is still the count of all of them.
- W3 Content box and margins are exact; a page without ink is reported as `blank` with no
  separators.
- W4 Every separator is white (no ink pixel inside), lies inside the content box and meets the
  size and side rules above.
- W5 On a page with a clean column gutter of width >= vsep_w x lh between two text columns, the
  gutter is returned as one `vsep` covering the gutter's full width and the columns' common rows.
  A `vsep` narrower than the word gap also reports aligned word gaps ("rivers"); the default
  vsep_w 1.0 is above the drawn text's word gap (0.5 x lh).
- W6 Unit 3 does not depend on unit 2: `--lh PX` supplies the line height.

Known limit, not handled here: a skewed scan narrows every maximal rectangle across a gutter.
That needs the seam variant and is its own unit.

CLI:

    inkcmp white [--threshold T] < page.pgm            (the format of gd3-white)
    inkcmp space [--threshold T] [--lh PX] [--vsep-w F] [--vsep-h F] [--hsep-w F] [--hsep-h F]
                 [--overlap F] [--max-sep N] < page.pgm

    INKSPACE1 <width> <height> lh <LH> white <count of maximal white rectangles>
    blank                                  (only on a page without ink; then no further records)
    content <x0> <y0> <x1> <y1>
    margin <top> <bottom> <left> <right>
    vsep <x0> <y0> <x1> <y1>               (sorted by x0, y0, x1, y1)
    hsep <x0> <y0> <x1> <y1>               (sorted by y0, x0, y1, x1)
    end <vseps> <hseps>

## Units 4–6 — the comparison (DRAFT, T-0148, 2026-11-16)

Document A is the original (the published PDF), and B is its reading rendered again. Both are
rendered at the same dpi and threshold. B page p is compared with A pages p-1, p and p+1, which
arrive on stdin as one stream:

    inkcmp compare [--threshold T] --a-pages N < A(p-1) A(p) A(p+1) B(p)   (N = 2 or 3 at the ends)

The PGMs are concatenated, as `gs -sOutputFile=%stdout` writes them. `lh` is GD3Blocks.lh of A
page p, and `q = ceil(lh / 4)`. Lines come from unit 2. A's lines are shifted into page p's frame
by whole page heights.

Evidence: scratch measurements on spent documents (work/run-files/inkcmp-unit4 and t0148-compare,
reported in drillgroup T-0148). G-R2 passed 3 of 4 fresh documents, and its failure was two merged
columns. Per-glyph XOR at t = 1 put every paired planted swap (b/d, l/1, =/-, x/×, a dropped
prime; 27 lines) at 164 px or more, while 2126 of 2146 unchanged lines were 0.

### Unit 4 — registration (`inkcmp/register.h`): in place

The judge renders the reading in place (coordinator, T-0148, 2026-11-16). Each lines.json line is a
standalone snippet; all snippets of a page go into one pdflatex run (one page per line).

Pass 1 renders every line page in one gs call at 400 dpi and takes each line's ink height. Pass 2
re-sets every line whose height ratio to the original's ink inside the region is off by >= 3%
with `\scalebox`, and renders all of them in one gs call at 400 dpi. Each line raster is then
placed with its ink box's top-left on the top-left of the original's ink box inside the region
(MathPix px → page px per page and per axis; CropBox frame).

Registration is therefore per line by construction. G-R2's segment search remains only for
readings that have no region boxes.

Measured (scratch, 2207.03560 p1, MathPix, both sides CM):
- 72 of 83 lines have glyph residual 0;
- the other 11 are real reading differences: quotes, lost bold/italic/small caps, a spurious
  space, a lost dagger, a caption formula;
- in-memory rescaling from one 600-dpi render gives 20 of 83 instead, so it is not used;
- cost: pdflatex 0.46 + 1.55 s and gs 0.45 s per page (above the board's 1 s; the second
  pdflatex should cover only the rescaled lines).

Guarantees
- R1 Every line of the reading has exactly one placement or one refusal: a LaTeX error, or no ink
  in its region on either side. A refusal is a finding, never a silent drop.
- R2 A page rendered from its own text layer, in its own fonts, places every line with offset 0.

### Unit 5 — matching (`inkcmp/match.h`)

Each pair (a, b) is matched glyph by glyph inside the two line crops. Labelling inside a line crop
is allowed: the rule against full-page labelling is GLYPHDRILL3's, and inkdrill's C works on crops
(coordinator, 2026-11-16).

1. Words: each line is cut at white column runs >= ceil(lh/4). When A and B have the same number
   of words, each B word is mapped onto its A word by its own x-offset and x-scale (A width / B
   width): justification and font expansion (2207.03560: the original's words are ~2% wider at
   the same height). Otherwise the line is compared without word mapping.
   - This answers the measured tail: in all 4 unchanged lines with nonzero XOR, a group of glyphs
     sat more than 3 px along the line from the line offset.
   - Following the drift glyph by glyph was tried twice and was worse (17 nonzero lines instead of 4).
2. Components: 8-connected, from the crop's spans (union-find over runs, so no per-pixel calls).
3. Each B component gets a candidate A component when:
   - its box size is within 2 px;
   - its box position is within 3 px after the word's (dx, dy);
   - the candidates come from a window sweep over components sorted by x0, which is linear.
4. The best candidate and a shift in [-2, 2]^2 give the least XOR. A pixel counts in the XOR only
   when it lies more than t = 1 px from the other side's ink. This is computed on runs.
5. Each A component without a partner is matched the same way, from A's side.

Guarantees
- M1 A against itself: every XOR is 0.
- M2 XOR is symmetric under exchanging A and B (computed on both sides).
- M3 A component whose box exceeds the window is never matched; it is reported, never dropped.

**Open branch: a font-tolerant comparison.** The plants above keep the font and the renderer. The
judge's real input is an original, possibly in Times with its own line breaks, against our reading
rendered again in CM. If on such a page the t = 1 XOR flags most glyphs:
- XOR is kept only for pairs inside one font (decided per pair by the XOR of matched glyphs);
- across fonts, a component is compared by font-free features (GLYPHDRILL3's key16, ends/caps,
  holes, crossings, or the ffeat distance; T-0149's measurement).
Measured (scratch): on a Times original (2209.02970 p1, NimbusRomNo9L) with the CM rendering, 0 of
58 lines are at 0 and 5478 of 5628 glyphs differ, so XOR does not cross fonts. A first font-free
check (each word's sequence of component hole counts) agrees on 181/229 words with equal text
(79%), against 716/727 (98.5%) in CM against CM: too weak alone. The cross-font comparison
is T-0149's feature work (key16, ends/caps, crossings, ffeat) and is not fixed here.

### Unit 6 — findings (`inkcmp/findings.h`)

    INKCMP1 page <p> lh <lh> linesA <n> linesB <n> paired <n>
    pair <bx0> <by0> <bx1> <by1> <a-page> <ax0> <ay0> <ax1> <ay1> <dx> <dy> fp|nofp
    glyph <side A|B> <page> <x0> <y0> <x1> <y1> xor <px> pair <k>
    line <side A|B> <page> <x0> <y0> <x1> <y1> unpaired
    end <glyph findings> <line findings> residual_px <sum>

- Boxes are page px, half-open, of the side named. The pt boxes of blob/1 come with T-0150's writer.
- `residual_px` is the page's value in the board's judge residual column (ink px with no
  counterpart, unpaired lines included).
- F1 Every finding names a box that contains ink. F2 With A = B, the page reports no findings.

### Gate (three lines, rule 5)
- Test: 4 fresh documents past draw position 396 (at least 2 two-column), plus the five plant
  kinds recompiled into 2 of them.
- Pass:
  - every paired plant has a glyph finding at its place;
  - at most 1% of unchanged pairs have any finding;
  - A against A gives no finding.
- Board: the judge residual column gets its first values, `residual_px` per page.

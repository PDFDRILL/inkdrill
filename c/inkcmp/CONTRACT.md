# inkcmp — interface contracts (written before the code)

`inkcmp` is the C version of inkdrill's judge: it compares two rendered pages by their ink and
reports residuals with enough detail to change the docmodel or a pdfdrill setting. It links
GLYPHDRILL3's sources and does not change them.

This file holds the contracts of the first three units. Units 4–8 (registration, matching,
findings, docmodel join, LLM branch) get their contracts when they are started.

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

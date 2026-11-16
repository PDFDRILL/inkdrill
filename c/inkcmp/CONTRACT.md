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

**Born-digital branch: phase-exact per glyph** (scratch, T-0148, 2026-11-16).
- The snippet preamble takes the original's text family from pdffonts:
  - Times/Nimbus → times;
  - Helvetica → helvet;
  - Palatino → newpxtext;
  - CM/LM → default.
  Unmapped families are reported.
- Glyph origins come from both content streams (mutool stext `<char x y>`).
- Both sides are rendered unhinted by poppler at 8 x 400 dpi; the original is rendered per line crop. Ghostscript hints
  Type 1 stems at 400 dpi, and -dGridFitTT=0 does not stop it.
- Each glyph is point-sampled onto the original's 400-dpi grid at its own fractional origin, with offset -0.5/8 px.
- Score per glyph: "solid" residual, the differing pixels whose 2x2 block differs entirely, at t = 0. This removes the
  1-px edge flips that remain from about 0.06 px of position difference.
- Measured on 040123 p2 (pdfTeX, Times, MathPix lines.json):
  - 2426 glyphs on lines without style loss: solid max 3;
  - 12 planted single-letter swaps: solid 11-277 (n→u 11 and 14, e→c 17 and 18, r→t 23, h→b 25, all others ≥ 116);
  - lines where MathPix lost italics or bold, or a mono URL (no family mapping): 168 glyphs ≥ 10, all real findings.
- XOR at t = 1, the same-font route above, misses n→u in Times (10 px). Glyph labels and Reeb signatures across fonts
  were measured and do not carry: 68% label agreement, and Reeb is noisy under phase.

**Threshold frozen at solid >= 7 per glyph** (2026-11-16, before the fresh-page run).
Fresh plants (seed 11, 12 per page) on pages not used to find the threshold. Same run:
- glyphs paired by position (x order plus text alignment);
- each line set at the original's text size (content stream);
- link annotations hidden;
- a glyph whose original font class differs from the reading's counts as a style finding: bold, italic, mono, or
  maths against text;
- Euler maths mapped to eulervm.

| page | equal-text glyphs | style findings | other flags | plants flagged |
|---|---|---|---|---|
| 2207.03560 p1, CM | 4095 | 132 | 0 | 16/17, missed n→u (solid 2, 9 pt) |
| 1604.04795 p2, Times | 4829 | 303 | 0 | 15/15 |
| 1407.4737 p2, URW Palladio + Euler | 2547 | 134 | 40 (37 maths: display layout, Euler glyph sources; 3 text in one heading) | 31/31* |

\* The 32nd "plant" is φ against ϕ with identical ink (a codepoint label), not a swap.

Open:
- the 9 pt miss needs a measure that keeps 2-px strokes (solid 2x2 discards them), tested on new plants;
- displayed maths needs its own comparison;
- cost per page (1604.04795 p2, 100 lines): pdflatex 0.71 s, the reading's line pages at 8x in one poppler call 3.26 s,
  the original's line crops at 8x, one call per line, 18.33 s. That is about 22 s against the board's 1 s, before the
  sampling. A C judge would rasterise the original's glyphs from their embedded font programs at their origins
  (U9's type1 → charstring → scan route), not render pages.

**Command (2026-11-17).** `bin/inkcmp-judge <original.pdf> <page> <lines.json> [--work DIR]` (`inkdrill/judge.py`)
is this born-digital branch exactly as measured above: UP 8, OX = OY = -1/16, ink < 154, threshold 7, bp -> TeX pt.
On 040123 p2 its `G` lines equal the scratch runs line for line, clean and planted (2857 glyphs, 176 and 185
flagged), in 7.5 s. `tests/test_judge.py` makes a CM page with pdflatex and plants one letter per line. The clean
reading gets 0 flags. 7 of 8 plants are flagged, and nothing else is. e->c at 10 pt (crossbar, solid 4) is the
thin-stroke miss, and an expected-failure test holds it open.

**Glyph rasteriser route (rule-6 step 7, scratch, 2026-11-17).** A's and B's glyphs come from their embedded
Type 1 programs (`mutool extract`, U9 type1 -> charstring -> scan at 400 dpi, both at A's fractional origin), with no
8x page render. On 040123 p2 (Type 1 only), against the 8x route: flags agree on 2856 of 2857 glyphs, and all 12 of
12 plants are flagged. Time: font extract and parse 0.14 s, stext 0.13 s, raster and compare 2.68 s (Python), plus a
400 dpi page render for the ink that is not glyphs, 0.08 s. That is about 3 s against the route above's 7.5 to 22 s.
In the library sample (299 documents, pp. 1-3), 72% of documents use only Type 1 and Type 1C fonts.

**Scan branch (named, not measured).** A scan has no text layer, so the glyph positions come from the original's ink
(GLYPHDRILL3 word boxes or blob centroids). Its noise floor has its own measurement before it gets a threshold.

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

## Unit 7 — lines (DRAFT, 2026-11-17): MathPix-shaped line geometry from ink, against YOLO

The owner's question (2026-11-17): can a reduced blob/white-space detector give the line geometry of a MathPix
lines.json faster than Gemma4's YOLO route, at the same accuracy? The prototype is scratch Python with PIL
(`work/run-files/blob-lines/blob_lines.py`), and the C port as `inkcmp lines` is next. Per page:

1. **Ink threshold, automatic per page.** Take the Otsu split t (paper against everything else) and the dark
   class's mean d. Ink is a pixel < (d + t)/2; a bilevel page uses 128. A grey shadow or fringe lies between the
   two and drops out.
2. **Edge shadows.** Columns in the outer 8% with > 50% ink (rows: > 80%) are blanked out to the edge. Then, by
   8-connected components: specks < 0.25 x the median component height go, so do components touching the left
   or right page edge, and so do components taller than half the page.
3. **Rows.** `inkcmp blocks` (gd3_blocks) gives the line bands. Each band is cut at white column runs
   >= 1.5 lh into pieces, and each piece is tightened to its ink.
4. **Line height** m = the 20th percentile of the heights of pieces wider than 0.3 W. (The median failed: merged
   pairs raised it.) A piece taller than 1.6 m is cut at its least-ink row when that row holds < 25% of the
   peak ink, recursively. Pieces are clipped at the column's right edge + 0.5 lh, and specks < 0.4 m are dropped.
5. **Figures.** Components on a 4x reduced ink image that are > 3 m tall and > 4 m wide, not touching the edge,
   become `diagram`, and they absorb the pieces whose centres they hold.
6. **Types, from geometry only:**
   - `equation_number`: a piece <= 4 lh wide at either column edge, >= 2 lh from the rest;
   - `math`: a row indented >= 1.5 lh on both sides, or one that has an equation number; adjacent `math`
     rows < 0.5 lh apart merge (MathPix boxes a display once);
   - `page_info`: the first or last row in the outer 7%;
   - `text`: everything else.

Scored against the MathPix lines.json. Recall and precision are at IoU >= 0.5, matched one to one, with
page_info left out of both. The YOLO route is Gemma4's lines.json (YOLO DocLayout + MFD + ink rows +
sanitize_lines), on the same page images.

| document (pages) | blob recall / precision | YOLO+Gemma (sane) recall / precision | role |
|---|---|---|---|
| Killing 1889 (48, bilevel scan) | 0.939 / 0.890 | 0.943 / 0.917 | developed on |
| Hilbert 1902 (10, grey scan, gutter shadow) | 0.859 / 0.812 | 0.876 / 0.912 | developed on |
| Penev thesis (88 pages from 22 4-up sheets), first run | **0.754 / 0.819** | 0.874 / 0.910 | held out until this run |
| Penev, after the line-height fix (step 4) | 0.855 / 0.827 | 0.874 / 0.910 | now developed on |

Time on this machine (16 cores, CPU):
- blob: 0.28 s per page for the whole process, Python and PIL, peak 64 MB. `inkcmp blocks` itself takes 9 ms. The
  Python component pass is most of the rest.
- YOLO: 4.9 s per page marginal plus 11 s model load, peak 1.8 GB, and that is before Gemma's OCR, which the YOLO
  route needs for its line boxes.

Still wrong: list items "7. a+b" are split into an equation number and maths, where MathPix keeps one list_item.
Tables and tables of contents are not modelled (simple_cell, table_of_contents_* are missed). Figures in
halftone or with detached labels are only partly absorbed. There is no held-out document left: the next test
needs a fresh one, a document with both a MathPix reading and a YOLO run.

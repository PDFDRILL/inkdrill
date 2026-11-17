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

**Judge fixes from the first board run (2026-11-17, rule 4).**
- **Unicode.** Line text goes through `latex_text` before pdflatex:
  - characters pdflatex cannot set (U+2212, Greek, arrows, x, euro, ...) become LaTeX, with maths mode tracked;
  - compatibility forms (ligatures, full-width) are NFKC-normalised;
  - raw `%`, `#` (and `&` outside maths) are escaped.
  - Text-layer readers write the real U+2212 and a raw `%` (MathPix writes LaTeX). One such line had ended the
    whole snippet document: board pages 0864 p4 and lib:88d76967110e p170 paired 0 glyphs. They now pair 3738 and
    5023 glyphs, with 0 lines not compiled.
- **Isolation.** A line pdflatex still cannot set (e.g. Cyrillic, Thai) is reported as `N L<i> NOT_COMPILED
  <error>`. It is set aside, an empty page keeps the numbering, and the run is repeated up to 12 times, so the
  other lines still pair. The culprit is the line named in "on input line N" when the error gives one.
- **Output.** JUDGE1 gains `not_compiled`, and a final line `STATUS OK|NOT_COMPILED|NO_TEXT|FONT_UNMAPPED` follows. A
  page with 0 pairs has no residual.
- **Fonts.**
  - Linux Libertine/Biolinum is mapped (`\usepackage{libertine}`), with its style suffixes (B bold, I italic).
  - A page whose text glyphs are mostly in a family with no TeX equivalent is `FONT_UNMAPPED`: no XOR is
    measured, because same-font comparison does not apply.
  - On the board's two-column pages that both readings had failed alike: lib:69bc5c71b881 p16 (CircularStd) is now
    FONT_UNMAPPED, where it had been 0.97 for both readings.
  - 1805.01788 p3 (Libertine): MathPix 0.569 -> 0.145 and ours 0.578 -> 0.188, now mostly style findings (lost
    italic/bold: 550 and 591) rather than glyph findings.
- **Engine.** It stays pdflatex. 040123 p2 gives identical G rows (176 of 2857).
- **Tests.** `tests/test_judge.py` JudgeLinesThatDoNotCompile: two U+2212 pair with 0 flags; a line with an
  undefined macro is NOT_COMPILED while the other two lines pair; `latex_text` and `style` unit cases. Removing the
  minus mapping or the isolation fails them.

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
lines.json faster than Gemma4's YOLO route, at the same accuracy?

    inkcmp lines [--threshold T] < pages.pgm        (one or more concatenated P5 pages, as gs -sOutputFile=%stdout)
    INKLINES1 page <n> <W> <H> threshold <t> lh <lh> m <m> lines <k>
    <text|math|equation_number|page_info|diagram> <x0> <y0> <x1> <y1>        (k rows, half-open page pixels)
    ...
    end

`c/inkcmp/lines.c` (2026-11-17) is a port of the scratch prototype (`work/run-files/blob-lines/blob_lines.py`, PIL).
It equalled the prototype on all 5501 lines of the three documents below, and then took two measured changes,
marked "(C)" in the steps. Tests are in `tests-c/test_inkcmp_lines.py`: a pdflatex page against pdftotext's
boxes, where every type fires, and the same page under a gutter shadow and dust gives the same lines. Per page:

1. **Ink threshold, automatic per page.** Take the Otsu split t (paper against everything else) and the dark
   class's mean d. Ink is a pixel < (d + t)/2; a bilevel page uses 128. A grey shadow or fringe lies between the
   two and drops out.
2. **Specks and shadows, by 8-connected components.** Specks < 0.25 x the median component height go, so do
   components touching the left or right page edge (a gutter shadow's core), and so do components taller than
   half the page. (C) The prototype's earlier step, blanking columns in the outer 8% with > 50% ink, is removed.
   It changed nothing on Killing and 7 lines on Penev, and it cost Hilbert precision 0.857 -> 0.821.
3. **Rows.** `inkcmp blocks` (gd3_blocks) gives the line bands. Each band is cut at white column runs
   >= 1.5 lh into pieces, and each piece is tightened to its ink.
4. **Line height** m = the 20th percentile of the heights of pieces wider than 0.3 W. (The median failed: merged
   pairs raised it.) A piece taller than 1.6 m is cut at its least-ink row when that row holds < 25% of the
   peak ink, recursively. Pieces are clipped at the column's right edge + 0.5 lh, and specks < 0.4 m are dropped.
5. **Figures.** Components on a 4x reduced ink image that are > 3 m tall and > 4 m wide, not touching the edge,
   become `diagram`, and they absorb the pieces whose centres they hold.
6. **Types, v1** (measured on the held-out set below, and replaced by v2):
   - rows are pieces overlapping vertically;
   - `equation_number` is a narrow piece at either edge of the column;
   - `math` is a row indented on both sides of the page's median text column;
   - `page_info` is the first or last row in the outer 7%.

**v2 (2026-11-17, `inkcmp lines` now), developed on 90 pages drawn with drillgroup `scripts/board_draw.py`,
seeds 7101/7102/7103, excluding the board and held-out sets (`work/run-files/lines-dev/dev-*.tsv`).**
Steps 1, 3, 4 (except the clip) and 5 are unchanged.
- 2b **Thin rules are erased** before blocks:
  - horizontal ink runs >= 12 med and vertical ones >= 4 med (med = median component height), whose
    thickness at their middle is <= 0.5 med;
  - this covers table grids, frames around text and underlines;
  - a frame around text is no longer a figure, and a table's cell text stays.
- 4 **Clip** at the rightmost column's right edge: the median right end of pieces > 0.2 W wide that end in the
  right half, + 0.5 lh. (v1 took the median line end, which on a two-column page is the gutter.)
- 6 **One line per piece, column-agnostic:**
  - a piece is `math` when it is indented >= 1.5 lh on both sides of the widest piece that overlaps it in x
    within 10 lh above or below;
  - an `equation_number` is a piece <= 4 lh wide, >= 2 lh beside a display in its row;
  - pieces of a row then join, unless a vertical white separator of unit 3 (`ic_space`, defaults) lies in the
    gap: a gutter, or a table's column gap. Equation numbers and page_info never join;
  - `math` pieces that overlap in x and lie < 0.5 lh apart become one display.

The steps of v2, measured on the 90 development pages (recall / precision, all pages). Each row adds one step:

| version | maths | german | two_column | table | scan | all |
|---|---|---|---|---|---|---|
| v1 | 0.918 / 0.905 | 0.574 / 0.808 | 0.359 / 0.540 | 0.108 / 0.605 | 0.423 / 0.365 | 0.335 / 0.596 |
| + one line per piece, new clip | 0.870 / 0.709 | 0.570 / 0.613 | 0.903 / 0.784 | 0.633 / 0.835 | 0.639 / 0.392 | 0.713 / 0.689 |
| + join across non-separator gaps | 0.877 / 0.805 | 0.581 / 0.685 | 0.900 / 0.812 | 0.539 / 0.803 | 0.607 / 0.404 | 0.668 / 0.705 |
| + widest-neighbour reference | 0.892 / 0.835 | 0.586 / 0.726 | 0.882 / 0.839 | 0.567 / 0.824 | 0.598 / 0.401 | 0.677 / 0.725 |
| + thin rules erased (v2) | 0.893 / 0.841 | 0.744 / 0.759 | 0.881 / 0.781 | 0.688 / 0.656 | 0.613 / 0.510 | **0.746 / 0.689** |

Notes:
- The last row renders every page with the longer side capped at 3500 px, so scans with a 70-inch page box no
  longer take 12 s. That cap is part of why the scan figures rose.
- Erasing rules trades table precision for German and table recall. The F1 is about equal (0.707 against 0.700).
  Rules are kept, because frames around text are common in the German stratum.
- v2 on the single-column documents: Killing 0.932/0.892, Hilbert 0.866/0.818, Penev 0.843/0.786. That is slightly
  below v1 there (0.942/0.902, 0.859/0.857, 0.855/0.844); the price of not assuming one column.
- Time: median 117 ms per page, max 146 ms (3500 px cap), of which `ic_space` is about 40 ms.

**v2 on held-out 2** (drillgroup `results/heldout2-set.tsv`, f83e586, seed 20261118, 30 pages; it excludes the
board set, held-out 1 and the development draws). Measured once, with v2 frozen at d028c8a, rendered at 300 dpi
with the longer side at most 3500 px, and scored one to one at IoU >= 0.5:

| stratum (pages) | truth | ours | matched | recall | precision |
|---|---|---|---|---|---|
| maths (8) | 311 | 319 | 263 | 0.846 | 0.824 |
| german (5) | 174 | 195 | 140 | 0.805 | 0.718 |
| two_column (6) | 651 | 654 | 617 | 0.948 | 0.943 |
| table (5) | 671 | 464 | 410 | 0.611 | 0.884 |
| scan (6, reported apart) | 364 | 582 | 237 | 0.651 | 0.407 |
| born-digital (24) | 1807 | 1632 | 1430 | 0.791 | 0.876 |
| all (30) | 2171 | 2214 | 1667 | 0.768 | 0.753 |

Time: median 131 ms per page, max 148 ms.

**The YOLO route on held-out 2** (Gemma4 c974575, gemma_lines 1db2059 column-aware, `testpool/heldout2/*.sane.lines.json`,
read once). Scored with the same one-to-one scorer, my numbers equal gemma4-ee's. One scan page has no file
(Gemma's reading failed) and counts as 0 of its 75 lines.

| stratum | YOLO+Gemma | blob v2 (d028c8a) | gate (within 2 points or ahead) |
|---|---|---|---|
| maths | 0.939 / 0.933 | 0.846 / 0.824 | **fails** (-9.3 / -10.9) |
| german | 0.793 / 0.831 | 0.805 / 0.718 | **fails** on precision (-11.3) |
| two_column | 0.940 / 0.942 | 0.948 / 0.943 | passes |
| table | 0.379 / 0.462 | 0.611 / 0.884 | passes |
| scan | 0.283 / 0.360 | 0.651 / 0.407 | passes |
| all | 0.644 / 0.712 | 0.768 / 0.753 | ahead |

Time per page: YOLO + lines + sanitize 12.9 s, plus Gemma 83 s; blob v2 131 ms.

**Gate on held-out 2: failed** on maths and German. The cost of v2's column-agnostic typing on maths pages showed
on the development set too (v1 0.918/0.905 -> v2 0.893/0.841), and v2 does not win it back. The next version
works on that, on the development draws only.

**v3 (2026-11-17, `inkcmp lines` now)** answers held-out 2's failures on maths and German. It is developed on the
same 90 development pages only; the held-out sets are untouched. Changes from v2:
- An `equation_number` must also end within 1.5 lh of its reference line's right end, or start within 1.5 lh of its
  left end (the column edge, as in v1). v2 also took narrow parts of a display as numbers; on the dev maths pages
  that gave 47 false numbers, which then split their displays.
- A row that keeps >= 3 boxes after joining is a table row: its pieces are `text` and never merge into a display.
  Without this, a table column of cells typed `math` merged into one tall box.
- `ic_space` may return up to 1024 separators (default 64), so tables keep their column gaps.
- The clip uses the 80th percentile of right ends, not the median. On pages of short lines the median cut every
  equation number off (3 dev pages, 23 numbers).
- Tried and left out:
  - a page_info band (every piece in the outer 7-10%), which traded recall for precision one for one;
  - joining a list marker across a separator, which lost German and table recall.

Measured on the development set (90 pages, recall / precision):

| version | maths | german | two_column | table | scan | all |
|---|---|---|---|---|---|---|
| v2 | 0.893 / 0.841 | 0.744 / 0.759 | 0.881 / 0.781 | 0.688 / 0.656 | 0.613 / 0.510 | 0.746 / 0.689 |
| v3 | 0.913 / 0.899 | 0.747 / 0.780 | 0.883 / 0.815 | 0.716 / 0.676 | 0.622 / 0.544 | 0.763 / 0.718 |

v3 on the single-column documents: Killing 0.932/0.908, Hilbert 0.873/0.853, Penev 0.856/0.818 (v1:
0.942/0.902, 0.859/0.857, 0.855/0.844). L1 now has a second display, with two narrow parts far apart: exactly two
equation numbers and two displays. Allowing numbers anywhere fails it.

**v3 on held-out 3** (drillgroup `results/heldout3-set.tsv`, 8626323, seed 20261119, 30 pages; it excludes the
board set, held-out 1 and 2, and the development draws). Measured once, with v3 frozen at 5245a31:

| stratum (pages) | truth | ours | matched | recall | precision |
|---|---|---|---|---|---|
| maths (8) | 282 | 296 | 266 | 0.943 | 0.899 |
| german (5) | 368 | 272 | 258 | 0.701 | 0.949 |
| two_column (6) | 622 | 636 | 540 | 0.868 | 0.849 |
| table (5) | 750 | 397 | 281 | 0.375 | 0.708 |
| scan (6, reported apart) | 538 | 469 | 212 | 0.394 | 0.452 |
| born-digital (24) | 2022 | 1601 | 1345 | 0.665 | 0.840 |
| all (30) | 2560 | 2070 | 1557 | 0.608 | 0.752 |

Time: median 139 ms per page, max 353 ms. The YOLO route on these pages comes from gemma4-ee (frozen); the gate
waits for it.

**v4 (2026-11-17, `inkcmp lines` now)**, developed on the same 90 development pages; the held-out sets are untouched.
Changes from v3:
- **Rules.** A rule is a run >= 12 med (horizontal) or >= 4 med (vertical) of which at least half the positions
  are <= 0.5 med thick, with runs found on two-pixel strips.
  - v3 sampled the thickness at one point (in a first fix, three). In a ruled table, the crossings can sit exactly
    at the sample points: in L5's 8-row table all three samples hit them and measured 570 px, so the grid
    survived and the table became one `math` box.
  - A 1-2 px rule wanders between neighbouring pixel columns, which is why runs are found on two-pixel strips.
- **m**, the line height: only pieces taller than 0.8 med count. A surviving rule (3 px) was once m, and then every
  glyph was a figure: one dev page had 512 `diagram` boxes. A first guard, >= 0.4 lh, failed where blocks' lh is
  itself inflated (Penev recall 0.856 -> 0.838).
- **Clip.** It never cuts into the inner 90% of the page width. On a page of short lines the 80th percentile cut a
  full-width line in half (L5's first line).
- **Header and footer blocks.** The rows beyond the outermost white gap >= 3 lh that lie wholly in the outer 12% of
  the page are `page_info`, like MathPix's letterhead and footer blocks. The sweep: F 0.12/0.18, G 2/3; 0.12/3 lost
  the least recall.
- **Not done:** deskewing. The one skewed dev page (a curved receipt, about 20 degrees) also has degenerate MathPix
  boxes, so it is no basis for a decision.

| version | maths | german | two_column | table | scan | all |
|---|---|---|---|---|---|---|
| v3 | 0.913 / 0.899 | 0.747 / 0.780 | 0.883 / 0.815 | 0.716 / 0.676 | 0.622 / 0.544 | 0.763 / 0.718 |
| v4 | 0.919 / 0.899 | 0.749 / 0.778 | 0.882 / 0.819 | 0.741 / 0.799 | 0.626 / 0.559 | 0.775 / 0.774 |

Single-column documents: Killing 0.931/0.901, Hilbert 0.873/0.853, Penev 0.857/0.818. Time: median 132 ms, max
176 ms per page. New test L5: a pdflatex ruled table of eight rows, where all 24 single-word cells are their own
line, with no `math` and no `diagram`, and m stays at the text's line height. Requiring a rule to be thin at every
position fails it.

**v4 on held-out 4** (drillgroup `results/heldout4-set.tsv`, fca0834, 86 pages from seeds 20261120/21/22; it
excludes the board set, held-out 1-3 and the development draws). Measured once, with v4 frozen at 0f26ea3:

| stratum (pages) | truth | ours | matched | recall | precision | dev (v4) |
|---|---|---|---|---|---|---|
| maths (24) | 910 | 936 | 807 | 0.887 | 0.862 | 0.919 / 0.899 |
| german (15) | 828 | 704 | 568 | 0.686 | 0.807 | 0.749 / 0.778 |
| two_column (18) | 1746 | 1693 | 1443 | 0.826 | 0.852 | 0.882 / 0.819 |
| table (15) | 1902 | 1553 | 1103 | 0.580 | 0.710 | 0.741 / 0.799 |
| scan (14, reported apart) | 709 | 855 | 400 | 0.564 | 0.468 | 0.626 / 0.559 |
| born-digital (72) | 5386 | 4886 | 3921 | 0.728 | 0.802 | 0.800 / 0.816 |
| all (86) | 6095 | 5741 | 4321 | 0.709 | 0.753 | 0.775 / 0.774 |

Time: median 142 ms per page, max 320 ms.
- Held-out recall is 3-16 points below dev recall, in every stratum. The dev figures are optimistic by about that much,
  and tables most of all.
- The YOLO route on these pages comes from gemma4-ee; the gate waits for it.

**Input correction for held-out 3 and 4 (2026-11-17).** In each set, one German page's folder has no source
PDF. The measurement runner took the folder's only other PDF, pdfdrill's `report.pdf`, and so measured a different
document.
- Pages: held-out 3 `lib:c8284ecd923d` p1, read by v3; held-out 4 `lib:f96282ec0392` p1, read by v4.
- The runner no longer falls back to another PDF. Without `<folder>.pdf` it reads `inspect/pages/page-NNNN.png`,
  grey and resized to the MathPix page size, which is the image gemma4-ee's YOLO route used.
- The two pages were rerun with the frozen binaries (built from 5245a31 and 0f26ea3); the code did not change.
  The corrected numbers, with the originals beside them:

| | page: matched / truth (ours) | german | all |
|---|---|---|---|
| held-out 3 as measured | 1 / 84 (8) | 0.701 / 0.949 | 0.608 / 0.752 |
| held-out 3, corrected input | 28 / 84 (69) | 0.774 / 0.856 | 0.619 / 0.743 |
| held-out 4 as measured | 0 / 24 (8) | 0.686 / 0.807 | 0.709 / 0.753 |
| held-out 4, corrected input | 18 / 24 (32) | 0.708 / 0.805 | 0.712 / 0.753 |

In the comparison with YOLO these two pages are also reported apart.

**Second development set (2026-11-17).** It was drawn after held-out 4 showed the first development set 3-16
points optimistic.
- `work/run-files/lines-dev2/dev2.tsv`: seeds 7201/7202/7203, chained exclusions of the board, held-out 1-4 and
  the first development draws.
- Scan rows are dropped (the coordinator keeps the remaining scan documents for the next held-out), which leaves 64
  pages: maths 24, two-column 18, table 15, German 7.
- v4 measures 0.635 / 0.760 on it, below its held-out 4 result: a fresh set predicts held-out again.

**v5 (2026-11-17, `inkcmp lines` now)**, developed on the second development set:
- **Threshold.** A rendered page of few grey levels has a flat Otsu maximum: every split in the empty gap between ink
  and paper scores the same. v4 took the first one, which sat at the text's own grey (a table page of black rules
  and grey-35 text: threshold 30, and 2 lines out of 283). v5 takes the middle of the flat range.
- **Ruled cells.** Erased vertical rules that span a band and are >= 3 lh long are cell boundaries. Pieces end
  there, and pieces of a row never join across them. With shorter rules, Penev's scans lost precision 0.819 ->
  0.667, through brackets and tall strokes taken for rules.
- **Tried and not taken:** a smaller piece gap (1.0 or 0.7 lh) with narrower separators (0.6 or 0.4 lh). Table recall
  rose a little, but maths and two-column precision fell. Booktabs-style tables, separated by white space alone,
  need a table region first.

| version | set | maths | german | two_column | table | scan | all |
|---|---|---|---|---|---|---|---|
| v4 | dev2 (64) | 0.864 / 0.810 | 0.422 / 0.527 | 0.741 / 0.748 | 0.468 / 0.785 | - | 0.635 / 0.760 |
| v5 | dev2 (64) | 0.889 / 0.809 | 0.475 / 0.559 | 0.841 / 0.779 | 0.585 / 0.810 | - | 0.725 / 0.783 |
| v4 | dev1 (90) | 0.919 / 0.899 | 0.749 / 0.778 | 0.882 / 0.819 | 0.741 / 0.799 | 0.626 / 0.559 | 0.775 / 0.774 |
| v5 | dev1 (90) | 0.919 / 0.895 | 0.752 / 0.779 | 0.885 / 0.807 | 0.752 / 0.789 | 0.632 / 0.554 | 0.782 / 0.766 |

Single-column documents: Killing 0.930/0.894, Hilbert 0.873/0.853, Penev 0.860/0.817. Time: median about 170 ms,
max 380 ms per page; the machine was also running other work.

New tests:
- L6: a ruled table with 2 pt column separation; all 36 cells are lines. Taking the cut out of pieces fails it.
- L7: the L1 page in three grey levels (text 35, paper 255, a black frame) gives the same 12 text lines as in black
  and white. Taking the first Otsu maximum fails it (0 lines).

Tests (`tests-c/test_inkcmp_lines.py`, against pdftotext's boxes):
- L1: every type fires; the drawn figure holds its label; the framed note's line is its own ink, not the frame.
- L2: a gutter shadow and dust change nothing.
- L3: two pages in one stream; a short second page is an error.
- L4: on a pdflatex two-column page, no text line crosses the gutter.
- Mutating rule removal fails L1, and dropping the separator check fails L4.

Scored against the MathPix lines.json. Recall and precision are at IoU >= 0.5, matched one to one, with
page_info left out of both. The YOLO route is Gemma4's lines.json (YOLO DocLayout + MFD + ink rows +
sanitize_lines), on the same page images.

| document (pages) | blob recall / precision | YOLO+Gemma (sane) recall / precision | role |
|---|---|---|---|
| Killing 1889 (48, bilevel scan) | 0.939 / 0.890 | 0.943 / 0.917 | developed on |
| Hilbert 1902 (10, grey scan, gutter shadow) | 0.859 / 0.812 | 0.876 / 0.912 | developed on |
| Penev thesis (88 pages from 22 4-up sheets), first run | **0.754 / 0.819** | 0.874 / 0.910 | held out until this run |
| Penev, after the line-height fix (step 4) | 0.855 / 0.827 | 0.874 / 0.910 | now developed on |
| **C, `inkcmp lines`:** Killing | 0.942 / 0.902 | 0.943 / 0.917 | developed on |
| C: Hilbert | 0.859 / 0.857 | 0.876 / 0.912 | developed on |
| C: Penev | 0.855 / 0.844 | 0.874 / 0.910 | developed on |

Time on this machine (16 cores, CPU):
- `inkcmp lines` (C): 19-49 ms per page (Penev, Hilbert, Killing; 1.2-5.6 Mpx), one process for the stream.
- blob prototype: 0.28 s per page for the whole process, Python and PIL, peak 64 MB. `inkcmp blocks` itself takes 9 ms. The
  Python component pass is most of the rest.
- YOLO: 4.9 s per page marginal plus 11 s model load, peak 1.8 GB, and that is before Gemma's OCR, which the YOLO
  route needs for its line boxes.

Still wrong (v1 text, kept for the record): list items "7. a+b" are split into an equation number and maths, where MathPix keeps one list_item.
Tables and tables of contents are not modelled (simple_cell, table_of_contents_* are missed). Figures in
halftone or with detached labels are only partly absorbed. The held-out test is drillgroup
`results/heldout-set.tsv` (30 pages with a MathPix lines.json, 6 of them scans; the coordinator, 2026-11-17). It
is measured once, with this C version (7c245b2), and nothing is tuned on it.

**Held-out result, 2026-11-17: gate failed.** Recall and precision are at IoU >= 0.5, one to one, against MathPix.
The pages are rendered by gs at 300 dpi (CropBox):

| stratum (pages) | truth | ours | matched | recall | precision |
|---|---|---|---|---|---|
| maths (8) | 286 | 300 | 271 | 0.948 | 0.903 |
| german (5) | 201 | 197 | 134 | 0.667 | 0.680 |
| two_column (6) | 534 | 354 | 217 | 0.406 | 0.613 |
| table (5) | 835 | 398 | 133 | 0.159 | 0.334 |
| scan (6) | 399 | 194 | 136 | 0.341 | 0.701 |
| all born-digital (24) | 1856 | 1249 | 755 | 0.407 | 0.604 |
| all (30) | 2255 | 1443 | 891 | 0.395 | 0.617 |

Time: `inkcmp lines` takes a median of 66 ms per page (process included). The maximum is 875 ms, on a large scan.

**The YOLO route on the same 30 pages** (Gemma4 e2c6117 `testpool/heldout30/*.sane.lines.json`: YOLO + Gemma +
lines + sanitize, read once). It is rescored with the same one-to-one scorer (recall / precision):

| stratum | YOLO+Gemma | blob v1 (7c245b2) |
|---|---|---|
| maths | 0.899 / 0.908 | 0.948 / 0.903 |
| german | 0.627 / 0.636 | 0.667 / 0.680 |
| two_column | 0.212 / 0.212 | 0.406 / 0.613 |
| table | 0.171 / 0.216 | 0.159 / 0.334 |
| scan | 0.313 / 0.322 | 0.341 / 0.701 |
| all | 0.339 / 0.370 | 0.395 / 0.617 |

Time per page: YOLO + lines + sanitize 9.5 s, plus Gemma 79 s on the iGPU; blob v1 66 ms.

**Gate as corrected by the coordinator (rule 5, 2026-11-17): passed.** The gate compares with YOLO on the same
pages, and blob v1 is within 2 points or ahead on every stratum; the closest is table recall at -1.2 points.
Neither route is good on two-column, table or scan pages, and the YOLO route has no column model (10 of its pages
fall back to rough boxes).

What it shows:
- The design is a single-column one, and it holds on single-column maths pages.
- Step 4's clip takes the column's right edge from the median line end. On a two-column page that is the left
  column's edge, so the right column is cut away.
- Tables: MathPix boxes each cell (simple_cell), and step 3 cuts only at gaps >= 1.5 lh.
- The YOLO route's numbers on this set are still to come (gemma4-ee).

The next version needs columns before lines (unit 3's white separators), developed on documents that are in
neither this set nor the board set.

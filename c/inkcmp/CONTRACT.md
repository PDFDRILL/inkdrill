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

**Unpaired ink (coordinator's decision, 2026-11-17, rule 1).** JUDGE1 now always prints `unpaired_A`/`unpaired_B`
(line-level, from the alignment) and the page-wide `a_glyphs` (every glyph of the original page), `a_unpaired`
(original glyphs that paired with nothing: outside any judged line, in an empty or uncompiled line, or skipped by
the alignment) and `b_unpaired` (rendered glyphs with no partner).
- The board's ink residual = (findings + a_unpaired + b_unpaired) / a_glyphs. flagged/paired stays beside it as
  "residual among paired".
- The reason: a reading that merges lines leaves glyphs unpaired, and flagged/paired did not see them (dev2 tables:
  ours 4732 unpaired original glyphs, ours+ink 661).
- 040123 p2: G rows unchanged; a_glyphs 3133, a_unpaired 276, b_unpaired 0.

**One owner line per original glyph (2026-11-17, rule 4).** Where a reading's line boxes overlap, the judge paired
an original glyph once per box. On board 2312.11532 p7 ours+ink, 3396 pairs came from 3325 glyphs, so a_unpaired
was negative. Each original glyph now belongs to the smallest leaf box that holds its centre. 040123 p2: G rows
unchanged. Test: a reading with one line boxed twice pairs no more glyphs than the page has, nor more than the
clean reading. Pairing per box fails it.

**Accents (2026-11-17, rule 4; found by pdfdrill-a2 on 2103.01507 p118).**
1. An accent glyph above a tight line box (`\bar{M}`: the macron at 201-202 px, the box from 203) belonged to no
   line and was unpaired on both sides. An unowned accent (macron, hat, tilde, dot, dieresis, caron, breve, ring,
   vector arrow, combining marks) now belongs to the line of the nearest owned glyph directly below it, within one
   glyph height.
2. On that board page there is no macron glyph at all: it sets `\overline{M}`, a drawn rule, where MathPix wrote
   `\bar{M}`. A rendered accent with no partner is now placed relative to its base's partner and XORed against the
   page's ink there (`accents_on_ink`). The bar is measured, not counted as invented ink.
   - MathPix p118: 26 rendered macrons, b_unpaired 36 -> 10. The 26 are now findings (solid 11, a real difference:
     the overline is a longer rule at another height). The ink residual is unchanged; the classification is right.
   - A reading that drops the accent still leaves the page's ink unmeasured (a rule) or the page's macron unpaired
     (a glyph).
- The line crop margin is 30 px at 400 dpi (was 7.5), so ink above a tight box is inside the crop. 040123 p2: G rows
  unchanged.
- Tests (JudgeAccents): `\bar{M}` read as `\bar{M}` pairs clean; read as `M`, the page's macron is unpaired;
  `\overline{M}` read as `\bar{M}` gives accents_on_ink 1 and b_unpaired 0. Dropping either mechanism fails them.

**Unmappable characters (2026-11-17, rule 4).** pdfdrill 660ea83c writes U+FFFD for a glyph it cannot name, and
the whole line was NOT_COMPILED: 6 of 30 lines on lib:3c16fac66fdd p370.
- A character pdflatex reports it cannot set ("Unicode character ... (U+XXXX) not set up") is now dropped from
  every snippet, and the run repeats. U+FFFD is dropped from the start.
- The line compiles and its other glyphs pair. The page's glyph there stays unpaired original ink, counted once.
- JUDGE1 `unmappable` counts the dropped occurrences, and `D U+XXXX dropped` names each character found this way.
- lib:3c16fac66fdd p370 (ours): 30 lines, 0 not compiled, unmappable 6, 1517 of 1546 glyphs paired.
- Tests (JudgeUnmappable): "4\ufffd" for "42" pairs the rest of the line, with the page's "2" unpaired; a Cyrillic
  letter is dropped through pdflatex's own error. Removing the error path fails them. The static U+FFFD entry is
  an equivalent mutant: pdflatex reports U+FFFD too, so it only saves a pass.

**Rule ink in formula lines (coordinator's decision, 2026-11-17).** The judge measured glyphs only, so a reading
that dropped an overline, fraction bar or radical vinculum passed clean. Formula lines are those of type
math/equation or with inline maths in their text.
- **Page rules:** the page's ink that none of the page's glyph quads covers.
- **Rendered rules:** the rendering's ink that no glyph covers. A paired glyph's cover is its partner's page quad
  mapped back by that pair's own offset: TeX's M quad reaches up over an `\overline`, and the page's does not.
- **What counts as a rule:** horizontal, >= 8 px long, >= 3x as long as thick, at most 6 px thick (400-dpi grid).
  It stands free: at most 30% of its pixels border glyph ink, so a glyph edge outside its quad is not one. On
  inline-maths lines it must lie over a maths-font glyph in x, so a footnote rule over a `^1` line is not one.
- **Registration:** each rendered rule moves into the page's frame by the offset of the nearest paired glyph in x.
  Displayed maths is spaced differently, so one offset per line misplaced its parts.
- **Ownership:** a page rule belongs to one line: the box that holds its centre, else the line of the glyph under
  it (an overline above a tight box), else the nearest box. The scan window reaches 0.6 box heights above and below.
- **Accents:** a page rule over an accent already measured on ink (`\bar` over a drawn `\overline`) is that accent's
  partner and is skipped.
- **Scoring:** a page rule less than half explained by rendered rule ink within 2 px is MISSING (`R L<i> missing`).
  A rendered rule with no partner is INVENTED. JUDGE1 gains `rules_a`, `rules_missing` and `rules_invented`; how
  they enter the residual is the board's call.
- **Results.** 040123 p2: G rows unchanged, rules 0/0/0. Board 2103.01507 p118: MathPix 0 page rules (its overlines
  are measured as accents on ink); ours 26 page rules, 0 missing, 0 invented. ours' "4/7" is not in the current
  ours or MathPix reading of p118.
- **Tests:**
  - JudgeRuleInk: `\overline{M}` read as M is missing, read as `\overline{M}` clean; fraction bar clean;
    dropped vinculum missing; invented overline; text lines never examined.
  - JudgeRuleOwner: the next line's overline under tight boxes.
  - JudgeRuleNotMaths: footnote rule.
  - Removing the call, the ownership or the maths filter fails them.

**T1 snippet encoding (2026-11-17, rule 4; found by pdfdrill-a2 on lib:c9daf79960c3 p68).** The snippet preamble
now has `\usepackage[T1]{fontenc}`.
- Why: in OT1, `\guillemetright` and `\TH` are unavailable (3 lines did not compile), and an OT1 `\~N` is a tilde
  glyph over an N where the page has one composed glyph.
- Effect on 040123 p2 (Times, MathPix):
  - flags unchanged, 176 of 2857 paired (166 style, 10 glyph);
  - the scored solid residual sums to 15269 against 15292 (-23, -0.15%);
  - the unscored raw XOR rises 11% on 9 of 43 lines (sub-pixel position changes of some glyphs).
  - The G rows therefore differ in xor/solid values, not in pairs or flags.
- The planted CM test is unchanged: 0 flags clean, 7 of 8 plants caught, e->c still the thin-stroke miss. Under T1,
  CM renders as the EC fonts.
- Test (JudgeT1): a T1 page with guillemets, a thorn and composed Ñ pairs with 0 flags, 0 unpaired, 0 not
  compiled. Without the T1 line it fails.

**Glyph rasteriser route (rule-6 step 7, scratch, 2026-11-17).** A's and B's glyphs come from their embedded
Type 1 programs (`mutool extract`, U9 type1 -> charstring -> scan at 400 dpi, both at A's fractional origin), with no
8x page render. On 040123 p2 (Type 1 only), against the 8x route: flags agree on 2856 of 2857 glyphs, and all 12 of
12 plants are flagged. Time: font extract and parse 0.14 s, stext 0.13 s, raster and compare 2.68 s (Python), plus a
400 dpi page render for the ink that is not glyphs, 0.08 s. That is about 3 s against the route above's 7.5 to 22 s.
In the library sample (299 documents, pp. 1-3), 72% of documents use only Type 1 and Type 1C fonts.

**Glyph-raster route in C (2026-11-17, `inkcmp-judge --route glyph`, opt-in).** `inkcmp glyphs` (glyphs.c: U9's
Type 1 parser and charstrings ported operation for operation, a CFF/Type 2 reader, scan.py's raster, the per-pair
score) and `inkcmp rules` (rules.c: rule_ink's pixel stage). The Python keeps the text side (pdflatex, stext, the
pairing). Glyph names, origins and matrices come from `mutool trace`; neighbours' glyphs in a pair's window are added
on both sides; an A glyph without an outline (no program, Type 3, an OCR layer over a scan) is the page's 400-dpi ink.
Oracles: C = U9 Python on 1324 TeX placements; CFF = Type 1 on 370; C = fontTools on all 5192 glyphs of the 109 board
CFF fonts (scratch); `inkcmp rules` = rule_pixels; the planted CM page flags the same glyphs on both routes.
Found on the way (rule 4, both fixed in the render route too): poppler rendered the MediaBox while stext is CropBox
(lib_d68e0271bd4d p334 flagged 1374 of 1380 glyphs; 1096 after); and poppler/Ghostscript place glyphs up to 0.75 px
left of mutool's origins at the end of a long snippet line (1511.08771 p2709, line 11), which the render route,
sampling poppler pixels at mutool origins, reads as residual.
Board, 30 pages, MathPix column, against the render route (CropBox fixed):
- text side (lines, glyphs, unpaired A/B, a_unpaired, b_unpaired, not compiled, accents on ink): equal on 30 of 30;
- flags: 50235 of 50671 pairs agree (99.14%); 17 pages >= 99.8%; lowest 1511.08771 92.7% (the drift), lib_6d90 93.0%;
- board residual (findings + unpaired) / glyphs: within 0.01 on 19 of 22 judged pages; outside: 1511.08771 p2709
  (-0.068), lib_d68e0271bd4d p334 (-0.021), BH1-9add65ca p113 (-0.014, an OCR layer over a scan: A is page ink);
- rules: rules_a/missing equal except BH1 (OCR layer); rules_invented differs on 6 pages;
- time: 6-231 s -> 0.9-1.9 s per page; median 1.05 s in process (load ~5), CLI 1.50 s wall at load 8-11, CPU 1.98 s.
The drift's cause (rule 4, fixed in both routes, `inkdrill/textpos.py`): MuPDF keeps /Widths as whole
thousandths of an em (496.9 -> 497) and places glyphs with them; poppler and Ghostscript use the exact widths. On
1511.08771's snippet page 12 mutool stands 0.122 pt right of /Widths at the last glyph, exactly what poppler and
Ghostscript draw. The judge now interprets each page's text operators with the exact widths and moves stext and
trace origins there (frame fitted to the glyphs after each absolute positioning; a page it cannot follow keeps
mutool's origins: on the board, the three OCR-layer pages BH1, BH3, lib_025faeafae2f). 1511.08771: render route
1716 -> 1665 flags, glyph route unchanged (it was consistent with itself); the remaining 138 render-only flags are
CMR10 against Fraktur parentheses (a style pair) at solid 7-12 against 1-6, rasteriser sampling on different
shapes.
Gate as set ("same findings", median <= 1 s): the findings part corrected -- exact equality cannot hold by
construction (the render route measures window ink at poppler positions); the time part FAILS.

**Rule 6, the judge's time (2026-11-17, before any several-day step).** State: glyph route, board's 42 judge jobs,
batch, 1.6-1.7 CPU s/page (load 9-16); 4 processes in parallel 1.45 pages/s (the minimum, 1 page/s on 4 cores, holds;
the target, 1 CPU s/page, does not). CPU of a median page: pdflatex 0.6, Python 0.6, inkcmp 0.4, mutool 0.3, poppler 0.2.
1. What others do (from knowledge, figures not re-checked): CDM (Character Detection Matching, 2024) renders both the
   predicted and the reference formula with LaTeX and matches detected characters (F1); im2latex scoring (Deng et al.
   2017) renders with pdflatex and compares images; OmniDocBench and olmOCR-bench score text by normalised edit
   distance, formulas by CDM, tables by TEDS -- none judges a page by its own ink without typesetting the reading
   again; MathPix publishes edit-distance numbers against reference text. All that render use TeX, at seconds per
   formula, not per page.
2. Alternative A (coordinator's): set the reading without pdflatex, with the page's own embedded programs (U9 has the
   outlines): each reading character -> the page font's glyph for it (ToUnicode/encoding reversed), advanced by
   /Widths, from the line's first glyph origin. Mechanism: equal characters give the page's own glyph at the page's
   own advance, so the XOR is exactly 0 there; a wrong character gives another glyph of the same font.
3. Its limit, measured now: formula lines are 21% of the lines (436 of 2115, MathPix) but carry 59% of the flags (6224
   of 10501 flagged pairs); maths needs TeX's layout, so pdflatex stays on 22 of 30 pages (21 of 24 for ours) unless
   maths is set too. And style findings (lost italic/bold) need a font the page may not embed.
4. Alternative B: the text side (pairing, owners, origins, rules) in C, beside inkcmp glyphs: Python 0.6 -> ~0.05.
5. Alternative C: pdflatex kept, run once per board run for all pages (the batch does this per preamble group);
   its content work, not start-up, is what remains (0.4-0.6 per page at load).
6. Cheapest experiment for A, hours: on the 8 MathPix board pages without formula lines, set the text lines from the
   page's fonts in Python + inkcmp glyphs and compare findings with the pdflatex route (flags, unpaired, style);
   wrong if findings differ on more than 1% of pairs or the setter needs more than 0.1 CPU s/page.
7. Cheapest experiment for B: port the pairing (difflib) alone and time it; wrong if Python's share does not fall by
   at least half.
8. Expected board change: none in the residual column (same findings), seconds per page -0.4 (A, text pages only),
   -0.5 (B, every page).
9. Order proposed: B first (every page, no semantic change), A only if B leaves the target missed on text pages.
10. Not done yet: any of them.

**Rule-6 experiments, measured (2026-11-17).**
- B (the pairing in C): decided without code. The pairing (difflib) costs 0.008-0.021 s of a page's 0.6-1.4 s of
  Python (lib_c9daf p68, 1805.01788 p3); porting it cannot halve Python's share. The rest of the Python is spread
  over stext parsing, origin lookups, job formatting and the textpos interpreter: "the text side in C" is the judge
  rewritten, not a port of one step.
- A (`tools/judge_setter.py`: the reading's text lines set in the page's own regular font program, with the page's
  own traced advances and kerning, through the unchanged judge): on the 6 judged MathPix pages without formula lines,
  flags agree with the pdflatex glyph route on 15921 of 15954 pairs (99.79%); setter 0.02-0.07 CPU s/page. Where they
  differ the setter is right more often than not: pdflatex substitutes Nimbus for the page's Times-Roman (`l` at
  xor 270 on lib_88d76967110e p170, 0 here). Its limit is the subset: a reading character the page never printed in
  that font has no glyph -- 578 of 1381 on lib_d68e0271bd4d p334 (code in CMTT that MathPix read as plain text), 1-11
  on three others -- and becomes unpaired (lib_d68e residual 0.778 -> 0.836). And maths still needs pdflatex on 22 of
  30 board pages.
- So neither reaches 1 CPU s/page on the board's median page: B's port is a rewrite, A removes pdflatex on text-only
  pages only (about 1 in 4) and needs a fallback (the TeX tree's Type 1 of the same font) for subset gaps.

**The thin-stroke miss, closed on the glyph route (2026-11-17).** e -> c at 10 pt reaches solid 4 (the crossbar is
2-3 px at 400 dpi), below 7; a lower solid or largest-piece threshold is no way out: on the board's glyph route,
pairs of one glyph name reach piece >= 23 (e -> c's) in 2.6% (1013 of 39230 unflagged). The measure that separates
them is topology, not area: `inkcmp glyphs` now reports the holes of each side's own glyph raster (4-connected white
regions enclosed by its ink, before any neighbour is added; >= 3 px), and the glyph route flags a pair whose counts
differ. Measured on the 30 board pages (MathPix and ours, glyph route, pdflatex), 89131 pairs of one glyph name:
counted from 1 px, 77 differ without a flag (raster pinholes where an arm meets a stem, mostly r); from 3 px, none.
On different glyphs it adds 4 flags (f against the ligature fl). The planted CM page now flags every plant on the
glyph route (test_every_plant_is_flagged passes there); the render route has no outlines and keeps the miss.
Oracle for the count: U9's nest() on the Python raster, 1056 placements (tests-c G5).

**Rule 6, inkcmp lines' figure step (2026-11-17; T-0154 test D: 0 of 0864's 6 MathPix figures come out whole).**
1. What others do (from knowledge, not re-checked): trained detectors (YOLO / DocLayout-YOLO, LayoutLMv3 on DocLayNet)
   predict one box per figure from pixels -- figure/picture AP about 0.8 on DocLayNet -- having learnt that axes,
   curves and labels belong together; pdfplumber exposes the PDF's lines, rects and curves but does not group them;
   PyMuPDF's `cluster_drawings` joins vector paths by distance into figure rectangles; pdfminer reports a form
   XObject as one LTFigure. None of them separates a figure from text by white space alone, which is what inkcmp does.
2. Idea: on a born-digital page, group the PDF's own drawn paths (stroke/fill, not clip) by distance, then attach the
   glyphs that lie within the same distance (labels, tick numbers); ink decides only where there are no paths.
3. Mechanism: the paths of one figure are drawn together and lie close, whatever white space separates its parts;
   body text has no paths, so it is not swept in unless the label attachment runs away.
4. Cheapest experiment, run (`tools/lines-audit/vecfig_0864.py`, 0864 against MathPix's 6 figures, IoU):
   tolerance 4 pt: 2 of 6 >= 0.5; 8 pt: 2; 12 pt: 4 (p1 chart 0.92, p3 diagram 0.96 -- whole, where inkcmp cuts it
   at white space -- p4 0.59 and 0.60); 20 pt: 2 (label attachment swallows body text).
5. Not reached: the p3 figure drawn with glyphs only (no path at all), and the p4 figure MathPix gives as one box over
   two panels (the paths make two clusters).
6. So the idea holds where figures are vector drawings, and its open parts are named: label attachment needs a stop
   (a glyph run that is a text line -- inkcmp's own line boxes -- is not a label), and glyph-only or raster figures
   stay with the ink route.
7. Next experiment, hours: label attachment bounded by inkcmp's line boxes (a line wider than 1/3 of the cluster is
   body text); gate: >= 5 of 6 at IoU >= 0.5 on 0864 without a figure swallowing a body line, then the same on 1805.01788
   and 2312.11532 before any C.
8. Board change: none directly (inkcmp lines is the Gemma inklines arm's line step, not the judge); T-0154 D turns green.

**Step 2, measured (2026-11-17, `tools/lines-audit/vecfig.py <doc> 12`; gate: >= 5/6 on 0864 with no body line
swallowed, then 1805.01788 and 2312.11532).** Candidates: the PDF's drawn paths clustered at 12 pt, AND its placed
images (`fill_image`); panels side by side in one row are one figure unless each has a text line of its own just
under it (a sub-caption: MathPix keeps those apart); labels are inkcmp's own line boxes within 12 pt, never one wider
than a third of the figure (a body line). Against MathPix's figure regions (IoU >= 0.5), body lines swallowed
(MathPix text lines outside every figure and caption whose centre falls in one):

| document | figures matched | swallowed | the misses |
|---|---|---|---|
| 0864 | 5 of 6 | 0 | p3 drawn with glyphs only |
| 1805.01788 | 16 of 16 | 0 | -- (14 are images: before images 0 of 16) |
| 2312.11532 | 6 of 8 | 0 | p3, p4: text figures (framed text, 3 rules, no drawing) |

Attempts on the way, each measured on the same pages: labels glyph by glyph (12 pt: 4/6 on 0864, at 20 pt half a page
swallowed); labels as line boxes bounded by width (3/6, 0 swallowed: the p4 panels stayed apart); panels joined in
a row (5/6 on 0864, but 4/16 and 27 swallowed on 1805.01788's sub-captioned image grid); the sub-caption exception
(the table above).
What this route cannot reach (they need the ink route or a learned detector): figures drawn with glyphs only (0864
p3), text figures whose only drawing is a few rules (2312.11532 p3, p4), and every scan (no paths, no placed vector
drawing -- a scan page is one image). Next: the same three documents through inkcmp lines with this as its figure
source on born-digital pages, so T-0154 D is measured on inkcmp's own output, then the C port.

**Held out, gate FAILED (2026-11-17; coordinator's gate: >= 80% whole, 0 body lines swallowed).** `tools/lines-audit/
vecfig_set.py audit-confirm-set.tsv 20` -- the first 20 rows of the confirm set (seed 9002, never used for figures)
whose MathPix page has a diagram or chart, the rule fixed before the run: 18 of 28 figures whole (64%), 17 body lines
swallowed. My selection let one scan in (lib:651638b87560 p4, a page-size image and an OCR layer); without it 18 of 26
(69%). The failures by cause:
- labels swallowing body text (2 pages, 17 lines): 1610.09935 p1 -- a filled background panel behind the figure widens
  the cluster and the narrow author-block lines beside it pass the width bound; 2410.19056v1 p2 -- a rule-framed text
  box (a prompt) is a cluster whose labels grow past MathPix's figure. The width bound alone does not tell a label
  from a short body line.
- no drawing at all (2 pages, 3 figures): 2303.13542v1 p7, lib:ba6e286cc422 p2 -- the whole page is one text object,
  its figures drawn with glyphs (Type 3): the known glyph-only gap, now seen on held-out pages.
- partly found (marx19a p7, entropy-23-01195-v2 p7, lib:6060cf21fa3a p4): not yet looked at.
So the sub-caption and width rules were fitted to the three development documents; it is not inkcmp's figure source.

**Held out, attempt 2, gate FAILED on swallowing (2026-11-17).** Rules fixed and committed before the run (9cbeb75):
a label is an unclaimed line box (no line of a text block: a neighbour above or below with nearly the same left
edge); framed text (<= 6 paths, >= 3 text lines inside, no image) is not a figure. Development documents: 26 of 30,
0 swallowed. The confirm set's 20 figure pages being used up by attempt 1, a fresh set was drawn and committed first
(`figdraw.py`, seed 9003: 20 born-digital pages with MathPix figures, no document of the board, held-out 1-4, the
audit sets or the development documents). Result (`vecfig_set.py figure-set-9003.tsv 20 --born-digital --framed`):
18 of 22 figures whole (82%: passes), 20 body lines swallowed on 4 pages (fails). Causes:
- 2306.02572 p12 (7 lines): three page-wide shaded text boxes -- framed text with more paths than the framed rule allows;
- 1706.08746 p2 (7 lines): text set as images (MathPix reads them as text): out of reach of a vector route;
- CM2526_exercises_08 p1 (5 lines): a header logo image and the page-wide rule under it make one figure with the
  header lines; 1807.03656 p4 (1 line).

**Held out, attempt 3 (the last vector-only one), gate FAILED (2026-11-17).** Fixed and committed before the run
(bbdc681): a cluster with no image whose area is >= 50% text lines is text (0.35 cost 2 development figures; 0.5 keeps
26 of 30); a small image at the top of the page is a page element. Fresh set `figure-set-9004.tsv` (seed 9004,
excluding the 9003 set). Result: 13 of 24 figures whole (54%), 25 body lines swallowed (1809.09528 p5: 17 lines with a
figure and its caption; p1450-yosef p2: 8). Three held-out attempts failed, each on a different page kind; the idea
changes (coordinator, 2026-11-17): figure regions come from a learned layout detector on every page, and the PDF's
paths only refine its boxes. The vector harness stays (`vecfig.py`, `vecfig_set.py`, `figdraw.py`) as the record and
as the refinement's measure.

**Rule 6, the detector route for figure regions (2026-11-17; budget <= 0.3 CPU s/page, the loop's 1 page/s).**
1. What exists (from knowledge, figures not re-checked): DocLayout-YOLO (YOLOv10, DocStructBench weights; Gemma4's
   layout arm at 1024) reports about 0.79 mAP on DocStructBench at ~85 FPS on a GPU; PP-DocLayout-S / -M (PaddleX) are
   built for CPU (the S model in the tens of ms per page on a server CPU at about 0.70 mAP@0.5, M at about 0.75); any
   YOLO detector trades input size for speed roughly with its square. Gemma4's YOLO costs 6-7 s/page on CPU because it
   also runs a formula detector at 1888 px.
2. Idea: one detector pass at a small input size gives every page its figure regions (scans, glyph-only figures and
   images of text included, which the vector route could not reach); the PDF's paths only trim its boxes.
3. Cheapest experiment, run (`tools/lines-audit/detector_dly.py`: DocLayout-YOLO, figure class, the 12 pages with
   MathPix figures of the 3 development documents, IoU >= 0.5, ONE thread, PyTorch CPU):
   input 1024: 27/30 (90%) 4.19 CPU s/page; 768: 26/30 2.52; 640: 26/30 1.62; 512: 26/30 1.12; 416: 26/30 0.81;
   320: 28/30 (93%) 0.48; 256: 21/30 (70%) 0.39.
4. ONNX Runtime at a fixed 320 x 320 square (`detector_ort.py`): 0.40 CPU s/page but 20/30 (67%) -- the square
   letterbox is not what ultralytics feeds (a rectangle with minimal padding): the preprocessing, not the runtime.
5. So the quality holds down to 320 (93% on pages it was not tuned on, better than any vector attempt held out), and
   the speed misses the budget by 0.1-0.2 s: 0.40-0.48 against 0.3.
6. Next experiment, hours: the ONNX model with ultralytics' rectangular letterbox (a portrait page at 320 is about
   256 x 320, 20% fewer pixels) and int8 dynamic quantisation (onnxruntime's quantize_dynamic); gate: >= 90% on these
   12 pages at <= 0.3 CPU s/page, then the 40 held-out figure pages (seeds 9003, 9004) at >= 80% whole, 0 swallowed.
7. If that misses, PP-DocLayout-S is the next candidate (built for CPU); it needs PaddlePaddle, a dependency to name
   before it is added.
8. Board change: none directly; the figure source of inkcmp lines (the Gemma inklines arm), T-0154 D.

**Detector route, measured (2026-11-17).** ONNX exports of DocLayout-YOLO at a fixed portrait rectangle (as ultralytics
letterboxes a page: about 256 x 320 at 320), onnxruntime on ONE thread, the page shrunk with cv2 INTER_LINEAR as
ultralytics does (PIL's antialiased shrink found 14 of 30 figures instead of 28: the preprocessing, not the model).
Development (the 12 figure pages of 0864, 1805.01788, 2312.11532):
| model | figures | CPU s/page |
|---|---|---|
| 320 x 256 fp32 | 28/30 (93%) | 0.30-0.33 (load 6-12) |
| 320 x 256 int8 (dynamic) | 28/30 | 0.332 (ConvInteger is not faster here; 19.4 MB) |
| 288 x 224 fp32 | 27/30 (90%) | 0.270 |
Held out (`tools/lines-audit/detector_set.py`, figure-set-9003 and -9004, 40 pages, 46 MathPix figures):
| model | figures whole | body lines swallowed | CPU s/page |
|---|---|---|---|
| 288 x 224 | 34/46 (74%) | 14 | 0.240 |
| 320 x 256 | 36/46 (78%) | 11 (10 on 1809.09528 p5, where MathPix splits a figure of example questions into text) | 0.286 |
Neither passes the gate (>= 80%, 0 swallowed); 320 x 256 is the best pair.
Licence and weights: the `doclayout_yolo` and `ultralytics` code is AGPL-3.0 (package metadata); the weights' copy used
(pix2text's fork of juliozhao/DocLayout-YOLO-DocStructBench) states Apache-2.0 in its card -- the original card is not
in the local cache, so the original's licence is NOT verified. The run path above needs no AGPL code: an exported ONNX
graph, onnxruntime (MIT), OpenCV's resize (Apache-2.0), numpy; the export itself used the AGPL package once. Sizes:
the .pt 40.7 MB (sha256 9a2ee022...), ONNX fp32 75.3 MB, int8 19.4 MB: fp32 needs a fetch script with its sha256
(rule 7), int8 would fit in git.
Coordinator's ruling (2026-11-17): the run path (ONNX graph, onnxruntime, OpenCV, numpy) may go into inkdrill; the
AGPL package only in the one-time `tools/detector/export_onnx.py`, never vendored or linked; the weights' Apache-2.0
card beside `tools/detector/fetch_weights.sh` (`LICENSE-WEIGHTS.md`), which downloads the weights, checks their sha256,
exports, and checks the graph's sha256 (the export is byte-identical run to run; checked for torch 2.14.1, onnxruntime
1.30.0). fp32, not int8 (not faster).
Reference disagreement, counted in the gate: 1809.09528 p5 -- a figure made of example questions, which MathPix splits
into text lines; any figure source that keeps it whole "swallows" them (vector route 17, detector 10).

**Known gap (coordinator, 2026-11-17: listed, no work now).** Pages whose text is an invisible OCR layer over a
scan (board BH1-9add65ca p113, BH3 p91, lib:025faeafae2f p153) keep mutool's glyph origins: their text operators do
not follow textpos's interpreter (1200-2360 of their glyphs land > 1 pt from mutool's origin). How far mutool's
rounding moves their glyphs is not measured; their A side is the scan's ink (no font program), so the judge pairs
there at mutool's origins.

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

`lines.c` (GLYPHDRILL3 inkcmp/ since its 7941726; inkdrill pins it) (2026-11-17) is a port of the scratch prototype (`work/run-files/blob-lines/blob_lines.py`, PIL).
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

**The YOLO route on held-out 3** (Gemma4 f2b726f, gemma_lines 1db2059, `testpool/heldout3`, read once, rescored
one to one; my numbers equal gemma4-ee's and the coordinator's):

| stratum | YOLO+Gemma | blob v3 (input-corrected) | gate (within 2 points or ahead) |
|---|---|---|---|
| maths | 0.894 / 0.955 | 0.943 / 0.899 | **fails** on precision (-5.6) |
| german | 0.508 / 0.563 | 0.774 / 0.856 | passes |
| two_column | 0.867 / 0.936 | 0.868 / 0.849 | **fails** on precision (-8.7) |
| table | 0.203 / 0.230 | 0.375 / 0.708 | passes |
| scan | 0.177 / 0.196 | 0.394 / 0.452 | passes |
| all | 0.479 / 0.528 | 0.619 / 0.743 | ahead |

- The page reported apart, lib:c8284ecd923d p1 (no source PDF; both routes read the inspect render), matched lines:
  YOLO 46/84, blob v3 28/84 (1/84 as first measured, from report.pdf).
- Without that page, german is YOLO 0.496/0.534 against blob 0.905/0.973, and all is YOLO 0.476/0.524 against blob
  0.628/0.755.
- **v3 is closed: the gate fails on maths and two-column precision.**

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

**The YOLO route on held-out 4** (Gemma4 ba6b443, gemma_lines 1db2059, `testpool/heldout4`, 86 pages, read once,
rescored one to one; equal to gemma4-ee's and the coordinator's numbers):

| stratum | YOLO+Gemma | blob v4 (input-corrected) | gate |
|---|---|---|---|
| maths | 0.903 / 0.851 | 0.887 / 0.862 | passes (within 2) |
| german | 0.597 / 0.690 | 0.708 / 0.805 | passes |
| two_column | 0.852 / 0.918 | 0.826 / 0.852 | **fails** (-2.6 / -6.6) |
| table | 0.264 / 0.282 | 0.580 / 0.710 | passes |
| scan | 0.485 / 0.446 | 0.564 / 0.468 | passes |
| all | 0.599 / 0.623 | 0.712 / 0.753 | ahead |

- The page reported apart, lib:f96282ec0392 p1 (inspect render for both routes), matched lines: YOLO 21/24, blob 18/24.
- Without that page: german YOLO 0.588/0.699 against blob 0.706/0.816; all 0.598/0.624 against 0.712/0.754.
- **v4 is closed.** The gate fails on two-column only.
- Line tuning stops here (the coordinator). Line IoU is a regression measure, and the board decides.

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

**v6, columns before lines (2026-11-17, `inkcmp lines` now; the owner: "go on with columns before lines").**
v4 failed held-out 4 on two-column only. On dev2 the largest miss class on two-column pages was "our box wider than
the truth line" (66). The cause: a gutter narrower than the piece gap (1.5 lh) and step 6's join separator (1 lh).
dev2 2110.13883 p8 has a 40 px gutter at lh 43 (0.93 lh); there gd3_blocks lines and pieces ran across both columns
and took the other column's offset baseline (29 of 87 lines matched).
- **Step 3a** finds gutters before any line is found:
  - a gutter is a unit 3 vertical separator, >= 0.75 lh wide and >= 10 lh tall, strictly inside the ink's x-range,
    with a column >= 10 lh wide on each side;
  - its ends move to the nearest white row within 1 lh;
  - the page is cut at the gutter ends into bands, and each band into cells at the middles of the gutters that
    cover it (neighbouring bands with the same cuts are one band);
  - gd3_blocks runs on each cell alone, at the page's lh;
  - step 6 also takes the gutters as join separators.
- Widths: 0.75 lh sits between a table's word gap (0.5 lh, dev2 2307.03122) and the narrowest real gutter
  (0.93 lh). With 1 lh the 2110 gutter is lost (52 matched instead of 72).
- The 10 lh column width keeps out a scanner bed's noise strip (dev1 scan_20260530_205842: 87 -> 162 boxes, 40
  matched either way) and the narrow columns of tables. Without it: dev1 all 0.784/0.748, dev2 0.743/0.789.
  Table cells are the table step's work (pdfdrill-a2), not this one's.
- **Tried and not taken:**
  - bridging gutter pieces less than 2 lh apart: it carried a "gutter" through whole text lines;
  - requiring ink within 2-4 lh on both sides of a gutter on >= 1/4 to 1/2 of its rows: this test was redundant
    beside the width test, equal or slightly worse on both sets, and is gone.

| version | set | maths | german | two_column | table | scan | all |
|---|---|---|---|---|---|---|---|
| v5 | dev2 (64) | 0.889 / 0.809 | 0.475 / 0.559 | 0.841 / 0.779 | 0.585 / 0.810 | - | 0.725 / 0.783 |
| v6 | dev2 (64) | 0.889 / 0.809 | 0.475 / 0.559 | 0.875 / 0.798 | 0.585 / 0.810 | - | 0.737 / 0.791 |
| v5 | dev1 (90) | 0.919 / 0.895 | 0.752 / 0.779 | 0.885 / 0.807 | 0.752 / 0.789 | 0.632 / 0.554 | 0.782 / 0.766 |
| v6 | dev1 (90) | 0.919 / 0.895 | 0.755 / 0.781 | 0.886 / 0.805 | 0.752 / 0.789 | 0.634 / 0.556 | 0.783 / 0.766 |
| v5 | held-out 4 (86) | 0.927 / 0.823 | 0.721 / 0.807 | 0.828 / 0.844 | 0.590 / 0.708 | 0.564 / 0.466 | 0.723 / 0.744 |
| v6 | held-out 4 (86) | 0.927 / 0.823 | 0.722 / 0.807 | 0.856 / 0.862 | 0.590 / 0.708 | 0.564 / 0.466 | 0.732 / 0.750 |

- Held-out 4 was measured before (v4, and YOLO). v5 was rerun on it from HEAD 5600508 so the column step's own
  share shows: it is the v5 -> v6 row. The lib:f96282ec0392 p1 inspect-render page is unchanged (18/24).
- **Against YOLO on held-out 4, two-column** (0.852 / 0.918): recall now passes (+0.4), precision still fails (-5.6).
  Every other stratum keeps v4's result against YOLO.
- Single-column documents: Killing 0.930/0.894 and Penev 0.860/0.817, unchanged; Hilbert 0.873/0.853 ->
  0.889/0.863.
- Time: held-out 4 median 117 -> 150 ms, max 147 -> 191 ms per page (the second unit 3 call).

New tests:
- L8: the L4 page with an 8 pt gutter at 10 pt type (0.88 lh). No line crosses it, and every pdftotext line is
  found (IoU >= 0.7). It fails when the cells are dropped (23 lines cross), when the gutters are not join separators
  (22), and when gutter ends are not moved to white rows.
- L9: a table of eight columns 2-4 lh wide and 0.8 lh apart; every box over it spans the table, as in v5. It fails
  without the 10 lh column width.

**v7 (2026-11-17, an audit chat; `inkcmp lines` now). Harness and sets: `tools/lines-audit/`.**

What the audit found, each seen on a page first:
- **The truth counts containers as lines.** A MathPix lines.json is a tree: `column`, `table`, `table_row`,
  `table_column`, and every node with `children_ids` (`list_item`, `abstract`, `footnote`, ...) hold other lines. The
  scorer counts all of them. No line finder can match a column box, so recall has a ceiling well below 1 (two-column
  pages: 66 `column` boxes in 30 pages; 1910.08395 p10 scores 56/78 raw and 56/56 on its leaves). Both routes carry
  the same handicap, so the comparison with YOLO stands; the absolute figures do not. `score.py` prints **raw** (the
  committed scorer) and **leaf** (containers dropped) side by side.
- **A dark page was read as ink** (Uebung12 p2: white text on black, 3 of 47 lines). Step 1b.
- **Gutters came back in pieces, or not at all.** Unit 3's cover keeps white rectangles by decreasing area over both
  classes and cuts a candidate where a kept one spans its width. Every paragraph gap that lines up in both columns is
  wider than the gutter and cut it (postscriptexamples23 p2: gutter y 294-2568, reported from 992). And step 3a's
  width test let any white rectangle beside a gutter veto it (the white in a figure, 26 px away).
- **Rows of one display were separate boxes**, and a matrix under a diagram's line was a "table".
- **Norm bars, determinant bars and tall brackets were table rules** and cut their display into cells.

Changes from v6:
- 1b **Polarity.** When more than half of the page is below the threshold, the grey values are inverted and the
  threshold is taken again.
- 2b **A vertical rule is a cell boundary only in a grid:** a horizontal rule must meet it. Others are still erased.
- 3a **Separators, tallest first** (`white_candidates`, `tall_white`). One scan gives every maximal white rectangle
  >= 0.75 lh wide and >= 8 lh tall. Candidates are taken by decreasing height; one is kept when it has ink within
  3 lh on each side in >= 1/5 of its rows (the two sides counted apart: two columns' lines need not share rows) and
  lies <= 20% inside those kept. Gutters: >= 10 lh tall; two side by side < 1 lh apart sharing 80% of the taller's rows
  are one (a speck in the gutter). The 10 lh column width is measured to the page edge, to kept gutters, and to
  candidates >= 80% as tall (a table's equal column gaps still veto each other, L9; a figure's short white does not).
- 6 **Join separators** are the same candidates >= 1 lh wide and >= 8 lh tall (sweep 4/6/8 x 1.0/1.25), plus: pieces
  >= 3 lh apart never join unless both are maths (sweep 2/3/4/6).
- 6 **Displays.** A piece is maths when indented >= 1.5 lh on both sides and either not left-anchored (its row's
  left indent >= 0.35 x the right one) or cut from a piece >= 1.4 m tall. Maths pieces that overlap in x and lie < 1 lh
  apart, or share rows < 3 lh apart, are one display; a text or maths piece whose centre lies in a display joins it.
- 6 **Table rows** (>= 3 boxes become text) need a horizontal rule above and one below within 30 lh, each over half
  the box's width. Without them the row stays maths (a matrix).
- **Speed.** The white scan ran twice at full resolution (73 of ~125 ms on a 6 Mpx page). It now runs once, on the
  run list reduced 2 x 2 when lh >= 16 px (10 ms; results equal the full-resolution scan's on the dev set). The rule
  search reads the page in memory order without a branch per pixel (30 -> 12 ms, boxes byte-identical on 111 pages).

Measured (recall / precision; raw = committed scorer, leaf = containers dropped). Pages rendered with poppler, not
Ghostscript. Only library documents whose PDF is in git could be rendered: 76 of the 176 held-out pages (3 scans).

| set | version | raw all | leaf all | leaf german | leaf maths | leaf table | leaf two_column | ms/page median |
|---|---|---|---|---|---|---|---|---|
| audit dev (111), developed on | v6 | 0.783 / 0.838 | 0.872 / 0.832 | 0.748 / 0.616 | 0.955 / 0.807 | 0.781 / 0.841 | 0.947 / 0.900 | 153 |
| | v7 | 0.817 / 0.875 | 0.915 / 0.873 | 0.865 / 0.772 | 0.961 / 0.856 | 0.854 / 0.882 | 0.966 / 0.917 | 79 |
| held-out 1-4, the 76 renderable pages; never developed on | YOLO+Gemma | 0.595 / 0.651 | 0.674 / 0.638 | 0.634 / 0.677 | 0.959 / 0.858 | 0.336 / 0.303 | 0.921 / 0.931 | 9800-18900 |
| | v6 | 0.716 / 0.809 | 0.823 / 0.806 | 0.795 / 0.668 | 0.967 / 0.888 | 0.688 / 0.774 | 0.925 / 0.876 | 158 |
| | v7 | 0.769 / 0.835 | 0.886 / 0.833 | 0.882 / 0.819 | 0.980 / 0.869 | 0.787 / 0.769 | 0.961 / 0.905 | 84 |
| audit confirm (85, no German, no scan); read after the code was frozen | v6 | 0.738 / 0.851 | 0.843 / 0.847 | - | 0.957 / 0.873 | 0.718 / 0.830 | 0.938 / 0.854 | 136 |
| | v7 | 0.753 / 0.865 | 0.862 / 0.863 | - | 0.965 / 0.892 | 0.738 / 0.839 | 0.960 / 0.874 | 77 |

- My YOLO scores equal gemma4-ee's per-page table on all 41 held-out 4 pages I could render (matched and box counts).
- Still behind YOLO: **two-column precision** (0.905 against 0.931 leaf; 0.906 against 0.932 raw). Maths precision is
  1 point ahead of YOLO and 2 points below v6 on the held-out pages.
- The dev gain (+4.3 / +4.1 leaf) is about twice the confirm gain (+1.9 / +1.6): the dev set held the dark page and
  the pages the gutter faults were found on.
- The confirm set was read twice: once before the L9 change to the column-width test (0.861 / 0.862), once after.
- Tried and not taken: column width 5 lh instead of 10 (dev leaf 0.922 / 0.875, table +1.2 recall; it contradicts L9);
  both-sides ink in 1/8 of the rows (dev 0.923 / 0.873; L1 fails).

New tests: L10 (the two-column page inverted gives the same 88 lines; committed code: 19) and L11 (an align block of
three rows is one display with three equation numbers; committed code: three displays).

**v7 re-measured here (2026-11-17, inkdrill CLI session; fast-forwarded main to it).** Ghostscript route
(`tools/lines-audit/gsrun.py`: gs pgmraw, crop box, 300 dpi, longer side <= 3500 px; a folder without its PDF uses its
inspect render), so all 176 held-out pages ran, 32 scans among them. Binaries built from 8fe82b8 and cf15e65.
Leaf recall / precision (raw in brackets for ALL):

| set | stratum | v6 | v7 |
|---|---|---|---|
| held-out 1-4 (176) | german (30) | 0.851 / 0.811 | 0.921 / 0.844 |
| | maths (48) | 0.961 / 0.850 | 0.969 / 0.852 |
| | scan (32) | 0.665 / 0.497 | 0.745 / 0.522 |
| | table (30) | 0.654 / 0.714 | 0.748 / 0.742 |
| | two_column (36) | 0.924 / 0.873 | 0.957 / 0.898 |
| | ALL | 0.808 / 0.758 (raw 0.705 / 0.764) | 0.866 / 0.778 (raw 0.753 / 0.782) |
| confirm (85) | maths (30) | 0.965 / 0.887 | 0.969 / 0.893 |
| | table (25) | 0.725 / 0.837 | 0.744 / 0.849 |
| | two_column (30) | 0.941 / 0.837 | 0.963 / 0.877 |
| | ALL | 0.848 / 0.845 (raw 0.742 / 0.848) | 0.866 / 0.869 (raw 0.758 / 0.872) |

inkcmp ms/page median: held-out v6 145 -> v7 82; confirm 155 -> 91. Gate (fixed before the run): v7 leaf ALL recall and
precision >= v6 on both sets, no stratum's precision > 2 points below v6, tests green: **holds** (every stratum's
precision rises, +0.2 to +4.0). 2110.13883 p8: the 3 text lines that crossed the gutter under v6 are 0 under v7.

One change at a time taken back out of v7 (leaf, held-out 1-4 ALL; v7 0.866 / 0.778):
- no polarity (1b): 0.860 / 0.772; one page differs (the dark German page: german 0.921/0.844 -> 0.876/0.795);
  confirm unchanged.
- full-resolution white scan instead of 2 x 2: 0.868 / 0.778, 20 of 176 pages differ (scan precision 0.522 -> 0.517,
  table 0.748/0.742 -> 0.755/0.746); confirm 7 pages differ, ALL 0.866/0.868; 106 ms instead of 81. The audit's
  "equal to the full-resolution scan" holds on its dev set, not here; the net is within 0.002.
- no ink-on-both-sides test (3a): 0.871 / 0.764, 75 pages differ: precision -1.4 (scan -3.1, two_column -0.9,
  german -1.6), recall +0.5; confirm 0.871 / 0.865.
- candidates by area instead of height (3a): 0.860 / 0.779, 28 pages differ: two_column recall 0.957 -> 0.950, table
  0.748 -> 0.736; confirm 0.855 / 0.867.

**ours+ink on the board (T-0148, 2026-11-17, scratch `work/run-files/board-ink/`).** The question: does
regrouping `pdfdrill glyphlines` lines by `inkcmp lines` boxes move the board's ours column? The gate: the ink
residual is lower on >= 4 of the 5 table rows, and no maths row is worse.
- **Attempt 1** (one-page extracts; re-cut lines rebuilt from text-layer characters): it failed. Table rows were
  3 lower and 2 equal, and 4 maths rows were worse, because re-cut display lines lost their LaTeX.
- **Attempt 2.** Maths/equation lines and lines with inline maths are never re-cut (variant B, chosen on dev2: maths
  rows worse 1/24, against 7/24 for maths/equation lines only).
  - Set-up: pdfdrill 27455713+ and judge db1e766, through board_run.py's own `row()`: three-page extracts, the
    judged page n, the corrected residual.
  - A first run is invalid: my harness's copy dropped `judged-page.txt`, so ours+ink was judged on page 1 of the
    extract. Rerun with a check that both columns judge the same page.
  - Table rows, residual (covered), ours -> ours+ink:
    - 1511.08771 p2709: 0.0186 (0.985) -> 0.0139 (0.987);
    - lib:d68e0271bd4d p334: 1.443 (0.119) -> 1.372 (0.083);
    - 2312.11532 p7: 0.537 (0.714) -> 0.157 (0.952), but CER 0.105 -> 0.286;
    - RE4B-DE p559 (FONT_UNMAPPED) and lib:025faeafae2f p153 (scan, NOT RUN): not measurable for either column.
  - Maths rows: none worse; lib:c57ac038ae5f p293 goes 0.2315 -> 0.2298, and the others are equal.
  - Cells matched against MathPix (IoU >= 0.5), ours -> ours+ink: 0 -> 2 of 33, 0 -> 0 of 155, 0 -> 10 of 356,
    1 -> 5 of 10, 5 -> 5 of 24. The regrouper splits a little, and it is not cell segmentation.
  - **Gate by its letter: FAIL.** 3 of 5 table rows are lower, 0 higher, and the other 2 cannot move for any
    reading. Maths: 0 worse.

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

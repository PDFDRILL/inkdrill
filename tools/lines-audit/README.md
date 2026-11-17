# lines-audit: the harness of the 2026-11-17 audit of `inkcmp lines` (CONTRACT.md unit 7, v7)

Written in an audit sandbox; paths are that sandbox's (`/home/claude/...`), change `LIB` and the work paths to rerun.

- `resolve.py`, `prep.py`: resolve a set TSV against pdfdrill-library and render each page at MathPix's page size
  (poppler `pdftoppm -cropbox -gray`; the sandbox had no Ghostscript) to PGM, with the page's MathPix lines as truth.
- `draw.py`: drillgroup `board_draw.py`'s strata over the library documents whose PDF is in git; scan = `pdffonts`
  lists no embedded font. `audit-dev-set.tsv` (seed 9001, 111 pages) and `audit-confirm-set.tsv` (seed 9002, 85
  pages) exclude the board and held-out 1-4.
- `run.py`: runs `inkcmp lines` per page and stores the boxes; `score.py`: recall/precision per stratum, **raw** (the
  committed scorer: every MathPix node counts) and **leaf** (containers dropped: nodes with `children_ids`, and
  `column`, `table`, `table_row`, `table_column`); `yolo.py`: the committed YOLO+Gemma files on the same pages.
- `miss.py`: every unmatched line by kind (merged, split, wide, shifted, none); `overlay.py`: truth and boxes on the
  page; `pdiff.py`: pages that differ between two runs.

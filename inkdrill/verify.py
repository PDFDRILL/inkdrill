"""inkdrill verify -- a document checked against its original PDF by ink.

    python3 -m inkdrill verify --pairs verify.pairs.json \
        --original x.pdf --candidates verify.candidates.pdf \
        -o verify.findings.json

The judge of contract inkverify/1 (drillgroup contracts/inkverify.md, as
amended 2026-11-14, drillgroup a712625; task T-0146 part A). pdfdrill
prepares the inputs (units 1-2) and reads the findings; this module is
units 4-6 and reads only the three files it is given. It never imports
pdfdrill and never reads the model.

STATUS (T-0146 A2). Built: the pairs reader, every run refusal, the pair
states, the closed lists, the pt-to-pixel rule and the findings writer.
NOT built: the content, position and closure judges (A3-A5). Until they
exist, a run whose inputs pass every precondition is refused with a line
saying so -- there is no findings file a judge did not produce.

Guarantees:

G1  EXIT CODES. 0 if and only if the findings file was written. 2 if and
    only if the run was refused; then stderr carries ONE line,
    "inkdrill verify: refused: <cause>. Fix: <what to run>", and no
    findings file is written. A file already at the output path is left
    as it was -- it names its own inputs' sha256, so it is recognisable
    as another run's.
G2  REFUSALS BEFORE WORK. Every run-level cause is checked before any page
    is rendered: a malformed pairs file, contract or units or dpi not the
    contract's, no container_types, an input file missing, either sha256
    not the pairs file's record, gs not on PATH, a page or candidate page
    absent from its PDF, no pair carrying a region. Each refusal has a
    test that fires it and a test where the same input with that one
    field corrected passes it.
G3  ONE STATE PER PAIR, AND THE FIVE STATE COUNTS SUM TO THE PAIRS. Each
    pair's state comes from `decide_state` alone, once. The header's
    counts are computed from the records, never kept separately, and the
    writer raises if they do not sum to the number of pairs in the pairs
    file.
G4  CLOSED LISTS. STATES, KINDS and REASONS are the contract's, with the
    amendments. A record or finding outside them raises; a new kind goes
    into the contract before it is added here. `speckle` is listed and
    never emitted (amendment 1).
G5  THE PIXEL RULE (amendment 3). rect_pt is measured from the top-left of
    the CropBox, y down; at `dpi`, px0 = floor(x0*dpi/72), py0 =
    floor(y0*dpi/72), px1 = ceil(x1*dpi/72), py1 = ceil(y1*dpi/72), and
    the pixels covered are the half-open [px0, px1) x [py0, py1) --
    `raster.Rect`'s own convention. One function, `rect_px`.
G6  THE HEADER STATES WHAT THE RUN ASSUMED (amendment 5): contract,
    inkdrill revision, both PDFs' sha256 and page counts, threshold,
    polarity (original auto, claim dark-on-white forced), dpi, floor and
    floor_source. Before G-0 has run: floor 0, floor_source "G-0 not run".
G7  NO RASTER OR CACHE ON DISK (amendment 8). Rendering goes through
    `ghost.render_masks`, a pipe; this module writes one file, the
    findings, and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib

from .ghost import GhostError, available as gs_available, page_count
from .raster import Rect
from .version import resolve as inkdrill_revision

CONTRACT = "inkverify/1"
UNITS = "pt"
DPI = 400
#: The binarisation level, passed explicitly to every reader (amendment 5).
#: 200 is what `compare`, `locate`, `residual` and `inkresidual` use.
THRESHOLD = 200
POLARITY = {"original": "auto", "claim": "dark-on-white"}
#: Before G-0 (T-0146 deviation 4): no floor has been measured, so only an
#: exact agreement agrees. Read from the registered G-0 result once it runs.
FLOOR = 0
FLOOR_SOURCE = "G-0 not run"

STATES = ("agree", "differ", "displaced", "unmeasurable", "not_attempted")

KINDS = ("missing_component", "surplus_component", "hole_count",
         "stack_structure", "region_overrun", "speckle", "unclaimed_ink",
         "region_without_ink", "straddling_ink", "overlapping_regions")
#: Listed in the contract, not emitted until an instrument is registered.
NOT_EMITTED = frozenset({"speckle"})

#: Pair-level reasons and the state each one gives (the contract's table).
REASONS = {
    "no_region": "not_attempted",
    "no_latex": "not_attempted",
    "compile_failed": "unmeasurable",
    "region_outside_page": "unmeasurable",
    "region_spans_pages": "unmeasurable",
    "truth_empty": "unmeasurable",
    "claim_empty": "unmeasurable",
}
#: Reasons only a measurement can give; a pairs file carrying one is
#: claiming a measurement unit 1 cannot have made.
MEASURED_REASONS = frozenset({"truth_empty", "claim_empty"})

FIX_PAIRS = "rebuild the pairs file with `pdfdrill inkverify <original.pdf>`"


class Refusal(Exception):
    """The whole run stops (G1): `cause` and the `fix` that removes it."""

    def __init__(self, cause: str, fix: str):
        super().__init__(f"{cause}. Fix: {fix}")
        self.cause = cause
        self.fix = fix

    def line(self) -> str:
        return f"inkdrill verify: refused: {self.cause}. Fix: {self.fix}"


class NotBuilt(Refusal):
    """A judge this run needs does not exist yet (T-0146 A3-A5)."""


# ---------------------------------------------------------------- geometry

def rect_px(rect_pt, dpi: int = DPI) -> Rect:
    """rect_pt [x0, y0, x1, y1] (CropBox top-left, y down) to the half-open
    pixel rectangle it covers at `dpi` (G5)."""
    x0, y0, x1, y1 = rect_pt
    return Rect(math.floor(x0 * dpi / 72), math.floor(y0 * dpi / 72),
                math.ceil(x1 * dpi / 72), math.ceil(y1 * dpi / 72))


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------- the pairs file

def _malformed(what: str) -> Refusal:
    return Refusal(f"pairs file malformed: {what}", FIX_PAIRS)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def read_pairs(path) -> dict:
    """Parse and validate a pairs file; every defect is a Refusal (G2)."""
    try:
        doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise Refusal(f"cannot read the pairs file {path}: "
                      f"{exc.strerror or exc}", FIX_PAIRS)
    except ValueError as exc:
        raise _malformed(f"{path} is not JSON ({exc})")
    if not isinstance(doc, dict):
        raise _malformed("the top level is not an object")
    if doc.get("contract") != CONTRACT:
        raise Refusal(f"pairs file contract is {doc.get('contract')!r}, "
                      f"this judge reads {CONTRACT!r}", FIX_PAIRS)
    if doc.get("units") != UNITS:
        raise Refusal(f"pairs file units is {doc.get('units')!r}, not "
                      f"'pt' -- a scale error looks exactly like a "
                      f"finding", FIX_PAIRS)
    if doc.get("dpi") != DPI:
        raise Refusal(f"pairs file dpi is {doc.get('dpi')!r}; the contract "
                      f"fixes {DPI}", FIX_PAIRS)
    ct = doc.get("container_types")
    if not isinstance(ct, list) or not all(isinstance(t, str) for t in ct):
        raise Refusal("pairs file has no container_types list (amendment "
                      "2: unit 1 writes it, the judge hard-codes none)",
                      FIX_PAIRS)
    for side in ("original", "candidates"):
        rec = doc.get(side)
        if not isinstance(rec, dict) or not isinstance(rec.get("sha256"),
                                                       str):
            raise _malformed(f"no {side}.sha256")
    pairs = doc.get("pairs")
    if not isinstance(pairs, list):
        raise _malformed("no pairs list")
    seen = set()
    for i, p in enumerate(pairs):
        _check_pair(i, p, seen)
    return doc


def _check_pair(i: int, p, seen: set) -> None:
    if not isinstance(p, dict):
        raise _malformed(f"pair {i} is not an object")
    pid = p.get("id")
    if not isinstance(pid, str) or not pid:
        raise _malformed(f"pair {i} has no id")
    if pid in seen:
        raise _malformed(f"pair id {pid!r} appears twice (unit 1 lists "
                         f"every object exactly once)")
    seen.add(pid)
    page = p.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise _malformed(f"pair {pid!r} has page {page!r}")
    reason = p.get("reason")
    if reason is not None and reason not in REASONS:
        raise _malformed(f"pair {pid!r} has reason {reason!r}, not one of "
                         f"the contract's")
    if reason in MEASURED_REASONS:
        raise _malformed(f"pair {pid!r} carries {reason!r}, which only "
                         f"the judge can measure")
    r = p.get("rect_pt")
    if r is None:
        if reason != "no_region":
            raise _malformed(f"pair {pid!r} has no rect_pt and reason "
                             f"{reason!r}, not 'no_region'")
    else:
        if reason == "no_region":
            raise _malformed(f"pair {pid!r} has a rect_pt and reason "
                             f"'no_region'")
        if (not isinstance(r, list) or len(r) != 4
                or not all(_is_num(v) for v in r)
                or not (r[0] < r[2] and r[1] < r[3])):
            raise _malformed(f"pair {pid!r} rect_pt {r!r} is not "
                             f"[x0, y0, x1, y1] with x0 < x1, y0 < y1")
    cp = p.get("candidate_page")
    if cp is None:
        if reason is None:
            raise _malformed(f"pair {pid!r} has no candidate_page and "
                             f"no reason")
    elif not isinstance(cp, int) or isinstance(cp, bool) or cp < 1:
        raise _malformed(f"pair {pid!r} has candidate_page {cp!r}")


# ----------------------------------------------------------- preconditions

def check_preconditions(doc: dict, original, candidates) -> dict:
    """Every run-level refusal not already raised by `read_pairs` (G2).

    Returns the facts the header needs: both sha256 and page counts.
    """
    if not any(p.get("rect_pt") is not None for p in doc["pairs"]):
        raise Refusal("no object in the pairs file carries a region -- "
                      "there is nothing to check",
                      "run `pdfdrill geometry <original.pdf>`, then "
                      "rebuild the pairs file")
    for name, path in (("original", original), ("candidates", candidates)):
        if not pathlib.Path(path).is_file():
            raise Refusal(f"the {name} PDF {path} does not exist",
                          f"pass the {name} PDF the pairs file was built "
                          f"from")
    facts = {}
    for name, path in (("original", original), ("candidates", candidates)):
        got = sha256_of(path)
        want = doc[name]["sha256"]
        if got != want:
            what = ("the regions describe another file" if name ==
                    "original" else "page k may hold another object")
            raise Refusal(f"the {name}'s sha256 {got[:12]} differs from "
                          f"the pairs file's record {want[:12]} -- {what}",
                          FIX_PAIRS)
        facts[name] = {"path": str(path), "sha256": got}
    if not gs_available():
        raise Refusal("Ghostscript (gs) is not on PATH",
                      "install Ghostscript and put `gs` on PATH")
    for name, path, key in (("original", original, "page"),
                            ("candidates", candidates, "candidate_page")):
        try:
            n = page_count(path)
        except GhostError as exc:
            raise Refusal(f"gs cannot read the {name} PDF: {exc}",
                          f"check that {path} is a complete PDF")
        facts[name]["pages"] = n
        for p in doc["pairs"]:
            k = p.get(key)
            if k is not None and k > n:
                raise Refusal(f"pair {p['id']!r} names {key} {k}, and the "
                              f"{name} PDF has {n} pages",
                              FIX_PAIRS)
    return facts


# ------------------------------------------------------------------ states

def decide_state(reason, *, distance=None, located=None,
                 floor: int = FLOOR) -> str:
    """The one state of one pair (G3).

    `reason` set: the contract's table decides. Otherwise the pair was
    measured: a distance above `floor` is `differ`; within it, `agree` if
    the claim was located inside its region and `displaced` if it was
    located elsewhere or not at all.
    """
    if reason is not None:
        if reason not in REASONS:
            raise ValueError(f"reason {reason!r} is not in the contract")
        return REASONS[reason]
    if not isinstance(distance, int) or isinstance(distance, bool) \
            or distance < 0:
        raise ValueError(f"a measured pair needs a distance, got "
                         f"{distance!r}")
    if distance > floor:
        return "differ"
    if located is None:
        raise ValueError("a pair within the floor needs a position result")
    return "agree" if located else "displaced"


def finding(kind: str, rect_pt, evidence: dict, target: str,
            say: str) -> dict:
    """One finding of a closed kind (G4)."""
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r} is not in the contract")
    if kind in NOT_EMITTED:
        raise ValueError(f"kind {kind!r} is listed but not emitted until "
                         f"its instrument is registered")
    return {"kind": kind, "rect_pt": rect_pt, "evidence": evidence,
            "advice": {"target": target, "say": say}}


def pair_record(pair: dict, state: str, *, reason=None, truth=None,
                claim=None, distance=None, located_pt=None, overlap=None,
                findings=()) -> dict:
    if state not in STATES:
        raise ValueError(f"state {state!r} is not in the contract")
    for f in findings:
        if f.get("kind") not in KINDS or f.get("kind") in NOT_EMITTED:
            raise ValueError(f"finding kind {f.get('kind')!r} not allowed")
    if state == "differ" and not findings:
        raise ValueError(f"pair {pair['id']!r}: differ carries at least "
                         f"one finding")
    return {"id": pair["id"], "type": pair.get("type"),
            "page": pair["page"], "state": state, "reason": reason,
            "truth": truth, "claim": claim, "distance": distance,
            "located_pt": located_pt, "overlap": overlap,
            "findings": list(findings), "ask_model": False}


def findings_document(doc: dict, facts: dict, records: list,
                      pages: list) -> dict:
    """The findings file's content (G3, G6). Raises if a pair is missing,
    repeated, or the state counts do not sum to the pairs."""
    want = [p["id"] for p in doc["pairs"]]
    got = [r["id"] for r in records]
    if sorted(got) != sorted(want) or len(set(got)) != len(got):
        raise ValueError("records do not cover every pair exactly once")
    counts = {s: 0 for s in STATES}
    for r in records:
        counts[r["state"]] += 1
    # Implied by the exactly-once check above (an equivalent mutant in the
    # sweep: no input reaches it). Kept because it is the contract's named
    # assertion, and it is the one that survives a later edit of the check.
    if sum(counts.values()) != len(doc["pairs"]):
        raise ValueError("state counts do not sum to the number of pairs")
    return {
        "contract": CONTRACT,
        "inkdrill": {"revision": inkdrill_revision()},
        "original": facts["original"],
        "candidates": facts["candidates"],
        "language": doc.get("language"),
        "dpi": DPI, "threshold": THRESHOLD, "polarity": dict(POLARITY),
        "floor": FLOOR, "floor_source": FLOOR_SOURCE,
        "pairs": len(doc["pairs"]),
        "state_counts": counts,
        "records": records,
        "pages": pages,
    }


# ---------------------------------------------------------------- the run

def run(pairs_path, original, candidates, out) -> int:
    """The whole command (G1). Returns the exit code; prints a refusal's
    one line to stderr."""
    import sys
    try:
        out = pathlib.Path(out)
        if not out.parent.is_dir():
            raise Refusal(f"the output directory {out.parent} does not "
                          f"exist", "create it or pass another -o")
        doc = read_pairs(pairs_path)
        check_preconditions(doc, original, candidates)
        # Every pair with a region needs closure (unit 6) and every
        # attempted pair content and position (units 4-5); none is built.
        raise NotBuilt("the content, position and closure judges are not "
                       "built yet (T-0146 A3-A5), so no findings can be "
                       "produced", "wait for T-0146 A3-A5")
    except Refusal as r:
        print(r.line(), file=sys.stderr)
        return 2

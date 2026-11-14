"""Ghostscript, behind one door: a PDF to its page count, or to masks on a pipe.

    page_count(pdf)                       -> int
    render_masks(pdf, dpi=400, threshold=200, first=None, last=None)
                                          -> iterator of InkMask, one per page

The only module in the package that runs Ghostscript (T-0146, inkverify/1).
Owner question 2 -- who rasterises -- is open; keeping every gs call here
means either answer changes this file and nothing else.

G1  NO RASTER ON DISK. gs writes `pgmraw` to its stdout
    (`-sOutputFile=%stdout`) and `pnmio.stream_masks` reads the pipe a row
    at a time. No file is created, temporary or otherwise.
G2  THE CROPBOX FRAME (inkverify/1 amendment 3). Every render passes
    `-dUseCropBox`, so pixel (0, 0) is the top-left of the CropBox, y down.
    Without it gs renders the MediaBox, and on a page whose CropBox differs
    every region is off by the crop offset -- silently, since both frames
    hold ink. Measured on a 612x792 pt page cropped to [36 0 612 720]: a box
    at (36, 90) pt in the crop frame lands at (200, 500) px with the flag and
    at (400, 900) px without it.
G3  /Rotate IS APPLIED. gs renders a page with /Rotate 90 as the rotated,
    visible page (a 612x792 pt page comes out 4400x3400 px at 400 dpi), so
    the frame G2 names is the rotated one. A region must be measured in that
    same frame; which frame unit 1 measures in is stated in the contract,
    not here.
G4  ONE THRESHOLD, PASSED EXPLICITLY. `threshold` and `ink_is_dark` go to
    `stream_masks` as given; this module adds no default of its own beyond
    the signature, and the caller records what it passed.
G5  SAFER. gs runs with `-dSAFER`; the one file it must read is permitted
    by name (`--permit-file-read`), nothing more.
G6  FAILURE IS AN EXCEPTION, NEVER A SHORT ITERATOR. A non-zero gs exit,
    or fewer pages than asked for, raises `GhostError` naming the file and
    gs's first stderr line. A render that silently stopped early would pair
    every later page with the wrong object. The exit code is not enough
    for the count: gs 10.08 exits 0 on a file with no catalog and prints
    its error and then "0" on stdout, so an "Error" line or a count below
    1 is a failure too.
"""

from __future__ import annotations

import shutil
import subprocess

from .pnmio import stream_masks


class GhostError(RuntimeError):
    """gs is missing, refused the file, or returned fewer pages."""


def available() -> bool:
    """Is `gs` on PATH? The precondition, checked in one place."""
    return shutil.which("gs") is not None


def _first_line(err: bytes) -> str:
    # Skipping blank lines is an equivalent mutant on gs 10.08 (the sweep
    # forces it True with no failure): gs's first output line was never
    # blank in any case tried. Kept for the gs that leads with one.
    for ln in err.decode("utf-8", "replace").splitlines():
        if ln.strip():
            return ln.strip()
    return "(no message)"


def page_count(pdf) -> int:
    """The number of pages gs sees in `pdf` (G5, G6)."""
    path = str(pdf)
    # A PostScript string literal: escape the three characters that
    # would end or alter it.
    lit = path.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    proc = subprocess.run(
        ["gs", "-q", "-dNODISPLAY", "-dSAFER", "-dBATCH", "-dNOPAUSE",
         f"--permit-file-read={path}",
         "-c", f"({lit}) (r) file runpdfbegin pdfpagecount = quit"],
        capture_output=True, timeout=300)
    out = proc.stdout.decode("ascii", "replace").strip().splitlines()
    # gs 10.08 on a file with no catalog exits 0 and prints its error on
    # STDOUT followed by "0": the exit code alone does not say it failed.
    said_error = any("Error" in ln for ln in
                     out + proc.stderr.decode("utf-8", "replace")
                     .splitlines())
    if (proc.returncode != 0 or said_error or not out
            or not out[-1].strip().isdigit() or int(out[-1]) < 1):
        raise GhostError(f"gs could not count the pages of {path}: "
                         f"{_first_line(proc.stderr + proc.stdout)}")
    return int(out[-1])


def render_masks(pdf, *, dpi: int = 400, threshold: int = 200,
                 ink_is_dark: bool = True, first: int | None = None,
                 last: int | None = None):
    """One `InkMask` per page of `pdf`, pages `first`..`last` (1-based,
    inclusive; all when both are None), read from gs's stdout (G1-G6).

    A generator: one page is held at a time.

    `-sstdout=%stderr` keeps gs's own messages off STDOUT, the pipe the
    raster travels on. Measured on gs 10.08: without it, a file that is
    not a PDF puts 648 bytes of error text on stdout (with it, 0). That
    case also exits 1, so it is caught either way; no exit-0 case that
    writes to stdout was found (a broken xref and an unknown content
    operator are both silent under -q). The flag is kept for the case
    not found, and no test can tell it from its absence -- an
    equivalent mutant on this gs, recorded rather than hidden.
    stderr is drained on a thread so a talkative gs cannot fill that pipe
    and block while stdout is being read.
    """
    import threading
    from .pnmio import CorruptPNM, UnsupportedPNM
    path = str(pdf)
    cmd = ["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER",
           "-sstdout=%stderr",
           "-sDEVICE=pgmraw", f"-r{dpi}", "-dUseCropBox"]
    if first is not None:
        cmd.append(f"-dFirstPage={first}")
    if last is not None:
        cmd.append(f"-dLastPage={last}")
    cmd += ["-sOutputFile=%stdout", path]
    want = None
    if first is not None and last is not None:
        want = last - first + 1
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    errbuf = []
    drain = threading.Thread(target=lambda: errbuf.append(proc.stderr.read()),
                             daemon=True)
    drain.start()
    got = 0
    bad = None
    try:
        try:
            for m in stream_masks(proc.stdout, dpi=dpi,
                                  threshold=threshold,
                                  ink_is_dark=ink_is_dark):
                got += 1
                yield m
        except (CorruptPNM, UnsupportedPNM) as exc:
            bad = exc
    finally:
        proc.stdout.close()
        rc = proc.wait()
        drain.join()
        proc.stderr.close()
    err = errbuf[0] if errbuf else b""
    if rc != 0:
        raise GhostError(f"gs failed on {path} (exit {rc}): "
                         f"{_first_line(err)}")
    if bad is not None:
        raise GhostError(f"gs output for {path} is not a PGM stream: {bad}")
    if want is not None and got != want:
        raise GhostError(f"gs returned {got} pages of {path}, "
                         f"asked for {want}")

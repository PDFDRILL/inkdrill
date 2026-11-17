"""Local paths of the lines-audit harness (the audit sandbox's /home/claude paths, made local 2026-11-17).
Override with LINES_AUDIT_LIB, LINES_AUDIT_WORK, LINES_AUDIT_GEMMA, LINES_AUDIT_DRILLGROUP."""
import os
H = os.path.expanduser("~")
LIB = os.environ.get("LINES_AUDIT_LIB", os.path.join(H, "pdfdrill-library"))
WORK = os.environ.get("LINES_AUDIT_WORK", os.path.join(H, "inkdrill", "work", "run-files", "lines-audit"))
GEMMA = os.environ.get("LINES_AUDIT_GEMMA", os.path.join(H, "Gemma4"))
DRILLGROUP = os.environ.get("LINES_AUDIT_DRILLGROUP", os.path.join(H, "drillgroup"))

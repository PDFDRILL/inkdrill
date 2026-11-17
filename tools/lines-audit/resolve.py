#!/usr/bin/env python3
"""resolve.py <set.tsv>... : print 'doc<TAB>folder' and write the library paths needed (pathspec, NUL-separated)."""
import csv, hashlib, os, subprocess, sys
LIB = "/home/claude/pdfdrill-library"
names = subprocess.run(["git", "-C", LIB, "ls-tree", "-z", "--name-only", "HEAD"], capture_output=True).stdout.split(b"\0")
names = [n for n in names if n]
ids = {"lib:" + hashlib.sha256(n).hexdigest()[:12]: n for n in names}
byname = {n.decode("utf-8", "surrogateescape"): n for n in names}
paths, seen = [], {}
for tsv in sys.argv[1:]:
    for r in csv.reader(open(tsv), delimiter="\t"):
        if not r or r[0].startswith("#") or r[0] == "stratum":
            continue
        doc, page = r[1], int(r[2])
        f = ids.get(doc) if doc.startswith("lib:") else byname.get(doc)
        if f is None:
            print("UNRESOLVED", doc, file=sys.stderr); continue
        seen[doc] = f
        paths += [f + b"/" + f + b".lines.json", f + b"/" + f + b".pdf", f + b"/inspect/pages/page-%04d.png" % page,
                  f + b"/probe-pdffonts.txt"]
open("/home/claude/work/sets/paths.bin", "wb").write(b"\0".join(dict.fromkeys(paths)))
import json
json.dump({d: f.decode("utf-8", "surrogateescape") for d, f in seen.items()}, open("/home/claude/work/sets/folders.json", "w"))
print(len(seen), "docs", len(set(paths)), "paths")

"""ENGINEERING.md 2a (drillgroup, 2026-10-21): decisions go into tables,
not long chains. Six or more branches on one variable is a finding."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 6


#: calls that are not decisions on a variable (type tests)
UNCOUNTED_CALLS = {"isinstance", "issubclass"}
#: statements a run of sibling ifs passes over (audit fbf36b2: 3 + 3 ifs
#: split by one assignment); a compound statement ends the run
PASSED_OVER = (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Pass)


def _named(node) -> bool:
    return isinstance(node, (ast.Name, ast.Attribute))


def _imported(tree) -> set[str]:
    """Names an import binds in the module: `import os` (os), `import
    os.path` (os), `import re as rx` (rx), `from x import y as z` (z)."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out |= {a.asname or a.name for a in node.names}
    return out


def _root(node) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _subjects(test, modules: frozenset = frozenset()) -> set[str]:
    """The variables an `if` decides on (T-0057 rule, 2026-10-21):
    the left side of a comparison (`x == ..`, `x in ..`, `x < ..`,
    `x is ..`); the receiver of a method call (`s.startswith(..)` -> s);
    the last positional argument of a call (`re.match(p, s)` -> s);
    through `not`; a BoolOp names what all its parts name. `isinstance`
    and plain truthiness name nothing, and neither does a module: a name
    an import binds, or an attribute chain starting with one
    (`os.path.exists(a)` names only a)."""
    def named(node) -> bool:
        return _named(node) and _root(node) not in modules
    if isinstance(test, ast.Compare):
        if isinstance(test.left, ast.Constant) and test.comparators:
            return {ast.unparse(test.comparators[0])}     # "a" == op -> op
        return {ast.unparse(test.left)}
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _subjects(test.operand, modules)
    if isinstance(test, ast.BoolOp):
        subs = [_subjects(v, modules) for v in test.values]
        return set.intersection(*subs) if subs else set()
    if isinstance(test, ast.Call):
        if isinstance(test.func, ast.Name) and test.func.id in UNCOUNTED_CALLS:
            return set()
        out = set()
        if isinstance(test.func, ast.Attribute) and named(test.func.value):
            out.add(ast.unparse(test.func.value))
        if test.args and named(test.args[-1]):
            out.add(ast.unparse(test.args[-1]))
        return out
    return set()


def _tests(node, modules) -> list[set[str]]:
    """The subjects of every test along an if/elif chain, and of the ifs
    directly in its final `else:` block (audit fbf36b2: a chain continued
    there)."""
    out, cur = [], node
    while True:
        out.append(_subjects(cur.test, modules))
        if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
            cur = cur.orelse[0]
        else:
            for stmt in cur.orelse:
                if isinstance(stmt, ast.If):
                    out += _tests(stmt, modules)
            return out


def _elifs(node) -> list:
    out, cur = [], node
    while len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
        cur = cur.orelse[0]
        out.append(cur)
    return out


def _final_else(node) -> list:
    """The statements of an if/elif chain's final `else:` block."""
    return (_elifs(node) or [node])[-1].orelse


def _absorbed(node) -> list:
    """The ifs a chain counts besides its head: its elifs, and the ifs
    directly in its final else block with all they absorb in turn (so
    that each is reported once, with the chain; coordinator, fc34d57)."""
    out = _elifs(node)
    for stmt in _final_else(node):
        if isinstance(stmt, ast.If):
            out += [stmt, *_absorbed(stmt)]
    return out


def chains(source: str, name: str) -> list[str]:
    """Six or more branches on one variable: in one if/elif chain, or in a
    run of sibling `if` statements counted together with the chains among
    them (`if a: .. if b: .. if c: .. elif d: ..`). A chain inside a run
    that is reported is not reported again. Match statements by cases."""
    tree = ast.parse(source)
    modules = frozenset(_imported(tree))
    found, covered = [], set()
    for node in ast.walk(tree):            # runs of sibling if statements
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if not isinstance(body, list):
                continue
            if isinstance(node, ast.If) and field == "orelse":
                continue                   # a chain's else: the chain counts it
            run = []
            for stmt in body + [None]:
                if isinstance(stmt, ast.If):
                    run.append(stmt)
                    continue
                if isinstance(stmt, PASSED_OVER):
                    continue
                if len(run) >= 2:
                    tests = [(s, sub) for s in run for sub in _tests(s, modules)]
                    for subj in sorted(set().union(*(sub for _, sub in tests))):
                        n = sum(subj in sub for _, sub in tests)
                        if n >= LIMIT:
                            first = next(s for s, sub in tests if subj in sub)
                            found.append(f"{name}:{first.lineno} {n} sibling "
                                         f"ifs on {subj}")
                            covered |= {(id(s), subj) for s in run}
                run = []
    inner = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Match) and len(node.cases) >= LIMIT:
            found.append(f"{name}:{node.lineno} match with {len(node.cases)} cases")
        if not isinstance(node, ast.If) or id(node) in inner:
            continue
        inner |= {id(e) for e in _absorbed(node)}
        subjects = _tests(node, modules)
        for s in sorted(set().union(*subjects)):
            n = sum(s in sub for sub in subjects)
            if n >= LIMIT and (id(node), s) not in covered:
                found.append(f"{name}:{node.lineno} {n} branches on {s}")
    return found


#: inkdrill's source package; tests/ is outside it, so no test file is scanned
PACKAGE = ROOT / "inkdrill"
#: sites allowed to stay over the limit until their decision moves to a table
BASELINE = Path(__file__).resolve().parent / "branching_baseline.txt"
KINDS = {"branches": "elif", "sibling ifs": "sibling-ifs"}


def _qualname(tree, line: int) -> str:
    """The innermost def/class around `line`, dotted (`_Runner._command`)."""
    def walk(body, prefix):
        for node in body:
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef))
                    and node.lineno <= line <= node.end_lineno):
                return walk(node.body, prefix + [node.name])
        return prefix
    return ".".join(walk(tree.body, [])) or "<module>"


def sites(source: str, name: str) -> dict[str, list[int]]:
    """chains(), keyed by baseline site `file:qualifiedfunction | kind |
    variable`, with that site's counts largest first."""
    tree = ast.parse(source)
    out: dict[str, list[int]] = {}
    for f in chains(source, name):
        where, rest = f.rsplit(":", 1)
        line, rest = rest.split(" ", 1)
        line = int(line)
        if rest.startswith("match with "):
            n, kind = int(rest.split()[2]), "match"
            var = next(ast.unparse(m.subject) for m in ast.walk(tree)
                       if isinstance(m, ast.Match) and m.lineno == line)
        else:
            n, what = rest.split(" ", 1)
            what, var = what.split(" on ", 1)
            n, kind = int(n), KINDS[what]
        key = f"{where}:{_qualname(tree, line)} | {kind} | {var}"
        out.setdefault(key, []).append(n)
    return {k: sorted(v, reverse=True) for k, v in out.items()}


def read_baseline(text: str) -> dict[str, list[int]]:
    """One line per site: `file:qualifiedfunction | kind | variable |
    counts`, counts comma-separated, largest first."""
    out: dict[str, list[int]] = {}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        key, counts = raw.rsplit(" | ", 1)
        assert key not in out, f"baseline lists {key} twice"
        out[key] = [int(c) for c in counts.split(",")]
    return out


def problems(found: dict, baseline: dict) -> list[str]:
    """Every way the found sites differ from the baseline. A new site, a
    grown count and a stale entry (site gone or below the limit) fail; so
    does a shrunk count, until the baseline follows it down."""
    out = []
    for key in sorted(set(found) | set(baseline)):
        have, allowed = found.get(key), baseline.get(key)
        line = f"{key} | {','.join(map(str, have or allowed))}"
        if allowed is None:
            out.append(f"new site: {line}")
        elif have is None:
            out.append(f"stale baseline entry (gone or below {LIMIT}): {line}")
        elif have != allowed:
            grew = len(have) > len(allowed) or any(
                h > a for h, a in zip(have, allowed))
            out.append(f"{'grown' if grew else 'shrunk, update the baseline'}: "
                       f"{key} | {','.join(map(str, allowed))} -> "
                       f"{','.join(map(str, have))}")
    return out


def package_sites() -> dict[str, list[int]]:
    found = {}
    for p in sorted(PACKAGE.rglob("*.py")):
        found.update(sites(p.read_text(), str(p.relative_to(ROOT))))
    return found


def test_no_long_chain_in_the_package():
    text = BASELINE.read_text() if BASELINE.exists() else ""
    found = problems(package_sites(), read_baseline(text))
    assert found == [], "\n" + "\n".join(found)


def _sites(src, name="p.py"):
    return sites(src, name)


def test_the_baseline_fails_a_new_site():
    planted = "def f(op):\n" + "".join(
        f"    {'if' if k == 0 else 'elif'} op == {k}:\n        return {k}\n"
        for k in range(6))
    assert problems(_sites(planted), {}) == ["new site: p.py:f | elif | op | 6"]
    five = "\n".join(planted.splitlines()[:-2]) + "\n"
    assert problems(_sites(five), {}) == []


def test_the_baseline_fails_a_grown_count():
    seven = "class C:\n    def f(self, op):\n" + "".join(
        f"        {'if' if k == 0 else 'elif'} op == {k}:\n            return {k}\n"
        for k in range(7))
    allowed = read_baseline("p.py:C.f | elif | op | 6\n")
    assert problems(_sites(seven), allowed) == [
        "grown: p.py:C.f | elif | op | 6 -> 7"]
    assert problems(_sites(seven), read_baseline("p.py:C.f | elif | op | 7")) == []


def test_the_baseline_fails_a_stale_entry_and_a_shrunk_count():
    allowed = read_baseline("p.py:f | match | op | 6\n")
    five = "def f(op):\n    match op:\n" + "".join(
        f"        case {k}:\n            return {k}\n" for k in range(5))
    assert problems(_sites(five), allowed) == [
        "stale baseline entry (gone or below 6): p.py:f | match | op | 6"]
    seven = five + "".join(
        f"        case {k}:\n            return {k}\n" for k in range(5, 7))
    assert problems(_sites(seven), read_baseline("p.py:f | match | op | 8")) == [
        "shrunk, update the baseline: p.py:f | match | op | 8 -> 7"]


def test_the_baseline_keeps_one_number_per_chain():
    two = "def f(op):\n" + "".join(
        f"    {'if' if k == 0 else 'elif'} op == {k}:\n        return {k}\n"
        for k in range(6)) + "    for _ in ():\n        pass\n" + "".join(
        f"    {'if' if k == 0 else 'elif'} op == {k}:\n        return {k}\n"
        for k in range(8))
    assert _sites(two) == {"p.py:f | elif | op": [8, 6]}
    assert problems(_sites(two), read_baseline("p.py:f | elif | op | 8")) == [
        "grown: p.py:f | elif | op | 8 -> 8,6"]


def test_the_check_sees_a_planted_chain():
    planted = "def f(op):\n" + "".join(
        f"    {'if' if k == 0 else 'elif'} op == {k}:\n        return {k}\n"
        for k in range(6))
    assert chains(planted, "planted.py") == ["planted.py:2 6 branches on op"]
    five = "\n".join(planted.splitlines()[:-2]) + "\n"
    assert chains(five, "five.py") == []


def test_the_check_sees_a_planted_match():
    src = "def f(op):\n    match op:\n" + "".join(
        f"        case {k}:\n            return {k}\n" for k in range(6))
    assert chains(src, "m.py") == ["m.py:2 match with 6 cases"]


def test_the_check_sees_flat_sibling_ifs():
    src = "def f(a):\n" + "".join(
        f"    if a.x == {k}:\n        return {k}\n" for k in range(6))
    assert chains(src, "s.py") == ["s.py:2 6 sibling ifs on a.x"]


def _chain(tests: list[str]) -> str:
    return "def f(s, x):\n" + "".join(
        f"    {'if' if k == 0 else 'elif'} {t}:\n        return {k}\n"
        for k, t in enumerate(tests))


def test_the_check_sees_method_calls_on_one_receiver():
    src = _chain([f"s.startswith('{c}')" for c in "abcde"] + ["s.isdigit()"])
    assert chains(src, "a.py") == ["a.py:2 6 branches on s"]


def test_the_check_sees_one_variable_as_the_last_argument():
    src = _chain(["s.startswith('a')", "s.startswith('b')",
                  "re.fullmatch('x+', s)", "re.match('y', s)",
                  "not re.search('z', s)", "s.endswith('q')"])
    assert chains(src, "b.py") == ["b.py:2 6 branches on s"]


def test_isinstance_and_truthiness_stay_uncounted():
    src = _chain([f"isinstance(x, T{k})" for k in range(6)])
    assert chains(src, "c.py") == []
    src = _chain(["x"] * 6)
    assert chains(src, "d.py") == []


def test_a_module_is_not_a_subject():
    head = "import os\nimport re as rx\nfrom os import path as p\n"
    different = head + _chain([f"os.path.exists(a{k})" for k in range(6)])
    assert chains(different, "e.py") == []
    aliased = head + _chain([f"p.isdir(b{k})" for k in range(6)])
    assert chains(aliased, "g.py") == []
    one_path = "import os\n" + "def f(q):\n" + "".join(
        f"    {'if' if k == 0 else 'elif'} os.path.{fn}(q):\n        return {k}\n"
        for k, fn in enumerate(("exists", "isdir", "isfile", "islink",
                                "ismount", "isabs")))
    assert chains(one_path, "h.py") == ["h.py:3 6 branches on q"]
    same = head + _chain(["os.path.exists(s)", "os.path.isdir(s)",
                          "p.isfile(s)", "rx.match('x', s)",
                          "not rx.search('y', s)", "s.endswith('z')"])
    assert chains(same, "f.py") == ["f.py:5 6 branches on s"]


def test_a_constant_on_the_left_names_the_variable():
    src = _chain([f"'{c}' == op" for c in "abcdef"])
    assert chains(src, "k.py") == ["k.py:2 6 branches on op"]


def test_sibling_ifs_and_a_following_chain_count_together():
    src = ("def f(name, args):\n"
           + "".join(f"    if name in T{k}:\n        return {k}\n"
                     for k in range(4))
           + "    if name == 'sqrt':\n        return 4\n"
           + "    elif name in DROP:\n        return 5\n"
           + "    elif name in SYM:\n        return 6\n")
    assert chains(src, "l.py") == ["l.py:2 7 sibling ifs on name"]
    five = ("def f(name):\n"
            + "".join(f"    if name in T{k}:\n        return {k}\n"
                      for k in range(3))
            + "    if name == 'a':\n        return 3\n"
            + "    elif x:\n        return 4\n")
    assert chains(five, "m.py") == []


def test_sibling_ifs_split_by_an_assignment_count_together():
    """Audit of the fixes (fbf36b2): 3 + 3 ifs with one assignment
    between them evaded the run."""
    src = ("def f(op):\n"
           + "".join(f"    if op == {k}:\n        return {k}\n" for k in range(3))
           + "    y = 1\n"
           + "".join(f"    if op == {k}:\n        return {k}\n" for k in range(3, 6)))
    assert chains(src, "n.py") == ["n.py:2 6 sibling ifs on op"]
    loop = src.replace("    y = 1\n", "    for _ in ():\n        pass\n")
    assert chains(loop, "o.py") == []


def test_a_chain_continued_in_an_else_block_counts_whole():
    src = ("def f(op):\n"
           + "".join(f"    {'if' if k == 0 else 'elif'} op == {k}:\n        return {k}\n"
                     for k in range(3))
           + "    else:\n        y = 1\n"
           + "".join(f"        {'if' if k == 3 else 'elif'} op == {k}:\n            return {k}\n"
                     for k in range(3, 6)))
    assert chains(src, "p.py") == ["p.py:2 6 branches on op"]


def test_ifs_in_an_else_block_are_reported_once():
    """Coordinator (llmwiki on fc34d57): the ifs of a chain's final else
    block were counted by the chain and again as a sibling run."""
    three = ("def f(op):\n"
             + "".join(f"    {'if' if k == 0 else 'elif'} op == {k}:\n        return {k}\n"
                       for k in range(3))
             + "    else:\n"
             + "".join(f"        if op == {k}:\n            return {k}\n"
                       for k in range(3, 6)))
    assert chains(three, "q.py") == ["q.py:2 6 branches on op"]
    six = ("def f(op):\n    if op == 0:\n        return 0\n    else:\n"
           + "".join(f"        if op == {k}:\n            return {k}\n"
                     for k in range(1, 7)))
    assert chains(six, "r.py") == ["r.py:2 7 branches on op"]
    nested = ("def f(op):\n    if op == 0:\n        return 0\n    else:\n"
              + "".join(f"        {'if' if k == 1 else 'elif'} op == {k}:\n"
                        f"            return {k}\n" for k in range(1, 7)))
    assert chains(nested, "s.py") == ["s.py:2 7 branches on op"]


class Branching(unittest.TestCase):
    """The checks above as unittest cases: inkdrill runs the standard
    library's runner, which does not collect bare test functions."""


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        setattr(Branching, _name, (lambda fn: lambda self: fn())(_fn))
del _name, _fn

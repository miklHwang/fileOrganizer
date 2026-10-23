from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "tidyup"

ALLOW = {
    ("fsops", "_remove_verified_source"): {"os.unlink"},
    ("fsops", "_discard_partial"): {"os.unlink"},
    ("fsops", "remove_empty_dir"): {"os.rmdir"},
    ("fsops", "move_tree"): {"os.rmdir"},
    ("winutil", "send_to_recycle_bin"): {"send2trash"},
}
FORBIDDEN = {"os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.replace", "shutil.rmtree", "shutil.move"}
ALWAYS = {"os.replace", "shutil.move", "shutil.rmtree"}


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def scan_source(source: str, mod: str):
    """Yield (module, function, name, lineno) for every risky reference in source.

    Resolves `import os as o` and `from os import remove [as r]` to canonical dotted names.
    Note: Path.replace cannot be statically distinguished from str.replace, so it is not scanned
    (os.replace is, and `.rename(` is forbidden outside fsops since str has no rename).
    """
    tree = ast.parse(source)
    modules: dict[str, str] = {}  # local alias -> module name
    names: dict[str, str] = {}  # local alias -> canonical dotted name
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                modules[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            for a in n.names:
                names[a.asname or a.name] = f"{n.module}.{a.name}"

    def canon(d: str) -> str:
        head, _, rest = d.partition(".")
        if head in modules:
            head = modules[head]
        elif head in names and not rest:
            return names[head]
        elif head in names:
            head = names[head]
        return head + ("." + rest if rest else "")

    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            fn = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.Attribute):
                d = _dotted(child)
                if d:
                    c = canon(d)
                    yield mod, fn, c, child.lineno
                    if c.split(".")[0] == "send2trash":
                        yield mod, fn, "send2trash", child.lineno
                if child.attr in ("unlink", "rmdir", "rename"):
                    yield mod, fn, "." + child.attr, child.lineno
            elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                if child.id in names:
                    c = names[child.id]
                    yield mod, fn, c, child.lineno
                    if c.split(".")[0] == "send2trash":
                        yield mod, fn, "send2trash", child.lineno
                elif child.id == "send2trash":
                    yield mod, fn, "send2trash", child.lineno
            elif isinstance(child, ast.Import):
                for a in child.names:
                    if a.name.split(".")[0] == "send2trash":
                        yield mod, fn, "send2trash", child.lineno
            elif isinstance(child, ast.ImportFrom) and (child.module or "").split(".")[0] == "send2trash":
                yield mod, fn, "send2trash", child.lineno
            yield from visit(child, fn)

    yield from visit(tree, None)


def _scan():
    for f in sorted(PKG.glob("*.py")):
        yield from scan_source(f.read_text("utf-8"), f.stem)


def _violations(source: str, mod: str = "other"):
    bad = []
    for m, fn, name, line in scan_source(source, mod):
        if name in FORBIDDEN or name in (".unlink", ".rmdir") or name == "send2trash":
            allowed = ALLOW.get((m, fn), set())
            ok = name in allowed or (name == ".unlink" and "os.unlink" in allowed) \
                or (name == ".rmdir" and "os.rmdir" in allowed)
            if not ok:
                bad.append(name)
        if m != "fsops" and name in (".rename", "os.rename"):
            bad.append(name)
    return bad


def test_forbidden_calls_only_in_allowlist():
    bad = []
    for mod, fn, name, line in _scan():
        if name in FORBIDDEN or name in (".unlink", ".rmdir") or name == "send2trash":
            allowed = ALLOW.get((mod, fn), set())
            ok = name in allowed or (name == ".unlink" and "os.unlink" in allowed) \
                or (name == ".rmdir" and "os.rmdir" in allowed)
            if not ok:
                bad.append(f"{mod}.py:{line} {name} in {fn}")
    assert not bad, bad


def test_never_replace_move_rmtree():
    bad = [f"{m}.py:{l} {n}" for m, _f, n, l in _scan() if n in ALWAYS]
    assert not bad, bad


def test_copy_and_rename_only_in_fsops():
    bad = []
    for mod, fn, name, line in _scan():
        if mod == "fsops":
            continue
        if name in ("os.rename", ".rename") or name.startswith("shutil.copy"):
            bad.append(f"{mod}.py:{line} {name}")
    assert not bad, bad


SNIPPETS = [
    "import os\ndef f(p):\n    os.remove(p)\n",
    "import os\ndef f(p):\n    p.unlink()\n",
    "from os import remove\ndef f(p):\n    remove(p)\n",
    "from os import remove as r\ndef f(p):\n    r(p)\n",
    "from shutil import rmtree\ndef f(p):\n    rmtree(p)\n",
    "import os as o\ndef f(p):\n    o.remove(p)\n",
    "import shutil as sh\ndef f(p):\n    sh.rmtree(p)\n",
    "import shutil\ndef f(a, b):\n    shutil.move(a, b)\n",
    "from send2trash import send2trash\ndef f(p):\n    send2trash(p)\n",
    "import send2trash\ndef f(p):\n    send2trash.send2trash(p)\n",
    "def f(p, q):\n    p.rename(q)\n",
    "import os\ndef f(p, q):\n    os.rename(p, q)\n",
    "from os import rename\ndef f(p, q):\n    rename(p, q)\n",
]


@pytest.mark.parametrize("src", SNIPPETS)
def test_scanner_flags_forbidden_forms(src):
    assert _violations(src, "other")


@pytest.mark.parametrize("src,mod,fn", [
    ("import os as o\ndef _remove_verified_source(p):\n    o.unlink(p)\n", "fsops", "_remove_verified_source"),
    ("from os import unlink\ndef _discard_partial(p):\n    unlink(p)\n", "fsops", "_discard_partial"),
    ("import os\ndef remove_empty_dir(p):\n    os.rmdir(p)\n", "fsops", "remove_empty_dir"),
    ("def send_to_recycle_bin(p):\n    from send2trash import send2trash\n    send2trash(p)\n", "winutil",
     "send_to_recycle_bin"),
])
def test_scanner_allows_allowlisted(src, mod, fn):
    assert (mod, fn) in ALLOW
    assert not _violations(src, mod)


def test_scanner_allowlist_is_function_specific():
    assert _violations("import os\ndef other(p):\n    os.unlink(p)\n", "fsops")

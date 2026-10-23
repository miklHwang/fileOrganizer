from __future__ import annotations

import ast
from pathlib import Path

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


def _scan():
    """Yield (module, function, kind, name, lineno) for every risky reference."""
    for f in sorted(PKG.glob("*.py")):
        tree = ast.parse(f.read_text("utf-8"))
        mod = f.stem

        def visit(node, func):
            for child in ast.iter_child_nodes(node):
                fn = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
                if isinstance(child, ast.Attribute):
                    d = _dotted(child)
                    if d:
                        yield mod, fn, d, child.lineno
                        if child.attr in ("unlink", "rmdir"):
                            yield mod, fn, "." + child.attr, child.lineno
                elif isinstance(child, ast.Name) and child.id == "send2trash":
                    yield mod, fn, "send2trash", child.lineno
                elif isinstance(child, ast.Import):
                    for a in child.names:
                        if a.name.split(".")[0] == "send2trash":
                            yield mod, fn, "send2trash", child.lineno
                elif isinstance(child, ast.ImportFrom) and (child.module or "").split(".")[0] == "send2trash":
                    yield mod, fn, "send2trash", child.lineno
                yield from visit(child, fn)

        yield from visit(tree, None)


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
        if name == "os.rename" or name.startswith("shutil.copy"):
            bad.append(f"{mod}.py:{line} {name}")
    assert not bad, bad


def test_checker_detects_violation(tmp_path):
    src = ast.parse("import os\ndef f(p):\n    os.remove(p)\n    p.unlink()\n")
    names = {_dotted(n) for n in ast.walk(src) if isinstance(n, ast.Attribute)}
    assert "os.remove" in names

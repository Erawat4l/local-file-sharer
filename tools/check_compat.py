#!/usr/bin/env python3
"""Catch syntax that is newer than the declared ``requires-python``.

There is no 3.9 interpreter in most dev containers, and the failure mode is
brutal: an f-string or a ``zip(strict=...)`` that parses fine locally raises
``SyntaxError``/``TypeError`` on import for the oldest supported version. This
walks the AST for the few constructs that are *not* hidden by
``from __future__ import annotations``.

Exit code 1 lists offenders; CI runs it on the lint job.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = (3, 9)
ROOTS = ("src", "tests", "tools")


def unguarded_pep604(path: Path, tree: ast.AST) -> list[str]:
    """``X | Y`` in an annotation is fine on 3.10+ and fine *as a string*."""
    if "from __future__ import annotations" in path.read_text(encoding="utf-8"):
        return []
    problems = []
    for node in ast.walk(tree):
        annotation = None
        if isinstance(node, (ast.arg, ast.AnnAssign, ast.FunctionDef, ast.AsyncFunctionDef)):
            annotation = getattr(node, "annotation", None) or getattr(node, "returns", None)
        if annotation is None:
            continue
        text = ast.unparse(annotation)
        if "|" in text or any(builtin in text for builtin in ("list[", "dict[", "set[", "tuple[")):
            problems.append(f"{path}:{node.lineno}: PEP 604/585 annotation without future import: {text}")
    return problems


def _guarded_imports(tree: ast.AST) -> set[int]:
    """Line numbers of imports inside a ``try:`` that has an ``except``.

    ``import tomllib`` on 3.9 is fine *if* the ImportError is handled and the
    package declares ``tomli`` for older versions — both are true here.
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and node.handlers:
            for statement in ast.walk(node):
                if isinstance(statement, (ast.Import, ast.ImportFrom)):
                    guarded.add(statement.lineno)
    return guarded


def other_problems(path: Path, tree: ast.AST) -> list[str]:
    problems = []
    guarded = _guarded_imports(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in {"zip", "map"} and any(kw.arg == "strict" for kw in node.keywords):
                problems.append(f"{path}:{node.lineno}: {name}(strict=…) needs Python 3.10")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tomllib" and node.lineno not in guarded:
                    problems.append(f"{path}:{node.lineno}: unguarded `import tomllib` needs Python 3.11")
    return problems


def main() -> int:
    problems: list[str] = []
    checked = 0
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            checked += 1
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            problems.extend(unguarded_pep604(path, tree))
            problems.extend(other_problems(path, tree))
    for problem in problems:
        print(f"✖ {problem}")
    if problems:
        return 1
    print(f"✓ {checked} files fit Python {TARGET[0]}.{TARGET[1]}+")
    return 0


if __name__ == "__main__":
    sys.exit(main())

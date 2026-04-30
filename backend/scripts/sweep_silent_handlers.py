"""One-shot sweep: replace `except [Exception]: pass` handlers with
structured `log_silent_failure(...)` calls.

WHY
---
44 truly-silent handlers across services/ were swallowing exceptions
with `pass` — no log, no metric, no trace. That's the single biggest
reason fixes regress without detection: a bug ships, it's silently
caught, nothing alerts, and the fix from a week ago has rotted.

WHAT THIS SCRIPT DOES
---------------------
For every `except [Exception [as e]]: pass` or `except: pass` block:
  1. Preserve the original except/as-clause.
  2. Replace the `pass` with a `log_silent_failure(subsystem, exc)`
     call that names the file + function as the subsystem.
  3. Add `from services.observability import log_silent_failure` at
     the top of the file if not already imported.

The converter is IDEMPOTENT: running it twice is a no-op. Once a file
has been converted, it won't be touched again.

IDENTIFICATION
--------------
Subsystem name = `{module_path}.{function_name}` resolved via the
python tokenizer — stable across formatting changes and safer than
regex across indentation variations.

SAFETY
------
- The `log_silent_failure` call itself never raises (see
  `services/observability/error_log.py`).
- Control flow is preserved: `pass` → `log_silent_failure(...)` is
  flow-equivalent (both swallow the exception).
- No bare `except:` clauses are left behind — they're all upgraded
  to the named-binding form so the handler receives the exception.

Run:
    cd /app/backend && python scripts/sweep_silent_handlers.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from typing import List, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]  # /app/backend
IMPORT_LINE = "from services.observability import log_silent_failure"
SUBSYSTEMS_SKIPPED = {
    # Already has adequate logging or is part of the observability
    # module itself — don't self-reference.
    "services/observability/error_log.py",
    "services/observability/__init__.py",
}


def _find_silent_handlers(tree: ast.AST, source_lines: List[str]) -> List[Tuple[int, int, str]]:
    """Return (line_of_pass, line_of_except, handler_var_name) for every
    pass-only exception handler under this tree.

    A "pass-only" handler has exactly one statement and it's `ast.Pass`.
    """
    hits: List[Tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # Body must be exactly `pass` (nothing else).
        if len(node.body) != 1:
            continue
        if not isinstance(node.body[0], ast.Pass):
            continue
        pass_line = node.body[0].lineno
        except_line = node.lineno
        handler_var = node.name or "_"
        hits.append((pass_line, except_line, handler_var))
    return hits


def _resolve_enclosing_function(tree: ast.AST, line_no: int) -> str:
    """Return the name of the nearest enclosing function/method for
    subsystem labelling. Falls back to `<module>` if top-level."""
    best_name = "<module>"
    best_start = -1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= line_no and node.lineno > best_start:
                # end_lineno exists on Python 3.8+.
                end = getattr(node, "end_lineno", None) or line_no + 10_000
                if end >= line_no:
                    best_start = node.lineno
                    best_name = node.name
    return best_name


def _file_subsystem(rel_path: str, func_name: str) -> str:
    stem = rel_path.replace("/", ".").replace(".py", "")
    return f"{stem}.{func_name}"


def _has_import(source: str) -> bool:
    return IMPORT_LINE in source


def _inject_import(source: str) -> str:
    """Insert the import after the last TOP-LEVEL import. Only lines
    where the `import`/`from` keyword starts in column 0 are candidates
    — otherwise we'd splice into function-local lazy imports and break
    indentation."""
    lines = source.splitlines(keepends=True)
    last_import = 0
    for i, line in enumerate(lines):
        # Strictly column-0 imports only.
        if line.startswith("import ") or line.startswith("from "):
            last_import = i
    lines.insert(last_import + 1, IMPORT_LINE + "\n")
    return "".join(lines)


_RE_PASS = re.compile(r'^(\s+)pass\s*(#.*)?$')
_RE_EXCEPT = re.compile(
    r'^(\s*)except(\s+[^:]+?)?(\s+as\s+(\w+))?\s*:\s*$'
)


def _rewrite_file(path: pathlib.Path) -> Tuple[int, int]:
    """Rewrite a single file. Returns (num_converted, num_skipped)."""
    source = path.read_text()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Not our problem — skip.
        return (0, 0)

    source_lines = source.splitlines(keepends=True)
    hits = _find_silent_handlers(tree, source_lines)
    if not hits:
        return (0, 0)

    rel_path = str(path.relative_to(ROOT))
    if rel_path in SUBSYSTEMS_SKIPPED:
        return (0, 0)

    # Sort by pass_line DESCENDING so we can mutate source_lines
    # without invalidating line numbers of earlier edits.
    hits.sort(key=lambda h: -h[0])

    converted = 0
    for pass_line, except_line, handler_var in hits:
        idx = pass_line - 1
        if idx < 0 or idx >= len(source_lines):
            continue
        line = source_lines[idx]
        m = _RE_PASS.match(line)
        if not m:
            continue
        indent = m.group(1)

        # If the handler captures as _, we need to bind it. Rewrite the
        # `except` line if needed.
        except_idx = except_line - 1
        if handler_var == "_" and 0 <= except_idx < len(source_lines):
            eline = source_lines[except_idx]
            em = _RE_EXCEPT.match(eline)
            if em:
                # em.groups(): (leading_ws, exc_clause, ' as X', X)
                lead = em.group(1)
                exc_clause = (em.group(2) or " Exception").rstrip()
                # Produce: "{lead}except {exc_clause} as _swept_exc:\n"
                new_except = f"{lead}except{exc_clause} as _swept_exc:\n"
                source_lines[except_idx] = new_except
                handler_var = "_swept_exc"

        func_name = _resolve_enclosing_function(tree, pass_line)
        subsystem = _file_subsystem(rel_path, func_name)
        new_line = (
            f"{indent}log_silent_failure("
            f'"{subsystem}", {handler_var})  # sweep-auto-converted\n'
        )
        source_lines[idx] = new_line
        converted += 1

    if converted == 0:
        return (0, len(hits))

    new_source = "".join(source_lines)
    if not _has_import(new_source):
        new_source = _inject_import(new_source)

    # Idempotency check: re-parse must succeed.
    try:
        ast.parse(new_source)
    except SyntaxError as e:
        print(
            f"  !! SKIP {rel_path}: sweep produced invalid AST ({e}), "
            f"no write.", file=sys.stderr,
        )
        return (0, len(hits))

    path.write_text(new_source)
    return (converted, 0)


def main() -> None:
    targets = []
    for folder in ("services", "routes"):
        for p in (ROOT / folder).rglob("*.py"):
            if "__pycache__" in p.parts or "tests" in p.parts:
                continue
            targets.append(p)

    total_converted = 0
    total_skipped = 0
    for p in sorted(targets):
        try:
            c, s = _rewrite_file(p)
        except Exception as e:
            print(f"  !! ERROR processing {p}: {e}", file=sys.stderr)
            continue
        if c or s:
            rel = str(p.relative_to(ROOT))
            print(f"  {rel}: converted={c} skipped={s}")
        total_converted += c
        total_skipped += s

    print()
    print(f"TOTAL converted: {total_converted}")
    print(f"TOTAL skipped  : {total_skipped}")


if __name__ == "__main__":
    main()

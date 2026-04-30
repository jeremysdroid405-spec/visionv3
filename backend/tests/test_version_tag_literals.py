"""Lint test — fail the build if anyone hardcodes a `final-<sport>-...`
version_tag literal in production code.

This is the anti-regression guard for `config/version_tags.py`.
Import from there. Do not sprinkle string literals across services/
and routes/. See `config/version_tags.py` for the rationale.

The test scans all .py files under services/ and routes/ (except tests
and this file itself) for raw literals of the form:

    "final-mlb"       "final-mlb-rt"       "final-mlb-rt-shadow"
    'final-nba'       'final-nba-rt'       'final-nba-rt-shadow'
    etc.

and fails if any occurrence is NOT inside a comment, docstring, or
explicit allowlist.

TO ADD A NEW TAG — update `config/version_tags.py`, not this file.
TO ADD A LEGITIMATE EXCEPTION — add the filename to `_ALLOWLISTED_FILES`
and justify in the comment above the entry.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]  # /app/backend

# ---- Legitimate exceptions -------------------------------------------
# Files that are ALLOWED to contain raw version_tag literals because
# they ARE the single source of truth.
_ALLOWLISTED_FILES = {
    # The source-of-truth module itself.
    "config/version_tags.py",
}

# Scan scope: production code only (tests, scripts, migrations excluded).
_SCAN_DIRS = ("services", "routes")

# Match any of: final-mlb, final-nba, final-mlb-rt, final-nba-rt-shadow, etc.
# The sport list is bounded so we don't match unrelated strings.
_LITERAL_RE = re.compile(
    r'["\'](final-(?:mlb|nba|nfl|nhl|wnba)(?:-[a-z0-9_-]*)?)["\']'
)


def _find_literal_uses(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return list of (line_no, matched_literal) for every string literal
    matching the pattern that appears as a real code string (not inside
    a comment).

    Uses `ast.parse` → walk → pull `ast.Constant` nodes of type str so
    we only ever flag actual code strings, never comments or docstrings
    that happen to contain the pattern.
    """
    try:
        source = path.read_text()
    except Exception:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Pre-existing syntax issue — not our concern. Skip.
        return []

    # Gather docstring node ids to skip (module / class / function
    # docstrings can legitimately mention the tag names in prose).
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                docstring_ids.add(id(node.body[0].value))

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids:
                continue
            m = _LITERAL_RE.search(f'"{node.value}"')
            if m:
                hits.append((node.lineno, m.group(1)))
    return hits


def _relative(path: pathlib.Path) -> str:
    return str(path.relative_to(ROOT))


def test_no_hardcoded_version_tag_literals():
    """Fail if any production file has a raw `final-<sport>[-...]`
    string literal outside the allowlist."""
    offenders: list[str] = []
    for folder in _SCAN_DIRS:
        for p in (ROOT / folder).rglob("*.py"):
            rel = _relative(p)
            if rel in _ALLOWLISTED_FILES:
                continue
            if "__pycache__" in rel or "/tests/" in rel:
                continue
            for line_no, literal in _find_literal_uses(p):
                offenders.append(f"{rel}:{line_no}: {literal!r}")

    if offenders:
        msg = (
            "Found hardcoded version_tag literals in production code.\n"
            "Import from `config.version_tags` instead.\n"
            "See `config/version_tags.py` for the rationale.\n\n"
            "Offenders:\n  " + "\n  ".join(offenders)
        )
        pytest.fail(msg)


def test_config_version_tags_self_consistent():
    """`config.version_tags` must export the keys documented in its
    module docstring. Also verifies `for_sport` / `shadow_for` /
    `sport_of` round-trip correctly."""
    from config.version_tags import (
        ALL_KNOWN_TAGS,
        LIVE_TAG_BY_SPORT,
        MLB_LIVE,
        MLB_SHADOW,
        NBA_LIVE,
        NBA_SHADOW,
        SHADOW_TAG_BY_SPORT,
        SUPPORTED_SPORTS,
        for_sport,
        is_live_tag,
        is_shadow_tag,
        shadow_for,
        sport_of,
    )

    # Basic constants.
    assert MLB_LIVE == "final-mlb-rt"
    assert NBA_LIVE == "final-nba-rt"
    assert MLB_SHADOW == "final-mlb-rt-shadow"
    assert NBA_SHADOW == "final-nba-rt-shadow"

    # Helpers.
    assert for_sport("mlb") == MLB_LIVE
    assert for_sport("MLB") == MLB_LIVE  # case-insensitive
    assert for_sport("nba", shadow=True) == NBA_SHADOW
    assert for_sport("mlb", baseline=True) == "final-mlb"
    assert shadow_for(MLB_LIVE) == MLB_SHADOW
    assert shadow_for(NBA_LIVE) == NBA_SHADOW
    assert is_live_tag(MLB_LIVE)
    assert is_shadow_tag(NBA_SHADOW)
    assert not is_live_tag("something-else")
    assert sport_of(MLB_LIVE) == "mlb"
    assert sport_of(NBA_SHADOW) == "nba"

    # Every sport has live + shadow + baseline entries.
    for s in SUPPORTED_SPORTS:
        assert s in LIVE_TAG_BY_SPORT
        assert s in SHADOW_TAG_BY_SPORT
        assert for_sport(s) in ALL_KNOWN_TAGS

    # Mutual exclusion.
    with pytest.raises(ValueError):
        for_sport("mlb", shadow=True, baseline=True)
    with pytest.raises(ValueError):
        for_sport("curling")  # unknown sport

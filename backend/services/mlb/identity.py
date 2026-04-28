"""
MLB player identity-resolution helpers.

This module is intentionally tiny. Three jobs:

1. `normalize_player_name(name)` — lossy canonical form used as the
   join key across `mlb_master_hub_2026`, `mlb_live_props`,
   `mlb_pick_history`, and `mlb_statcast_player_features`.
2. `apply_alias(name)`           — manual override layer for edge cases
   the deterministic normalizer can't catch (e.g. Suzuki "Ichiro").
3. `string_similarity(a, b)`     — wraps difflib for fuzzy fallback.

Convention used everywhere downstream:
  * Output is lowercase, ASCII, no punctuation, no jr/sr/ii/iii/iv suffix.
  * Whitespace collapsed to single spaces, trimmed.
  * Embedded periods are removed (so "J.D. Martinez" → "jd martinez").

The MLB engine MUST always call `normalize_player_name(...)` before
joining to / from `mlb_player_identity_map`. This file is the single
source of truth for that contract; do not roll your own normalizer.
"""
from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Optional

from unidecode import unidecode

ALIASES_PATH = "/app/backend/config/mlb_player_aliases.json"

# Suffixes stripped after the rest of normalization. Order matters —
# longer first — so "iii" is consumed before "ii".
_SUFFIXES = ("iv", "iii", "ii", "jr", "sr")

# Apostrophes & periods should COLLAPSE adjacent letters (so "J.D." →
# "jd", "O'Neill" → "oneill") rather than insert a space. Stripped
# first, before the broader punctuation-to-space pass.
_COLLAPSE_RE = re.compile(r"['`.\u2018\u2019]+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE    = re.compile(r"\s+")


@lru_cache(maxsize=1)
def _load_aliases() -> dict:
    """Load the manual alias map. Cached for the process lifetime —
    aliases are static config, no need to re-read every call."""
    if not os.path.exists(ALIASES_PATH): return {}
    try:
        with open(ALIASES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return {str(k).strip().lower(): str(v).strip().lower()
                for k, v in data.items() if k and v}
    except (json.JSONDecodeError, OSError):
        return {}


def normalize_player_name(name: Optional[str]) -> Optional[str]:
    """Canonical join key for an MLB player name.

    Steps (in order):
      1. unidecode (Acuña → Acuna, García → Garcia)
      2. lowercase
      3. drop ALL punctuation (so periods inside initials disappear:
         "J.D." → "jd"; apostrophes vanish: "O'Neill" → "oneill")
      4. collapse whitespace
      5. strip suffix tokens (jr / sr / ii / iii / iv)

    Returns None when the input is None/empty/whitespace-only.
    """
    if name is None: return None
    if not isinstance(name, str): name = str(name)
    s = unidecode(name).lower()
    # Pass 1: collapse apostrophes/periods (no space inserted).
    s = _COLLAPSE_RE.sub("", s)
    # Pass 2: replace remaining punctuation with space.
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    if not s: return None
    parts = s.split(" ")
    # Pass 3: collapse adjacent single-letter tokens. Chadwick stores
    # "J.P." as first-name "j p" (literal space), while our normalizer
    # already turned the live-side periods into nothing → "jp". Fold
    # both forms together by glomming runs of single-letter tokens.
    folded: list[str] = []
    i = 0
    while i < len(parts):
        if len(parts[i]) == 1:
            j = i
            while j < len(parts) and len(parts[j]) == 1: j += 1
            folded.append("".join(parts[i:j]))
            i = j
        else:
            folded.append(parts[i]); i += 1
    parts = folded
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts) if parts else None


def apply_alias(normalized: Optional[str]) -> Optional[str]:
    """If `normalized` is in the manual alias file, return its mapping;
    else return the input unchanged. Always called AFTER
    `normalize_player_name(...)`."""
    if not normalized: return normalized
    return _load_aliases().get(normalized, normalized)


def string_similarity(a: Optional[str], b: Optional[str]) -> float:
    """0.0–1.0 difflib ratio. Used only for low-confidence audit/fuzzy
    matching — never for production join decisions."""
    if not a or not b: return 0.0
    return SequenceMatcher(None, a, b).ratio()


__all__ = [
    "normalize_player_name", "apply_alias",
    "string_similarity", "ALIASES_PATH",
]

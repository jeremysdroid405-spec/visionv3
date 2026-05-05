"""
L5/L10/L20 mathematical-grain & SSOT contract
=============================================

Four contracts:

  1. L5 display value must be one of {0, 20, 40, 60, 80, 100}.
  2. L10 display value must be a multiple of 10.
  3. L20 display value must be a multiple of 5.
  4. Displayed L5 must equal API `hit_rate_l5` (no fallback to L10/L20
     or any cached_board alias).

The first three are mathematical invariants of windowed hit-rates with
strict `>` and `<` comparisons (push-aware): for an N-game window with
H hits and P pushes, the rate is `100 * H / N`. Step size is `100/N`,
i.e. 20% for L5, 10% for L10, 5% for L20. ANY value off-grid means a
larger-window value leaked into the smaller-window cell.

Contract 4 is enforced via static parse of every L5 reader on the
frontend. Every reader that surfaces an L5 cell to the user must
consult `hit_rate_l5` BEFORE any legacy alias (`h5_rate`,
`l5_hit_rate`, `hit_rates.l5_rate`).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


VALID_L5 = {0, 20, 40, 60, 80, 100}
FRONTEND_ROOT = Path("/app/frontend/src")


# ── Contract 1, 2, 3 — grid invariants ───────────────────────────────
@pytest.mark.parametrize("rate,window", [
    ( 0, 5), (20, 5), (40, 5), (60, 5), (80, 5), (100, 5),
    (10, 10), (90, 10), (100, 10),
    (55, 20), (95, 20),
])
def test_valid_grid_values_pass(rate, window):
    """Sanity: values that ARE on-grid for their window must pass."""
    assert _is_on_grid(rate, window), \
        f"on-grid value {rate}% (L{window}) should validate"


@pytest.mark.parametrize("rate,window", [
    (90, 5), (10, 5), (50, 5), (99, 5),
    (5, 10), (15, 10), (75.5, 10),
    (50.3, 20), (99, 20),
])
def test_off_grid_values_fail(rate, window):
    """Off-grid values for a given window must fail — an L5 of 90% is
    mathematically impossible (closest valid: 80 or 100)."""
    assert not _is_on_grid(rate, window), (
        f"off-grid value {rate}% (L{window}) should NOT validate. "
        f"Closest valid: {_closest_grid(rate, window)}%."
    )


def _is_on_grid(rate: float, window: int) -> bool:
    if rate is None:
        return True
    step = 100 / window
    # Tolerate sub-1% float drift; nothing else.
    nearest = round(rate / step) * step
    return abs(rate - nearest) < 1e-6


def _closest_grid(rate: float, window: int) -> float:
    step = 100 / window
    return round(rate / step) * step


# ── Contract 4 — every L5 frontend reader consults `hit_rate_l5` first
@pytest.mark.parametrize("relpath", [
    "components/dashboard/PlayerDetailPage.jsx",
    "components/dashboard/UniversalPlayerCard.jsx",
    "pages/Dashboard.jsx",
    "components/dashboard/CommandPost.jsx",
])
def test_l5_readers_prefer_canonical_hit_rate_l5(relpath):
    """Static parse: every L5-rate reader must reference `hit_rate_l5`
    in the same nullish-coalescing / `||` chain that consumes a legacy
    alias.

    We collapse multi-line chains (joined ternary / `?? / ||`
    expressions) to a single string, then scan for legacy alias usage
    that lacks a canonical companion within the chain.
    """
    text = (FRONTEND_ROOT / relpath).read_text()
    assert "hit_rate_l5" in text, (
        f"{relpath}: no reference to canonical `hit_rate_l5` — every "
        f"L5 reader must consult the score-doc SSOT."
    )

    # Collapse continuation lines into a single "logical chunk" using
    # the trailing-operator rule: if the previous chunk's last
    # non-whitespace token ends with `??` / `||` / `?` / `:` / `(` /
    # `=`, the next line is a continuation. Line numbers track the
    # FIRST line of each chunk.
    lines = text.splitlines()
    chunks: list[tuple[int, str]] = []
    cont_tail = re.compile(r"(\?\?|\|\||[?:(=])\s*$")
    for n, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if chunks:
            prev_n, prev_chunk = chunks[-1]
            if cont_tail.search(prev_chunk):
                chunks[-1] = (prev_n, prev_chunk + " " + stripped)
                continue
            # Also merge when current line clearly continues an
            # expression (starts with `?` `:` `||` `??` `)` `}` `.`).
            if stripped.startswith(("?", ":", "||", "??", ")", "}", ".")):
                chunks[-1] = (prev_n, prev_chunk + " " + stripped)
                continue
        chunks.append((n, raw))

    # Legacy L5 RATE aliases (NOT averages — `.l5?.avg` / `.l5_avg`
    # are season-window averages, separate concept).
    legacy_rate_pattern = re.compile(
        r"\b(h5_rate|l5_hit_rate)\b"
        r"|hit_rates\??\.l5(?!_avg|\?\.avg)\??\.(?:hit_rate|l5_rate|rate)"
        r"|hit_rates\??\.l5_rate"
    )
    bad = []
    for n, chunk in chunks:
        if not legacy_rate_pattern.search(chunk):
            continue
        if "hit_rate_l5" in chunk:
            continue  # canonical-first chain — OK
        stripped = chunk.lstrip()
        # Skip pure declarations / comments / imports / object-key
        # write-backs.
        if stripped.startswith(("//", "/*", "* ", "import ", "export ", "*/}")):
            continue
        if "score doc" in chunk or "SSOT" in chunk:
            continue  # in-source documentation references
        # Object-literal SHORTHAND (`h5_rate,`) is a write-back; allow.
        if re.match(r"^\s*h5_rate\s*[,:}]", chunk):
            continue
        if re.match(r"^\s*l5_hit_rate\s*[,:}]", chunk):
            continue
        # Bare destructure of `h5_rate` from props (write-back).
        if re.search(r"^\s*\{[^{}]*\bh5_rate\b[^{}]*\}\s*=", chunk):
            continue
        bad.append((n, chunk.strip()[:140]))
    assert not bad, (
        f"{relpath}: legacy L5 alias used WITHOUT canonical "
        f"`hit_rate_l5` companion (SSOT breach). Chunks:\n  "
        + "\n  ".join(f"L{n}: {c}" for n, c in bad[:5])
    )


# ── Live API guards (smoke) ──────────────────────────────────────────
def test_api_responses_have_on_grid_values_for_active_picks():
    """Sanity guard against backend regressions: every active-pick L5
    in the live API response must be on-grid. This is the canary for
    Contract 1 at the data layer."""
    import urllib.request
    import json

    base = os.environ.get("REACT_APP_BACKEND_URL")
    if not base:
        env = Path("/app/frontend/.env").read_text()
        m = re.search(r"REACT_APP_BACKEND_URL=(\S+)", env)
        if m:
            base = m.group(1)
    if not base:
        pytest.skip("REACT_APP_BACKEND_URL unavailable")

    try:
        req = urllib.request.Request(
            f"{base}/api/v3/ferrari/all",
            headers={"User-Agent": "ssot-grid-test/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode())
    except Exception:
        # Public preview URL is behind a CDN that may 403 unauthenticated
        # python clients. Fall back to localhost (same-pod backend).
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8001/api/v3/ferrari/all", timeout=10
            ) as r:
                payload = json.loads(r.read().decode())
        except Exception as exc:  # pragma: no cover - smoke only
            pytest.skip(f"live API unreachable: {exc}")

    bad = []
    for tier in ("safe_haven", "front_lines", "war_zone"):
        for p in (payload.get(tier, {}) or {}).get("picks", []) or []:
            l5 = p.get("hit_rate_l5")
            l10 = p.get("hit_rate_l10")
            l20 = p.get("hit_rate_l20")
            if l5 is not None and not _is_on_grid(l5, 5):
                bad.append((tier, p["player_name"], "L5", l5))
            if l10 is not None and not _is_on_grid(l10, 10):
                bad.append((tier, p["player_name"], "L10", l10))
            if l20 is not None and not _is_on_grid(l20, 20):
                bad.append((tier, p["player_name"], "L20", l20))
    assert not bad, (
        "Live API has off-grid hit-rate windows:\n  "
        + "\n  ".join(f"[{t}] {pl} {w}={v}" for t, pl, w, v in bad[:10])
    )

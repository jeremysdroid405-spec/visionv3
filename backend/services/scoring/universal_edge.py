"""Universal Edge SSOT (2026-05-15).

Single owner of the canonical model-vs-market edge metric.

  edge_vs_fair  =  p_model - fair_prob       (decimal,   stored)
  edge_pct      =  edge_vs_fair * 100        (pp,        derived only)

`fair_prob` is sourced via `services.scoring.scoring_stack
._pick_fair_probability` — the same source the vision score uses, so
gates and UI evaluate the SAME number.

FIELD OWNERSHIP CONTRACT
------------------------
  owner   : services/scoring/universal_edge.py
  writers : ONLY `compute_edge_vs_fair()` in this module
  readers : scoring_stack, gates/engine, metrics_builder, recompute,
            score-doc API, UI render layer
"""
from __future__ import annotations

from typing import Dict, Optional


def compute_edge_vs_fair(
    p_model: Optional[float],
    fair_prob: Optional[float],
) -> Optional[float]:
    """Canonical decimal edge. Returns None when either input is None."""
    if p_model is None or fair_prob is None:
        return None
    return round(float(p_model) - float(fair_prob), 4)


def derive_edge_pct(
    edge_vs_fair: Optional[float],
) -> Optional[float]:
    """Convert canonical decimal edge → percentage-points for gates.
    Pure derivation — never recomputes edge."""
    if edge_vs_fair is None:
        return None
    return round(float(edge_vs_fair) * 100.0, 4)


def compute_edge_bundle(
    p_model: Optional[float],
    fair_prob: Optional[float],
) -> Dict[str, Optional[float]]:
    """One-shot helper for adapters. Adapters MUST call this exclusively
    and NEVER compute edge from `(p_model * 100) - tp` or any variant."""
    edge_vs_fair = compute_edge_vs_fair(p_model, fair_prob)
    return {
        "edge_vs_fair": edge_vs_fair,
        "edge_pct":     derive_edge_pct(edge_vs_fair),
    }


# ─────────────────────────────────────────────────────────────────────
# Drift detector — flags duplicate edge writers anywhere in the
# scoring package. Invoked by `scripts/lint_universal_edge.py` (CI) and
# optionally from FastAPI startup. Not a hot-path call.
# ─────────────────────────────────────────────────────────────────────
_DUPLICATE_PATTERNS = (
    "p_model * 100", "p_model*100",
    "p_model * 100.0", "p_model*100.0",
    "p_model - fair_prob",
    "fair_prob - p_model",
    "edge_vs_fair * 100", "edge_vs_fair*100",
)

# Modules explicitly permitted to reference the patterns above
# (docstrings, historical replay, vegas regression which uses a
# different "edge" semantic, audit scripts, etc.).
_ALLOWLIST_MODULES = frozenset({
    "services.scoring.universal_edge",
    # `scoring_stack._compute_vision_score` was the original canonical
    # `edge_vs_fair` writer; kept inline to avoid an import cycle
    # with universal_edge. The math is bit-identical.
    "services.scoring.scoring_stack",
    "services.replay.engine",
    "services.replay.scoring_only",
    "services.forward_test.pick_history",
    "services.forward_test.mlb_pick_history",
    "services.vegas_regression_model",
    "services.intel_suite_calculator",
    "services.gemini_scout_engine",
    "scripts.phase2a_edge_gate_audit",
    "scripts.audit_mlb_pa_inputs",
    "scripts.vision_v1_vs_v2_backtest",
    "scripts.vision_walkthrough",
})


def audit_edge_writers(repo_root: str = "/app/backend") -> Dict[str, object]:
    """Walk services/scoring/ and routes/ for duplicate edge writers.
    Returns {'scanned': int, 'violations': [...]}. Empty violations
    means the SSOT contract holds."""
    import os
    violations: list = []
    scanned = 0
    for root_dir in (
        os.path.join(repo_root, "services", "scoring"),
        os.path.join(repo_root, "routes"),
    ):
        for dirpath, _, files in os.walk(root_dir):
            if "__pycache__" in dirpath or "_archive" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, fn)
                scanned += 1
                rel = (os.path.relpath(fp, repo_root)[:-3]
                        .replace(os.sep, "."))
                if rel in _ALLOWLIST_MODULES:
                    continue
                if rel.endswith("universal_edge"):
                    continue
                try:
                    with open(fp, "r", encoding="utf-8",
                               errors="ignore") as f:
                        src = f.read()
                except OSError:
                    continue
                for pat in _DUPLICATE_PATTERNS:
                    if pat in src:
                        for ln_no, ln in enumerate(src.splitlines(), 1):
                            if pat in ln and not ln.lstrip().startswith("#"):
                                violations.append({
                                    "module": rel, "line": ln_no,
                                    "pattern": pat,
                                    "snippet": ln.strip()[:160],
                                })
                                break
                        break
    return {"scanned": scanned, "violations": violations}


__all__ = [
    "compute_edge_vs_fair",
    "derive_edge_pct",
    "compute_edge_bundle",
    "audit_edge_writers",
]

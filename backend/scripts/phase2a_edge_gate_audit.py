"""Forensic edge-gate audit (2026-05-15).

Goal: prove or disprove that the gate-evaluated edge field is the same
as the UI-displayed edge field for FL OVER rejects with
tier_reason=gate_edge_fail.

Two distinct edge metrics exist in the codebase:

  (1) edge_vs_fair  — SSOT field on the score doc.
       Built by `scoring_stack._compute_vision_score()`:
         edge_vs_fair = p_model - fair_prob              (decimal, 4dp)
       where `fair_prob` is the dev-igged multi-book FAIR probability
       (Pinnacle preferred → DK/FD/MGM chain). Used by the UI as the
       displayed "edge".

  (2) edge_pct     — what the FIRST-PASS gate evaluator reads.
       Built by `adapters/mlb_scoring.py`:
         edge_pct = round((p_model * 100) - tp, 1)        (percent, 1dp)
       where `tp` is also a dev-igged multi-book TP but built via a
       DIFFERENT selection chain (`_pick_tp`) than `fair_prob`.

  (3) edge_pct (RE-EVAL) — what the SECOND-PASS gate reads.
       Rebuilt by `metrics_builder.build_metrics_from_score_doc()`:
         edge_pct = doc['edge_vs_fair'] * 100.0           (percent, fp)

So the gate sees TWO DIFFERENT edge values depending on whether it's
the first-pass live scoring or a re-eval pass — and neither is bit-
identical to the UI's `edge_vs_fair`.

This script dumps every relevant field for the 4 named props.
"""
from __future__ import annotations
import asyncio, os, sys
from typing import Dict, Any, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


TARGETS = [
    ("Shea Langeliers",    "Total Bases",      0.5, "OVER"),
    ("Freddie Freeman",    "Total Bases",      0.5, "OVER"),
    ("Daulton Varsho",     "Hits+Runs+RBIs",   0.5, "OVER"),
    ("Josh Naylor",        "Hits+Runs+RBIs",   0.5, "OVER"),
]

# Gate thresholds from /app/backend/services/scoring/gates/thresholds.py
GATE_THRESHOLDS = {
    # From /app/backend/services/scoring/gates/thresholds.py line 396-408:
    # _MLB_FRONT_LINES — edge_min: 5.0 PP for ALL families.
    "mlb_front_lines_OVER": 5.0,
    "mlb_safe_haven":        None,   # per-family edge_min (varies)
    "mlb_warzone":           30.0,   # universal warzone floor (line 410+)
}


def _fmt(v, d=4):
    if v is None:
        return "None"
    if isinstance(v, (int, float)):
        return f"{v:.{d}f}"
    return str(v)


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    print("=" * 95)
    print("EDGE-GATE FORENSIC AUDIT — Two-metric inconsistency check")
    print("=" * 95)
    print("Threshold for FL OVER edge_gate: edge_pct >= 0.01 (pp)")
    print("UI displays:    edge_vs_fair   (= p_model - fair_prob)")
    print("Gate evaluates: edge_pct       (= (p_model*100) - tp, ROUNDED to 1dp)")
    print()

    for name, stat, line, rec in TARGETS:
        rows = await db.mlb_prop_scores.find(
            {"player_name": name, "stat_type": stat,
             "line": line, "recommendation": rec, "active": True},
            {"_id": 0},
        ).to_list(length=5)
        if not rows:
            print(f"── {name} {stat} {line} {rec}: NO ACTIVE ROW")
            continue
        # Use one row (they should all be identical for this stat/line)
        r = rows[0]
        print("─" * 95)
        print(f"── {name} {stat} {line} {rec}")
        print("─" * 95)
        # Identifiers
        print(f"  projection_model_version : {r.get('projection_model_version')}")
        print(f"  tier_reason              : {r.get('tier_reason')}")
        print(f"  routed_tier / tier       : {r.get('routed_tier')} / {r.get('tier')}")

        # Probability inputs
        p_model_doc    = r.get("p_model")
        tp             = r.get("tp")
        fair_prob      = r.get("fair_prob")
        market_prob    = r.get("market_probability") or r.get("consensus_implied_prob")

        # ── Reconstruct p_model from SSOT: p_model = fair_prob + edge_vs_fair
        # The doc-level `p_model` is None for active rows (it's only
        # stored on terminal docs); the SSOT-recoverable value lives
        # in `edge_vs_fair` + `fair_prob`.
        edge_vs_fair  = r.get("edge_vs_fair")
        if p_model_doc is None and fair_prob is not None and edge_vs_fair is not None:
            p_model = fair_prob + edge_vs_fair
        else:
            p_model = p_model_doc

        print()
        print(f"  p_model (DOC raw)        : {_fmt(p_model_doc, 6)}")
        print(f"  p_model (RECONSTRUCTED)  : {_fmt(p_model, 6)}  "
              f"(= fair_prob + edge_vs_fair)")
        print(f"  tp (devig)               : {_fmt(tp, 4)}    (units: percent 0..100)")
        print(f"  fair_prob (devig)        : {_fmt(fair_prob, 6)}    (units: decimal 0..1)")
        print(f"  market_probability       : {_fmt(market_prob, 6)}")

        # The three edge values
        total_edge    = r.get("total_edge")        # vs best book
        best_edge     = r.get("best_book_edge")    # alias

        # Reconstruct gate input
        if p_model is not None and tp is not None:
            gate_edge_raw    = (p_model * 100.0) - tp
            gate_edge_round  = round(gate_edge_raw, 1)
        else:
            gate_edge_raw = gate_edge_round = None
        if p_model is not None and fair_prob is not None:
            ssot_edge_raw    = p_model - fair_prob
            ssot_edge_round4 = round(ssot_edge_raw, 4)
            ssot_edge_pct    = ssot_edge_raw * 100.0
        else:
            ssot_edge_raw = ssot_edge_round4 = ssot_edge_pct = None

        print()
        print("  ── UI / DISPLAYED FIELDS ──")
        print(f"  edge_vs_fair (DB)        : {_fmt(edge_vs_fair, 6)}        (units: decimal)")
        print(f"  total_edge (DB)          : {_fmt(total_edge, 6)}        (units: decimal)")
        print(f"  best_book_edge (DB)      : {_fmt(best_edge, 6)}        (units: decimal)")

        print()
        print("  ── GATE INPUT (first-pass, adapters/mlb_scoring.py) ──")
        print(f"  edge_pct = round((p_model*100) - tp, 1)")
        print(f"    raw (unrounded)        : {_fmt(gate_edge_raw, 6)}")
        print(f"    ROUNDED (gate sees)    : {_fmt(gate_edge_round, 4)}")

        print()
        print("  ── GATE INPUT (re-eval, metrics_builder.py) ──")
        print(f"  edge_pct = doc['edge_vs_fair'] * 100")
        print(f"    re-eval value          : {_fmt(ssot_edge_pct, 6)}")

        print()
        print("  ── INCONSISTENCY ANALYSIS ──")
        # Inconsistency 1: gate metric (edge_pct) vs UI metric (edge_vs_fair)
        same_as_ssot = (
            gate_edge_round is not None and ssot_edge_round4 is not None
            and abs(gate_edge_round - round(ssot_edge_pct, 1)) < 0.01
        )
        print(f"  gate_edge_pct ≈ edge_vs_fair*100? : "
              f"{'YES' if same_as_ssot else 'NO  ← MISMATCH'}")
        if gate_edge_round is not None and ssot_edge_pct is not None:
            delta = gate_edge_round - ssot_edge_pct
            print(f"  delta (gate − ssot*100)            : {_fmt(delta, 4)} pp")

        # Inconsistency 2: rounding causing pass/fail flip
        thresh = GATE_THRESHOLDS["mlb_front_lines_OVER"]
        if gate_edge_raw is not None:
            pass_raw    = gate_edge_raw    >= thresh
            pass_round  = gate_edge_round  >= thresh
            pass_ssot   = (ssot_edge_pct or -999) >= thresh
            rounding_flips = pass_raw != pass_round
            print(f"  threshold (FL OVER)              : {thresh}")
            print(f"  pass with raw     ({_fmt(gate_edge_raw, 4):>7})         : {pass_raw}")
            print(f"  pass with rounded ({_fmt(gate_edge_round, 1):>7})       : {pass_round}  "
                  f"{'(rounding FLIPPED outcome)' if rounding_flips else ''}")
            print(f"  pass with SSOT    ({_fmt(ssot_edge_pct, 4):>7})         : {pass_ssot}")

        # Inconsistency 3: total_edge vs edge_vs_fair gap
        if total_edge is not None and edge_vs_fair is not None:
            print(f"  total_edge − edge_vs_fair         : "
                  f"{_fmt(total_edge - edge_vs_fair, 4)}  "
                  f"({'best-book-only premium' if total_edge>edge_vs_fair else 'fair-multi premium'})")

        print()


if __name__ == "__main__":
    asyncio.run(main())

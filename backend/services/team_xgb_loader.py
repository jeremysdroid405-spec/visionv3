"""
services/team_xgb_loader.py — Trained team-prop XGB model loader.

Loads + caches per-(sport, market_category) artifacts produced by
`scripts/sgo/train_team_xgb.py`. Exposes a single `score_team_prop`
function that returns `(model_probability, edge, vision_score,
implied_probability)` for one prop row.

DESIGN
    • Lazy load: artifacts are read on first request and cached in
      memory for the life of the process. ~1.5MB per model × 12 models
      = ~18MB resident — trivial.
    • Pure scoring API: `score_team_prop(row)` returns Optional[dict].
      Returns None if no model is available for that (sport, mc).
    • The vision_score composite mirrors the player-side pattern:
      `vision_score = round(model_prob * (1 + max(0, edge)), 4)`.

Replay-contract compliance (`services.replay.contract`):
    ✅ Pure XGB inference; no `apply_production_eligibility` chain
       is ever invoked here. Every row with a valid (sport,
       market_category) tuple AND a loadable artifact gets a score
       doc. Rows with missing artifacts return None — that's a
       data-coverage signal, not a gate filter. The replay
       contract is satisfied by construction.
"""
from __future__ import annotations
import pickle
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

# Re-export the trainer helpers — same code path on both ends.
from scripts.sgo.train_team_xgb import (
    feature_columns, row_to_features, get_prior_fields,
    _american_implied_prob, VERSION,
)

# Replay-contract compliance flag — read by
# `tests/test_replay_infrastructure_contract.py` for static audit.
REPLAY_CONTRACT_COMPLIANT = True


ARTIFACT_ROOT = Path("/var/www/app/backend/models/team_xgb")
_CACHE: Dict[tuple, Optional[Dict[str, Any]]] = {}
_LOCK = threading.Lock()


def _artifact_path(sport: str, market_category: str) -> Path:
    return ARTIFACT_ROOT / sport / f"{market_category}.pkl"


def load_artifact(sport: str,
                    market_category: str) -> Optional[Dict[str, Any]]:
    """Memoized artifact load. Returns None if no artifact exists."""
    key = (sport, market_category)
    if key in _CACHE:
        return _CACHE[key]
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        p = _artifact_path(sport, market_category)
        if not p.exists():
            _CACHE[key] = None
            return None
        with p.open("rb") as f:
            payload = pickle.load(f)
        # Sanity: ensure feature contract matches.
        if payload.get("features") != feature_columns(payload.get("sport", "")):
            print(f"  [team_xgb_loader] WARNING: feature mismatch on "
                  f"{p} — refusing to load.")
            _CACHE[key] = None
            return None
        _CACHE[key] = payload
        return payload


def score_team_prop(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Score one `team_model_prop_features` row.

    Returns dict:
        {model_probability, implied_probability, edge, vision_score,
         model_version}
    Or None if no model is available for that (sport, market_category).

    Pure-ish — touches the cached model but never the DB.
    """
    sport = row.get("sport")
    mc = row.get("market_category")
    if not (sport and mc):
        return None
    art = load_artifact(sport, mc)
    if art is None:
        return None
    model = art["model"]
    scaler = art["scaler"]
    vec = np.array([row_to_features(row, sport)], dtype=np.float64)
    vec_s = scaler.transform(vec)
    p = float(model.predict_proba(vec_s)[0, 1])
    odds = row.get("odds")
    implied = (_american_implied_prob(float(odds))
                if isinstance(odds, (int, float)) and odds else None)
    edge = (p - implied) if implied is not None else None
    vision = round(p * (1.0 + max(0.0, edge or 0.0)), 4)
    reg_home = art.get("regressor_home")
    reg_away = art.get("regressor_away")
    score_projection = None
    if reg_home is not None and reg_away is not None:
        score_projection = {
            "home": round(float(reg_home.predict(vec_s)[0]), 1),
            "away": round(float(reg_away.predict(vec_s)[0]), 1),
        }
    return {
        "model_probability":    round(p, 4),
        "implied_probability":  (round(implied, 4)
                                       if implied is not None else None),
        "edge":                 (round(edge, 4)
                                       if edge is not None else None),
        "vision_score":         vision,
        "model_version":        VERSION,
        "score_projection":     score_projection,
    }


def score_team_props_batch(
    rows: list,
) -> list:
    """Batched scoring API. Groups rows by (sport, market_category),
    runs ONE predict_proba per group, returns a list of result dicts
    (one per input row, in input order). Result dict shape matches
    `score_team_prop`. None entries for rows lacking a model.

    Bulk inference is ~100× faster than per-row calls when reshape
    has to process 1M+ rows."""
    out: list = [None] * len(rows)
    # Group indices by (sport, mc)
    groups: Dict[tuple, list] = {}
    for i, r in enumerate(rows):
        s = r.get("sport")
        mc = r.get("market_category")
        if not (s and mc):
            continue
        groups.setdefault((s, mc), []).append(i)

    for (sport, mc), idx_list in groups.items():
        art = load_artifact(sport, mc)
        if art is None:
            continue
        model = art["model"]
        scaler = art["scaler"]
        reg_home = art.get("regressor_home")
        reg_away = art.get("regressor_away")
        # Build a single matrix for the whole group.
        mat = np.array(
            [row_to_features(rows[i], sport) for i in idx_list],
            dtype=np.float64)
        mat_s = scaler.transform(mat)
        probs = model.predict_proba(mat_s)[:, 1]
        home_preds = reg_home.predict(mat_s) if reg_home is not None else None
        away_preds = reg_away.predict(mat_s) if reg_away is not None else None
        for j, i in enumerate(idx_list):
            p = float(probs[j])
            odds = rows[i].get("odds")
            implied = (_american_implied_prob(float(odds))
                          if isinstance(odds, (int, float)) and odds else None)
            edge = (p - implied) if implied is not None else None
            vision = round(p * (1.0 + max(0.0, edge or 0.0)), 4)
            sp = None
            if home_preds is not None and away_preds is not None:
                sp = {
                    "home": round(float(home_preds[j]), 1),
                    "away": round(float(away_preds[j]), 1),
                }
            out[i] = {
                "model_probability":    round(p, 4),
                "implied_probability":  (round(implied, 4)
                                              if implied is not None else None),
                "edge":                 (round(edge, 4)
                                              if edge is not None else None),
                "vision_score":         vision,
                "model_version":        VERSION,
                "score_projection":     sp,
            }
    return out


def list_loaded_artifacts() -> Dict[str, Any]:
    """Diagnostic: which models are currently in the cache."""
    return {
        f"{s}/{mc}": (None if a is None else {
            "samples": a["samples"],
            "feature_count": a["feature_count"],
            "trained_at": a["trained_at"],
            "auc_test": a["metrics"]["auc_test"],
            "version": a["version"],
        })
        for (s, mc), a in _CACHE.items()
    }

"""
Replay VK2 — historical, as-of-time projection service.

WHAT THIS DOES
--------------
For a given replay snapshot timestamp, generate the SAME VK2 projection
production would have generated, using:

  * production model pickles (`/app/backend/models/vk2_*.pkl`)
  * production feature builder
    (`services.scoring.nba_vk2_features.build_features`)
  * leakage-gated history pulled from `bdl_historical_game_logs`
  * leakage-gated adv-stat lookups from `bdl_advanced_stats`

NO FORK. We `import build_features` and `model.predict()` exactly the
way `services.scoring.adapters.nba_scoring.NBAScoringAdapter._predict_vk2_prob_over`
does. The differences vs production are the data-source slices (we
strictly filter to before snapshot) and the absence of live wiring
(rate × minutes / availability guard / shadow recipes / heteroscedastic
sigma) — those are downstream of the projection step and are by design
NOT part of "VK2 parity" per the user spec ("μ drives direction, TP,
edge, tiers"). They'll be wired in subsequent phases.

LEAKAGE RULES (HARD ASSERTIONS, NOT WARNINGS)
---------------------------------------------
Every public function in this module guarantees:
  1. `assert_no_future_games(logs, as_of_ts=snapshot_ts, timestamp_field="date")`
     fires on EVERY history slice that hits `build_features`.
  2. `assert_pregame_only(snapshot_ts, commence_time)` runs at the
     orchestration boundary (one level up).
  3. The adv_map only contains rows whose `game_date < snapshot_date`.
  4. The function REFUSES (returns `error="vk2_unsupported_family"`)
     for stat families without a trained model. There is no VK1
     fallback path; per spec we never silently degrade.

PUBLIC API
----------
    load_vk2_models()                 -> dict[stat] -> {model, scaler, …}
    PlayerIdResolver(db).resolve(name) -> Optional[int]
    build_history_logs_as_of(...)     -> list[dict] (newest-first, leakage-checked)
    build_adv_map_as_of(...)          -> dict[(pid, gid)] -> adv doc
    predict_vk2_as_of(...)            -> dict (projection / sigma / p_over / version)
    predict_combo_vk2_as_of(...)      -> dict (synthesized combo)
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import re
from datetime import datetime, timezone
from math import erf, sqrt
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from services.scoring.nba_vk2_features import ADV_FIELDS, build_features
from services.replay.leakage_checks import (
    LeakageDetected, assert_no_future_games,
)

logger = logging.getLogger(__name__)

VK2_DIR = "/app/backend/models"
VK2_FILE_MAP = {
    "PTS": "vk2_pts.pkl",
    "REB": "vk2_reb.pkl",
    "AST": "vk2_ast.pkl",
    "3PM": "vk2_3pm.pkl",
    "PRA": "vk2_pra.pkl",
}

# Combo families that have NO direct model and must be synthesized.
# Mirrors NBAScoringAdapter._COMBO_COMPONENTS.
COMBO_COMPONENTS = {
    "PTS_REB": ("PTS", "REB"),
    "PTS_AST": ("PTS", "AST"),
    "REB_AST": ("REB", "AST"),
}
COMBO_FALLBACK_RHO = 0.25

# Stat families production has but VK2 does NOT (BLK, STL, TURNOVERS).
# Replay refuses to score these via VK2 — they get marked unsupported
# rather than falling back to legacy VK1.
VK2_UNSUPPORTED_FAMILIES = {"BLK", "STL", "TURNOVERS"}

# Replay stat_family values (engine.py uses uppercase; nba_vk2_features
# uses MODEL_KEY style "PTS"/"REB"/"AST"/"3PM"/"PRA"). We translate.
# THREES is the canonical 3-pointers token in the replay normalizer.
REPLAY_FAMILY_TO_MODEL_KEY = {
    "PTS":     "PTS",
    "REB":     "REB",
    "AST":     "AST",
    "THREES":  "3PM",
    "PRA":     "PRA",
    "PTS_REB": "PTS_REB",
    "PTS_AST": "PTS_AST",
    "REB_AST": "REB_AST",
    "BLK":     "BLK",
    "STL":     "STL",
    "TURNOVERS": "TURNOVERS",
}


# ============================================================================
# Model loader (idempotent process-local cache).
# ============================================================================
_MODEL_CACHE: Dict[str, Dict[str, Any]] = {}


def load_vk2_models() -> Dict[str, Dict[str, Any]]:
    """Load all VK2 model pickles. Cached at module level so repeated
    calls during a replay run don't re-read disk.

    Returns dict keyed by uppercase stat ("PTS", "REB", "AST", "3PM", "PRA").
    Each value: {model, scaler, features (list[str]), sigma (float),
    version, feature_count}.
    """
    if _MODEL_CACHE:
        return _MODEL_CACHE
    for stat, fn in VK2_FILE_MAP.items():
        p = os.path.join(VK2_DIR, fn)
        if not os.path.exists(p):
            logger.warning("[replay.vk2] model missing: %s", p)
            continue
        with open(p, "rb") as f:
            payload = pickle.load(f)
        _MODEL_CACHE[stat] = {
            "model":         payload["model"],
            "scaler":        payload["scaler"],
            "features":      list(payload["features"]),
            "sigma":         float(payload["residual_sigma_empirical"]),
            "version":       payload.get("version"),
            "feature_count": payload.get("feature_count"),
        }
    return _MODEL_CACHE


# ============================================================================
# Player-ID resolver (replay normalizer stores lowercase names; VK2 needs
# bdl_player_id). Lazy single-pass over `bdl_historical_game_logs`.
# ============================================================================
def _norm_name(s: Optional[str]) -> str:
    """Strip non-alphanumerics, lowercase. Matches production's
    `services.replay.engine._norm_name` so two normalizations are
    canonically equal."""
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


class PlayerIdResolver:
    """Build a lazy {normalized_name: bdl_player_id} index from
    `bdl_historical_game_logs`. Picks the player_id with the most
    log rows for that name (handles trade / re-signing aliases).

    Kept as a class (not a free function) so a single index is shared
    across thousands of predict calls in one replay run.
    """

    def __init__(self, db, collection: str = "bdl_historical_game_logs"):
        self._db = db
        self._coll = collection
        self._index: Optional[Dict[str, int]] = None

    async def _build(self) -> Dict[str, int]:
        counts: Dict[str, Dict[int, int]] = {}
        cursor = self._db[self._coll].find(
            {}, {"player_name": 1, "player_id": 1, "_id": 0},
        )
        async for d in cursor:
            nm = _norm_name(d.get("player_name"))
            pid = d.get("player_id")
            if not nm or pid is None:
                continue
            counts.setdefault(nm, {}).setdefault(int(pid), 0)
            counts[nm][int(pid)] += 1
        # Pick most-frequent pid per normalized name.
        out: Dict[str, int] = {}
        for nm, pid_counts in counts.items():
            best_pid = max(pid_counts.items(), key=lambda kv: kv[1])[0]
            out[nm] = best_pid
        return out

    async def resolve(self, player_field: str) -> Optional[int]:
        if self._index is None:
            self._index = await self._build()
        return self._index.get(_norm_name(player_field))


# ============================================================================
# Leakage-gated as-of slices.
# ============================================================================
async def build_history_logs_as_of(
    db, *,
    bdl_player_id: int,
    as_of_ts: datetime,
    window: int = 20,
    collection: str = "bdl_historical_game_logs",
) -> List[Dict[str, Any]]:
    """Return up to `window` newest-first game logs strictly before
    `as_of_ts` for `bdl_player_id`. Raises `LeakageDetected` if any
    row slips through.

    Mirrors the column projection / minute-string handling of production
    `_get_vk2_history_logs` so the schemas hitting `build_features` are
    indistinguishable.
    """
    if as_of_ts.tzinfo is None:
        raise ValueError("as_of_ts must be tz-aware")
    cutoff_date_str = as_of_ts.date().isoformat()
    cursor = db[collection].find(
        {"player_id": int(bdl_player_id),
         "date": {"$lt": cutoff_date_str}},
        projection={
            "_id": 0, "player_id": 1, "game_id": 1, "season": 1,
            "date": 1, "min": 1, "team_id": 1, "home_team_id": 1,
            "pts": 1, "reb": 1, "ast": 1,
            "fg3m": 1, "fga": 1, "fg3a": 1, "fta": 1, "fgm": 1, "ftm": 1,
            "fg_pct": 1, "fg3_pct": 1, "ft_pct": 1,
            "stl": 1, "blk": 1, "turnover": 1,
            "player_name": 1,
        },
    ).sort("date", -1).limit(window)
    rows: List[Dict[str, Any]] = []
    async for r in cursor:
        rows.append(r)
    # Defensive leakage assertion.
    assert_no_future_games(
        rows, as_of_ts=as_of_ts, timestamp_field="date",
    )
    return rows


async def build_adv_map_as_of(
    db, *,
    pairs: Iterable[Tuple[int, int]],
    as_of_ts: datetime,
    collection: str = "bdl_advanced_stats",
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """Build `{(player_id, game_id): adv_doc}` for adv lookups in
    `build_features`. Filters strictly to `game_date < as_of_ts`.
    Returns an EMPTY map (not None) if the window has no adv coverage —
    `build_features` handles missing adv gracefully via `*_miss` flags.
    """
    if as_of_ts.tzinfo is None:
        raise ValueError("as_of_ts must be tz-aware")
    pairs = list({(int(p), int(g)) for p, g in pairs if p and g})
    if not pairs:
        return {}
    cutoff_date_str = as_of_ts.date().isoformat()
    proj = {"_id": 0, "player_id": 1, "game_id": 1, "game_date": 1}
    for f in ADV_FIELDS:
        proj[f] = 1
    # game_id+player_id in pairs, plus game_date pre-snapshot.
    or_clauses = [{"player_id": p, "game_id": g} for p, g in pairs]
    if not or_clauses:
        return {}
    cursor = db[collection].find(
        {"$or": or_clauses, "game_date": {"$lt": cutoff_date_str}},
        proj,
    )
    out: Dict[Tuple[int, int], Dict[str, Any]] = {}
    async for d in cursor:
        pid = d.get("player_id"); gid = d.get("game_id")
        if pid is None or gid is None:
            continue
        out[(int(pid), int(gid))] = d
    return out


# ============================================================================
# Feature hash (audit / lineage).
# ============================================================================
def feature_hash(feats: Dict[str, float], schema: Sequence[str]) -> str:
    """Stable SHA-1 over the (schema-ordered) numeric feature vector +
    feature names. Lets us detect silent feature drift across reruns."""
    h = hashlib.sha1()
    h.update(("|".join(schema)).encode("utf-8"))
    h.update(b"||")
    for name in schema:
        v = feats.get(name, 0.0)
        try:
            h.update(f"{name}={float(v):.6f}".encode("utf-8"))
        except (TypeError, ValueError):
            h.update(f"{name}=0".encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()


def adv_coverage_l10(history: List[Dict[str, Any]],
                     adv_map: Dict[Tuple[int, int], Dict[str, Any]]) -> int:
    """How many of the 10 most-recent games had adv stats present?
    Matches the production `adv_coverage_L10` feature semantics so
    downstream feature_completeness tagging is consistent."""
    n = 0
    for g in history[:10]:
        if (g.get("player_id"), g.get("game_id")) in adv_map:
            n += 1
    return n


def feature_completeness_label(adv_cov_l10: int) -> str:
    """`vk2_full` requires >=5 of the L10 games to carry adv stats —
    matches the threshold the production retrain script uses to mark
    a row reliable. Otherwise → `vk2_partial`."""
    return "vk2_full" if adv_cov_l10 >= 5 else "vk2_partial"


# ============================================================================
# Single-stat predictor (PTS / REB / AST / 3PM / PRA).
# ============================================================================
def _gaussian_p_over(mu: float, sigma: float, line: float) -> float:
    if sigma <= 0:
        return float("nan")
    z = (mu - line) / sigma
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


async def predict_vk2_as_of(
    db, *,
    bdl_player_id: int,
    stat_family: str,           # replay-side family ("PTS", "REB", "THREES", …)
    line: float,
    snapshot_ts: datetime,
    target_game: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the production VK2 model for one (player, stat, line) at
    `snapshot_ts`. Returns a dict consumable by the replay engine.

    On error (no model, no history, feature build fail) returns:
        {"projection": None, "sigma": None, "p_over": None,
         "error": <reason>}
    On success returns:
        {"projection", "sigma", "p_over", "model_version",
         "feature_count", "feature_hash",
         "adv_coverage_l10", "feature_completeness", "history_size",
         "error": None}
    """
    family_up = (stat_family or "").upper()
    model_key = REPLAY_FAMILY_TO_MODEL_KEY.get(family_up, family_up)

    if model_key in VK2_UNSUPPORTED_FAMILIES:
        return {"projection": None, "sigma": None, "p_over": None,
                "error": f"vk2_unsupported_family:{model_key}"}

    if model_key not in VK2_FILE_MAP:
        return {"projection": None, "sigma": None, "p_over": None,
                "error": f"no_vk2_model_for:{model_key}"}

    models = load_vk2_models()
    m = models.get(model_key)
    if m is None:
        return {"projection": None, "sigma": None, "p_over": None,
                "error": f"vk2_model_not_loaded:{model_key}"}

    history = await build_history_logs_as_of(
        db, bdl_player_id=bdl_player_id,
        as_of_ts=snapshot_ts, window=20,
    )
    if len(history) < 5:
        return {"projection": None, "sigma": None, "p_over": None,
                "error": f"insufficient_history:n={len(history)}"}

    pairs = [(g.get("player_id"), g.get("game_id")) for g in history[:10]]
    adv_map = await build_adv_map_as_of(
        db, pairs=pairs, as_of_ts=snapshot_ts,
    )

    # Use most-recent game as `target_game` if caller didn't supply
    # one — production does the same (`history[0]` for is_home context).
    tgt = target_game or history[0]

    feats = build_features(
        history_logs=history, target_game=tgt, adv_map=adv_map,
    )
    if feats is None:
        return {"projection": None, "sigma": None, "p_over": None,
                "error": "feature_build_failed"}

    schema = m["features"]
    row = np.asarray(
        [[float(feats.get(c, 0.0)) for c in schema]], dtype=np.float32,
    )
    try:
        row_s = m["scaler"].transform(row)
        mu = float(m["model"].predict(row_s)[0])
    except Exception as exc:  # noqa: BLE001
        return {"projection": None, "sigma": None, "p_over": None,
                "error": f"predict_failed:{exc}"}

    # Clamp negatives (production `apply_projection_intercept` then
    # `if projection < 0: projection = 0.0`). The replay path does NOT
    # apply the additive intercept calibration — it's an audit knob,
    # not part of the model. Stamp `_intercept_skipped=True` for
    # observability.
    if mu < 0:
        mu = 0.0

    sigma = float(m["sigma"])
    p_over = _gaussian_p_over(mu, sigma, float(line))

    cov = adv_coverage_l10(history, adv_map)
    return {
        "projection":           round(mu, 4),
        "sigma":                round(sigma, 4),
        "p_over":               round(p_over, 6),
        "model_version":        m["version"],
        "feature_count":        m["feature_count"],
        "feature_hash":         feature_hash(feats, schema),
        "adv_coverage_l10":     cov,
        "feature_completeness": feature_completeness_label(cov),
        "history_size":         len(history),
        "intercept_skipped":    True,
        "error":                None,
    }


# ============================================================================
# Combo predictor — synth from components with empirical covariance.
# ============================================================================
def _empirical_pair_cov(
    history: List[Dict[str, Any]],
    field_a: str, field_b: str,
    window: int = 20,
) -> Optional[float]:
    """Sample covariance over pre-snapshot logs. Mirrors production
    `_empirical_covariance` — same min-paired threshold (5) and same
    near-zero-variance refusal."""
    paired: List[Tuple[float, float]] = []
    for g in history[:window]:
        a = g.get(field_a); b = g.get(field_b)
        if a is None or b is None:
            continue
        try:
            paired.append((float(a), float(b)))
        except (TypeError, ValueError):
            continue
    if len(paired) < 5:
        return None
    xs = np.asarray([p[0] for p in paired])
    ys = np.asarray([p[1] for p in paired])
    if xs.std(ddof=1) < 1e-6 or ys.std(ddof=1) < 1e-6:
        return None
    return float(np.cov(xs, ys, ddof=1)[0, 1])


_COMBO_FIELD_MAP = {"PTS": "pts", "REB": "reb", "AST": "ast", "3PM": "fg3m"}


async def predict_combo_vk2_as_of(
    db, *,
    bdl_player_id: int,
    stat_family: str,           # "PTS_REB" | "PTS_AST" | "REB_AST"
    line: float,
    snapshot_ts: datetime,
    target_game: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Synthesize a 2-component combo projection by summing component
    VK2 means and widening sigma via empirical covariance.

    Returns {projection, sigma, p_over, components, covariance_source,
    feature_completeness, error}. Component error short-circuits the
    whole synth — there is NO partial-component fallback (per spec:
    'no fallback to legacy VK1' applies transitively).
    """
    family_up = (stat_family or "").upper()
    components = COMBO_COMPONENTS.get(family_up)
    if not components:
        return {"projection": None, "sigma": None, "p_over": None,
                "error": f"not_a_combo_family:{family_up}"}

    history = await build_history_logs_as_of(
        db, bdl_player_id=bdl_player_id,
        as_of_ts=snapshot_ts, window=20,
    )
    if len(history) < 5:
        return {"projection": None, "sigma": None, "p_over": None,
                "error": f"insufficient_history:n={len(history)}"}

    comp_results: List[Dict[str, Any]] = []
    for c in components:
        # Component prediction reuses the same history slice (and
        # therefore the same adv_map) so combo math is consistent
        # with production. Pass `target_game` through so `is_home`
        # is identical across components.
        r = await predict_vk2_as_of(
            db, bdl_player_id=bdl_player_id,
            stat_family=c, line=line,
            snapshot_ts=snapshot_ts,
            target_game=target_game or history[0],
        )
        comp_results.append({"key": c, **r})

    if any(c["projection"] is None for c in comp_results):
        return {"projection": None, "sigma": None, "p_over": None,
                "components": comp_results,
                "error": "component_prediction_failed"}

    # Empirical pair covariance from history.
    fa = _COMBO_FIELD_MAP[components[0]]
    fb = _COMBO_FIELD_MAP[components[1]]
    cov = _empirical_pair_cov(history, fa, fb, window=20)
    cov_source = "empirical"
    if cov is None:
        si = comp_results[0]["sigma"]; sj = comp_results[1]["sigma"]
        cov = COMBO_FALLBACK_RHO * si * sj
        cov_source = "fallback_rho"

    proj = sum(c["projection"] for c in comp_results)
    var_combo = sum(c["sigma"] ** 2 for c in comp_results) + 2.0 * cov
    if var_combo <= 0:
        var_combo = max(1.0, sum(c["sigma"] ** 2 for c in comp_results))
        cov_source = "fallback_nonpositive_variance"
    sigma = sqrt(var_combo)
    p_over = _gaussian_p_over(proj, sigma, float(line))

    # Combo feature_completeness = WORST of components.
    fc_levels = ["vk2_partial", "vk2_full"]
    worst = min(
        (fc_levels.index(c["feature_completeness"]) for c in comp_results),
        default=0,
    )
    fc = fc_levels[worst]

    # Use the first component's model_version + feature_count as the
    # combo lineage anchor (acceptable since it's the only model
    # actually invoked twice with identical feature vectors).
    return {
        "projection":           round(proj, 4),
        "sigma":                round(sigma, 4),
        "p_over":               round(p_over, 6),
        "model_version":        comp_results[0].get("model_version"),
        "feature_count":        comp_results[0].get("feature_count"),
        "feature_hash":         comp_results[0].get("feature_hash"),
        "adv_coverage_l10":     min(c.get("adv_coverage_l10", 0)
                                    for c in comp_results),
        "feature_completeness": fc,
        "history_size":         len(history),
        "components":           [{"key": c["key"],
                                  "projection": c["projection"],
                                  "sigma": c["sigma"]}
                                 for c in comp_results],
        "covariance_source":    cov_source,
        "covariance":           round(float(cov), 4),
        "intercept_skipped":    True,
        "error":                None,
    }


__all__ = [
    "VK2_DIR", "VK2_FILE_MAP",
    "VK2_UNSUPPORTED_FAMILIES", "COMBO_COMPONENTS",
    "REPLAY_FAMILY_TO_MODEL_KEY",
    "load_vk2_models", "PlayerIdResolver",
    "build_history_logs_as_of", "build_adv_map_as_of",
    "feature_hash", "adv_coverage_l10", "feature_completeness_label",
    "predict_vk2_as_of", "predict_combo_vk2_as_of",
]

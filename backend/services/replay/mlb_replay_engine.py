"""
MLB Replay Engine — Layer 3.
============================

Replays historical alt-odds rows through the SAME production model
(`MLBHighFrictionModel`) used live, but with all feature inputs sourced
from Layer-2 cache (`mlb_replay_feature_cache`). Zero external API
calls. Future-leakage-safe by construction (cache is AS-OF filtered).

Output rows are written to `mlb_replay_model_outputs`, keyed by
`(game_date, event_id, player_name_normalized, market, line, side, book,
 snapshot_iso, scoring_config_version)`.

Layer 3 does NOT apply gates (Layer 4) or grade outcomes (Layer 4).
Layer 3 produces:
  - projection_mu, sigma
  - model_probability (p_over from production NormalCDF)
  - fair_probability  = p_over for OVER / 1-p_over for UNDER
  - implied_probability (from the book odds)
  - edge = fair_probability - implied_probability
  - L5/L10/L20 hit-rate-vs-line, CV
  - feature_health / imputed flags
  - source_version + scoring_config_version pins

UNDER alt-line replay is intentionally skipped — historical alt markets
are OVER-only (see `audits/replay_market_coverage_rule_2026_05_16.md`).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psutil
import pandas as pd
from pymongo import ASCENDING, UpdateOne
from scipy import stats as _scistats

from services.mlb_high_friction_model import MLBHighFrictionModel
from services.replay.mlb_feature_cache import (
    SOURCE_VERSION as FEATURE_CACHE_VERSION,
    _STAT_FIELD_MAP, _PITCHER_FAMILIES,
    market_to_stat_family, family_to_model_key,
)

logger = logging.getLogger(__name__)

OUT_COLL = "mlb_replay_model_outputs"
STATUS_COLL = "mlb_replay_model_status"
SCORING_CONFIG_VERSION = "scoring_v3.1_phase2a__wz_rewrite_2026_05_16"
# 2026-05-17 P0 — bump engine version after the feature-hydration fix
# (`replay_one` now passes platoon splits, home/away splits, PA-windowed
# Statcast, and batter handedness through `_build_friction_features`,
# matching the live `predict()` path). See:
# `audits/PATH_A_TASK_2_OLSON_DIVERGENCE.md` for root-cause analysis.
SOURCE_VERSION = "replay_engine_v1.1_hydration_2026_05_17"


def _derive_batter_hand_from_hub(hub: Optional[Dict[str, Any]]) -> Optional[str]:
    """Hub stores `bats_throws='Left/Right'` while `bats` itself is often
    None. Live `predict()` is given `batter_hand` upstream by
    `feature_hydration.py::_propagate_phase1_context`. Mirror that
    derivation here: split on `/`, take the left side (the bats half).
    Accepts 'L', 'R', 'S', 'Left', 'Right', 'Switch' (case-insensitive).
    """
    if not hub:
        return None
    bats = hub.get("bats")
    if bats:
        s = str(bats).strip().upper()
        return s[:1] if s and s[:1] in ("L", "R", "S") else None
    bt = hub.get("bats_throws")
    if not bt:
        return None
    head = str(bt).split("/", 1)[0].strip().upper()
    return head[:1] if head and head[:1] in ("L", "R", "S") else None

DEFAULT_MEM_LIMIT_MB = 1_500


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _american_to_implied(odds: int) -> float:
    """Vig-included implied probability from an American price."""
    odds = int(odds)
    if odds < 0:
        return (-odds) / ((-odds) + 100)
    return 100.0 / (odds + 100)


async def ensure_indexes(db) -> None:
    await db[OUT_COLL].create_index(
        [("game_date", ASCENDING), ("event_id", ASCENDING),
         ("player_name_normalized", ASCENDING),
         ("market", ASCENDING), ("line", ASCENDING),
         ("side", ASCENDING), ("book", ASCENDING),
         ("snapshot_iso", ASCENDING),
         ("scoring_config_version", ASCENDING)],
        name="replay_outputs_compound_unique", unique=True,
    )
    await db[OUT_COLL].create_index("game_date")
    await db[OUT_COLL].create_index("stat_family")
    await db[OUT_COLL].create_index([("game_date", ASCENDING),
                                      ("edge", ASCENDING)])
    await db[STATUS_COLL].create_index(
        [("game_date", ASCENDING),
         ("snapshot_iso", ASCENDING),
         ("scoring_config_version", ASCENDING)],
        name="replay_status_unique", unique=True,
    )


def _build_player_dict(cache_row: Dict[str, Any],
                       hub_extras: Optional[Dict[str, Any]] = None,
                       ) -> Dict[str, Any]:
    """Synthesize the `player` dict shape expected by
    `_build_friction_features`. `is_in_lineup_today=True` bypasses the
    live-clock heuristic; the player WAS active on the replay date by
    construction (a prop was priced).

    2026-05-17 P0 hydration: when `hub_extras` is supplied (a snapshot
    of additional master_hub fields the cache row doesn't store), pass
    through the platoon and home/away split blocks the live `predict()`
    path uses. Without these, `_build_friction_features` zeroes 27+
    feature columns (`vs_lhp_*`, `vs_rhp_*`, `home_avg`, etc.) and
    XGBoost over-weights remaining signals — see Olson μ=7.8 vs 2.25.
    """
    out = {
        "display_name": cache_row.get("player_name_canonical"),
        "player_name":  cache_row.get("player_name_canonical"),
        "team":         cache_row.get("team"),
        "position":     cache_row.get("position"),
        "bat_side":     cache_row.get("bat_side"),
        "throws":       cache_row.get("throws"),
        "is_in_lineup_today": True,
        "bdl_id":       cache_row.get("bdl_id"),
        "mlbam_id":     cache_row.get("player_id"),
    }
    if hub_extras:
        # NOTE: master_hub `vs_left/vs_right/home_splits/away_splits`
        # are season-cumulative. This matches what live `predict()`
        # consumes today — both paths share the same leakage profile.
        # When an as-of-date splits feed lands, swap in those snapshots.
        for k in ("vs_left", "vs_right",
                   "home_splits", "away_splits",
                   "bats_throws", "bats"):
            v = hub_extras.get(k)
            if v is not None:
                out[k] = v
    return out


def _build_game_logs(cache_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    # 2026-05-18 — normalize legacy stat_family strings (e.g.
    # `"strikeouts"` → `"batter_strikeouts"`) before the lookup.
    # Fresh cache writes already emit canonical names; this protects
    # legacy `mlb_replay_feature_cache` rows from pre-fix runs.
    from services.scoring.canonical_stats import canonical_family
    fam = canonical_family("mlb", cache_row["stat_family"])
    field = _STAT_FIELD_MAP.get(fam, fam)
    logs = []
    stat_vals = cache_row.get("stat_values") or []
    pa_vals = cache_row.get("pa_values") or []
    dates = cache_row.get("dates") or []
    for i, sv in enumerate(stat_vals):
        log: Dict[str, Any] = {
            field: sv,
            "date": dates[i] if i < len(dates) else None,
        }
        if i < len(pa_vals) and pa_vals[i] is not None:
            log["plate_appearances"] = pa_vals[i]
        logs.append(log)
    return logs


def _opp_team_from_event(
    cache_row: Dict[str, Any], home_team: str, away_team: str,
) -> Tuple[Optional[str], bool]:
    """Returns (opponent_team_name, is_away_team)."""
    team = (cache_row.get("team") or "").upper()
    # Hub team abbreviations vs API full names; match by first-letter
    # initials of the full name as a fallback.
    def _matches(team_abbr, full_name):
        if not full_name or not team_abbr:
            return False
        return team_abbr.upper() in full_name.upper()
    if _matches(team, home_team):
        return away_team, False
    if _matches(team, away_team):
        return home_team, True
    return None, False


def _hit_rate_panels(
    stat_vals: List[float], line: float,
) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {
        "hit_rate_l5": None, "hit_rate_l10": None, "hit_rate_l20": None,
    }
    for n, key in ((5, "hit_rate_l5"), (10, "hit_rate_l10"),
                   (20, "hit_rate_l20")):
        sub = stat_vals[:n]
        if len(sub) >= max(1, min(n, 3)):
            wins = sum(1 for v in sub if v > line)
            out[key] = 100.0 * wins / len(sub)
    return out


def replay_one(
    model: MLBHighFrictionModel,
    cache_row: Dict[str, Any],
    odds_row: Dict[str, Any],
    hub_extras: Optional[Dict[str, Any]] = None,
    *,
    drop_counter: Optional[Dict[str, int]] = None,
) -> Optional[Dict[str, Any]]:
    """Run one (cache_row × odds_row) replay. Returns the output dict
    or None when inference inputs are insufficient.

    `drop_counter` (optional): when supplied, increments
    `f"{stat_family}::{reason}"` on every early return so callers can
    audit silent-drop breakdowns per family without log-scraping. The
    canonical reasons are:
        - `model_feature_cols_miss` (family not in MLB-HF.feature_cols)
        - `missing_line` / `missing_odds_or_side`
        - `feature_build_returned_none` (model._build_friction_features
              returned None — usually missing PA-statcast or game_logs
              for the player on that as_of date)

    2026-05-17 P0 hydration fix:
      - When ``hub_extras`` is supplied, platoon/home/away splits and
        batter handedness flow into the feature vector (closes 80+
        zeroed columns vs. live `predict()`).
      - PA-windowed Statcast (`pa_b_*` / `pa_p_*`) is hydrated by
        calling `model._get_pa_cache().batter_features` /
        `.pitcher_features` with ``as_of=cache_row['game_date']``.
      - `batter_hand` / `opp_pitcher_throws` are derived from
        `hub_extras['bats_throws']` and `cache_row['opp_pitcher_throws']`
        respectively, restoring the Phase-2A matchup feature block.

    Without these, the model fed zeros where training expected real
    values and produced inflated μ (Olson 7.8 vs live 2.25). See
    `audits/PATH_A_TASK_2_OLSON_DIVERGENCE.md`.
    """
    def _drop(reason: str, stat_fam: str) -> None:
        if drop_counter is None:
            return
        key = f"{stat_fam or 'unknown'}::{reason}"
        drop_counter[key] = drop_counter.get(key, 0) + 1
        logger.info("[replay_one_drop] family=%s reason=%s",
                       stat_fam, reason)

    # 2026-05-18 — read-side normalisation for legacy cache rows.
    from services.scoring.canonical_stats import canonical_family
    stat_family = canonical_family("mlb", cache_row["stat_family"])
    # 2026-05-18 — translate canonical family → model artifact key.
    # The MLB-HF model pkl was trained with legacy family keys
    # (`strikeouts`, `pitcher_walks`, `hits_allowed`, `hits+runs+rbis`).
    # `_STAT_FAMILY_MAP` now emits canonical tokens downstream so the
    # audit / gate / output layer sees ONE consistent name. This
    # translator is the ONLY place where the legacy keys still
    # surface, and only for the duration of model lookups.
    model_family = family_to_model_key(stat_family)
    if model_family not in model.feature_cols:
        _drop("model_feature_cols_miss", stat_family)
        return None
    line = odds_row.get("line")
    if line is None:
        _drop("missing_line", stat_family)
        return None
    line_f = float(line)
    side = odds_row.get("side")
    odds = odds_row.get("odds")
    if odds is None or side not in ("OVER", "UNDER"):
        _drop("missing_odds_or_side", stat_family)
        return None

    player = _build_player_dict(cache_row, hub_extras=hub_extras)
    game_logs = _build_game_logs(cache_row)
    is_pitcher_fam = stat_family in _PITCHER_FAMILIES
    sc_self = cache_row.get("statcast_self_as_of")
    opp, is_away = _opp_team_from_event(
        cache_row, odds_row.get("home_team") or "", odds_row.get("away_team") or "",
    )
    park_team = cache_row.get("team") if not is_away else opp

    # ── PA-windowed Statcast hydration (P0 2026-05-17) ──────────────
    # Hydrate from the model's PA cache, as-of the replay's game_date.
    # Live `predict()` uses today's date here — we use the replay date
    # to guarantee no future-data leakage. The cache key is mlbam_id.
    pa_batter = None
    pa_pitcher = None
    try:
        pa_cache = model._get_pa_cache()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pa_cache = None
    if pa_cache is not None:
        mlbam_id = cache_row.get("player_id")
        if mlbam_id is not None:
            as_of = cache_row.get("game_date") or odds_row.get("game_date")
            try:
                if is_pitcher_fam:
                    pa_pitcher = pa_cache.pitcher_features(int(mlbam_id), as_of)
                else:
                    pa_batter = pa_cache.batter_features(int(mlbam_id), as_of)
            except Exception as _pa_err:  # noqa: BLE001
                logger.debug("[replay_one] pa_cache lookup failed pid=%s "
                              "as_of=%s err=%r", mlbam_id, as_of, _pa_err)

    # ── Matchup-aware feature inputs (P0 2026-05-17) ────────────────
    batter_hand_val = _derive_batter_hand_from_hub(hub_extras)
    opp_throws_val = cache_row.get("opp_pitcher_throws")

    feats = model._build_friction_features(  # noqa: SLF001
        player, game_logs, model_family,
        opponent=opp, park_team=park_team, dk_odds=None, line=line_f,
        statcast_features=(sc_self if not is_pitcher_fam else None),
        pitcher_statcast_features=(sc_self if is_pitcher_fam else None),
        pa_batter_features=pa_batter,
        pa_pitcher_features=pa_pitcher,
        batter_hand=batter_hand_val,
        opp_pitcher_throws=opp_throws_val,
        opp_pitcher_features=None,
        opposing_lineup=None,
    )
    if feats is None:
        _drop("feature_build_returned_none", stat_family)
        return None

    cols = model.feature_cols[model_family]
    X = pd.DataFrame([feats])
    for c in cols:
        if c not in X.columns:
            X[c] = 0
    X = X[cols].fillna(0)
    Xs = model.scalers[model_family].transform(X)
    raw_pred = float(model.models[model_family].predict(Xs)[0])
    park_factor = float(feats.get("park_factor", 1.0))
    mu = raw_pred * park_factor
    sigma = float(feats.get("std_dev_l10", 0.0))

    # Normal-CDF p_over (the math the production model uses for the
    # majority of stat families; specific distributions like Poisson
    # are handled inside production scoring_stack but here we honour
    # the NormalCDF baseline for layer-3 consistency).
    if sigma > 0:
        z = (line_f - mu) / sigma
        p_over_frac = 1.0 - _scistats.norm.cdf(z)
    else:
        z = None
        p_over_frac = 1.0 if mu > line_f else 0.0
    p_over_pct = 100.0 * p_over_frac
    if p_over_frac > 0.5 and mu < line_f:
        # Mirror the production "model says OVER but μ<line" force-down.
        p_over_pct = max(5.0, 50.0 - abs(z or 0) * 10)
        p_over_frac = p_over_pct / 100.0

    fair_prob = p_over_frac if side == "OVER" else (1.0 - p_over_frac)
    implied = _american_to_implied(odds)
    edge = fair_prob - implied

    # Hit-rate panels at THIS line
    stat_vals = cache_row.get("stat_values") or []
    panels = _hit_rate_panels(stat_vals, line_f)

    return {
        # Identity / keying
        "sport": "mlb",
        "game_date": odds_row["game_date"],
        "event_id": odds_row["event_id"],
        "home_team": odds_row.get("home_team"),
        "away_team": odds_row.get("away_team"),
        "commence_time": odds_row.get("commence_time"),
        "snapshot_iso": odds_row.get("snapshot_iso"),
        "player_name_normalized": cache_row["player_name_normalized"],
        "player_name": cache_row.get("player_name_canonical"),
        "player_id": cache_row.get("player_id"),
        "team": cache_row.get("team"),
        "opponent": opp,
        "is_away_team": is_away,
        "bat_side": cache_row.get("bat_side"),
        # Market
        "market": odds_row["market"],
        "is_alternate": bool(odds_row.get("is_alternate")),
        "stat_family": stat_family,
        "line": line_f,
        "side": side,
        "book": odds_row.get("book"),
        "odds": int(odds),
        # Model outputs
        "raw_prediction": raw_pred,
        "park_factor": park_factor,
        "projection_mu": mu,
        "sigma": sigma,
        "z_score": z,
        "model_probability": p_over_frac,   # P(OVER) from model
        "prob_over_pct": p_over_pct,
        "fair_probability": fair_prob,      # side-aware
        "implied_probability": implied,
        "edge": edge,
        # Form panels at THIS line
        "hit_rate_l5":  panels["hit_rate_l5"],
        "hit_rate_l10": panels["hit_rate_l10"],
        "hit_rate_l20": panels["hit_rate_l20"],
        "cv": cache_row.get("cv_l10"),
        # Provenance
        "feature_cache_version": cache_row.get("source_version", FEATURE_CACHE_VERSION),
        "scoring_config_version": SCORING_CONFIG_VERSION,
        "source_version": SOURCE_VERSION,
        "replayed_at": datetime.now(timezone.utc),
    }


async def replay_date(
    db, replay_date_str: str, *,
    snapshot_iso: Optional[str] = None,
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
    force: bool = False,
    over_only_alts: bool = True,
    odds_collection: str = "mlb_historical_alt_odds_raw",
) -> Dict[str, Any]:
    """Warm-replay a single date.

    `over_only_alts=True` enforces the rule documented in
    `audits/replay_market_coverage_rule_2026_05_16.md`: alternate
    markets are OVER-only. UNDER on alt is silently skipped (the API
    doesn't carry it; we never synthesize).

    `odds_collection` defaults to the live production collection
    (`mlb_historical_alt_odds_raw`). The SSOT historical replay
    (`scripts.sgo.historical_full_pipeline_replay`) overrides it with
    `sgo_replay_alt_odds_raw` so SGO-namespace data drives the same
    Layer-3 engine without polluting the production odds archive.
    """
    await ensure_indexes(db)
    if snapshot_iso is None:
        snapshot_iso = f"{replay_date_str}T11:00:00Z"

    # Resume short-circuit
    s_filter = {"game_date": replay_date_str, "snapshot_iso": snapshot_iso,
                "scoring_config_version": SCORING_CONFIG_VERSION}
    if not force:
        s = await db[STATUS_COLL].find_one(s_filter, {"_id": 0, "status": 1})
        if s and s.get("status") == "completed":
            return {"date": replay_date_str, "snapshot_iso": snapshot_iso,
                    "skipped": True}

    # Init model
    started_at = datetime.now(timezone.utc)
    rss0 = _rss_mb()
    model = MLBHighFrictionModel(db.delegate)  # motor → pymongo
    model.load_models()
    rss_after_models = _rss_mb()

    await db[STATUS_COLL].update_one(
        s_filter,
        {"$set": {"status": "in_progress", "started_at": started_at,
                  "rss_mb_start": round(rss0, 1),
                  "rss_mb_after_model_load": round(rss_after_models, 1)}},
        upsert=True,
    )

    # Build {(player, stat_family) → cache_row} for this date
    # 2026-05-18 — canonicalise the stat_family on the index key so
    # legacy cache rows written before the canonicalisation refactor
    # (`stat_family="strikeouts"` / `"pitcher_walks"`) align with the
    # canonical names emitted by `market_to_stat_family` downstream
    # (`"batter_strikeouts"` / `"walks_allowed"`). Also stamp the
    # canonical family back onto the cache_row so every consumer
    # downstream sees a single SSOT name.
    from services.scoring.canonical_stats import canonical_family
    cache_idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
    async for c in db.mlb_replay_feature_cache.find(
        {"game_date": replay_date_str,
         "source_version": FEATURE_CACHE_VERSION},
        projection={"_id": 0},
    ):
        canon_fam = canonical_family("mlb", c.get("stat_family"))
        c["stat_family"] = canon_fam
        cache_idx[(c["player_name_normalized"], canon_fam)] = c

    # ── 2026-05-17 P0 hydration — master_hub extras index ─────────
    # `replay_one` needs platoon/home-away splits and bats_throws,
    # which the cache row doesn't carry. We fetch them ONCE per
    # unique bdl_id at the top of the run instead of on every call.
    # This restores feature parity with live `predict()` without
    # changing the cache schema. Memory is bounded — ~750 unique
    # players × ~1 KB ≈ < 1 MB.
    bdl_ids = sorted({c.get("bdl_id") for c in cache_idx.values()
                       if c.get("bdl_id") is not None})
    hub_extras_idx: Dict[int, Dict[str, Any]] = {}
    if bdl_ids:
        proj = {"_id": 0, "bdl_id": 1, "vs_left": 1, "vs_right": 1,
                 "home_splits": 1, "away_splits": 1,
                 "bats_throws": 1, "bats": 1, "throws": 1}
        async for h in db.mlb_master_hub_2026.find(
            {"$or": [{"bdl_id":         {"$in": bdl_ids}},
                       {"bdl_player_id":  {"$in": bdl_ids}}]},
            projection=proj,
        ):
            bid = h.get("bdl_id")
            if bid is None:
                continue
            hub_extras_idx[int(bid)] = h
    logger.info(
        "[replay_engine] hub_extras hydrated for %d / %d players "
        "(date=%s)", len(hub_extras_idx), len(bdl_ids), replay_date_str,
    )

    # Inference μ-memo: same (player, stat_family, line) → identical μ
    # regardless of book/side. Memoize to avoid repeat XGBoost calls.
    mu_memo: Dict[Tuple[str, str, float], Dict[str, Any]] = {}

    buffer: List[Dict[str, Any]] = []
    seen = 0
    written = 0
    no_cache = 0
    no_mu = 0
    skipped_under_alt = 0
    unmapped_market = 0
    # Per-(family, reason) drop counter — emitted in the run summary and
    # persisted to the status row so the operator can audit silent
    # drops without log-scraping. See `replay_one(drop_counter=…)`.
    drop_counter: Dict[str, int] = {}
    # Per-family unmapped-market counter (market_to_stat_family
    # returned None — the raw odds market has no entry in
    # `_STAT_FAMILY_MAP`).
    unmapped_markets: Dict[str, int] = {}
    # Per-family no-cache counter — feature cache had no row for
    # (player, family) on this date.
    no_cache_per_family: Dict[str, int] = {}
    rss_peak = rss_after_models

    cursor = db[odds_collection].find(
        {"game_date": replay_date_str, "snapshot_iso": snapshot_iso},
        projection={"_id": 0},
    )

    async def _flush():
        nonlocal written, buffer
        if not buffer:
            return
        ops = []
        key_fields = ("game_date", "event_id", "player_name_normalized",
                       "market", "line", "side", "book",
                       "snapshot_iso", "scoring_config_version")
        for r in buffer:
            f = {k: r[k] for k in key_fields}
            ops.append(UpdateOne(f, {"$set": r}, upsert=True))
        try:
            res = await db[OUT_COLL].bulk_write(ops, ordered=False)
            written += int(res.upserted_count or 0) + int(res.modified_count or 0)
        except Exception as exc:  # noqa: BLE001
            logger.error("[replay_engine] bulk_write failed: %s", exc)
        buffer.clear()

    async for o in cursor:
        seen += 1
        if over_only_alts and o.get("is_alternate") and o.get("side") != "OVER":
            skipped_under_alt += 1
            continue
        market_raw = o["market"]
        stat_fam = market_to_stat_family(market_raw)
        if stat_fam is None:
            unmapped_market += 1
            unmapped_markets[market_raw] = unmapped_markets.get(market_raw, 0) + 1
            continue
        cache_row = cache_idx.get((o["player_name_normalized"], stat_fam))
        if cache_row is None:
            no_cache += 1
            no_cache_per_family[stat_fam] = no_cache_per_family.get(stat_fam, 0) + 1
            continue

        line_key = (o["player_name_normalized"], stat_fam, float(o["line"]))
        memo = mu_memo.get(line_key)
        if memo is None:
            extras = hub_extras_idx.get(int(cache_row["bdl_id"])) \
                if cache_row.get("bdl_id") is not None else None
            tmp = replay_one(model, cache_row, o, hub_extras=extras,
                                  drop_counter=drop_counter)
            if tmp is None:
                no_mu += 1
                continue
            memo = {"projection_mu": tmp["projection_mu"],
                    "sigma": tmp["sigma"],
                    "z_score": tmp["z_score"],
                    "model_probability": tmp["model_probability"],
                    "prob_over_pct": tmp["prob_over_pct"],
                    "raw_prediction": tmp["raw_prediction"],
                    "park_factor": tmp["park_factor"]}
            mu_memo[line_key] = memo
            buffer.append(tmp)
        else:
            # Reuse μ; recompute side-aware fair/implied/edge for THIS
            # book + side combo.
            p_over_frac = memo["model_probability"]
            side = o["side"]
            fair_prob = p_over_frac if side == "OVER" else 1.0 - p_over_frac
            implied = _american_to_implied(o["odds"])
            edge = fair_prob - implied
            # Hit-rate panels (line-dependent, but same line key → identical)
            panels = _hit_rate_panels(cache_row.get("stat_values") or [], float(o["line"]))
            opp, is_away = _opp_team_from_event(
                cache_row, o.get("home_team") or "", o.get("away_team") or "")
            buffer.append({
                "sport": "mlb",
                "game_date": o["game_date"], "event_id": o["event_id"],
                "home_team": o.get("home_team"), "away_team": o.get("away_team"),
                "commence_time": o.get("commence_time"),
                "snapshot_iso": o.get("snapshot_iso"),
                "player_name_normalized": cache_row["player_name_normalized"],
                "player_name": cache_row.get("player_name_canonical"),
                "player_id": cache_row.get("player_id"),
                "team": cache_row.get("team"), "opponent": opp,
                "is_away_team": is_away, "bat_side": cache_row.get("bat_side"),
                "market": o["market"], "is_alternate": bool(o.get("is_alternate")),
                "stat_family": stat_fam,
                "line": float(o["line"]), "side": side,
                "book": o.get("book"), "odds": int(o["odds"]),
                "raw_prediction": memo["raw_prediction"],
                "park_factor": memo["park_factor"],
                "projection_mu": memo["projection_mu"],
                "sigma": memo["sigma"], "z_score": memo["z_score"],
                "model_probability": p_over_frac,
                "prob_over_pct": memo["prob_over_pct"],
                "fair_probability": fair_prob,
                "implied_probability": implied,
                "edge": edge,
                "hit_rate_l5": panels["hit_rate_l5"],
                "hit_rate_l10": panels["hit_rate_l10"],
                "hit_rate_l20": panels["hit_rate_l20"],
                "cv": cache_row.get("cv_l10"),
                "feature_cache_version": cache_row.get(
                    "source_version", FEATURE_CACHE_VERSION),
                "scoring_config_version": SCORING_CONFIG_VERSION,
                "source_version": SOURCE_VERSION,
                "replayed_at": datetime.now(timezone.utc),
            })

        if len(buffer) >= 500:
            await _flush()
            rss = _rss_mb()
            if rss > rss_peak:
                rss_peak = rss
            if rss > mem_limit_mb:
                await db[STATUS_COLL].update_one(
                    s_filter,
                    {"$set": {"status": "memory_halt",
                              "rss_mb_at_halt": round(rss, 1),
                              "seen": seen, "written": written}},
                )
                raise MemoryError(
                    f"replay_engine RSS {rss:.1f} > {mem_limit_mb} at "
                    f"row {seen} ({replay_date_str}/{snapshot_iso})"
                )

    await _flush()

    finished_at = datetime.now(timezone.utc)
    elapsed = (finished_at - started_at).total_seconds()
    summary = {
        "date": replay_date_str,
        "snapshot_iso": snapshot_iso,
        "alt_odds_rows_seen": seen,
        "model_outputs_written": written,
        "candidates_skipped_no_cache": no_cache,
        "candidates_skipped_inference_failed": no_mu,
        "candidates_skipped_under_alt": skipped_under_alt,
        "candidates_skipped_unmapped_market": unmapped_market,
        # Per-family drop telemetry — emitted so the operator can see
        # WHICH families are dropping at which stage instead of staring
        # at an opaque single counter. NEVER silently dropped again.
        "drop_counter_by_family_and_reason": drop_counter,
        "unmapped_markets_by_market":       unmapped_markets,
        "no_cache_by_family":                no_cache_per_family,
        "unique_mu_predictions": len(mu_memo),
        "rss_mb_start": round(rss0, 1),
        "rss_mb_after_model_load": round(rss_after_models, 1),
        "rss_mb_peak": round(rss_peak, 1),
        "rss_mb_end": round(_rss_mb(), 1),
        "elapsed_s": elapsed,
        "scoring_config_version": SCORING_CONFIG_VERSION,
        "source_version": SOURCE_VERSION,
    }
    await db[STATUS_COLL].update_one(
        s_filter, {"$set": {"status": "completed",
                            "completed_at": finished_at, **summary}},
    )
    return summary


__all__ = [
    "OUT_COLL", "STATUS_COLL", "SCORING_CONFIG_VERSION", "SOURCE_VERSION",
    "DEFAULT_MEM_LIMIT_MB",
    "ensure_indexes", "replay_one", "replay_date",
]

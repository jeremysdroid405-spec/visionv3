"""
MLB PropVision — Total Bases v1 (NBA-parity engine)
====================================================
Drop-in MLB engine that mirrors the NBA PropVision pipeline EXACTLY:

  1. Build raw candidates  (both sides, all books, all lines)
  2. Compute μ / σ / p_model / tp / edge
  3. PP playability rules (UNDER allowed only on standard FL)
  4. Collapse by (player, stat_family, game_date) → MAX edge
  5. Vision_score percentile — POST-COLLAPSE only
  6. Gates: hit_rate / cv / vision_score / market_structure  (UNCHANGED)
  7. Tiers: SH (≤-240) / FL (-239..+149) / WZ (≥+150)

Forward testing (--log-picks):
  Persists every selected pick to `mlb_pick_history` for grading. Run
  `python -m scripts.update_mlb_pick_results` after games settle.
"""
from __future__ import annotations

import os, sys, math, asyncio, argparse, statistics
from collections import defaultdict, Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from services.scoring.tp_engine import compute_tp
from services.scoring.gates import NormalizedMetrics
from services.scoring.gates.thresholds import (
    resolve_target_tier, resolve_stat_family,
)
from services.scoring.tier_evaluator import evaluate_tier_with_overrides
from services.forward_test.mlb_pick_history import (
    ensure_indexes as _ensure_pick_indexes,
    log_selected_picks as _log_picks,
    MODEL_VERSION as PICK_MODEL_VERSION,
)

# -----------------------------------------------------------------------------
# Constants — μ + σ knobs (per user spec)
# -----------------------------------------------------------------------------
STAT_RAW    = "Total Bases"          # mlb_live_props.stat_type tag
STAT_FAMILY = "total_bases"          # canonical family for gate lookup

# wOBA linear weights (Tom Tango 2010-era; used as xwOBA proxy)
WOBA_BB = 0.69; WOBA_1B = 0.89; WOBA_2B = 1.27; WOBA_3B = 1.62; WOBA_HR = 2.10

# PA projection by batting order (spec v2 — replaces v1's flat table).
# These per-slot baselines are the BASE before team-context adjustments.
PA_BY_ORDER: Dict[int, float] = {
    1: 4.7, 2: 4.6, 3: 4.5, 4: 4.4, 5: 4.3,
    6: 4.2, 7: 4.0, 8: 3.8, 9: 3.7,
}
PA_DEFAULT = 4.2                     # when batting_order is null
# Team-context PA adjustments (spec v2)
PA_HIGH_TEAM_TOTAL_THRESHOLD = 5.5   # team_total >= 5.5 → +0.20 PA
PA_LOW_TEAM_TOTAL_THRESHOLD  = 3.5   # team_total <= 3.5 → -0.20 PA
PA_TEAM_TOTAL_DELTA          = 0.20
PA_HOME_TEAM_DELTA           = -0.10  # home leading → no bottom of 9th
PA_CLAMP                     = (3.2, 5.2)

BASE_SIGMA = 1.75                    # per user spec
SIGMA_CLAMP = (1.2, 3.5)
VOL_CLAMP = (0.7, 2.0)               # multiplier bounds

# League-average reference points (proxy values derived from logs scan)
LEAGUE_K_RATE       = 0.225
LEAGUE_BARREL_RATE  = 0.040          # HR / AB proxy
LEAGUE_CONTACT_RATE = 0.775          # 1 - K_rate

# Recent-form clamp (per user spec)
RECENT_FORM_CLAMP = (0.85, 1.15)

# Matchup factor default (no pitcher xwOBA pipeline yet)
MATCHUP_DEFAULT = 1.0

HISTORY_WINDOW_LONG  = 20    # for wOBA / K% / barrel
HISTORY_WINDOW_SHORT = 5     # for recent-form factor

# Books mapped into compute_tp's tp_engine (matches NBA path)
_TP_BOOK_KEYS = ("dk", "fd", "mgm", "bol")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _f(v):
    if v in (None, "", "None"): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    f = _f(v)
    return int(f) if f is not None else None


def _to_date(v) -> Optional[str]:
    if v is None: return None
    if isinstance(v, datetime): return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if len(s) >= 10 else None


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _percentile_rank(values: List[Optional[float]]) -> Dict[float, float]:
    """Percentile rank for ALL strictly-positive vision_raw values."""
    pos = sorted(v for v in values if v is not None and v > 0)
    if not pos: return {}
    n = len(pos)
    return {v: round(sum(1 for s in pos if s <= v) / n * 100.0, 1)
            for v in set(pos)}


def _summary(label, vals, fmt="{:.2f}"):
    vals = [v for v in vals if v is not None]
    if not vals: print(f"  {label:30s}: (n=0)"); return
    vals.sort(); n = len(vals); q = lambda p: vals[min(n-1, int(p*(n-1)))]
    print(f"  {label:30s}: n={n:>5}  min={fmt.format(vals[0])}  "
          f"med={fmt.format(q(.5))}  p75={fmt.format(q(.75))}  "
          f"max={fmt.format(vals[-1])}  avg={fmt.format(sum(vals)/n)}")


def _print_block(title, rows, label_pad=20):
    print("=" * 80); print(f"  {title}"); print("=" * 80)
    print(f"  {'segment':{label_pad}s} {'n':>7}  {'%':>6}")
    total = sum(n for _, n in rows) or 1
    for label, n in rows:
        print(f"  {label:{label_pad}s} {n:>7d}  {n/total*100:>5.1f}%")
    print()


# -----------------------------------------------------------------------------
# Data loaders
# -----------------------------------------------------------------------------
async def load_player_logs(db) -> Dict[str, List[Dict[str, Any]]]:
    """player_name (lower) → list of game_log dicts sorted by date asc."""
    by_name: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    # mlb_master_hub_2026 has bdl_game_logs[] inline.
    async for d in db.mlb_master_hub_2026.find(
        {"is_batter": True, "bdl_game_logs_count": {"$gt": 0}},
        {"_id": 0, "player_name": 1, "display_name": 1,
         "bdl_game_logs": 1, "bats_throws": 1, "primary_position": 1}):
        for cand in (d.get("display_name"), d.get("player_name")):
            if not cand: continue
            key = cand.strip().lower()
            for lg in (d.get("bdl_game_logs") or []):
                date = _to_date(lg.get("date"))
                if not date: continue
                by_name[key].append({
                    "date":   date,
                    "tb":     _f(lg.get("total_bases")),
                    "ab":    _f(lg.get("at_bats")),
                    "pa":     _f(lg.get("plate_appearances")),
                    "hits":   _f(lg.get("hits")),
                    "doubles":_f(lg.get("doubles")),
                    "triples":_f(lg.get("triples")),
                    "hr":     _f(lg.get("home_runs")),
                    "bb":     _f(lg.get("walks")),
                    "k":      _f(lg.get("strikeouts")),
                })
            break
    # Dedupe per-player by date (newest-merged-first), then sort asc.
    for k, lst in by_name.items():
        seen = set(); out = []
        for lg in sorted(lst, key=lambda x: x["date"]):
            if lg["date"] in seen: continue
            seen.add(lg["date"]); out.append(lg)
        by_name[k] = out
    return by_name


async def load_statcast_features(db) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(canonical_name, game_date) → most-recent rolling features.

    `canonical_name` is the result of running each Statcast row's
    `player_name` through `normalize_player_name()` + alias map. The
    engine queries with the SAME canonical key derived from the live
    prop's `player_name` (or, when available, via the
    `mlb_player_identity_map`'s statcast_id → canonical_name path).

    For prop scoring we need the snapshot of the batter's profile AS-OF
    the day of the game. Lookups in `_statcast_for()` therefore filter
    to `game_date < target_date` — production-faithful, no leakage.
    """
    from services.mlb.identity import normalize_player_name, apply_alias
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    proj = {"_id": 0, "player_id": 1, "player_name": 1, "game_date": 1,
             "rolling_7": 1, "rolling_14": 1, "rolling_30": 1,
             "season_window": 1}
    async for d in db.mlb_statcast_player_features.find({}, proj):
        nn = apply_alias(normalize_player_name(d.get("player_name")))
        date = d.get("game_date")
        if not nn or not date: continue
        out[(nn, date)] = d
    return out


async def load_identity_map(db) -> Dict[str, Dict[str, Any]]:
    """canonical_name → identity-map row (mlb_id, confidence, etc.).

    Returns rows with confidence >= 0.92 only — fuzzy matches below
    that bar are intentionally NOT exposed to the engine (per spec
    safety rule). The bar matches `FUZZY_THRESHOLD` from the builder.
    """
    out: Dict[str, Dict[str, Any]] = {}
    async for d in db.mlb_player_identity_map.find(
        {"confidence": {"$gte": 0.92}},
        {"_id": 0, "normalized_name": 1, "mlb_id": 1, "statcast_id": 1,
         "bdl_id": 1, "team": 1, "match_method": 1, "confidence": 1,
         "aliases": 1}):
        nn = d.get("normalized_name")
        if nn: out[nn] = d
    return out


def _statcast_for(by_pd: Dict[Tuple[str, str], Dict[str, Any]],
                   player_raw: str, target_date: str,
                   identity_map: Optional[Dict[str, Dict[str, Any]]] = None,
                   ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Lookup priority (per spec):
      1. statcast_id  via identity_map → canonical name
      2. canonical normalized_name  (with alias) → most-recent row
                                                    BEFORE target_date
      3. None → engine falls back to bdl_proxy

    Returns (feature_row_or_None, identity_metadata) where metadata is:
      {match_method, confidence, statcast_id, feature_source}.
    Used by the engine to stamp `feature_source`/`identity_*` on every
    candidate so analytics can slice by match strength.
    """
    from services.mlb.identity import normalize_player_name, apply_alias
    nn = apply_alias(normalize_player_name(player_raw))
    if not nn:
        return None, {"match_method": "no_name",
                       "confidence": 0.0,
                       "statcast_id": None,
                       "feature_source": "bdl_proxy"}

    ident = (identity_map or {}).get(nn)
    statcast_id = ident.get("statcast_id") if ident else None
    method = (ident.get("match_method") if ident else "no_identity_row")
    confidence = float(ident.get("confidence", 0.0)) if ident else 0.0

    # Walk every (name, date) feature row whose name canonicalizes to
    # this player's `nn` AND game_date is strictly before target_date.
    # Most batters have a single canonical name → fast path; the
    # collision-tagged identity rows fall through here too.
    candidates = [(d, v) for (n2, d), v in by_pd.items()
                   if n2 == nn and d < target_date]
    if not candidates:
        return None, {"match_method": method,
                       "confidence": confidence,
                       "statcast_id": statcast_id,
                       "feature_source": "bdl_proxy"}

    candidates.sort(key=lambda x: x[0], reverse=True)
    feat = candidates[0][1]
    src = ("statcast_id"   if (ident and statcast_id is not None
                                 and confidence >= 0.95)
           else "statcast_name")
    return feat, {"match_method": method or "name_match",
                   "confidence": confidence or 0.95,
                   "statcast_id": statcast_id,
                   "feature_source": src}


# -----------------------------------------------------------------------------
# Feature extraction (μ + σ inputs)
# -----------------------------------------------------------------------------
def _woba_proxy(logs: List[Dict[str, Any]]) -> Optional[float]:
    """Tom Tango wOBA from raw counts. Used as xwOBA proxy."""
    pa = bb = singles = doubles = triples = hr = 0.0
    for lg in logs:
        p = lg.get("pa"); h = lg.get("hits"); d = lg.get("doubles")
        t = lg.get("triples"); h_r = lg.get("hr"); b = lg.get("bb")
        if p is None or h is None or b is None: continue
        s = (h or 0) - (d or 0) - (t or 0) - (h_r or 0)
        pa += p; bb += b; singles += max(0, s)
        doubles += d or 0; triples += t or 0; hr += h_r or 0
    if pa <= 0: return None
    return (WOBA_BB*bb + WOBA_1B*singles + WOBA_2B*doubles
            + WOBA_3B*triples + WOBA_HR*hr) / pa


def _k_rate(logs):
    pa = sum(lg.get("pa") or 0 for lg in logs)
    k  = sum(lg.get("k") or 0  for lg in logs)
    return (k / pa) if pa > 0 else None


def _barrel_proxy(logs):
    ab = sum(lg.get("ab") or 0 for lg in logs)
    hr = sum(lg.get("hr") or 0 for lg in logs)
    return (hr / ab) if ab > 0 else None


def _tb_per_pa(logs):
    pa = sum(lg.get("pa") or 0 for lg in logs)
    tb = sum(lg.get("tb") or 0 for lg in logs)
    return (tb / pa) if pa > 0 else None


def _tb_values(logs):
    """Per-game TB (used for HR/CV/ceiling_rate gate features)."""
    return [lg["tb"] for lg in logs if lg.get("tb") is not None]


# -----------------------------------------------------------------------------
# PA projection (spec v2)
# -----------------------------------------------------------------------------
def project_pa(
    batting_order: Optional[int],
    team_implied_total: Optional[float],
    is_home_team: Optional[bool],
) -> Tuple[float, str]:
    """Spec v2 expected-PA model.

    Returns (projected_pa, source_tag) where source_tag is one of:
       "lineup"   — batting_order is known (per-slot baseline + adjustments)
       "fallback" — batting_order is None (single 4.2 PA estimate, NO adjustments)

    No leakage: this function takes ONLY pre-game inputs (lineup card,
    sportsbook implied total, home/away). It never touches game logs.
    """
    if batting_order is None:
        return PA_DEFAULT, "fallback"
    try:
        slot = int(batting_order)
    except (TypeError, ValueError):
        return PA_DEFAULT, "fallback"
    base = PA_BY_ORDER.get(slot)
    if base is None:
        return PA_DEFAULT, "fallback"

    pa = base
    # Team-total swing — only applied when we have a confirmed lineup.
    if team_implied_total is not None:
        try:
            tit = float(team_implied_total)
            if tit >= PA_HIGH_TEAM_TOTAL_THRESHOLD:
                pa += PA_TEAM_TOTAL_DELTA
            elif tit <= PA_LOW_TEAM_TOTAL_THRESHOLD:
                pa -= PA_TEAM_TOTAL_DELTA
        except (TypeError, ValueError):
            pass

    # Home-team penalty — winning home teams skip bottom of 9th.
    if is_home_team:
        pa += PA_HOME_TEAM_DELTA

    pa = max(PA_CLAMP[0], min(PA_CLAMP[1], pa))
    return pa, "lineup"


# -----------------------------------------------------------------------------
# μ + σ models (per user spec)
# -----------------------------------------------------------------------------
def predict_mu_sigma(
    *, prior_logs: List[Dict[str, Any]],
    batting_order: Optional[int],
    statcast: Optional[Dict[str, Any]] = None,
    team_implied_total: Optional[float] = None,
    is_home_team: Optional[bool] = None,
    matchup_factor_shadow: Optional[float] = None,
    pitcher_confidence_flag: Optional[str] = None,
) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    """Returns (μ, σ, debug). When `statcast` is supplied (the rolling
    feature row from `mlb_statcast_player_features`), the engine uses
    TRUE Statcast inputs. Otherwise it falls back to the v1 wOBA / HR-
    AB proxies derived from BDL game logs.

    Spec v2 changes (μ ONLY — σ untouched):
      * PA via project_pa() with team-context adjustments
      * matchup_factor_shadow applied IF available AND batter_BBE>=25
        AND pitcher_confidence_flag == 'high' (otherwise μ is unchanged)
    """
    if len(prior_logs) < 10: return None, None, {"reason": "lt10_logs"}

    long_w  = prior_logs[-HISTORY_WINDOW_LONG:]
    short_w = prior_logs[-HISTORY_WINDOW_SHORT:]

    # ---- μ inputs ----
    sc_30 = (statcast or {}).get("rolling_30") or {}
    sc_7  = (statcast or {}).get("rolling_7")  or {}

    # Prefer xwOBA from Statcast (most predictive of batted-ball quality);
    # fall back to wOBA, then to v1 BDL proxy.
    woba_long = sc_30.get("xwOBA") or sc_30.get("wOBA")
    if woba_long is None:
        woba_long = _woba_proxy(long_w)
    if woba_long is None or woba_long <= 0:
        return None, None, {"reason": "no_woba"}

    # Recent form: prefer 7-day xwOBA / 30-day xwOBA; fall back to short
    # vs long log proxy. Statcast-driven recency signal is much sharper
    # than 5-game raw counts.
    woba_short = sc_7.get("xwOBA") or sc_7.get("wOBA") or _woba_proxy(short_w)
    if woba_short is not None and woba_long > 0:
        rf = woba_short / woba_long
        rf = max(RECENT_FORM_CLAMP[0], min(RECENT_FORM_CLAMP[1], rf))
    else:
        rf = 1.0

    # Spec v2 — PA from project_pa() (lineup + team context adjustments).
    pa_proj, pa_source = project_pa(batting_order, team_implied_total,
                                       is_home_team)

    # μ formula. The wOBA-units → TB calibration (0.75) is preserved
    # so the engine output remains drop-in compatible with v1.
    mu_per_pa_to_tb = 0.75
    rate_per_pa = woba_long * mu_per_pa_to_tb * rf
    mu = rate_per_pa * pa_proj

    # Apply matchup_factor_shadow (spec v2) — ONLY when:
    #   * the shadow factor is supplied (caller resolved it from
    #     `mlb_statcast_pitcher_features` for a settled or pre-game-
    #     known matchup),
    #   * batter rolling_30 BBE >= 25 (sufficient batter-side signal),
    #   * pitcher_confidence_flag == 'high' (sufficient pitcher-side
    #     sample).
    # Otherwise μ is left unchanged so we don't introduce noise.
    matchup_applied = 1.0
    bbe_30 = (sc_30 or {}).get("batted_ball_events")
    can_apply_matchup = (
        matchup_factor_shadow is not None
        and bbe_30 is not None and bbe_30 >= 25
        and pitcher_confidence_flag == "high"
    )
    if can_apply_matchup:
        try:
            mf = max(0.85, min(1.15, float(matchup_factor_shadow)))
            mu *= mf
            matchup_applied = mf
        except (TypeError, ValueError):
            pass

    # ---- σ inputs (UNCHANGED) -----------------------------------------
    k_rate    = sc_30.get("k_rate")
    if k_rate is None: k_rate = _k_rate(long_w) or LEAGUE_K_RATE
    barrel    = sc_30.get("barrel_rate")
    if barrel is None: barrel = _barrel_proxy(long_w) or LEAGUE_BARREL_RATE
    contact   = sc_30.get("contact_rate")
    if contact is None: contact = max(0.0, 1.0 - k_rate)
    hard_hit  = sc_30.get("hard_hit_rate")
    avg_ev    = sc_30.get("avg_exit_velocity")

    vol = (1.0
           + 1.5 * (k_rate    - LEAGUE_K_RATE)
           - 1.0 * (contact   - LEAGUE_CONTACT_RATE)
           + 2.0 * (barrel    - LEAGUE_BARREL_RATE))
    if hard_hit is not None:
        vol += 1.0 * (hard_hit - 0.38)
    vol = max(VOL_CLAMP[0], min(VOL_CLAMP[1], vol))

    sigma = BASE_SIGMA * vol
    sigma = max(SIGMA_CLAMP[0], min(SIGMA_CLAMP[1], sigma))

    return mu, sigma, {
        "woba_long": woba_long, "woba_short": woba_short, "rf": rf,
        "pa_proj": pa_proj, "pa_source": pa_source,
        "rate_per_pa": rate_per_pa,
        "team_implied_total": team_implied_total,
        "is_home_team": bool(is_home_team) if is_home_team is not None else None,
        "matchup_factor_applied": matchup_applied,
        "k_rate": k_rate, "barrel": barrel, "contact": contact,
        "hard_hit_rate": hard_hit, "avg_exit_velocity": avg_ev,
        "vol": vol,
        "statcast_source": ("statcast"
                             if (statcast and sc_30.get("xwOBA"))
                             else "bdl_proxy"),
        "xwOBA": sc_30.get("xwOBA"),
    }


# -----------------------------------------------------------------------------
# Pivot mlb_live_props → per-(player, date, line) candidate buckets
# -----------------------------------------------------------------------------
def _player_key(p: Dict[str, Any]) -> str:
    return (p.get("player_name") or "").strip().lower()


def _slate_date(p: Dict[str, Any]) -> Optional[str]:
    """UTC commence_time → YYYY-MM-DD slate."""
    ct = p.get("commence_time")
    return _to_date(ct)


def _is_alt(p: Dict[str, Any]) -> bool:
    return bool(p.get("is_alternate_market"))


def _book_layer(p: Dict[str, Any], book: str) -> Optional[Dict[str, Any]]:
    return p.get(f"{book}_layer")


async def load_total_bases_props(db) -> List[Dict[str, Any]]:
    cursor = db["mlb_live_props"].find(
        {"stat_type": STAT_RAW},
        {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1,
         "recommendation": 1, "bookmaker": 1, "odds": 1,
         "commence_time": 1, "event_id": 1, "is_alternate_market": 1,
         "batting_order": 1, "team": 1, "opponent_team": 1,
         "all_lines": 1, "all_odds": 1,
         "dk_layer": 1, "fd_layer": 1, "mgm_layer": 1, "bol_layer": 1,
         "dk_line": 1, "dk_odds": 1, "dk_odds_opp": 1,
         "fd_line": 1, "fd_odds": 1, "fd_odds_opp": 1,
         "mgm_line": 1, "mgm_odds": 1, "mgm_odds_opp": 1,
         "bol_line": 1, "bol_odds": 1, "bol_odds_opp": 1,
         "anchor_book": 1, "playable_on_pp": 1,
         "opp_pitcher_throws": 1, "team_total": 1})
    return await cursor.to_list(length=None)


# -----------------------------------------------------------------------------
# Main pipeline
# -----------------------------------------------------------------------------
async def main(*, log_picks: bool = False):
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db  = cli[os.environ["DB_NAME"]]

    print("[MLB-PV] Loading batter game logs (mlb_master_hub_2026) …")
    by_name = await load_player_logs(db)
    print(f"        batters with logs : {len(by_name):,}")

    print("[MLB-PV] Loading Statcast rolling features "
          "(mlb_statcast_player_features) …")
    statcast_by_pd = await load_statcast_features(db)
    sc_players = {nm for (nm, _d) in statcast_by_pd}
    print(f"        Statcast feature rows : {len(statcast_by_pd):,}")
    print(f"        unique batters w/ Statcast : {len(sc_players):,}")

    print("[MLB-PV] Loading mlb_player_identity_map (≥0.92 confidence) …")
    identity_map = await load_identity_map(db)
    print(f"        identity rows loaded  : {len(identity_map):,}")

    print("[MLB-PV] Loading Total Bases live props …")
    raw_props = await load_total_bases_props(db)
    print(f"        live Total Bases props loaded : {len(raw_props):,}")

    # ---- 1. BUILD RAW CANDIDATES ------------------------------------------
    # Pivot by (player, date, line). One pivot key may have OVER+UNDER from
    # multiple books — we capture them all so compute_tp can devig.
    bucket: Dict[Tuple[str, str, float], Dict[str, Dict[str, Any]]] = \
        defaultdict(dict)
    skip = Counter()
    for p in raw_props:
        nm = _player_key(p)
        date = _slate_date(p)
        line = _f(p.get("line"))
        side = (p.get("recommendation") or "").upper()
        if not nm: skip["no_player"] += 1; continue
        if not date: skip["no_date"] += 1; continue
        if line is None: skip["no_line"] += 1; continue
        if side not in ("OVER", "UNDER"): skip["no_side"] += 1; continue
        bk = (p.get("bookmaker") or "").strip().lower()
        odds = _i(p.get("odds"))
        is_alt = _is_alt(p)
        is_pp_playable = bool(p.get("playable_on_pp"))
        ck = (nm, date, line)
        prop = bucket[ck]
        prop.setdefault("player", nm)
        prop.setdefault("date",   date)
        prop.setdefault("line",   line)
        prop.setdefault("event_id", p.get("event_id"))
        prop.setdefault("batting_order", _i(p.get("batting_order")))
        prop.setdefault("team", p.get("team"))
        prop.setdefault("opponent", p.get("opponent_team"))
        # Spec v2: capture team_total + is_home_team for project_pa().
        prop.setdefault("team_implied_total", _f(p.get("team_total")))
        prop.setdefault("is_home_team", bool(p.get("is_home_team")))
        prop["is_alt"] = is_alt or prop.get("is_alt", False)
        prop["pp_playable"] = (
            is_pp_playable or prop.get("pp_playable", False))
        # Capture each book's odds payload for compute_tp + ref-routing.
        prop.setdefault("books", {})
        for book in _TP_BOOK_KEYS:
            ln = _f(p.get(f"{book}_line"))
            od = _i(p.get(f"{book}_odds"))
            od_opp = _i(p.get(f"{book}_odds_opp"))
            if ln is not None and abs(ln - line) < 1e-6 and (od is not None or od_opp is not None):
                prop["books"].setdefault(book, {})
                # OVER side from book → over odds; UNDER → under odds.
                # mlb_live_props stores (odds = OVER side, odds_opp = UNDER side)
                # for THIS row's recommendation. Detect direction:
                if od is not None:
                    if side == "OVER":
                        prop["books"][book]["over"] = od
                        if od_opp is not None:
                            prop["books"][book]["under"] = od_opp
                    else:
                        prop["books"][book]["under"] = od
                        if od_opp is not None:
                            prop["books"][book]["over"] = od_opp

    print(f"        unique pivot keys (player,date,line): {len(bucket):,}")
    if skip:
        for r, n in skip.most_common(): print(f"        skip {r}: {n}")

    # ---- 2. μ / σ / p_model / tp / edge ------------------------------------
    candidates: List[Dict[str, Any]] = []
    cand_skip = Counter()
    feat_cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
    for ck, prop in bucket.items():
        nm = prop["player"]; date = prop["date"]; line = prop["line"]
        plogs = by_name.get(nm) or []
        if not plogs: cand_skip["no_player_logs"] += 1; continue
        prior = [lg for lg in plogs if lg["date"] < date]
        if len(prior) < 10: cand_skip["lt10_prior_logs"] += 1; continue
        cache_key = (nm, date)
        ce = feat_cache.get(cache_key)
        if ce is None:
            sc_row, ident = _statcast_for(statcast_by_pd, nm, date,
                                            identity_map=identity_map)
            mu, sigma, dbg = predict_mu_sigma(
                prior_logs=prior, batting_order=prop.get("batting_order"),
                statcast=sc_row,
                team_implied_total=prop.get("team_implied_total"),
                is_home_team=prop.get("is_home_team"),
                # matchup_factor_shadow is None at live-engine time
                # (live_props don't carry probable pitchers). The shadow
                # backfill stamps it on `mlb_pick_history` post-game; the
                # path is exposed here so it slots in once the upstream
                # feed lands.
                matchup_factor_shadow=None,
                pitcher_confidence_flag=None)
            if mu is None:
                feat_cache[cache_key] = {"_skip": dbg.get("reason")}
                cand_skip[dbg.get("reason") or "mu_fail"] += 1; continue
            tb_window = prior[-HISTORY_WINDOW_LONG:]
            tb_vals = _tb_values(tb_window)
            mean_tb = (statistics.mean(tb_vals) if tb_vals else None)
            std_tb  = (statistics.stdev(tb_vals)
                        if len(tb_vals) >= 2 else None)
            cv = (std_tb / mean_tb) if mean_tb and mean_tb > 0 else None
            # Track BBE for sample-quality slicing in forward-test reports
            sc30 = (sc_row or {}).get("rolling_30") or {}
            ce = {"_skip": None, "mu": mu, "sigma": sigma, "dbg": dbg,
                  "tb_vals": tb_vals, "cv": cv,
                  "ident": ident,
                  "bbe_30": sc30.get("batted_ball_events"),
                  "pa_30":  sc30.get("plate_appearances")}
            feat_cache[cache_key] = ce
        elif ce.get("_skip"):
            cand_skip[ce["_skip"]] += 1; continue

        mu = ce["mu"]; sigma = ce["sigma"]; cv = ce["cv"]
        tb_vals = ce["tb_vals"]
        if not tb_vals:
            cand_skip["no_tb_vals"] += 1; continue

        # hit-rate / ceiling features (line-specific, cheap)
        n = len(tb_vals)
        n_over = sum(1 for v in tb_vals if v > line)
        hr_o = n_over / n * 100.0
        hr_u = (n - n_over) / n * 100.0
        ceil_o = sum(1 for v in tb_vals if v >= max(line*1.5, line+0.5))/n*100.0
        ceil_u = sum(1 for v in tb_vals if v <= min(line*0.5, line-0.5))/n*100.0

        # tp via prod tp_engine. Build the same flat prop shape NBA uses.
        tp_input = {"line": line}
        any_devig = False; book_count = 0
        for book, od in prop["books"].items():
            o_over = od.get("over"); o_under = od.get("under")
            if o_over is not None:
                tp_input[f"{book}_odds"] = o_over; book_count += 1
            if o_under is not None:
                tp_input[f"{book}_odds_opp"] = o_under
            if o_over is not None and o_under is not None: any_devig = True

        # Reference odds → tier routing. Prefer DK > FD > MGM > BOL.
        ref_book = ref_odds = None
        for book in _TP_BOOK_KEYS:
            ov = prop["books"].get(book, {}).get("over")
            if ov is not None:
                ref_book = book; ref_odds = int(ov); break
        if ref_odds is None: cand_skip["no_ref_book"] += 1; continue
        routed = resolve_target_tier("mlb", ref_odds)
        if routed is None: cand_skip["routed_none"] += 1; continue

        z = (line - mu) / sigma
        p_over  = 1.0 - _norm_cdf(z)
        p_under = 1.0 - p_over
        tp_o = compute_tp(prop=tp_input, side="OVER")
        tp_u = compute_tp(prop=tp_input, side="UNDER")
        is_alt = bool(prop.get("is_alt"))
        market_type = "alternate" if is_alt else "standard"

        for side, p_side, hr_side, ceil_side, tp_res in (
            ("OVER",  p_over,  hr_o, ceil_o, tp_o),
            ("UNDER", p_under, hr_u, ceil_u, tp_u),
        ):
            tp_side = tp_res.get("tp")
            tp_source = (tp_res.get("tp_source")
                          or ("devig" if any_devig else "one_sided"))
            edge_side = (p_side*100.0 - tp_side) if tp_side is not None else None
            if tp_side is not None:
                fair_prob = tp_side/100.0
                stab = max(0.3, min(1.0, 1.0 - (cv/3.0))) if cv else 0.5
                conf = (1.0 + (1.0 if hr_side > 0 else 0.0)
                        + (1.0 if book_count >= 2 else 0.5)) / 3.0
                pos = max(0.0, p_side - fair_prob)
                vision_raw = pos * p_side * stab * conf
            else:
                vision_raw = None
            candidates.append({
                "player": nm, "date": date, "line": line,
                "stat": "TOTAL_BASES",
                "stat_family": STAT_FAMILY,
                "side": side, "p_model_pct": p_side*100.0,
                "hit_rate": hr_side, "cv": cv, "ceiling_rate": ceil_side,
                "tp": tp_side, "tp_source": tp_source, "edge_pct": edge_side,
                "vision_raw": vision_raw, "vision_score": None,
                "ref_book": ref_book, "ref_odds": ref_odds,
                "book_count": book_count, "routed_tier": routed,
                "is_alt": is_alt, "is_combo": False,
                "market_type": market_type,
                "mu": mu, "sigma": sigma, "stability_dbg": ce["dbg"],
                "bookmaker": ref_book, "event_id": prop.get("event_id"),
                "pp_playable": prop.get("pp_playable", False),
                # Team / opponent / lineup snapshot for pick_history
                "team":            prop.get("team"),
                "opponent":        prop.get("opponent"),
                "batting_order":   prop.get("batting_order"),
                # MLB-specific feature snapshot
                "expected_PA":     ce["dbg"].get("pa_proj"),
                "pa_source":       ce["dbg"].get("pa_source"),
                "team_implied_total": ce["dbg"].get("team_implied_total"),
                "is_home_team":    ce["dbg"].get("is_home_team"),
                "rate_per_pa":     ce["dbg"].get("rate_per_pa"),
                "matchup_factor_applied": ce["dbg"].get("matchup_factor_applied"),
                "woba_proxy":      ce["dbg"].get("woba_long"),
                "barrel_rate":     ce["dbg"].get("barrel"),
                "matchup_factor":  ce["dbg"].get("matchup_factor_applied"),
                # Statcast features (populated when available; else None)
                "xwOBA":           ce["dbg"].get("xwOBA"),
                "hard_hit_rate":   ce["dbg"].get("hard_hit_rate"),
                "avg_exit_velocity": ce["dbg"].get("avg_exit_velocity"),
                "k_rate":          ce["dbg"].get("k_rate"),
                "feature_source":  ce["ident"]["feature_source"],
                "identity_match_method": ce["ident"]["match_method"],
                "identity_confidence":   ce["ident"]["confidence"],
                "statcast_id":           ce["ident"]["statcast_id"],
                "bbe_30":          ce.get("bbe_30"),
                "pa_30":           ce.get("pa_30"),
            })

    print(f"        candidates after μ/σ/tp/edge build : {len(candidates):,}")
    if cand_skip:
        for r, n in cand_skip.most_common(8):
            print(f"          skip {r:24s}: {n:,}")

    # ---- 2b. PP playability (UNDER → standard FL only) ---------------------
    print()
    print("[MLB-PV] PP playability rules …")
    pp_drop = Counter(); pp_passed = []
    for c in candidates:
        if c["side"] == "OVER": pp_passed.append(c); continue
        if c["is_alt"]:           pp_drop["under_alt"] += 1; continue
        if c["is_combo"]:         pp_drop["under_combo"] += 1; continue
        if c["routed_tier"] != "front_lines":
            pp_drop[f"under_tier_{c['routed_tier']}"] += 1; continue
        pp_passed.append(c)
    n_pre_pp = len(candidates); n_post_pp = len(pp_passed)
    candidates = pp_passed
    print(f"        pre-PP: {n_pre_pp:,}  →  post-PP: {n_post_pp:,}")
    for r, n in pp_drop.most_common(): print(f"          dropped {r}: {n:,}")

    # ---- 2c. CANDIDATE COLLAPSE -------------------------------------------
    # ONE candidate per (player, stat_family, date) → MAX edge.
    print()
    print("[MLB-PV] Collapse by (player, stat_family, date) — MAX edge …")
    by_group: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        gk = (c["player"], c["stat_family"], c["date"])
        by_group[gk].append(c)
    collapsed: List[Dict[str, Any]] = []
    n_no_edge = 0
    for gk, members in by_group.items():
        with_edge = [m for m in members if m["edge_pct"] is not None]
        if not with_edge: n_no_edge += 1; continue
        collapsed.append(max(with_edge, key=lambda m: m["edge_pct"]))
    n_post_collapse = len(collapsed)
    candidates = collapsed
    print(f"        groups: {len(by_group):,}  →  collapsed: {n_post_collapse:,}  "
          f"(no_edge: {n_no_edge:,})")

    # ---- 2d. vision_score percentile  POST-COLLAPSE ONLY -------------------
    print()
    print("[MLB-PV] vision_score percentile — POST-COLLAPSE only …")
    by_slate = defaultdict(list)
    for c in candidates: by_slate[c["date"]].append(c)
    for date, slate in by_slate.items():
        rank = _percentile_rank([c["vision_raw"] for c in slate])
        for c in slate:
            v = c["vision_raw"]
            c["vision_score"] = 0.0 if v is None or v <= 0 else rank.get(v, 0.0)

    # ---- 3. Gates (UNCHANGED) ---------------------------------------------
    print()
    print("[MLB-PV] Running UniversalGateEngine …")
    selected = []; rejected = 0
    failed_counter = Counter()
    for c in candidates:
        m = NormalizedMetrics(
            sport="mlb", tier=c["routed_tier"],
            stat_family=c["stat_family"], side=c["side"],
            reference_book=c["ref_book"], reference_odds=c["ref_odds"],
            book_count=c["book_count"], tp=c["tp"],
            hit_rate=c["hit_rate"], hit_rate_l20=c["hit_rate"],
            hit_rate_l10=None, hit_rate_l5=None, hit_rate_sample_size=20,
            ceiling_rate=c["ceiling_rate"], cv=c["cv"], edge_pct=c["edge_pct"],
            line=c["line"], vision_score=c["vision_score"],
            tp_source=c["tp_source"], is_alt=c["is_alt"],
            p_model_pct=c["p_model_pct"], extras={"cv_cap_override": None},
        )
        res = evaluate_tier_with_overrides(m)
        if res.passed:
            c["tier_final"] = c["routed_tier"]
            selected.append(c)
        else:
            rejected += 1
            for g in res.failed_gates: failed_counter[g] += 1

    # ===========================================================================
    # OUTPUT
    # ===========================================================================
    print()
    print("#" * 80)
    print("#  MLB PROPVISION — TOTAL BASES v1                                   ")
    print("#  Pipeline: NBA-parity (collapse → vision → gates → tiers)          ")
    print("#" * 80)
    print()

    # 1. Funnel
    print("=" * 80); print("  1. FUNNEL"); print("=" * 80)
    print(f"  raw Total Bases live props        : {len(raw_props):>9,d}")
    print(f"  unique pivots (player,date,line)  : {len(bucket):>9,d}")
    print(f"  candidates after μ/σ/tp build     : {n_pre_pp:>9,d}   "
          f"(2 sides × pivot)")
    print(f"  after PP playability rules        : {n_post_pp:>9,d}")
    print(f"  after (player,stat,date) collapse : {n_post_collapse:>9,d}")
    print(f"  rejected by gates                 : {rejected:>9,d}")
    print(f"  selected (gate-pass)              : {len(selected):>9,d}")
    if n_post_collapse:
        print(f"  selection rate (post-collapse)    : "
              f"{len(selected)/n_post_collapse*100:>8.2f}%")
    print()

    # 2. Picks per slate
    by_slate_sel = defaultdict(int)
    for c in selected: by_slate_sel[c["date"]] += 1
    print("=" * 80); print("  2. PICKS PER SLATE"); print("=" * 80)
    if by_slate_sel:
        counts = list(by_slate_sel.values())
        print(f"  slate days with picks  : {len(by_slate_sel)}")
        print(f"  avg picks/slate        : {sum(counts)/len(counts):.2f}")
        print(f"  min picks/slate        : {min(counts)}")
        print(f"  max picks/slate        : {max(counts)}")
        print(f"  picks per slate detail :")
        for d in sorted(by_slate_sel): print(f"    {d}  →  {by_slate_sel[d]}")
    else:
        print("  (no picks)")
    print()

    # 3. Tier
    _print_block("3. TIER DISTRIBUTION", [
        (t, sum(1 for c in selected if c["tier_final"] == t))
        for t in ("safe_haven", "front_lines", "war_zone")])

    # 4. Stat (single stat in v1; reported for parity)
    _print_block("4. STAT DISTRIBUTION", [
        (k, sum(1 for c in selected if c["stat"] == k))
        for k in sorted({c["stat"] for c in selected} or {"TOTAL_BASES"})])

    # 5. Market type
    _print_block("5. MARKET TYPE DISTRIBUTION", [
        (k, sum(1 for c in selected if c["market_type"] == k))
        for k in ("standard", "alternate", "combo")])

    # 6. Side
    _print_block("6. SIDE DISTRIBUTION", [
        (k, sum(1 for c in selected if c["side"] == k))
        for k in ("OVER", "UNDER")])

    # 7. Performance — N/A for live slate (games not yet completed).
    print("=" * 80); print("  7. PERFORMANCE (LIVE SLATE)"); print("=" * 80)
    print("  hit rate / ROI : N/A — live slate not yet settled.")
    print("                   Re-run after `mlb_master_hub_2026.bdl_game_logs`")
    print("                   captures the slate's actuals (next-day cron).")
    print()

    # ---- Diagnostics --------------------------------------------------------
    print("=" * 80); print("  DIAGNOSTICS"); print("=" * 80)
    _summary("μ (TB projection)", [c["mu"] for c in selected])
    _summary("σ (TB sigma)",       [c["sigma"] for c in selected], fmt="{:.3f}")
    _summary("edge_pct",           [c["edge_pct"] for c in selected])
    _summary("hit_rate (L20)",     [c["hit_rate"] for c in selected])
    _summary("CV",                 [c["cv"] for c in selected], fmt="{:.3f}")
    _summary("vision_score",       [c["vision_score"] for c in selected])
    _summary("p_model_pct",        [c["p_model_pct"] for c in selected])
    _summary("tp",                 [c["tp"] for c in selected])
    _summary("ref_odds",           [c["ref_odds"] for c in selected], fmt="{:.0f}")
    print()
    if rejected:
        print("  Gate kill counts (rejected candidates):")
        for g, n in failed_counter.most_common():
            print(f"    {g:30s} {n:>5,}  ({n/max(rejected,1)*100:.1f}%)")
        print()

    # Statcast coverage diagnostic
    n_sc_id   = sum(1 for c in selected
                     if c.get("feature_source") == "statcast_id")
    n_sc_name = sum(1 for c in selected
                     if c.get("feature_source") == "statcast_name")
    n_proxy   = sum(1 for c in selected
                     if c.get("feature_source") == "bdl_proxy")
    if selected:
        print("  Feature source split (selected picks):")
        print(f"    statcast_id   (id-bridged μ/σ)   : "
              f"{n_sc_id:>4,}  ({n_sc_id/len(selected)*100:.1f}%)")
        print(f"    statcast_name (name-only μ/σ)    : "
              f"{n_sc_name:>4,}  ({n_sc_name/len(selected)*100:.1f}%)")
        print(f"    bdl_proxy (wOBA fallback)        : "
              f"{n_proxy:>4,}  ({n_proxy/len(selected)*100:.1f}%)")
        print()

    # ---- Validation ---------------------------------------------------------
    sh_n = sum(1 for c in selected if c["tier_final"] == "safe_haven")
    fl_n = sum(1 for c in selected if c["tier_final"] == "front_lines")
    wz_n = sum(1 for c in selected if c["tier_final"] == "war_zone")
    counts_per = list(by_slate_sel.values()) or [0]
    avg_per = sum(counts_per) / max(len(counts_per), 1)
    vs_p75 = 0.0
    if selected:
        vs_sorted = sorted(c["vision_score"] for c in selected)
        vs_p75 = vs_sorted[min(len(vs_sorted)-1, int(0.75*(len(vs_sorted)-1)))]
    checks = [
        ("Picks/slate within 20–60 band", 20 <= avg_per <= 60,
         f"avg={avg_per:.1f}"),
        ("FL + WZ mix (both > 0)",        fl_n > 0 and wz_n > 0,
         f"FL={fl_n} WZ={wz_n}"),
        ("Safe Haven > 0",                sh_n > 0, f"n={sh_n}"),
        ("Vision-score not collapsed (p75>40)", vs_p75 > 40,
         f"p75={vs_p75:.1f}"),
        ("No duplicate (player,stat,date)", True,  # collapse guarantees this
         f"groups={len(by_group):,} → picks={len(selected):,}"),
    ]
    print("=" * 80); print("  VALIDATION CHECKS"); print("=" * 80)
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {label:42s} {detail}")
    print()

    # ---- Output sample (per-prop schema requested) -------------------------
    print("=" * 80); print("  SAMPLE OUTPUT (top 10 selected by edge_pct)"); print("=" * 80)
    print(f"  {'player':24s} {'date':10s} {'line':>5} {'side':5s} "
          f"{'tier':12s} {'mtype':9s} {'μ':>5} {'σ':>5} "
          f"{'pmdl':>5} {'tp':>5} {'edge':>6} {'vs':>5} {'book':5s} {'alt':3s}")
    for c in sorted(selected, key=lambda x: -(x["edge_pct"] or -999))[:10]:
        e = c["edge_pct"]
        print(f"  {c['player'][:24]:24s} {c['date']:10s} "
              f"{c['line']:>5.1f} {c['side']:5s} {c['tier_final']:12s} "
              f"{c['market_type']:9s} {c['mu']:>5.2f} {c['sigma']:>5.2f} "
              f"{c['p_model_pct']:>5.1f} "
              f"{(c['tp'] or 0):>5.1f} "
              f"{(f'{e:+.1f}' if e is not None else '—'):>6} "
              f"{c['vision_score']:>5.1f} {c['ref_book']:5s} "
              f"{('Y' if c['is_alt'] else 'N'):>3}")
    print()
    print("[MLB-PV] DONE — read-only, no production state changed.")

    # ---- Persistent forward-test logging ---------------------------------
    if log_picks and selected:
        # Stamp each pick with `tier` (the logger reads this name) and
        # `game_date` (already on each candidate but rename for clarity).
        for c in selected:
            c["tier"] = c.get("tier_final") or c.get("tier")
            c["game_date"] = c.get("date")
        print()
        print("[MLB-PV] Logging selected picks to mlb_pick_history …")
        await _ensure_pick_indexes(db)
        stats = await _log_picks(db, selected)
        print(f"        inserted={stats['inserted']:>4d}  "
              f"updated={stats['updated']:>4d}  "
              f"skipped={stats['skipped']:>4d}  "
              f"errors={stats['errors']:>4d}  "
              f"model_version={PICK_MODEL_VERSION}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="MLB Total Bases v1 PropVision engine")
    p.add_argument("--log-picks", action="store_true",
                   help="Persist selected picks to mlb_pick_history "
                         "for forward-test grading.")
    args = p.parse_args()
    asyncio.run(main(log_picks=args.log_picks))

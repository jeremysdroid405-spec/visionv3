"""
build_team_features.py — Phase 2 Team Feature Builder.

CONTRACT
    Mirrors the player model feature builder (services/replay/engine.py
    ::build_as_of_features) but at the TEAM level. For every (sport,
    team_id, as_of_date) tuple where the team has at least one
    `team_historical_outcomes` row, computes a `TeamAsOfFeatures`
    record and upserts it into `team_model_features`.

INPUTS
    Source:      `team_historical_outcomes` (Phase 1 output).
    Pre-req:     run `build_team_historical_outcomes` for the sport
                 first. Without resolved outcomes, this script will
                 still run but every feature row will carry
                 sample_size=0 / mu=None.

OUTPUT
    `team_model_features` documents keyed by
    (sport, team_id, as_of_date). One row per team per date.

LEAKAGE GUARANTEE
    Every feature is computed STRICTLY from games before
    `as_of_date` (date < as_of_date). The orchestrator validates this
    on a sample at the end of each run. See `assert_no_future_games`.

FEATURE SET (Phase 2A — rolling priors)
    Mirroring the player feature set verbatim where applicable:
        - sample_size              (games played before as_of_date)
        - mu_points_scored         (μ over season)
        - sigma_points_scored      (σ over season)
        - cv_points_scored         (σ / μ)
    Team-specific extensions:
        - win_rate_l5 / l10 / season
        - avg_scored_l5 / l10 / season
        - avg_allowed_l5 / l10 / season
        - spread_cover_rate_l10
        - ou_hit_rate_l10          (over rate on game_total markets)
        - home_win_rate / away_win_rate
        - rest_days
        - tempo_l10                (NBA: (scored+allowed)/2 L10;
                                    MLB: runs/game L10)
        - run_trend_l10            (MLB only: L10 runs vs season runs)
    Tag every row `feature_completeness="team_v1_priors"`.

USAGE
    # MLB dry-run (computes & prints summary; no writes)
    python -m scripts.sgo.build_team_features --sport mlb --dry-run

    # MLB live (writes to team_model_features)
    python -m scripts.sgo.build_team_features --sport mlb

    # All-sport in sequence
    python -m scripts.sgo.build_team_features --sport all
"""
from __future__ import annotations
import argparse
import asyncio
import math
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import pymongo

FEATURE_VERSION = "team_v1_priors"
SRC_COLL = "team_historical_outcomes"
DST_COLL = "team_model_features"
BDL_GAME_FEATURES_COLL = "bdl_mlb_team_game_features"
NBA_BDL_GAME_FEATURES_COLL = "bdl_nba_team_game_features"

SUPPORTED_SPORTS = ("mlb", "nba", "nfl")


# ───── data shape (mirrors AsOfFeatures from services/replay/engine.py) ─────
@dataclass
class TeamGameRecord:
    """One unique (team_id, event_id) game row. Built by aggregating
    multiple outcome rows for the same game into one canonical record.
    Pure data — emitted by `aggregate_team_games`."""
    event_id:        str
    game_date:       str           # 'YYYY-MM-DD'
    is_home:         bool
    points_scored:   Optional[float]
    points_allowed:  Optional[float]
    won_h2h:         Optional[bool]
    # Per-market hits / lines collected across all books for this game:
    spread_outcomes: List[Tuple[Optional[float], Optional[bool]]] = field(default_factory=list)
    ou_outcomes:     List[Optional[bool]] = field(default_factory=list)


@dataclass
class TeamAsOfFeatures:
    """Phase 2 rolling-priors feature snapshot for one (team, as_of)."""
    sample_size:           int
    mu_points_scored:      Optional[float]
    sigma_points_scored:   Optional[float]
    cv_points_scored:      Optional[float]
    # Win rates
    win_rate_l5:           Optional[float]
    win_rate_l10:          Optional[float]
    win_rate_season:       Optional[float]
    # Scoring
    avg_scored_l5:         Optional[float]
    avg_scored_l10:        Optional[float]
    avg_scored_season:     Optional[float]
    avg_allowed_l5:        Optional[float]
    avg_allowed_l10:       Optional[float]
    avg_allowed_season:    Optional[float]
    # Market-specific hit rates
    spread_cover_rate_l10: Optional[float]
    ou_hit_rate_l10:       Optional[float]
    # Splits
    home_win_rate:         Optional[float]
    away_win_rate:         Optional[float]
    # Rest
    rest_days:             Optional[int]
    # Tempo / sport-specific
    tempo_l10:             Optional[float]  # NBA pace proxy; MLB runs/g
    run_trend_l10:         Optional[float]  # MLB only; None otherwise
    spread_cover_rate_l5:  Optional[float] = None
    ou_hit_rate_l5:        Optional[float] = None
    feature_completeness:  str = FEATURE_VERSION
    # MLB starting pitcher quality (rolling 14-day averages across rotation)
    sp_k_rate_avg:          Optional[float] = None
    sp_woba_allowed_avg:    Optional[float] = None
    sp_hard_hit_rate_avg:   Optional[float] = None
    sp_bb_rate_avg:         Optional[float] = None
    sp_xwoba_allowed_avg:   Optional[float] = None

    # BDL MLB features (from bdl_mlb_team_game_features; None for non-MLB sports)
    batting_avg:              Optional[float] = None
    batting_obp:              Optional[float] = None
    batting_slg:              Optional[float] = None
    batting_ops:              Optional[float] = None
    batting_k_rate:           Optional[float] = None
    batting_bb_rate:          Optional[float] = None
    batting_hr_rate:          Optional[float] = None
    pitching_era:             Optional[float] = None
    pitching_whip:            Optional[float] = None
    pitching_k_per_9:         Optional[float] = None
    pitching_bb_per_9:        Optional[float] = None
    pitching_hr_per_9:        Optional[float] = None
    pitching_qs_rate:         Optional[float] = None
    fielding_fp:              Optional[float] = None
    runs_scored_l5_bdl:       Optional[float] = None
    runs_scored_l10_bdl:      Optional[float] = None
    runs_scored_l20_bdl:      Optional[float] = None
    runs_allowed_l5_bdl:      Optional[float] = None
    runs_allowed_l10_bdl:     Optional[float] = None
    runs_allowed_l20_bdl:     Optional[float] = None
    first5_runs_l5:           Optional[float] = None
    first5_runs_l10:          Optional[float] = None
    first5_runs_l20:          Optional[float] = None
    first_inning_score_rate:  Optional[float] = None
    total_runs_l10_avg:       Optional[float] = None

    # Line-specific spread cover rates (L5 and L10 windows)
    spread_cover_neg1_5_l5:   Optional[float] = None
    spread_cover_neg1_5_l10:  Optional[float] = None
    spread_cover_1_5_l5:      Optional[float] = None
    spread_cover_1_5_l10:     Optional[float] = None
    spread_cover_neg2_5_l5:   Optional[float] = None
    spread_cover_neg2_5_l10:  Optional[float] = None
    spread_cover_2_5_l5:      Optional[float] = None
    spread_cover_2_5_l10:     Optional[float] = None
    spread_cover_neg3_5_l5:   Optional[float] = None
    spread_cover_neg3_5_l10:  Optional[float] = None
    spread_cover_3_5_l5:      Optional[float] = None
    spread_cover_3_5_l10:     Optional[float] = None

    # BDL NBA features (from bdl_nba_team_game_features; None for non-NBA sports)
    off_rating_l5:        Optional[float] = None
    off_rating_l10:       Optional[float] = None
    off_rating_l20:       Optional[float] = None
    def_rating_l5:        Optional[float] = None
    def_rating_l10:       Optional[float] = None
    def_rating_l20:       Optional[float] = None
    pace_l5:              Optional[float] = None
    pace_l10:             Optional[float] = None
    pace_l20:             Optional[float] = None
    ts_pct_l10:           Optional[float] = None
    fg_pct_l10:           Optional[float] = None
    fg3_pct_l10:          Optional[float] = None
    reb_pct_l10:          Optional[float] = None
    ast_pct_l10:          Optional[float] = None
    is_back_to_back:      Optional[int]   = None
    games_in_last_7:      Optional[int]   = None

    def asdict(self) -> Dict[str, Any]:
        return asdict(self)


# ───── pure helpers (unit-tested, no DB) ─────
def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def aggregate_team_games(
    outcome_rows: List[Dict[str, Any]],
) -> List[TeamGameRecord]:
    """Collapse N outcome rows for one team into M unique games (one per
    event_id). Each game's spread/OU outcomes from multiple books are
    collected as lists.

    Pure function. Caller passes outcome rows already pre-filtered to
    a single team_id and sorted whatever way — we sort by game_date
    on output.
    """
    by_event: Dict[str, TeamGameRecord] = {}
    for r in outcome_rows:
        eid = r.get("event_id")
        if not eid:
            continue
        if eid not in by_event:
            home_away = (r.get("home_away") or "").lower()
            is_home = (home_away == "home")
            hs = _num(r.get("home_score_used"))
            as_ = _num(r.get("away_score_used"))
            scored = hs if is_home else as_
            allowed = as_ if is_home else hs
            if scored is None and allowed is None:
                continue
            by_event[eid] = TeamGameRecord(
                event_id=eid,
                game_date=r.get("game_date") or "",
                is_home=is_home,
                points_scored=scored,
                points_allowed=allowed,
                won_h2h=None,
                spread_outcomes=[],
                ou_outcomes=[],
            )
        rec = by_event[eid]
        cat = (r.get("market_category") or "").lower()
        outcome_resolved = bool(r.get("outcome_resolved"))
        hit = r.get("hit") if outcome_resolved else None
        push = bool(r.get("push"))
        if cat == "h2h" and outcome_resolved and not push:
            # All h2h rows for one team/game agree (score-only). First wins.
            if rec.won_h2h is None and isinstance(hit, bool):
                rec.won_h2h = bool(hit)
        elif cat == "spread":
            if outcome_resolved and not push and isinstance(hit, bool):
                rec.spread_outcomes.append((r.get("line"), bool(hit)))
            elif push:
                rec.spread_outcomes.append((r.get("line"), None))
        elif cat == "game_total":
            if outcome_resolved and not push and isinstance(hit, bool):
                rec.ou_outcomes.append(bool(hit))
            elif push:
                rec.ou_outcomes.append(None)
    games = list(by_event.values())
    games.sort(key=lambda g: g.game_date)
    return games


def _mean(vals: List[float]) -> Optional[float]:
    return sum(vals) / len(vals) if vals else None


def _std(vals: List[float]) -> Optional[float]:
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return math.sqrt(var) if var > 0 else None


def _rate(items: List[Any]) -> Optional[float]:
    """Fraction of True among non-None items. None means push/unknown,
    excluded from denominator. Returns None when no usable items."""
    clean = [bool(x) for x in items if x is not None]
    if not clean:
        return None
    return sum(1 for x in clean if x) / len(clean)


def _rest_days(prior_date: Optional[str], as_of_date: str) -> Optional[int]:
    """Days between a team's last completed game and `as_of_date`.
    Caps at 14 — anything larger is treated as 14 (e.g. all-star break
    drops to a flat ceiling). Pure helper."""
    if not prior_date:
        return None
    try:
        from datetime import date
        a = date.fromisoformat(prior_date[:10])
        b = date.fromisoformat(as_of_date[:10])
    except ValueError:
        return None
    d = (b - a).days
    if d < 0:
        return None
    return min(14, d)


def compute_team_as_of_features(
    games: List[TeamGameRecord], *,
    as_of_date: str, sport: str,
) -> TeamAsOfFeatures:
    """Pure function: given a team's full game history sorted ascending,
    cut to games strictly before `as_of_date` and compute the feature
    snapshot. Mirrors the player engine's L5/L10/season ladder."""
    prior = [g for g in games if g.game_date and g.game_date < as_of_date]
    n = len(prior)
    if n == 0:
        return TeamAsOfFeatures(
            sample_size=0,
            mu_points_scored=None, sigma_points_scored=None,
            cv_points_scored=None,
            win_rate_l5=None, win_rate_l10=None, win_rate_season=None,
            avg_scored_l5=None, avg_scored_l10=None, avg_scored_season=None,
            avg_allowed_l5=None, avg_allowed_l10=None, avg_allowed_season=None,
            spread_cover_rate_l10=None, ou_hit_rate_l10=None,
            home_win_rate=None, away_win_rate=None,
            rest_days=None, tempo_l10=None, run_trend_l10=None,
        )

    recent = prior[-1:-21:-1] if len(prior) >= 1 else []   # most-recent-first
    last_5 = recent[:5]
    last_10 = recent[:10]
    season = prior  # all priors are within the same season-ish boundary
                     # (caller filters at orchestration time if needed)

    scored_all = [g.points_scored for g in season if g.points_scored is not None]
    allowed_all = [g.points_allowed for g in season if g.points_allowed is not None]
    scored_l5 = [g.points_scored for g in last_5 if g.points_scored is not None]
    scored_l10 = [g.points_scored for g in last_10 if g.points_scored is not None]
    allowed_l5 = [g.points_allowed for g in last_5 if g.points_allowed is not None]
    allowed_l10 = [g.points_allowed for g in last_10 if g.points_allowed is not None]

    mu = _mean(scored_all)
    sigma = _std(scored_all)
    cv = (sigma / mu) if (sigma is not None and mu and mu > 0) else None

    wins_l5 = [g.won_h2h for g in last_5]
    wins_l10 = [g.won_h2h for g in last_10]
    wins_season = [g.won_h2h for g in season]
    home_wins = [g.won_h2h for g in season if g.is_home]
    away_wins = [g.won_h2h for g in season if not g.is_home]

    spread_l10_tuples: List[Tuple[Optional[float], Optional[bool]]] = []
    ou_l10: List[Any] = []
    for g in last_10:
        spread_l10_tuples.extend(g.spread_outcomes)
        ou_l10.extend(g.ou_outcomes)

    spread_l5_tuples: List[Tuple[Optional[float], Optional[bool]]] = []
    ou_l5: List[Any] = []
    for g in last_5:
        spread_l5_tuples.extend(g.spread_outcomes)
        ou_l5.extend(g.ou_outcomes)

    rest = _rest_days(prior[-1].game_date if prior else None, as_of_date)

    # Tempo
    tempo_l10 = None
    if scored_l10 and allowed_l10 and len(scored_l10) == len(allowed_l10):
        tempo_l10 = sum(s + a for s, a in zip(scored_l10, allowed_l10)) / (
            2 * len(scored_l10))
    elif sport == "mlb" and scored_l10:
        tempo_l10 = sum(scored_l10) / len(scored_l10)

    # MLB run trend: L10 avg runs scored minus season avg
    run_trend_l10 = None
    if sport == "mlb":
        l10_avg = _mean(scored_l10)
        season_avg = _mean(scored_all)
        if l10_avg is not None and season_avg is not None:
            run_trend_l10 = round(l10_avg - season_avg, 3)

    def _r(x: Optional[float], nd: int = 4) -> Optional[float]:
        return round(x, nd) if x is not None else None

    STANDARD_SPREAD_LINES = [-1.5, 1.5, -2.5, 2.5, -3.5, 3.5]
    _line_feats: Dict[str, Optional[float]] = {}
    for _lv in STANDARD_SPREAD_LINES:
        _key = str(_lv).replace('.', '_').replace('-', 'neg')
        _hits_l5  = [hit for ln, hit in spread_l5_tuples  if ln == _lv and hit is not None]
        _hits_l10 = [hit for ln, hit in spread_l10_tuples if ln == _lv and hit is not None]
        _line_feats[f"spread_cover_{_key}_l5"]  = _r(_rate(_hits_l5))
        _line_feats[f"spread_cover_{_key}_l10"] = _r(_rate(_hits_l10))

    return TeamAsOfFeatures(
        sample_size=n,
        mu_points_scored=_r(mu, 3),
        sigma_points_scored=_r(sigma, 3),
        cv_points_scored=_r(cv, 4),
        win_rate_l5=_r(_rate(wins_l5)),
        win_rate_l10=_r(_rate(wins_l10)),
        win_rate_season=_r(_rate(wins_season)),
        avg_scored_l5=_r(_mean(scored_l5), 3),
        avg_scored_l10=_r(_mean(scored_l10), 3),
        avg_scored_season=_r(_mean(scored_all), 3),
        avg_allowed_l5=_r(_mean(allowed_l5), 3),
        avg_allowed_l10=_r(_mean(allowed_l10), 3),
        avg_allowed_season=_r(_mean(allowed_all), 3),
        spread_cover_rate_l10=_r(_rate([hit for _, hit in spread_l10_tuples])),
        ou_hit_rate_l10=_r(_rate(ou_l10)),
        spread_cover_rate_l5=_r(_rate([hit for _, hit in spread_l5_tuples])),
        ou_hit_rate_l5=_r(_rate(ou_l5)),
        home_win_rate=_r(_rate(home_wins)),
        away_win_rate=_r(_rate(away_wins)),
        rest_days=rest,
        tempo_l10=_r(tempo_l10, 3),
        run_trend_l10=run_trend_l10,
        **_line_feats,
    )


def assert_no_future_games(
    games: List[TeamGameRecord], *, as_of_date: str,
) -> None:
    """Leakage guard mirroring services/replay/engine.py's check."""
    for g in games:
        if g.game_date and g.game_date >= as_of_date:
            raise RuntimeError(
                f"leakage: game_date {g.game_date} >= as_of_date "
                f"{as_of_date} (event_id={g.event_id})")


# ───── SP enrichment (MLB only) ─────
async def _load_sp_lookup(db: AsyncIOMotorDatabase):
    """Pre-load all data needed for SP feature enrichment.

    Returns (team_id_to_abbr, team_abbr_to_pitchers, pitcher_name_to_stats):
      team_id_to_abbr:        'mlb_ari' → 'ARI'
      team_abbr_to_pitchers:  'ARI' → ['zac gallen', ...]
      pitcher_name_to_stats:  'zac gallen' → [(game_date, rolling_14), ...]
                              sorted ascending by game_date
    """
    team_id_to_abbr: Dict[str, str] = {}
    async for doc in db["team_master_hub"].find(
        {"sport": "mlb"}, {"team_id": 1, "display_names": 1}
    ):
        abbr = (doc.get("display_names") or {}).get("abbrev")
        tid = doc.get("team_id")
        if tid and abbr:
            team_id_to_abbr[tid] = abbr

    team_abbr_to_pitchers: Dict[str, List[str]] = {}
    async for doc in db["mlb_master_hub_2026"].find(
        {"position": {"$in": ["SP", "RP"]}}, {"team_abbr": 1, "player_name": 1}
    ):
        abbr = doc.get("team_abbr")
        name = (doc.get("player_name") or "").strip().lower()
        if abbr and name:
            team_abbr_to_pitchers.setdefault(abbr, []).append(name)

    pitcher_name_to_stats: Dict[str, List] = {}
    async for doc in db["mlb_statcast_pitcher_features"].find(
        {}, {"pitcher_name": 1, "game_date": 1, "rolling_14": 1}
    ):
        name = (doc.get("pitcher_name") or "").strip().lower()
        gd = doc.get("game_date")
        r14 = doc.get("rolling_14") or {}
        if name and gd:
            pitcher_name_to_stats.setdefault(name, []).append((gd, r14))
    for name in pitcher_name_to_stats:
        pitcher_name_to_stats[name].sort(key=lambda x: x[0])

    return team_id_to_abbr, team_abbr_to_pitchers, pitcher_name_to_stats


def _sp_features_for_team(
    team_abbr: Optional[str],
    as_of_date: str,
    team_abbr_to_pitchers: Dict[str, List[str]],
    pitcher_name_to_stats: Dict[str, List],
) -> Dict[str, Optional[float]]:
    """Compute average SP rotation quality for a team as of a given date.
    Uses pitcher stats from the rolling_14 window strictly before as_of_date."""
    if not team_abbr:
        return {}
    pitchers = team_abbr_to_pitchers.get(team_abbr, [])
    k_rates: List[float] = []
    woba_vals: List[float] = []
    hard_hit_vals: List[float] = []
    bb_rates: List[float] = []
    xwoba_vals: List[float] = []

    for pitcher_name in pitchers:
        stats = pitcher_name_to_stats.get(pitcher_name, [])
        best = None
        for gd, r14 in reversed(stats):
            if gd < as_of_date:
                best = r14
                break
        if best is None or (best.get("plate_appearances") or 0) < 10:
            continue
        if best.get("k_rate") is not None:
            k_rates.append(best["k_rate"])
        if best.get("wOBA_allowed") is not None:
            woba_vals.append(best["wOBA_allowed"])
        if best.get("hard_hit_allowed_rate") is not None:
            hard_hit_vals.append(best["hard_hit_allowed_rate"])
        if best.get("bb_rate") is not None:
            bb_rates.append(best["bb_rate"])
        if best.get("xwOBA_allowed") is not None:
            xwoba_vals.append(best["xwOBA_allowed"])

    def _avg(vals: List[float]) -> Optional[float]:
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "sp_k_rate_avg":        _avg(k_rates),
        "sp_woba_allowed_avg":  _avg(woba_vals),
        "sp_hard_hit_rate_avg": _avg(hard_hit_vals),
        "sp_bb_rate_avg":       _avg(bb_rates),
        "sp_xwoba_allowed_avg": _avg(xwoba_vals),
    }


# ───── BDL game features index ─────

async def _load_bdl_index(
    db: AsyncIOMotorDatabase,
) -> Dict[str, List[Tuple[str, Dict[str, Any]]]]:
    """Pre-load bdl_mlb_team_game_features into {mlb_team_id: [(game_date, doc), ...]}
    sorted ascending by game_date. One bulk load; no per-row queries."""
    index: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    cursor = db[BDL_GAME_FEATURES_COLL].find(
        {},
        projection={
            "_id": 0, "mlb_team_id": 1, "game_date": 1,
            "batting_avg": 1, "batting_obp": 1, "batting_slg": 1, "batting_ops": 1,
            "batting_k_rate": 1, "batting_bb_rate": 1, "batting_hr_rate": 1,
            "pitching_era": 1, "pitching_whip": 1,
            "pitching_k_per_9": 1, "pitching_bb_per_9": 1,
            "pitching_hr_per_9": 1, "pitching_qs_rate": 1,
            "fielding_fp": 1,
            "runs_scored_l5": 1, "runs_scored_l10": 1, "runs_scored_l20": 1,
            "runs_allowed_l5": 1, "runs_allowed_l10": 1, "runs_allowed_l20": 1,
            "first5_runs_l5": 1, "first5_runs_l10": 1, "first5_runs_l20": 1,
            "first_inning_score_rate": 1, "total_runs_l10_avg": 1,
        },
    ).batch_size(5000)
    async for doc in cursor:
        tid = doc.get("mlb_team_id")
        gd = doc.get("game_date")
        if not tid or not gd:
            continue
        index.setdefault(tid, []).append((gd, doc))
    for lst in index.values():
        lst.sort(key=lambda x: x[0])
    return index


def _bdl_lookup(
    index: Dict[str, List[Tuple[str, Dict[str, Any]]]],
    mlb_team_id: str,
    as_of_date: str,
) -> Optional[Dict[str, Any]]:
    """Return the most recent BDL doc for mlb_team_id where game_date < as_of_date."""
    entries = index.get(mlb_team_id)
    if not entries:
        return None
    lo, hi = 0, len(entries)
    while lo < hi:
        mid = (lo + hi) // 2
        if entries[mid][0] < as_of_date:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    return entries[lo - 1][1]


def _merge_bdl_into_feat(feat: "TeamAsOfFeatures", bdl_doc: Dict[str, Any]) -> None:
    """Copy BDL fields from a bdl_mlb_team_game_features doc into a TeamAsOfFeatures."""
    feat.batting_avg             = bdl_doc.get("batting_avg")
    feat.batting_obp             = bdl_doc.get("batting_obp")
    feat.batting_slg             = bdl_doc.get("batting_slg")
    feat.batting_ops             = bdl_doc.get("batting_ops")
    feat.batting_k_rate          = bdl_doc.get("batting_k_rate")
    feat.batting_bb_rate         = bdl_doc.get("batting_bb_rate")
    feat.batting_hr_rate         = bdl_doc.get("batting_hr_rate")
    feat.pitching_era            = bdl_doc.get("pitching_era")
    feat.pitching_whip           = bdl_doc.get("pitching_whip")
    feat.pitching_k_per_9        = bdl_doc.get("pitching_k_per_9")
    feat.pitching_bb_per_9       = bdl_doc.get("pitching_bb_per_9")
    feat.pitching_hr_per_9       = bdl_doc.get("pitching_hr_per_9")
    feat.pitching_qs_rate        = bdl_doc.get("pitching_qs_rate")
    feat.fielding_fp             = bdl_doc.get("fielding_fp")
    feat.runs_scored_l5_bdl      = bdl_doc.get("runs_scored_l5")
    feat.runs_scored_l10_bdl     = bdl_doc.get("runs_scored_l10")
    feat.runs_scored_l20_bdl     = bdl_doc.get("runs_scored_l20")
    feat.runs_allowed_l5_bdl     = bdl_doc.get("runs_allowed_l5")
    feat.runs_allowed_l10_bdl    = bdl_doc.get("runs_allowed_l10")
    feat.runs_allowed_l20_bdl    = bdl_doc.get("runs_allowed_l20")
    feat.first5_runs_l5          = bdl_doc.get("first5_runs_l5")
    feat.first5_runs_l10         = bdl_doc.get("first5_runs_l10")
    feat.first5_runs_l20         = bdl_doc.get("first5_runs_l20")
    feat.first_inning_score_rate = bdl_doc.get("first_inning_score_rate")
    feat.total_runs_l10_avg      = bdl_doc.get("total_runs_l10_avg")


# ───── BDL NBA game features index ─────

async def _load_nba_bdl_index(
    db: AsyncIOMotorDatabase,
) -> Dict[str, List[Tuple[str, Dict[str, Any]]]]:
    """Pre-load bdl_nba_team_game_features into {nba_team_id: [(game_date, doc), ...]}
    sorted ascending by game_date. One bulk load; no per-row queries."""
    index: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    cursor = db[NBA_BDL_GAME_FEATURES_COLL].find(
        {},
        projection={
            "_id": 0, "nba_team_id": 1, "game_date": 1,
            "off_rating_l5": 1, "off_rating_l10": 1, "off_rating_l20": 1,
            "def_rating_l5": 1, "def_rating_l10": 1, "def_rating_l20": 1,
            "pace_l5": 1, "pace_l10": 1, "pace_l20": 1,
            "ts_pct_l10": 1,
            "base_fg_pct": 1, "base_fg3_pct": 1,
            "advanced_reb_pct": 1, "advanced_ast_pct": 1,
            "is_back_to_back": 1, "games_in_last_7": 1,
        },
    ).batch_size(5000)
    async for doc in cursor:
        tid = doc.get("nba_team_id")
        gd = doc.get("game_date")
        if not tid or not gd:
            continue
        index.setdefault(tid, []).append((gd, doc))
    for lst in index.values():
        lst.sort(key=lambda x: x[0])
    return index


def _nba_bdl_lookup(
    index: Dict[str, List[Tuple[str, Dict[str, Any]]]],
    nba_team_id: str,
    as_of_date: str,
) -> Optional[Dict[str, Any]]:
    """Return the most recent BDL doc for nba_team_id where game_date < as_of_date."""
    entries = index.get(nba_team_id)
    if not entries:
        return None
    lo, hi = 0, len(entries)
    while lo < hi:
        mid = (lo + hi) // 2
        if entries[mid][0] < as_of_date:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    return entries[lo - 1][1]


def _merge_nba_bdl_into_feat(feat: "TeamAsOfFeatures", bdl_doc: Dict[str, Any]) -> None:
    """Copy BDL fields from a bdl_nba_team_game_features doc into a TeamAsOfFeatures."""
    feat.off_rating_l5   = bdl_doc.get("off_rating_l5")
    feat.off_rating_l10  = bdl_doc.get("off_rating_l10")
    feat.off_rating_l20  = bdl_doc.get("off_rating_l20")
    feat.def_rating_l5   = bdl_doc.get("def_rating_l5")
    feat.def_rating_l10  = bdl_doc.get("def_rating_l10")
    feat.def_rating_l20  = bdl_doc.get("def_rating_l20")
    feat.pace_l5         = bdl_doc.get("pace_l5")
    feat.pace_l10        = bdl_doc.get("pace_l10")
    feat.pace_l20        = bdl_doc.get("pace_l20")
    feat.ts_pct_l10      = bdl_doc.get("ts_pct_l10")
    feat.fg_pct_l10      = bdl_doc.get("base_fg_pct")
    feat.fg3_pct_l10     = bdl_doc.get("base_fg3_pct")
    feat.reb_pct_l10     = bdl_doc.get("advanced_reb_pct")
    feat.ast_pct_l10     = bdl_doc.get("advanced_ast_pct")
    feat.is_back_to_back = bdl_doc.get("is_back_to_back")
    feat.games_in_last_7 = bdl_doc.get("games_in_last_7")


# ───── DB orchestration ─────
async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Idempotent — same tolerant pattern as the rest of the SGO scripts."""
    try:
        await db[DST_COLL].create_index(
            [("sport", pymongo.ASCENDING),
              ("team_id", pymongo.ASCENDING),
              ("as_of_date", pymongo.ASCENDING)],
            unique=True, name="uniq_sport_team_asof",
        )
        await db[DST_COLL].create_index(
            [("sport", pymongo.ASCENDING),
              ("as_of_date", pymongo.ASCENDING)],
            name="sport_asof",
        )
    except Exception as e:
        print(f"  [indexes] non-fatal: {e}")


async def _load_team_history(
    db: AsyncIOMotorDatabase, *, sport: str, team_id: str,
) -> List[TeamGameRecord]:
    """Pull all outcomes for one team in one sport and aggregate to
    unique games. Pure-ish (touches DB but no business logic)."""
    rows: List[Dict[str, Any]] = []
    cursor = db[SRC_COLL].find(
        {"sport": sport, "team_id": team_id},
        projection={
            "_id": 0, "event_id": 1, "game_date": 1,
            "home_away": 1, "home_score_used": 1, "away_score_used": 1,
            "market_category": 1, "line": 1, "outcome_resolved": 1, "hit": 1,
            "push": 1,
        },
    ).batch_size(2000)
    async for r in cursor:
        rows.append(r)
    return aggregate_team_games(rows)


async def _distinct_team_ids(db: AsyncIOMotorDatabase, sport: str) -> List[str]:
    ids = await db[SRC_COLL].distinct("team_id", {"sport": sport})
    # Skip the virtual "game" team_id used by Phase 1 for game-total
    # market rows — that's not a real team.
    return sorted([t for t in ids if isinstance(t, str)
                    and t and t != "game"])


async def _distinct_as_of_dates(
    db: AsyncIOMotorDatabase, sport: str, team_id: str,
) -> List[str]:
    """Every distinct game_date for the team (these are the as_of_date
    anchors at which we compute the snapshot — one snapshot per game
    the team played)."""
    dates = await db[SRC_COLL].distinct(
        "game_date", {"sport": sport, "team_id": team_id})
    return sorted([d for d in dates if isinstance(d, str) and d])


async def build_features_for_sport(
    db: AsyncIOMotorDatabase, *,
    sport: str, dry_run: bool, force: bool,
    max_teams: int = 200,
) -> Dict[str, Any]:
    print(f"\n  [{sport.upper()}] building team features → {DST_COLL}")
    teams = await _distinct_team_ids(db, sport)
    if max_teams and len(teams) > max_teams:
        print(f"  [{sport.upper()}] capping at first {max_teams} of "
              f"{len(teams)} teams (use --max-teams to lift)")
        teams = teams[:max_teams]
    print(f"  [{sport.upper()}] team_ids: {len(teams)}  "
          f"sample={teams[:3]}…")

    if not dry_run:
        await _ensure_indexes(db)

    # Pre-load SP lookup for MLB enrichment.
    sp_enabled = (sport == "mlb")
    team_id_to_abbr: Dict[str, str] = {}
    team_abbr_to_pitchers: Dict[str, List[str]] = {}
    pitcher_name_to_stats: Dict[str, List] = {}
    if sp_enabled:
        print(f"  [{sport.upper()}] loading SP lookup data…")
        team_id_to_abbr, team_abbr_to_pitchers, pitcher_name_to_stats = (
            await _load_sp_lookup(db)
        )
        print(f"  [{sport.upper()}] SP lookup: "
              f"{len(team_id_to_abbr)} teams, "
              f"{sum(len(v) for v in team_abbr_to_pitchers.values())} pitchers, "
              f"{len(pitcher_name_to_stats)} statcast names")

    # Pre-load BDL game features index for MLB enrichment.
    bdl_index: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    if sport == "mlb":
        print(f"  [{sport.upper()}] loading BDL team game features index…")
        bdl_index = await _load_bdl_index(db)
        print(f"  [{sport.upper()}] BDL index: {len(bdl_index)} teams")

    # Pre-load BDL game features index for NBA enrichment.
    nba_bdl_index: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    if sport == "nba":
        print(f"  [{sport.upper()}] loading BDL NBA team game features index…")
        nba_bdl_index = await _load_nba_bdl_index(db)
        print(f"  [{sport.upper()}] NBA BDL index: {len(nba_bdl_index)} teams")

    counters = {
        "teams_processed":      0,
        "team_dates_emitted":   0,
        "feature_rows_written": 0,
        "skipped_existing":     0,
        "leakage_violations":   0,
        "dry_run":              dry_run,
    }
    sample_rows: List[Dict[str, Any]] = []

    for team_id in teams:
        games = await _load_team_history(db, sport=sport, team_id=team_id)
        if not games:
            continue
        counters["teams_processed"] += 1
        team_abbr = team_id_to_abbr.get(team_id) if sp_enabled else None
        team_game_dates = sorted({g.game_date for g in games if g.game_date})
        for as_of in team_game_dates:
            try:
                prior_games = [g for g in games if g.game_date < as_of]
                assert_no_future_games(prior_games, as_of_date=as_of)
            except RuntimeError as e:
                counters["leakage_violations"] += 1
                print(f"  [{sport.upper()}] LEAKAGE: {e}")
                continue
            feat = compute_team_as_of_features(
                games, as_of_date=as_of, sport=sport)
            # MLB: enrich with SP rotation quality.
            if sp_enabled and team_abbr:
                sp = _sp_features_for_team(
                    team_abbr, as_of,
                    team_abbr_to_pitchers, pitcher_name_to_stats,
                )
                feat.sp_k_rate_avg        = sp.get("sp_k_rate_avg")
                feat.sp_woba_allowed_avg  = sp.get("sp_woba_allowed_avg")
                feat.sp_hard_hit_rate_avg = sp.get("sp_hard_hit_rate_avg")
                feat.sp_bb_rate_avg       = sp.get("sp_bb_rate_avg")
                feat.sp_xwoba_allowed_avg = sp.get("sp_xwoba_allowed_avg")
            # MLB: enrich with BDL rolling boxscore + season stat features.
            if sport == "mlb":
                bdl_doc = _bdl_lookup(bdl_index, team_id, as_of)
                if bdl_doc:
                    _merge_bdl_into_feat(feat, bdl_doc)
            # NBA: enrich with BDL advanced team game features.
            if sport == "nba":
                nba_bdl_doc = _nba_bdl_lookup(nba_bdl_index, team_id, as_of)
                if nba_bdl_doc:
                    _merge_nba_bdl_into_feat(feat, nba_bdl_doc)
            counters["team_dates_emitted"] += 1
            if len(sample_rows) < 5 and feat.sample_size > 0:
                sample_rows.append({
                    "sport": sport, "team_id": team_id,
                    "as_of_date": as_of, **feat.asdict(),
                })
            if dry_run:
                continue
            doc = {
                "sport":      sport,
                "team_id":    team_id,
                "as_of_date": as_of,
                "computed_at": datetime.now(timezone.utc),
                **feat.asdict(),
            }
            await db[DST_COLL].update_one(
                {"sport": sport, "team_id": team_id, "as_of_date": as_of},
                {"$set": doc}, upsert=True,
            )
            counters["feature_rows_written"] += 1
        if counters["teams_processed"] % 5 == 0:
            print(f"    [{sport.upper()}] processed {counters['teams_processed']}/"
                  f"{len(teams)} teams; emitted={counters['team_dates_emitted']:,}")
    return {"sport": sport, "counters": counters, "sample_rows": sample_rows}


def _print_summary(r: Dict[str, Any]) -> None:
    c = r["counters"]
    print()
    print(f"  ── {r['sport'].upper()} TEAM FEATURE BUILD SUMMARY ──")
    print(f"     teams processed:        {c['teams_processed']:,}")
    print(f"     team-dates emitted:     {c['team_dates_emitted']:,}")
    print(f"     feature rows written:   {c['feature_rows_written']:,}  "
          f"({'DRY-RUN' if c['dry_run'] else 'live'})")
    print(f"     leakage violations:     {c['leakage_violations']:,}")
    if r["sample_rows"]:
        print("     sample rows (first 5):")
        for s in r["sample_rows"]:
            print(f"        {s['team_id']:<10s} @ {s['as_of_date']}  "
                  f"n={s['sample_size']:>3}  "
                  f"win_l10={s.get('win_rate_l10')}  "
                  f"avg_scored_l10={s.get('avg_scored_l10')}  "
                  f"tempo_l10={s.get('tempo_l10')}")


# ───── main ─────
async def amain(args: argparse.Namespace) -> int:
    sports = [args.sport] if args.sport != "all" else list(SUPPORTED_SPORTS)
    for s in sports:
        if s not in SUPPORTED_SPORTS:
            print(f"  ERROR: unsupported --sport {s!r}")
            return 2
    dry_run = bool(args.dry_run)
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"build_team_features  version={FEATURE_VERSION}")
    print(f"  sports={sports}  dry_run={dry_run}  force={args.force}")
    print("  CONTRACT: upserts to team_model_features keyed by "
          "(sport, team_id, as_of_date). Idempotent. Leakage-safe.")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        for sp in sports:
            r = await build_features_for_sport(
                db, sport=sp, dry_run=dry_run, force=args.force,
                max_teams=args.max_teams)
            _print_summary(r)
        if dry_run:
            print("\n  DRY-RUN — no writes. Re-run without --dry-run to persist.")
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=list(SUPPORTED_SPORTS) + ["all"],
                    default="all")
    p.add_argument("--dry-run", action="store_true",
                    help="Compute & summarize but do not write to Mongo.")
    p.add_argument("--force", action="store_true",
                    help="(Reserved for future incremental mode)")
    p.add_argument("--max-teams", type=int, default=200,
                    help="Safety cap on teams processed per sport.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

"""
Team Prop Tier Service — Phase 1 (board read path).

PRODUCTION ROUTING RULES (this module is held to them):
  * NO read from SGO archive collections (team_historical_props,
    nfl_historical_props, sgo_*).
  * Read source = `team_prop_scores` (mirrors player path:
    {sport}_prop_scores).
  * If `team_prop_scores` is empty for the requested sport, lazily
    invoke `passthrough_team_live_to_scores` to copy whatever is
    currently in `team_live_props` (the live-provider write target)
    into `team_prop_scores`. The future live-ingest pipeline can also
    call the passthrough explicitly after its writes.
  * Model fields stay None. Cards display `team_model_pending=true`.
  * 2026-06-02 — every card is enriched with REAL historical hit
    rates (l5/l10/l20), team averages, projection, deterministic
    vision_intel + scout_badges from `team_historical_outcomes`
    via `services/team_historical_enrichment.py`. Same SSOT helpers
    `routes/team_with_badges.py` uses, so the board cards and the
    detail page can never disagree on the numbers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import logging

from services.team_prop_passthrough import (
    SCORES_COLL,
    passthrough_team_live_to_scores,
)
from services.team_historical_enrichment import (
    compute_hit_rates,
    fetch_team_game_history,
    compute_baseline_stats,
    market_category_to_stat_token,
    build_vision_intel,
    build_scout_badges,
    split_team_id,
)


def _classify_market_category_from_key(market_key):
    """Mirror of `team_live_xgb_scorer.classify_market_category` —
    duplicated here to avoid an import cycle and keep this service
    self-contained.

      *-ml-*                       → h2h
      *-sp-*                       → spread
      *-all-game-ou-*              → game_total
      *-home-game-ou-*/-away-game-ou-* → team_total

    `market_key` here is what the live sync writes — either the raw
    Odds API key (`h2h` / `spreads` / `totals` / `team_totals`) or
    the canonical seeded `points-{side}-game-{type}` token. We
    accept both shapes.
    """
    if not market_key:
        return None
    s = str(market_key).lower()
    # Odds-API raw keys (live sync writes these on team_live_props).
    if s == "h2h":
        return "h2h"
    if s == "spreads":
        return "spread"
    if s == "totals":
        return "game_total"
    if s == "team_totals":
        return "team_total"
    # Canonical seeded / scorer token shape.
    if "-ml-" in s:
        return "h2h"
    if "-sp-" in s:
        return "spread"
    if "-all-game-ou-" in s:
        return "game_total"
    if "-home-game-ou-" in s or "-away-game-ou-" in s:
        return "team_total"
    return None

logger = logging.getLogger(__name__)

MATCHUP_COLL_BY_SPORT: Dict[str, str] = {
    "mlb":   "team_matchups",
    "nba":   "team_matchups",
    "nfl":   "nfl_matchups",
    "ncaaf": "ncaaf_matchups",
}
TEAM_MASTER_COLL = "team_master_hub"

_TIER_LABEL = {
    "safe_haven":  "Safe Haven",
    "front_lines": "Front Lines",
    "war_zone":    "War Zone",
}


async def _build_team_lookup(db, sport: str
                                  ) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    async for t in db[TEAM_MASTER_COLL].find(
        {"sport": sport}, projection={"_id": 0}):
        tid = t.get("team_id")
        if not tid:
            continue
        dn   = t.get("display_names") or {}
        cols = t.get("colors") or {}
        out[tid] = {
            "team_id":       tid,
            "team_abbr":     dn.get("abbrev"),
            "team_name":     dn.get("full") or dn.get("short"),
            "team_short":    dn.get("short"),
            "team_market":   dn.get("market"),
            "sport":         t.get("sport"),
            "team_logo_url": None,
            "team_colors":   cols,
        }
    return out


async def _build_event_lookup(db, sport: str,
                                   event_ids: List[str]
                                   ) -> Dict[str, Dict[str, Any]]:
    if not event_ids:
        return {}
    coll = MATCHUP_COLL_BY_SPORT.get(sport, "team_matchups")
    flt: Dict[str, Any] = {"event_id": {"$in": event_ids}}
    if sport == "nfl":
        flt["sport"] = "nfl"
    out: Dict[str, Dict[str, Any]] = {}
    async for m in db[coll].find(flt, projection={"_id": 0}):
        out[m["event_id"]] = m
    return out


def _market_label(score_row: Dict[str, Any]) -> str:
    """Best-available label without inventing strings."""
    for k in ("market_name", "stat_type", "market", "market_key"):
        v = score_row.get(k)
        if isinstance(v, str) and v.strip():
            return v.replace("_", " ").strip().title() \
                if k in ("stat_type", "market_key") else v
    return "Team Prop"


def _hydrate_card(score: Dict[str, Any],
                     teams: Dict[str, Dict[str, Any]],
                     events: Dict[str, Dict[str, Any]]
                     ) -> Optional[Dict[str, Any]]:
    """`team_prop_scores` row → UniversalPlayerCard-shaped pick."""
    tid = score.get("team_id")
    if not tid:
        return None
    event_id = score.get("event_id")
    matchup  = events.get(event_id) or {}
    home_id  = matchup.get("home_team_id")
    away_id  = matchup.get("away_team_id")
    this_team = teams.get(tid) or {}
    opp_team  = teams.get(away_id if tid == home_id else home_id) or {}
    is_home   = (tid == home_id)
    team_name = this_team.get("team_short") \
                or this_team.get("team_name") \
                or (matchup.get("home_team_name") if is_home
                    else matchup.get("away_team_name"))
    opp_name  = opp_team.get("team_short") \
                or opp_team.get("team_name") \
                or (matchup.get("away_team_name") if is_home
                    else matchup.get("home_team_name"))
    market_label = _market_label(score)

    # Fallback opponent resolution: when `events` doesn't have the
    # event (e.g. synthetic seeds or rows missing the master_hub
    # entry), use `home_team` / `away_team` strings stamped on the
    # score row by the live ingest.
    if not opp_name:
        home_team_str = score.get("home_team") or ""
        away_team_str = score.get("away_team") or ""
        team_abbr = (this_team.get("team_abbr") or "").upper()
        team_id_abbr = (tid or "").split("_", 1)[-1].upper()
        # If `home_team` string ends with this team's name or matches
        # the abbr, the team is HOME and opp = away_team_str.
        if home_team_str and team_id_abbr and (
                team_id_abbr in home_team_str.upper()
                or team_abbr and team_abbr in home_team_str.upper()):
            opp_name = away_team_str or opp_name
            is_home = True
        elif away_team_str and team_id_abbr and (
                team_id_abbr in away_team_str.upper()
                or team_abbr and team_abbr in away_team_str.upper()):
            opp_name = home_team_str or opp_name
            is_home = False

    # Derive opponent abbr from the resolved opponent display name —
    # downstream `_resolve_opp_def_rank` keys on the abbr.
    opp_abbr = None
    if opp_name:
        # team_master_hub stores `team_abbr`; if the opponent isn't
        # in the hub, take the last word (e.g. "Mavericks").
        for v in teams.values():
            if (v.get("team_name") or v.get("team_short")) == opp_name:
                opp_abbr = v.get("team_abbr")
                break

    return {
        # Identity
        "prop_type":          "team",
        "team_id":            tid,
        "team_name":          team_name,
        "team_abbr":          this_team.get("team_abbr"),
        "team_logo_url":      this_team.get("team_logo_url"),
        "team_colors":        this_team.get("team_colors") or {},
        "team":               team_name,
        "opponent":           opp_name,
        "opponent_abbr":      opp_abbr,
        "home_away":          "home" if is_home else "away",
        "is_home":            is_home,
        "player_id":          None,
        "player_name":        None,
        "photo_url":          None,
        "headshot_url":       None,

        # Market / line / odds (passthrough)
        "event_id":            event_id,
        "game_date":           score.get("game_date"),
        "commence_time":       score.get("commence_time"),
        "sport":               score.get("sport"),
        "market":              market_label,
        "market_key":          score.get("market_key"),
        "display_market":      market_label,
        "stat_type":           score.get("stat_type"),
        "period_id":           score.get("period_id"),
        "line":                score.get("line"),
        "side":                score.get("side"),
        "odds":                score.get("odds"),
        "sharp_price":         score.get("sharp_price"),
        "book":                score.get("book"),
        "bookmaker":           score.get("book"),
        "is_alternate":        score.get("is_alternate"),
        "reference_only":      score.get("reference_only"),

        # Tier + display
        "tier":                score.get("tier"),
        "tier_label":          score.get("tier_label"),
        "badges":              score.get("badges") or [],

        # Model fields — populated when the XGB scorer has run (set by
        # services.team_live_xgb_scorer). Until then they're None and
        # `team_model_pending=True` keeps the card in its pending state.
        "vision_score":        score.get("vision_score"),
        "intel_score":         score.get("intel_score"),
        "intel_verdict":       None,
        "intel_suite":         None,
        "vision_intel":        None,
        "confidence":          score.get("confidence"),
        "model_probability":   score.get("model_probability"),
        "true_probability":    score.get("true_probability"),
        "implied_probability": score.get("implied_probability"),
        "edge":                score.get("edge"),
        "edge_pct":            score.get("edge_pct"),
        "projection":          None,
        "cv":                  None,
        "vk_predicted":        None,
        "season_avg":          None,
        "hit_rate_l5":         None,
        "hit_rate_l10":        None,
        "hit_rate_l20":        None,
        "context_badges":      [],
        "is_vision_enriched":  bool(score.get("model_probability") is not None),
        "validation":          {"is_fully_validated": False,
                                 "has_mlr": False,
                                 "has_gemini": False},
        "playable_on_pp":      True,

        # Flags / lineage
        "team_model_pending":  bool(score.get("team_model_pending", True)),
        "model_version":       score.get("model_version"),
        "market_category":     score.get("market_category"),
        "gate_reasons":        score.get("gate_reasons") or [],
        "routing_source":      score.get("routing_source") or "odds",
        "odds_routed":         True,
        "snapshot_iso":        score.get("snapshot_iso"),
        "ingested_at":         score.get("ingested_at"),
        "passthrough_at":      score.get("passthrough_at"),
        "scored_at":           score.get("scored_at"),
    }


async def _resolve_opp_def_rank(
    db, *, sport: str, opp_team_abbr: Optional[str],
) -> Optional[int]:
    """Rank the opponent's points-allowed average against all other
    teams in the same sport. Cheap: a single aggregation over
    `team_historical_outcomes` keyed on `opponent_team_id`. Cached
    per request via the LRU in this module.
    """
    if not opp_team_abbr:
        return None
    sport_l = (sport or "").lower()
    cache_key = sport_l
    ranks = _OPP_DEF_RANK_CACHE.get(cache_key)
    if ranks is None:
        # Aggregate: for every team, the mean of `home_score_used +
        # away_score_used` filtered to games where THIS team was the
        # OPPONENT. Lower mean = tougher defense = lower rank (#1).
        pipeline = [
            {"$match": {
                "sport": sport_l,
                "outcome_resolved": True,
                "home_score_used": {"$ne": None},
                "away_score_used": {"$ne": None},
                "opponent_team_id": {"$ne": None},
            }},
            {"$group": {
                "_id": "$opponent_team_id",
                "n": {"$sum": 1},
                # The opponent's points scored = (this team's row's
                # opponent_score_used). The row stores BOTH scores
                # already so we just use total - team_score.
                "total_points_avg": {"$avg": {"$add": [
                    "$home_score_used", "$away_score_used"]}},
            }},
            {"$match": {"n": {"$gte": 5}}},
            {"$sort": {"total_points_avg": 1}},  # lowest = tougher
        ]
        ranks = {}
        i = 0
        async for r in db["team_historical_outcomes"].aggregate(pipeline):
            i += 1
            tid = (r["_id"] or "").lower()
            # Convert tid like "nba_bos" → "BOS" abbreviation
            abbr = tid.split("_", 1)[-1].upper()
            ranks[abbr] = i
        _OPP_DEF_RANK_CACHE[cache_key] = ranks
    return ranks.get((opp_team_abbr or "").upper())


_OPP_DEF_RANK_CACHE: Dict[str, Dict[str, int]] = {}


async def _enrich_cards_with_history(
    db, cards: List[Dict[str, Any]], sport: str,
) -> List[Dict[str, Any]]:
    """Stamp REAL historical hit_rate_l5/l10/l20 + season avg +
    projection + vision_intel + scout_badges on every card.

    Pulled from `team_historical_outcomes` via shared SSOT helpers so
    the board card and the detail page are byte-equal on the numbers
    they display.

    Cost: one game-history query per UNIQUE team (cached), one
    hit-rate query per prop. For a typical tier read (10 cards), this
    is 1-2 team queries + 10 prop queries on a small graded-row index
    — well under 100ms.
    """
    if not cards:
        return cards

    # 1. Per-team game history (used for baseline averages).
    by_team: Dict[str, List[Dict[str, Any]]] = {}
    for c in cards:
        tid = c.get("team_id")
        if tid:
            by_team.setdefault(tid, []).append(c)
    baseline_by_team: Dict[str, Dict[str, Any]] = {}
    for tid in by_team.keys():
        logs = await fetch_team_game_history(
            db, team_id=tid, sport=sport, limit=25)
        baseline_by_team[tid] = compute_baseline_stats(logs)

    # 2. Per-card hit-rate + intel enrichment.
    for c in cards:
        tid = c.get("team_id")
        if not tid:
            continue
        # market_category lands on the row from the XGB scorer; the
        # passthrough output (pre-scorer) and seeded synthetic rows
        # don't carry it. Derive on-the-fly from market_key when
        # missing so enrichment works on both code paths.
        market_category = (
            c.get("market_category")
            or _classify_market_category_from_key(c.get("market_key"))
            or ""
        )
        c["market_category"] = market_category
        side = (c.get("side") or "").upper()
        try:
            line = float(c["line"]) if c.get("line") is not None else None
        except (TypeError, ValueError):
            line = None
        stat_token = market_category_to_stat_token(market_category)

        # Opponent abbr (for h2h hit rate sentence). Cards don't carry
        # `opponent_team_id`, only `opponent` name — h2h slice is left
        # to the team-detail endpoint where opp_team_id is resolved.
        opp_abbr = (c.get("opponent") or "")

        # Historical hit rates @ this (category, side, ~line).
        hr = await compute_hit_rates(
            db,
            team_id=tid, sport=sport,
            market_category=market_category,
            side=side, line=line,
        )
        hit_l5 = hr.get("hit_rate_l5")
        hit_l10 = hr.get("hit_rate_l10")
        hit_l20 = hr.get("hit_rate_l20")
        season_hr = hr.get("season_hit_rate")

        # Baseline avg for this stat token (projection input).
        bs = (baseline_by_team.get(tid) or {}).get(stat_token, {}) or {}
        l5_avg = bs.get("l5_avg")
        l10_avg = bs.get("l10_avg")
        l20_avg = bs.get("l20_avg")
        season_avg = bs.get("season_avg")

        # Projection: use l10 avg (no model). Edge vs line ratio.
        projection = l10_avg
        edge_vs_fair: Optional[float] = None
        if (projection is not None and line not in (None, 0)
                and market_category != "h2h"):
            edge_vs_fair = round((projection - line) / line, 4)

        # H2H — opponent_team_id isn't stamped on team_prop_scores
        # today, so we skip the h2h slice for board cards. The team
        # detail page (team_with_badges) does include it because the
        # endpoint resolves opp_team_id from event_id + master_hub.
        opp_hr = None
        opp_sample = 0

        vision_text = build_vision_intel(
            stat_token=stat_token, side=side, line=line,
            hit_l5=hit_l5, hit_l10=hit_l10, hit_l20=hit_l20,
            season_hr=season_hr,
            opp_abbr=opp_abbr, opp_hit_pct=opp_hr, opp_sample=opp_sample,
        )
        scout = build_scout_badges(
            hit_l5=hit_l5, hit_l10=hit_l10, hit_l20=hit_l20,
        )

        # ── Display tokens UniversalPlayerCard reads. The card's
        # `formatStatType(stat_type)` call drives the visible
        # "STAT_TYPE LINE" headline. For team props, stat_type is None
        # on the raw row (no XGB scorer write) — we substitute the
        # human-friendly bucket name so the headline reads
        # "Team Total 110.5", "Spread -5.5", etc.
        stat_label = {
            "team_total": "Team Total",
            "game_total": "Game Total",
            "spread":     "Spread",
            "h2h":        "Moneyline",
        }.get(market_category, market_category.replace("_", " ").title())

        # Direction display: HOME / AWAY / OVER / UNDER → Over/Under
        # mapping so the existing `vk_recommendation` chip renders
        # the same colour gradient as player cards.
        if side in ("OVER", "HOME"):
            vk_rec, side_label = "OVER", "Over"
        elif side in ("UNDER", "AWAY"):
            vk_rec, side_label = "UNDER", "Under"
        elif side == "ML":
            # Moneyline — use HOME / AWAY semantics from the row
            ha = (c.get("home_away") or "").upper()
            if ha == "HOME":
                vk_rec, side_label = "OVER", "Home ML"
            else:
                vk_rec, side_label = "UNDER", "Away ML"
        else:
            vk_rec, side_label = "OVER", side.title() if side else ""

        # Headline strings the card renders verbatim.
        if line is not None:
            stat_line = f"{stat_label} {line}"
            big_pick = f"{side_label.upper()} {line} {stat_label.upper()}"
        else:
            stat_line = stat_label
            big_pick = f"{side_label.upper()} {stat_label.upper()}"

        # True-probability synthesis: in the player pipeline `tp` is
        # the consensus de-vigged probability. For teams (no model
        # yet) we use `season_hit_rate` as the consensus baseline so
        # the TP chip on the card has a real, deterministic number.
        # Fallback ladder: season_hr → l20 → l10 → 50.
        tp_pct = season_hr if season_hr is not None else (
            hit_l20 if hit_l20 is not None else (
                hit_l10 if hit_l10 is not None else 50.0
            ))

        # PrizePicks-style multiplier label heuristic (goblin /
        # balanced / demon) bound to historical hit-rate confidence.
        if hit_l20 is not None and hit_l20 >= 70:
            pp_label = "goblin"
        elif hit_l20 is not None and hit_l20 <= 35:
            pp_label = "demon"
        else:
            pp_label = "balanced"

        # Opponent allowance rank — team analog of player DVP. Reads
        # `OPP_TOTAL.season_avg` for the opponent (computed below).
        # Stored on the card as `opponent_defensive_rank` so the
        # existing DVP chip on UniversalPlayerCard renders for teams.
        opp_def_rank: Optional[int] = None
        opp_def_source: Optional[str] = None
        if c.get("opponent"):
            # Get all teams' OPP_TOTAL season_avg for this sport →
            # rank our opponent's "points allowed" baseline. Lower
            # rank = tougher defense (better for unders). Cheap:
            # 30 teams, single query, no per-card cost.
            opp_def_rank = await _resolve_opp_def_rank(
                db, sport=sport, opp_team_abbr=c.get("opponent"))
            opp_def_source = "team_historical_outcomes" if opp_def_rank else None

        # Stamp the canonical player-card fields so UniversalPlayerCard
        # / TeamPropRow render real numbers instead of nulls.
        c["hit_rate_l5"]  = hit_l5
        c["hit_rate_l10"] = hit_l10
        c["hit_rate_l20"] = hit_l20
        c["hit_rate_over"] = hit_l20 if side in ("OVER", "HOME", "ML") else None
        c["hit_rate_under"] = hit_l20 if side in ("UNDER", "AWAY") else None
        c["season_hit_rate"] = season_hr
        c["hit_rate_sample_size"] = hr.get("sample_size")
        c["hit_rate_status"]  = "computed" if hit_l20 is not None else "no_data"
        c["l5_avg"]   = l5_avg
        c["l10_avg"]  = l10_avg
        c["l20_avg"]  = l20_avg
        c["avg"]      = l10_avg
        c["season_avg"] = season_avg
        c["vk_predicted"] = projection
        c["vk2_projection"] = projection
        c["projection"]   = projection
        c["model_projection"] = projection
        c["edge_vs_fair"] = edge_vs_fair
        c["total_edge"]   = edge_vs_fair
        c["vision_intel"] = vision_text
        c["vision_summary"] = vision_text
        c["short_sentence"] = vision_text
        c["scout_badges"] = scout
        c["active_badges"] = [b["badge_key"] for b in scout]
        c["intel_suite"] = {
            "lasso": (
                {"projection": projection,
                 "confidence_tier": "HISTORICAL"}
                if projection is not None else None
            ),
            "scout_badges": scout,
            "context_badges": [],
        }
        c["is_vision_enriched"] = vision_text is not None

        # Display-binding fields (the ones the card actually reads to
        # render the visible headline / TP / DVP / pp_multiplier).
        c["stat_type"]            = stat_label
        c["stat_type_canonical"]  = stat_label
        c["stat_line"]            = stat_line
        c["big_pick_text"]        = big_pick
        c["recommendation"]       = side_label
        c["vk_recommendation"]    = vk_rec
        c["tp"]                   = round(tp_pct, 1) if tp_pct is not None else None
        c["confidence_score"]     = round((tp_pct or 0) / 100.0, 4)
        c["pp_multiplier_label"]  = pp_label
        c["pp_multiplier_source"] = "historical_hit_rate"
        c["opponent_defensive_rank"]      = opp_def_rank
        c["opponent_defensive_source"]    = opp_def_source
        c["opponent_defensive_stat_type"] = stat_label
        c["opponent_abbr"]                = c.get("opponent")
        # Vision score % synthesis: same TP-anchored value so the
        # green-circle chip on the card has a real number.
        c["vision_score"]    = round(tp_pct, 1) if tp_pct is not None else None
        c["vision_score_v2"] = c["vision_score"]
        c["intel_score"]     = c["vision_score"]

    return cards


async def get_team_prop_picks(db, *, sport: str, tier_name: str,
                                   limit: int = 10,
                                   sort: Optional[str] = None,
                                   ) -> Dict[str, Any]:
    """Read team_prop_scores → card-shaped picks for ONE tier.

    Returns the SAME envelope shape player tier endpoints return.
    """
    sport_l = (sport or "").lower()

    # Lazy passthrough: if scores are empty for this sport, copy
    # current live_props into scores. Idempotent.
    n_in_scores = await db[SCORES_COLL].count_documents(
        {"sport": sport_l, "team_model_pending": True})
    passthrough_audit: Optional[Dict[str, Any]] = None
    if n_in_scores == 0:
        passthrough_audit = await passthrough_team_live_to_scores(
            db, sport=sport_l)

    # 2026-06-01: lazy XGB scoring. If any rows for this sport are
    # still unscored (model_probability is None), kick off a bounded
    # batch score so the cards arrive with real model fields. This is
    # idempotent — already-scored rows are filtered out at query time.
    score_audit: Optional[Dict[str, Any]] = None
    n_unscored = await db[SCORES_COLL].count_documents(
        {"sport": sport_l, "prop_type": "team",
         "model_probability": None})
    if n_unscored > 0:
        try:
            from services.team_live_xgb_scorer import score_team_live_props
            score_audit = await score_team_live_props(
                db, sport=sport_l, max_rows=5000)
        except Exception:
            logger.exception(
                "[team_prop_tier] lazy XGB score failed — falling back "
                "to pending cards")

    score_rows: List[Dict[str, Any]] = []
    cur = db[SCORES_COLL].find(
        {"sport": sport_l, "tier": tier_name},
        projection={"_id": 0}
    ).limit(5000)
    async for r in cur:
        score_rows.append(r)

    teams = await _build_team_lookup(db, sport_l)
    event_ids = list({r.get("event_id") for r in score_rows
                      if r.get("event_id")})
    events = await _build_event_lookup(db, sport_l, event_ids)

    cards: List[Dict[str, Any]] = []
    for r in score_rows:
        c = _hydrate_card(r, teams, events)
        if c is not None:
            cards.append(c)

    # Dedupe by canonical compound key
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for c in cards:
        k = (c["event_id"], c["team_id"], c["market_key"],
              c["side"], c["line"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(c)

    if tier_name == "safe_haven":
        deduped.sort(key=lambda x: (x["odds"] or 0))
    elif tier_name == "war_zone":
        deduped.sort(key=lambda x: -(x["odds"] or 0))
    else:
        deduped.sort(key=lambda x: abs(x["odds"] or 0))

    cards = deduped[:limit]

    # 2026-06-02 — Enrich the FINAL N cards (post-dedupe + limit) with
    # REAL historical hit rates + deterministic vision intel + scout
    # badges. Same SSOT helpers `routes/team_with_badges.py` uses, so
    # the board card and the team detail page agree on every number.
    # Per-team game-history query is cached so it runs at most once
    # per team (1-3 queries) + one hit-rate query per returned card.
    cards = await _enrich_cards_with_history(db, cards, sport_l)
    return {
        "tier":         tier_name,
        "tier_label":   f"{_TIER_LABEL[tier_name]} "
                         f"({sport_l.upper()})",
        "sport":        sport_l,
        "prop_type":    "team",
        "picks":        cards,
        "count":        len(cards),
        "status":       "odds_routed",
        "pipeline": {
            "source":             SCORES_COLL,
            "passthrough_from":   "team_live_props",
            "odds_routed":        True,
            "team_model_pending": False,
            "routing_source":     "odds_then_xgb",
            "model_version":      (
                cards[0].get("model_version") if cards else None),
        },
        "lazy_passthrough":  passthrough_audit,
        "lazy_xgb_score":    score_audit,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
    }

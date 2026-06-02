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
        market_category = c.get("market_category") or ""
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

        # Stamp the canonical player-card fields so UniversalPlayerCard
        # / TeamPropRow render real numbers instead of nulls.
        c["hit_rate_l5"]  = hit_l5
        c["hit_rate_l10"] = hit_l10
        c["hit_rate_l20"] = hit_l20
        c["season_hit_rate"] = season_hr
        c["hit_rate_sample_size"] = hr.get("sample_size")
        c["l5_avg"]   = l5_avg
        c["l10_avg"]  = l10_avg
        c["l20_avg"]  = l20_avg
        c["season_avg"] = season_avg
        c["vk_predicted"] = projection
        c["projection"]   = projection
        c["edge_vs_fair"] = edge_vs_fair
        c["vision_intel"] = vision_text
        c["vision_summary"] = vision_text
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

    # 2026-06-02 — Enrich every card with REAL historical hit rates +
    # deterministic vision intel + scout badges. Same SSOT helpers
    # `routes/team_with_badges.py` uses, so the board card and the
    # team detail page agree on every number.
    #
    # Batch by team_id so we fetch each team's game history (used for
    # baseline averages) at most once. Per-prop hit-rate query stays
    # one round-trip — small (<= 200 rows) and capped at the request
    # limit (10 cards × 3 tiers = 30 props max).
    cards = await _enrich_cards_with_history(db, cards, sport_l)

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

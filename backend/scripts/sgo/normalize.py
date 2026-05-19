"""
Schema-tolerant extractors for SGO event payloads.

Designed to handle field-name variants discovered at probe time. Each extractor
is defensive: missing fields → None / [].  We always preserve the raw payload
under `sgo_events.raw` so we can re-extract after schema discovery without
re-fetching from the API.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Iterable


def _get(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """First-match lookup over multiple candidate keys."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _as_iso(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    return v


# ─────────────────────────────────────────────────────────────── event metadata
def extract_event(ev: Dict[str, Any], *, snapshot_time: str) -> Dict[str, Any]:
    return {
        "event_id":        _get(ev, "eventID", "event_id", "id"),
        "sport_id":        _get(ev, "sportID", "sport_id"),
        "league_id":       _get(ev, "leagueID", "league_id"),
        "start_time":      _as_iso(_get(ev, "startTime", "commenceTime",
                                        "commence_time", "start_time")),
        "game_status":     _get(ev, "status", "gameStatus", "game_status"),
        "home_team_id":    _get(ev, "homeTeamID", "home_team_id"),
        "away_team_id":    _get(ev, "awayTeamID", "away_team_id"),
        "home_team_name":  _get(ev, "homeTeamName", "homeTeam", "home_team_name"),
        "away_team_name":  _get(ev, "awayTeamName", "awayTeam", "away_team_name"),
        "home_score":      _get(ev, "homeScore", "home_score"),
        "away_score":      _get(ev, "awayScore", "away_score"),
        "season":          _get(ev, "season"),
        "week":            _get(ev, "week"),
        "snapshot_time":   snapshot_time,
        "raw":             ev,
    }


# ──────────────────────────────────────────────────────────────────────── result
def extract_result(ev: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_id":     _get(ev, "eventID", "event_id", "id"),
        "league_id":    _get(ev, "leagueID", "league_id"),
        "game_status":  _get(ev, "status", "gameStatus", "game_status"),
        "final_status": _get(ev, "finalStatus", "final_status"),
        "completed_at": _as_iso(_get(ev, "completedAt", "completed_at",
                                     "endTime", "end_time")),
        "home_score":   _get(ev, "homeScore", "home_score"),
        "away_score":   _get(ev, "awayScore", "away_score"),
        "winning_team_id": _get(ev, "winningTeamID", "winning_team_id"),
        "result_summary":  _get(ev, "result", "resultSummary"),
    }


# ─────────────────────────────────────────────────────────────── markets/odds
def _iter_markets(ev: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    return (ev.get("odds") or ev.get("markets") or
            ev.get("oddsByOdd") or ev.get("oddsByPlayer") or [])


def _iter_books(market: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    return (market.get("bookmakers") or market.get("books") or
            market.get("bySportsbook") or [])


def _iter_outcomes(book: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    return (book.get("outcomes") or book.get("selections") or
            book.get("sides") or [])


def extract_props_and_outcomes(
    ev: Dict[str, Any], *, snapshot_time: str
) -> Dict[str, List[Dict[str, Any]]]:
    """
    From one event, emit:
      • raw props rows (sgo_props_raw): one per (event, oddID, book, side)
      • outcome rows (sgo_odds_outcomes): graded resolutions if present
      • consensus rows (sgo_book_consensus): fairOdds + bookOdds per market
    """
    eid = _get(ev, "eventID", "event_id", "id")
    league_id = _get(ev, "leagueID", "league_id")

    props_raw: List[Dict[str, Any]] = []
    outcomes:  List[Dict[str, Any]] = []
    consensus: List[Dict[str, Any]] = []

    for m in _iter_markets(ev):
        odd_id   = _get(m, "oddID", "odd_id", "marketID", "id")
        stat_id  = _get(m, "statID", "stat_id")
        player_id= _get(m, "playerID", "player_id")
        period_id= _get(m, "periodID", "period_id")
        bet_type = _get(m, "betTypeID", "bet_type_id")
        side_id  = _get(m, "sideID", "side_id")
        line     = _get(m, "line", "lineValue", "value")
        # consensus
        cons = m.get("consensus") or {}
        fair_odds   = _get(cons, "fairOdds", "fair_odds")
        book_odds   = _get(cons, "bookOdds", "book_odds")
        cons_prob   = _get(cons, "consensusProbability", "consensus_probability")
        if fair_odds is not None or book_odds is not None or cons_prob is not None:
            consensus.append({
                "event_id":          eid,
                "odd_id":            odd_id,
                "league_id":         league_id,
                "fair_odds":         fair_odds,
                "book_odds":         book_odds,
                "consensus_probability": cons_prob,
                "snapshot_time":     snapshot_time,
            })
        # per-book odds
        for b in _iter_books(m):
            book_id = _get(b, "bookmakerID", "bookmaker_id", "book_id", "id")
            for o in _iter_outcomes(b):
                price  = _get(o, "price", "odds", "americanOdds", "american_odds")
                sel_id = _get(o, "selectionID", "selection_id", "outcomeID")
                side   = _get(o, "side", "name", "sideID")
                row = {
                    "event_id":      eid,
                    "league_id":     league_id,
                    "odd_id":        odd_id,
                    "stat_id":       stat_id,
                    "player_id":     player_id,
                    "period_id":     period_id,
                    "bet_type_id":   bet_type,
                    "side":          side or side_id,
                    "line":          line,
                    "price":         price,
                    "book_id":       book_id,
                    "selection_id":  sel_id,
                    "snapshot_time": snapshot_time,
                }
                props_raw.append(row)
                # If SGO already settled this outcome, store it separately
                settled = _get(o, "settlementStatus", "settlement_status",
                               "status", "outcomeStatus")
                is_winner = _get(o, "isWinner", "is_winner", "won")
                if settled is not None or is_winner is not None:
                    outcomes.append({
                        "event_id":      eid,
                        "odd_id":        odd_id,
                        "book_id":       book_id,
                        "selection_id":  sel_id,
                        "side":          side or side_id,
                        "line":          line,
                        "price":         price,
                        "settlement":    settled,
                        "is_winner":     is_winner,
                        "snapshot_time": snapshot_time,
                    })
    return {"props_raw": props_raw, "outcomes": outcomes, "consensus": consensus}


# ───────────────────────────────────────────────────── player & team stats
def extract_player_stats(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    eid = _get(ev, "eventID", "event_id", "id")
    league_id = _get(ev, "leagueID", "league_id")
    game_date = _as_iso(_get(ev, "startTime", "commenceTime", "commence_time"))
    if isinstance(game_date, str):
        game_date = game_date[:10]
    out: List[Dict[str, Any]] = []
    raw = (ev.get("playerStats") or ev.get("players") or
           ev.get("playerResults") or [])
    for ps in raw:
        out.append({
            "event_id":   eid,
            "league_id":  league_id,
            "game_date":  game_date,
            "player_id":  _get(ps, "playerID", "player_id", "id"),
            "player_name": _get(ps, "playerName", "name"),
            "team_id":    _get(ps, "teamID", "team_id"),
            "stats":      _get(ps, "stats", "statistics", default={}),
        })
    return out


def extract_team_stats(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    eid = _get(ev, "eventID", "event_id", "id")
    league_id = _get(ev, "leagueID", "league_id")
    raw = ev.get("teamStats") or ev.get("teamResults") or []
    out: List[Dict[str, Any]] = []
    for ts in raw:
        out.append({
            "event_id":  eid,
            "league_id": league_id,
            "team_id":   _get(ts, "teamID", "team_id"),
            "stats":     _get(ts, "stats", "statistics", default={}),
        })
    return out


# ────────────────────────────────────────────────────────── player registry
def extract_player_registry_entries(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull player identity rows out of player_stats so we can backfill sgo_players."""
    league_id = _get(ev, "leagueID", "league_id")
    sport_id  = _get(ev, "sportID", "sport_id")
    out: List[Dict[str, Any]] = []
    raw = (ev.get("playerStats") or ev.get("players") or [])
    for ps in raw:
        pid = _get(ps, "playerID", "player_id", "id")
        if not pid:
            continue
        out.append({
            "player_id":   pid,
            "player_name": _get(ps, "playerName", "name"),
            "team_id":     _get(ps, "teamID", "team_id"),
            "league_id":   league_id,
            "sport_id":    sport_id,
        })
    return out

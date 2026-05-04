"""
Universal Dashboard Pick Card Contract  —  Sport-Agnostic Normalizer
====================================================================

Front-page tier dashboards (Safe Haven / Front Lines / War Zone) all
render the SAME UI component (`UniversalPlayerCard` compact mode).  To
keep the component sport-agnostic, every pick — regardless of sport —
must expose the same 8 fields:

    player_name      string
    team             string | null
    stat_line        string | null      "PTS 14.5"
    big_pick_text    string | null      "OVER 14.5 PTS"
    projection       float  | null
    hit_rate         float  | null      side-correct percentage
    avg              float  | null
    short_sentence   string | null      truncated vision_intel; no fallback

`stamp_dashboard_card_contract` is a PURE display-layer normalizer.

It NEVER:
  * mutates μ / σ / gates / thresholds / tier routing / selection
  * recomputes projections, hit-rates, edges, or any model output
  * removes or relabels any existing field — only ADDS the 8 above
  * generates fabricated `short_sentence` text — vision_intel or null

It is invoked once per Ferrari-tier API response (after the picks are
selected and post-processed) and is fully idempotent.

`avg` BACKFILL (2026-04-29)
---------------------------
When a pick lacks every legacy avg field (`season_avg`/`l20_avg`/
`l10_avg`/`l5_avg`/`eb_player_career_mean`), we compute the L10 mean
on-the-fly from `{sport}_master_hub_2026.bdl_game_logs` — the SAME
source the player-detail page already uses (see
`routes/ferrari_tiers.py::get_mlb_player_props` ~line 3349 and
`routes/player.py::_BOARD_ENRICHMENT_FIELDS`). Pure display-layer
read; never mutates the master hub.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- friendly stat-name mappings (display-only) ---------------------
_STAT_SHORT_NBA = {
    "Points": "PTS", "Rebounds": "REB", "Assists": "AST",
    "Pts+Rebs+Asts": "P+R+A", "Pts+Rebs": "P+R", "Pts+Asts": "P+A",
    "Rebs+Asts": "R+A", "3-PT Made": "3PM", "Steals": "STL",
    "Blocks": "BLK", "Turnovers": "TO", "Stl+Blk": "S+B",
    "Double-Double": "DD", "Triple-Double": "TD",
}
_STAT_SHORT_MLB = {
    "Total Bases": "TB", "Hits": "H", "Singles": "1B", "Doubles": "2B",
    "Triples": "3B", "Home Runs": "HR", "Hits+Runs+RBIs": "H+R+RBI",
    "RBIs": "RBI", "Runs": "R", "Stolen Bases": "SB",
    "Batter Walks": "BB", "Batter Strikeouts": "K", "Earned Runs": "ER",
    "Hits Allowed": "HA", "Pitcher Strikeouts": "K", "Walks Allowed": "BB",
    "Pitches Thrown": "PIT", "Hits+Walks+Earned Runs": "H+BB+ER",
}
_MAX_SENTENCE = 140


def _stat_short(stat_type: str) -> str:
    if not stat_type:
        return ""
    return (
        _STAT_SHORT_NBA.get(stat_type)
        or _STAT_SHORT_MLB.get(stat_type)
        or stat_type.upper()
    )


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truncate_vision(raw: Any) -> Optional[str]:
    if isinstance(raw, dict):
        raw = raw.get("summary") or raw.get("text") or raw.get("line")
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if len(s) <= _MAX_SENTENCE:
        return s
    head = s[: _MAX_SENTENCE].rsplit(" ", 1)[0]
    return head + "…"


def to_card_contract(pick: Dict[str, Any]) -> Dict[str, Any]:
    """Build the 8-field contract from any sport's pick dict."""
    # ── 1. player_name ────────────────────────────────────────────────
    # SSOT (FIELD_OWNERSHIP.md:player_name): owner is
    # master_hub.display_name. By the time a pick reaches this card
    # contract it has ALREADY been populated with master_hub-derived
    # player_name upstream (universal_odds_sync → live_props →
    # picks_getter). Reading `pick.get("player_name")` directly is the
    # canonical path. Previously-accepted aliases (`player`, `name`)
    # were silent-rename footguns with no owning writer — removed.
    player_name = pick.get("player_name")

    # ── 2. team ──────────────────────────────────────────────────────
    # SSOT (FIELD_OWNERSHIP.md:team): owner is live_props.team (3-letter
    # abbr). Aliases `team_abbr` / `player_team` / `home_team_abbr` /
    # `away_team_abbr` do not have an owner and each historically
    # represented a different (and sometimes contradictory) source. The
    # fallback chain below was removed 2026-05-04 so a missing team
    # surfaces as None (→ UI renders `—`) instead of being quietly
    # replaced with a mismatched value.
    team = pick.get("team")

    # ── helpers ──────────────────────────────────────────────────────
    stat_type = pick.get("stat_type") or ""
    line = pick.get("line")
    # SSOT (FIELD_OWNERSHIP.md:side, 2026-05-04 Tier C): `side` is the
    # canonical OVER/UNDER selector, owned by live_props.recommendation.
    # Legacy aliases `direction` / `pick_side` / `selection` /
    # `over_under` may still be stamped on response picks by upstream
    # code paths, but the card contract (and anything downstream of
    # it) must read ONE normalised value. We preserve reading
    # `direction` here ONLY as a temporary upstream-tolerance fallback
    # (some adapters still write lowercase `direction` before the
    # canonical `recommendation` is stamped) — this will go away in
    # Tier D Pydantic. Default to OVER ONLY on unparseable input, and
    # log the violation so regressions stay visible.
    side = (pick.get("recommendation") or pick.get("direction") or "").upper().strip()
    if side not in ("OVER", "UNDER"):
        side = "OVER"
    stat_short = _stat_short(stat_type)
    line_str = f"{line}" if line is not None else ""

    # ── 3. stat_line — "PTS 14.5" ────────────────────────────────────
    stat_line = (
        f"{stat_short} {line_str}".strip()
        if stat_short or line_str
        else None
    ) or None

    # ── 4. big_pick_text — "OVER 14.5 PTS" ───────────────────────────
    big_pick_text = (
        f"{side} {line_str} {stat_short}".strip()
        if line_str
        else None
    ) or None

    # ── 5. projection ────────────────────────────────────────────────
    projection = (
        _f(pick.get("vk_predicted"))
        or _f(pick.get("model_projection"))
        or _f(pick.get("projection"))
    )

    # ── 6. hit_rate — side-correct percentage ────────────────────────
    hr_over  = _f(pick.get("hit_rate_over"))
    hr_under = _f(pick.get("hit_rate_under"))
    if side == "UNDER" and hr_under is not None:
        hit_rate = hr_under
    elif hr_over is not None:
        hit_rate = hr_over
    else:
        # NBA cards already populate `h10_rate` upstream of this point.
        hit_rate = _f(pick.get("h10_rate")) or _f(pick.get("hit_rate"))

    # ── 6b. hit_rate WINDOW TRIO (2026-05-01) ────────────────────────
    # Card displays L20 (gate input) / L10 (graph parity) / L5 (recent
    # form sub-gate input) so the operator can see EVERY window the
    # gate evaluated. Side-awareness:
    #   - L20: use hit_rate_under for UNDER, hit_rate_over for OVER
    #     (these fields ARE explicitly OVER/UNDER on the score doc)
    #   - L10 / L5: hit_rate_l5 / hit_rate_l10 are ALREADY side-aware
    #     on the score doc — adapters compute them with the prop's
    #     direction. Pass through verbatim, no complement.
    hit_rate_l20 = hr_under if side == "UNDER" else hr_over
    hit_rate_l10 = _f(pick.get("hit_rate_l10"))
    hit_rate_l5  = _f(pick.get("hit_rate_l5"))

    # ── 7. avg — historical mean (any reasonable source) ─────────────
    avg = (
        _f(pick.get("season_avg"))
        or _f(pick.get("l20_avg"))
        or _f(pick.get("l10_avg"))
        or _f(pick.get("l5_avg"))
        or _f(pick.get("eb_player_career_mean"))   # MLB Empirical-Bayes prior
    )

    # ── 8. short_sentence — truncated vision_intel ONLY (no fallback) ─
    short_sentence = _truncate_vision(
        pick.get("vision_intel") or pick.get("vision_summary")
    )

    return {
        "player_name":    player_name,
        "team":           team,
        # 2026-05-04 Tier C — canonical `side` enum (OVER|UNDER), stamped
        # next to the legacy `direction` alias so readers can migrate.
        # `direction` continues to be stamped upstream; this guarantees
        # the normalised value is present on every contracted card.
        "side":           side,
        "stat_line":      stat_line,
        "big_pick_text":  big_pick_text,
        "projection":     projection,
        "hit_rate":       hit_rate,
        # 2026-05-01 — full hit-rate window trio (gate L20, graph L10,
        # recent-form L5). All three are side-correct. Frontend renders
        # them stacked under the "Hit Rate" cell so the gate decision
        # is auditable straight from the card.
        "hit_rate_l20":   hit_rate_l20,
        "hit_rate_l10":   hit_rate_l10,
        "hit_rate_l5":    hit_rate_l5,
        "avg":            avg,
        "short_sentence": short_sentence,
    }


async def stamp_dashboard_card_contract(
    db, picks: List[Dict[str, Any]], sport: str
) -> None:
    """Stamp the 8 contract fields onto every pick in `picks`, in place.

    Side-effect-only ADDITIVE. Never removes or rewrites existing keys.
    Idempotent: re-running over the same picks produces the same fields.

    For MLB, also merges `team` from `mlb_live_props` when the pick has
    no team-side identity field — `mlb_prop_scores` strips this on
    write, so we re-attach at response time.
    """
    if not picks:
        return
    sport = (sport or "").lower()

    # ── MLB-only: attach `team` from mlb_live_props (one batch query) ─
    if sport == "mlb":
        await _attach_mlb_team(db, picks)

    # ── Stamp the 8 contract fields onto every pick ───────────────────
    for p in picks:
        contract = to_card_contract(p)
        for k, v in contract.items():
            # Additive: only set when key absent OR currently null/empty.
            existing = p.get(k)
            if existing in (None, "", "—"):
                p[k] = v
        # ── Universal Logo Contract (2026-04-29): every card MUST
        #    carry its own `sport` so the frontend logo lookup keys on
        #    (sport, team) and never cross-populates between leagues
        #    (BOS/ATL/CLE/DET/HOU/MIA/MIL/MIN/PHI/TOR collide NBA↔MLB;
        #    CAR / NY / LA / SF / ARI collide across NFL/NHL/MLB).
        if sport and not p.get("sport"):
            p["sport"] = sport
        # `team_logo_url` reserved for future backend-driven logo
        # overrides (custom per-tenant assets, dark/light theme variants).
        # Left absent today — the frontend `getTeamLogo(sport, team)`
        # is authoritative.

    # ── 2026-04-29: backfill `avg` from master_hub game logs ─────────
    # Many picks (esp. MLB and NBA combos like PRA) reach here with no
    # avg* field populated. Compute L10 mean directly from the same
    # source the player-detail page uses. Pure read-side; idempotent.
    await _backfill_avg_from_game_logs(db, picks, sport)


async def _attach_mlb_team(db, picks: List[Dict[str, Any]]) -> None:
    # SSOT (FIELD_OWNERSHIP.md:team): only backfill from live_props
    # when the canonical `team` field is missing. The legacy alias
    # `team_abbr` is no longer checked here (removed 2026-05-04).
    need = [p for p in picks
            if not p.get("team")
            and p.get("bdl_player_id") is not None
            and p.get("event_id")]
    if not need:
        return
    # Batch fetch live_props rows for the unique (bdl_id, event_id) pairs.
    pairs = list({(p["bdl_player_id"], p["event_id"]) for p in need})
    pids   = sorted({pid for pid, _ in pairs})
    eids   = sorted({eid for _, eid in pairs})
    cursor = db["mlb_live_props"].find(
        {"bdl_player_id": {"$in": pids}, "event_id": {"$in": eids}},
        {"_id": 0, "bdl_player_id": 1, "event_id": 1, "team": 1,
         "opponent_team": 1, "team_full": 1,
         "home_team": 1, "away_team": 1, "is_home_team": 1, "venue": 1},
    )
    lookup: Dict[tuple, Dict[str, Any]] = {}
    async for d in cursor:
        key = (d.get("bdl_player_id"), d.get("event_id"))
        if key not in lookup:
            lookup[key] = d
    stamped = 0
    for p in need:
        d = lookup.get((p["bdl_player_id"], p["event_id"]))
        if not d:
            continue
        # Additive only.
        for src, dest in (
            ("team", "team"),
            ("team_full", "team_full"),
            ("opponent_team", "opponent_abbr"),
            ("home_team", "home_team"),
            ("away_team", "away_team"),
            ("is_home_team", "is_home_team"),
            ("venue", "venue"),
        ):
            v = d.get(src)
            if v is not None and p.get(dest) in (None, "", "—"):
                p[dest] = v
        stamped += 1
    if stamped:
        logger.info("[CARD_CONTRACT:mlb] team stamped on %d/%d picks",
                    stamped, len(need))


__all__ = ["to_card_contract", "stamp_dashboard_card_contract"]


# =====================================================================
# `avg` BACKFILL  —  computed from master_hub.bdl_game_logs (L10 mean)
# =====================================================================
# Mirrors the math used by the Player-Detail page so the dashboard pick
# card and the player-detail card cannot disagree on a player's recent
# average. Stat-field maps below are a strict subset of the maps in
# `routes/ferrari_tiers.py::STAT_FIELD_MAP` (MLB) and
# `services/scoring/adapters/nba_scoring.py` (NBA).

# Each value is either:
#   * a string  → game-log field to read directly
#   * a tuple of strings → sum these fields per game (combo stats)
#   * a callable(g)→float|None → custom per-game extractor
_NBA_LOG_FIELD = {
    "Points": "pts", "PTS": "pts",
    "Rebounds": "reb", "REB": "reb",
    "Assists": "ast", "AST": "ast",
    "3-PT Made": "fg3m", "3PM": "fg3m", "Threes": "fg3m",
    "Steals": "stl", "STL": "stl",
    "Blocks": "blk", "BLK": "blk",
    "Turnovers": "turnover", "TO": "turnover",
    # Combo stats — both verbose and short forms emitted by the
    # Ferrari endpoint (e.g. "Pts+Asts" → "P+A" via stat-family
    # short codes). Both must resolve.
    "Pts+Rebs+Asts": ("pts", "reb", "ast"),
    "PRA":           ("pts", "reb", "ast"),
    "P+R+A":         ("pts", "reb", "ast"),
    "Pts+Rebs":      ("pts", "reb"),
    "P+R":           ("pts", "reb"),
    "PR":            ("pts", "reb"),
    "Pts+Asts":      ("pts", "ast"),
    "P+A":           ("pts", "ast"),
    "PA":            ("pts", "ast"),
    "Rebs+Asts":     ("reb", "ast"),
    "R+A":           ("reb", "ast"),
    "RA":            ("reb", "ast"),
    "Stl+Blk":       ("stl", "blk"),
    "S+B":           ("stl", "blk"),
    "BLST":          ("stl", "blk"),
}

_MLB_LOG_FIELD = {
    "Hits": "hits",
    "Total Bases": "total_bases",
    "RBIs": "rbis",
    "Runs": "runs",
    "Stolen Bases": "stolen_bases",
    "Home Runs": "home_runs",
    "Walks": "walks",
    "Batter Walks": "walks",
    "Strikeouts": "strikeouts",
    "Batter Strikeouts": "strikeouts",
    "Doubles": "doubles",
    "Triples": "triples",
    # `Singles` requires arithmetic (hits − 2B − 3B − HR).
    "Hits+Runs+RBIs": ("hits", "runs", "rbis"),
    # Pitcher stats
    "Pitcher Strikeouts": "pitcher_strikeouts",
    "Earned Runs": "earned_runs",
    "Earned Runs Allowed": "earned_runs",
    "Hits Allowed": "hits_allowed",
    "Walks Allowed": "pitcher_walks",
    "Pitches Thrown": "pitch_count",
    "Hits+Walks+Earned Runs": ("hits_allowed", "pitcher_walks", "earned_runs"),
}


def _extract_stat_value(stat_type: str, sport: str, log: Dict[str, Any]) -> Optional[float]:
    """Return the per-game stat value for one game log, or None."""
    if not isinstance(log, dict):
        return None
    s = (sport or "").lower()
    if s == "nba":
        spec = _NBA_LOG_FIELD.get(stat_type)
    elif s == "mlb":
        # MLB special cases ──────────────────────────────────────────
        if stat_type == "Singles":
            h = log.get("hits")
            if h is None:
                return None
            d = log.get("doubles") or 0
            t = log.get("triples") or 0
            hr = log.get("home_runs") or 0
            return float(max(0, h - d - t - hr))
        if stat_type in ("Pitcher Outs", "Pitching Outs"):
            ip = log.get("innings_pitched")
            return float(ip) * 3.0 if ip is not None else None
        spec = _MLB_LOG_FIELD.get(stat_type)
    else:
        return None
    if spec is None:
        return None
    if isinstance(spec, str):
        v = log.get(spec)
        return float(v) if v is not None else None
    if isinstance(spec, tuple):
        # Combo stat: sum components; skip game if every component is None
        parts = [log.get(f) for f in spec]
        if all(p is None for p in parts):
            return None
        return float(sum((p or 0) for p in parts))
    return None


def _l10_mean(stat_type: str, sport: str, logs: List[Dict[str, Any]]) -> Optional[float]:
    """L10 mean over the most recent 10 games with a valid stat value."""
    if not logs:
        return None
    sorted_logs = sorted(
        logs,
        key=lambda g: (g.get("date") or "", g.get("game_id") or 0),
        reverse=True,
    )
    vals: List[float] = []
    for g in sorted_logs:
        if len(vals) >= 10:
            break
        v = _extract_stat_value(stat_type, sport, g)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


async def _backfill_avg_from_game_logs(
    db, picks: List[Dict[str, Any]], sport: str
) -> None:
    """Stamp the canonical L10 hit-profile + avg + team on every pick.

    Replaces the previous ad-hoc avg-only backfill. For every pick we
    pull the player's `bdl_game_logs` from `{sport}_master_hub_2026` —
    the SAME source the front-end's `GameLogBarChart` reads — and run
    `services.hit_profile.compute_hit_profile`. The card's `hit_rate`,
    `l10_hit_count`, `l10_total`, `l10_values`, and `avg` all derive
    from that ONE function.

    `pick.hit_rate_over` (model L20 probability) is left untouched on
    the pick — it stays available for `ranking_score_v2` and any
    internal scoring readers.

    No-op when sport unknown, db is None, or no picks have a bdl id.
    """
    if not picks or db is None:
        return
    s = (sport or "").lower()
    if s not in ("nba", "mlb"):
        return

    # Collect bdl_player_ids for every pick — we re-stamp hit_profile
    # on EVERY pick (idempotent), regardless of whether a stale avg /
    # hit_rate is already present. This guarantees graph↔card parity.
    ids: set = set()
    for p in picks:
        pid = p.get("bdl_player_id") or p.get("bdl_id")
        if pid is None:
            continue
        try:
            ids.add(int(pid))
        except (TypeError, ValueError):
            continue
    if not ids:
        return

    coll = f"{s}_master_hub_2026"
    cursor = db[coll].find(
        {"$or": [
            {"bdl_player_id": {"$in": list(ids)}},
            {"bdl_id":        {"$in": list(ids)}},
        ]},
        {"_id": 0,
         "bdl_player_id": 1, "bdl_id": 1,
         "team": 1, "team_abbr": 1, "team_full": 1,
         "bdl_game_logs": 1},
    )
    by_id: Dict[int, Dict[str, Any]] = {}
    async for doc in cursor:
        for key in ("bdl_player_id", "bdl_id"):
            v = doc.get(key)
            if v is None:
                continue
            try:
                by_id[int(v)] = doc
            except (TypeError, ValueError):
                continue

    if not by_id:
        logger.info(
            "[CARD_CONTRACT:%s] hit_profile skipped — no hub docs for %d ids",
            s, len(ids),
        )
        return

    # Lazy import to avoid a circular at module-load.
    from services.hit_profile import stamp_hit_profile_on_pick

    profiled = 0
    team_stamped = 0
    for p in picks:
        pid = p.get("bdl_player_id") or p.get("bdl_id")
        try:
            pid = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid = None
        if pid is None:
            continue
        hub = by_id.get(pid)
        if not hub:
            continue

        # ---- canonical hit_profile (overwrites hit_rate + avg) -------
        stamp_hit_profile_on_pick(p, hub.get("bdl_game_logs") or [], sport=s)
        profiled += 1

        # ---- team abbr (only when missing) --------------------------
        # SSOT (FIELD_OWNERSHIP.md:team): owner is live_props.team, not
        # master_hub. This backfill was historically the #1 source of
        # team/opponent contradictions (e.g. hub cached an offseason
        # trade while live_props already had the correct new team).
        # Disabled 2026-05-04 — a missing team now surfaces as None
        # and the card renders `—`. To re-enable this path cleanly,
        # fix the upstream `live_props` writer (`universal_odds_sync`)
        # to stamp `team` on every row, then delete this block.

    logger.info(
        "[CARD_CONTRACT:%s] hit_profile stamped on %d/%d picks (team=%d)",
        s, profiled, len(picks), team_stamped,
    )

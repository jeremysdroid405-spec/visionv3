"""services/team_live_xgb_scorer.py

Apply the trained team-prop XGB models (built by
`scripts/sgo/train_team_xgb.py`) to live rows in `team_prop_scores`.

After `passthrough_team_live_to_scores` copies rows from
`team_live_props` into `team_prop_scores` with model fields nulled,
this scorer joins each row with the latest pre-game rolling stats from
`team_model_features` (for both the team and its opponent), classifies
the row's `market_category` from `market_key`, runs the batched
predictor, and writes back the scored fields:

    model_probability, implied_probability, edge, vision_score,
    market_category, model_version, gate_reasons, intel_score (mirror)

The collection's compound unique key includes `model_version` and
`gate_config_version`. We update IN PLACE by matching on
`(event_id, team_id, market, line, side, book)` so the existing rows
gain model fields without creating duplicates.

Idempotent: re-running is cheap; rows already scored with the current
model_version are skipped (filtered out at query time).

DOES NOT call any external API. Pure local Mongo + pickle artifacts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import DeleteOne, UpdateOne

from services.team_xgb_loader import score_team_props_batch, VERSION

logger = logging.getLogger(__name__)

SCORES_COLL = "team_prop_scores"
FEATURES_COLL = "team_model_features"

# Matchups carry home/away mapping. Keep parity with
# team_prop_tier_service.MATCHUP_COLL_BY_SPORT.
MATCHUP_COLL_BY_SPORT: Dict[str, str] = {
    "mlb":   "team_matchups",
    "nba":   "team_matchups",
    "nfl":   "nfl_matchups",
    "ncaaf": "ncaaf_matchups",
}


# ─── pure helpers ───────────────────────────────────────────────────
def classify_market_category(market_key: Optional[str]) -> Optional[str]:
    """Map `market_key` token to the model's market_category bucket.

    Rules derived from `team_model_prop_features`:
      * `<stat>-<all|home|away>-game-ml-*`   → h2h
      * `<stat>-<home|away>-game-sp-*`       → spread
      * `<stat>-all-game-ou-*`               → game_total
      * `<stat>-<home|away>-game-ou-*`       → team_total
    """
    if not isinstance(market_key, str):
        return None
    s = market_key.lower()
    if "-ml-" in s:
        return "h2h"
    if "-sp-" in s:
        return "spread"
    if "-ou-" in s:
        # game_total has "-all-" before "-game-ou-"; team_total has
        # "-home-" or "-away-" before "-game-ou-".
        if "-all-game-ou-" in s:
            return "game_total"
        if "-home-game-ou-" in s or "-away-game-ou-" in s:
            return "team_total"
    return None


def _normalize_side(market_key: str, side_raw: Optional[str]) -> str:
    """Match what the trainer saw. row_to_features treats
    ('OVER','HOME') as is_over=1.0; everything else as 0.0.
    The market_key already encodes side in its `-home-` / `-away-` /
    `-over-` / `-under-` slot, so we pass that through cleanly.
    """
    s = (side_raw or "").upper()
    if s in ("OVER", "HOME"):
        return s
    if s in ("UNDER", "AWAY"):
        return s
    # Fallback: read from market_key.
    mk = (market_key or "").lower()
    if mk.endswith("-over") or "-home-game-" in mk:
        return "OVER"
    if mk.endswith("-under") or "-away-game-" in mk:
        return "UNDER"
    return s


# ─── feature lookup ─────────────────────────────────────────────────
async def _build_team_features_lookup(
    db, sport: str, game_dates: List[str],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """For each (team_id, game_date) needed, find the most recent
    `team_model_features` row with `as_of_date < game_date`. Returns
    a `(team_id, game_date)` → feature-dict mapping ready for
    `row_to_features` (only the inner stat dict, no metadata).
    """
    if not game_dates:
        return {}
    # Pull all features for this sport up to the max game date in one
    # query, then bucket by team_id and pick the most recent as_of_date
    # ≤ each game_date.
    max_date = max(game_dates)
    cur = db[FEATURES_COLL].find(
        {"sport": sport, "as_of_date": {"$lt": max_date}},
        projection={"_id": 0},
    )
    by_team: Dict[str, List[Dict[str, Any]]] = {}
    async for f in cur:
        tid = f.get("team_id")
        if not tid:
            continue
        by_team.setdefault(tid, []).append(f)
    for rows in by_team.values():
        rows.sort(key=lambda r: r.get("as_of_date") or "")

    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for team_id, rows in by_team.items():
        for gd in set(game_dates):
            # Last row with as_of_date < gd.
            best = None
            for r in rows:
                if (r.get("as_of_date") or "") < gd:
                    best = r
                else:
                    break
            if best is not None:
                out[(team_id, gd)] = best
    return out


async def _build_matchup_lookup(
    db, sport: str, event_ids: List[str],
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


# ─── main scoring path ─────────────────────────────────────────────
async def score_team_live_props(
    db, *, sport: str,
    rescore: bool = False,
    max_rows: int = 5000,
) -> Dict[str, Any]:
    """Apply trained XGB models to `team_prop_scores` rows for `sport`.

    Args:
        sport:   "mlb" | "nba" (NFL artifacts exist but live ingest
                  has limited coverage today; included for parity).
        rescore: When False (default), only score rows where
                 `model_probability` is None. When True, re-score every
                 row regardless of state.
        max_rows: Safety cap on per-call work.

    Returns an audit dict.
    """
    sport = (sport or "").lower()
    flt: Dict[str, Any] = {"sport": sport, "prop_type": "team"}
    if not rescore:
        flt["model_probability"] = None

    audit: Dict[str, Any] = {
        "sport":           sport,
        "model_version":   VERSION,
        "scanned":         0,
        "skipped_no_mc":   0,
        "skipped_no_feat": 0,
        "scored":          0,
        "updated":         0,
        "errors":          [],
        "ts":              datetime.now(timezone.utc).isoformat(),
    }

    rows: List[Dict[str, Any]] = []
    async for r in db[SCORES_COLL].find(flt, projection={"_id": 0}).limit(max_rows):
        rows.append(r)
    audit["scanned"] = len(rows)
    if not rows:
        logger.info(
            f"[team_live_scorer] sport={sport} no rows to score "
            f"(rescore={rescore})")
        return audit

    # Build feature + matchup lookups once.
    game_dates = sorted({r.get("game_date") for r in rows if r.get("game_date")})
    event_ids = list({r.get("event_id") for r in rows if r.get("event_id")})
    feats = await _build_team_features_lookup(db, sport, game_dates)
    matchups = await _build_matchup_lookup(db, sport, event_ids)

    # Assemble per-row enriched payloads.
    enriched: List[Dict[str, Any]] = []
    enriched_idx: List[int] = []
    for i, r in enumerate(rows):
        mc = classify_market_category(r.get("market_key") or r.get("market"))
        if mc is None:
            audit["skipped_no_mc"] += 1
            continue
        gd = r.get("game_date")
        tid = r.get("team_id")
        if not (gd and tid):
            audit["skipped_no_feat"] += 1
            continue

        # Identify opponent_team_id via matchup
        m = matchups.get(r.get("event_id") or "") or {}
        home_id = m.get("home_team_id")
        away_id = m.get("away_team_id")
        if tid == home_id:
            opp_id = away_id
            home_away = "home"
        elif tid == away_id:
            opp_id = home_id
            home_away = "away"
        else:
            opp_id = None
            home_away = None

        tf = feats.get((tid, gd))
        of = feats.get((opp_id, gd)) if opp_id else None
        if tf is None and of is None:
            audit["skipped_no_feat"] += 1
            continue

        enriched.append({
            "sport":              sport,
            "market_category":    mc,
            "line":               r.get("line"),
            "side":               _normalize_side(r.get("market_key") or "",
                                                    r.get("side")),
            "is_alternate":       r.get("is_alternate") or False,
            "home_away":          home_away,
            "odds":               r.get("odds"),
            "team_features":      tf or {},
            "opponent_features":  of or {},
            # carry the addressing info for the writeback step
            "_event_id":          r.get("event_id"),
            "_team_id":           tid,
            "_market":            r.get("market"),
            "_line":              r.get("line"),
            "_side":              r.get("side"),
            "_book":              r.get("book"),
            "_market_key":        r.get("market_key"),
        })
        enriched_idx.append(i)

    if not enriched:
        return audit

    # Batched inference.
    scores = score_team_props_batch(enriched)
    audit["scored"] = sum(1 for s in scores if s is not None)

    # 2026-06-02 — Stale-duplicate guard.
    #
    # The unique compound index (`event_id, team_id, market, line,
    # side, book, snapshot_iso, model_version, gate_config_version`)
    # allows BOTH an unscored row (`model_version=None`, from the
    # passthrough) AND a previously-scored row
    # (`model_version='team_xgb_v1'`) to coexist for the same natural
    # key. A naive `$set: {model_version: VERSION}` on the unscored
    # row triggers `E11000` because the resulting compound key
    # duplicates the scored sibling.
    #
    # Fix: for each unscored row we want to promote, first check if a
    # scored sibling already exists. If yes, the unscored row is a
    # stale passthrough duplicate — drop it. If no, promote it.
    # Either way the unique index stays consistent.
    natural_keys = [
        (p["_event_id"], p["_team_id"], p["_market"],
         p["_line"], p["_side"], p["_book"])
        for p in enriched
    ]
    scored_sibling_keys: set = set()
    if natural_keys:
        or_clauses = [
            {
                "event_id": k[0], "team_id": k[1], "market": k[2],
                "line":     k[3], "side":    k[4], "book":   k[5],
                "model_version": {"$ne": None},
            }
            for k in natural_keys
        ]
        async for r in db[SCORES_COLL].find(
            {"$or": or_clauses},
            projection={
                "_id": 0, "event_id": 1, "team_id": 1, "market": 1,
                "line": 1, "side": 1, "book": 1,
            },
        ):
            scored_sibling_keys.add((
                r.get("event_id"), r.get("team_id"), r.get("market"),
                r.get("line"), r.get("side"), r.get("book"),
            ))

    audit["stale_unscored_deleted"] = 0

    # Build bulk update ops; addressing key is the natural
    # `(event_id, team_id, market, line, side, book)` tuple. We DO NOT
    # touch the unique-index columns (model_version /
    # gate_config_version) on the existing rows — we only $set the
    # model output fields and a couple of derived display flags.
    ops: List = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for payload, score in zip(enriched, scores):
        if score is None:
            continue
        nk = (payload["_event_id"], payload["_team_id"],
              payload["_market"], payload["_line"],
              payload["_side"], payload["_book"])

        # Stale unscored duplicate: a scored sibling already exists
        # for this natural key. Drop the unscored row so the index is
        # clean. The scored sibling carries the canonical fields.
        if nk in scored_sibling_keys:
            ops.append(DeleteOne({
                "event_id":      payload["_event_id"],
                "team_id":       payload["_team_id"],
                "market":        payload["_market"],
                "line":          payload["_line"],
                "side":          payload["_side"],
                "book":          payload["_book"],
                "model_version": None,
            }))
            audit["stale_unscored_deleted"] += 1
            continue

        # Tier stays driven by the odds-bucket router in the
        # passthrough — we DO NOT override it here. The model fields
        # are additive.
        update = {
            "$set": {
                "market_category":    payload["market_category"],
                "stat_family":        payload["market_category"],
                "model_probability":  score["model_probability"],
                "implied_probability": score["implied_probability"],
                "edge":               score["edge"],
                "edge_pct":           (round(100.0 * score["edge"], 2)
                                          if score["edge"] is not None else None),
                "vision_score":       score["vision_score"],
                "intel_score":        score["vision_score"],
                "true_probability":   score["model_probability"],
                "confidence":         score["model_probability"],
                "model_version":      score["model_version"],
                "gate_reasons":       [],
                "team_model_pending": False,
                "scored_at":          now_iso,
            },
        }
        # Filter narrowed to `model_version: None` so the update can
        # only ever touch the unscored row. If somehow a scored
        # sibling slipped through the dedup pass, the filter won't
        # match and the op becomes a no-op (no E11000).
        ops.append(UpdateOne(
            {
                "event_id":      payload["_event_id"],
                "team_id":       payload["_team_id"],
                "market":        payload["_market"],
                "line":          payload["_line"],
                "side":          payload["_side"],
                "book":          payload["_book"],
                "model_version": None,
            },
            update,
            upsert=False,
        ))

    if not ops:
        return audit

    try:
        result = await db[SCORES_COLL].bulk_write(ops, ordered=False)
        audit["updated"] = (
            int(result.modified_count or 0)
            + int(getattr(result, "matched_count", 0) or 0))
        # `matched_count + modified_count` over-counts; prefer matched.
        audit["updated"] = int(getattr(result, "matched_count",
                                         result.modified_count or 0) or 0)
    except Exception as exc:  # noqa: BLE001
        audit["errors"].append(repr(exc))
        logger.exception(
            f"[team_live_scorer] sport={sport} bulk_write failed")

    logger.info(
        f"[team_live_scorer] sport={sport} "
        f"scanned={audit['scanned']} "
        f"skipped_no_mc={audit['skipped_no_mc']} "
        f"skipped_no_feat={audit['skipped_no_feat']} "
        f"scored={audit['scored']} "
        f"updated={audit['updated']}"
    )
    return audit

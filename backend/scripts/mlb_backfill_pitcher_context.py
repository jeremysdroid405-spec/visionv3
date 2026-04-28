"""
MLB Pick-History Pitcher-Context Backfill (SHADOW)
====================================================
Joins each settled / unsettled pick in `mlb_pick_history` to the
opposing starting pitcher (resolved post-hoc from `mlb_statcast_raw`),
looks up the pitcher's rolling features as-of the day BEFORE the pick's
game_date, and stamps the pick with:

    pitcher_id, pitcher_name, pitcher_p_throws, batter_stand,
    pitcher_xwOBA_allowed, pitcher_split_used,
    matchup_factor_shadow, pitcher_confidence_flag,
    pitcher_resolved_via, shadow_feature_version

This is **shadow-only** — μ / σ / edge / tp / vision_score / gates /
tier are not affected. matchup_factor_shadow is stored for analytics
and is NEVER read by the scoring pipeline.

Resolver order (high → low confidence):
  1. Statcast raw row matching (game_date, batter=mlb_id) →
     first-faced pitcher's MLBAM id  (only works for settled games)
  2. Live-prop's `opp_pitcher_id`    (currently always None — placeholder
     for when the upstream feed provides probable pitchers)
  3. None  (do NOT guess; spec rule)

Idempotent — re-running on already-stamped picks just re-computes the
same shadow values; outcomes (hit/result/actual) are untouched.
"""
from __future__ import annotations

import argparse, asyncio, logging, os, sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from services.mlb.identity import normalize_player_name, apply_alias

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mlb_backfill_pitcher_context")

PICK_COLL  = "mlb_pick_history"
RAW        = "mlb_statcast_raw"
PITCH_FEAT = "mlb_statcast_pitcher_features"
IDENTITY   = "mlb_player_identity_map"

LEAGUE_AVG_XWOBA  = 0.320       # Statcast historical league baseline
SHADOW_VERSION    = "pitcher_context_v1"
MATCHUP_CLAMP     = (0.85, 1.15)


# ---------------------------------------------------------------------------
def _clamp(x, lo, hi):
    if x is None: return None
    return max(lo, min(hi, x))


async def _build_pitcher_resolver(
        db) -> Dict[Tuple[int, str], Dict[str, Any]]:
    """For each (batter_id, game_date), find the FIRST pitch the
    batter saw → that's the starting pitcher in shadow analytics.
    Returns {(batter_id, game_date) → {pitcher_id, p_throws, stand}}.
    """
    # Group raw rows by (batter, game_date), keep the row with the
    # smallest at_bat_number / pitch_number — that pitcher started.
    keyed: Dict[Tuple[int, str], Dict[str, Any]] = {}
    proj = {"_id": 0, "batter": 1, "pitcher": 1, "pitcher_name": 1,
             "p_throws": 1, "stand": 1, "game_date": 1,
             "at_bat_number": 1, "pitch_number": 1}
    n = 0
    async for d in db[RAW].find(
        {"batter": {"$ne": None}, "pitcher": {"$ne": None}}, proj):
        bid = d.get("batter")
        date = d.get("game_date")
        if bid is None or not date: continue
        key = (int(bid), date)
        ab = d.get("at_bat_number") or 9999
        pn = d.get("pitch_number") or 999
        slot = keyed.get(key)
        if slot is None or (ab, pn) < (slot["_ab"], slot["_pn"]):
            keyed[key] = {
                "_ab": ab, "_pn": pn,
                "pitcher_id": int(d["pitcher"]),
                "pitcher_name": d.get("pitcher_name"),
                "p_throws": d.get("p_throws"),
                "stand":    d.get("stand"),
            }
        n += 1
    logger.info(f"resolver: scanned {n:,} raw rows  "
                  f"·  resolved {len(keyed):,} (batter,date) keys")
    return keyed


async def _build_identity_map(db) -> Dict[str, Optional[int]]:
    """canonical_name → mlb_id (for resolving pick.player → batter_id)."""
    out: Dict[str, Optional[int]] = {}
    async for d in db[IDENTITY].find(
        {"confidence": {"$gte": 0.92},
         "statcast_id": {"$ne": None}},
        {"_id": 0, "normalized_name": 1, "statcast_id": 1}):
        out[d["normalized_name"]] = int(d["statcast_id"])
    return out


async def _load_pitcher_features(db) -> Dict[Tuple[int, str], Dict[str, Any]]:
    """(pitcher_id, game_date) → feature row."""
    out: Dict[Tuple[int, str], Dict[str, Any]] = {}
    proj = {"_id": 0, "pitcher_id": 1, "pitcher_name": 1, "p_throws": 1,
             "game_date": 1, "rolling_30": 1, "split_30_vs_L": 1,
             "split_30_vs_R": 1}
    async for d in db[PITCH_FEAT].find({}, proj):
        out[(int(d["pitcher_id"]), d["game_date"])] = d
    return out


def _pitcher_feat_before(by_pd: Dict[Tuple[int, str], Dict[str, Any]],
                          pid: int, target_date: str) -> Optional[Dict[str, Any]]:
    """Most-recent pitcher feature row STRICTLY BEFORE target_date —
    no future leakage. Mirrors `_statcast_for()` semantics on the
    batter side."""
    candidates = [(d, v) for (p2, d), v in by_pd.items()
                   if p2 == pid and d < target_date]
    if not candidates: return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _pick_split(feat: Dict[str, Any], batter_stand: Optional[str]
                 ) -> Tuple[Optional[float], str]:
    """Choose the split xwOBA-allowed bucket per spec:
       L-batter vs R-pitcher → split_30_vs_L
       R-batter vs L-pitcher → split_30_vs_R
       fallback              → rolling_30.xwOBA_allowed"""
    p_throws = feat.get("p_throws")
    if batter_stand and p_throws and batter_stand != p_throws:
        # Opposite-handed matchup → use the pitcher's split AGAINST
        # the batter's handedness side.
        split_node = (feat.get(f"split_30_vs_{batter_stand}") or {})
        x = split_node.get("xwOBA_allowed")
        if x is not None and (split_node.get("batted_ball_events") or 0) >= 10:
            return x, f"vs_{batter_stand}"
    base = (feat.get("rolling_30") or {}).get("xwOBA_allowed")
    return base, "overall_30"


# ---------------------------------------------------------------------------
async def backfill(db, *, since: Optional[str] = None,
                    dry_run: bool = False) -> Dict[str, int]:
    resolver       = await _build_pitcher_resolver(db)
    name_to_mlb_id = await _build_identity_map(db)
    pitcher_feats  = await _load_pitcher_features(db)
    logger.info(f"loaded resolver={len(resolver):,}  "
                  f"identity={len(name_to_mlb_id):,}  "
                  f"pitcher_feats={len(pitcher_feats):,}")

    q: Dict[str, Any] = {}
    if since: q["game_date"] = {"$gte": since}
    cursor = db[PICK_COLL].find(q, {
        "_id": 1, "player": 1, "game_date": 1})
    from pymongo import UpdateOne
    ops: List[Any] = []
    n_total = n_resolved = n_no_batter_id = n_no_pitcher = n_no_feat = 0
    n_high_conf = n_low_conf = 0

    async for pick in cursor:
        n_total += 1
        nn = apply_alias(normalize_player_name(pick.get("player")))
        if not nn: continue
        bid = name_to_mlb_id.get(nn)
        date = pick.get("game_date")
        if bid is None or not date:
            n_no_batter_id += 1; continue

        pres = resolver.get((bid, date))
        if pres is None:
            n_no_pitcher += 1
            update = {
                "pitcher_id":             None,
                "pitcher_name":           None,
                "pitcher_p_throws":       None,
                "batter_stand":           None,
                "pitcher_xwOBA_allowed":  None,
                "pitcher_split_used":     None,
                "matchup_factor_shadow":  None,
                "pitcher_confidence_flag": None,
                "pitcher_resolved_via":   "no_statcast_match",
                "shadow_feature_version": SHADOW_VERSION,
            }
        else:
            pid = pres["pitcher_id"]
            feat = _pitcher_feat_before(pitcher_feats, pid, date)
            if feat is None:
                n_no_feat += 1
                update = {
                    "pitcher_id":             pid,
                    "pitcher_name":           pres.get("pitcher_name"),
                    "pitcher_p_throws":       pres.get("p_throws"),
                    "batter_stand":           pres.get("stand"),
                    "pitcher_xwOBA_allowed":  None,
                    "pitcher_split_used":     None,
                    "matchup_factor_shadow":  None,
                    "pitcher_confidence_flag": "low",
                    "pitcher_resolved_via":   "first_pitch_no_history",
                    "shadow_feature_version": SHADOW_VERSION,
                }
            else:
                xw, split_label = _pick_split(feat, pres.get("stand"))
                conf_flag = (feat.get("rolling_30") or {}).get(
                    "confidence_flag") or "low"
                if conf_flag == "high": n_high_conf += 1
                else:                   n_low_conf += 1
                if xw is not None:
                    factor = _clamp(xw / LEAGUE_AVG_XWOBA, *MATCHUP_CLAMP)
                else:
                    factor = None
                n_resolved += 1
                update = {
                    "pitcher_id":             pid,
                    "pitcher_name":           feat.get("pitcher_name")
                                                or pres.get("pitcher_name"),
                    "pitcher_p_throws":       feat.get("p_throws")
                                                or pres.get("p_throws"),
                    "batter_stand":           pres.get("stand"),
                    "pitcher_xwOBA_allowed":  xw,
                    "pitcher_split_used":     split_label,
                    "matchup_factor_shadow":  factor,
                    "pitcher_confidence_flag": conf_flag,
                    "pitcher_resolved_via":   "first_pitch_statcast",
                    "shadow_feature_version": SHADOW_VERSION,
                }
        if not dry_run:
            ops.append(UpdateOne({"_id": pick["_id"]}, {"$set": update}))

    real_updates = 0
    if ops and not dry_run:
        BATCH = 1000
        for i in range(0, len(ops), BATCH):
            r = await db[PICK_COLL].bulk_write(ops[i:i + BATCH], ordered=False)
            real_updates += (r.modified_count or 0)

    logger.info(f"scanned={n_total:,}  resolved={n_resolved:,}  "
                  f"no_batter_id={n_no_batter_id:,}  "
                  f"no_pitcher_match={n_no_pitcher:,}  "
                  f"no_pitcher_feat={n_no_feat:,}  "
                  f"high_conf={n_high_conf:,}  low_conf={n_low_conf:,}  "
                  f"updates_written={real_updates:,}")
    return {"scanned": n_total, "resolved": n_resolved,
             "no_batter_id": n_no_batter_id, "no_pitcher": n_no_pitcher,
             "no_pitcher_feat": n_no_feat,
             "high_conf": n_high_conf, "low_conf": n_low_conf,
             "updates_written": real_updates}


async def _amain():
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None)
    p.add_argument("--dry", action="store_true")
    args = p.parse_args()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await backfill(db, since=args.since, dry_run=args.dry)


if __name__ == "__main__":
    asyncio.run(_amain())

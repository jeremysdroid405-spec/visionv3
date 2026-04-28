"""
MLB Statcast Feature Builder
============================
Reads `mlb_statcast_raw` and emits one document per (player_id, game_date)
into `mlb_statcast_player_features` with rolling 7 / 14 / 30-day +
season windows. Idempotent on (player_id, game_date).

Each window includes (per spec):
  xwOBA, wOBA, hard_hit_rate, barrel_rate,
  avg_exit_velocity, avg_launch_angle, sweet_spot_rate,
  k_rate, whiff_rate, contact_rate,
  batted_ball_events, plate_appearances

Definitions (Baseball Savant convention):
  PA          = events ∈ {everything but None / `pickoff_*` / `caught_stealing_*`}
                — practical proxy: count of LAST pitch of each at-bat (events != None)
                                   PLUS strike-outs/walks/etc.
                  Equivalent: nunique (game_pk, at_bat_number) per batter.
  BBE         = batted-ball events: events with a `bb_type` label.
  Hard Hit    = batted-ball event with launch_speed >= 95.0 mph.
  Barrel      = `bb_type == 'barrel'` (Statcast's official tag) when
                present, else launch_speed*1.5 - launch_angle - 117 ≥ 0
                with launch_speed ≥ 98 (rough Savant heuristic).
  Sweet Spot  = batted ball with launch_angle in [8, 32].
  K           = events == 'strikeout'.
  Whiff       = description == 'swinging_strike' or 'swinging_strike_blocked'.
  Contact     = swings - whiffs.  Swings = description ∈
                {hit_into_play, foul, foul_tip, foul_pitchout,
                 swinging_strike, swinging_strike_blocked, swinging_pitchout}.
  xwOBA       = mean(estimated_woba_using_speedangle) over BBE
                + walk/HBP/K rates folded in via woba_value (Statcast's
                row-level value already encodes outcome wOBA).
                Practical: mean(woba_value) over PA-final pitches.
  wOBA        = same as xwOBA but using woba_value (actual outcome).

CLI:
    python -m scripts.mlb_statcast_build_features
    python -m scripts.mlb_statcast_build_features --since 2026-04-20
    python -m scripts.mlb_statcast_build_features --player 606466
"""
from __future__ import annotations

import argparse, asyncio, logging, os, sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mlb_statcast_build_features")

RAW_COLLECTION = "mlb_statcast_raw"
FEAT_COLLECTION = "mlb_statcast_player_features"

# Pitch descriptions counted as "swing".
SWING_DESCRIPTIONS = {
    "hit_into_play", "foul", "foul_tip", "foul_pitchout",
    "swinging_strike", "swinging_strike_blocked", "swinging_pitchout",
}
WHIFF_DESCRIPTIONS = {
    "swinging_strike", "swinging_strike_blocked", "swinging_pitchout",
}


def _f(v):
    if v is None: return None
    try: return float(v)
    except (TypeError, ValueError): return None


# ---------------------------------------------------------------------------
async def ensure_indexes(db) -> None:
    coll = db[FEAT_COLLECTION]
    await coll.create_index(
        [("player_id", 1), ("game_date", 1)],
        name="uniq_player_date", unique=True)
    await coll.create_index([("player_name", 1), ("game_date", 1)],
                              name="player_name_date")
    await coll.create_index([("game_date", 1)], name="game_date")


# ---------------------------------------------------------------------------
def _accumulate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate raw event rows for a single (player, window) into the
    feature dict. Operates on already-filtered rows."""
    if not rows:
        return {"plate_appearances": 0, "batted_ball_events": 0,
                "xwOBA": None, "wOBA": None,
                "hard_hit_rate": None, "barrel_rate": None,
                "avg_exit_velocity": None, "avg_launch_angle": None,
                "sweet_spot_rate": None,
                "k_rate": None, "whiff_rate": None, "contact_rate": None}

    # PA = count of unique (game_pk, at_bat_number).
    pas = {(r["game_pk"], r["at_bat_number"]) for r in rows
           if r.get("game_pk") is not None and r.get("at_bat_number") is not None}
    n_pa = len(pas)

    # BBE: rows with a bb_type tag.
    bbes = [r for r in rows if r.get("bb_type")]
    n_bbe = len(bbes)

    # Outcomes (per AB-final pitch — the row carrying `events`).
    final_rows = [r for r in rows if r.get("events")]
    woba_values = [_f(r.get("woba_value")) for r in final_rows]
    woba_values = [v for v in woba_values if v is not None]
    xwobas = [_f(r.get("estimated_woba_using_speedangle"))
              for r in final_rows]
    xwobas = [v for v in xwobas if v is not None]

    # Hard hit / barrels / sweet-spot — over BBE only.
    speeds = [_f(r.get("launch_speed")) for r in bbes]
    angles = [_f(r.get("launch_angle")) for r in bbes]
    n_hard = sum(1 for s in speeds if s is not None and s >= 95.0)

    # Barrel = bb_type == 'barrel' (Statcast tag, when present)
    n_barrel = sum(1 for r in bbes if (r.get("bb_type") or "") == "barrel")
    if n_barrel == 0:
        # Heuristic fallback (Savant ≥98 mph / launch_angle band).
        for r in bbes:
            ls = _f(r.get("launch_speed"))
            la = _f(r.get("launch_angle"))
            if ls is None or la is None: continue
            if ls >= 98.0 and (ls * 1.5 - la - 117.0) >= 0:
                n_barrel += 1

    n_sweet = sum(1 for la in angles
                   if la is not None and 8.0 <= la <= 32.0)
    speeds_v = [s for s in speeds if s is not None]
    angles_v = [la for la in angles if la is not None]

    # K / whiff / contact — over ALL rows.
    n_k = sum(1 for r in final_rows if (r.get("events") or "") == "strikeout")
    swings = [r for r in rows
              if (r.get("description") or "") in SWING_DESCRIPTIONS]
    whiffs = [r for r in rows
              if (r.get("description") or "") in WHIFF_DESCRIPTIONS]

    def _safe_div(num, den): return (num / den) if den else None
    def _avg(vs): return (sum(vs) / len(vs)) if vs else None

    return {
        "plate_appearances":  n_pa,
        "batted_ball_events": n_bbe,
        "xwOBA":              _avg(xwobas) if xwobas else None,
        "wOBA":               _avg(woba_values) if woba_values else None,
        "hard_hit_rate":      _safe_div(n_hard, n_bbe),
        "barrel_rate":        _safe_div(n_barrel, n_bbe),
        "avg_exit_velocity":  _avg(speeds_v),
        "avg_launch_angle":   _avg(angles_v),
        "sweet_spot_rate":    _safe_div(n_sweet, n_bbe),
        "k_rate":             _safe_div(n_k, n_pa),
        "whiff_rate":         _safe_div(len(whiffs), len(swings)),
        "contact_rate":       _safe_div(len(swings) - len(whiffs), len(swings)),
    }


# ---------------------------------------------------------------------------
async def _load_raw(db, since: Optional[str], player: Optional[int]
                    ) -> Dict[Tuple[int, str], List[Dict[str, Any]]]:
    """Load all raw rows (optionally bounded) into memory keyed by
    (batter_id, game_date)."""
    q: Dict[str, Any] = {"batter": {"$ne": None}}
    if since:  q["game_date"] = {"$gte": since}
    if player: q["batter"]    = int(player)
    by_pd: Dict[Tuple[int, str], List[Dict[str, Any]]] = defaultdict(list)
    proj = {"_id": 0, "game_pk": 1, "at_bat_number": 1, "pitch_number": 1,
            "batter": 1, "batter_name": 1, "game_date": 1,
            "events": 1, "description": 1, "bb_type": 1,
            "launch_speed": 1, "launch_angle": 1,
            "estimated_woba_using_speedangle": 1, "woba_value": 1}
    n = 0
    async for d in db[RAW_COLLECTION].find(q, proj):
        date = d.get("game_date")
        bid = d.get("batter")
        if not date or bid is None: continue
        by_pd[(int(bid), date)].append(d)
        n += 1
    logger.info(f"loaded {n:,} raw rows  ·  "
                  f"unique (batter,date) keys: {len(by_pd):,}")
    return by_pd


def _name_for_player(rows_for_player_all_dates) -> Optional[str]:
    """Pick the most-common normalized batter_name across this player's rows."""
    from collections import Counter
    names = [r.get("batter_name") for r in rows_for_player_all_dates
              if r.get("batter_name")]
    if not names: return None
    return Counter(names).most_common(1)[0][0]


async def build_features(db, *, since: Optional[str] = None,
                           player: Optional[int] = None,
                           dry_run: bool = False) -> Dict[str, int]:
    """For each (player_id, game_date) key, compute rolling 7 / 14 / 30
    + season features and upsert.

    Rolling windows are inclusive of the target game_date and look back
    N-1 days. So the 7-day window for game_date=2026-04-25 includes
    every event from 2026-04-19 through 2026-04-25.
    """
    if not dry_run:
        await ensure_indexes(db)

    by_pd = await _load_raw(db, since=since, player=player)
    if not by_pd:
        logger.info("no raw rows — nothing to build."); return {
            "computed": 0, "inserted": 0, "updated": 0, "errors": 0}

    # Player → { date_str → list[row] }, sorted by date.
    by_player: Dict[int, Dict[str, List[Dict[str, Any]]]] = defaultdict(dict)
    for (bid, dt), rows in by_pd.items():
        by_player[bid][dt] = rows

    from pymongo import UpdateOne
    ops: List[Any] = []
    n_computed = 0

    for bid, by_date in by_player.items():
        sorted_dates = sorted(by_date)
        all_rows_player: List[Dict[str, Any]] = []
        for d in sorted_dates: all_rows_player.extend(by_date[d])
        player_name = _name_for_player(all_rows_player)
        season = (sorted_dates[0][:4] if sorted_dates else None)

        # Season aggregate (computed once; identical for every game_date).
        season_feat = _accumulate(all_rows_player)

        for tgt in sorted_dates:
            target = date.fromisoformat(tgt)
            def _window_rows(days: int) -> List[Dict[str, Any]]:
                lo = (target - timedelta(days=days - 1)).isoformat()
                return [r for d, rs in by_date.items()
                          if lo <= d <= tgt for r in rs]
            doc = {
                "player_id":    bid,
                "player_name":  player_name,
                "game_date":    tgt,
                "season":       season,
                "rolling_7":    _accumulate(_window_rows(7)),
                "rolling_14":   _accumulate(_window_rows(14)),
                "rolling_30":   _accumulate(_window_rows(30)),
                "season_window": season_feat,
                "computed_at":  datetime.now(timezone.utc),
            }
            n_computed += 1
            if not dry_run:
                ops.append(UpdateOne(
                    {"player_id": bid, "game_date": tgt},
                    {"$set": doc}, upsert=True))

    inserted = updated = errors = 0
    if not dry_run and ops:
        BATCH = 1000
        for i in range(0, len(ops), BATCH):
            try:
                res = await db[FEAT_COLLECTION].bulk_write(
                    ops[i:i + BATCH], ordered=False)
                inserted += (res.upserted_count or 0)
                updated  += (res.modified_count or 0)
            except Exception as ex:
                logger.warning(f"[write] batch {i} failed: {ex!r}")
                errors += len(ops[i:i + BATCH])

    logger.info(f"computed={n_computed:,}  inserted={inserted:,}  "
                  f"updated={updated:,}  errors={errors:,}  "
                  f"dry={dry_run}")
    return {"computed": n_computed, "inserted": inserted,
            "updated": updated, "errors": errors}


async def _amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None,
                    help="YYYY-MM-DD. Build features for game_dates ≥ this.")
    p.add_argument("--player", type=int, default=None,
                    help="Limit to a single batter MLBAM id.")
    p.add_argument("--dry", action="store_true",
                    help="Compute but do not write.")
    args = p.parse_args()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await build_features(db, since=args.since, player=args.player,
                          dry_run=args.dry)


if __name__ == "__main__":
    asyncio.run(_amain())

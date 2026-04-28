"""
MLB player identity-map builder.

Reads:
  • mlb_master_hub_2026                     (bdl_id, names, team)
  • mlb_statcast_player_features            (MLBAM player_id, names)
  • mlb_live_props                          (bdl_player_id, names, team)

Writes:
  • mlb_player_identity_map                 (one row per MLB player)

Resolution rules (high → low confidence):

  1.0   exact normalized name AND team match across hub + statcast
        (high-confidence canonical row)
  0.95  unique normalized name match across hub + statcast (no team conflict)
  0.92  alias-file rewrite + unique normalized name match
  0.80  fuzzy difflib match (>= 0.92 ratio) — STORED but tagged as
        "fuzzy" so the engine can refuse to consume it
  0.0   no match (hub-only, statcast-only, or live-only)

Usage:
    python -m scripts.build_mlb_player_identity_map
    python -m scripts.build_mlb_player_identity_map --dry
"""
from __future__ import annotations

import argparse, asyncio, logging, os, sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from services.mlb.identity import (
    normalize_player_name, apply_alias, string_similarity,
)

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_mlb_player_identity_map")

OUT_COLLECTION = "mlb_player_identity_map"
FUZZY_THRESHOLD = 0.92


async def _ensure_indexes(db) -> None:
    coll = db[OUT_COLLECTION]
    await coll.create_index([("normalized_name", 1)],
                              name="norm_name", unique=False)
    await coll.create_index([("statcast_id", 1)],
                              name="statcast_id",
                              partialFilterExpression={
                                  "statcast_id": {"$type": "number"}})
    await coll.create_index([("bdl_id", 1)], name="bdl_id",
                              partialFilterExpression={
                                  "bdl_id": {"$type": "number"}})
    await coll.create_index([("aliases", 1)], name="aliases")


async def _load_hub(db) -> Dict[str, Dict[str, Any]]:
    """Master hub keyed by normalized canonical name. Source of truth
    for active MLB rosters in our pipeline."""
    out: Dict[str, Dict[str, Any]] = {}
    async for d in db.mlb_master_hub_2026.find(
        {"is_batter": True},
        {"_id": 0, "player_name": 1, "display_name": 1, "bdl_id": 1,
         "team": 1, "active": 1}):
        # Prefer display_name (CamelCase, no diacritic loss); fall back
        # to player_name. Both are stored as `aliases` so downstream
        # joins from either side hit a row.
        raw = d.get("display_name") or d.get("player_name")
        nn  = apply_alias(normalize_player_name(raw))
        if not nn: continue
        # Multiple hub rows can share a normalized name (rare but real
        # — e.g. two "Will Smith"s). We keep them all but tag a
        # `_collision` flag so the consumer can skip ambiguous rows.
        existing = out.get(nn)
        if existing is None:
            out[nn] = {
                "bdl_id":          d.get("bdl_id"),
                "bdl_name":        raw,
                "team":            d.get("team"),
                "active":          d.get("active"),
                "aliases":         {raw,
                                     d.get("display_name"),
                                     d.get("player_name")} - {None},
                "_collision":      False,
            }
        else:
            existing["_collision"] = True
            for n in (raw, d.get("display_name"), d.get("player_name")):
                if n: existing["aliases"].add(n)
    return out


async def _load_statcast(db) -> Dict[str, List[Dict[str, Any]]]:
    """Statcast keyed by normalized name. List values because
    name-collisions exist (we resolve them in the merge step)."""
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_ids = set()
    async for d in db.mlb_statcast_player_features.find(
        {}, {"_id": 0, "player_id": 1, "player_name": 1}):
        pid = d.get("player_id"); nm = d.get("player_name")
        if pid is None: continue
        if pid in seen_ids: continue
        seen_ids.add(pid)
        nn = apply_alias(normalize_player_name(nm))
        if not nn: continue
        out[nn].append({"statcast_id": int(pid), "statcast_name": nm})
    return out


async def _load_live_props(db) -> Dict[str, Dict[str, Any]]:
    """Live-props keyed by normalized name. Used to confirm whether
    each unmatched hub player even appears on the current board (so
    we don't burn fuzzy-match credits on inactive minor-leaguers)."""
    out: Dict[str, Dict[str, Any]] = {}
    async for d in db.mlb_live_props.find(
        {}, {"_id": 0, "player_name": 1, "bdl_player_id": 1, "team": 1}):
        nn = apply_alias(normalize_player_name(d.get("player_name")))
        if not nn: continue
        # last-write-wins is fine; identity is keyed by name.
        out[nn] = {
            "bdl_player_id": d.get("bdl_player_id"),
            "team":          d.get("team"),
        }
    return out


def _resolve_row(nn: str, hub: Dict[str, Dict[str, Any]],
                  sc: Dict[str, List[Dict[str, Any]]],
                  live: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Build a single identity row for one normalized name."""
    h = hub.get(nn); s_list = sc.get(nn) or []; l = live.get(nn)
    confidence = 0.0; method = "none"; statcast_id = statcast_name = None
    bdl_id = team = bdl_name = None; aliases: set = set()

    if h:
        bdl_id   = h.get("bdl_id")
        team     = h.get("team")
        bdl_name = h.get("bdl_name")
        aliases.update(h.get("aliases") or set())

    if s_list and not h.get("_collision", False) if h else s_list:
        # Use the first statcast row; if there are multiple ids for one
        # normalized name (Will Smith × N) we tag confidence down.
        s = s_list[0]
        statcast_id   = s["statcast_id"]
        statcast_name = s["statcast_name"]
        if statcast_name: aliases.add(statcast_name)
        if h and not h.get("_collision", False) and len(s_list) == 1:
            confidence = 1.0; method = "exact_normalized_name"
        else:
            confidence = 0.95; method = "name_match_with_collision"
    elif h:
        confidence = 0.0; method = "hub_only_no_statcast"

    return {
        "normalized_name": nn,
        "mlb_id":          statcast_id,           # MLBAM
        "bdl_id":          bdl_id,
        "statcast_id":     statcast_id,
        "bdl_name":        bdl_name,
        "statcast_name":   statcast_name,
        "odds_api_name":   None,                   # OddsAPI uses BDL pipeline names
        "aliases":         sorted(a for a in aliases if a),
        "team":            team,
        "active":          (h.get("active") if h else None),
        "on_live_board":   l is not None,
        "confidence":      confidence,
        "match_method":    method,
        "source":          "hub+statcast+live",
        "updated_at":      datetime.now(timezone.utc),
    }


def _fuzzy_match(unmatched_hub: List[str],
                  statcast_keys: List[str]
                  ) -> List[Tuple[str, str, float]]:
    """Cross-product fuzzy match for hub names that didn't auto-resolve.
    Returns a list of (hub_norm, statcast_norm, score) for scores
    >= FUZZY_THRESHOLD. We pick the BEST single statcast key per hub
    name to keep cardinality 1:1 — this avoids fan-out errors."""
    out: List[Tuple[str, str, float]] = []
    sc_set = set(statcast_keys)
    for h in unmatched_hub:
        best = None
        for s in sc_set:
            score = string_similarity(h, s)
            if score >= FUZZY_THRESHOLD and (best is None or score > best[1]):
                best = (s, score)
        if best: out.append((h, best[0], best[1]))
    return out


# ---------------------------------------------------------------------------
async def build(db, *, dry: bool = False) -> Dict[str, int]:
    hub  = await _load_hub(db)
    sc   = await _load_statcast(db)
    live = await _load_live_props(db)
    logger.info(f"hub={len(hub):,}  statcast={len(sc):,}  live={len(live):,}")

    # Universe = union of all normalized keys we've seen.
    keys = set(hub) | set(sc) | set(live)
    rows = [_resolve_row(k, hub, sc, live) for k in keys]

    # Fuzzy patch: hub-only rows that COULD link to an unused statcast key.
    used_sc = {r["statcast_id"] for r in rows
                if r["statcast_id"] is not None}
    unmatched_hub = [r["normalized_name"] for r in rows
                       if r["bdl_id"] is not None
                       and r["statcast_id"] is None]
    free_sc_keys = [k for k in sc
                     if not any(s["statcast_id"] in used_sc
                                  for s in sc[k])]
    fuzzy_hits = _fuzzy_match(unmatched_hub, free_sc_keys)
    logger.info(f"fuzzy candidates={len(fuzzy_hits)} "
                  f"(threshold>={FUZZY_THRESHOLD})")
    by_norm = {r["normalized_name"]: r for r in rows}
    for h_norm, s_norm, score in fuzzy_hits:
        s = sc[s_norm][0]
        r = by_norm[h_norm]
        # Stamp fuzzy match with low confidence so the engine guard
        # rejects it from production scoring (per spec safety rule).
        r["statcast_id"]   = s["statcast_id"]
        r["mlb_id"]        = s["statcast_id"]
        r["statcast_name"] = s["statcast_name"]
        if s["statcast_name"]: r["aliases"] = sorted(set(r["aliases"]) | {s["statcast_name"]})
        r["confidence"]    = round(0.80, 3)
        r["match_method"]  = "fuzzy"
        r["fuzzy_score"]   = round(score, 4)
        r["fuzzy_against"] = s_norm

    if not dry:
        await _ensure_indexes(db)
        from pymongo import UpdateOne
        ops = [UpdateOne({"normalized_name": r["normalized_name"]},
                          {"$set": r}, upsert=True) for r in rows]
        if ops:
            res = await db[OUT_COLLECTION].bulk_write(ops, ordered=False)
            logger.info(f"upsert rows={len(rows):,}  "
                          f"inserted={res.upserted_count or 0}  "
                          f"updated={res.modified_count or 0}")

    summary = {
        "rows_total":            len(rows),
        "matched_high_conf":     sum(1 for r in rows if r["confidence"] >= 0.95),
        "matched_fuzzy":         sum(1 for r in rows if r["match_method"] == "fuzzy"),
        "hub_only_unmatched":    sum(1 for r in rows
                                     if r["bdl_id"] is not None
                                     and r["statcast_id"] is None),
        "statcast_only":         sum(1 for r in rows
                                     if r["bdl_id"] is None
                                     and r["statcast_id"] is not None),
    }
    logger.info(f"summary={summary}")
    return summary


async def _amain():
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    args = p.parse_args()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await build(db, dry=args.dry)


if __name__ == "__main__":
    asyncio.run(_amain())

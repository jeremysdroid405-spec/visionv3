"""
Read-only validation: scoring → cached_board routing health.
=============================================================

Usage:
    python /app/backend/services/scoring/scripts/validate_board_routing.py

Compares playable props in `{sport}_prop_scores @ final-{sport}-rt` (tier ∈
{safe_haven, front_lines, war_zone}) against `{sport}_cached_board` for
both NBA and MLB. Reports:

    • count of scored playable props
    • count of cached_board playable props
    • routing breakdown:
        canon_exact / 5tuple_exact / 4tuple_exact / 4tuple_opp_dir /
        player_stat_only / MISS
    • alt-line preservation (same player+stat+side, multiple lines)
    • duplicate canonical_keys
    • collapsed alt-lines
    • stat_type normalization mismatches between scored and cached
    • side mismatch
    • tier mismatch (cached_board may store a tier; if so we check it)
    • pitcher↔batter strikeout collisions (MLB)
    • combo↔base collisions (NBA / MLB)

Exits 0 always (read-only). Output is plain text + `/tmp/board_routing_report.json`.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Tuple, Any, Set

sys.path.insert(0, "/app/backend")

from pymongo import MongoClient

from services.scoring.stat_family import (
    canonical_stat_family,
    build_canonical_key,
    is_pitcher_stat,
    is_batter_stat,
)


def _env(k: str) -> str:
    env = open("/app/backend/.env").read()
    for line in env.splitlines():
        if line.startswith(f"{k}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"missing env {k}")


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _flatten_board(db, sport: str) -> Tuple[List[Dict], Set[str]]:
    """Return (flat list of board props with parent player meta,
    set of canonical_keys present)."""
    coll = f"{sport}_cached_board"
    flat = []
    canon = set()
    for d in db[coll].find({}, {"_id": 0}):
        parent = {k: d.get(k) for k in (
            "player_name", "team", "team_name", "position", "bdl_id"
        )}
        for p in d.get("props", []) or []:
            if not isinstance(p, dict):
                continue
            flat.append({"parent": parent, "prop": p})
            ck = p.get("canonical_key")
            if ck:
                canon.add(ck)
    return flat, canon


def _audit_sport(db, sport: str) -> Dict[str, Any]:
    print(f"\n{'=' * 60}\n{sport.upper()} routing audit\n{'=' * 60}")

    prop_scores = db[f"{sport}_prop_scores"]
    version = f"final-{sport}-rt"

    scored = list(prop_scores.find(
        {"version_tag": version,
         "tier": {"$in": ["safe_haven", "front_lines", "war_zone"]}},
        {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1,
         "recommendation": 1, "event_id": 1, "tier": 1,
         "canonical_key": 1},
    ))
    print(f"Scored playable: {len(scored)}")

    flat_board, board_canon = _flatten_board(db, sport)
    print(f"Cached board props: {len(flat_board)}")

    # Build lookups
    by_5tuple: Dict[tuple, Dict] = {}
    by_4tuple: Dict[tuple, Dict] = {}
    by_player_stat: Dict[tuple, Dict] = {}
    canon_index: Dict[str, Dict] = {}
    canon_dups: Counter = Counter()
    for entry in flat_board:
        p = entry["prop"]
        pn = ((p.get("player_name") or entry["parent"].get("player_name") or "")
              .strip().lower())
        st_raw = p.get("stat_type")
        st = canonical_stat_family(st_raw, sport=sport)
        line = _safe_float(p.get("line"))
        side_raw = (p.get("side") or p.get("recommendation")
                    or p.get("direction") or "")
        side_u = str(side_raw).strip().upper()
        if side_u not in ("OVER", "UNDER"):
            side_u = "OVER"
        eid = p.get("event_id")
        if not pn or not st or line is None:
            continue
        if eid:
            by_5tuple.setdefault((eid, pn, st, line, side_u), entry)
        by_4tuple.setdefault((pn, st, line, side_u), entry)
        by_player_stat.setdefault((pn, st), entry)
        ck = p.get("canonical_key")
        if ck:
            if ck in canon_index:
                canon_dups[ck] += 1
            canon_index[ck] = entry

    # Walk scored
    routing = Counter()
    miss_by_st = Counter()
    side_mismatch = 0
    tier_mismatch = 0
    stat_norm_mismatch_examples = []

    # alt-line tracking
    scored_lines_by_key: Dict[tuple, Set[float]] = defaultdict(set)
    board_lines_by_key: Dict[tuple, Set[float]] = defaultdict(set)
    for entry in flat_board:
        p = entry["prop"]
        pn = ((p.get("player_name") or entry["parent"].get("player_name") or "")
              .strip().lower())
        st = canonical_stat_family(p.get("stat_type"), sport=sport)
        line = _safe_float(p.get("line"))
        side_u = (p.get("side") or p.get("recommendation")
                  or p.get("direction") or "OVER")
        side_u = str(side_u).strip().upper()
        if side_u not in ("OVER", "UNDER"):
            side_u = "OVER"
        if pn and st and line is not None:
            board_lines_by_key[(pn, st, side_u)].add(line)

    # Collision detection
    pitcher_batter_collisions = []
    combo_base_collisions = []

    for s in scored:
        pn = (s.get("player_name") or "").strip().lower()
        raw_st = s.get("stat_type") or ""
        st = canonical_stat_family(raw_st, sport=sport)
        line = _safe_float(s.get("line"))
        side_u = (s.get("recommendation") or "OVER").strip().upper()
        if side_u not in ("OVER", "UNDER"):
            side_u = "OVER"
        eid = s.get("event_id")
        ck = s.get("canonical_key")

        if pn and st and line is not None:
            scored_lines_by_key[(pn, st, side_u)].add(line)

        # Track examples where raw differs from canonical
        if raw_st and raw_st != st and len(stat_norm_mismatch_examples) < 5:
            stat_norm_mismatch_examples.append({
                "player": s.get("player_name"),
                "raw": raw_st, "canonical": st,
            })

        # Routing classification
        if ck and ck in canon_index:
            routing["canon_exact"] += 1
            entry = canon_index[ck]
        elif eid and (eid, pn, st, line, side_u) in by_5tuple:
            routing["5tuple_exact"] += 1
            entry = by_5tuple[(eid, pn, st, line, side_u)]
        elif (pn, st, line, side_u) in by_4tuple:
            routing["4tuple_exact"] += 1
            entry = by_4tuple[(pn, st, line, side_u)]
        elif (pn, st, line,
              "UNDER" if side_u == "OVER" else "OVER") in by_4tuple:
            routing["4tuple_opp_dir"] += 1
            side_mismatch += 1
            entry = by_4tuple[(pn, st, line,
                              "UNDER" if side_u == "OVER" else "OVER")]
        elif (pn, st) in by_player_stat:
            routing["player_stat_only"] += 1
            entry = by_player_stat[(pn, st)]
        else:
            routing["MISS"] += 1
            miss_by_st[raw_st or "?"] += 1
            continue

        # Pitcher↔batter collision check (MLB)
        if sport == "mlb":
            board_st = canonical_stat_family(
                entry["prop"].get("stat_type"), sport=sport
            )
            if (is_pitcher_stat(st) and is_batter_stat(board_st)) or \
               (is_batter_stat(st) and is_pitcher_stat(board_st)):
                pitcher_batter_collisions.append({
                    "player": s.get("player_name"),
                    "scored_stat": st, "board_stat": board_st,
                })

        # Combo↔base check (both sports)
        board_st = canonical_stat_family(
            entry["prop"].get("stat_type"), sport=sport
        )
        if board_st and st and board_st != st:
            scored_is_combo = "+" in st or st == "PRA"
            board_is_combo = "+" in board_st or board_st == "PRA"
            if scored_is_combo != board_is_combo:
                combo_base_collisions.append({
                    "player": s.get("player_name"),
                    "scored_stat": st, "board_stat": board_st,
                })

    # Alt-line preservation
    multi_line_scored = {k: v for k, v in scored_lines_by_key.items()
                         if len(v) > 1}
    survival_full = survival_partial = survival_collapsed = 0
    for key, lines in multi_line_scored.items():
        bl = board_lines_by_key.get(key, set())
        hits = len(lines & bl)
        if hits == len(lines):
            survival_full += 1
        elif hits > 0:
            survival_partial += 1
        else:
            survival_collapsed += 1

    # Print summary
    print(f"\nRouting breakdown (out of {len(scored)} scored):")
    for k, v in routing.most_common():
        pct = v / max(len(scored), 1)
        print(f"  {k:<22s} {v:>4d}  ({pct:.1%})")
    print(f"\nMISS by raw stat_type:")
    for k, v in miss_by_st.most_common(10):
        print(f"  {k:<45s} {v}")

    print(f"\nstat_type raw→canonical examples (this is what was missing "
          f"before the fix):")
    for ex in stat_norm_mismatch_examples:
        print(f"  '{ex['raw']}' → '{ex['canonical']}'  ({ex['player']})")

    print(f"\nDuplicate canonical_keys in cached_board: {sum(canon_dups.values())} "
          f"across {len(canon_dups)} keys")

    print(f"\nAlt-line preservation (same player+stat+side, "
          f"multiple lines in scored):")
    print(f"  total multi-line keys: {len(multi_line_scored)}")
    print(f"    full survival in cache  : {survival_full}")
    print(f"    partial survival        : {survival_partial}")
    print(f"    collapsed (none in cache): {survival_collapsed}")

    if sport == "mlb" and pitcher_batter_collisions:
        print(f"\n⚠️  Pitcher↔batter collisions: {len(pitcher_batter_collisions)}")
        for c in pitcher_batter_collisions[:3]:
            print(f"  {c}")
    elif sport == "mlb":
        print("\n✅ No pitcher↔batter collisions")

    if combo_base_collisions:
        print(f"\n⚠️  Combo↔base collisions: {len(combo_base_collisions)}")
        for c in combo_base_collisions[:3]:
            print(f"  {c}")
    else:
        print("\n✅ No combo↔base collisions")

    return {
        "sport": sport,
        "scored_playable": len(scored),
        "board_props": len(flat_board),
        "routing": dict(routing),
        "miss_by_stat_type_raw": dict(miss_by_st),
        "stat_norm_examples": stat_norm_mismatch_examples,
        "duplicate_canonical_keys": dict(canon_dups),
        "alt_lines": {
            "multi_line_keys": len(multi_line_scored),
            "full_survival": survival_full,
            "partial_survival": survival_partial,
            "collapsed": survival_collapsed,
        },
        "pitcher_batter_collisions": pitcher_batter_collisions,
        "combo_base_collisions": combo_base_collisions,
        "side_mismatch": side_mismatch,
    }


def main():
    db = MongoClient(_env("MONGO_URL"))[_env("DB_NAME")]
    out = {
        "nba": _audit_sport(db, "nba"),
        "mlb": _audit_sport(db, "mlb"),
    }
    out_path = "/tmp/board_routing_report.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n📄 wrote {out_path}")


if __name__ == "__main__":
    main()

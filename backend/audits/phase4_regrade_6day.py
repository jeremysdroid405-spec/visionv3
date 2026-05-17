"""Phase 4 — Regrade the 6-day, 3-tier card cohort in place.

Fixes TWO root causes of the 29.6% ungraded rate, with NO changes to:
  - models       (no inference)
  - gate engine  (no gate calls)
  - card builder (no card identity changes — player/market/line/side/
                   odds/book/event are NOT touched)

Only `grade_status`, `actual_value`, `profit_units` (and `stake_units`
if previously 0 due to ungraded) are updated on the existing card docs.

ROOT CAUSE 1 — Stat-family key mismatch
  The replay engine emits `stat_family="strikeouts"` for batter K
  markets (and `"pitcher_walks"` for walks_allowed markets). The
  adapter `fetch_actuals` returns pdoc keyed on the production
  canonical family (`"batter_strikeouts"`, `"walks_allowed"`). The
  runner does `pdoc.get(row["stat_family"])` → `None` → "ungraded".
  Fix: bypass `pdoc[fam]` lookup; instead read BDL logs directly with
  the market-aware family resolver (`_resolve_mlb_family`) + the
  production stat→log field map (`_MLB_STAT_FIELD_MAP`).

ROOT CAUSE 2 — Cross-midnight / doubleheader date prefix
  BDL game-log timestamps are stored in UTC. An ET evening game on
  date D appears as `"D+1T01:40:00Z"`. The current adapter's
  `fetch_actuals` filters by `(log.date)[:10] == game_date` and
  silently drops those entries. Doubleheaders compound this: a
  player can legitimately have TWO logs dated `D` and `D+1` for the
  SAME calendar slate.
  Fix: For each card we have `event_id` + `commence_time` + `game_date`
  on the joined output row. We scan the player's BDL logs whose
  date_prefix ∈ {D-1, D, D+1} (±1 calendar day), then pick the
  single log whose log-datetime is closest to `commence_time`. Reject
  the match if |Δt| > 18h (safety net — game shouldn't be that far off
  from its own quote).

Output: detailed before/after diff + updated JSON artifact.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import asyncio, json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional, Any

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

from services.replay.providers.mlb_adapter import (
    _resolve_mlb_family, _MLB_STAT_FIELD_MAP,
)
from services.replay.mlb_feature_cache import normalize_player_name


SERIALS = [
  'MLB-PRODREPLAY-20260501-SH-1100UTC-00018','MLB-PRODREPLAY-20260501-FL-1100UTC-00019','MLB-PRODREPLAY-20260501-WZ-1100UTC-00020',
  'MLB-PRODREPLAY-20260502-SH-1100UTC-00021','MLB-PRODREPLAY-20260502-FL-1100UTC-00022','MLB-PRODREPLAY-20260502-WZ-1100UTC-00023',
  'MLB-PRODREPLAY-20260503-SH-1100UTC-00024','MLB-PRODREPLAY-20260503-FL-1100UTC-00025','MLB-PRODREPLAY-20260503-WZ-1100UTC-00026',
  'MLB-PRODREPLAY-20260504-SH-1100UTC-00027','MLB-PRODREPLAY-20260504-FL-1100UTC-00028','MLB-PRODREPLAY-20260504-WZ-1100UTC-00029',
  'MLB-PRODREPLAY-20260505-SH-1100UTC-00030','MLB-PRODREPLAY-20260505-FL-1100UTC-00031','MLB-PRODREPLAY-20260505-WZ-1100UTC-00032',
  'MLB-PRODREPLAY-20260506-SH-1100UTC-00033','MLB-PRODREPLAY-20260506-FL-1100UTC-00034','MLB-PRODREPLAY-20260506-WZ-1100UTC-00035',
]

MAX_DT_HOURS = 18.0  # safety bound for commence_time → log time match


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s: return None
    try:
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def _date_prefix(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return s[:10] if len(s) >= 10 else None


def _composite(stats: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    vals = []
    for k in keys:
        v = stats.get(k)
        if v is None: return None
        try: vals.append(float(v))
        except Exception: return None
    return sum(vals)


def _resolve_actual(
    *, stats: Dict[str, Any], stat_family_canonical: str,
) -> Optional[float]:
    """Read the actual value for a canonical stat family out of a single
    BDL game-log dict. Mirrors `_MLB_STAT_FIELD_MAP` + the composite
    `hits_runs_rbis` rule from `fetch_actuals`. Returns None when the
    field is missing OR explicitly None on the log."""
    if stat_family_canonical == "hits_runs_rbis":
        return _composite(stats, ("hits", "runs", "rbis"))
    field = _MLB_STAT_FIELD_MAP.get(stat_family_canonical)
    if field is None:
        return None
    v = stats.get(field)
    if v is None:
        # `pitcher_outs` may be encoded as `innings_pitched` (× 3).
        if field == "pitcher_outs":
            ip = stats.get("innings_pitched")
            return float(ip) * 3.0 if ip is not None else None
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _grade(actual: Optional[float], *, line: float, side: str,
            odds: int) -> Dict[str, Any]:
    """Same arithmetic as MLBReplayAdapter.grade_outcome."""
    if actual is None:
        return {"status": "ungraded", "profit_units": 0.0,
                "stake_units": 0.0, "actual": None}
    payout = (odds / 100.0) if odds > 0 else (100.0 / -odds)
    if side == "OVER":
        if actual > line:
            return {"status": "win", "profit_units": payout * 1.0,
                    "stake_units": 1.0, "actual": actual}
        if actual < line:
            return {"status": "loss", "profit_units": -1.0,
                    "stake_units": 1.0, "actual": actual}
        return {"status": "push", "profit_units": 0.0,
                "stake_units": 1.0, "actual": actual}
    # UNDER
    if actual < line:
        return {"status": "win", "profit_units": payout * 1.0,
                "stake_units": 1.0, "actual": actual}
    if actual > line:
        return {"status": "loss", "profit_units": -1.0,
                "stake_units": 1.0, "actual": actual}
    return {"status": "push", "profit_units": 0.0,
            "stake_units": 1.0, "actual": actual}


def _best_log_for(
    logs: List[Dict[str, Any]],
    *, game_date: str, commence_time: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Pick the BDL log entry that best matches the card's slate.

    Acceptance window: log date_prefix ∈ {D-1, D, D+1}.
    Selection: among accepted logs, pick the one whose log-datetime
    is closest in absolute terms to `commence_time`. Reject if
    |Δt| > MAX_DT_HOURS. If `commence_time` is None, accept any log
    on `D` exactly.
    """
    if not logs:
        return None
    try:
        d0 = datetime.strptime(game_date, "%Y-%m-%d").date()
    except Exception:
        return None
    accepted_prefixes = {
        (d0 - timedelta(days=1)).isoformat(),
        d0.isoformat(),
        (d0 + timedelta(days=1)).isoformat(),
    }
    ct = _parse_iso(commence_time)
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for g in logs:
        dp = _date_prefix(g.get("date") or g.get("game_date"))
        if dp is None or dp not in accepted_prefixes:
            continue
        if ct is None:
            # Exact-day fallback. Accept any log on D.
            if dp == d0.isoformat():
                candidates.append((0.0, g))
            continue
        lt = _parse_iso(g.get("date") or g.get("game_date"))
        if lt is None:
            continue
        dt = abs((lt - ct).total_seconds()) / 3600.0
        if dt > MAX_DT_HOURS:
            continue
        candidates.append((dt, g))
    if not candidates:
        return None
    candidates.sort(key=lambda kv: kv[0])
    return candidates[0][1]


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print("\n=== Phase 4 — Regrade 6-day × 3-tier cohort ===\n")

    # ── 1. Pull all cards + join to output rows ──────────────────────
    cards = await db.mlb_production_replay_cards.find(
        {"replay_serial": {"$in": SERIALS}}, projection={"_id": 0},
    ).to_list(length=None)
    print(f"cards loaded: {len(cards)}")

    # Build a key → output_row dict (recovers event_id, commence_time,
    # game_date that the card builder didn't propagate).
    out_index: Dict[Tuple, Dict[str, Any]] = {}
    needed_keys = set()
    for c in cards:
        k = (c["replay_serial"],
             c.get("player_name_normalized"),
             c.get("market"), c.get("line"),
             c.get("side"), c.get("book"))
        needed_keys.add(k)
    print(f"unique output keys to join: {len(needed_keys)}")
    cursor = db.mlb_production_replay_outputs.find(
        {"replay_serial": {"$in": SERIALS}, "gate_pass": True},
        projection={"_id": 0, "replay_serial": 1, "player_name_normalized": 1,
                     "market": 1, "line": 1, "side": 1, "book": 1,
                     "event_id": 1, "commence_time": 1, "game_date": 1,
                     "stat_family": 1, "odds": 1},
    )
    async for r in cursor:
        k = (r["replay_serial"], r.get("player_name_normalized"),
             r.get("market"), r.get("line"), r.get("side"), r.get("book"))
        out_index[k] = r
    print(f"output rows indexed: {len(out_index)}")

    # ── 2. Pull BDL game logs for every unique player ────────────────
    players_needed = {c.get("player_name_normalized") for c in cards}
    players_needed.discard(None)
    print(f"unique players in cohort: {len(players_needed)}")
    bdl: Dict[str, List[Dict[str, Any]]] = {}
    cursor = db.mlb_master_hub_2026.find(
        {"bdl_game_logs.0": {"$exists": True}},
        projection={"_id": 0, "player_name": 1, "display_name": 1,
                     "mlb_full_name": 1, "bdl_game_logs": 1},
    )
    async for hub in cursor:
        canon = (hub.get("display_name") or hub.get("player_name")
                 or hub.get("mlb_full_name") or "")
        nk = normalize_player_name(canon)
        if not nk or nk not in players_needed:
            continue
        logs = hub.get("bdl_game_logs") or []
        prior = bdl.get(nk)
        if prior is None or len(logs) > len(prior):
            bdl[nk] = logs
    print(f"BDL logs loaded for: {len(bdl)} / {len(players_needed)} players")

    # ── 3. Re-grade each card ───────────────────────────────────────
    before_summary = {"win":0, "loss":0, "push":0, "ungraded":0}
    after_summary  = {"win":0, "loss":0, "push":0, "ungraded":0}
    changed: List[Dict[str, Any]] = []
    no_change: int = 0
    still_ungraded: List[Dict[str, Any]] = []
    bulk_ops: List[UpdateOne] = []
    for c in cards:
        prev_status = c.get("grade_status") or "ungraded"
        if prev_status in ("win","loss","push"):
            before_summary[prev_status] += 1
        else:
            before_summary["ungraded"] += 1

        k = (c["replay_serial"], c.get("player_name_normalized"),
             c.get("market"), c.get("line"), c.get("side"), c.get("book"))
        out = out_index.get(k)
        if out is None:
            # Card has no matching output row — shouldn't happen, but
            # surface it rather than silently default.
            after_summary["ungraded"] += 1
            still_ungraded.append({"reason":"no_output_row", **c})
            continue

        market = (c.get("market") or out.get("market") or "").lower()
        # Resolve the canonical stat-family for grading (NOT for gates).
        # Uses the adapter's market+replay_family resolver. This fixes
        # ROOT CAUSE 1: "strikeouts" → "batter_strikeouts" or
        # "pitcher_strikeouts" depending on the market.
        canonical_family = _resolve_mlb_family(
            market, out.get("stat_family"))

        # Find the matching BDL game log for this card's slate.
        logs = bdl.get(c.get("player_name_normalized") or "", [])
        log = _best_log_for(
            logs,
            game_date=out.get("game_date") or "",
            commence_time=out.get("commence_time"),
        )
        actual = None
        if log is not None:
            actual = _resolve_actual(
                stats=log, stat_family_canonical=canonical_family)

        new = _grade(
            actual, line=float(c.get("line")),
            side=(c.get("side") or "OVER").upper(),
            odds=int(c.get("odds")),
        )
        new_status = new["status"]
        if new_status in ("win","loss","push"):
            after_summary[new_status] += 1
        else:
            after_summary["ungraded"] += 1
            still_ungraded.append({
                "reason": ("no_log_match" if log is None
                            else "log_field_null"),
                "replay_serial": c["replay_serial"],
                "player_name": c.get("player_name"),
                "player_name_normalized": c.get("player_name_normalized"),
                "stat_family": c.get("stat_family"),
                "canonical_family": canonical_family,
                "market": c.get("market"),
                "line": c.get("line"), "side": c.get("side"),
                "game_date": out.get("game_date"),
                "commence_time": out.get("commence_time"),
                "had_logs": bool(logs),
            })

        if (new_status != prev_status
                or float(c.get("profit_units") or 0) != float(new["profit_units"])):
            changed.append({
                "replay_serial": c["replay_serial"],
                "rank": c.get("rank"),
                "player_name": c.get("player_name"),
                "stat_family": c.get("stat_family"),
                "canonical_family": canonical_family,
                "market": c.get("market"),
                "line": c.get("line"), "side": c.get("side"),
                "book": c.get("book"), "odds": c.get("odds"),
                "before": {
                    "grade_status": prev_status,
                    "actual_value": c.get("actual_value"),
                    "profit_units": c.get("profit_units"),
                    "stake_units": c.get("stake_units"),
                },
                "after": {
                    "grade_status": new_status,
                    "actual_value": new["actual"],
                    "profit_units": new["profit_units"],
                    "stake_units": new["stake_units"],
                },
            })
            bulk_ops.append(UpdateOne(
                {"replay_serial": c["replay_serial"], "rank": c.get("rank")},
                {"$set": {
                    "grade_status": new_status,
                    "actual_value": new["actual"],
                    "profit_units": float(new["profit_units"]),
                    "stake_units": float(new["stake_units"]),
                    "regrade_method": "phase4_regrade_v1_2026_05_17",
                }},
            ))
        else:
            no_change += 1

    print(f"\nupdates queued: {len(bulk_ops)}; unchanged: {no_change}")
    if bulk_ops:
        res = await db.mlb_production_replay_cards.bulk_write(
            bulk_ops, ordered=False)
        print(f"bulk write done: matched={res.matched_count} modified={res.modified_count}")

    # ── 4. Recompute aggregate HR / ROI / P&L (post-regrade) ────────
    print(f"\nbefore: {before_summary}")
    print(f"after : {after_summary}")
    before_total = sum(before_summary.values())
    after_total = sum(after_summary.values())
    before_dec = before_summary["win"] + before_summary["loss"]
    after_dec = after_summary["win"] + after_summary["loss"]
    before_hr = (100*before_summary["win"]/before_dec) if before_dec else 0.0
    after_hr  = (100*after_summary["win"]/after_dec)  if after_dec else 0.0

    # ROI / P&L (need profit sums, fresh after persistence)
    cards_after = await db.mlb_production_replay_cards.find(
        {"replay_serial": {"$in": SERIALS}}, projection={"_id": 0}
    ).to_list(length=None)
    stake_after = sum(float(c.get("stake_units") or 0) for c in cards_after)
    profit_after = sum(float(c.get("profit_units") or 0) for c in cards_after)
    stake_before = sum(float(c.get("stake_units") or 0) for c in cards)
    profit_before = sum(float(c.get("profit_units") or 0) for c in cards)
    before_roi = (100*profit_before/stake_before) if stake_before else 0.0
    after_roi  = (100*profit_after/stake_after) if stake_after else 0.0

    print(f"\nbefore: stake={stake_before:.2f}  profit={profit_before:+.4f}  "
          f"HR={before_hr:.4f}%  ROI={before_roi:+.4f}%  "
          f"ungraded={before_summary['ungraded']}/{before_total} "
          f"({100*before_summary['ungraded']/before_total:.2f}%)")
    print(f"after : stake={stake_after:.2f}  profit={profit_after:+.4f}  "
          f"HR={after_hr:.4f}%  ROI={after_roi:+.4f}%  "
          f"ungraded={after_summary['ungraded']}/{after_total} "
          f"({100*after_summary['ungraded']/after_total:.2f}%)")

    # Breakdown of changes by transition
    transitions: Dict[Tuple[str, str], int] = {}
    for ch in changed:
        key = (ch["before"]["grade_status"], ch["after"]["grade_status"])
        transitions[key] = transitions.get(key, 0) + 1
    print("\ntransitions (before → after, count):")
    for (a, b), n in sorted(transitions.items(), key=lambda kv: -kv[1]):
        print(f"  {a:>10} → {b:<10} : {n}")

    # Still ungraded — by reason
    print(f"\nstill ungraded: {len(still_ungraded)}")
    by_reason: Dict[str, int] = {}
    by_family: Dict[str, int] = {}
    for x in still_ungraded:
        by_reason[x["reason"]] = by_reason.get(x["reason"], 0) + 1
        by_family[x.get("canonical_family") or x.get("stat_family") or "_unknown"] = \
            by_family.get(x.get("canonical_family") or x.get("stat_family") or "_unknown", 0) + 1
    print("  by reason:", by_reason)
    print("  by canonical_family:", by_family)
    for x in still_ungraded[:30]:
        print(f"    [{x['reason']}] {x.get('player_name')!s:<30} {x.get('market')!s:<35} "
              f"line={x.get('line')} side={x.get('side')} date={x.get('game_date')} "
              f"commence={x.get('commence_time')}")

    # ── 5. Persist artifact ─────────────────────────────────────────
    art = "/app/backend/audits/phase4_regrade_6day_2026_05_17.json"
    out = {
        "serials": SERIALS,
        "regrade_method": "phase4_regrade_v1_2026_05_17",
        "before": {
            **before_summary,
            "stake": stake_before, "profit": profit_before,
            "hr_pct": round(before_hr, 4), "roi_pct": round(before_roi, 4),
            "ungraded_pct": round(100*before_summary["ungraded"]/before_total, 4),
        },
        "after": {
            **after_summary,
            "stake": stake_after, "profit": profit_after,
            "hr_pct": round(after_hr, 4), "roi_pct": round(after_roi, 4),
            "ungraded_pct": round(100*after_summary["ungraded"]/after_total, 4),
        },
        "transitions": {f"{a}->{b}": n for (a, b), n in transitions.items()},
        "changed_count": len(changed),
        "unchanged_count": no_change,
        "still_ungraded": still_ungraded,
        "changes": changed,
    }
    with open(art, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\n[json] wrote {art}")

    cli.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

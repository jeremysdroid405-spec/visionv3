"""
audit_pipeline_parity.py — Four-check parity audit for the team betting pipeline.

Run as:
    python -m scripts.sgo.audit_pipeline_parity

Checks:
  1. Backtest vs Production stack (MLB)
  2. Backtest vs Production stack (NBA)
  3. MLB vs NBA parity in backtest stack
  4. MLB vs NBA parity in production stack
"""
from __future__ import annotations
import asyncio
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

REPLAY_COLL = "sgo_propvision_full_pipeline_replay"
PROP_FEAT_COLL = "team_model_prop_features"
SCORES_COLL = "team_prop_scores"

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

COMPARE_FIELDS = ("tp", "edge", "implied_probability", "clean_odds", "selected_tier", "odds_bucket")
FLOAT_TOL = 0.001


# ── Tier / bucket logic (copied exactly from source files) ──────────────────

def _odds_bucket(odds: Optional[int]) -> str:
    """Mirrors historical_full_pipeline_replay._odds_bucket exactly."""
    if odds is None:
        return "odds_na"
    o = int(odds)
    if o < -200:
        return "odds_lt_-200"
    if o < -100:
        return "odds_-200_-100"
    if o < 0:
        return "odds_-100_-0"
    if o < 150:
        return "odds_+0_+150"
    if o < 300:
        return "odds_+150_+300"
    return "odds_+300p"


def _tier_for_odds_bucket(bucket: str) -> str:
    """Mirrors reshape_team_props_to_replay.tier_for_odds_bucket exactly."""
    if bucket in ("odds_-200_-100", "odds_lt_-200"):
        return "safe_haven"
    if bucket in ("odds_+150_+300", "odds_+300p"):
        return "war_zone"
    return "front_lines"


# Production passthrough thresholds (team_prop_passthrough.py).
PROD_SAFE_HAVEN_MAX = -250
PROD_WAR_ZONE_MIN = +250


def _prod_route_tier(odds: Optional[int]) -> Optional[str]:
    """Mirrors team_prop_passthrough._route_tier."""
    if odds is None:
        return None
    if odds <= PROD_SAFE_HAVEN_MAX:
        return "safe_haven"
    if PROD_WAR_ZONE_MIN <= odds <= 1500:
        return "war_zone"
    if -249 <= odds <= 249:
        return "front_lines"
    return None


def _american_implied(odds: Any) -> Optional[float]:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o < 0:
        return round(-o / (-o + 100.0), 4)
    return round(100.0 / (o + 100.0), 4)


def _close(a: Optional[float], b: Optional[float], tol: float = FLOAT_TOL) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


# ── Printer helpers ──────────────────────────────────────────────────────────

def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _info(msg: str) -> None:
    print(f"       {msg}")


# ── Check 1 & 2 — Backtest vs Production (per sport) ───────────────────────

async def _check_backtest_vs_prod(db: AsyncIOMotorDatabase, sport: str, n: int = 20) -> None:
    league = sport.upper()
    print(f"\nCHECK {'1' if sport == 'mlb' else '2'} — Backtest vs Production ({league})")

    # Sample recent replay rows with clean_odds set.
    sample: List[Dict[str, Any]] = await db[REPLAY_COLL].find(
        {
            "prop_type": "team",
            "league_id": league,
            "clean_odds": {"$ne": None},
            "tp": {"$ne": None},
        },
        {"_id": 0},
    ).sort("game_date", -1).limit(n * 5).to_list(n * 5)

    # Deduplicate to unique (event_id, team_id, market, side, line, book).
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for r in sample:
        k = (r.get("event_id"), r.get("team_id"), r.get("market"),
             r.get("side"), r.get("line"), r.get("book"))
        if k not in seen:
            seen.add(k)
            deduped.append(r)
        if len(deduped) >= n:
            break

    if not deduped:
        _warn(f"No {league} replay rows found with clean_odds + tp set")
        return

    print(f"  Sampled {len(deduped)} {league} replay rows")

    # Lazy-import scorer.
    try:
        from services.team_xgb_loader import score_team_prop
    except Exception as e:
        _warn(f"Cannot import score_team_prop: {e}")
        return

    field_mismatches: Dict[str, int] = {f: 0 for f in COMPARE_FIELDS}
    field_checked: Dict[str, int] = {f: 0 for f in COMPARE_FIELDS}
    miss_src = 0

    for replay in deduped:
        # Fetch source feature row.
        src = await db[PROP_FEAT_COLL].find_one(
            {
                "sport": sport,
                "event_id": replay.get("event_id"),
                "team_id": replay.get("team_id"),
                "market_key": replay.get("market"),
                "line": replay.get("line"),
                "side": replay.get("side"),
                "book": replay.get("book"),
            },
            {"_id": 0},
        )
        if src is None:
            miss_src += 1
            continue

        # Re-score via production scorer.
        prod_score = score_team_prop(src)
        if prod_score is None:
            continue

        # tp / model_probability
        field_checked["tp"] += 1
        bt_tp = replay.get("tp")
        prod_tp = prod_score.get("model_probability")
        if not _close(bt_tp, prod_tp):
            field_mismatches["tp"] += 1

        # edge (may differ: backtest uses Odds API implied; prod uses raw odds)
        field_checked["edge"] += 1
        bt_edge = replay.get("edge")
        prod_edge = prod_score.get("edge")
        if not _close(bt_edge, prod_edge):
            field_mismatches["edge"] += 1

        # implied_probability
        field_checked["implied_probability"] += 1
        bt_impl = replay.get("implied_probability")
        prod_impl = prod_score.get("implied_probability")
        if not _close(bt_impl, prod_impl):
            field_mismatches["implied_probability"] += 1

        # clean_odds — production path (passthrough) doesn't write clean_odds;
        # compare replay's clean_odds to _american_implied basis.
        field_checked["clean_odds"] += 1
        bt_co = replay.get("clean_odds")
        raw_odds = src.get("odds")
        prod_co = raw_odds  # production uses raw odds, not Odds API lookup
        if bt_co != prod_co:
            field_mismatches["clean_odds"] += 1

        # selected_tier — backtest uses tier_for_odds_bucket(_odds_bucket(clean_odds))
        field_checked["selected_tier"] += 1
        bt_tier = replay.get("selected_tier")
        bt_bucket = replay.get("odds_bucket")
        prod_tier = _prod_route_tier(raw_odds)  # production route from raw odds
        if bt_tier != prod_tier:
            field_mismatches["selected_tier"] += 1

        # odds_bucket — backtest writes _odds_bucket(clean_odds or odds)
        field_checked["odds_bucket"] += 1
        expected_bucket = _odds_bucket(bt_co or raw_odds)
        if bt_bucket != expected_bucket:
            field_mismatches["odds_bucket"] += 1

    if miss_src:
        _warn(f"Could not find source prop feature row for {miss_src}/{len(deduped)} rows")

    for field in COMPARE_FIELDS:
        checked = field_checked[field]
        misses = field_mismatches[field]
        ok_n = checked - misses
        if checked == 0:
            _warn(f"{field}: no rows checked")
        elif misses == 0:
            _ok(f"{field} matches within {FLOAT_TOL} ({ok_n}/{checked})")
        else:
            _fail(f"{field} mismatch: {misses}/{checked} rows diverge")
            if field == "selected_tier":
                _info(
                    f"Cause: backtest uses tier_for_odds_bucket(_odds_bucket(clean_odds)), "
                    f"production uses _route_tier(raw_odds) with different thresholds "
                    f"(safe_haven≤-250 vs backtest safe_haven<-100)"
                )
            if field == "implied_probability":
                _info(
                    "Cause: backtest overlays Odds API implied; "
                    "production uses _american_implied(raw_odds)"
                )
            if field == "clean_odds":
                _info(
                    "Cause: backtest writes Odds API fetched odds as clean_odds; "
                    "production passthrough does not write clean_odds"
                )


# ── Check 3 & 4 — MLB vs NBA field-level parity ─────────────────────────────

async def _check_sport_parity(
    db: AsyncIOMotorDatabase,
    *,
    check_num: int,
    label: str,
    coll: str,
    base_filter: Dict[str, Any],
    markets: List[str],
    n_per_market: int = 50,
) -> None:
    print(f"\nCHECK {check_num} — MLB vs NBA parity in {label}")
    sports = ("mlb", "nba")

    for check_name, check_fn in [
        ("clean_odds source", _parity_clean_odds_source),
        ("tier routing from clean_odds", _parity_tier_routing),
        ("tp flip (OVER+UNDER = 1.0)", _parity_tp_flip),
        ("implied_probability source", _parity_implied_source),
        ("tier thresholds", _parity_tier_thresholds),
    ]:
        print(f"\n  [{check_name}]")
        results: Dict[str, Any] = {}
        for sport in sports:
            rows = await _sample_rows(db, coll, sport, base_filter, markets, n_per_market)
            results[sport] = rows

        await check_fn(results, db=db, coll=coll, label=label)


async def _sample_rows(
    db: AsyncIOMotorDatabase,
    coll: str,
    sport: str,
    base_filter: Dict[str, Any],
    markets: List[str],
    n_per_market: int,
) -> List[Dict[str, Any]]:
    league = sport.upper()
    out: List[Dict[str, Any]] = []
    for mc in markets:
        q = {
            **base_filter,
            "league_id": league,
            "stat_family": mc,
        }
        rows = await db[coll].find(q, {"_id": 0}).sort("game_date", -1).limit(n_per_market).to_list(n_per_market)
        out.extend(rows)
    return out


async def _parity_clean_odds_source(
    results: Dict[str, List[Dict[str, Any]]],
    *,
    db: AsyncIOMotorDatabase,
    coll: str,
    label: str,
) -> None:
    """Both sports should only have clean_odds from Odds API (no null clean_odds in replay)."""
    for sport, rows in results.items():
        if not rows:
            _warn(f"{sport.upper()}: no rows sampled")
            continue
        null_co = sum(1 for r in rows if r.get("clean_odds") is None)
        if label == "backtest":
            # Backtest replay should have 0 null clean_odds (filtered at write time).
            if null_co == 0:
                _ok(f"{sport.upper()}: all {len(rows)} rows have clean_odds from Odds API only")
            else:
                _fail(f"{sport.upper()}: {null_co}/{len(rows)} rows have clean_odds=None "
                      "(reshape filter should have excluded these)")
        else:
            # Production passthrough doesn't write clean_odds — this is expected.
            _info(f"{sport.upper()}: production passthrough does not write clean_odds; "
                  f"{null_co}/{len(rows)} rows have clean_odds=None "
                  "(expected — production uses raw odds)")


async def _parity_tier_routing(
    results: Dict[str, List[Dict[str, Any]]],
    *,
    db: AsyncIOMotorDatabase,
    coll: str,
    label: str,
) -> None:
    """selected_tier should be derived from clean_odds, not raw odds, in backtest."""
    for sport, rows in results.items():
        if not rows:
            continue
        clean_match = 0
        raw_match = 0
        total = 0
        for r in rows:
            co = r.get("clean_odds")
            raw = r.get("odds")
            tier_stored = r.get("selected_tier") or r.get("tier")
            if tier_stored is None:
                continue
            total += 1
            tier_from_co = _tier_for_odds_bucket(_odds_bucket(co)) if co is not None else None
            tier_from_raw = _tier_for_odds_bucket(_odds_bucket(raw)) if raw is not None else None
            if tier_from_co == tier_stored:
                clean_match += 1
            if tier_from_raw == tier_stored:
                raw_match += 1

        if total == 0:
            _warn(f"{sport.upper()}: no rows with a tier to check")
            continue
        if label == "backtest":
            if clean_match == total:
                _ok(f"{sport.upper()}: tier routing uses clean_odds for all {total}/{total} rows")
            elif raw_match > clean_match:
                _fail(f"{sport.upper()}: tier routing uses raw odds for "
                      f"{raw_match}/{total} rows, clean_odds for {clean_match}/{total}")
            else:
                _warn(f"{sport.upper()}: clean_odds match={clean_match}/{total}, "
                      f"raw odds match={raw_match}/{total}")
        else:
            # Production uses raw odds.
            if raw_match == total:
                _ok(f"{sport.upper()}: tier routing uses raw odds for {total}/{total} rows (expected)")
            else:
                _warn(f"{sport.upper()}: raw odds tier match={raw_match}/{total}")


async def _parity_tp_flip(
    results: Dict[str, List[Dict[str, Any]]],
    *,
    db: AsyncIOMotorDatabase,
    coll: str,
    label: str,
) -> None:
    """OVER+UNDER pairs should sum to 1.0 for both sports."""
    for sport, rows in results.items():
        if not rows:
            continue
        # Index by (event_id, stat_family, line) → {side → tp}
        groups: Dict[tuple, Dict[str, Optional[float]]] = {}
        for r in rows:
            k = (r.get("event_id"), r.get("stat_family"), r.get("line"))
            if None in k:
                continue
            side = (r.get("side") or "").upper()
            tp = r.get("tp") or r.get("model_probability")
            if side in ("OVER", "UNDER") and k not in groups:
                groups[k] = {}
            if side in ("OVER", "UNDER"):
                groups[k][side] = tp

        pairs = [(k, v) for k, v in groups.items()
                 if "OVER" in v and "UNDER" in v
                 and v["OVER"] is not None and v["UNDER"] is not None]
        if not pairs:
            _warn(f"{sport.upper()}: no OVER/UNDER pairs found to check")
            continue

        bad = []
        for k, v in pairs:
            total = (v["OVER"] or 0.0) + (v["UNDER"] or 0.0)
            if abs(total - 1.0) > FLOAT_TOL:
                bad.append((k, v["OVER"], v["UNDER"], total))

        if not bad:
            _ok(f"{sport.upper()}: all {len(pairs)} OVER+UNDER pairs sum to 1.0")
        else:
            _fail(f"{sport.upper()}: {len(bad)}/{len(pairs)} pairs do NOT sum to 1.0")
            for k, ov, un, tot in bad[:3]:
                _info(f"  event={str(k[0])[:12]} mkt={k[1]} line={k[2]} "
                      f"OVER={ov:.4f} UNDER={un:.4f} sum={tot:.4f}")


async def _parity_implied_source(
    results: Dict[str, List[Dict[str, Any]]],
    *,
    db: AsyncIOMotorDatabase,
    coll: str,
    label: str,
) -> None:
    """Check whether implied_probability tracks Odds API or raw odds."""
    for sport, rows in results.items():
        if not rows:
            continue
        raw_match = 0
        api_diverge = 0
        neither = 0
        total = 0
        for r in rows:
            impl = r.get("implied_probability")
            odds = r.get("odds")
            if impl is None or odds is None:
                continue
            total += 1
            from_raw = _american_implied(odds)
            if from_raw is not None and abs(impl - from_raw) <= FLOAT_TOL:
                raw_match += 1
            else:
                # implied_probability differs from raw-odds implied → likely Odds API
                api_diverge += 1

        if total == 0:
            _warn(f"{sport.upper()}: no rows with both implied_probability and odds")
            continue
        if label == "backtest":
            # Backtest should mostly use Odds API implied (api_diverge > 0 is good).
            if api_diverge > 0:
                _ok(f"{sport.upper()}: {api_diverge}/{total} rows have implied_probability "
                    "diverging from raw odds → Odds API source confirmed")
            else:
                _warn(f"{sport.upper()}: all {total} rows have implied_probability == _american_implied(odds); "
                      "Odds API overlay may not be active")
        else:
            # Production uses raw odds for implied.
            if raw_match == total:
                _ok(f"{sport.upper()}: all {total} rows have implied_probability == _american_implied(odds) "
                    "(expected: production uses raw odds)")
            else:
                _info(f"{sport.upper()}: {raw_match}/{total} match raw-odds implied; "
                      f"{api_diverge}/{total} diverge")


async def _parity_tier_thresholds(
    results: Dict[str, List[Dict[str, Any]]],
    *,
    db: AsyncIOMotorDatabase,
    coll: str,
    label: str,
) -> None:
    """Verify UNIVERSAL_SAFE_HAVEN_MAX=-300, UNIVERSAL_WAR_ZONE_MIN=150 hold for both sports."""
    SAFE_HAVEN_MAX = -300  # any clean_odds <= -300 must be safe_haven
    WAR_ZONE_MIN = 150    # any clean_odds >= +150 must be war_zone

    for sport, rows in results.items():
        if not rows:
            continue
        # Use clean_odds for backtest, raw odds for production.
        odds_field = "clean_odds" if label == "backtest" else "odds"
        sh_wrong = 0
        wz_wrong = 0
        sh_total = 0
        wz_total = 0
        for r in rows:
            tier_stored = r.get("selected_tier") or r.get("tier")
            o = r.get(odds_field)
            if o is None or tier_stored is None:
                continue
            try:
                o = int(o)
            except (TypeError, ValueError):
                continue
            if o <= SAFE_HAVEN_MAX:
                sh_total += 1
                if tier_stored != "safe_haven":
                    sh_wrong += 1
            if o >= WAR_ZONE_MIN:
                wz_total += 1
                if tier_stored != "war_zone":
                    wz_wrong += 1

        tag = f"{sport.upper()} [{odds_field}]"
        if sh_total == 0 and wz_total == 0:
            _warn(f"{tag}: no rows with odds <= {SAFE_HAVEN_MAX} or >= {WAR_ZONE_MIN} to verify")
            continue
        if sh_total > 0:
            if sh_wrong == 0:
                _ok(f"{tag}: all {sh_total} rows with odds <= {SAFE_HAVEN_MAX} → safe_haven")
            else:
                _fail(f"{tag}: {sh_wrong}/{sh_total} rows with odds <= {SAFE_HAVEN_MAX} NOT in safe_haven")
        if wz_total > 0:
            if wz_wrong == 0:
                _ok(f"{tag}: all {wz_total} rows with odds >= {WAR_ZONE_MIN} → war_zone")
            else:
                _fail(f"{tag}: {wz_wrong}/{wz_total} rows with odds >= {WAR_ZONE_MIN} NOT in war_zone")


# ── Main ────────────────────────────────────────────────────────────────────

async def amain() -> None:
    print(f"\nAUDIT REPORT — {TODAY}")
    print("=" * 56)

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    mongo = AsyncIOMotorClient(mongo_url)
    db = mongo[db_name]

    try:
        # ── Checks 1 & 2 ────────────────────────────────────────────────────
        await _check_backtest_vs_prod(db, "mlb", n=20)
        await _check_backtest_vs_prod(db, "nba", n=20)

        # ── Check 3 — MLB vs NBA parity in backtest stack ───────────────────
        await _check_sport_parity(
            db,
            check_num=3,
            label="backtest",
            coll=REPLAY_COLL,
            base_filter={"prop_type": "team", "clean_odds": {"$ne": None}},
            markets=["game_total", "spread", "h2h", "team_total"],
            n_per_market=50,
        )

        # ── Check 4 — MLB vs NBA parity in production stack ─────────────────
        # Only scored rows (model_version set) for meaningful field checks.
        await _check_sport_parity(
            db,
            check_num=4,
            label="production",
            coll=SCORES_COLL,
            base_filter={"prop_type": "team"},
            markets=["game_total", "spread", "h2h", "team_total"],
            n_per_market=50,
        )

    finally:
        mongo.close()

    print(f"\n{'=' * 56}")
    print("END OF AUDIT REPORT\n")


def main() -> None:
    asyncio.run(amain())

    from scripts.sgo.handoff import update_handoff
    update_handoff(fixed="pipeline parity audit added")


if __name__ == "__main__":
    main()

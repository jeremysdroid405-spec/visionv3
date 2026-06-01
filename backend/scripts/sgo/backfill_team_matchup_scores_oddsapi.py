"""
backfill_team_matchup_scores_oddsapi.py — populate final scores into
NFL/team matchups using The Odds API `/v4/sports/{sport}/scores` endpoint.

WHY THIS SCRIPT (vs. the SGO sibling)
    The SGO bulk-finalized-events feed does NOT carry final scores in
    a shape that `build_team_historical_outcomes` can consume — even
    when paginated, `scores_found` stays 0. The user therefore directed
    us to pivot to The Odds API for the score backfill.

    The SGO-based script remains in-tree as a fallback (do not delete).

THE ODDS API CONTRACT
    Endpoint:   GET /v4/sports/{sport}/scores
    Params:     apiKey, daysFrom (1..3), dateFormat=iso
                eventIds (optional, comma-separated)
    Returns:    list of game dicts with `id`, `home_team`, `away_team`,
                `commence_time`, `completed`, `scores` = [
                    {"name": "<team>", "score": "<int-as-str>"}, ...
                ]
    LIMIT:      `daysFrom` is bounded to {1, 2, 3}. The endpoint
                ONLY returns games completed within the last
                `daysFrom` calendar days (plus live + upcoming).
                There is no deep-historical scores endpoint on
                The Odds API.

OPERATIONAL MODEL
    • One run = one snapshot. Pull `/scores?daysFrom=3` for NFL,
      build an in-memory index keyed by
        (commence_date_iso, normalized_home, normalized_away)
      AND a second index keyed by (normalized_home, normalized_away)
      for graceful date-mismatch fallback.
    • For each candidate matchup row, look it up. If found AND
      `completed=true` AND both scores parse → `$set` on the
      matchup doc. Otherwise increment the right counter and move on.
    • Idempotent: re-runs SKIP rows that already have scores (unless
      `--force`). Per-row `$set` only — preserves every other field.

INTENDED CADENCE
    Run during the season once per week (after Sunday-Monday slate)
    to capture recent finals. Deep-historical backfill of past seasons
    is OUT OF SCOPE for this endpoint — flagged loudly in counters.

USAGE
    # NFL dry-run, default 3-day window
    python -m scripts.sgo.backfill_team_matchup_scores_oddsapi --sport nfl --dry-run

    # NFL live, override days-back
    python -m scripts.sgo.backfill_team_matchup_scores_oddsapi --sport nfl --days-back 3 --yes

    # Restrict to a specific game_date window in the matchups table
    python -m scripts.sgo.backfill_team_matchup_scores_oddsapi --sport nfl \\
        --start 2026-02-01 --end 2026-02-08 --yes
"""
from __future__ import annotations
import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

BACKFILL_VERSION = "v1"
SCORE_SOURCE = "odds_api_scores"

# sport → (matchups_collection, odds_api_sport_key)
SPORT_CONFIG: Dict[str, Tuple[str, str]] = {
    "nfl": ("nfl_matchups",  "americanfootball_nfl"),
    "mlb": ("team_matchups", "baseball_mlb"),
    "nba": ("team_matchups", "basketball_nba"),
}


# ───── pure helpers (unit-tested) ─────
def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):  # pymongo treats bool as int otherwise
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_TEAM_NAME_NOISE = re.compile(r"[^a-z0-9]+")
_NICKNAME_OVERRIDES = {
    # Future-proof against SGO ↔ Odds-API team-name drift. Most teams
    # match exactly (both feeds use full official names), but record any
    # divergences here. Keys are the normalized full-name form.
    "washingtonfootballteam": "washingtoncommanders",
    "oaklandraiders": "lasvegasraiders",
    "stlouisrams": "losangelesrams",
}


def normalize_team_name(name: Optional[str]) -> str:
    """Lowercase, strip non-alphanumeric, apply known nickname overrides.

    Both SGO and Odds API use full official names today
    ("Kansas City Chiefs"), so the simple normalize is sufficient,
    but we keep an override map for franchise-rename robustness."""
    if not isinstance(name, str):
        return ""
    flat = _TEAM_NAME_NOISE.sub("", name.lower())
    return _NICKNAME_OVERRIDES.get(flat, flat)


def commence_date_iso(commence_time: Optional[str]) -> Optional[str]:
    """Extract 'YYYY-MM-DD' from an ISO commence_time string."""
    if not isinstance(commence_time, str):
        return None
    if len(commence_time) < 10:
        return None
    return commence_time[:10]


def extract_scores_from_odds_event(
    ev: Dict[str, Any], *, home_team: str, away_team: str,
) -> Tuple[Optional[float], Optional[float]]:
    """Pull (home_score, away_score) from an Odds API /scores entry.

    The payload shape is:
        scores: [
            {"name": "Kansas City Chiefs", "score": "25"},
            {"name": "San Francisco 49ers", "score": "22"},
        ]
    `name` matching is name-based (not positional) per the docs. Pure
    function. The caller supplies pre-normalized team names so we do
    NOT renormalize per row (keeps the function trivially testable).
    """
    if not isinstance(ev, dict):
        return None, None
    scores = ev.get("scores")
    if not isinstance(scores, list):
        return None, None
    hs: Optional[float] = None
    as_: Optional[float] = None
    for s in scores:
        if not isinstance(s, dict):
            continue
        nm = normalize_team_name(s.get("name"))
        val = _num(s.get("score"))
        if nm == home_team:
            hs = val
        elif nm == away_team:
            as_ = val
    return hs, as_


def build_event_index(
    events: List[Dict[str, Any]],
) -> Tuple[Dict[Tuple[str, str, str], Dict[str, Any]],
            Dict[Tuple[str, str], Dict[str, Any]]]:
    """Build TWO lookup indices from an Odds API /scores response.

    Returns:
        primary: keyed by (commence_date_iso, norm_home, norm_away)
        fallback: keyed by (norm_home, norm_away) — last-write-wins;
                  used when a matchup row's game_date drifts ±1 day
                  from the Odds API commence_time (timezone edge).

    Both indices store the raw event dict so the caller can pull
    `completed`, `id`, and pass the normalized team keys back to
    `extract_scores_from_odds_event`.

    Pure function — easy to unit-test.
    """
    primary: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    fallback: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        h = normalize_team_name(ev.get("home_team"))
        a = normalize_team_name(ev.get("away_team"))
        d = commence_date_iso(ev.get("commence_time"))
        if not h or not a:
            continue
        if d:
            primary[(d, h, a)] = ev
        fallback[(h, a)] = ev
    return primary, fallback


def _completed(ev: Dict[str, Any]) -> bool:
    """Odds API marks finals with `completed: true`. Stricter than
    'scores present' because a live game can also carry partial scores."""
    return bool(ev.get("completed"))


def _is_score_present(row: Dict[str, Any]) -> bool:
    return (row.get("home_score") is not None
            and row.get("away_score") is not None)


# ───── HTTP fetch (mockable in tests) ─────
async def fetch_odds_scores(
    api_key: str, sport_key: str, *,
    days_back: int = 3, event_ids: Optional[List[str]] = None,
    timeout_s: float = 30.0,
) -> List[Dict[str, Any]]:
    """One HTTP GET against /v4/sports/{sport}/scores. Returns the
    list of event dicts the API gave us (already JSON-decoded).
    Raises RuntimeError on non-2xx for caller visibility.

    Kept thin on purpose — the rest of the script never touches HTTP
    so unit tests can monkeypatch this single function."""
    import aiohttp
    if days_back < 1 or days_back > 3:
        raise ValueError("days_back must be 1, 2, or 3 (Odds API contract)")
    params: Dict[str, Any] = {
        "apiKey":     api_key,
        "daysFrom":   days_back,
        "dateFormat": "iso",
    }
    if event_ids:
        params["eventIds"] = ",".join(event_ids)
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores"
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout_s),
    ) as sess:
        async with sess.get(url, params=params) as resp:
            rem = resp.headers.get("x-requests-remaining")
            last = resp.headers.get("x-requests-last")
            body_text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(
                    f"Odds API /scores returned HTTP {resp.status}: "
                    f"{body_text[:300]}")
            import json as _json
            data = _json.loads(body_text)
            print(f"    [odds_api] /scores 200 "
                  f"events={len(data) if isinstance(data, list) else 0}  "
                  f"credits_remaining={rem}  call_cost={last}")
            if not isinstance(data, list):
                return []
            return data


# ───── core ─────
async def _derive_date_window(
    db: AsyncIOMotorDatabase, coll_name: str, sport: str,
    start: Optional[str], end: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Auto-derive the game_date window if the operator didn't pass one."""
    if start and end:
        return start, end
    base: Dict[str, Any] = {"status": "completed"}
    if sport != "nfl":
        base["sport"] = sport
    else:
        base["$or"] = [{"sport": "nfl"}, {"league": "NFL"}]
    agg = await db[coll_name].aggregate([
        {"$match": base},
        {"$group": {"_id": None,
                     "minGD": {"$min": "$game_date"},
                     "maxGD": {"$max": "$game_date"}}},
    ]).to_list(length=1)
    if not agg:
        return start, end
    return start or agg[0].get("minGD"), end or agg[0].get("maxGD")


async def backfill_sport(
    db: AsyncIOMotorDatabase, *,
    sport: str, api_key: str,
    start: Optional[str], end: Optional[str],
    dry_run: bool, force: bool, days_back: int,
    max_events: int,
    fetcher: Any = None,
) -> Dict[str, Any]:
    """Pull /scores once, then update matchups in-place.

    `fetcher` is the HTTP function with the same signature as
    `fetch_odds_scores`. Tests pass a stub; production passes
    `fetch_odds_scores`."""
    coll_name, sport_key = SPORT_CONFIG[sport]
    fetcher = fetcher or fetch_odds_scores
    print(f"\n  [{sport.upper()}] backfilling final scores into {coll_name}")
    print(f"  [{sport.upper()}] odds_api_sport_key={sport_key}  "
          f"start={start}  end={end}  dry_run={dry_run}  "
          f"force={force}  days_back={days_back}")

    starts_after, starts_before = await _derive_date_window(
        db, coll_name, sport, start, end)
    if (starts_after, starts_before) != (start, end):
        print(f"  [{sport.upper()}] auto-derived game_date window: "
              f"{starts_after} → {starts_before}")

    # Single API call — /scores is bulk + cheap (1 credit per call).
    events = await fetcher(api_key, sport_key, days_back=days_back)
    print(f"  [{sport.upper()}] received {len(events):,} event(s) from "
          f"Odds API /scores (daysFrom={days_back}).")
    primary_idx, fallback_idx = build_event_index(events)
    print(f"  [{sport.upper()}] index sizes: "
          f"primary={len(primary_idx):,}  fallback={len(fallback_idx):,}")

    match: Dict[str, Any] = {"status": "completed"}
    if sport != "nfl":
        match["sport"] = sport
    else:
        match["$or"] = [{"sport": "nfl"}, {"league": "NFL"}]
    if starts_after or starts_before:
        gd: Dict[str, Any] = {}
        if starts_after:
            gd["$gte"] = starts_after
        if starts_before:
            gd["$lte"] = starts_before
        match["game_date"] = gd

    n_total = await db[coll_name].count_documents(match)
    print(f"  [{sport.upper()}] candidate completed matchups: {n_total:,}")

    counters = {
        "scanned":             0,
        "already_scored_skip": 0,
        "no_team_names":       0,
        "matched_primary":     0,
        "matched_fallback":    0,
        "not_in_window":       0,
        "found_but_not_completed": 0,
        "missing_score_fields":    0,
        "scores_found":        0,
        "updated":             0,
        "dry_run":             dry_run,
    }
    sample_updates: List[Dict[str, Any]] = []

    cursor = db[coll_name].find(match, {
        "_id": 0, "event_id": 1, "home_team_name": 1, "away_team_name": 1,
        "commence_time": 1, "game_date": 1,
        "home_score": 1, "away_score": 1,
    }).batch_size(200)

    async for m in cursor:
        counters["scanned"] += 1
        if counters["scanned"] > max_events:
            print(f"  [{sport.upper()}] hit --max-events={max_events}; "
                  f"stopping early.")
            break
        if not force and _is_score_present(m):
            counters["already_scored_skip"] += 1
            continue
        h_norm = normalize_team_name(m.get("home_team_name"))
        a_norm = normalize_team_name(m.get("away_team_name"))
        if not h_norm or not a_norm:
            counters["no_team_names"] += 1
            continue
        date_iso = (m.get("game_date") or
                      commence_date_iso(m.get("commence_time")))
        hit = None
        used_fallback = False
        if date_iso:
            hit = primary_idx.get((date_iso, h_norm, a_norm))
        if hit is None:
            hit = fallback_idx.get((h_norm, a_norm))
            used_fallback = hit is not None
        if hit is None:
            counters["not_in_window"] += 1
            continue
        if not _completed(hit):
            counters["found_but_not_completed"] += 1
            continue
        hs, as_ = extract_scores_from_odds_event(
            hit, home_team=h_norm, away_team=a_norm)
        if hs is None or as_ is None:
            counters["missing_score_fields"] += 1
            continue
        counters["matched_fallback" if used_fallback else "matched_primary"] += 1
        counters["scores_found"] += 1
        if len(sample_updates) < 5:
            sample_updates.append({
                "event_id":   m.get("event_id"),
                "home_team":  m.get("home_team_name"),
                "away_team":  m.get("away_team_name"),
                "game_date":  m.get("game_date"),
                "home_score": hs, "away_score": as_,
                "via_fallback": used_fallback,
            })
        if dry_run:
            continue
        update_doc = {
            "$set": {
                "home_score":             hs,
                "away_score":             as_,
                "final_score":            {"home": hs, "away": as_},
                "score_source":           SCORE_SOURCE,
                "score_backfilled_at":    datetime.now(timezone.utc),
                "score_backfill_version": BACKFILL_VERSION,
            },
        }
        r = await db[coll_name].update_one(
            {"event_id": m.get("event_id")}, update_doc)
        counters["updated"] += (r.modified_count or 0)
        if counters["scanned"] % 200 == 0:
            print(f"    [{sport.upper()}] scanned={counters['scanned']:,}  "
                  f"updated={counters['updated']:,}  "
                  f"already={counters['already_scored_skip']:,}  "
                  f"not_in_window={counters['not_in_window']:,}")

    return {"sport": sport, "coll": coll_name, "counters": counters,
            "sample_updates": sample_updates}


def _print_summary(r: Dict[str, Any]) -> None:
    c = r["counters"]
    print()
    print(f"  ── {r['sport'].upper()} ODDS-API BACKFILL SUMMARY "
          f"({r['coll']}) ──")
    print(f"     scanned:                  {c['scanned']:,}")
    print(f"     already scored (skip):    {c['already_scored_skip']:,}")
    print(f"     no team names on matchup: {c['no_team_names']:,}")
    print(f"     matched (primary date+nm):{c['matched_primary']:,}")
    print(f"     matched (fallback nm):    {c['matched_fallback']:,}")
    print(f"     not in /scores window:    {c['not_in_window']:,}  "
          f"(events older than daysFrom)")
    print(f"     found but not completed:  {c['found_but_not_completed']:,}")
    print(f"     missing score fields:     {c['missing_score_fields']:,}")
    print(f"     scores found:             {c['scores_found']:,}")
    print(f"     rows updated:             {c['updated']:,}  "
          f"({'DRY-RUN' if c['dry_run'] else 'live'})")
    if r["sample_updates"]:
        print("     sample updates (first 5):")
        for s in r["sample_updates"]:
            tag = " (fallback)" if s.get("via_fallback") else ""
            print(f"        eid={s['event_id']}  "
                  f"{s.get('away_team') or '?'} @ {s.get('home_team') or '?'}  "
                  f"{s.get('game_date') or '?'}  "
                  f"→ {s['away_score']}–{s['home_score']}{tag}")


# ───── main ─────
async def amain(args: argparse.Namespace) -> int:
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("  ERROR: ODDS_API_KEY missing from environment.")
        return 2

    sports: List[str] = [args.sport] if args.sport != "all" else ["nfl", "mlb", "nba"]
    for s in sports:
        if s not in SPORT_CONFIG:
            print(f"  ERROR: unsupported --sport {s!r} "
                  f"(supported: {sorted(SPORT_CONFIG)})")
            return 2

    dry_run = not args.yes
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"backfill_team_matchup_scores_oddsapi  version={BACKFILL_VERSION}")
    print(f"  sports={sports}  yes={args.yes}  dry_run={dry_run}  "
          f"force={args.force}  days_back={args.days_back}  "
          f"max_events_per_sport={args.max_events}")
    print("  CONTRACT: in-place $set updates to matchup docs; preserves "
          "all other fields; idempotent. score_source='odds_api_scores'.")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        all_results: List[Dict[str, Any]] = []
        for sp in sports:
            r = await backfill_sport(
                db, sport=sp, api_key=api_key,
                start=args.start, end=args.end,
                dry_run=dry_run, force=args.force,
                days_back=args.days_back,
                max_events=args.max_events,
            )
            _print_summary(r)
            all_results.append(r)

        print()
        print("=" * 72)
        print("  GRAND TOTALS")
        print("=" * 72)
        tot = {"scanned": 0, "updated": 0, "scores_found": 0,
                "not_in_window": 0, "already_scored_skip": 0,
                "matched_primary": 0, "matched_fallback": 0}
        for r in all_results:
            for k in tot:
                tot[k] += r["counters"].get(k, 0)
        for k, v in tot.items():
            print(f"  {k:<22s} {v:,}")
        if dry_run:
            print("\n  DRY-RUN — no writes. Pass --yes to apply.")
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=["nfl", "mlb", "nba", "all"],
                    default="nfl",
                    help="Sport to backfill. Default 'nfl'.")
    p.add_argument("--start", default=None,
                    help="Optional inclusive start game_date 'YYYY-MM-DD'.")
    p.add_argument("--end",   default=None,
                    help="Optional inclusive end game_date 'YYYY-MM-DD'.")
    p.add_argument("--yes", action="store_true",
                    help="Actually write to Mongo (default is dry-run).")
    p.add_argument("--dry-run", action="store_true",
                    help="Explicit dry-run flag (default behaviour).")
    p.add_argument("--force", action="store_true",
                    help="Overwrite even when home_score/away_score "
                         "are already set.")
    p.add_argument("--days-back", type=int, default=3,
                    help="Odds API daysFrom parameter. Must be 1, 2, "
                         "or 3 (API contract). Default 3.")
    p.add_argument("--max-events", type=int, default=10_000,
                    help="Safety cap on matchups processed per sport.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

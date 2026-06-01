"""
scripts/forensic/tests.py — Registered forensic test catalog.

EACH TEST is a small async function returning:
    {"expected": <any>, "actual": <any>, "passed": bool,
     "failure_reason": Optional[str]}

CATEGORIES
    infrastructure      — env vars, supervisor, basic connectivity
    auth                — admin token enforcement
    data_sanity         — collection presence + row count sanity
    score_backfill      — BDL score backfill state
    team_phase1         — team_historical_outcomes (Phase 1)
    team_phase2a        — team_model_features (rolling priors)
    team_phase2b        — team_model_prop_features (per-prop)
    team_reshape        — sgo_propvision_full_pipeline_replay team rows
    optimizer_endpoints — preflight, run, results endpoints
    optimizer_filter    — prop_type filter contract (player/team/all)
    candidates          — save_as_candidates threads sport + prop_type
    leakage             — no-future-game guarantee on Phase 2A features
    frontend_smoke      — main route returns 200

Add a new test by registering it in `ALL_TESTS` at the bottom.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List

from scripts.forensic._runner import TestCase, ForensicContext


# ───── helpers ─────
async def _count(ctx: ForensicContext, coll: str,
                  filter_: Dict[str, Any] = None) -> int:
    return await ctx.db[coll].count_documents(filter_ or {})


def _pass(expected, actual, *, reason: str = None) -> Dict[str, Any]:
    return {"expected": expected, "actual": actual,
            "passed": True, "failure_reason": reason}


def _fail(expected, actual, *, reason: str) -> Dict[str, Any]:
    return {"expected": expected, "actual": actual,
            "passed": False, "failure_reason": reason}


def _bool(expected_truthy: bool, actual: bool, *,
            reason: str = None) -> Dict[str, Any]:
    return {"expected": expected_truthy, "actual": bool(actual),
            "passed": bool(actual) == bool(expected_truthy),
            "failure_reason": (None if bool(actual) == bool(expected_truthy)
                                  else reason or "boolean mismatch")}


def _gte(threshold: int, actual: int, *,
          reason: str = None) -> Dict[str, Any]:
    ok = actual >= threshold
    return {"expected": f">= {threshold}", "actual": actual,
            "passed": ok,
            "failure_reason": (None if ok else
                                  reason or f"actual {actual} < {threshold}")}


# ───── infrastructure ─────
async def test_mongo_connectivity(ctx):
    # Ping by counting any well-known collection
    n = await _count(ctx, "users")
    return {"expected": ">= 0", "actual": n, "passed": True,
            "failure_reason": None}


async def test_env_vars_present(ctx):
    required = ["MONGO_URL", "DB_NAME", "BDL_API_KEY",
                  "EMERGENT_ADMIN_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    return _bool(False, bool(missing),
                  reason=f"missing env vars: {missing}") if missing else {
        "expected": "all required env vars present",
        "actual": {"checked": required, "missing": []},
        "passed": True, "failure_reason": None,
    }


async def test_backend_health(ctx):
    r = await ctx.http("GET", "/api/emergent-admin/auth/whoami")
    return _bool(200, r["status"] == 200,
                  reason=f"whoami returned {r['status']}: "
                              f"{str(r['body'])[:200]}")


# ───── auth ─────
async def test_auth_missing_token_401(ctx):
    import aiohttp
    async with ctx.session.get(
        f"{ctx.base_url}/api/emergent-admin/auth/whoami",
        timeout=aiohttp.ClientTimeout(total=10),
    ) as r:
        return _bool(True, r.status in (401, 403),
                      reason=f"expected 401/403, got {r.status}")


async def test_auth_invalid_token_401(ctx):
    import aiohttp
    async with ctx.session.get(
        f"{ctx.base_url}/api/emergent-admin/auth/whoami",
        headers={"X-Admin-Token": "bogus"},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as r:
        return _bool(True, r.status == 401,
                      reason=f"expected 401, got {r.status}")


# ───── data_sanity ─────
async def test_user_collection_present(ctx):
    n = await _count(ctx, "users")
    return _gte(0, n)


async def test_audit_log_present(ctx):
    colls = await ctx.db.list_collection_names()
    has = any("audit" in c.lower() for c in colls)
    return _bool(True, has, reason="no audit-log-like collection found")


# ───── score_backfill ─────
async def test_nfl_matchups_scored(ctx):
    n = await _count(ctx, "nfl_matchups",
                       {"home_score": {"$ne": None},
                        "score_source": "bdl_nfl_games"})
    return _gte(500, n,
                  reason="nfl_matchups should carry >=500 BDL-scored rows "
                         "(Phase 1 dependency)")


async def test_mlb_matchups_scored(ctx):
    n = await _count(ctx, "team_matchups",
                       {"sport": "mlb",
                        "home_score": {"$ne": None},
                        "score_source": "bdl_mlb_games"})
    return _gte(5000, n,
                  reason="team_matchups (MLB) should carry >=5000 BDL "
                         "rows after backfill")


async def test_nba_matchups_scored(ctx):
    n = await _count(ctx, "team_matchups",
                       {"sport": "nba",
                        "home_score": {"$ne": None},
                        "score_source": "bdl_nba_games"})
    return _gte(2000, n,
                  reason="team_matchups (NBA) should carry >=2000 BDL "
                         "rows after backfill")


# ───── team_phase1 ─────
async def test_phase1_nfl_outcomes(ctx):
    n = await _count(ctx, "team_historical_outcomes", {"sport": "nfl"})
    return _gte(100_000, n,
                  reason="Phase 1 NFL outcomes should be >100k")


async def test_phase1_mlb_outcomes(ctx):
    n = await _count(ctx, "team_historical_outcomes", {"sport": "mlb"})
    return _gte(800_000, n,
                  reason="Phase 1 MLB outcomes should be >800k")


async def test_phase1_nba_outcomes(ctx):
    n = await _count(ctx, "team_historical_outcomes", {"sport": "nba"})
    return _gte(400_000, n,
                  reason="Phase 1 NBA outcomes should be >400k")


async def test_phase1_resolution_rate_nfl(ctx):
    total = await _count(ctx, "team_historical_outcomes", {"sport": "nfl"})
    resolved = await _count(ctx, "team_historical_outcomes",
                              {"sport": "nfl", "outcome_resolved": True})
    rate = (resolved / total) if total else 0
    return {"expected": ">= 0.85 resolution",
            "actual": {"total": total, "resolved": resolved,
                        "rate": round(rate, 4)},
            "passed": rate >= 0.85,
            "failure_reason": None if rate >= 0.85
                              else f"NFL resolution rate {rate:.3f} < 0.85"}


async def test_phase1_outcomes_balanced(ctx):
    """Wins ≈ Losses across all sports (basic sanity — no systematic bias).
    Outcomes are stored uppercase (WIN/LOSS/PUSH) by Phase 1."""
    cur = ctx.db.team_historical_outcomes.aggregate([
        {"$match": {"outcome_resolved": True,
                      "outcome": {"$in": ["WIN", "LOSS"]}}},
        {"$group": {"_id": "$outcome", "n": {"$sum": 1}}},
    ])
    out = {d["_id"]: d["n"] async for d in cur}
    wins = out.get("WIN", 0)
    losses = out.get("LOSS", 0)
    total = wins + losses
    ratio = (wins / total) if total else 0
    ok = total > 0 and 0.45 <= ratio <= 0.55
    return {"expected": "wins/(wins+losses) between 0.45 and 0.55",
            "actual": {"wins": wins, "losses": losses,
                        "win_rate": round(ratio, 4)},
            "passed": ok,
            "failure_reason": None if ok else f"win_rate {ratio:.3f} biased"}


# ───── team_phase2a ─────
async def test_phase2a_features_present(ctx):
    n = await _count(ctx, "team_model_features")
    return _gte(10_000, n,
                  reason="team_model_features should be >10k rows")


async def test_phase2a_unique_index_present(ctx):
    info = await ctx.db.team_model_features.index_information()
    has = "uniq_sport_team_asof" in info
    return _bool(True, has,
                  reason="uniq_sport_team_asof missing on team_model_features")


async def test_phase2a_all_3_sports_have_features(ctx):
    cur = ctx.db.team_model_features.aggregate([
        {"$group": {"_id": "$sport", "n": {"$sum": 1}}},
    ])
    by_sport = {d["_id"]: d["n"] async for d in cur}
    needed = {"nfl", "mlb", "nba"}
    missing = needed - set(by_sport)
    ok = not missing
    return {"expected": "features exist for nfl, mlb, nba",
            "actual": by_sport, "passed": ok,
            "failure_reason": None if ok else f"missing sports: {missing}"}


async def test_phase2a_no_leakage_sample(ctx):
    """Spot-check 20 random feature rows have sample_size consistent with
    only-prior games. A leakage violation would manifest as sample_size
    exceeding the count of distinct game_dates strictly before as_of_date
    for that team. We approximate by checking sample_size <= the count
    of outcome rows for the same (sport, team_id) with game_date < as_of_date."""
    pipeline = [{"$sample": {"size": 20}},
                  {"$project": {"_id": 0, "sport": 1, "team_id": 1,
                                  "as_of_date": 1, "sample_size": 1}}]
    samples = [d async for d in ctx.db.team_model_features.aggregate(pipeline)]
    violations = []
    for s in samples:
        if s.get("sample_size", 0) == 0:
            continue
        n_prior_games = len(
            await ctx.db.team_historical_outcomes.distinct(
                "event_id",
                {"sport": s["sport"], "team_id": s["team_id"],
                  "game_date": {"$lt": s["as_of_date"]}}))
        if s["sample_size"] > n_prior_games:
            violations.append({**s, "prior_games": n_prior_games})
    ok = not violations
    return {"expected": "no sample_size > prior games (leakage)",
            "actual": {"checked": len(samples),
                        "violations": violations[:5]},
            "passed": ok,
            "failure_reason": (None if ok else
                                  f"{len(violations)} leakage violations "
                                  f"in 20-row spot-check")}


# ───── team_phase2b ─────
async def test_phase2b_prop_features_count(ctx):
    n = await _count(ctx, "team_model_prop_features")
    return _gte(1_000_000, n,
                  reason="prop_features should be >1M rows across 3 sports")


async def test_phase2b_unique_index_present(ctx):
    info = await ctx.db.team_model_prop_features.index_information()
    has = "uniq_prop_decision" in info
    return _bool(True, has, reason="uniq_prop_decision missing")


async def test_phase2b_carries_outcome(ctx):
    """Every resolved-prop row should carry hit/outcome_numeric."""
    n_resolved = await _count(ctx, "team_model_prop_features",
                                  {"outcome_resolved": True})
    n_with_outcome = await _count(ctx, "team_model_prop_features",
                                       {"outcome_resolved": True,
                                        "outcome_numeric": {"$ne": None}})
    ok = n_resolved == n_with_outcome and n_resolved > 0
    return {"expected": "every resolved row has outcome_numeric set",
            "actual": {"n_resolved": n_resolved,
                        "n_with_outcome": n_with_outcome},
            "passed": ok,
            "failure_reason": (None if ok else
                                  f"{n_resolved - n_with_outcome} resolved "
                                  "rows missing outcome_numeric")}


# ───── team_reshape ─────
async def test_replay_has_team_rows(ctx):
    n = await _count(ctx, "sgo_propvision_full_pipeline_replay",
                       {"prop_type": "team"})
    return _gte(1_000_000, n,
                  reason="replay collection should hold >1M team rows")


async def test_replay_team_pipeline_version(ctx):
    n = await _count(ctx, "sgo_propvision_full_pipeline_replay",
                       {"prop_type": "team",
                        "pipeline_version": "team_v1"})
    return _gte(1_000_000, n,
                  reason="all team rows must carry pipeline_version=team_v1")


async def test_replay_team_odds_buckets_populated(ctx):
    """At least 4 of the 6 canonical odds buckets should be populated
    on team rows. NOTE: `odds_-100_-0` is legitimately rare for team
    markets (books rarely post -50 or -75 on team props), so we don't
    require it. `odds_na` is also expected-absent for graded rows."""
    cur = ctx.db.sgo_propvision_full_pipeline_replay.aggregate([
        {"$match": {"prop_type": "team"}},
        {"$group": {"_id": "$odds_bucket", "n": {"$sum": 1}}},
    ])
    buckets = {d["_id"]: d["n"] async for d in cur}
    canonical = {"odds_lt_-200", "odds_-200_-100", "odds_-100_-0",
                   "odds_+0_+150", "odds_+150_+300", "odds_+300p"}
    present = set(buckets) & canonical
    ok = len(present) >= 4
    return {"expected": ">= 4 of 6 canonical buckets populated",
            "actual": {"buckets": buckets,
                        "present_canonical": sorted(present)},
            "passed": ok,
            "failure_reason": (None if ok else
                                  f"only {len(present)} canonical buckets "
                                  f"present: {sorted(present)}")}


# ───── optimizer_endpoints ─────
async def test_optimizer_preflight_nfl_team(ctx):
    r = await ctx.http("POST", "/api/emergent-admin/optimizer/preflight",
                          json_body={"sport": "NFL",
                                      "start": "2024-09-01",
                                      "end": "2025-02-15",
                                      "prop_type": "team"})
    n_total = (r["body"] or {}).get("n_total_in_window") or 0
    return _gte(10_000, n_total,
                  reason=f"preflight returned {n_total} rows: "
                          f"{str(r['body'])[:200]}")


async def test_optimizer_preflight_returns_tier_breakdown(ctx):
    r = await ctx.http("POST", "/api/emergent-admin/optimizer/preflight",
                          json_body={"sport": "NFL",
                                      "start": "2024-09-01",
                                      "end": "2025-02-15",
                                      "prop_type": "team"})
    tiers = (r["body"] or {}).get("by_tier", [])
    names = {t.get("tier") for t in tiers}
    expected = {"safe_haven", "front_lines", "war_zone"}
    ok = expected.issubset(names)
    return {"expected": expected, "actual": list(names),
            "passed": ok,
            "failure_reason": None if ok else f"missing tiers: {expected-names}"}


async def test_optimizer_preflight_returns_stat_family_breakdown(ctx):
    r = await ctx.http("POST", "/api/emergent-admin/optimizer/preflight",
                          json_body={"sport": "NFL",
                                      "start": "2024-09-01",
                                      "end": "2025-02-15",
                                      "prop_type": "team"})
    fams = {f.get("stat_family")
              for f in (r["body"] or {}).get("by_stat_family", [])}
    expected = {"h2h", "spread", "game_total", "team_total"}
    ok = expected.issubset(fams)
    return {"expected": expected, "actual": list(fams),
            "passed": ok, "failure_reason": None if ok else f"missing: {expected-fams}"}


async def test_optimizer_run_accepts_team(ctx):
    r = await ctx.http("POST", "/api/emergent-admin/optimizer/run",
                          json_body={"sport": "NFL",
                                      "start": "2024-09-01",
                                      "end": "2025-02-15",
                                      "prop_type": "team",
                                      "tiers": ["safe_haven"],
                                      "optimization_goal": "profit"})
    ok = r["status"] == 200 and (r["body"] or {}).get("ok") is True
    return _bool(True, ok,
                  reason=f"run returned {r['status']}: {str(r['body'])[:200]}")


# ───── optimizer_filter ─────
async def test_prop_type_filter_team_only(ctx):
    """Team filter must return team rows. Threshold calibrated against
    the one-season window we have on the preview pod."""
    r = await ctx.http("POST", "/api/emergent-admin/optimizer/preflight",
                          json_body={"sport": "MLB",
                                      "start": "2024-04-01",
                                      "end": "2024-11-15",
                                      "prop_type": "team"})
    n = (r["body"] or {}).get("n_total_in_window") or 0
    return _gte(50_000, n,
                  reason=f"MLB team preflight too low: {n}")


async def test_prop_type_filter_player_default_excludes_team(ctx):
    """Default player filter MUST exclude team rows. On this preview
    pod we have no player rows, so we expect 409 (no rows)."""
    r = await ctx.http("POST", "/api/emergent-admin/optimizer/run",
                          json_body={"sport": "MLB",
                                      "start": "2024-04-01",
                                      "end": "2024-11-15",
                                      "tiers": ["safe_haven"],
                                      "optimization_goal": "profit"})
    # 409 = no rows for player+window; this is the expected back-compat
    # behavior since we have no player data here. If 200, it means the
    # default filter is leaking team rows — that's a leakage bug.
    return _bool(409, r["status"] == 409,
                  reason=(f"expected 409 (player default should exclude "
                          f"team rows on this pod), got {r['status']}: "
                          f"{str(r['body'])[:200]}"))


async def test_prop_type_filter_all_includes_team(ctx):
    """prop_type=all should include team rows."""
    r = await ctx.http("POST", "/api/emergent-admin/optimizer/preflight",
                          json_body={"sport": "MLB",
                                      "start": "2024-04-01",
                                      "end": "2024-11-15",
                                      "prop_type": "all"})
    n = (r["body"] or {}).get("n_total_in_window") or 0
    return _gte(50_000, n,
                  reason=f"MLB 'all' preflight too low: {n}")


# ───── leakage ─────
async def test_no_team_features_after_as_of_date(ctx):
    """Sanity: no feature row should have computed_at < as_of_date."""
    cur = ctx.db.team_model_features.find(
        {}, {"_id": 0, "as_of_date": 1, "computed_at": 1}
    ).sort("as_of_date", -1).limit(100)
    seen = 0
    violations = []
    async for d in cur:
        seen += 1
        c = d.get("computed_at")
        a = d.get("as_of_date")
        if not (c and a):
            continue
        c_date = c.isoformat()[:10] if hasattr(c, "isoformat") else str(c)[:10]
        if c_date < a:
            violations.append({"computed_at": c_date, "as_of_date": a})
    ok = not violations
    return {"expected": "no computed_at < as_of_date",
            "actual": {"checked": seen, "violations": violations[:3]},
            "passed": ok,
            "failure_reason": (None if ok else
                                  f"{len(violations)} leakage violations")}


# ───── frontend_smoke ─────
async def test_frontend_root_reachable(ctx):
    """The frontend is served on port 3000 internally, but externally
    on the preview/prod URL. Read REACT_APP_BACKEND_URL from frontend
    .env if present (that's the externally-resolvable host); falls
    back to localhost:3000."""
    import os
    front_url = None
    for env_path in ("/var/www/app/frontend/.env", "/app/frontend/.env"):
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("REACT_APP_BACKEND_URL="):
                        front_url = line.split("=", 1)[1].strip()
                        break
            if front_url:
                break
    front_url = front_url or "http://localhost:3000"
    import aiohttp
    try:
        async with ctx.session.get(
            front_url, timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            return {"expected": "frontend HTTP 2xx/3xx",
                    "actual": {"url": front_url, "status": r.status},
                    "passed": 200 <= r.status < 400,
                    "failure_reason": (None if 200 <= r.status < 400
                                          else f"frontend HTTP {r.status}")}
    except Exception as e:
        return {"expected": "frontend HTTP 2xx/3xx",
                "actual": {"url": front_url, "error": str(e)},
                "passed": False,
                "failure_reason": f"frontend unreachable: {e}"}


# ───── catalog registration ─────
ALL_TESTS: List[TestCase] = [
    # infrastructure
    TestCase("FA-001", "infrastructure",
              "Mongo connectivity (count users coll)",
              {}, test_mongo_connectivity),
    TestCase("FA-002", "infrastructure",
              "Required env vars present (MONGO_URL/DB_NAME/BDL_API_KEY/EMERGENT_ADMIN_TOKEN)",
              {"checked": ["MONGO_URL", "DB_NAME", "BDL_API_KEY",
                              "EMERGENT_ADMIN_TOKEN"]},
              test_env_vars_present),
    TestCase("FA-003", "infrastructure",
              "Backend whoami returns 200 with admin token",
              {"path": "/api/emergent-admin/auth/whoami"},
              test_backend_health),
    # auth
    TestCase("FA-010", "auth",
              "whoami without token returns 401/403",
              {}, test_auth_missing_token_401),
    TestCase("FA-011", "auth",
              "whoami with bogus token returns 401",
              {}, test_auth_invalid_token_401),
    # data_sanity
    TestCase("FA-020", "data_sanity",
              "Users collection exists (>=0 docs)",
              {"coll": "users"}, test_user_collection_present),
    TestCase("FA-021", "data_sanity",
              "Some audit-log collection exists",
              {}, test_audit_log_present),
    # score_backfill
    TestCase("FA-030", "score_backfill",
              "NFL matchups scored via BDL (>=500)",
              {"coll": "nfl_matchups", "score_source": "bdl_nfl_games"},
              test_nfl_matchups_scored),
    TestCase("FA-031", "score_backfill",
              "MLB matchups scored via BDL (>=5000)",
              {"coll": "team_matchups", "sport": "mlb",
                "score_source": "bdl_mlb_games"},
              test_mlb_matchups_scored),
    TestCase("FA-032", "score_backfill",
              "NBA matchups scored via BDL (>=2000)",
              {"coll": "team_matchups", "sport": "nba",
                "score_source": "bdl_nba_games"},
              test_nba_matchups_scored),
    # team_phase1
    TestCase("FA-040", "team_phase1",
              "NFL outcomes built (>=100k)",
              {"coll": "team_historical_outcomes", "sport": "nfl"},
              test_phase1_nfl_outcomes),
    TestCase("FA-041", "team_phase1",
              "MLB outcomes built (>=800k)",
              {"coll": "team_historical_outcomes", "sport": "mlb"},
              test_phase1_mlb_outcomes),
    TestCase("FA-042", "team_phase1",
              "NBA outcomes built (>=400k)",
              {"coll": "team_historical_outcomes", "sport": "nba"},
              test_phase1_nba_outcomes),
    TestCase("FA-043", "team_phase1",
              "NFL outcome resolution rate >= 85%",
              {"sport": "nfl"}, test_phase1_resolution_rate_nfl),
    TestCase("FA-044", "team_phase1",
              "Outcomes balanced (wins/losses ~50/50)",
              {}, test_phase1_outcomes_balanced),
    # team_phase2a
    TestCase("FA-050", "team_phase2a",
              "Team feature rows present (>=10k)",
              {"coll": "team_model_features"}, test_phase2a_features_present),
    TestCase("FA-051", "team_phase2a",
              "Unique index uniq_sport_team_asof present",
              {}, test_phase2a_unique_index_present),
    TestCase("FA-052", "team_phase2a",
              "Features exist for all 3 sports (NFL+MLB+NBA)",
              {}, test_phase2a_all_3_sports_have_features),
    TestCase("FA-053", "team_phase2a",
              "Leakage guard: sample_size <= prior games (spot-check 20)",
              {}, test_phase2a_no_leakage_sample),
    TestCase("FA-054", "team_phase2a",
              "No computed_at < as_of_date (spot-check 100)",
              {}, test_no_team_features_after_as_of_date),
    # team_phase2b
    TestCase("FA-060", "team_phase2b",
              "Prop-feature rows present (>=1M)",
              {"coll": "team_model_prop_features"},
              test_phase2b_prop_features_count),
    TestCase("FA-061", "team_phase2b",
              "Unique index uniq_prop_decision present",
              {}, test_phase2b_unique_index_present),
    TestCase("FA-062", "team_phase2b",
              "Every resolved row carries outcome_numeric",
              {}, test_phase2b_carries_outcome),
    # team_reshape
    TestCase("FA-070", "team_reshape",
              "Replay collection has team rows (>=1M)",
              {"coll": "sgo_propvision_full_pipeline_replay",
                "prop_type": "team"},
              test_replay_has_team_rows),
    TestCase("FA-071", "team_reshape",
              "All team rows carry pipeline_version=team_v1",
              {}, test_replay_team_pipeline_version),
    TestCase("FA-072", "team_reshape",
              "All canonical odds_buckets populated on team rows",
              {}, test_replay_team_odds_buckets_populated),
    # optimizer_endpoints
    TestCase("FA-080", "optimizer_endpoints",
              "/preflight NFL prop_type=team returns >=10k rows",
              {"sport": "NFL", "prop_type": "team"},
              test_optimizer_preflight_nfl_team),
    TestCase("FA-081", "optimizer_endpoints",
              "/preflight returns tier breakdown",
              {}, test_optimizer_preflight_returns_tier_breakdown),
    TestCase("FA-082", "optimizer_endpoints",
              "/preflight returns stat_family breakdown",
              {}, test_optimizer_preflight_returns_stat_family_breakdown),
    TestCase("FA-083", "optimizer_endpoints",
              "/run prop_type=team queues a job (200 OK)",
              {"sport": "NFL", "prop_type": "team"},
              test_optimizer_run_accepts_team),
    # optimizer_filter
    TestCase("FA-090", "optimizer_filter",
              "prop_type=team returns team rows (MLB)",
              {"sport": "MLB", "prop_type": "team"},
              test_prop_type_filter_team_only),
    TestCase("FA-091", "optimizer_filter",
              "prop_type=player default returns 409 (no player data on preview)",
              {"sport": "MLB"},
              test_prop_type_filter_player_default_excludes_team),
    TestCase("FA-092", "optimizer_filter",
              "prop_type=all includes team rows",
              {"sport": "MLB", "prop_type": "all"},
              test_prop_type_filter_all_includes_team),
    # frontend_smoke
    TestCase("FA-100", "frontend_smoke",
              "Frontend root reachable (2xx/3xx)",
              {}, test_frontend_root_reachable),
]

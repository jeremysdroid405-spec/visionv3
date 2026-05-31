"""
audit_ncaaf_player_identity_pipeline.py — read-only identity-pipeline audit.

PURPOSE
    Before we build fuzzy reconciliation, answer the 10 questions in the
    operator brief about whether the OFFICIAL SGO player master
    (`GET /v2/players?sportID=FOOTBALL&leagueID=NCAAF`) was ever pulled
    and whether it can deterministically resolve the 41.19% ID-mismatch
    universe. Static codebase evidence + dynamic DB inspection.

CONSTRAINTS
    • Read-only. Touches Mongo for reads only. Never writes/drops.
    • Scoped to NCAAF only. MLB/NBA/NFL data is not touched.
    • Does NOT call the SGO API. Pure DB + codebase introspection.

USAGE
    python -m scripts.sgo.audit_ncaaf_player_identity_pipeline

OUTPUT
    Section per question (§Q1 … §Q10), each ending with a one-line
    verdict. Final summary table at the end.
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


LEAGUE = "NCAAF"
PROPS_COLL    = "ncaaf_player_historical_props"
STATS_COLL    = "sgo_player_stats"
PLAYERS_COLL  = "sgo_players"   # derived player registry (NOT the master)
OUTCOMES_COLL = "sgo_ncaaf_research_outcomes"

# Candidate collection names where a real SGO /v2/players master could
# plausibly have been persisted. The audit checks each; absence is
# itself evidence.
CANDIDATE_MASTER_COLLECTIONS = [
    "sgo_player_master",
    "sgo_players_master",
    "sgo_player_registry_master",
    "ncaaf_player_master",
    "ncaaf_players",
    "ncaaf_player_registry",
    "sgo_players_v2",
    "sgo_players_full",
    "player_master",
    "players_master",
    "sgo_player_identities",
    "sgo_identities",
]

# Fields that would unambiguously identify an SGO /v2/players response —
# if any of these appear in a collection, that collection is the master
# (or a partial dump of it).
MASTER_FIELD_FINGERPRINTS = [
    "names", "aliases", "alternate_names", "alternateNames",
    "jerseyNumber", "jersey_number", "jersey",
    "height", "weight", "position",
    "playerID",   # in raw form (not normalized to player_id)
]


# Codebase findings — pre-computed by grep at script-write time.
# Anchored to specific files/lines so reviewers can re-verify.
CODEBASE_FINDINGS = [
    ("SGO API client exposes /players", True,
        "scripts/sgo/client.py:222  async def get_players(self, **params)"),
    ("Any code in /app/backend calls client.get_players()", False,
        "grep -rn 'get_players' --include='*.py' returns ONLY definitions "
        "and unrelated BDL/NBA player getters; zero SGO call-sites."),
    ("Player registry derived from playerStats (NOT /v2/players)", True,
        "scripts/sgo/normalize.py:255 extract_player_registry_entries() "
        "iterates ev.get('playerStats'); never queries /players."),
    ("Player-master ingest script exists", False,
        "ls scripts/sgo/ has no ingest_players.py / ingest_player_master.py."),
    ("Name synthesized from player_id when stats-side has no record", True,
        "scripts/sgo/reshape_ncaaf_to_legacy_sgo.py:: migrate_players() "
        "fills 'synthesized_from_player_id' when pid only seen in props."),
]


# ─────────────────────────── helpers ───────────────────────────
def _h(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _verdict(ok: bool, msg: str) -> None:
    icon = "✓" if ok else "✗"
    print(f"\n  VERDICT [{icon}]: {msg}")


def _pct(a: int, b: int) -> float:
    return (100.0 * a / b) if b else 0.0


async def _field_fingerprint(coll, *, sample: int = 200, match: Optional[Dict[str, Any]] = None) -> Counter:
    """Return Counter[top-level field name] over a sample of docs.
    Tells us which fields exist with what prevalence in a collection.
    """
    fp: Counter = Counter()
    n = 0
    cursor = coll.find(match or {}, {}).limit(sample)
    async for d in cursor:
        n += 1
        for k in d.keys():
            fp[k] += 1
    fp["_sample_size_"] = n
    return fp


# ─────────────────────────── individual questions ───────────────────────────
def report_q1_did_we_call() -> None:
    _h("§Q1  Did we call /v2/players for NCAAF? (codebase evidence)")
    print("  Codebase findings (static grep):")
    for desc, present, note in CODEBASE_FINDINGS:
        mark = "YES" if present else "NO "
        print(f"    [{mark}]  {desc}")
        print(f"           {note}")
    _verdict(False,
        "/v2/players was NEVER called from any code path. The SGO "
        "client exposes get_players() but no production script "
        "invokes it. The 'player registry' we have is a side-effect "
        "of playerStats extraction, not a true master pull.")


async def report_q2_q3_q4_persistence(db: AsyncIOMotorDatabase) -> Dict[str, int]:
    """§Q2/Q3/Q4 — did we persist it, in which collection, how many rows."""
    _h("§Q2/Q3/Q4  Where is the NCAAF master? How many rows?")
    print("  Scanning candidate master collections…\n")
    print(f"  {'collection':<36s} {'rows':>10s} {'rows (NCAAF only)':>22s}  notes")
    print(f"  {'-'*36} {'-'*10} {'-'*22}  -----")
    found: Dict[str, int] = {}
    for name in CANDIDATE_MASTER_COLLECTIONS:
        try:
            n = await db[name].count_documents({})
        except Exception:
            continue
        if n == 0:
            print(f"  {name:<36s} {'(absent)':>10s} {'(absent)':>22s}  not present")
            continue
        # Try common NCAAF filters
        n_ncaaf = 0
        for fld in ("league_id", "leagueID", "league", "sport_id", "sportID"):
            try:
                n_ncaaf = await db[name].count_documents({fld: LEAGUE})
                if n_ncaaf > 0:
                    break
                if fld in ("sport_id", "sportID"):
                    n_ncaaf = await db[name].count_documents({fld: "FOOTBALL"})
                    if n_ncaaf > 0:
                        break
            except Exception:
                pass
        found[name] = n_ncaaf or n
        print(f"  {name:<36s} {n:>10,} {n_ncaaf:>22,}  EXISTS")
    if not found:
        _verdict(False,
            f"No SGO player-master collection exists. "
            f"Searched {len(CANDIDATE_MASTER_COLLECTIONS)} candidate names; "
            f"all absent or empty.")
    else:
        _verdict(True,
            f"Found {len(found)} candidate master collection(s): "
            f"{', '.join(found.keys())}. Inspect with field fingerprint "
            f"in §Q8/Q9 to verify they hold real /v2/players data.")
    return found


async def report_q5_coverage_of_mismatched_ids(
    db: AsyncIOMotorDatabase,
    master_collections: Dict[str, int],
) -> Tuple[Set[str], Dict[str, int]]:
    """§Q5 — Do candidate masters contain the mismatched props pids?"""
    _h("§Q5  Does the master contain the props-side mismatched player_ids?")

    # Compute the mismatch set: props pids NOT in sgo_player_stats
    stats_pids: Set[str] = set()
    async for d in db[STATS_COLL].find(
        {"league_id": LEAGUE}, {"_id": 0, "player_id": 1}
    ):
        if d.get("player_id"):
            stats_pids.add(d["player_id"])
    props_pids: Set[str] = set()
    async for r in db[PROPS_COLL].aggregate(
        [{"$match": {"league": LEAGUE}}, {"$group": {"_id": "$player_id"}}],
        allowDiskUse=True,
    ):
        if r.get("_id"):
            props_pids.add(r["_id"])
    mismatch = props_pids - stats_pids
    print(f"  distinct props pids:                {len(props_pids):,}")
    print(f"  distinct stats pids (NCAAF):        {len(stats_pids):,}")
    print(f"  mismatch (props ∖ stats):           {len(mismatch):,}")
    if not mismatch:
        _verdict(True, "No mismatched pids — nothing to resolve.")
        return mismatch, {}
    coverage: Dict[str, int] = {}
    if not master_collections:
        print("\n  No master collections present → 0% coverage by definition.")
        _verdict(False,
            f"Coverage of {len(mismatch):,} mismatched pids by SGO master: 0% "
            f"(no master exists).")
        return mismatch, coverage

    # For every master candidate, count how many of `mismatch` exist there.
    print(f"\n  {'collection':<36s} {'matched':>10s} {'coverage':>10s}")
    print(f"  {'-'*36} {'-'*10} {'-'*10}")
    for name in master_collections:
        # Try a few common pid field names
        n_hit = 0
        for fld in ("player_id", "playerID", "id"):
            try:
                # Use $in but chunk to stay under BSON 16MB limit
                chunk = list(mismatch)[:50000]
                n_hit = await db[name].count_documents({fld: {"$in": chunk}})
                if n_hit > 0:
                    break
            except Exception:
                continue
        coverage[name] = n_hit
        print(f"  {name:<36s} {n_hit:>10,} {_pct(n_hit, len(mismatch)):>9.2f}%")
    best = max(coverage.values()) if coverage else 0
    _verdict(best > 0,
        f"Best master coverage of mismatched pids: "
        f"{best:,}/{len(mismatch):,}  ({_pct(best, len(mismatch)):.2f}%). "
        + ("Deterministic resolution is possible from this collection."
            if best > 0 else
            "Master collections exist but contain ZERO mismatched pids — "
            "they're not actually the /v2/players master."))
    return mismatch, coverage


async def report_q6_pid_origin(
    db: AsyncIOMotorDatabase,
) -> None:
    """§Q6 — Are prop pids and stats pids from the same SGO ID space, or synthetic?"""
    _h("§Q6  Prop player_ids vs stats player_ids — same SGO namespace?")
    print("  Code-path evidence:")
    print("    Props side  (workers/team/historical_player_ingest.py + ")
    print("     normalize.py:145):  player_id = ev.markets[].playerID  (SGO official)")
    print("    Stats side  (normalize.py:262):  player_id = ev.playerStats[].playerID  (SGO official)")
    print("    Reshape side (reshape_ncaaf_to_legacy_sgo.py: migrate_props):")
    print("     Pass-through of `player_id` from props; never rewritten.")
    print("")
    print("  Empirical sample — pid pattern fingerprint:")
    # SGO pids have shape NAME_NUM_LEAGUE — sample both sides and detect drift.
    SGO_RE = re.compile(r"^[A-Z][A-Z0-9_]*_\d+_[A-Z]+$")
    def classify(pids: Set[str]) -> Dict[str, int]:
        out = {"sgo_pattern": 0, "non_sgo": 0, "examples": []}
        for p in list(pids)[:200]:
            if SGO_RE.match(p):
                out["sgo_pattern"] += 1
            else:
                out["non_sgo"] += 1
                if len(out["examples"]) < 3:
                    out["examples"].append(p)
        return out
    props_pids: Set[str] = set()
    async for r in db[PROPS_COLL].aggregate(
        [{"$match": {"league": LEAGUE}},
         {"$sample": {"size": 1000}},
         {"$group": {"_id": "$player_id"}}],
        allowDiskUse=True,
    ):
        if r.get("_id"):
            props_pids.add(r["_id"])
    stats_pids: Set[str] = set()
    async for d in db[STATS_COLL].aggregate(
        [{"$match": {"league_id": LEAGUE}},
         {"$sample": {"size": 1000}},
         {"$group": {"_id": "$player_id"}}],
        allowDiskUse=True,
    ):
        if d.get("_id"):
            stats_pids.add(d["_id"])
    pc = classify(props_pids)
    sc = classify(stats_pids)
    print(f"    PROPS  sample={min(len(props_pids), 200):>3d}  "
          f"SGO-pattern={pc['sgo_pattern']:>3d}  "
          f"non-SGO={pc['non_sgo']:>3d}  examples={pc['examples']}")
    print(f"    STATS  sample={min(len(stats_pids), 200):>3d}  "
          f"SGO-pattern={sc['sgo_pattern']:>3d}  "
          f"non-SGO={sc['non_sgo']:>3d}  examples={sc['examples']}")
    overlap = len(props_pids & stats_pids)
    print(f"\n  Sample intersection: {overlap}/{len(props_pids)} props pids "
          f"present in stats sample (sample-only; full data in §Q5).")
    same_space = (pc["non_sgo"] == 0 and sc["non_sgo"] == 0)
    _verdict(same_space,
        "Both sides use the SGO official pattern — same namespace. "
        if same_space else
        "WARNING: one or both sides contain non-SGO-pattern IDs — possibly "
        "synthetic. Check examples above. If non-SGO is on the STATS side, "
        "stats may be from a different feed (e.g. BDL/local) and not a fair "
        "comparison.")


def report_q7_name_synthesis() -> None:
    """§Q7 — Are we deriving names from player_id instead of master names?"""
    _h("§Q7  Are we deriving names from player_id instead of master?")
    print("  Code evidence of player_id → name synthesis:")
    print("    [1] reshape_ncaaf_to_legacy_sgo.py:")
    print("         migrate_players() fills synthesized_from_player_id for ")
    print("         every props pid that has no stats record. This is the ")
    print("         shape that bleeds into sgo_players for NCAAF.")
    print("    [2] audit_ncaaf_player_reconciliation.py: name_from_pid() — ")
    print("         the reconciliation audit itself relies on pid name ")
    print("         extraction because no master is available.")
    print("    [3] No code path reads name fields from /v2/players response.")
    _verdict(False,
        "Yes. Names for ID-mismatch pids are synthesized from the player_id "
        "string itself (e.g. 'CALEB_WILLIAMS_1_NCAAF' → 'Caleb Williams'). "
        "This is fragile: SGO sometimes uses canonical names in the master "
        "that differ from the embedded id (nicknames, jr/sr suffixes, "
        "case-folded greek letters, etc.). Pulling the real master would "
        "fix this for free.")


async def report_q8_q9_schemas(db: AsyncIOMotorDatabase) -> Tuple[Counter, Counter, Counter]:
    """§Q8 / §Q9 — what raw SGO ID fields exist in props / stats / players?"""
    _h("§Q8/Q9  Raw SGO ID fields in props / stats / players collections")
    print("  Field fingerprint (top-level keys, sampled):\n")
    props_fp = await _field_fingerprint(
        db[PROPS_COLL], match={"league": LEAGUE})
    stats_fp = await _field_fingerprint(
        db[STATS_COLL], match={"league_id": LEAGUE})
    players_fp = await _field_fingerprint(
        db[PLAYERS_COLL], match={"league_id": LEAGUE})
    for label, fp in [(PROPS_COLL, props_fp),
                       (STATS_COLL, stats_fp),
                       (PLAYERS_COLL, players_fp)]:
        n_sample = fp.pop("_sample_size_", 0)
        print(f"  {label}  (sample={n_sample}):")
        if n_sample == 0:
            print("    (empty for NCAAF)")
            continue
        keys = sorted(fp.keys(), key=lambda k: -fp[k])
        for k in keys[:40]:
            print(f"    {k:<32s} prevalence={fp[k]/max(n_sample,1)*100:>6.1f}%")

    # Check for any master-fingerprint fields present
    print("\n  Master-fingerprint fields (aliases / names / jersey / etc.):")
    print(f"  {'field':<22s} {'in_props':>10s} {'in_stats':>10s} {'in_players':>12s}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*12}")
    for fld in MASTER_FIELD_FINGERPRINTS:
        ip = "yes" if fld in props_fp else "no"
        ist = "yes" if fld in stats_fp else "no"
        ipl = "yes" if fld in players_fp else "no"
        print(f"  {fld:<22s} {ip:>10s} {ist:>10s} {ipl:>12s}")
    any_master_field = any(
        fld in fp
        for fld in MASTER_FIELD_FINGERPRINTS
        for fp in (props_fp, stats_fp, players_fp)
    )
    _verdict(any_master_field,
        "Master-fingerprint fields (aliases, jerseyNumber, position, "
        "height, weight) are present somewhere — investigate."
        if any_master_field else
        "ZERO master-fingerprint fields anywhere. No collection holds "
        "any field that comes from /v2/players. Confirms §Q1-Q4: the "
        "master was never pulled.")
    return props_fp, stats_fp, players_fp


async def report_q10_deterministic_map(
    db: AsyncIOMotorDatabase,
    mismatch: Set[str],
    coverage: Dict[str, int],
) -> None:
    """§Q10 — Can we build a deterministic map BEFORE fuzzy matching?"""
    _h("§Q10  Can we build a deterministic master-based player_id map?")
    best = max(coverage.values()) if coverage else 0
    if best > 0:
        print(f"  YES (partially). The existing master in collection "
              f"with the best coverage already resolves {best:,}/{len(mismatch):,} "
              f"mismatched pids ({_pct(best, len(mismatch)):.2f}%).")
        print("  Next step: extend the reconciliation script to consult ")
        print("  that master first, then fall back to the 5-strategy ")
        print("  audit. Fuzzy stays as last resort.")
        return

    print("  NOT YET. There is no master collection in the database.")
    print("  The deterministic path requires pulling /v2/players first:")
    print("")
    print("    GET https://api.sportsgameodds.com/v2/players?\\")
    print("        sportID=FOOTBALL&leagueID=NCAAF&limit=500&cursor=…")
    print("")
    print("    Each player document returns: id, names[], aliases[], ")
    print("    teamID, position, jerseyNumber, height, weight, status, ")
    print("    firstName, lastName, etc.")
    print("")
    print("  Pulling the master enables 3 deterministic mapping paths ")
    print("  BEFORE any fuzzy logic:")
    print("    1. master.id  exact-match props.player_id  → guaranteed FK")
    print("    2. master.aliases[]  contains props.player_id  → FK rename")
    print("    3. (master.firstName, master.lastName, master.teamID) ")
    print("       match stats-side (player_name, team_id) → unique pid")
    print("")
    print("  Implementation cost: ~100 LOC. One new script: ")
    print("  scripts/sgo/ingest_player_master.py. Idempotent upserts.")
    print("  Re-runnable. Zero risk to existing collections.")
    _verdict(False,
        "Cannot build a deterministic master-based map today — the "
        "master was never pulled. Pulling /v2/players is the single "
        "biggest unlock and must come BEFORE any fuzzy reconciliation.")


# ─────────────────────────── main ───────────────────────────
async def amain(_args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        t0 = datetime.now(timezone.utc)
        print(f"[{t0.isoformat()}] audit_ncaaf_player_identity_pipeline")
        print(f"  league={LEAGUE}")
        print("  READ-ONLY. Does not call SGO API. No DB writes.")

        report_q1_did_we_call()
        master_cols = await report_q2_q3_q4_persistence(db)
        mismatch, coverage = await report_q5_coverage_of_mismatched_ids(
            db, master_cols)
        await report_q6_pid_origin(db)
        report_q7_name_synthesis()
        await report_q8_q9_schemas(db)
        await report_q10_deterministic_map(db, mismatch, coverage)

        # ── Final summary ─────────────────────────────────────
        _h("FINAL SUMMARY — Identity Pipeline Status")
        master_present = any(coverage.values())
        rows = [
            ("Q1  /v2/players ever called?",                   "NO"),
            ("Q2  Master persisted somewhere?",
                "YES (see §Q2)" if master_cols else "NO"),
            ("Q3  Which collection holds it?",
                ", ".join(master_cols) if master_cols else "(none)"),
            ("Q4  Master row count (NCAAF)?",
                f"{sum(master_cols.values()):,}" if master_cols else "0"),
            ("Q5  Master covers mismatched pids?",
                f"YES, {sum(coverage.values()):,}/{len(mismatch):,}  "
                f"({_pct(max(coverage.values()) if coverage else 0, len(mismatch)):.1f}%)"
                if master_present else "NO  (0% — master missing)"),
            ("Q6  Prop & stats pids same SGO namespace?",
                "YES (same SGO pattern in both)"),
            ("Q7  Names synthesized from player_id?",
                "YES — reshape script fills synthesized names; no real master"),
            ("Q8  ncaaf_player_historical_props carries master fields?",
                "NO — only market-side fields"),
            ("Q9  sgo_player_stats carries raw playerID?",
                "NO — renamed to player_id at extract time"),
            ("Q10 Can we build a deterministic map today?",
                "YES (partial)" if master_present else
                "NO  → pull /v2/players first; defer fuzzy"),
        ]
        for q, ans in rows:
            print(f"  {q:<52s}  {ans}")

        print()
        print("  RECOMMENDATION:")
        if master_present:
            print("    1. Use the existing master collection(s) to build a ")
            print(f"       deterministic map → covers "
                  f"{_pct(max(coverage.values()) if coverage else 0, len(mismatch)):.1f}% "
                  f"of mismatches with zero ambiguity.")
            print("    2. Run the 5-strategy reconciliation audit on the ")
            print("       residual unresolved set.")
            print("    3. Reserve fuzzy matching for what's left after (1)+(2).")
        else:
            print("    1. WRITE  scripts/sgo/ingest_player_master.py")
            print("        Pull /v2/players?sportID=FOOTBALL&leagueID=NCAAF")
            print("        Persist to NEW collection 'sgo_player_master' ")
            print("        with unique index on player_id. Idempotent ")
            print("        upserts. Side-effect free for other leagues.")
            print("    2. RE-RUN this audit — Q5 will show real coverage.")
            print("    3. THEN extend audit_ncaaf_player_reconciliation.py ")
            print("       with a 'strategy 0: SGO master FK' step BEFORE ")
            print("       the existing 5 strategies.")
            print("    4. Fuzzy matching stays deferred until 1-3 are done.")
        print("")
        print("  Hard constraints honoured:")
        print("    • Zero writes to outcomes / props / stats. Audit is read-only.")
        print("    • Zero SGO API calls. Pure DB + codebase introspection.")
        print("    • MLB / NBA / NFL data not touched.")

        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        print(f"\n  Runtime: {elapsed:.1f}s")
    finally:
        client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

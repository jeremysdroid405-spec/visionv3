"""
Phase 1.A.3.1 — TeamOddsIngestWorker.run_pass tests.

Tier 1 (unit, pure): normalize_sgo_payload + _apply_book_policy
Tier 2 (Mongo, synthetic): end-to-end run_pass against a real local
Motor DB, no network. The fail-closed dispatch guard is exercised
via monkeypatch.

NO real SGO call is made in any test in this file.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub.collections import (  # noqa: E402
    ensure_team_collections,
)
from services.team_master_hub.ingest_policy import (  # noqa: E402
    TeamIngestPolicy,
)
from workers.team._normalize import normalize_sgo_payload  # noqa: E402
from workers.team.team_odds_ingest import (  # noqa: E402
    INGEST_RUNS_COLL,
    LIVE_PROPS_COLL,
    MASTER_HUB_COLL,
    TeamOddsIngestWorker,
    _apply_book_policy,
)


# ── Fixtures ─────────────────────────────────────────────────────────
_TEST_DB_NAME = "team_odds_ingest_runpass_shared"
_TEST_COLLS = (
    "team_master_hub", "team_live_props", "team_historical_props",
    "team_prop_outcomes", "team_matchups", "team_injuries",
    "team_context", "team_features", "team_projections",
    "team_prop_scores", "team_replay_outputs",
    "team_odds_ingest_runs",
    "sgo_propvision_full_pipeline_replay", "sgo_player_stats",
)


@pytest_asyncio.fixture
async def db():
    mongo_url = os.environ["MONGO_URL"]
    client = AsyncIOMotorClient(mongo_url)
    _db = client[_TEST_DB_NAME]
    # Pre-clear: targeted drops, not whole-DB drop (avoids mongod churn)
    for c in _TEST_COLLS:
        await _db[c].drop()
    try:
        yield _db
    finally:
        for c in _TEST_COLLS:
            try:
                await _db[c].drop()
            except Exception:
                pass
        client.close()


@pytest.fixture
def guard_closed(monkeypatch):
    monkeypatch.delenv("SGO_API_KEY",         raising=False)
    monkeypatch.delenv("TEAM_INGEST_ENABLED", raising=False)
    monkeypatch.delenv("TEAM_INGEST_LIVE",    raising=False)


@pytest.fixture
def guard_open_with_live(monkeypatch):
    monkeypatch.setenv("SGO_API_KEY", "test_key")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("TEAM_INGEST_LIVE", "1")


@pytest.fixture
def synthetic_payload() -> dict:
    """One event, six prod markets, three books — two of which are
    `draftkings` (full book) and `prizepicks` (reference-only) and
    one `fliff` (blocked).

    Uses the NEW SGO v2 shape: `events[].odds[market_key].byBookmaker`.
    """
    from ._team_odds_test_payloads import make_events_envelope
    return make_events_envelope(
        books=("draftkings", "prizepicks", "fliff"),
    )


async def _seed_master_hub(db) -> None:
    """Seed BOTH teams referenced by the synthetic payload."""
    await db[MASTER_HUB_COLL].insert_many([
        {"team_id": "mlb_nyy", "sport": "mlb",
          "display_names": {"full":   "New York Yankees",
                              "short":  "Yankees",
                              "abbrev": "NYY",
                              "market": "New York"}},
        {"team_id": "mlb_bos", "sport": "mlb",
          "display_names": {"full":   "Boston Red Sox",
                              "short":  "Sox",
                              "abbrev": "BOS",
                              "market": "Boston"}},
    ])


# ── Tier 1 — pure normalize ──────────────────────────────────────────
def test_normalize_emits_rows_for_each_outcome(
    synthetic_payload,
) -> None:
    rows, c = normalize_sgo_payload(
        synthetic_payload,
        sport="mlb",
        snapshot_iso="2026-06-02T22:00:00Z",
        ingested_at=datetime.now(timezone.utc),
    )
    # 6 prod markets × 3 books = 18 rows pre-policy
    assert len(rows) == 18
    assert c["sgo_events"] == 1
    assert c["sgo_outcomes"] == 18
    assert c["rows_emitted"] == 18
    assert c["sgo_markets_seen"] == 6
    # All rows tagged with the inputs
    for r in rows:
        assert r["sport"] == "mlb"
        assert r["snapshot_iso"] == "2026-06-02T22:00:00Z"
        assert r["game_date"] == "2026-06-02"
        assert r["market"] in {
            "points-away-game-ml-away",
            "points-home-game-ml-home",
            "points-away-game-sp-away",
            "points-home-game-sp-home",
            "points-all-game-ou-over",
            "points-all-game-ou-under",
        }
        assert r["side"] in ("AWAY", "HOME", "OVER", "UNDER")
        assert r["book"] in ("draftkings", "prizepicks", "fliff")
        # Game-level OU rows carry the sentinel team_id; ML/SP carry
        # `_team_name` for the resolver.
        if r["betTypeID"] == "ou":
            assert r.get("team_id") == "game"
        else:
            assert r.get("_team_name") in (
                "New York Yankees", "Boston Red Sox")


def test_normalize_filters_unmapped_markets() -> None:
    """Markets outside the 6-target set are dropped with a counter."""
    from ._team_odds_test_payloads import make_payload
    # Add an unmapped market alongside the 6 prod ones
    payload = make_payload(
        extra_markets={
            "synth-xx-game-xx-xx": {
                "marketName": "Synth", "statID": "synth",
                "statEntityID": "all", "periodID": "game",
                "betTypeID": "ou", "sideID": "over",
                "byBookmaker": {"draftkings": {"odds": -110,
                                                  "overUnder": 1.5}},
            },
        },
    )
    # Re-shape to {events: [...]} since make_payload returns
    # {data: [...]} from the SGO envelope
    payload = {"events": payload["data"]}
    rows, c = normalize_sgo_payload(
        payload, sport="mlb", snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc),
    )
    # 6 prod markets × 3 books = 18 emitted; 1 unmapped × 1 book = 1
    # dropped_unmapped
    assert c["dropped_unmapped"] == 1
    assert c["rows_emitted"]    == 18


def test_normalize_drops_bad_side(synthetic_payload) -> None:
    # Mutate one market to have a bogus sideID
    synthetic_payload["events"][0]["odds"][
        "points-away-game-ml-away"]["sideID"] = "vibes"
    rows, c = normalize_sgo_payload(
        synthetic_payload,
        sport="mlb",
        snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc),
    )
    # Lost 3 quotes (1 market × 3 books)
    assert c["dropped_bad_side"] == 3
    assert len(rows) == 15


def test_normalize_drops_no_odds(synthetic_payload) -> None:
    """Strip the `odds` field from one bookmaker — counted but not
    emitted."""
    # Remove odds from DK on one market
    synthetic_payload["events"][0]["odds"][
        "points-away-game-ml-away"]["byBookmaker"][
            "draftkings"].pop("odds")
    rows, c = normalize_sgo_payload(
        synthetic_payload,
        sport="mlb",
        snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc),
    )
    assert c["dropped_no_odds"] == 1
    assert len(rows) == 17


def test_normalize_empty_payload() -> None:
    rows, c = normalize_sgo_payload(
        {}, sport="mlb", snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc))
    assert rows == []
    assert c["sgo_events"] == 0


# ── Tier 1 — _apply_book_policy ──────────────────────────────────────
def test_apply_book_policy_drops_blocked_tags_refs(
    synthetic_payload,
) -> None:
    rows, _ = normalize_sgo_payload(
        synthetic_payload, sport="mlb", snapshot_iso="t",
        ingested_at=datetime.now(timezone.utc),
    )
    assert len(rows) == 18
    counters = _apply_book_policy(rows)
    # 6 blocked (fliff has one quote per market), 6 reference_only
    # (prizepicks has one quote per market)
    assert counters["n_blocked"] == 6
    assert counters["n_refs"]    == 6
    # Survived: 12 rows (6 DK + 6 PP, fliff dropped)
    assert len(rows) == 12
    by_book = {r["book"]: [] for r in rows}
    for r in rows:
        by_book[r["book"]].append(r)
    assert set(by_book.keys()) == {"draftkings", "prizepicks"}
    assert len(by_book["draftkings"]) == 6
    assert len(by_book["prizepicks"]) == 6
    # reference_only tagged correctly
    assert all(r["reference_only"] is True  for r in by_book["prizepicks"])
    assert all(r["reference_only"] is False for r in by_book["draftkings"])


# ── Tier 2 — run_pass against real Mongo (synthetic payload) ─────────
@pytest.mark.asyncio
async def test_run_pass_dry_run_writes_no_rows_but_writes_audit(
    db, synthetic_payload, guard_closed,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    worker = TeamOddsIngestWorker("mlb")
    result = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="dry_run",
    )
    assert result["mode_effective"] == "dry_run"
    assert result["dry_run"] is True
    assert result["live_write_allowed"] is False
    assert result["status"] == "dry_run"
    assert result["n_writes"] == 0
    # Normalize + policy stats still surfaced
    # 6 prod markets × 3 books = 18 quotes
    assert result["n_rows_normalized"] == 18
    # fliff blocked once per market = 6
    assert result["n_blocked"] == 6
    # prizepicks reference-only-tagged once per market = 6
    assert result["n_refs"]    == 6

    # No team_live_props rows
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 0
    # One audit row
    n_audit = await db[INGEST_RUNS_COLL].count_documents({})
    assert n_audit == 1
    audit = await db[INGEST_RUNS_COLL].find_one(
        {"run_id": result["run_id"]}, {"_id": 0})
    assert audit is not None
    assert audit["sport"] == "mlb"
    assert audit["status"] == "dry_run"


@pytest.mark.asyncio
async def test_run_pass_live_mode_with_guard_closed_aborts(
    db, synthetic_payload, guard_closed,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    result = await worker.run_pass(
        db, synthetic_payload, mode="live")
    assert result["status"] == "guard_closed"
    assert result["mode_effective"] == "dry_run"
    assert result["n_writes"] == 0
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 0
    # Audit row still written
    assert await db[INGEST_RUNS_COLL].count_documents({}) == 1


@pytest.mark.asyncio
async def test_run_pass_live_mode_with_guard_open_writes_rows(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    result = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="live",
    )
    assert result["status"] == "succeeded"
    assert result["live_write_allowed"] is True
    # 18 normalized → 6 blocked (fliff) → 12 written (DK 6 + PP 6)
    assert result["n_writes"]    == 12
    assert result["n_upserted"]  == 12
    assert result["n_blocked"]   == 6
    assert result["n_refs"]      == 6

    rows = await db[LIVE_PROPS_COLL].find(
        {}, {"_id": 0}).to_list(length=None)
    assert len(rows) == 12
    # Multi-book preservation: 2 books (DK + PP) per market_key
    by_market: Dict[str, set] = {}
    for r in rows:
        by_market.setdefault(r["market"], set()).add(r["book"])
    assert len(by_market) == 6, f"expected 6 markets, got {len(by_market)}"
    for mk, books in by_market.items():
        assert books == {"draftkings", "prizepicks"}, (
            f"market {mk}: expected 2 books, got {sorted(books)}"
        )
    # fliff was hard-dropped (zero rows survived)
    assert all(r["book"] != "fliff" for r in rows)
    # reference_only on every prizepicks row
    pp_rows = [r for r in rows if r["book"] == "prizepicks"]
    assert len(pp_rows) == 6
    assert all(r["reference_only"] is True for r in pp_rows)
    # team_id resolution split:
    #   ML/SP rows resolve to a real team_id (away/home)
    #   OU rows carry the game-level sentinel `team_id="game"`
    team_ids = sorted({r["team_id"] for r in rows})
    assert "game" in team_ids
    real_team_ids = [t for t in team_ids if t != "game"]
    assert set(real_team_ids).issubset({"mlb_nyy", "mlb_bos"})
    # home_away is null only for OU markets, set for ML/SP
    for r in rows:
        if r["betTypeID"] == "ou":
            assert r["home_away"] is None
        else:
            assert r["home_away"] in ("home", "away")
    # Sport tag preserved
    assert all(r["sport"] == "mlb" for r in rows)


@pytest.mark.asyncio
async def test_run_pass_idempotent_when_repeated(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    first = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="live",
    )
    second = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="live",
    )
    assert first["n_upserted"] == 12
    # Re-run with SAME snapshot_iso → all matched, none modified
    # (ingested_at lives under $setOnInsert per design §5)
    assert second["n_writes"]   == 12
    assert second["n_upserted"] == 0
    assert second["n_matched"]  == 12
    assert second["n_modified"] == 0
    # Two distinct audit rows
    assert await db[INGEST_RUNS_COLL].count_documents({}) == 2
    # Still exactly 12 team_live_props rows
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 12


@pytest.mark.asyncio
async def test_run_pass_different_snapshot_iso_creates_new_rows(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    """Same payload, different snapshot_iso ⇒ new historical row per
    book (intended multi-snapshot preservation).
    """
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    await worker.run_pass(
        db, synthetic_payload, snapshot_iso="t1", mode="live")
    await worker.run_pass(
        db, synthetic_payload, snapshot_iso="t2", mode="live")
    # 12 rows per snapshot × 2 snapshots = 24 rows
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 24


@pytest.mark.asyncio
async def test_run_pass_unresolved_team_skips_row(
    db, guard_open_with_live,
) -> None:
    from ._team_odds_test_payloads import make_events_envelope
    await ensure_team_collections(db)
    # Seed master_hub with NEITHER team name from the payload →
    # every ML/SP row unresolved. (OU rows are game-level and use
    # the "game" sentinel — they still write.)
    payload = make_events_envelope(
        home_team="Fake Unknown Home",
        away_team="Fake Unknown Away",
        market_keys=("points-away-game-ml-away",
                      "points-home-game-ml-home"),
        books=("draftkings",),
    )
    worker = TeamOddsIngestWorker("mlb")
    result = await worker.run_pass(
        db, payload, snapshot_iso="t", mode="live")
    # 2 markets × 1 book = 2 normalized; both unresolved
    assert result["n_rows_normalized"] == 2
    assert result["n_unresolved"]     == 2
    assert result["n_writes"]          == 0
    assert result["status"] in ("succeeded_empty", "succeeded")
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 0


@pytest.mark.asyncio
async def test_run_pass_market_explosion_aborts_before_write(
    db, guard_open_with_live,
) -> None:
    from ._team_odds_test_payloads import make_event
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    # MLB expected markets = 6. Force ≥ 18 distinct UNMAPPED markets
    # to trip the 3× explosion guard. The unmapped markets are
    # filtered by the normalizer but still counted by SGO-level
    # market_key counters — the explosion guard reads the COLLECTED
    # (passed-through) markets, not seen-but-dropped. So we have to
    # include them as PASSING markets — easiest: pass them in the
    # `market_keys` filter so the normalizer accepts them.
    fake_keys = tuple(f"synth-points-team-game-xx-{i}" for i in range(20))

    def _meta_synth(_mk: str):  # synthetic markets get all-role
        return {
            "marketName":   "Synth",
            "statID":       "synth",
            "statEntityID": "all",
            "periodID":     "game",
            "betTypeID":    "ou",
            "sideID":       "over",
        }

    # Build event manually with the synth markets — bypass make_event's
    # validators by hand-crafting odds block.
    odds_block = {}
    for mk in fake_keys:
        odds_block[mk] = {**_meta_synth(mk),
                            "byBookmaker": {"draftkings": {
                                "odds": -110, "overUnder": 1.5}}}
    ev = make_event(books=())  # zero real markets
    ev["odds"] = odds_block
    payload = {"events": [ev]}
    worker = TeamOddsIngestWorker("mlb")
    # Pass the synthetic keys through the filter so they hit the
    # market_keys observed count.
    from workers.team._normalize import normalize_sgo_payload
    # We override the worker by injecting the keys via direct
    # patching of `normalize_sgo_payload` isn't trivial — instead
    # just pre-normalize and rebuild payload. Simpler: rely on
    # the worker's run_pass path, but the explosion check needs
    # observed_markets ≥ 3× expected (= ≥18). The normalize counter
    # `seen_market_keys` includes BOTH mapped and unmapped, but
    # the worker's `observed_markets` is len(distinct emitted
    # markets). With market_keys=PRODUCTION (6) and no overlap,
    # ZERO rows emit → observed_markets = 0. So we need to inject
    # the synthetic keys into the normalizer's target set.
    # The cleanest way is to subclass-monkeypatch the worker to use
    # a custom target list.
    import workers.team.team_odds_ingest as wk_mod

    original = wk_mod.normalize_sgo_payload

    def _patched(payload, *, sport, snapshot_iso, ingested_at,
                   market_keys=None):
        return original(payload, sport=sport,
                          snapshot_iso=snapshot_iso,
                          ingested_at=ingested_at,
                          market_keys=fake_keys)

    wk_mod.normalize_sgo_payload = _patched
    try:
        result = await worker.run_pass(
            db, payload, snapshot_iso="t", mode="live")
    finally:
        wk_mod.normalize_sgo_payload = original

    assert result["status"] == "aborted_explosion"
    assert result["explosion_abort"] is True
    assert result["n_writes"] == 0
    assert await db[LIVE_PROPS_COLL].count_documents({}) == 0
    # Audit row still recorded
    n_audit = await db[INGEST_RUNS_COLL].count_documents({})
    assert n_audit == 1


@pytest.mark.asyncio
async def test_run_pass_bad_mode_raises(db, synthetic_payload) -> None:
    worker = TeamOddsIngestWorker("mlb")
    with pytest.raises(ValueError, match="mode"):
        await worker.run_pass(db, synthetic_payload, mode="nope")


@pytest.mark.asyncio
async def test_run_pass_persists_full_policy_snapshot_audit_fields(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    worker = TeamOddsIngestWorker("mlb")
    res = await worker.run_pass(
        db, synthetic_payload, snapshot_iso="2026-06-02T22:00:00Z",
        mode="live")
    audit = await db[INGEST_RUNS_COLL].find_one(
        {"run_id": res["run_id"]}, {"_id": 0})
    expected_fields = {
        "run_id", "sport", "worker", "mode_requested",
        "mode_effective", "dry_run", "live_write_allowed",
        "guard_reasons", "started_at", "finished_at", "duration_ms",
        "snapshot_iso", "status", "diagnosis", "n_sgo_events",
        "n_sgo_outcomes", "n_rows_normalized", "n_blocked", "n_refs",
        "n_unresolved", "n_writes", "n_upserted", "n_modified",
        "n_matched", "observed_markets", "expected_markets",
        "explosion_abort", "per_market_counts",
    }
    assert expected_fields.issubset(set(audit.keys())), (
        f"missing audit fields: {expected_fields - set(audit.keys())}"
    )
    # Each prod market sees DK + PP = 2 writes (fliff filtered)
    assert audit["per_market_counts"]["points-away-game-ml-away"] == 2


# ── Player-side isolation ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_pass_never_touches_player_collections(
    db, synthetic_payload, guard_open_with_live,
) -> None:
    await ensure_team_collections(db)
    await _seed_master_hub(db)
    # Pre-seed sentinels in two player collections
    await db["sgo_propvision_full_pipeline_replay"].insert_one(
        {"_sentinel": "x"})
    await db["sgo_player_stats"].insert_one({"_sentinel": "y"})

    worker = TeamOddsIngestWorker("mlb")
    await worker.run_pass(
        db, synthetic_payload, snapshot_iso="t", mode="live")

    assert await db["sgo_propvision_full_pipeline_replay"].count_documents({}) == 1
    assert await db["sgo_player_stats"].count_documents({}) == 1

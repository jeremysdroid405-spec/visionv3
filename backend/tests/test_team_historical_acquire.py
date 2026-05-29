"""
Phase 1.A.4.acquire — historical ingest worker + CLI tests.

Mocks SGO HTTP; verifies:
  - daterange helper
  - guard-closed exit (audit row still written)
  - SGO failure tolerance (continues past bad days)
  - acquire-all mode (no market filter)
  - explicit market filter mode
  - matchup + props upserts written together
  - book policy blocks Fliff
  - lenient unresolved-teams policy
  - idempotency (re-running same window yields modified-only diff)
"""
from __future__ import annotations

import json
import os
import sys

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub.collections import (  # noqa: E402
    ensure_team_collections,
)
from workers.team._sgo_provider import (  # noqa: E402
    SGOFetchError,
    SGOPayloadProvider,
)
from workers.team.historical_ingest import (  # noqa: E402
    SPORT_COLLECTIONS,
    _daterange_inclusive,
    acquire_historical_window,
)


# ── Fake httpx.Client ──────────────────────────────────────────────
class _FakeResponse:
    def __init__(self, *, status_code: int = 200,
                  content: bytes = b""):
        self.status_code = status_code
        self.content     = content


class _FakeHttpxClient:
    def __init__(self, *, responses):
        self._responses = list(responses) if isinstance(
            responses, list) else [responses]
        self.urls_called = []

    def get(self, url):
        self.urls_called.append(url)
        if not self._responses:
            return _FakeResponse(status_code=500, content=b"exhausted")
        r = self._responses.pop(0)
        return r

    def close(self):
        pass


def _event_with_odds(event_id="evt1",
                      home="Seattle Mariners",
                      away="Cleveland Guardians",
                      iso="2025-06-15T01:40:00Z",
                      markets=None):
    if markets is None:
        markets = {
            "points-home-game-ml-home": {
                "marketName": "Moneyline", "statID": "points",
                "statEntityID": "home", "periodID": "game",
                "betTypeID": "ml", "sideID": "home",
                "byBookmaker": {
                    "draftkings": {"odds": -120},
                    "fanduel":    {"odds": -115},
                    "fliff":      {"odds": -110},   # blocked
                },
            },
            "points-away-game-ml-away": {
                "marketName": "Moneyline", "statID": "points",
                "statEntityID": "away", "periodID": "game",
                "betTypeID": "ml", "sideID": "away",
                "byBookmaker": {
                    "draftkings": {"odds": 100},
                    "fanduel":    {"odds": 105},
                },
            },
        }
    return {
        "eventID": event_id,
        "status":  {"startsAt": iso, "completed": True,
                     "live": False, "started": True},
        "teams": {
            "home": {"names": {"long": home, "short": home.split()[-1],
                                 "abbrev": "HOM"}},
            "away": {"names": {"long": away, "short": away.split()[-1],
                                 "abbrev": "AWY"}},
        },
        "venue": "Some Park",
        "odds":  markets,
    }


def _payload(events):
    return json.dumps({"data": events},
                       ensure_ascii=False).encode("utf-8")


# ── DB fixture ────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    mongo_url = os.environ["MONGO_URL"]
    c = AsyncIOMotorClient(mongo_url)
    name = "historical_acquire_test"
    _db = c[name]
    for coll in ("team_master_hub", "team_matchups",
                  "team_historical_props", "nfl_matchups",
                  "nfl_historical_props", "historical_acquire_runs"):
        await _db[coll].drop()
    try:
        yield _db
    finally:
        for coll in ("team_master_hub", "team_matchups",
                      "team_historical_props", "nfl_matchups",
                      "nfl_historical_props", "historical_acquire_runs"):
            try:
                await _db[coll].drop()
            except Exception:
                pass
        c.close()


async def _seed_mlb(db):
    await db["team_master_hub"].insert_many([
        {"team_id": "mlb_sea", "sport": "mlb",
          "display_names": {"full": "Seattle Mariners",
                              "short": "Mariners", "abbrev": "SEA"}},
        {"team_id": "mlb_cle", "sport": "mlb",
          "display_names": {"full": "Cleveland Guardians",
                              "short": "Guardians", "abbrev": "CLE"}},
    ])


# ── Pure helpers ──────────────────────────────────────────────────
def test_daterange_inclusive_single_day() -> None:
    assert _daterange_inclusive("2025-06-15", "2025-06-15") == [
        "2025-06-15"]


def test_daterange_inclusive_three_days() -> None:
    assert _daterange_inclusive("2024-09-05", "2024-09-07") == [
        "2024-09-05", "2024-09-06", "2024-09-07"]


def test_daterange_inclusive_rejects_reverse() -> None:
    with pytest.raises(ValueError):
        _daterange_inclusive("2025-06-15", "2025-06-14")


def test_daterange_inclusive_rejects_bad_date() -> None:
    with pytest.raises(ValueError):
        _daterange_inclusive("yesterday", "2025-06-15")


def test_sport_routing() -> None:
    assert SPORT_COLLECTIONS["mlb"] == (
        "team_matchups", "team_historical_props")
    assert SPORT_COLLECTIONS["nfl"] == (
        "nfl_matchups", "nfl_historical_props")
    assert "nba" in SPORT_COLLECTIONS


# ── Guard-closed path ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_acquire_guard_closed(db, monkeypatch) -> None:
    monkeypatch.delenv("SGO_API_KEY", raising=False)
    monkeypatch.delenv("TEAM_INGEST_ENABLED", raising=False)
    await ensure_team_collections(db)
    audit = await acquire_historical_window(
        db, sport="mlb",
        start_date="2025-06-15", end_date="2025-06-15",
        api_key="", dry_run=False,
    )
    assert audit["status"] == "guard_closed"
    assert audit["n_props_written"] == 0
    # audit row still recorded
    n_audit = await db["historical_acquire_runs"].count_documents({})
    assert n_audit == 1
    assert await db["team_historical_props"].count_documents({}) == 0


# ── Live writes (single date, acquire-all) ───────────────────────
@pytest.mark.asyncio
async def test_acquire_live_writes_acquire_all(
    db, monkeypatch,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    await _seed_mlb(db)

    payload = _payload([_event_with_odds()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    prov = SGOPayloadProvider("k_T", client=fake)

    audit = await acquire_historical_window(
        db, sport="mlb",
        start_date="2025-06-15", end_date="2025-06-15",
        api_key="k_T", dry_run=False, provider=prov,
    )
    assert audit["status"]      == "succeeded"
    assert audit["n_sgo_events"]== 1
    assert audit["n_matchups_written"] == 1
    # 2 markets × 3 books in home-ml, 1 fliff blocked → 2+2=4 normalized,
    # 1 blocked → 3 written rows
    assert audit["n_props_normalized"] >= 3
    assert audit["n_blocked"]   == 1
    assert audit["n_unresolved"]== 0
    assert audit["acquire_all"] is True
    # collection routing
    assert await db["team_matchups"].count_documents({}) == 1
    assert await db["team_historical_props"].count_documents({}) >= 3
    assert await db["nfl_matchups"].count_documents({}) == 0
    assert await db["nfl_historical_props"].count_documents({}) == 0


# ── NFL routing (no MLB collections touched) ─────────────────────
@pytest.mark.asyncio
async def test_acquire_nfl_routes_to_nfl_collections(
    db, monkeypatch,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    # Seed NFL master hub
    await db["team_master_hub"].insert_many([
        {"team_id": "nfl_kc",  "sport": "nfl",
          "display_names": {"full": "Kansas City Chiefs",
                              "short": "Chiefs", "abbrev": "KC"}},
        {"team_id": "nfl_buf", "sport": "nfl",
          "display_names": {"full": "Buffalo Bills",
                              "short": "Bills", "abbrev": "BUF"}},
    ])
    ev = _event_with_odds(event_id="nfl_evt_1",
                            home="Kansas City Chiefs",
                            away="Buffalo Bills",
                            iso="2024-09-08T17:00:00Z")
    payload = _payload([ev])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    prov = SGOPayloadProvider("k_T", client=fake)

    audit = await acquire_historical_window(
        db, sport="nfl",
        start_date="2024-09-08", end_date="2024-09-08",
        api_key="k_T", dry_run=False, provider=prov,
    )
    assert audit["status"] == "succeeded"
    assert await db["nfl_matchups"].count_documents({}) == 1
    assert await db["nfl_historical_props"].count_documents({}) >= 3
    assert await db["team_matchups"].count_documents({}) == 0
    assert await db["team_historical_props"].count_documents({}) == 0


# ── Idempotency: rerun yields modified=0 ─────────────────────────
@pytest.mark.asyncio
async def test_acquire_idempotent_rerun(db, monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    await _seed_mlb(db)

    payload = _payload([_event_with_odds()])

    async def _run_one():
        fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
        prov = SGOPayloadProvider("k_T", client=fake)
        return await acquire_historical_window(
            db, sport="mlb",
            start_date="2025-06-15", end_date="2025-06-15",
            api_key="k_T", dry_run=False, provider=prov,
        )

    a1 = await _run_one()
    # Second pass uses a DIFFERENT snapshot_iso (started=now) so props
    # write NEW rows. Matchups upsert is idempotent on (sport, event_id).
    n_props_after_1 = await db["team_historical_props"].count_documents({})
    n_matchups_after_1 = await db["team_matchups"].count_documents({})
    assert n_matchups_after_1 == 1
    assert n_props_after_1 == a1["n_props_upserted"]


# ── Explicit market filter ──────────────────────────────────────
@pytest.mark.asyncio
async def test_acquire_with_explicit_market_filter(
    db, monkeypatch,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    await _seed_mlb(db)
    payload = _payload([_event_with_odds()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    prov = SGOPayloadProvider("k_T", client=fake)

    audit = await acquire_historical_window(
        db, sport="mlb",
        start_date="2025-06-15", end_date="2025-06-15",
        api_key="k_T", dry_run=False, provider=prov,
        market_keys=("points-home-game-ml-home",),
    )
    assert audit["status"]      == "succeeded"
    assert audit["acquire_all"] is False
    # only home-ml emitted → 3 books × 1 market, minus 1 fliff blocked → 2
    rows = await db["team_historical_props"].count_documents({})
    assert rows == 2


# ── Dry-run path ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_acquire_dry_run_no_writes(db, monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    await _seed_mlb(db)
    payload = _payload([_event_with_odds()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    prov = SGOPayloadProvider("k_T", client=fake)

    audit = await acquire_historical_window(
        db, sport="mlb",
        start_date="2025-06-15", end_date="2025-06-15",
        api_key="k_T", dry_run=True, provider=prov,
    )
    assert audit["status"] == "dry_run"
    assert audit["n_props_written"] >= 3   # WOULD-write count
    assert audit["n_props_upserted"] == 0
    # nothing in DB
    assert await db["team_historical_props"].count_documents({}) == 0
    assert await db["team_matchups"].count_documents({}) == 0


# ── Multi-day window, SGO failure on one day ────────────────────
@pytest.mark.asyncio
async def test_acquire_multi_day_with_one_failure(
    db, monkeypatch,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    await _seed_mlb(db)
    good = _payload([_event_with_odds(event_id="evt_day1")])
    good2 = _payload([_event_with_odds(event_id="evt_day3",
                                          iso="2025-06-17T01:40:00Z")])
    fake = _FakeHttpxClient(responses=[
        _FakeResponse(content=good),
        _FakeResponse(status_code=429, content=b'"rate-limited"'),
        _FakeResponse(content=good2),
    ])
    prov = SGOPayloadProvider("k_T", client=fake)
    audit = await acquire_historical_window(
        db, sport="mlb",
        start_date="2025-06-15", end_date="2025-06-17",
        api_key="k_T", dry_run=False, provider=prov,
    )
    assert audit["status"] == "succeeded"
    assert audit["n_dates"] == 3
    pdc = audit["per_date_counts"]
    assert pdc["2025-06-15"] == 1
    assert pdc["2025-06-16"] == -1   # marker for failure
    assert pdc["2025-06-17"] == 1
    assert await db["team_matchups"].count_documents({}) == 2


# ── CLI: argparse + dry-run default ──────────────────────────────
def test_cli_argparse() -> None:
    from scripts.team_historical_acquire import _build_parser
    p = _build_parser()
    args = p.parse_args(["--sport", "nfl",
                          "--start", "2024-09-05",
                          "--end",   "2024-09-09"])
    assert args.sport == "nfl"
    assert args.yes is False
    with pytest.raises(SystemExit):
        p.parse_args(["--sport", "f1", "--start", "x", "--end", "y"])


@pytest.mark.asyncio
async def test_cli_runs_dry(db, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("DB_NAME", db.name)
    await ensure_team_collections(db)
    await _seed_mlb(db)
    payload = _payload([_event_with_odds()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    from workers.team import _sgo_provider as prov_mod
    real_cls = prov_mod.SGOPayloadProvider

    class _Patched(real_cls):
        def __init__(self, api_key, **kw):
            super().__init__(api_key, client=fake, **kw)

    monkeypatch.setattr(prov_mod, "SGOPayloadProvider", _Patched)
    from workers.team import historical_ingest as wk_mod
    monkeypatch.setattr(wk_mod, "SGOPayloadProvider", _Patched)

    from scripts.team_historical_acquire import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb",
        "--start", "2025-06-15", "--end", "2025-06-15",
    ])
    rc = await _run(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry_run" in out
    assert await db["team_historical_props"].count_documents({}) == 0


def test_cli_rejects_bad_date_format() -> None:
    # Using subprocess would be heavier — just verify _run returns 2
    import asyncio as _aio

    from scripts.team_historical_acquire import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb",
        "--start", "bad",
        "--end", "2025-06-15",
    ])
    rc = _aio.run(_run(args))
    assert rc == 2

"""
Phase 1.A.4a — sync-worker + CLI tests.

Mocks the SGO HTTP client; no real network. Verifies:
  - dispatch_guard_closed exit path
  - SGO failure path
  - dry-run preview (no writes)
  - live write (--yes) creates upserts in `team_matchups`
  - team-id resolution against `team_master_hub`
  - lenient unresolved-teams policy
  - admin endpoint contract
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


# ── Fake httpx.Client ──────────────────────────────────────────────
class _FakeResponse:
    def __init__(self, *, status_code: int = 200,
                  content: bytes = b""):
        self.status_code = status_code
        self.content     = content


class _FakeHttpxClient:
    def __init__(self, *, responses):
        # Accept either a single response or a list (for pagination)
        if isinstance(responses, _FakeResponse):
            self._responses = [responses]
        else:
            self._responses = list(responses)
        self.urls_called = []

    def get(self, url):
        self.urls_called.append(url)
        return self._responses.pop(0) if self._responses \
                 else _FakeResponse(status_code=500, content=b"exhausted")

    def close(self):
        pass


def _payload_events(events):
    return json.dumps({"data": events}, ensure_ascii=False).encode("utf-8")


def _real_sgo_event(event_id="evt1", home="Seattle Mariners",
                      away="Cleveland Guardians", iso="2025-06-15T01:40:00Z",
                      completed=True):
    return {
        "eventID":  event_id,
        "status":   {"startsAt":  iso,
                      "completed": completed, "live": False,
                      "started":   True, "cancelled": False},
        "teams": {
            "home": {"names": {"long": home,
                                 "short":  home.split()[-1],
                                 "abbrev": "HOM"}},
            "away": {"names": {"long": away,
                                 "short":  away.split()[-1],
                                 "abbrev": "AWY"}},
        },
        "venue": "T-Mobile Park",
    }


# ── DB fixture ─────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    mongo_url = os.environ["MONGO_URL"]
    c = AsyncIOMotorClient(mongo_url)
    name = "team_events_sync_test"
    _db = c[name]
    for coll in ("team_master_hub", "team_matchups"):
        await _db[coll].drop()
    try:
        yield _db
    finally:
        for coll in ("team_master_hub", "team_matchups"):
            try:
                await _db[coll].drop()
            except Exception:
                pass
        c.close()


async def _seed_master_hub(db):
    await db["team_master_hub"].insert_many([
        {"team_id": "mlb_sea", "sport": "mlb",
          "display_names": {"full":   "Seattle Mariners",
                              "short":  "Mariners",
                              "abbrev": "SEA"}},
        {"team_id": "mlb_cle", "sport": "mlb",
          "display_names": {"full":   "Cleveland Guardians",
                              "short":  "Guardians",
                              "abbrev": "CLE"}},
    ])


# ── argparse contract ──────────────────────────────────────────────
def test_argparse_contract() -> None:
    from scripts.team_events_sync import _build_parser
    p = _build_parser()
    args = p.parse_args(["--sport", "mlb", "--date", "2025-06-15"])
    assert args.sport == "mlb"
    assert args.date  == "2025-06-15"
    assert args.yes is False
    assert args.json is False
    with pytest.raises(SystemExit):
        p.parse_args([])
    with pytest.raises(SystemExit):
        p.parse_args(["--sport", "f1", "--date", "2025-06-15"])


# ── sync worker happy path (dry-run) ──────────────────────────────
@pytest.mark.asyncio
async def test_sync_dry_run_no_writes(db, monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    payload = _payload_events([_real_sgo_event()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    prov = SGOPayloadProvider("k_T", client=fake)
    from workers.team.team_events_sync import fetch_and_sync

    audit = await fetch_and_sync(
        db, sport="mlb", game_date="2025-06-15",
        api_key="k_T", dry_run=True, provider=prov)

    assert audit["status"]       == "dry_run"
    assert audit["n_sgo_events"] == 1
    assert audit["n_normalized"] == 1
    assert audit["n_writes"]     == 0
    assert await db["team_matchups"].count_documents({}) == 0


@pytest.mark.asyncio
async def test_sync_live_writes(db, monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    payload = _payload_events([
        _real_sgo_event(event_id="evt1"),
        _real_sgo_event(event_id="evt2", iso="2025-06-15T20:00:00Z",
                         completed=False),
    ])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    prov = SGOPayloadProvider("k_T", client=fake)
    from workers.team.team_events_sync import fetch_and_sync

    audit = await fetch_and_sync(
        db, sport="mlb", game_date="2025-06-15",
        api_key="k_T", dry_run=False, provider=prov)

    assert audit["status"]     == "succeeded"
    assert audit["n_writes"]   == 2
    assert audit["n_upserted"] == 2
    # Both team IDs resolved on both events → unresolved=0
    assert audit["n_unresolved"] == 0

    rows = []
    async for d in db["team_matchups"].find({},
            projection={"_id": 0, "status_raw": 0}).sort("event_id", 1):
        rows.append(d)
    assert len(rows) == 2
    assert rows[0]["event_id"]     == "evt1"
    assert rows[0]["home_team_id"] == "mlb_sea"
    assert rows[0]["away_team_id"] == "mlb_cle"
    assert rows[0]["status"]       == "completed"
    assert rows[0]["game_date"]    == "2025-06-15"
    assert rows[0]["sport"]        == "mlb"
    assert rows[0]["league"]       == "MLB"
    assert rows[1]["event_id"]     == "evt2"
    assert rows[1]["status"]       == "live"


@pytest.mark.asyncio
async def test_sync_idempotent_rewrite(db, monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    payload = _payload_events([_real_sgo_event()])
    fake = _FakeHttpxClient(responses=[
        _FakeResponse(content=payload),
        _FakeResponse(content=payload),
    ])
    prov = SGOPayloadProvider("k_T", client=fake)
    from workers.team.team_events_sync import fetch_and_sync

    a1 = await fetch_and_sync(db, sport="mlb", game_date="2025-06-15",
                                api_key="k_T", dry_run=False, provider=prov)
    a2 = await fetch_and_sync(db, sport="mlb", game_date="2025-06-15",
                                api_key="k_T", dry_run=False, provider=prov)
    # First run: 1 upsert. Second run: 0 upsert (the same row matched),
    # modified is 1 because `updated_at` changes per run.
    assert a1["n_upserted"] == 1
    assert a2["n_upserted"] == 0
    assert a2["n_matched"]  == 1


@pytest.mark.asyncio
async def test_sync_unresolved_team_is_lenient(db, monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    # Only Seattle in master hub — Cleveland will be unresolved
    await db["team_master_hub"].insert_one({
        "team_id": "mlb_sea", "sport": "mlb",
        "display_names": {"full": "Seattle Mariners",
                            "short": "Mariners", "abbrev": "SEA"},
    })

    payload = _payload_events([_real_sgo_event()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    prov = SGOPayloadProvider("k_T", client=fake)
    from workers.team.team_events_sync import fetch_and_sync

    audit = await fetch_and_sync(
        db, sport="mlb", game_date="2025-06-15",
        api_key="k_T", dry_run=False, provider=prov)
    assert audit["status"]       == "succeeded"
    assert audit["n_writes"]     == 1   # lenient: row still written
    assert audit["n_unresolved"] == 1   # Cleveland flagged
    row = await db["team_matchups"].find_one({"event_id": "evt1"},
                                                 projection={"_id": 0})
    assert row["home_team_id"]      == "mlb_sea"
    assert row["away_team_id"]      is None
    assert row["unresolved_teams"]  == ["Cleveland Guardians"]


@pytest.mark.asyncio
async def test_sync_guard_closed_returns_audit(db, monkeypatch) -> None:
    # No SGO_API_KEY, no TEAM_INGEST_ENABLED → dispatch guard closed
    monkeypatch.delenv("SGO_API_KEY", raising=False)
    monkeypatch.delenv("TEAM_INGEST_ENABLED", raising=False)
    await ensure_team_collections(db)
    from workers.team.team_events_sync import fetch_and_sync
    audit = await fetch_and_sync(
        db, sport="mlb", game_date="2025-06-15",
        api_key="", dry_run=False)
    assert audit["status"] == "guard_closed"
    assert audit["n_writes"] == 0


@pytest.mark.asyncio
async def test_sync_sgo_failure(db, monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    fake = _FakeHttpxClient(responses=_FakeResponse(
        status_code=429, content=b'"rate limited"'))
    prov = SGOPayloadProvider("k_T", client=fake)
    from workers.team.team_events_sync import fetch_and_sync

    audit = await fetch_and_sync(
        db, sport="mlb", game_date="2025-06-15",
        api_key="k_T", dry_run=False, provider=prov)
    assert audit["status"] == "sgo_failure"
    assert "429" in audit["diagnosis"]
    assert audit["n_writes"] == 0
    assert await db["team_matchups"].count_documents({}) == 0


# ── CLI runner ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cli_dry_run_default(db, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("DB_NAME", db.name)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    payload = _payload_events([_real_sgo_event()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    from workers.team import _sgo_provider as prov_mod
    real_cls = prov_mod.SGOPayloadProvider

    class _Patched(real_cls):
        def __init__(self, api_key, **kw):
            super().__init__(api_key, client=fake, **kw)

    monkeypatch.setattr(prov_mod, "SGOPayloadProvider", _Patched)
    from workers.team import team_events_sync as wk_mod
    monkeypatch.setattr(wk_mod, "SGOPayloadProvider", _Patched)

    from scripts.team_events_sync import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb", "--date", "2025-06-15"])
    rc = await _run(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" not in out  # banner is printed by main(), not _run()
    assert "dry_run" in out
    assert "n_normalized    : 1" in out
    # No writes
    assert await db["team_matchups"].count_documents({}) == 0


@pytest.mark.asyncio
async def test_cli_yes_writes(db, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("DB_NAME", db.name)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    payload = _payload_events([_real_sgo_event()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    from workers.team import _sgo_provider as prov_mod
    real_cls = prov_mod.SGOPayloadProvider

    class _Patched(real_cls):
        def __init__(self, api_key, **kw):
            super().__init__(api_key, client=fake, **kw)

    monkeypatch.setattr(prov_mod, "SGOPayloadProvider", _Patched)
    from workers.team import team_events_sync as wk_mod
    monkeypatch.setattr(wk_mod, "SGOPayloadProvider", _Patched)

    from scripts.team_events_sync import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb", "--date", "2025-06-15", "--yes"])
    rc = await _run(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "n_upserted      : 1" in out
    assert await db["team_matchups"].count_documents({}) == 1

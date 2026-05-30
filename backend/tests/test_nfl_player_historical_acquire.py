"""
Phase 4 — NFL player-prop normalizer + worker tests.

Mocks SGO HTTP; no real network. Verifies:
  - team entities filtered out
  - player-prop rows emitted with correct shape
  - book policy filters
  - stat_families bookkeeping
  - dry-run vs live writes
  - guard-closed path still writes audit
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub.collections import (  # noqa: E402
    ensure_team_collections,
)
from workers.team._normalize_player import (  # noqa: E402
    TEAM_ENTITIES,
    normalize_player_payload,
)
from workers.team._sgo_provider import (  # noqa: E402
    SGOPayloadProvider,
)


# ── Fake HTTP ──
class _FakeResponse:
    def __init__(self, *, status_code: int = 200,
                  content: bytes = b""):
        self.status_code = status_code
        self.content     = content


class _FakeHttpxClient:
    def __init__(self, *, responses):
        self._responses = list(responses) if isinstance(
            responses, list) else [responses]

    def get(self, url):
        return self._responses.pop(0) if self._responses \
                 else _FakeResponse(status_code=500, content=b"")

    def close(self):
        pass


def _payload(events):
    return json.dumps({"data": events},
                       ensure_ascii=False).encode("utf-8")


def _event_with_mixed_markets():
    return {
        "eventID": "evt1",
        "status":  {"startsAt": "2024-09-08T17:00:00Z",
                     "completed": True, "live": False,
                     "started": True},
        "teams": {
            "home": {"names": {"long": "Kansas City Chiefs"}},
            "away": {"names": {"long": "Buffalo Bills"}},
        },
        "odds": {
            # team-level (should be SKIPPED)
            "points-home-game-ml-home": {
                "marketName": "Moneyline",
                "statID":  "points",
                "statEntityID": "home",
                "periodID": "game",
                "betTypeID": "ml",
                "sideID":   "home",
                "byBookmaker": {
                    "draftkings": {"odds": -120},
                    "fanduel":    {"odds": -125},
                },
            },
            # player-level (over)
            "receiving_receptions-PATRICK_MAHOMES_1_NFL-game-ou-over": {
                "marketName": "Mahomes Receptions O/U",
                "statID":  "receiving_receptions",
                "statEntityID": "PATRICK_MAHOMES_1_NFL",
                "playerID":     "PATRICK_MAHOMES_1_NFL",
                "periodID": "game",
                "betTypeID": "ou",
                "sideID":   "over",
                "byBookmaker": {
                    "draftkings": {"odds": -110, "overUnder": 0.5,
                                     "isMainLine": True},
                    "fliff":      {"odds": -110, "overUnder": 0.5},  # blocked
                },
            },
            # player-level (under, alternate line)
            "receiving_receptions-PATRICK_MAHOMES_1_NFL-game-ou-under": {
                "marketName": "Mahomes Receptions O/U",
                "statID":  "receiving_receptions",
                "statEntityID": "PATRICK_MAHOMES_1_NFL",
                "playerID":     "PATRICK_MAHOMES_1_NFL",
                "periodID": "game",
                "betTypeID": "ou",
                "sideID":   "under",
                "byBookmaker": {
                    "draftkings": {"odds": -130, "overUnder": 1.5,
                                     "isAlternate": True},
                },
            },
            # player-level YN
            "firstTouchdown-JOSH_ALLEN_1_NFL-game-yn-yes": {
                "marketName": "Allen First TD",
                "statID":  "firstTouchdown",
                "statEntityID": "JOSH_ALLEN_1_NFL",
                "playerID":     "JOSH_ALLEN_1_NFL",
                "periodID": "game",
                "betTypeID": "yn",
                "sideID":   "yes",
                "byBookmaker": {
                    "draftkings": {"odds": 800},
                },
            },
        },
    }


# ── DB fixture ──
@pytest_asyncio.fixture
async def db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    _db = c["nfl_player_test"]
    for coll in ("nfl_player_historical_props",
                  "historical_acquire_runs"):
        await _db[coll].drop()
    try:
        yield _db
    finally:
        for coll in ("nfl_player_historical_props",
                      "historical_acquire_runs"):
            try:
                await _db[coll].drop()
            except Exception:
                pass
        c.close()


# ── Pure normalize tests ──
class TestPlayerNormalize:
    def _normalize(self, payload):
        return normalize_player_payload(
            payload, sport="nfl", league="NFL",
            snapshot_iso="2026-02-18T00:00:00Z",
            ingested_at=datetime(2026, 2, 18, tzinfo=timezone.utc),
        )

    def test_filters_out_team_entities(self) -> None:
        rows, c = self._normalize({"events": [_event_with_mixed_markets()]})
        # NONE of the team-level outcomes should appear
        for r in rows:
            assert r["player_id"] not in ("home", "away", "all", "game")
            assert r["player_id"]
        # 4 player rows: ou/over (2 books, 1 blocked), ou/under (1), yn (1)
        # team-ml-home market drops 2 outcomes into dropped_team_entity
        assert c["dropped_team_entity"] == 2
        assert c["rows_emitted"] == 4  # before book policy

    def test_player_row_shape(self) -> None:
        rows, _ = self._normalize({"events": [_event_with_mixed_markets()]})
        mahomes = [r for r in rows
                    if r["player_id"] == "PATRICK_MAHOMES_1_NFL"
                    and r["side"] == "OVER" and r["book"] == "draftkings"][0]
        assert mahomes["event_id"]     == "evt1"
        assert mahomes["sport"]        == "nfl"
        assert mahomes["league"]       == "NFL"
        assert mahomes["market"]       == \
            "receiving_receptions-PATRICK_MAHOMES_1_NFL-game-ou-over"
        assert mahomes["market_name"]  == "Mahomes Receptions O/U"
        assert mahomes["statID"]       == "receiving_receptions"
        assert mahomes["statEntityID"] == "PATRICK_MAHOMES_1_NFL"
        assert mahomes["periodID"]     == "game"
        assert mahomes["betTypeID"]    == "ou"
        assert mahomes["sideID"]       == "over"
        assert mahomes["side"]         == "OVER"
        assert mahomes["line"]         == 0.5
        assert mahomes["odds"]         == -110
        assert mahomes["is_alternate"] is False
        assert mahomes["game_date"]    == "2024-09-08"
        assert mahomes["commence_time"]== "2024-09-08T17:00:00Z"

    def test_alternate_line_flagged(self) -> None:
        rows, _ = self._normalize({"events": [_event_with_mixed_markets()]})
        alt = [r for r in rows
                if r["side"] == "UNDER" and r["book"] == "draftkings"][0]
        assert alt["is_alternate"] is True
        assert alt["line"] == 1.5

    def test_yn_market_no_line(self) -> None:
        rows, _ = self._normalize({"events": [_event_with_mixed_markets()]})
        yn = [r for r in rows
                if r["player_id"] == "JOSH_ALLEN_1_NFL"][0]
        assert yn["side"] == "YES"
        assert yn["line"] is None
        assert yn["odds"] == 800

    def test_stat_families_counted(self) -> None:
        _, c = self._normalize({"events": [_event_with_mixed_markets()]})
        # 2 player-prop market_keys for receiving_receptions, 1 for firstTouchdown
        assert c["stat_families"]["receiving_receptions"] == 2
        assert c["stat_families"]["firstTouchdown"]       == 1

    def test_event_without_id_skipped(self) -> None:
        rows, _ = self._normalize({"events": [{
            "teams": {}, "odds": {
                "x-PLAYER_NAME_1_NFL-game-ou-over": {
                    "playerID": "PLAYER_NAME_1_NFL", "statEntityID": "PLAYER_NAME_1_NFL",
                    "betTypeID":"ou","sideID":"over",
                    "byBookmaker": {"draftkings": {"odds": -110, "overUnder": 1.5}},
                }}}]})
        assert rows == []

    def test_bad_side_dropped(self) -> None:
        rows, c = self._normalize({"events": [{
            "eventID":"e","status":{"startsAt":"2024-09-08T17:00:00Z"},
            "odds": {
                "x-PLAYER_NAME_1_NFL-game-ou-banana": {
                    "playerID": "PLAYER_NAME_1_NFL",
                    "statEntityID": "PLAYER_NAME_1_NFL",
                    "betTypeID":"ou","sideID":"banana",
                    "byBookmaker": {"draftkings": {"odds": -110}},
                },
            }}]})
        assert rows == []
        assert c["dropped_bad_side"] == 1


# ── Worker happy path ──
@pytest.mark.asyncio
async def test_acquire_player_dry_run(db, monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    payload = _payload([_event_with_mixed_markets()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    prov = SGOPayloadProvider("k_T", client=fake)
    from workers.team.historical_player_ingest import (
        acquire_player_historical_window,
    )
    audit = await acquire_player_historical_window(
        db, sport="nfl",
        start_date="2024-09-08", end_date="2024-09-08",
        api_key="k_T", dry_run=True, provider=prov,
    )
    assert audit["status"] == "dry_run"
    assert audit["n_sgo_events"] == 1
    assert audit["n_props_written"] >= 3
    assert await db["nfl_player_historical_props"].count_documents({}) == 0


@pytest.mark.asyncio
async def test_acquire_player_live_writes(db, monkeypatch) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_T")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    await ensure_team_collections(db)
    payload = _payload([_event_with_mixed_markets()])
    fake = _FakeHttpxClient(responses=_FakeResponse(content=payload))
    prov = SGOPayloadProvider("k_T", client=fake)
    from workers.team.historical_player_ingest import (
        acquire_player_historical_window,
    )
    audit = await acquire_player_historical_window(
        db, sport="nfl",
        start_date="2024-09-08", end_date="2024-09-08",
        api_key="k_T", dry_run=False, provider=prov,
    )
    # 4 normalized − 1 fliff blocked = 3 written
    assert audit["status"] == "succeeded"
    assert audit["n_blocked"] == 1
    assert audit["n_props_upserted"] == 3
    assert await db["nfl_player_historical_props"].count_documents({}) == 3
    sample = await db["nfl_player_historical_props"].find_one(
        {"player_id": "JOSH_ALLEN_1_NFL"}, projection={"_id": 0})
    assert sample["side"] == "YES"
    assert sample["odds"] == 800
    assert sample["sport"] == "nfl"


@pytest.mark.asyncio
async def test_acquire_player_guard_closed(db, monkeypatch) -> None:
    monkeypatch.delenv("SGO_API_KEY", raising=False)
    monkeypatch.delenv("TEAM_INGEST_ENABLED", raising=False)
    await ensure_team_collections(db)
    from workers.team.historical_player_ingest import (
        acquire_player_historical_window,
    )
    audit = await acquire_player_historical_window(
        db, sport="nfl",
        start_date="2024-09-08", end_date="2024-09-08",
        api_key="", dry_run=False,
    )
    assert audit["status"] == "guard_closed"
    n_audit = await db["historical_acquire_runs"].count_documents({})
    assert n_audit == 1


@pytest.mark.asyncio
async def test_acquire_player_rejects_non_nfl(db) -> None:
    from workers.team.historical_player_ingest import (
        acquire_player_historical_window,
    )
    with pytest.raises(ValueError):
        await acquire_player_historical_window(
            db, sport="mlb",
            start_date="2024-09-08", end_date="2024-09-08",
            api_key="x", dry_run=True,
        )


def test_cli_argparse() -> None:
    from scripts.nfl_player_historical_acquire import _build_parser
    p = _build_parser()
    args = p.parse_args(["--start", "2024-02-10", "--end", "2026-02-09"])
    assert args.start == "2024-02-10"
    assert args.end   == "2026-02-09"
    assert args.yes is False

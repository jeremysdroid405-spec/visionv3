"""
Phase 1.A.3.4a — `team_odds_dry_run_fetch` CLI tests.

In-process tests inject a fake httpx.Client into the SGO provider so
no network calls happen. Subprocess test confirms the guard-closed
exit code path end-to-end.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub.collections import (  # noqa: E402
    ensure_team_collections,
)


# ── Fake httpx.Client (mirrors the recorder tests) ──────────────────
class _FakeResponse:
    def __init__(self, *, status_code: int = 200,
                  content: bytes = b""):
        self.status_code = status_code
        self.content     = content


class _FakeHttpxClient:
    def __init__(self, *, response: _FakeResponse) -> None:
        self._response = response
        self.last_url: str | None = None

    def get(self, url: str) -> _FakeResponse:
        self.last_url = url
        return self._response

    def close(self) -> None:
        pass


def _payload_bytes() -> bytes:
    payload = {
        "events": [{
            "event_id":      "evt_dryrun_001",
            "commence_time": "2026-06-02T22:00:00Z",
            "bookmakers": [
                {"key": "draftkings",
                  "markets": [{
                     "key":  "team_total_runs",
                     "team": "New York Yankees",
                     "outcomes": [
                         {"name": "Over",  "point": 4.5, "price": -110},
                         {"name": "Under", "point": 4.5, "price": -110},
                     ],
                  }]},
                {"key": "fliff",   # BLOCKED — should be dropped
                  "markets": [{
                     "key":  "team_total_runs",
                     "team": "New York Yankees",
                     "outcomes": [
                         {"name": "Over", "point": 4.5, "price": -110},
                     ],
                  }]},
            ],
        }],
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


@pytest_asyncio.fixture
async def db():
    mongo_url = os.environ["MONGO_URL"]
    client = AsyncIOMotorClient(mongo_url)
    name = "team_odds_dry_run_fetch_shared"
    _db = client[name]
    for c in ("team_master_hub", "team_live_props",
              "team_odds_ingest_runs"):
        await _db[c].drop()
    try:
        yield _db
    finally:
        for c in ("team_master_hub", "team_live_props",
                  "team_odds_ingest_runs"):
            try:
                await _db[c].drop()
            except Exception:
                pass
        client.close()


async def _seed_master_hub(db) -> None:
    await db["team_master_hub"].insert_one({
        "team_id": "mlb_nyy", "sport": "mlb",
        "display_names": {"full":   "New York Yankees",
                            "short":  "Yankees",
                            "abbrev": "NYY",
                            "market": "New York"},
    })


# ── argparse contract ───────────────────────────────────────────────
def test_argparse_contract() -> None:
    from scripts.team_odds_dry_run_fetch import _build_parser
    parser = _build_parser()
    args = parser.parse_args([
        "--sport", "mlb", "--event-id", "evt_xyz",
    ])
    assert args.sport      == "mlb"
    assert args.event_id   == "evt_xyz"
    assert args.snapshot_iso is None
    assert args.json       is False
    # Required flags
    with pytest.raises(SystemExit):
        parser.parse_args([])
    # Sport choice gate
    with pytest.raises(SystemExit):
        parser.parse_args(["--sport", "formula1",
                            "--event-id", "x"])


def test_would_write_count_helper() -> None:
    from scripts.team_odds_dry_run_fetch import _would_write_count
    # normalized=10, blocked=2, unresolved=1 → 7
    assert _would_write_count({
        "n_rows_normalized": 10, "n_blocked": 2, "n_unresolved": 1,
        "explosion_abort": False,
    }) == 7
    # market explosion → 0
    assert _would_write_count({
        "n_rows_normalized": 10, "n_blocked": 0, "n_unresolved": 0,
        "explosion_abort": True,
    }) == 0
    # never negative
    assert _would_write_count({
        "n_rows_normalized": 1, "n_blocked": 5, "n_unresolved": 0,
        "explosion_abort": False,
    }) == 0


def test_print_summary_renders_every_section(capsys) -> None:
    from scripts.team_odds_dry_run_fetch import _print_summary
    audit = {
        "run_id": "r1", "status": "dry_run",
        "diagnosis": "dry_run mode — no rows written",
        "duration_ms": 42, "snapshot_iso": "t",
        "n_sgo_events": 1, "n_sgo_outcomes": 4,
        "n_rows_normalized": 4, "n_blocked": 1, "n_refs": 0,
        "n_unresolved": 0,
        "observed_markets": 1, "expected_markets": 4,
        "explosion_abort": False,
        "n_writes": 0,
        "per_market_counts": {"team_total_runs": 3},
    }
    _print_summary(audit)
    out = capsys.readouterr().out
    for marker in ("audit row", "HTTP / normalize", "book policy",
                    "master hub resolution",
                    "market explosion guard",
                    "per-market normalized counts",
                    "projected impact",
                    "WOULD have written : 3",
                    "actually wrote     : 0"):
        assert marker in out, f"missing section: {marker!r}"


# ── In-process happy path (mocked HTTP) ─────────────────────────────
@pytest.mark.asyncio
async def test_dry_run_fetch_happy_path(
    db, monkeypatch, capsys,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_DRYRUN_FETCH_TEST_KEY")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    # Even if operator has TEAM_INGEST_LIVE=1, the CLI hard-codes
    # mode="dry_run" — verify by setting it.
    monkeypatch.setenv("TEAM_INGEST_LIVE", "1")
    # Force the DB connection to use our throw-away test DB
    monkeypatch.setenv("DB_NAME", db.name)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    # Patch the SGOPayloadProvider in the worker module to use the fake
    from workers.team import _sgo_provider as prov_mod
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_payload_bytes()))
    real_cls = prov_mod.SGOPayloadProvider

    class _Patched(real_cls):
        def __init__(self, api_key, **kw):
            super().__init__(api_key, client=fake, **kw)

    monkeypatch.setattr(prov_mod, "SGOPayloadProvider",
                          _Patched, raising=True)
    from workers.team import team_odds_ingest as wk_mod
    monkeypatch.setattr(wk_mod, "SGOPayloadProvider",
                          _Patched, raising=True)

    # Invoke _run() directly (avoids nested asyncio.run from main)
    from scripts.team_odds_dry_run_fetch import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb", "--event-id", "evt_dryrun_001",
    ])
    rc = await _run(args)
    out = capsys.readouterr().out

    assert rc == 0
    # Sections
    assert "mode='dry_run'" in out
    # Real counters from the mocked payload:
    #   3 normalized outcomes (2 DK + 1 Fliff), 1 blocked (Fliff)
    #   → would write 2 in live, actually wrote 0 in dry-run
    assert "rows_normalized : 3" in out
    assert "n_blocked       : 1" in out
    assert "actually wrote     : 0 rows" in out
    assert "WOULD have written : 2 rows" in out

    # DB invariants — 0 team_live_props, 1 audit row
    assert await db["team_live_props"].count_documents({}) == 0
    assert await db["team_odds_ingest_runs"].count_documents({}) == 1


@pytest.mark.asyncio
async def test_dry_run_fetch_json_mode(db, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_DRYRUN_JSON_TEST")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("DB_NAME", db.name)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_payload_bytes()))
    from workers.team import _sgo_provider as prov_mod
    from workers.team import team_odds_ingest as wk_mod
    real_cls = prov_mod.SGOPayloadProvider

    class _Patched(real_cls):
        def __init__(self, api_key, **kw):
            super().__init__(api_key, client=fake, **kw)

    monkeypatch.setattr(prov_mod, "SGOPayloadProvider", _Patched)
    monkeypatch.setattr(wk_mod,   "SGOPayloadProvider", _Patched)

    from scripts.team_odds_dry_run_fetch import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb", "--event-id", "evt_dryrun_001",
        "--json",
    ])
    rc = await _run(args)
    out = capsys.readouterr().out
    assert rc == 0
    # The JSON block must be parseable; the banner above it is OK
    json_start = out.find("{")
    parsed = json.loads(out[json_start:])
    assert parsed["audit"]["status"] == "dry_run"
    assert parsed["audit"]["n_writes"] == 0
    # No diff_planned key unless --diff-planned passed
    assert "diff_planned" not in parsed


@pytest.mark.asyncio
async def test_dry_run_fetch_sgo_failure_returns_exit_5(
    db, monkeypatch, capsys,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_SGO_FAIL_TEST")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("DB_NAME", db.name)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    fake = _FakeHttpxClient(response=_FakeResponse(
        status_code=429, content=b'"rate limited"'))
    from workers.team import _sgo_provider as prov_mod
    from workers.team import team_odds_ingest as wk_mod
    real_cls = prov_mod.SGOPayloadProvider

    class _Patched(real_cls):
        def __init__(self, api_key, **kw):
            super().__init__(api_key, client=fake, **kw)

    monkeypatch.setattr(prov_mod, "SGOPayloadProvider", _Patched)
    monkeypatch.setattr(wk_mod,   "SGOPayloadProvider", _Patched)

    from scripts.team_odds_dry_run_fetch import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb", "--event-id", "evt_x",
    ])
    rc = await _run(args)
    err = capsys.readouterr().err
    assert rc == 5
    assert "SGO fetch failed" in err
    # No audit row written on transport failure
    assert await db["team_odds_ingest_runs"].count_documents({}) == 0


# ── Subprocess: guard-closed exit 3 ─────────────────────────────────
def test_subprocess_guard_closed_exits_3(tmp_path) -> None:
    env = {k: v for k, v in os.environ.items()
            if k not in ("SGO_API_KEY", "TEAM_INGEST_ENABLED")}
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.team_odds_dry_run_fetch",
          "--sport", "mlb", "--event-id", "evt_xyz"],
        cwd="/app/backend", env=env,
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 3
    assert "dispatch guard closed" in proc.stderr.lower()
    # No state mutation possible since fetch never happened


# ── Mode is hard-locked to dry_run ──────────────────────────────────
def test_cli_never_passes_live_mode_to_worker(monkeypatch) -> None:
    """Even if the operator sets TEAM_INGEST_LIVE=1, the CLI must
    call `fetch_and_run_pass(mode='dry_run')`. We assert by patching
    the worker method and capturing the call args.
    """
    import asyncio
    captured: dict = {}

    async def _capture(self_, db, **kw):
        captured.update(kw)
        return {
            "run_id": "stub", "status": "dry_run",
            "diagnosis": "stub", "duration_ms": 0,
            "snapshot_iso": kw.get("snapshot_iso"),
            "n_sgo_events": 0, "n_sgo_outcomes": 0,
            "n_rows_normalized": 0, "n_blocked": 0, "n_refs": 0,
            "n_unresolved": 0,
            "observed_markets": 0, "expected_markets": 0,
            "explosion_abort": False, "n_writes": 0,
            "per_market_counts": {},
        }

    monkeypatch.setenv("SGO_API_KEY", "k_NO_LIVE_TEST")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("TEAM_INGEST_LIVE", "1")  # operator set this

    from workers.team import team_odds_ingest as wk_mod
    monkeypatch.setattr(
        wk_mod.TeamOddsIngestWorker,
        "fetch_and_run_pass",
        _capture,
    )

    from scripts.team_odds_dry_run_fetch import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb", "--event-id", "evt_lockcheck",
    ])
    rc = asyncio.run(_run(args))
    assert rc == 0
    assert captured.get("mode") == "dry_run", (
        f"CLI must hard-lock mode='dry_run' even when "
        f"TEAM_INGEST_LIVE=1 is set. Got mode={captured.get('mode')!r}"
    )


# ── --diff-planned tests ────────────────────────────────────────────
def test_compute_market_diff_clean_match() -> None:
    from scripts.team_odds_dry_run_fetch import compute_market_diff
    # MLB planned markets (6 prod targets):
    audit = {
        "per_market_counts": {
            "points-away-game-ml-away": 3,
            "points-home-game-ml-home": 3,
        },
        "explosion_abort": False,
    }
    d = compute_market_diff(audit, "mlb")
    assert d["n_unmapped"] == 0
    assert d["n_matched"]  == 2
    assert d["n_missing"]  == 4   # 6 planned - 2 observed


def test_compute_market_diff_unmapped_market_surfaces() -> None:
    from scripts.team_odds_dry_run_fetch import compute_market_diff
    audit = {
        "per_market_counts": {
            "points-away-game-ml-away":  3,    # planned
            "synth-unmapped-game-xx-aa": 5,    # NEW unplanned market
            "synth-unmapped-game-xx-bb": 7,    # NEW unplanned market
        },
        "explosion_abort": False,
    }
    d = compute_market_diff(audit, "mlb")
    assert d["n_unmapped"] == 2
    unmapped_names = [r["market"] for r in d["unmapped"]]
    assert "synth-unmapped-game-xx-aa" in unmapped_names
    assert "synth-unmapped-game-xx-bb" in unmapped_names
    counts = {r["market"]: r["count"] for r in d["unmapped"]}
    assert counts["synth-unmapped-game-xx-aa"] == 5
    assert counts["synth-unmapped-game-xx-bb"] == 7


def test_compute_market_diff_empty_observed() -> None:
    from scripts.team_odds_dry_run_fetch import compute_market_diff
    d = compute_market_diff({}, "nba")
    assert d["n_unmapped"] == 0
    assert d["n_matched"]  == 0
    # All planned NBA markets missing (6 prod targets)
    assert d["n_missing"]  == 6


def test_compute_market_diff_surfaces_explosion_flag() -> None:
    from scripts.team_odds_dry_run_fetch import compute_market_diff
    audit = {
        "per_market_counts": {"team_total_runs": 1},
        "explosion_abort":   True,
    }
    d = compute_market_diff(audit, "mlb")
    assert d["explosion_abort"] is True


def test_print_diff_planned_renders_all_sections(capsys) -> None:
    from scripts.team_odds_dry_run_fetch import _print_diff_planned
    diff = {
        "sport": "mlb",
        "planned":  ["a", "b"],
        "observed": ["a", "c"],
        "matched":  [{"market": "a", "count": 3}],
        "unmapped": [{"market": "c", "count": 7}],
        "missing":  ["b"],
        "n_matched":  1, "n_unmapped": 1, "n_missing": 1,
        "explosion_abort": False,
    }
    _print_diff_planned(diff)
    out = capsys.readouterr().out
    assert "--diff-planned" in out
    assert "UNMAPPED markets" in out
    assert "MISSING planned markets" in out
    assert "c" in out  # unmapped market name
    assert "7 outcomes" in out  # unmapped count
    assert "b" in out  # missing planned market


@pytest.mark.asyncio
async def test_dry_run_fetch_diff_planned_text_mode(
    db, monkeypatch, capsys,
) -> None:
    """End-to-end: fetch (mocked) → audit → diff-planned section
    appears in text output with the planned/observed counts.
    """
    monkeypatch.setenv("SGO_API_KEY", "k_DIFF_PLANNED_TEST")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("DB_NAME", db.name)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    # Payload includes ONE planned (team_total_runs) and ONE unmapped
    # (team_total_doubles) market.
    payload = {
        "events": [{
            "event_id":      "evt_diff_001",
            "commence_time": "2026-06-02T22:00:00Z",
            "bookmakers": [{
                "key": "draftkings",
                "markets": [
                    {"key":  "team_total_runs",
                      "team": "New York Yankees",
                      "outcomes": [
                          {"name": "Over",  "point": 4.5, "price": -110}
                      ]},
                    {"key":  "team_total_doubles",   # unmapped
                      "team": "New York Yankees",
                      "outcomes": [
                          {"name": "Over",  "point": 1.5, "price": -110}
                      ]},
                ],
            }],
        }],
    }
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=json.dumps(payload).encode("utf-8")))
    from workers.team import _sgo_provider as prov_mod
    from workers.team import team_odds_ingest as wk_mod
    real_cls = prov_mod.SGOPayloadProvider

    class _Patched(real_cls):
        def __init__(self, api_key, **kw):
            super().__init__(api_key, client=fake, **kw)

    monkeypatch.setattr(prov_mod, "SGOPayloadProvider", _Patched)
    monkeypatch.setattr(wk_mod,   "SGOPayloadProvider", _Patched)

    from scripts.team_odds_dry_run_fetch import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb", "--event-id", "evt_diff_001",
        "--diff-planned",
    ])
    rc = await _run(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "--diff-planned" in out
    assert "team_total_doubles" in out
    assert "UNMAPPED" in out


@pytest.mark.asyncio
async def test_dry_run_fetch_diff_planned_json_mode(
    db, monkeypatch, capsys,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_DIFF_JSON_TEST")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("DB_NAME", db.name)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_payload_bytes()))
    from workers.team import _sgo_provider as prov_mod
    from workers.team import team_odds_ingest as wk_mod
    real_cls = prov_mod.SGOPayloadProvider

    class _Patched(real_cls):
        def __init__(self, api_key, **kw):
            super().__init__(api_key, client=fake, **kw)

    monkeypatch.setattr(prov_mod, "SGOPayloadProvider", _Patched)
    monkeypatch.setattr(wk_mod,   "SGOPayloadProvider", _Patched)

    from scripts.team_odds_dry_run_fetch import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb", "--event-id", "evt_dryrun_001",
        "--json", "--diff-planned",
    ])
    rc = await _run(args)
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out[out.find("{"):])
    # Both keys present
    assert "audit"        in parsed
    assert "diff_planned" in parsed
    diff = parsed["diff_planned"]
    assert diff["sport"] == "mlb"
    # MLB has 4 planned markets; the synthetic payload observes one
    assert diff["n_matched"] == 1
    assert diff["n_missing"] == 3
    assert diff["n_unmapped"] == 0


def test_diff_planned_get_planned_markets_for_every_sport() -> None:
    from workers.team.team_odds_ingest import get_planned_markets
    for sport in ("mlb", "nba", "nfl"):
        planned = get_planned_markets(sport)
        assert isinstance(planned, list)
        assert len(planned) >= 3, (
            f"sport {sport!r} should have ≥3 planned markets")
    # Unknown sport → empty list, no crash
    assert get_planned_markets("formula1") == []
    assert get_planned_markets("") == []


# ── --raw mode tests ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_raw_mode_dumps_sanitized_payload_no_worker_pass(
    db, monkeypatch, capsys,
) -> None:
    """`--raw` must bypass the worker entirely (no audit row written,
    no team_live_props rows) and just dump the sanitized SGO payload.
    """
    monkeypatch.setenv("SGO_API_KEY", "k_RAW_INSPECTION_TEST")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.setenv("DB_NAME", db.name)
    await ensure_team_collections(db)

    # SGO v2 returns `{"data": [...]}` — the provider normalizes this
    # into `{"events": [...]}` for downstream consumers, and that's
    # the shape --raw should print.
    payload = {"data": [{
        "eventID":  "evt_raw_001",
        "startsAt": "2026-06-02T22:00:00Z",
        "props":    [{"marketName": "Team Total Runs",
                       "statID": "runs"}],
    }]}
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=json.dumps(payload).encode("utf-8")))
    from workers.team import _sgo_provider as prov_mod
    from scripts import team_odds_dry_run_fetch as cli_mod
    real_cls = prov_mod.SGOPayloadProvider

    class _Patched(real_cls):
        def __init__(self, api_key, **kw):
            super().__init__(api_key, client=fake, **kw)

    monkeypatch.setattr(prov_mod, "SGOPayloadProvider", _Patched)
    monkeypatch.setattr(cli_mod,  "SGOPayloadProvider", _Patched)

    from scripts.team_odds_dry_run_fetch import _build_parser, _run
    args = _build_parser().parse_args([
        "--sport", "mlb", "--event-id", "evt_raw_001", "--raw",
    ])
    rc = await _run(args)
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out[out.find("{"):])
    assert parsed["mode"] == "raw"
    # The normalized envelope under `events`
    assert "events" in parsed["payload"]
    assert parsed["payload"]["events"][0]["eventID"] == "evt_raw_001"
    # NO worker pass: zero rows, zero audit rows
    assert await db["team_live_props"].count_documents({}) == 0
    assert await db["team_odds_ingest_runs"].count_documents({}) == 0


def test_build_url_uses_camelcase_apikey_and_eventid() -> None:
    """Regression for the 401 root cause: SGO expects `apiKey` +
    `eventID` (camelCase), not snake_case. Pinning the URL contract.
    """
    p = SGOPayloadProvider("k_TEST_URL_PIN")
    real, sanitized = p._build_url(sport="mlb", event_id="evt_xyz")
    # Auth: apiKey camelCase, NOT api_key
    assert "apiKey=k_TEST_URL_PIN" in real
    assert "api_key=" not in real
    # Event ID: eventID camelCase, NOT event_id
    assert "eventID=evt_xyz" in real
    assert "event_id=" not in real
    # Sport / league filters
    assert "sportID=BASEBALL" in real
    assert "leagueID=MLB"     in real
    # expandResults included
    assert "expandResults=true" in real
    # Sanitized URL has the key replaced
    assert "k_TEST_URL_PIN" not in sanitized
    assert REDACTION_TOKEN in sanitized


def test_provider_accepts_data_envelope() -> None:
    """SGO v2 wraps events under `data`. The provider must accept
    this and normalize to `events`.
    """
    body = json.dumps({"data": [{"eventID": "x",
                                    "bookmakers": [{
                                        "key": "draftkings",
                                        "markets": [{
                                            "key": "team_total_runs",
                                            "team": "NYY",
                                            "outcomes": [
                                                {"name": "Over",
                                                 "point": 4.5,
                                                 "price": -110}
                                            ]}]}]}]}).encode("utf-8")
    fake = _FakeHttpxClient(response=_FakeResponse(content=body))
    p = SGOPayloadProvider("k_DATA_ENVELOPE_TEST", client=fake)
    result = p.fetch_event_odds(sport="mlb", event_id="x")
    # Normalized to `events` key
    assert "events" in result["payload"]
    assert len(result["payload"]["events"]) == 1


def test_provider_rejects_dict_without_events_or_data() -> None:
    body = b'{"meta": "no data"}'
    fake = _FakeHttpxClient(response=_FakeResponse(content=body))
    p = SGOPayloadProvider("k_NO_EVENTS_TEST", client=fake)
    with pytest.raises(SGOFetchError) as exc:
        p.fetch_event_odds(sport="mlb", event_id="x")
    assert exc.value.kind == "empty_payload"


# Need the SGOPayloadProvider + REDACTION_TOKEN symbols in this module
from services.team_master_hub.fixtures import (  # noqa: E402
    REDACTION_TOKEN,
)
from workers.team._sgo_provider import (  # noqa: E402
    SGOFetchError,
    SGOPayloadProvider,
)

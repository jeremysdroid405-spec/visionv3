"""
Phase 1.A.3.3 — Bearer sanitizer pattern + SGO provider + recorder
body + dry-run HTTP fetcher integration tests.

All HTTP is mocked via an injectable `httpx.Client` stub. No real
SGO calls. The end-to-end proof walks the full path:

    mocked HTTP response
      → SGOPayloadProvider.fetch_event_odds (sanitize)
      → assert_sanitized + atomic write (recorder)
      → load_fixture (offline replay)
      → run_pass(dry_run)
      → team_live_props (zero rows)
      → team_odds_ingest_runs (one audit row)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub.collections import (  # noqa: E402
    ensure_team_collections,
)
from services.team_master_hub.fixtures import (  # noqa: E402
    REDACTION_TOKEN,
    FixtureSanitizationError,
    assert_sanitized,
    load_fixture,
    sanitize_response_bytes,
)
from workers.team._sgo_provider import (  # noqa: E402
    SGOFetchError,
    SGOPayloadProvider,
)
from workers.team.team_odds_ingest import (  # noqa: E402
    TeamOddsIngestWorker,
)


# ───────────────────────────────────────────────────────────────────
# Bearer-token regression
# ───────────────────────────────────────────────────────────────────
def test_assert_sanitized_catches_bearer_token_in_meta() -> None:
    payload = {"events": []}
    payload_bytes = json.dumps(payload).encode("utf-8")
    import hashlib
    meta = {
        "fixture_version": 1, "sanitization_version": 1,
        "recorded_at": "2026-06-02T00:00:00Z",
        "sport": "mlb", "sgo_endpoint": "/v2/events",
        "event_id": "x", "commence_time": "2026-06-02T22:00:00Z",
        "checksum_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "operator_note": "Bearer abc123def456ghi789",
    }
    with pytest.raises(FixtureSanitizationError) as exc:
        assert_sanitized(payload, meta)
    assert exc.value.rule == "leaked_key_meta"


def test_sanitize_response_bytes_strips_bearer_token() -> None:
    raw = b'{"err":"Authorization: Bearer abc123def456ghi789"}'
    out = sanitize_response_bytes(raw)
    assert b"abc123def456ghi789" not in out
    assert REDACTION_TOKEN.encode("utf-8") in out


def test_sanitize_bearer_keeps_short_words_alone() -> None:
    """The literal word 'bearer' in unrelated copy must not match —
    only when followed by ≥ 12 token-chars.
    """
    raw = b"The bearer of bad news arrived."
    assert sanitize_response_bytes(raw) == raw


# ───────────────────────────────────────────────────────────────────
# httpx.Client mock helpers
# ───────────────────────────────────────────────────────────────────
class _FakeResponse:
    def __init__(self, *, status_code: int = 200, content: bytes = b""):
        self.status_code = status_code
        self.content     = content


class _FakeHttpxClient:
    """Replacement for `httpx.Client` — records the last URL and
    returns a canned `_FakeResponse`. Never touches the network.
    """

    def __init__(self, *, response: _FakeResponse,
                  raise_on_get: Exception | None = None) -> None:
        self._response = response
        self._raise    = raise_on_get
        self.last_url:    str | None = None
        self.was_closed:  bool        = False

    def get(self, url: str) -> _FakeResponse:  # noqa: D401
        self.last_url = url
        if self._raise is not None:
            raise self._raise
        return self._response

    def close(self) -> None:
        self.was_closed = True


def _make_payload_bytes() -> bytes:
    from tests._team_odds_test_payloads import make_payload_bytes
    return make_payload_bytes(
        event_id="evt_e2e_001",
        books=("draftkings", "fanduel", "betmgm"),
    )


# ───────────────────────────────────────────────────────────────────
# SGOPayloadProvider
# ───────────────────────────────────────────────────────────────────
def test_provider_rejects_empty_api_key() -> None:
    with pytest.raises(SGOFetchError) as exc:
        SGOPayloadProvider("")
    assert exc.value.kind == "transport"


def test_provider_rejects_unknown_sport() -> None:
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_make_payload_bytes()))
    p = SGOPayloadProvider("k_THIS_IS_THE_OPERATOR_KEY", client=fake)
    with pytest.raises(SGOFetchError) as exc:
        p.fetch_event_odds(sport="formula1", event_id="x")
    assert exc.value.kind == "transport"


def test_provider_rejects_empty_event_id() -> None:
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_make_payload_bytes()))
    p = SGOPayloadProvider("k_THIS_IS_THE_OPERATOR_KEY", client=fake)
    with pytest.raises(SGOFetchError) as exc:
        p.fetch_event_odds(sport="mlb", event_id="")
    assert exc.value.kind == "transport"


def test_provider_fetch_happy_path() -> None:
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_make_payload_bytes()))
    p = SGOPayloadProvider("k_THIS_IS_THE_OPERATOR_KEY", client=fake)
    result = p.fetch_event_odds(sport="mlb", event_id="evt_e2e_001")
    assert result["books_seen"] == ["betmgm", "draftkings", "fanduel"]
    assert len(result["markets_seen"]) == 6
    # 3 books × 6 markets = 18
    assert result["outcomes_count"] == 18
    # The OUTBOUND URL (last_url) carries the real key — that's the
    # actual wire request
    assert "k_THIS_IS_THE_OPERATOR_KEY" in fake.last_url
    # But the sgo_endpoint returned to the caller has the key stripped
    assert "k_THIS_IS_THE_OPERATOR_KEY" not in result["sgo_endpoint"]
    assert REDACTION_TOKEN in result["sgo_endpoint"]


def test_provider_sanitizes_response_bytes() -> None:
    """If the SGO body somehow echoes the literal key back, the
    sanitizer strips it before parsing.
    """
    secret = "k_THIS_IS_THE_OPERATOR_KEY"
    body = b'{"events":[],"echoed_key":"' + secret.encode() + b'"}'
    fake = _FakeHttpxClient(response=_FakeResponse(content=body))
    p = SGOPayloadProvider(secret, client=fake)
    with pytest.raises(SGOFetchError) as exc:
        # No events → empty_payload error after sanitization
        p.fetch_event_odds(sport="mlb", event_id="x")
    assert exc.value.kind == "empty_payload"


def test_provider_http_status_failure() -> None:
    fake = _FakeHttpxClient(response=_FakeResponse(
        status_code=401, content=b'{"err":"unauthorized"}'))
    p = SGOPayloadProvider("k_THIS_IS_THE_OPERATOR_KEY", client=fake)
    with pytest.raises(SGOFetchError) as exc:
        p.fetch_event_odds(sport="mlb", event_id="x")
    assert exc.value.kind == "http_status"
    assert "401" in str(exc.value)


def test_provider_transport_failure() -> None:
    fake = _FakeHttpxClient(
        response=_FakeResponse(),
        raise_on_get=httpx.ConnectError("DNS hiccup"),
    )
    p = SGOPayloadProvider("k_THIS_IS_THE_OPERATOR_KEY", client=fake)
    with pytest.raises(SGOFetchError) as exc:
        p.fetch_event_odds(sport="mlb", event_id="x")
    assert exc.value.kind == "transport"


def test_provider_json_decode_failure() -> None:
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=b"not-json-at-all"))
    p = SGOPayloadProvider("k_THIS_IS_THE_OPERATOR_KEY", client=fake)
    with pytest.raises(SGOFetchError) as exc:
        p.fetch_event_odds(sport="mlb", event_id="x")
    assert exc.value.kind == "json_decode"


def test_provider_empty_payload_failure() -> None:
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=b'{"events":[]}'))
    p = SGOPayloadProvider("k_THIS_IS_THE_OPERATOR_KEY", client=fake)
    with pytest.raises(SGOFetchError) as exc:
        p.fetch_event_odds(sport="mlb", event_id="x")
    assert exc.value.kind == "empty_payload"


# ───────────────────────────────────────────────────────────────────
# Recorder body (subprocess-style test runs the CLI in-process via
# `main()` since the subprocess can't easily inject the httpx mock.
# CLI argparse contract was already pinned in Phase 1.A.3.2 tests.)
# ───────────────────────────────────────────────────────────────────
def _run_recorder_in_process(
    args_list: list[str],
    *,
    monkeypatch,
    fake_client: _FakeHttpxClient | None = None,
) -> int:
    """Call the recorder's `main()` in-process while injecting the
    fake httpx client into the provider. Returns the exit code.
    """
    if fake_client is not None:
        from workers.team import _sgo_provider as prov_mod

        class _PatchedProvider(SGOPayloadProvider):
            def __init__(self, api_key, **kw):  # noqa: D401
                super().__init__(api_key, client=fake_client, **kw)

        monkeypatch.setattr(prov_mod, "SGOPayloadProvider",
                              _PatchedProvider, raising=True)
        # The CLI imports SGOPayloadProvider at module level, so we
        # also need to patch the CLI's reference
        from scripts import team_odds_fixture_record as cli_mod
        monkeypatch.setattr(cli_mod, "SGOPayloadProvider",
                              _PatchedProvider, raising=True)

    from scripts.team_odds_fixture_record import main
    monkeypatch.setattr(sys, "argv",
                          ["team_odds_fixture_record"] + args_list)
    return main()


def test_recorder_aborts_when_guard_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("SGO_API_KEY",         raising=False)
    monkeypatch.delenv("TEAM_INGEST_ENABLED", raising=False)
    rc = _run_recorder_in_process(
        ["--sport", "mlb", "--event-id", "evt_x",
          "--recorded-by", "ops", "--output", str(tmp_path),
          "--yes"],
        monkeypatch=monkeypatch,
    )
    assert rc == 3
    # Output dir empty
    assert not any(tmp_path.iterdir())


def test_recorder_print_plan_does_not_call_http(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_THIS_IS_THE_OPERATOR_KEY")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    rc = _run_recorder_in_process(
        ["--sport", "mlb", "--event-id", "evt_x",
          "--recorded-by", "ops", "--output", str(tmp_path),
          "--print-plan"],
        monkeypatch=monkeypatch,
    )
    assert rc == 0
    assert not any(tmp_path.iterdir())


def test_recorder_happy_path_writes_two_files(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_THIS_IS_THE_OPERATOR_KEY")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_make_payload_bytes()))
    rc = _run_recorder_in_process(
        ["--sport", "mlb", "--event-id", "evt_e2e_001",
          "--recorded-by", "ops-e2e",
          "--output", str(tmp_path), "--yes"],
        monkeypatch=monkeypatch, fake_client=fake,
    )
    assert rc == 0

    # Two files written under tmp_path/mlb/
    mlb_dir = tmp_path / "mlb"
    files = sorted(mlb_dir.iterdir())
    assert len(files) == 2
    json_files = [p for p in files if not p.name.endswith(".meta.json")]
    meta_files = [p for p in files if p.name.endswith(".meta.json")]
    assert len(json_files) == 1
    assert len(meta_files) == 1
    payload_path = json_files[0]

    # Filename pattern: <UTC_DATE>_<event_id>.json
    assert payload_path.name == "2026-06-02_evt_e2e_001.json"

    # load_fixture round-trips cleanly (sanitization + checksum pass)
    loaded = load_fixture(payload_path)
    assert loaded.meta["sport"] == "mlb"
    assert loaded.meta["event_id"] == "evt_e2e_001"
    assert loaded.meta["recorded_by"] == "ops-e2e"
    assert loaded.meta["books_seen"] == ["betmgm", "draftkings", "fanduel"]
    assert len(loaded.meta["markets_seen"]) == 6
    assert loaded.meta["outcomes_count"] == 18
    # The key is in the OUTBOUND URL but NOT in the meta endpoint
    assert "k_THIS_IS_THE_OPERATOR_KEY" not in loaded.meta["sgo_endpoint"]
    assert REDACTION_TOKEN in loaded.meta["sgo_endpoint"]

    # No leaked key anywhere on disk
    payload_bytes = payload_path.read_bytes()
    meta_bytes    = (mlb_dir / meta_files[0].name).read_bytes()
    assert b"k_THIS_IS_THE_OPERATOR_KEY" not in payload_bytes
    assert b"k_THIS_IS_THE_OPERATOR_KEY" not in meta_bytes


def test_recorder_aborts_on_sanitization_failure(
    monkeypatch, tmp_path,
) -> None:
    """If the upstream SGO payload contains a forbidden field, the
    recorder must NOT write the files and must exit non-zero.
    """
    monkeypatch.setenv("SGO_API_KEY", "k_THIS_IS_THE_OPERATOR_KEY")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    poisoned = json.loads(_make_payload_bytes())
    poisoned["events"][0]["operator_email"] = "leak@example.com"
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=json.dumps(poisoned).encode("utf-8")))
    rc = _run_recorder_in_process(
        ["--sport", "mlb", "--event-id", "evt_e2e_002",
          "--recorded-by", "ops",
          "--output", str(tmp_path), "--yes"],
        monkeypatch=monkeypatch, fake_client=fake,
    )
    assert rc == 6   # sanitization failure exit code
    # No real files written (only possibly `.tmp` residue — and even
    # that should be cleaned up if the writer ran)
    real_files = [p for p in (tmp_path / "mlb").glob("*")
                   if not p.name.endswith(".tmp")] \
                  if (tmp_path / "mlb").exists() else []
    assert real_files == []


def test_recorder_aborts_on_http_error(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("SGO_API_KEY", "k_THIS_IS_THE_OPERATOR_KEY")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    fake = _FakeHttpxClient(response=_FakeResponse(
        status_code=429, content=b'"rate limited"'))
    rc = _run_recorder_in_process(
        ["--sport", "mlb", "--event-id", "evt_e2e_003",
          "--recorded-by", "ops",
          "--output", str(tmp_path), "--yes"],
        monkeypatch=monkeypatch, fake_client=fake,
    )
    assert rc == 5   # SGO fetch failure
    assert not (tmp_path / "mlb").exists() or \
           not any((tmp_path / "mlb").iterdir())


# ───────────────────────────────────────────────────────────────────
# Worker dry-run HTTP fetcher integration (`fetch_and_run_pass`)
# ───────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    mongo_url = os.environ["MONGO_URL"]
    client = AsyncIOMotorClient(mongo_url)
    name = "team_odds_fetch_and_run_pass_shared"
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


@pytest.mark.asyncio
async def test_fetch_and_run_pass_dry_run_no_writes(
    db, monkeypatch,
) -> None:
    """End-to-end dry-run path: mocked HTTP → sanitize → run_pass →
    audit row, ZERO `team_live_props` writes."""
    monkeypatch.delenv("TEAM_INGEST_LIVE", raising=False)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_make_payload_bytes()))
    provider = SGOPayloadProvider("k_TEST_KEY_FETCH_AND_RUN_PASS",
                                    client=fake)
    worker = TeamOddsIngestWorker("mlb")
    res = await worker.fetch_and_run_pass(
        db, event_id="evt_e2e_001",
        api_key="k_TEST_KEY_FETCH_AND_RUN_PASS",
        snapshot_iso="2026-06-02T22:00:00Z",
        mode="dry_run",
        provider=provider,
    )
    assert res["status"] == "dry_run"
    assert res["n_rows_normalized"] == 18
    assert res["n_writes"] == 0

    # Verify the mocked HTTP was actually called
    assert fake.last_url is not None
    assert "k_TEST_KEY_FETCH_AND_RUN_PASS" in fake.last_url

    assert await db["team_live_props"].count_documents({}) == 0
    assert await db["team_odds_ingest_runs"].count_documents({}) == 1


@pytest.mark.asyncio
async def test_fetch_and_run_pass_live_mode_with_guard_closed(
    db, monkeypatch,
) -> None:
    """Even when caller passes `mode='live'`, with guard closed the
    write still gets downgraded — Phase 1.A.3.3 must not silently
    enable live writes."""
    monkeypatch.delenv("SGO_API_KEY",         raising=False)
    monkeypatch.delenv("TEAM_INGEST_ENABLED", raising=False)
    monkeypatch.delenv("TEAM_INGEST_LIVE",    raising=False)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_make_payload_bytes()))
    provider = SGOPayloadProvider("k_GUARD_CLOSED_TEST",
                                    client=fake)
    worker = TeamOddsIngestWorker("mlb")
    res = await worker.fetch_and_run_pass(
        db, event_id="evt_e2e_001",
        api_key="k_GUARD_CLOSED_TEST", mode="live",
        provider=provider,
    )
    assert res["status"] == "guard_closed"
    assert res["n_writes"] == 0
    assert await db["team_live_props"].count_documents({}) == 0


@pytest.mark.asyncio
async def test_fetch_and_run_pass_propagates_sgo_fetch_error(
    db, monkeypatch,
) -> None:
    await ensure_team_collections(db)
    fake = _FakeHttpxClient(response=_FakeResponse(
        status_code=500, content=b"server explode"))
    provider = SGOPayloadProvider("k_PROPAGATE_TEST", client=fake)
    worker = TeamOddsIngestWorker("mlb")
    with pytest.raises(SGOFetchError) as exc:
        await worker.fetch_and_run_pass(
            db, event_id="x", api_key="k_PROPAGATE_TEST",
            provider=provider,
        )
    assert exc.value.kind == "http_status"
    # No audit row written for transport failures (caller decides
    # how to handle)
    assert await db["team_odds_ingest_runs"].count_documents({}) == 0


# ───────────────────────────────────────────────────────────────────
# Full path proof: recorder → load_fixture → run_pass (dry-run)
# ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_full_path_record_then_replay_then_run_pass(
    db, monkeypatch, tmp_path,
) -> None:
    """The canonical Phase 1.A.3.3 end-to-end proof:

        mocked HTTP response
          → SGOPayloadProvider.fetch_event_odds (sanitize)
          → recorder atomic write of {payload, meta}
          → load_fixture (offline replay re-runs assert_sanitized
             + verifies checksum)
          → run_pass(mode='dry_run')
          → team_live_props (zero rows)
          → team_odds_ingest_runs (one audit row)
    """
    # ── Setup ──
    monkeypatch.setenv("SGO_API_KEY", "k_THIS_IS_THE_OPERATOR_KEY")
    monkeypatch.setenv("TEAM_INGEST_ENABLED", "1")
    monkeypatch.delenv("TEAM_INGEST_LIVE", raising=False)
    await ensure_team_collections(db)
    await _seed_master_hub(db)

    # ── Step 1: record via the CLI (HTTP mocked) ──
    fake = _FakeHttpxClient(response=_FakeResponse(
        content=_make_payload_bytes()))
    rc = _run_recorder_in_process(
        ["--sport", "mlb", "--event-id", "evt_e2e_001",
          "--recorded-by", "ops-e2e-full-path",
          "--output", str(tmp_path), "--yes"],
        monkeypatch=monkeypatch, fake_client=fake,
    )
    assert rc == 0

    # ── Step 2: discover the written fixture ──
    payload_files = sorted(
        p for p in (tmp_path / "mlb").iterdir()
        if not p.name.endswith(".meta.json")
    )
    assert len(payload_files) == 1
    payload_path = payload_files[0]

    # ── Step 3: offline replay ──
    loaded = load_fixture(payload_path)
    assert loaded.meta["event_id"] == "evt_e2e_001"

    # ── Step 4: feed into run_pass(dry_run) ──
    worker = TeamOddsIngestWorker("mlb")
    res = await worker.run_pass(
        db, loaded.payload,
        snapshot_iso=loaded.meta["recorded_at"],
        mode="dry_run",
    )
    assert res["status"] == "dry_run"
    assert res["n_rows_normalized"] == 18
    assert res["n_writes"] == 0

    # ── Step 5: invariants ──
    assert await db["team_live_props"].count_documents({}) == 0
    assert await db["team_odds_ingest_runs"].count_documents({}) == 1
    # And the operator key is not on disk anywhere
    on_disk = b"".join(p.read_bytes() for p in
                         (tmp_path / "mlb").rglob("*")
                         if p.is_file())
    assert b"k_THIS_IS_THE_OPERATOR_KEY" not in on_disk


# ───────────────────────────────────────────────────────────────────
# Phase invariant: no real fixtures committed yet
# ───────────────────────────────────────────────────────────────────
def test_no_committed_fixtures_under_tests_fixtures_team_odds() -> None:
    root = Path("/app/backend/tests/fixtures/team_odds")
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file() and p.name != "README.md":
                pytest.fail(
                    f"Phase 1.A.3.3 still forbids any committed "
                    f"fixture under {root}, found: {p}"
                )

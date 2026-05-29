"""
Phase 1.A.3.2 — fixture loader + sanitizer + CLI stub tests.

Pure tests — no network, no SGO calls, no commits under
`backend/tests/fixtures/team_odds/`. Each test writes its own
fixture into a tmp_path and exercises the loader against it.

Also verifies end-to-end replay through `run_pass(dry_run)` so the
shape contract holds.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub.collections import (  # noqa: E402
    ensure_team_collections,
)
from services.team_master_hub.fixtures import (  # noqa: E402
    LoadedFixture,
    REDACTION_TOKEN,
    SANITIZATION_VERSION,
    FixtureSanitizationError,
    assert_sanitized,
    compute_checksum,
    list_fixtures,
    load_fixture,
    sanitize_response_bytes,
)
from services.team_master_hub.collections import (  # noqa: E402,F401
    TEAM_COLLECTIONS,
)
from workers.team.team_odds_ingest import (  # noqa: E402
    TeamOddsIngestWorker,
)


# ── Tiny synthetic fixture builder ──────────────────────────────────
def _synthetic_payload() -> dict:
    """Real SGO v2 shape — used as the payload-on-disk for fixture
    loader tests. The provider already normalizes `data` → `events`,
    so the persisted shape here uses `events` directly.
    """
    from tests._team_odds_test_payloads import make_events_envelope
    return make_events_envelope(
        event_id="evt_fixture_001",
        books=("draftkings",),
    )


def _meta(payload_bytes: bytes, *, overrides: dict | None = None) -> dict:
    base = {
        "fixture_version":      1,
        "sanitization_version": SANITIZATION_VERSION,
        "recorded_at":          "2026-06-02T22:00:00Z",
        "sport":                "mlb",
        "sgo_endpoint":         "/v2/events?league=MLB",
        "event_id":             "evt_fixture_001",
        "commence_time":        "2026-06-02T22:00:00Z",
        "checksum_sha256":      compute_checksum(payload_bytes),
    }
    if overrides:
        base.update(overrides)
    return base


def _write_fixture(tmp_path: Path,
                    payload: dict,
                    meta_overrides: dict | None = None,
                    *,
                    name: str = "evt_fixture_001.json") -> Path:
    payload_bytes = json.dumps(payload, ensure_ascii=False,
                                sort_keys=True).encode("utf-8")
    meta = _meta(payload_bytes, overrides=meta_overrides)
    (tmp_path / "mlb").mkdir(parents=True, exist_ok=True)
    payload_path = tmp_path / "mlb" / name
    meta_path    = tmp_path / "mlb" / name.replace(".json", ".meta.json")
    payload_path.write_bytes(payload_bytes)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return payload_path


# ── assert_sanitized — happy path ────────────────────────────────────
def test_assert_sanitized_accepts_clean_fixture() -> None:
    payload = _synthetic_payload()
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes)
    # Should not raise
    assert_sanitized(payload, meta)


# ── meta_shape ───────────────────────────────────────────────────────
def test_meta_shape_missing_key_raises() -> None:
    payload = _synthetic_payload()
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes)
    meta.pop("recorded_at")
    with pytest.raises(FixtureSanitizationError) as exc:
        assert_sanitized(payload, meta)
    assert exc.value.rule == "meta_shape"


# ── sanitization_version ─────────────────────────────────────────────
def test_sanitization_version_mismatch_raises() -> None:
    payload = _synthetic_payload()
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes)
    meta["sanitization_version"] = SANITIZATION_VERSION + 5
    with pytest.raises(FixtureSanitizationError) as exc:
        assert_sanitized(payload, meta)
    assert exc.value.rule == "sanitization_version"


# ── endpoint_allow_list ──────────────────────────────────────────────
@pytest.mark.parametrize("endpoint", [
    "/v2/events",                              # bare path — ok
    "https://api.sportsgameodds.com/v2/events", # allowed host — ok
])
def test_endpoint_allow_list_accepts_allowed(endpoint) -> None:
    payload = _synthetic_payload()
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes, overrides={"sgo_endpoint": endpoint})
    assert_sanitized(payload, meta)


@pytest.mark.parametrize("endpoint", [
    "https://evil-mirror.example.com/v2/events",
    "https://api.bad-clone.com/v2/events",
    "",   # empty
])
def test_endpoint_allow_list_rejects_others(endpoint) -> None:
    payload = _synthetic_payload()
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes, overrides={"sgo_endpoint": endpoint})
    with pytest.raises(FixtureSanitizationError) as exc:
        assert_sanitized(payload, meta)
    assert exc.value.rule == "endpoint_allow_list"


# ── leaked_key detection ─────────────────────────────────────────────
def test_leaked_key_in_meta_raises() -> None:
    payload = _synthetic_payload()
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes)
    meta["debug_url"] = "https://api.sportsgameodds.com/v2/events?api_key=AAAA1111BBBB2222CCCC"
    with pytest.raises(FixtureSanitizationError) as exc:
        assert_sanitized(payload, meta)
    assert exc.value.rule == "leaked_key_meta"


def test_leaked_key_in_payload_raises() -> None:
    payload = _synthetic_payload()
    payload["debug"] = "x-rapidapi-key was here"
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes)
    with pytest.raises(FixtureSanitizationError) as exc:
        assert_sanitized(payload, meta)
    assert exc.value.rule == "leaked_key_payload"


# ── forbidden_field ──────────────────────────────────────────────────
def test_forbidden_field_in_payload_raises() -> None:
    payload = _synthetic_payload()
    payload["events"][0]["operator_email"] = "ops@example.com"
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes)
    with pytest.raises(FixtureSanitizationError) as exc:
        assert_sanitized(payload, meta)
    assert exc.value.rule == "forbidden_field"


# ── payload_shape ────────────────────────────────────────────────────
def test_payload_shape_missing_events_raises() -> None:
    payload = {"games": []}   # wrong top-level key
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes)
    with pytest.raises(FixtureSanitizationError) as exc:
        assert_sanitized(payload, meta)
    assert exc.value.rule == "payload_shape"


# ── recorded_at_present ──────────────────────────────────────────────
def test_recorded_at_empty_raises() -> None:
    payload = _synthetic_payload()
    payload_bytes = json.dumps(payload).encode("utf-8")
    meta = _meta(payload_bytes, overrides={"recorded_at": "   "})
    with pytest.raises(FixtureSanitizationError) as exc:
        assert_sanitized(payload, meta)
    assert exc.value.rule == "recorded_at_present"


# ── sanitize_response_bytes ──────────────────────────────────────────
def test_sanitize_strips_literal_api_key() -> None:
    raw = b'{"url":"?api_key=SUPER_SECRET_KEY_12345","ok":true}'
    out = sanitize_response_bytes(raw,
        api_key_to_strip="SUPER_SECRET_KEY_12345")
    assert b"SUPER_SECRET_KEY_12345" not in out
    assert REDACTION_TOKEN.encode("utf-8") in out


def test_sanitize_strips_query_param_pattern() -> None:
    raw = b"https://api.sportsgameodds.com/v2?api_key=ABCDEFGH12345"
    out = sanitize_response_bytes(raw)
    assert b"ABCDEFGH12345" not in out
    assert REDACTION_TOKEN.encode("utf-8") in out


def test_sanitize_idempotent() -> None:
    raw = b"clean payload no secrets"
    assert sanitize_response_bytes(raw) == raw


# ── load_fixture happy path ──────────────────────────────────────────
def test_load_fixture_happy_path(tmp_path) -> None:
    payload = _synthetic_payload()
    payload_path = _write_fixture(tmp_path, payload)
    loaded = load_fixture(payload_path)
    assert isinstance(loaded, LoadedFixture)
    assert loaded.payload == payload
    assert loaded.meta["sport"] == "mlb"
    assert loaded.path == payload_path.resolve() or \
           loaded.path == payload_path


# ── load_fixture failures ────────────────────────────────────────────
def test_load_fixture_missing_payload(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_fixture(tmp_path / "mlb" / "missing.json")


def test_load_fixture_missing_meta(tmp_path) -> None:
    payload_path = _write_fixture(tmp_path, _synthetic_payload())
    # Delete the sibling meta
    meta_path = payload_path.with_suffix(".meta.json")
    meta_path.unlink()
    with pytest.raises(FileNotFoundError):
        load_fixture(payload_path)


def test_load_fixture_checksum_mismatch(tmp_path) -> None:
    payload_path = _write_fixture(tmp_path, _synthetic_payload())
    # Corrupt the meta checksum
    meta_path = payload_path.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text())
    meta["checksum_sha256"] = "0" * 64
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_fixture(payload_path)


def test_load_fixture_runs_sanitization(tmp_path) -> None:
    payload = _synthetic_payload()
    payload["events"][0]["operator_email"] = "leaked@example.com"
    payload_path = _write_fixture(tmp_path, payload)
    with pytest.raises(FixtureSanitizationError) as exc:
        load_fixture(payload_path)
    assert exc.value.rule == "forbidden_field"


# ── list_fixtures ────────────────────────────────────────────────────
def test_list_fixtures_finds_all_json_excluding_meta(tmp_path) -> None:
    _write_fixture(tmp_path, _synthetic_payload(),
                    name="evt_001.json")
    _write_fixture(tmp_path, _synthetic_payload(),
                    name="evt_002.json")
    paths = list_fixtures(tmp_path)
    # Two payload files, no meta sidecars
    assert len(paths) == 2
    assert all(p.name.endswith(".json") for p in paths)
    assert not any(p.name.endswith(".meta.json") for p in paths)
    # Sorted
    assert paths == sorted(paths)


def test_list_fixtures_empty_dir(tmp_path) -> None:
    assert list_fixtures(tmp_path) == []


def test_list_fixtures_nonexistent_dir(tmp_path) -> None:
    assert list_fixtures(tmp_path / "does_not_exist") == []


# ── No real fixture committed under tests/fixtures/team_odds/ ────────
def test_no_team_odds_fixtures_committed_yet() -> None:
    """Phase 1.A.3.2 contract: NO fixture files exist in-repo yet."""
    fixture_root = Path("/app/backend/tests/fixtures/team_odds")
    if fixture_root.exists():
        # Allow only the README placeholder, if any
        leftovers = [p for p in fixture_root.rglob("*")
                       if p.is_file() and not p.name == "README.md"]
        assert leftovers == [], (
            f"Phase 1.A.3.2 forbids committed fixtures, found: "
            f"{leftovers}"
        )


# ── CLI argparse contract ────────────────────────────────────────────
def test_cli_argparse_contract() -> None:
    from scripts.team_odds_fixture_record import _build_parser
    parser = _build_parser()
    # Required flags: --sport, --event-id, --recorded-by
    args = parser.parse_args([
        "--sport", "mlb",
        "--event-id", "evt_abc",
        "--recorded-by", "ops-alice",
    ])
    assert args.sport       == "mlb"
    assert args.event_id    == "evt_abc"
    assert args.recorded_by == "ops-alice"
    assert args.output      == "backend/tests/fixtures/team_odds"
    assert args.yes         is False
    assert args.print_plan  is False
    # Sport choice gate
    with pytest.raises(SystemExit):
        parser.parse_args(["--sport", "formula1",
                            "--event-id", "x",
                            "--recorded-by", "y"])


def test_cli_print_plan_exits_zero_no_writes(tmp_path) -> None:
    """`--print-plan` must NEVER touch the network OR the filesystem
    under `--output`. Run as a subprocess so any leakage surfaces.
    """
    out_dir = tmp_path / "would_record_here"
    out_dir.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.team_odds_fixture_record",
          "--sport", "mlb",
          "--event-id", "evt_xyz",
          "--recorded-by", "ops-alice",
          "--output", str(out_dir),
          "--print-plan"],
        cwd="/app/backend",
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, (
        f"--print-plan should exit 0; got {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "RECORDING PLAN" in proc.stdout
    # Output dir is still empty
    assert list(out_dir.iterdir()) == []


def test_cli_default_refuses_without_guard(tmp_path, monkeypatch) -> None:
    """Without SGO_API_KEY + TEAM_INGEST_ENABLED, the CLI must abort
    with exit code 3 and write nothing.
    """
    out_dir = tmp_path / "would_record_here"
    out_dir.mkdir()
    env = {k: v for k, v in os.environ.items()
            if k not in ("SGO_API_KEY", "TEAM_INGEST_ENABLED")}
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.team_odds_fixture_record",
          "--sport", "mlb",
          "--event-id", "evt_xyz",
          "--recorded-by", "ops-alice",
          "--output", str(out_dir),
          "--yes"],
        cwd="/app/backend",
        capture_output=True, text=True, timeout=15,
        env=env,
    )
    assert proc.returncode == 3, (
        f"guard-closed should exit 3; got {proc.returncode}\n"
        f"stderr: {proc.stderr}"
    )
    assert "dispatch guard closed" in proc.stderr.lower()
    assert list(out_dir.iterdir()) == []


# ── End-to-end replay via run_pass (offline) ─────────────────────────
@pytest_asyncio.fixture
async def db():
    mongo_url = __import__("os").environ["MONGO_URL"]
    client = AsyncIOMotorClient(mongo_url)
    name = "team_odds_fixture_replay_shared"
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


@pytest.mark.asyncio
async def test_fixture_replays_through_run_pass(db, tmp_path) -> None:
    """Tier 3 contract check: a loaded fixture flows through
    `run_pass(dry_run=True)` without SGO calls and without writes."""
    await ensure_team_collections(db)
    await db["team_master_hub"].insert_one({
        "team_id": "mlb_nyy", "sport": "mlb",
        "display_names": {"full": "New York Yankees",
                            "short": "Yankees",
                            "abbrev": "NYY",
                            "market": "New York"},
    })

    payload_path = _write_fixture(tmp_path, _synthetic_payload())
    loaded = load_fixture(payload_path)

    worker = TeamOddsIngestWorker("mlb")
    res = await worker.run_pass(
        db, loaded.payload,
        snapshot_iso=loaded.meta["recorded_at"],
        mode="dry_run",
    )
    assert res["status"] == "dry_run"
    assert res["n_rows_normalized"] == 6   # 6 prod markets × 1 book
    # Zero rows in team_live_props
    assert await db["team_live_props"].count_documents({}) == 0
    # One audit row
    assert await db["team_odds_ingest_runs"].count_documents({}) == 1


# ── checksum helper sanity ───────────────────────────────────────────
def test_compute_checksum_deterministic() -> None:
    payload = _synthetic_payload()
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    assert compute_checksum(blob) == hashlib.sha256(blob).hexdigest()

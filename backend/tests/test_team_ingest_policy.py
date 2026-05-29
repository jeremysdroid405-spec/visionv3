"""
Phase 1.A.3.0 — Team ingest policy tests.

Pure unit tests, no DB, no network. Pins the locked defaults,
fail-closed dispatch + dry-run behavior, exponential backoff math,
the two kill-switches, and the book-policy SSOT inheritance.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/app/backend")

from services import team_policy  # noqa: E402
from services.team_master_hub.ingest_policy import (  # noqa: E402
    DEFAULT_ABORT_ERROR_MIN_SAMPLE,
    DEFAULT_ABORT_ERROR_RATE_THRESHOLD,
    DEFAULT_ABORT_MARKET_EXPLOSION_MIN,
    DEFAULT_ABORT_MARKET_EXPLOSION_RATIO,
    DEFAULT_BACKOFF_CAP_SEC,
    DEFAULT_LIVE_TTL_HOURS,
    DEFAULT_MAX_RPM_PER_SPORT,
    DEFAULT_RETRY_COUNT,
    ENABLE_FLAG_ENV,
    SGO_KEY_ENV,
    TeamIngestPolicy,
    dispatch_guard_ok,
    dry_run_default,
    is_book_blocked,
    is_book_reference_only,
    next_backoff_seconds,
    policy_summary,
    should_abort_on_error_rate,
    should_abort_on_market_explosion,
)


# ── Fixtures ─────────────────────────────────────────────────────────
@pytest.fixture
def guard_closed(monkeypatch):
    monkeypatch.delenv(SGO_KEY_ENV,    raising=False)
    monkeypatch.delenv(ENABLE_FLAG_ENV, raising=False)
    monkeypatch.delenv("TEAM_INGEST_LIVE", raising=False)


@pytest.fixture
def guard_open(monkeypatch):
    monkeypatch.setenv(SGO_KEY_ENV,    "test_key")
    monkeypatch.setenv(ENABLE_FLAG_ENV, "1")
    monkeypatch.delenv("TEAM_INGEST_LIVE", raising=False)


# ── Defaults (locked) ────────────────────────────────────────────────
def test_default_policy_constants() -> None:
    assert DEFAULT_MAX_RPM_PER_SPORT == {"mlb": 60, "nba": 60, "nfl": 30}
    assert DEFAULT_RETRY_COUNT == 5
    assert DEFAULT_BACKOFF_CAP_SEC == 10.0
    assert DEFAULT_LIVE_TTL_HOURS == 48
    assert DEFAULT_ABORT_ERROR_RATE_THRESHOLD == 0.25
    assert DEFAULT_ABORT_ERROR_MIN_SAMPLE == 20
    assert DEFAULT_ABORT_MARKET_EXPLOSION_RATIO == 3.0
    assert DEFAULT_ABORT_MARKET_EXPLOSION_MIN == 5


def test_policy_is_frozen_dataclass() -> None:
    p = TeamIngestPolicy()
    with pytest.raises(Exception):  # FrozenInstanceError
        p.retry_count = 99  # type: ignore[misc]


def test_policy_from_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("TEAM_INGEST_MAX_RPM_MLB", "120")
    monkeypatch.setenv("TEAM_INGEST_RETRY_COUNT", "7")
    monkeypatch.setenv("TEAM_INGEST_BACKOFF_CAP_SEC", "20.5")
    monkeypatch.setenv("TEAM_INGEST_LIVE_TTL_HOURS", "72")
    p = TeamIngestPolicy.from_env()
    assert p.max_rpm_per_sport["mlb"] == 120
    assert p.max_rpm_per_sport["nba"] == 60   # untouched
    assert p.retry_count == 7
    assert p.backoff_cap_sec == 20.5
    assert p.live_ttl_hours == 72


def test_policy_from_env_falls_back_on_invalid(monkeypatch) -> None:
    monkeypatch.setenv("TEAM_INGEST_RETRY_COUNT", "not_an_int")
    monkeypatch.setenv("TEAM_INGEST_BACKOFF_CAP_SEC", "")
    p = TeamIngestPolicy.from_env()
    assert p.retry_count == DEFAULT_RETRY_COUNT
    assert p.backoff_cap_sec == DEFAULT_BACKOFF_CAP_SEC


# ── Dispatch guard + dry-run default ─────────────────────────────────
def test_dispatch_guard_closed(guard_closed) -> None:
    ok, reasons = dispatch_guard_ok()
    assert ok is False
    assert any(SGO_KEY_ENV in r for r in reasons)
    assert any(ENABLE_FLAG_ENV in r for r in reasons)


def test_dispatch_guard_open(guard_open) -> None:
    ok, reasons = dispatch_guard_ok()
    assert ok is True
    assert reasons == []


def test_dry_run_default_true_when_guard_closed(guard_closed) -> None:
    assert dry_run_default() is True


def test_dry_run_default_true_even_when_guard_open(guard_open) -> None:
    # Guard open, but TEAM_INGEST_LIVE not set → still dry-run by default
    assert dry_run_default() is True


def test_dry_run_default_false_only_with_explicit_live_flag(
    guard_open, monkeypatch,
) -> None:
    monkeypatch.setenv("TEAM_INGEST_LIVE", "1")
    assert dry_run_default() is False


def test_dry_run_default_true_when_live_flag_but_guard_closed(
    guard_closed, monkeypatch,
) -> None:
    # Even with explicit live flag, missing key/enable closes the guard.
    monkeypatch.setenv("TEAM_INGEST_LIVE", "1")
    assert dry_run_default() is True


# ── Exponential backoff ──────────────────────────────────────────────
@pytest.mark.parametrize("attempt,expected", [
    (0, 0.0),
    (1, 0.5),
    (2, 1.0),
    (3, 2.0),
    (4, 4.0),
    (5, 8.0),
    (6, 10.0),   # capped at backoff_cap_sec
    (7, 10.0),   # still capped
    (20, 10.0),  # still capped
])
def test_next_backoff_seconds(attempt, expected) -> None:
    assert next_backoff_seconds(attempt) == expected


def test_backoff_respects_custom_policy() -> None:
    p = TeamIngestPolicy(backoff_base_sec=1.0,
                         backoff_factor=3.0,
                         backoff_cap_sec=50.0)
    assert next_backoff_seconds(1, p) == 1.0
    assert next_backoff_seconds(2, p) == 3.0
    assert next_backoff_seconds(3, p) == 9.0
    assert next_backoff_seconds(4, p) == 27.0
    assert next_backoff_seconds(5, p) == 50.0  # capped


# ── Error-rate kill switch ───────────────────────────────────────────
def test_abort_below_min_sample_returns_false() -> None:
    abort, reason = should_abort_on_error_rate(n_errors=10, n_requests=10)
    assert abort is False
    assert "sample below" in reason


def test_abort_at_threshold_returns_true() -> None:
    # 25/100 = 25% → at threshold → abort
    abort, reason = should_abort_on_error_rate(n_errors=25, n_requests=100)
    assert abort is True
    assert "25" in reason


def test_abort_below_threshold_returns_false() -> None:
    abort, reason = should_abort_on_error_rate(n_errors=4, n_requests=100)
    assert abort is False
    assert "below threshold" in reason


def test_abort_zero_errors_returns_false() -> None:
    abort, _ = should_abort_on_error_rate(n_errors=0, n_requests=100)
    assert abort is False


# ── Market-explosion kill switch ─────────────────────────────────────
def test_market_explosion_below_min_observed_returns_false() -> None:
    abort, reason = should_abort_on_market_explosion(
        observed_markets=4, expected_markets=2)
    # Even though 4/2 = 2.0× (not above 3.0×), it would also be below
    # the min observed sample. Either guard returning False is fine.
    assert abort is False


def test_market_explosion_fires_at_3x() -> None:
    abort, reason = should_abort_on_market_explosion(
        observed_markets=15, expected_markets=5)
    assert abort is True
    assert "3.00×" in reason or "3.0" in reason


def test_market_explosion_below_ratio_returns_false() -> None:
    abort, reason = should_abort_on_market_explosion(
        observed_markets=10, expected_markets=5)
    assert abort is False
    assert "below" in reason


def test_market_explosion_zero_expected_returns_false() -> None:
    abort, reason = should_abort_on_market_explosion(
        observed_markets=99, expected_markets=0)
    assert abort is False
    assert "no expected baseline" in reason


# ── Book policy SSOT inheritance (§14.5 identity invariant) ──────────
def test_policy_blocked_books_is_same_object_as_canonical() -> None:
    p = TeamIngestPolicy()
    assert p.blocked_books is team_policy.BLOCKED_BOOKS, (
        "TeamIngestPolicy.blocked_books MUST be the same Python "
        "object as services.team_policy.BLOCKED_BOOKS (§14.5)"
    )
    assert p.reference_only_books is team_policy.REFERENCE_ONLY_BOOKS


def test_is_book_blocked_inheritance() -> None:
    # `fliff` is in the canonical BLOCKED_BOOKS — assert that without
    # mutating the canonical set.
    assert is_book_blocked("fliff") is True
    assert is_book_blocked("FLIFF") is True  # case-insensitive
    assert is_book_blocked("draftkings") is False
    assert is_book_blocked("") is False


def test_is_book_reference_only_inheritance() -> None:
    # Underdog / PrizePicks live in REFERENCE_ONLY_BOOKS on the
    # canonical set
    assert is_book_reference_only("underdog") is True
    assert is_book_reference_only("prizepicks") is True
    assert is_book_reference_only("draftkings") is False


# ── policy_summary shape (the admin endpoint contract) ───────────────
def test_policy_summary_shape_with_guard_closed(guard_closed) -> None:
    s = policy_summary()
    assert s["ok"] is True
    assert s["dispatch_guard"]["allowed"] is False
    assert s["dispatch_guard"]["env_flag"]    == ENABLE_FLAG_ENV
    assert s["dispatch_guard"]["env_sgo_key"] == SGO_KEY_ENV
    assert s["dry_run_default"] is True
    assert s["rate_limit"]["max_rpm_per_sport"] == \
        dict(DEFAULT_MAX_RPM_PER_SPORT)
    assert s["retry"]["count"]           == DEFAULT_RETRY_COUNT
    assert s["retry"]["backoff_cap_sec"] == DEFAULT_BACKOFF_CAP_SEC
    assert s["retention"]["live_ttl_hours"] == DEFAULT_LIVE_TTL_HOURS
    assert s["kill_switches"]["abort_error_rate_threshold"] == \
        DEFAULT_ABORT_ERROR_RATE_THRESHOLD
    # Book policy preview is sorted lists, not the raw frozensets
    assert isinstance(s["book_policy"]["blocked_books"], list)
    assert s["book_policy"]["blocked_books"] == sorted(
        team_policy.BLOCKED_BOOKS)
    assert s["book_policy"]["reference_only_books"] == sorted(
        team_policy.REFERENCE_ONLY_BOOKS)
    # Schedule preview matches the deterministic backoff math
    sched = s["retry"]["schedule_preview"]
    assert sched[0] == 0.5
    assert sched[1] == 1.0
    assert sched[2] == 2.0
    assert sched[3] == 4.0
    assert sched[4] == 8.0  # attempt 5
    assert sched[5] == 10.0  # attempt 6 capped


def test_policy_summary_with_guard_open(guard_open) -> None:
    s = policy_summary()
    assert s["dispatch_guard"]["allowed"] is True
    assert s["dispatch_guard"]["reasons"] == []
    # Still dry-run by default — operator must explicitly set
    # TEAM_INGEST_LIVE=1 to flip dry_run_default to False.
    assert s["dry_run_default"] is True

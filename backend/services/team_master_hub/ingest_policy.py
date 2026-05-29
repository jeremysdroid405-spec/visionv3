"""
Phase 1.A.3.0 — Team ingest policy SSOT.

Pure configuration + pure helper functions. NO I/O, NO SGO calls,
NO Mongo writes. This module is the single source of truth for:

  - Dispatch guards (`TEAM_INGEST_ENABLED` + `SGO_API_KEY`)
  - Per-sport rate limits (requests per minute)
  - Retry count + exponential backoff schedule
  - Snapshot retention TTL on `team_live_props`
  - Dry-run default behavior (fail-closed when guard is closed)
  - Blocked / reference-only book inheritance
    (re-exports the canonical sets from `services.team_policy`
    so policy can never drift between player and team paths)
  - Abnormal error-rate abort threshold
  - Unexpected market-explosion abort threshold

Used by:
  - `workers/team/*` (cadence + backoff in the future ingest loop)
  - `routes/emergent_admin/team_master_hub.py::ingest_policy_endpoint`

Architecture: /app/memory/TEAM_PROPS_ARCHITECTURE.md §§4.3 / 14.5.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Tuple

# Canonical book policy — DO NOT redefine here. §14.5 invariant.
from services.team_policy import BLOCKED_BOOKS, REFERENCE_ONLY_BOOKS

# ── Dispatch guard env-var names ─────────────────────────────────────
ENABLE_FLAG_ENV: str = "TEAM_INGEST_ENABLED"
SGO_KEY_ENV:     str = "SGO_API_KEY"

# ── Defaults (locked) ────────────────────────────────────────────────
# Per-sport requests per minute. Conservative defaults; the real prod
# SGO plan can raise these via env override at Phase 1.A.3 launch.
DEFAULT_MAX_RPM_PER_SPORT: Mapping[str, int] = {
    "mlb": 60,
    "nba": 60,
    "nfl": 30,
}

# Retry / backoff schedule (exponential, capped). Total worst-case
# wait per ingest request: 0.5 + 1 + 2 + 4 + 8 = 15.5 s before the
# error counts toward the abort-on-error-rate gate.
DEFAULT_RETRY_COUNT:         int   = 5
DEFAULT_BACKOFF_BASE_SEC:    float = 0.5
DEFAULT_BACKOFF_FACTOR:      float = 2.0
DEFAULT_BACKOFF_CAP_SEC:     float = 10.0

# Snapshot retention on `team_live_props` BEFORE promotion to
# `team_historical_props`. 48 h matches the player-side TTL plan.
DEFAULT_LIVE_TTL_HOURS: int = 48

# Abnormal-condition kill switches. Worker self-aborts the current
# ingest pass if either fires; orchestrator surfaces the reason.
DEFAULT_ABORT_ERROR_RATE_THRESHOLD:    float = 0.25  # 25% errors
DEFAULT_ABORT_ERROR_MIN_SAMPLE:        int   = 20    # need ≥ 20 reqs
DEFAULT_ABORT_MARKET_EXPLOSION_RATIO:  float = 3.0   # observed/expected
DEFAULT_ABORT_MARKET_EXPLOSION_MIN:    int   = 5     # need ≥ 5 observed


@dataclass(frozen=True)
class TeamIngestPolicy:
    """Frozen snapshot of the effective team-ingest policy.

    Built via `TeamIngestPolicy.from_env()` so the operator can
    raise rate limits / lower thresholds with env vars without
    touching code. Every field is immutable once constructed.
    """
    # Dispatch
    enable_flag_env: str = ENABLE_FLAG_ENV
    sgo_key_env:     str = SGO_KEY_ENV

    # Rate limit
    max_rpm_per_sport: Mapping[str, int] = field(
        default_factory=lambda: dict(DEFAULT_MAX_RPM_PER_SPORT)
    )

    # Retry / backoff
    retry_count:      int   = DEFAULT_RETRY_COUNT
    backoff_base_sec: float = DEFAULT_BACKOFF_BASE_SEC
    backoff_factor:   float = DEFAULT_BACKOFF_FACTOR
    backoff_cap_sec:  float = DEFAULT_BACKOFF_CAP_SEC

    # Retention
    live_ttl_hours: int = DEFAULT_LIVE_TTL_HOURS

    # Kill switches
    abort_error_rate_threshold:   float = DEFAULT_ABORT_ERROR_RATE_THRESHOLD
    abort_error_min_sample:       int   = DEFAULT_ABORT_ERROR_MIN_SAMPLE
    abort_market_explosion_ratio: float = DEFAULT_ABORT_MARKET_EXPLOSION_RATIO
    abort_market_explosion_min:   int   = DEFAULT_ABORT_MARKET_EXPLOSION_MIN

    # Book policy — re-exported here so a single import surfaces
    # everything a worker needs.
    blocked_books:         FrozenSet[str] = field(
        default_factory=lambda: BLOCKED_BOOKS
    )
    reference_only_books:  FrozenSet[str] = field(
        default_factory=lambda: REFERENCE_ONLY_BOOKS
    )

    @classmethod
    def from_env(cls) -> "TeamIngestPolicy":
        """Build the policy from env overrides. Missing/invalid vars
        fall back to the locked defaults — never raises.

        Recognised overrides:
            TEAM_INGEST_MAX_RPM_MLB / _NBA / _NFL  (int)
            TEAM_INGEST_RETRY_COUNT                (int)
            TEAM_INGEST_BACKOFF_CAP_SEC            (float)
            TEAM_INGEST_LIVE_TTL_HOURS             (int)
        """
        def _int(name: str, default: int) -> int:
            v = os.environ.get(name)
            if v is None or v == "":
                return default
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        def _float(name: str, default: float) -> float:
            v = os.environ.get(name)
            if v is None or v == "":
                return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        rpm = {
            "mlb": _int("TEAM_INGEST_MAX_RPM_MLB",
                          DEFAULT_MAX_RPM_PER_SPORT["mlb"]),
            "nba": _int("TEAM_INGEST_MAX_RPM_NBA",
                          DEFAULT_MAX_RPM_PER_SPORT["nba"]),
            "nfl": _int("TEAM_INGEST_MAX_RPM_NFL",
                          DEFAULT_MAX_RPM_PER_SPORT["nfl"]),
        }
        return cls(
            max_rpm_per_sport=rpm,
            retry_count=_int("TEAM_INGEST_RETRY_COUNT",
                              DEFAULT_RETRY_COUNT),
            backoff_cap_sec=_float("TEAM_INGEST_BACKOFF_CAP_SEC",
                                    DEFAULT_BACKOFF_CAP_SEC),
            live_ttl_hours=_int("TEAM_INGEST_LIVE_TTL_HOURS",
                                 DEFAULT_LIVE_TTL_HOURS),
        )


# ── Dispatch guard (echoes workers/team/base.py — single source) ─────
def dispatch_guard_ok() -> Tuple[bool, List[str]]:
    """Returns `(ok, reasons)`. Fail-closed: when EITHER env var is
    missing or `TEAM_INGEST_ENABLED != '1'`, this returns False and
    the dry-run default below evaluates True.
    """
    reasons: List[str] = []
    if not os.environ.get(SGO_KEY_ENV):
        reasons.append(f"{SGO_KEY_ENV} env var is missing")
    if os.environ.get(ENABLE_FLAG_ENV, "0") != "1":
        reasons.append(f"{ENABLE_FLAG_ENV} is not set to '1'")
    return (len(reasons) == 0, reasons)


def dry_run_default() -> bool:
    """True ⇒ every worker pass should default to dry-run mode.

    Fail-closed by construction: when the dispatch guard is open
    AND the operator has explicitly set `TEAM_INGEST_LIVE=1`, real
    dispatch is permitted. Any other combination defaults to dry-run.
    """
    ok, _ = dispatch_guard_ok()
    if not ok:
        return True
    return os.environ.get("TEAM_INGEST_LIVE", "0") != "1"


# ── Pure helpers ─────────────────────────────────────────────────────
def next_backoff_seconds(
    attempt: int,
    policy: TeamIngestPolicy | None = None,
) -> float:
    """Exponential backoff capped at `backoff_cap_sec`.

    `attempt` is 1-indexed (first retry = 1). Schedule with defaults:
        attempt=1 → 0.5
        attempt=2 → 1.0
        attempt=3 → 2.0
        attempt=4 → 4.0
        attempt=5 → 8.0
        attempt=6 → 10.0 (capped)
    """
    if attempt < 1:
        return 0.0
    p = policy or TeamIngestPolicy()
    raw = p.backoff_base_sec * (p.backoff_factor ** (attempt - 1))
    return float(min(raw, p.backoff_cap_sec))


def should_abort_on_error_rate(
    n_errors: int,
    n_requests: int,
    policy: TeamIngestPolicy | None = None,
) -> Tuple[bool, str]:
    """Decide whether the current ingest pass should self-abort.

    Returns `(abort, reason)`. Requires the minimum sample threshold
    before an abort can fire — prevents single-error early aborts on
    cold-start runs.
    """
    p = policy or TeamIngestPolicy()
    if n_requests < p.abort_error_min_sample:
        return (False,
                f"sample below abort threshold "
                f"({n_requests} < {p.abort_error_min_sample})")
    rate = (n_errors / n_requests) if n_requests else 0.0
    if rate >= p.abort_error_rate_threshold:
        return (True,
                f"error rate {rate:.2%} ≥ "
                f"{p.abort_error_rate_threshold:.0%} threshold "
                f"({n_errors}/{n_requests})")
    return (False,
            f"error rate {rate:.2%} below threshold "
            f"({n_errors}/{n_requests})")


def should_abort_on_market_explosion(
    observed_markets: int,
    expected_markets: int,
    policy: TeamIngestPolicy | None = None,
) -> Tuple[bool, str]:
    """Fire when the worker sees an unexpected explosion of market
    names — typically a sign that SGO has added new market keys we
    don't have a mapping for. Stops the ingest before we silently
    fill `team_live_props` with un-graded rows.

    Returns `(abort, reason)`.
    """
    p = policy or TeamIngestPolicy()
    if expected_markets <= 0:
        return (False, "no expected baseline (skipped)")
    if observed_markets < p.abort_market_explosion_min:
        return (False,
                f"observed {observed_markets} < min explosion "
                f"sample {p.abort_market_explosion_min}")
    ratio = observed_markets / expected_markets
    if ratio >= p.abort_market_explosion_ratio:
        return (True,
                f"observed {observed_markets} markets vs expected "
                f"{expected_markets} (ratio {ratio:.2f}× ≥ "
                f"{p.abort_market_explosion_ratio:.2f}×) — "
                "likely new SGO market keys, abort")
    return (False,
            f"market ratio {ratio:.2f}× below "
            f"{p.abort_market_explosion_ratio:.2f}× threshold")


def is_book_blocked(book: str) -> bool:
    """Return True if `book` is on the hard-block list (e.g. Fliff)."""
    return (book or "").lower() in BLOCKED_BOOKS


def is_book_reference_only(book: str) -> bool:
    """Return True if `book` is reference-only in optimizer math
    (e.g. Underdog, PrizePicks).
    """
    return (book or "").lower() in REFERENCE_ONLY_BOOKS


def policy_summary(
    policy: TeamIngestPolicy | None = None,
) -> Dict[str, Any]:
    """Serializable snapshot for the admin status endpoint. Includes
    the effective env-derived policy plus the current dispatch +
    dry-run state.
    """
    p = policy or TeamIngestPolicy.from_env()
    ok, reasons = dispatch_guard_ok()
    return {
        "ok": True,
        "dispatch_guard": {
            "allowed": ok,
            "reasons": reasons,
            "env_flag":    p.enable_flag_env,
            "env_sgo_key": p.sgo_key_env,
        },
        "dry_run_default": dry_run_default(),
        "rate_limit": {
            "max_rpm_per_sport": dict(p.max_rpm_per_sport),
        },
        "retry": {
            "count":            p.retry_count,
            "backoff_base_sec": p.backoff_base_sec,
            "backoff_factor":   p.backoff_factor,
            "backoff_cap_sec":  p.backoff_cap_sec,
            "schedule_preview": [
                next_backoff_seconds(i, p)
                for i in range(1, p.retry_count + 2)
            ],
        },
        "retention": {
            "live_ttl_hours": p.live_ttl_hours,
        },
        "kill_switches": {
            "abort_error_rate_threshold":
                p.abort_error_rate_threshold,
            "abort_error_min_sample":
                p.abort_error_min_sample,
            "abort_market_explosion_ratio":
                p.abort_market_explosion_ratio,
            "abort_market_explosion_min":
                p.abort_market_explosion_min,
        },
        "book_policy": {
            "blocked_books":        sorted(p.blocked_books),
            "reference_only_books": sorted(p.reference_only_books),
        },
        "diff_vs_defaults": policy_diff(p),
    }


def policy_diff(
    policy: TeamIngestPolicy | None = None,
) -> Dict[str, Any]:
    """Pre-deploy safety check: return the fields where the effective
    policy differs from the locked defaults.

    Pure compare — never reads env, never writes anything. Pass
    `policy=TeamIngestPolicy.from_env()` (or omit; it's the default)
    to see what the current pod has overridden.

    Shape:
        {
          "is_default": bool,
          "overrides": {
            "<field>": {"default": ..., "effective": ...},
            ...
          },
        }
    """
    p = policy or TeamIngestPolicy.from_env()
    default_rpm = dict(DEFAULT_MAX_RPM_PER_SPORT)
    effective_rpm = dict(p.max_rpm_per_sport)
    overrides: Dict[str, Any] = {}

    if effective_rpm != default_rpm:
        overrides["max_rpm_per_sport"] = {
            "default":   default_rpm,
            "effective": effective_rpm,
        }
    if p.retry_count != DEFAULT_RETRY_COUNT:
        overrides["retry_count"] = {
            "default":   DEFAULT_RETRY_COUNT,
            "effective": p.retry_count,
        }
    if p.backoff_cap_sec != DEFAULT_BACKOFF_CAP_SEC:
        overrides["backoff_cap_sec"] = {
            "default":   DEFAULT_BACKOFF_CAP_SEC,
            "effective": p.backoff_cap_sec,
        }
    if p.live_ttl_hours != DEFAULT_LIVE_TTL_HOURS:
        overrides["live_ttl_hours"] = {
            "default":   DEFAULT_LIVE_TTL_HOURS,
            "effective": p.live_ttl_hours,
        }

    return {
        "is_default":    len(overrides) == 0,
        "overrides":     overrides,
        "n_overrides":   len(overrides),
    }

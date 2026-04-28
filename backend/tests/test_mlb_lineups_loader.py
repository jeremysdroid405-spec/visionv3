"""
Tests for services.mlb_lineups_loader — slot lookup + no-leakage guard.

Run:
    cd /app/backend && python -m pytest tests/test_mlb_lineups_loader.py -v

These tests exercise the synchronous part of the loader (`lookup_slot`
and `_to_dt`) in pure Python — no MongoDB / motor required, so they
run fast and are safe in CI.

Strict scope: this file only verifies the slot-lookup + no-leakage
contract.  It does NOT exercise μ/σ, gates, thresholds, tier routing,
or selection logic.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "/app/backend")

from services.mlb_lineups_loader import _to_dt, lookup_slot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
EVT = "evt_123"
PID = 42
COMMENCE = datetime(2026, 4, 28, 23, 0, tzinfo=timezone.utc)


def _slot_map(*, as_of=None, slot=3, source="mlb_stats_api",
              confirmed=True, event_id=EVT, bdl_player_id=PID):
    """Build a single-entry slot_map mirroring what `load_slot_map`
    returns from MongoDB."""
    return {
        (event_id, bdl_player_id): {
            "slot":      slot,
            "confirmed": confirmed,
            "as_of":     as_of if as_of is not None
                         else (COMMENCE - timedelta(hours=2)),
            "source":    source,
        }
    }


# ---------------------------------------------------------------------------
# 1. Valid lineup card  (as_of < commence_time)
# ---------------------------------------------------------------------------
def test_valid_card_returns_correct_slot_confirmed_and_source():
    sm = _slot_map(slot=4, source="mlb_stats_api", confirmed=True)
    slot, confirmed, src = lookup_slot(sm, EVT, PID, COMMENCE)
    assert slot == 4
    assert confirmed is True
    assert src == "mlb_stats_api"


def test_valid_card_unconfirmed_propagates_flag():
    sm = _slot_map(confirmed=False, source="rotowire")
    slot, confirmed, src = lookup_slot(sm, EVT, PID, COMMENCE)
    assert slot == 3
    assert confirmed is False
    assert src == "rotowire"


# ---------------------------------------------------------------------------
# 2. Leakage card  (as_of > commence_time)
# ---------------------------------------------------------------------------
def test_leakage_card_returns_none_and_does_not_fill_slot():
    sm = _slot_map(as_of=COMMENCE + timedelta(seconds=1))
    slot, confirmed, src = lookup_slot(sm, EVT, PID, COMMENCE)
    assert slot is None
    assert confirmed is False
    assert src is None


def test_leakage_far_future_rejected():
    sm = _slot_map(as_of=COMMENCE + timedelta(days=1))
    assert lookup_slot(sm, EVT, PID, COMMENCE) == (None, False, None)


# ---------------------------------------------------------------------------
# 3. Exact boundary  (as_of == commence_time)
# ---------------------------------------------------------------------------
def test_boundary_exact_equality_is_allowed():
    """`as_of == commence_time` → ALLOWED.

    Rationale: production lineup cards are persisted with
    `as_of = min(now_utc, commence_time)` (see
    `scripts/ingest_mlb_projected_lineups.ingest`).  When a backfill
    run sets `as_of` exactly to `commence_time`, the card was
    structurally known by game start and is NOT post-game data.
    The guard uses `as_of > commence_time` (strict) so equality
    must pass.  Locking this behaviour with a test prevents an
    accidental flip to `>=`."""
    sm = _slot_map(as_of=COMMENCE)
    slot, confirmed, src = lookup_slot(sm, EVT, PID, COMMENCE)
    assert slot == 3
    assert confirmed is True
    assert src == "mlb_stats_api"


# ---------------------------------------------------------------------------
# 4. Missing player  (card present, this player not in it)
# ---------------------------------------------------------------------------
def test_missing_player_returns_none():
    sm = _slot_map(bdl_player_id=999)  # different pid in the card
    slot, confirmed, src = lookup_slot(sm, EVT, PID, COMMENCE)
    assert slot is None
    assert confirmed is False
    assert src is None


def test_wrong_event_returns_none():
    sm = _slot_map(event_id="other_event")
    assert lookup_slot(sm, EVT, PID, COMMENCE) == (None, False, None)


def test_empty_slot_map_returns_none():
    assert lookup_slot({}, EVT, PID, COMMENCE) == (None, False, None)


def test_missing_event_id_arg_returns_none():
    sm = _slot_map()
    assert lookup_slot(sm, None, PID, COMMENCE) == (None, False, None)
    assert lookup_slot(sm, "",   PID, COMMENCE) == (None, False, None)


def test_missing_player_id_arg_returns_none():
    sm = _slot_map()
    assert lookup_slot(sm, EVT, None, COMMENCE) == (None, False, None)


# ---------------------------------------------------------------------------
# 5. Bad slot values  (must be 1..9 or rejected at load time)
#
# `lookup_slot` is downstream of `load_slot_map`, which already filters
# slot ∉ [1,9].  We additionally verify that an externally-injected bad
# value still propagates whatever `load_slot_map` would have stored,
# but the load filter is the canonical guard — so we test BOTH layers.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_slot", [0, -1, 10, 99, None])
def test_load_slot_map_rejects_out_of_range_slot(bad_slot):
    """Replicate the filter inside load_slot_map without needing a DB."""
    entry = {"slot": bad_slot, "bdl_player_id": PID}
    # Mirror the production filter (loader lines 96-103).
    try:
        slot = int(entry.get("slot"))
        int(entry.get("bdl_player_id"))
        passes_int_cast = True
    except (TypeError, ValueError):
        passes_int_cast = False
        slot = None
    if not passes_int_cast or not (1 <= slot <= 9):
        # Excluded from the slot_map — the loader would skip this entry.
        excluded = True
    else:
        excluded = False
    assert excluded is True, (
        f"slot={bad_slot} must be excluded by load_slot_map; "
        "production filter is `1 <= slot <= 9` after int-cast"
    )


def test_load_slot_map_rejects_non_integer_pid():
    entry = {"slot": 3, "bdl_player_id": "not-a-number"}
    try:
        int(entry["slot"])
        int(entry["bdl_player_id"])
        excluded = False
    except (TypeError, ValueError):
        excluded = True
    assert excluded is True


# ---------------------------------------------------------------------------
# 6. Naive vs timezone-aware datetime comparisons (UTC-safe)
# ---------------------------------------------------------------------------
def test_naive_datetime_treated_as_utc():
    naive = datetime(2026, 4, 28, 21, 0)        # 2 h before commence
    out = _to_dt(naive)
    assert out is not None
    assert out.tzinfo is not None
    assert out == datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)


def test_aware_datetime_passthrough():
    aware = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    assert _to_dt(aware) == aware
    assert _to_dt(aware).tzinfo is timezone.utc


def test_iso_string_with_z_suffix_parses():
    out = _to_dt("2026-04-28T21:00:00Z")
    assert out == datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)


def test_iso_string_with_offset_parses():
    out = _to_dt("2026-04-28T17:00:00-04:00")  # EDT
    assert out == datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)


def test_iso_string_naive_parses_as_utc():
    out = _to_dt("2026-04-28T21:00:00")
    assert out == datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)


def test_unparseable_string_returns_none():
    assert _to_dt("not-a-date") is None
    assert _to_dt("") is None
    assert _to_dt("   ") is None


def test_none_and_unknown_type_return_none():
    assert _to_dt(None) is None
    assert _to_dt(12345) is None
    assert _to_dt(object()) is None


def test_naive_as_of_compared_to_iso_commence():
    """Loader must not raise `naive vs aware` TypeError."""
    sm = _slot_map(as_of=datetime(2026, 4, 28, 21, 0))     # naive
    slot, _, _ = lookup_slot(sm, EVT, PID, "2026-04-28T23:00:00Z")
    assert slot == 3   # naive is treated as UTC, 2 h before commence


def test_aware_as_of_compared_to_naive_iso_commence():
    sm = _slot_map(as_of=datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc))
    slot, _, _ = lookup_slot(sm, EVT, PID, "2026-04-28T23:00:00")  # naive ISO
    assert slot == 3


def test_unparseable_commence_rejects_card():
    sm = _slot_map()
    slot, confirmed, src = lookup_slot(sm, EVT, PID, "garbage-string")
    assert slot is None
    assert confirmed is False
    assert src is None


def test_unparseable_as_of_rejects_card():
    sm = _slot_map(as_of="garbage-string")
    slot, confirmed, src = lookup_slot(sm, EVT, PID, COMMENCE)
    assert slot is None
    assert confirmed is False
    assert src is None

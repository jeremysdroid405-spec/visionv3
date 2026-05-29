"""
Phase 1.A.3.5b — unit tests for the shared SGO event-field helpers.

Covers the canonical lookup chain for event start timestamp:

    1. event.status.startsAt   (primary)
    2. event.startTime          (fallback)
    3. event.commenceTime       (fallback)
    4. event.commence_time      (fallback)
    5. event.startsAt           (tertiary — synthetic shape)
    6. (none present) → ""

Also covers `derive_game_date` parse paths.
"""
from __future__ import annotations

import pytest

from services.team_master_hub.sgo_event_helpers import (
    derive_game_date,
    extract_event_start_iso,
)


# ── extract_event_start_iso ─────────────────────────────────────────
class TestExtractEventStartIso:
    def test_primary_status_startsAt(self) -> None:
        ev = {"status": {"startsAt": "2026-05-29T22:00:00Z"}}
        assert extract_event_start_iso(ev) == "2026-05-29T22:00:00Z"

    def test_falls_through_when_status_block_present_but_empty(self) -> None:
        ev = {"status": {}, "startTime": "2026-06-02T18:05:00Z"}
        assert extract_event_start_iso(ev) == "2026-06-02T18:05:00Z"

    def test_falls_through_when_status_startsAt_is_empty_string(self) -> None:
        ev = {"status": {"startsAt": ""},
              "commenceTime": "2026-06-02T18:05:00Z"}
        assert extract_event_start_iso(ev) == "2026-06-02T18:05:00Z"

    def test_fallback_startTime(self) -> None:
        ev = {"startTime": "2026-06-02T18:05:00Z"}
        assert extract_event_start_iso(ev) == "2026-06-02T18:05:00Z"

    def test_fallback_commenceTime_camelCase(self) -> None:
        ev = {"commenceTime": "2026-06-02T18:05:00Z"}
        assert extract_event_start_iso(ev) == "2026-06-02T18:05:00Z"

    def test_fallback_commence_time_snake_case(self) -> None:
        ev = {"commence_time": "2026-06-02T18:05:00Z"}
        assert extract_event_start_iso(ev) == "2026-06-02T18:05:00Z"

    def test_tertiary_top_level_startsAt(self) -> None:
        ev = {"startsAt": "2026-06-02T18:05:00Z"}
        assert extract_event_start_iso(ev) == "2026-06-02T18:05:00Z"

    def test_priority_status_wins_over_top_level(self) -> None:
        ev = {
            "status": {"startsAt": "2026-05-29T22:00:00Z"},
            "startTime":     "2099-01-01T00:00:00Z",
            "commenceTime":  "2099-01-01T00:00:00Z",
            "commence_time": "2099-01-01T00:00:00Z",
            "startsAt":      "2099-01-01T00:00:00Z",
        }
        # status.startsAt must beat all the fallbacks
        assert extract_event_start_iso(ev) == "2026-05-29T22:00:00Z"

    def test_priority_startTime_beats_commenceTime(self) -> None:
        ev = {
            "startTime":    "2026-05-29T22:00:00Z",
            "commenceTime": "2099-01-01T00:00:00Z",
        }
        assert extract_event_start_iso(ev) == "2026-05-29T22:00:00Z"

    def test_priority_commenceTime_beats_commence_time(self) -> None:
        ev = {
            "commenceTime":  "2026-05-29T22:00:00Z",
            "commence_time": "2099-01-01T00:00:00Z",
        }
        assert extract_event_start_iso(ev) == "2026-05-29T22:00:00Z"

    def test_missing_everywhere_returns_empty_string(self) -> None:
        ev = {"eventID": "evt_xyz", "teams": {}}
        assert extract_event_start_iso(ev) == ""

    def test_empty_dict_returns_empty_string(self) -> None:
        assert extract_event_start_iso({}) == ""

    def test_non_dict_input_returns_empty_string(self) -> None:
        # Defensive — pure function shouldn't raise on garbage input
        for bad in (None, "string", 42, [1, 2, 3]):
            assert extract_event_start_iso(bad) == ""  # type: ignore[arg-type]

    def test_status_non_dict_falls_through(self) -> None:
        ev = {"status": "open", "startTime": "2026-05-29T22:00:00Z"}
        assert extract_event_start_iso(ev) == "2026-05-29T22:00:00Z"

    def test_non_string_values_are_ignored(self) -> None:
        ev = {
            "status":       {"startsAt": 1717363200},  # int — ignore
            "startTime":    None,
            "commenceTime": {"nested": "x"},
            "commence_time": "2026-05-29T22:00:00Z",
        }
        assert extract_event_start_iso(ev) == "2026-05-29T22:00:00Z"


# ── derive_game_date ────────────────────────────────────────────────
class TestDeriveGameDate:
    def test_iso_with_Z_suffix(self) -> None:
        assert derive_game_date("2026-05-29T22:00:00Z") == "2026-05-29"

    def test_iso_with_explicit_offset(self) -> None:
        assert derive_game_date("2026-05-29T22:00:00+00:00") == "2026-05-29"

    def test_iso_converts_other_timezones_to_utc(self) -> None:
        # 02:00 +03:00 == 23:00 UTC the previous day
        assert derive_game_date("2026-05-29T02:00:00+03:00") == "2026-05-28"

    def test_empty_string_returns_none(self) -> None:
        assert derive_game_date("") is None

    def test_none_returns_none(self) -> None:
        assert derive_game_date(None) is None  # type: ignore[arg-type]

    def test_garbage_returns_none(self) -> None:
        assert derive_game_date("not-a-date") is None

    def test_non_string_returns_none(self) -> None:
        for bad in (42, 3.14, [1, 2, 3], {"x": "y"}, True):
            assert derive_game_date(bad) is None  # type: ignore[arg-type]


# ── Integration: real SGO v2 shape from durxysyG9m2bDAPWTSv7 ────────
class TestExtractFromRealSgoShape:
    def test_real_sgo_v2_shape(self) -> None:
        """Sanity check on the shape the live MLB fetch returned —
        ensures we'd correctly resolve the date if SGO nests it
        under `status.startsAt` (the canonical location).
        """
        ev = {
            "eventID":  "durxysyG9m2bDAPWTSv7",
            "status":   {"startsAt": "2026-05-29T22:00:00Z"},
            "teams":    {"home": {"names": {"long": "Seattle Mariners"}},
                         "away": {"names": {"long": "Cleveland Guardians"}}},
            "odds":     {},
        }
        assert extract_event_start_iso(ev) == "2026-05-29T22:00:00Z"
        assert derive_game_date(
            extract_event_start_iso(ev)) == "2026-05-29"

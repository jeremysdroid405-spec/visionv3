"""
Phase 1.A.4a — unit tests for the team_events normalizer + status
classifier. Pure tests, no DB / no HTTP.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.team_master_hub.team_events import (
    classify_status,
    normalize_event_to_matchup,
)


# ── classify_status ─────────────────────────────────────────────────
class TestClassifyStatus:
    def test_default_scheduled(self) -> None:
        assert classify_status({}) == "scheduled"
        assert classify_status({"started": False, "completed": False}) == \
            "scheduled"

    def test_cancelled_wins(self) -> None:
        # cancelled beats every other flag
        assert classify_status({
            "cancelled": True, "completed": True, "live": True,
            "started":   True, "delayed":   True,
        }) == "cancelled"

    def test_postponed_via_delayed(self) -> None:
        assert classify_status({"delayed": True}) == "postponed"

    def test_postponed_via_explicit(self) -> None:
        assert classify_status({"postponed": True}) == "postponed"

    def test_completed_via_completed_flag(self) -> None:
        assert classify_status({"completed": True}) == "completed"

    def test_completed_via_ended_flag(self) -> None:
        assert classify_status({"ended": True}) == "completed"

    def test_live_via_live_flag(self) -> None:
        assert classify_status({"live": True}) == "live"

    def test_live_via_started_only(self) -> None:
        # started=True but live/completed both False → still live
        assert classify_status({"started": True}) == "live"

    def test_priority_completed_beats_live(self) -> None:
        assert classify_status({"completed": True, "live": True}) == \
            "completed"

    def test_priority_live_beats_started(self) -> None:
        assert classify_status({"live": True, "started": True}) == "live"

    def test_non_dict_input(self) -> None:
        assert classify_status(None) == "scheduled"
        assert classify_status("string") == "scheduled"
        assert classify_status(42) == "scheduled"

    def test_none_values_treated_as_false(self) -> None:
        assert classify_status({"completed": None, "live": None}) == \
            "scheduled"


# ── normalize_event_to_matchup ──────────────────────────────────────
@pytest.fixture
def fetched_at() -> datetime:
    return datetime(2026, 2, 18, 5, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def team_lookup() -> dict:
    # Mimics what build_team_id_lookup returns for MLB
    return {
        "Seattle Mariners":   "mlb_sea",
        "Mariners":           "mlb_sea",
        "SEA":                "mlb_sea",
        "Cleveland Guardians": "mlb_cle",
        "Guardians":          "mlb_cle",
        "CLE":                "mlb_cle",
    }


def _real_sgo_event() -> dict:
    """Minimal real SGO v2 shape."""
    return {
        "eventID":  "durxysyG9m2bDAPWTSv7",
        "status":   {"startsAt":  "2025-06-15T01:40:00.000Z",
                      "started":   True,
                      "completed": True,
                      "live":      False},
        "teams": {
            "home": {"names": {"long":   "Seattle Mariners",
                                 "short":  "Mariners",
                                 "abbrev": "SEA"}},
            "away": {"names": {"long":   "Cleveland Guardians",
                                 "short":  "Guardians",
                                 "abbrev": "CLE"}},
        },
        "venue":    {"name": "T-Mobile Park"},
    }


class TestNormalize:
    def test_real_sgo_shape(self, fetched_at, team_lookup) -> None:
        row = normalize_event_to_matchup(
            _real_sgo_event(), sport="mlb",
            team_id_lookup=team_lookup, fetched_at=fetched_at,
            source_endpoint="https://api.sgo/v2/events?…",
        )
        assert row is not None
        assert row["event_id"]       == "durxysyG9m2bDAPWTSv7"
        assert row["sport"]          == "mlb"
        assert row["league"]         == "MLB"
        assert row["home_team_id"]   == "mlb_sea"
        assert row["away_team_id"]   == "mlb_cle"
        assert row["home_team_name"] == "Seattle Mariners"
        assert row["away_team_name"] == "Cleveland Guardians"
        assert row["commence_time"]  == "2025-06-15T01:40:00.000Z"
        assert row["game_date"]      == "2025-06-15"
        assert row["venue"]          == "T-Mobile Park"
        assert row["status"]         == "completed"
        assert row["status_raw"]["completed"] is True
        assert row["source"]         == "sgo"
        assert row["source_endpoint"] == "https://api.sgo/v2/events?…"
        assert row["fetched_at"]     == fetched_at
        assert row["updated_at"]     == fetched_at
        assert row["unresolved_teams"] is None

    def test_missing_event_id_returns_none(self, fetched_at,
                                              team_lookup) -> None:
        assert normalize_event_to_matchup(
            {"teams": {}}, sport="mlb",
            team_id_lookup=team_lookup, fetched_at=fetched_at) is None

    def test_lenient_unresolved_home(self, fetched_at, team_lookup) -> None:
        ev = _real_sgo_event()
        ev["teams"]["home"]["names"]["long"] = "Sasquatch FC"
        ev["teams"]["home"]["names"]["short"] = "Sasquatch"
        ev["teams"]["home"]["names"]["abbrev"] = "SQT"
        row = normalize_event_to_matchup(
            ev, sport="mlb",
            team_id_lookup=team_lookup, fetched_at=fetched_at)
        assert row is not None
        assert row["home_team_id"]   is None
        assert row["away_team_id"]   == "mlb_cle"
        assert row["home_team_name"] == "Sasquatch FC"
        assert row["unresolved_teams"] == ["Sasquatch FC"]

    def test_lenient_unresolved_both(self, fetched_at) -> None:
        ev = _real_sgo_event()
        row = normalize_event_to_matchup(
            ev, sport="mlb", team_id_lookup={}, fetched_at=fetched_at)
        assert row is not None
        assert row["home_team_id"] is None
        assert row["away_team_id"] is None
        # both names surfaced for the operator
        assert set(row["unresolved_teams"]) == {
            "Seattle Mariners", "Cleveland Guardians"}

    def test_status_priority_completed(self, fetched_at,
                                          team_lookup) -> None:
        ev = _real_sgo_event()
        ev["status"] = {"started": True, "completed": True, "live": True}
        row = normalize_event_to_matchup(
            ev, sport="mlb",
            team_id_lookup=team_lookup, fetched_at=fetched_at)
        assert row["status"] == "completed"

    def test_scheduled_with_status_dict_absent(self, fetched_at,
                                                  team_lookup) -> None:
        ev = _real_sgo_event()
        ev.pop("status")
        ev["startsAt"] = "2026-06-02T22:00:00Z"
        row = normalize_event_to_matchup(
            ev, sport="mlb",
            team_id_lookup=team_lookup, fetched_at=fetched_at)
        assert row["status"]        == "scheduled"
        assert row["commence_time"] == "2026-06-02T22:00:00Z"
        assert row["game_date"]     == "2026-06-02"
        assert row["status_raw"]    is None

    def test_venue_string_form(self, fetched_at, team_lookup) -> None:
        ev = _real_sgo_event()
        ev["venue"] = "Wrigley Field"
        row = normalize_event_to_matchup(
            ev, sport="mlb",
            team_id_lookup=team_lookup, fetched_at=fetched_at)
        assert row["venue"] == "Wrigley Field"

    def test_venue_missing(self, fetched_at, team_lookup) -> None:
        ev = _real_sgo_event()
        ev.pop("venue", None)
        row = normalize_event_to_matchup(
            ev, sport="mlb",
            team_id_lookup=team_lookup, fetched_at=fetched_at)
        assert row["venue"] is None

    def test_synthetic_short_name_resolves(self, fetched_at,
                                              team_lookup) -> None:
        ev = _real_sgo_event()
        # Only abbrev is present; lookup still hits via "SEA" variant
        ev["teams"]["home"]["names"] = {"abbrev": "SEA"}
        row = normalize_event_to_matchup(
            ev, sport="mlb",
            team_id_lookup=team_lookup, fetched_at=fetched_at)
        assert row["home_team_id"] == "mlb_sea"

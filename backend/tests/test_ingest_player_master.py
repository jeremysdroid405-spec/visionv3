"""
Unit tests for scripts/sgo/ingest_player_master.py — normalize_player_doc().

Pure-function tests. Validate:
  • required fields (player_id) drop docs without it
  • aliases / names lists handled across SGO's polymorphic shapes
  • canonical-name fallback chain (display → first+last → first name)
  • all identity fields preserved on output
  • raw payload retained for forensics
  • ingest_version stamp written
"""
from __future__ import annotations
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")

from scripts.sgo.ingest_player_master import (
    INGEST_VERSION, _as_list, _get, normalize_player_doc,
)


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ─── helpers ───
def test_get_returns_first_non_none():
    d = {"a": None, "b": "x", "c": "y"}
    assert _get(d, "a", "b", "c") == "x"
    assert _get(d, "missing") is None


def test_as_list_polymorphic():
    assert _as_list(None) == []
    assert _as_list("") == []
    assert _as_list("Caleb") == ["Caleb"]
    assert _as_list(["a", "b"]) == ["a", "b"]
    assert _as_list(["a", None, "b", ""]) == ["a", "b"]


# ─── normalize_player_doc ───
def test_normalize_drops_doc_without_pid():
    assert normalize_player_doc({"name": "X"}, league_id="NCAAF",
                                  sport_id="FOOTBALL", now=_NOW) is None


def test_normalize_preserves_canonical_pid_and_league():
    d = normalize_player_doc(
        {"playerID": "CALEB_WILLIAMS_1_NCAAF",
         "playerName": "Caleb Williams"},
        league_id="NCAAF", sport_id="FOOTBALL", now=_NOW)
    assert d["player_id"] == "CALEB_WILLIAMS_1_NCAAF"
    assert d["league_id"] == "NCAAF"
    assert d["sport_id"] == "FOOTBALL"
    assert d["ingest_version"] == INGEST_VERSION
    assert d["ingested_at"] == _NOW


def test_normalize_aliases_array_preserved():
    raw = {
        "playerID": "X_1_NCAAF",
        "aliases":  ["X.Y. Smith", "XY Smith", "smithy"],
    }
    d = normalize_player_doc(raw, league_id="NCAAF",
                              sport_id="FOOTBALL", now=_NOW)
    assert d["aliases"] == ["X.Y. Smith", "XY Smith", "smithy"]


def test_normalize_names_includes_display_first():
    raw = {
        "playerID":   "X_1_NCAAF",
        "playerName": "Calvin Smith",
        "names":      ["Cal Smith", "C. Smith"],
    }
    d = normalize_player_doc(raw, league_id="NCAAF",
                              sport_id="FOOTBALL", now=_NOW)
    # Display name is first in the names list
    assert d["names"][0] == "Calvin Smith"
    assert "Cal Smith" in d["names"]
    assert "C. Smith" in d["names"]
    assert d["player_name"] == "Calvin Smith"


def test_normalize_falls_back_to_first_last():
    raw = {"playerID": "X_1_NCAAF",
            "firstName": "Bijan", "lastName": "Robinson"}
    d = normalize_player_doc(raw, league_id="NCAAF",
                              sport_id="FOOTBALL", now=_NOW)
    assert d["first_name"] == "Bijan"
    assert d["last_name"] == "Robinson"
    assert d["player_name"] == "Bijan Robinson"


def test_normalize_falls_back_to_names_array():
    raw = {"playerID": "X_1_NCAAF",
            "names": ["Quinn Ewers", "Q. Ewers"]}
    d = normalize_player_doc(raw, league_id="NCAAF",
                              sport_id="FOOTBALL", now=_NOW)
    # No display, no first/last → first item of names[]
    assert d["player_name"] == "Quinn Ewers"


def test_normalize_preserves_full_identity_fields():
    raw = {
        "playerID":    "C_W_1_NCAAF",
        "playerName":  "Caleb Williams",
        "teamID":      "USC",
        "position":    "QB",
        "jerseyNumber": 13,
        "height":      "73",
        "weight":      215,
        "status":      "active",
        "birthDate":   "2001-11-18",
        "teamHistory": [{"team_id": "USC", "season": 2024}],
    }
    d = normalize_player_doc(raw, league_id="NCAAF",
                              sport_id="FOOTBALL", now=_NOW)
    assert d["team_id"] == "USC"
    assert d["position"] == "QB"
    assert d["jersey_number"] == "13"   # coerced to str
    assert d["height"] == "73"
    assert d["weight"] == 215
    assert d["status"] == "active"
    assert d["birth_date"] == "2001-11-18"
    assert d["team_history"] == [{"team_id": "USC", "season": 2024}]


def test_normalize_retains_raw_for_forensics():
    raw = {"playerID": "X_1_NCAAF", "extra_sgo_field": "preserved"}
    d = normalize_player_doc(raw, league_id="NCAAF",
                              sport_id="FOOTBALL", now=_NOW)
    assert d["raw"] == raw
    assert d["raw"]["extra_sgo_field"] == "preserved"


def test_normalize_jersey_none_stays_none():
    raw = {"playerID": "X_1_NCAAF"}
    d = normalize_player_doc(raw, league_id="NCAAF",
                              sport_id="FOOTBALL", now=_NOW)
    assert d["jersey_number"] is None


def test_normalize_handles_snake_case_keys():
    raw = {"player_id":     "X_1_NCAAF",
            "first_name":    "John",
            "last_name":     "Smith",
            "team_id":       "OSU",
            "jersey_number": 7}
    d = normalize_player_doc(raw, league_id="NCAAF",
                              sport_id="FOOTBALL", now=_NOW)
    assert d["player_id"] == "X_1_NCAAF"
    assert d["first_name"] == "John"
    assert d["last_name"] == "Smith"
    assert d["team_id"] == "OSU"
    assert d["jersey_number"] == "7"

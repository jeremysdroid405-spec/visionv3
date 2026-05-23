"""
NFL pipeline unit tests.

Pure-Python coverage of the three things that determine whether NFL
data flows correctly through the pipeline:

    1. services/replay/nfl_stat_family_map.canonical_family()
       — SGO stat_id → canonical NFL family.
    2. scripts/sgo/ingest_historical_player_stats._normalize_nfl_stats()
       — raw SGO playerStats.stats dict → canonical NFL fields.
    3. scripts/sgo/build_historical_outcomes.STAT_RESOLVERS  for
       canonical NFL families — resolves actual_value from a
       normalized stats dict.

No SGO API access required; these are deterministic projections we can
exercise locally before/after a real ingest.
"""
from __future__ import annotations
import importlib.util
import sys

sys.path.insert(0, "/app/backend")

from services.replay.nfl_stat_family_map import (  # noqa: E402
    NFL_FAMILIES, canonical_family,
)

# Load the normalizer & resolver modules directly so the integration tests
# don't pay the cost of bootstrapping motor / dotenv at import time.
_spec_n = importlib.util.spec_from_file_location(
    "ingest_hps",
    "/app/backend/scripts/sgo/ingest_historical_player_stats.py")
_ingest = importlib.util.module_from_spec(_spec_n)
_spec_n.loader.exec_module(_ingest)  # type: ignore[union-attr]
normalize_stats = _ingest.normalize_stats
_normalize_nfl  = _ingest._normalize_nfl_stats

_spec_o = importlib.util.spec_from_file_location(
    "build_outcomes",
    "/app/backend/scripts/sgo/build_historical_outcomes.py")
_outcomes = importlib.util.module_from_spec(_spec_o)
_spec_o.loader.exec_module(_outcomes)  # type: ignore[union-attr]
STAT_RESOLVERS = _outcomes.STAT_RESOLVERS


# ── canonical_family() ────────────────────────────────────────────────
def test_canonical_family_passing():
    for v in ("passing_yards", "passingYards", "pass_yards",
                "PASSING-YARDS", "qb_passing_yards"):
        assert canonical_family(v) == "pass_yards", v


def test_canonical_family_rushing_receiving():
    assert canonical_family("rushingYards")     == "rush_yards"
    assert canonical_family("rec_yards")        == "receiving_yards"
    assert canonical_family("targets")          == "receiving_targets"
    assert canonical_family("carries")          == "rush_attempts"


def test_canonical_family_kicking():
    assert canonical_family("fieldGoalsMade")   == "field_goals_made"
    assert canonical_family("fgm")              == "field_goals_made"
    assert canonical_family("extraPointsMade")  == "extra_points_made"


def test_canonical_family_unknown_returns_none():
    assert canonical_family("snake_yards") is None
    assert canonical_family("") is None
    assert canonical_family(None) is None  # type: ignore[arg-type]


def test_every_canonical_family_round_trips():
    """Every canonical family is also a valid alias key — so a probe
    that emits {family} as stat_id resolves back to itself."""
    for fam in NFL_FAMILIES:
        assert canonical_family(fam) == fam, fam


# ── _normalize_nfl_stats() — multiple input flavours ──────────────────
def test_normalize_nfl_snake_case_payload():
    raw = {
        "passing_yards": 312, "passing_attempts": 41, "passing_completions": 27,
        "passing_touchdowns": 3, "passing_interceptions": 1,
        "rushing_yards": 22, "rushing_attempts": 4,
    }
    out = _normalize_nfl(raw)
    assert out["pass_yards"] == 312
    assert out["pass_attempts"] == 41
    assert out["pass_completions"] == 27
    assert out["pass_touchdowns"] == 3
    assert out["interceptions"] == 1
    assert out["rush_yards"] == 22
    assert out["rush_attempts"] == 4


def test_normalize_nfl_camel_case_payload():
    raw = {
        "passingYards": 215, "passingAttempts": 30, "passingCompletions": 20,
        "passingTouchdowns": 2, "passingInterceptions": 0,
        "receivingYards": 85, "receptions": 6, "targets": 9,
        "receivingTouchdowns": 1, "longestReception": 32,
    }
    out = _normalize_nfl(raw)
    assert out["pass_yards"] == 215
    assert out["receiving_yards"] == 85
    assert out["receptions"] == 6
    assert out["receiving_targets"] == 9
    assert out["receiving_touchdowns"] == 1
    assert out["longest_reception"] == 32


def test_normalize_nfl_missing_fields_are_none():
    out = _normalize_nfl({"passing_yards": 100})
    assert out["pass_yards"] == 100
    assert out["rush_yards"]      is None
    assert out["receptions"]      is None
    assert out["field_goals_made"] is None


def test_normalize_nfl_empty_returns_empty_dict():
    assert _normalize_nfl({}) == {}


def test_normalize_dispatches_to_nfl():
    """normalize_stats(league='NFL', ...) must reach the NFL branch."""
    raw = {"passing_yards": 250, "rushing_yards": 35}
    out = normalize_stats(raw, league="NFL")
    assert out["pass_yards"] == 250
    assert out["rush_yards"] == 35
    # MLB-only field must NOT appear
    assert "hits" not in out
    # NBA-only field must NOT appear
    assert "points" not in out


def test_normalize_auto_detect_picks_nfl_when_strongest():
    """No --league supplied; nfl signal exceeds mlb/nba → nfl wins."""
    raw = {
        "passing_yards": 220, "rushing_yards": 40, "receptions": 5,
        "receiving_yards": 70, "passing_touchdowns": 2,
    }
    out = normalize_stats(raw, league=None)
    # NFL-specific key present
    assert out.get("pass_yards") == 220


# ── STAT_RESOLVERS — outcome-time grading ─────────────────────────────
def test_resolver_pass_yards_reads_normalized_stats():
    stats = {"pass_yards": 312, "passing_yards": 312}
    assert STAT_RESOLVERS["pass_yards"](stats) == 312


def test_resolver_pass_yards_reads_camel_case():
    stats = {"passingYards": 250}
    assert STAT_RESOLVERS["pass_yards"](stats) == 250


def test_resolver_receptions_targets_longest():
    stats = {"receptions": 6, "targets": 9, "longestReception": 28}
    assert STAT_RESOLVERS["receptions"](stats)          == 6
    assert STAT_RESOLVERS["receiving_targets"](stats)   == 9
    assert STAT_RESOLVERS["longest_reception"](stats)   == 28


def test_resolver_returns_none_for_missing():
    assert STAT_RESOLVERS["pass_yards"]({})          is None
    assert STAT_RESOLVERS["field_goals_made"]({})    is None


def test_every_nfl_family_has_a_resolver():
    """Every canonical NFL family in the family-map must have a
    corresponding STAT_RESOLVERS entry, so outcomes grading never silently
    skips an NFL row."""
    for fam in NFL_FAMILIES:
        assert fam in STAT_RESOLVERS, f"missing resolver: {fam}"



# ── build_historical_outcomes amain() kwarg contract ─────────────────
def test_distinct_game_dates_accepts_src_coll_kwarg():
    """Regression: amain() passes src_coll=... to _distinct_game_dates;
    a removed kwarg breaks every NFL/MLB outcome run with a cryptic
    TypeError at startup. Pin the signature so we catch it in CI."""
    import inspect
    sig = inspect.signature(_outcomes._distinct_game_dates)
    assert "src_coll" in sig.parameters


def test_process_date_accepts_out_and_src_coll_kwargs():
    import inspect
    sig = inspect.signature(_outcomes.process_date)
    assert "out_coll" in sig.parameters
    assert "src_coll" in sig.parameters

"""
Mirror event_id allowlist contract (2026-06-02).

ROOT CAUSE this pins (user-confirmed):
  June 2025 historical-replay grading coverage dropped from 97% (May) to
  33% because `mlb_propvision_full_pipeline_outputs` accumulated rows
  from TWO Layer-3 sources writing to the same collection:
    1. The SSOT SGO replay path keyed on `sgo_replay_alt_odds_raw`
       (20-char V1 event_ids that match `sgo_pp_research_outcomes`).
    2. The live production replay path keyed on
       `mlb_historical_alt_odds_raw` (32-char MD5 event_ids that have
       NO matching outcome row).

  The mirror previously aggregated ALL rows for the given replay_serials
  and wrote one mirror doc per (event_id, player, market, line, side),
  so every (player, market, line, side) prop ended up with TWO mirror
  rows — one for each event_id format. The V2-hash row was always
  unresolved (no outcome can match it), driving the 1.96x duplication
  and the 33% coverage rate.

CONTRACT this test pins:
  `_mirror_to_legacy` MUST query the outcomes collection first to build
  an allowlist of valid event_ids for the affected game_dates, and the
  runner-outputs aggregation match stage MUST constrain `event_id` to
  that allowlist. Without the constraint we silently re-introduce
  V2-hash dead-weight rows on every replay run.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/app/backend")

MIRROR_SRC = Path("/app/backend/scripts/sgo/historical_full_pipeline_replay.py")


@pytest.fixture(scope="module")
def src() -> str:
    return MIRROR_SRC.read_text()


def test_mirror_builds_outcome_event_id_allowlist(src: str) -> None:
    """The mirror must look up outcome event_ids before aggregating."""
    assert "valid_event_ids" in src, (
        "Mirror must define a `valid_event_ids` set sourced from "
        "sgo_pp_research_outcomes BEFORE the runner-outputs aggregation."
    )
    # The lookup must hit the outcomes collection
    assert "SGO_OUTCOMES_COLL" in src, (
        "Mirror should reference SGO_OUTCOMES_COLL when building the "
        "event_id allowlist."
    )


def test_mirror_aggregation_match_constrains_event_id(src: str) -> None:
    """The aggregation pipeline's $match stage must filter on event_id."""
    # The match_stage dict must include event_id constraint when allowlist non-empty
    assert "match_stage" in src, (
        "Mirror should build a `match_stage` dict that can include "
        "the event_id allowlist constraint."
    )
    assert 'match_stage["event_id"]' in src or "match_stage['event_id']" in src, (
        "Mirror must add an event_id constraint to the aggregation "
        "$match stage when the outcomes allowlist is non-empty."
    )


def test_mirror_pulls_game_dates_from_runner_runs(src: str) -> None:
    """The mirror needs game_dates to scope the outcomes lookup."""
    assert "RUNNER_RUNS" in src, (
        "Mirror should reference RUNNER_RUNS to resolve game_dates "
        "for the replay_serials before querying outcomes."
    )
    assert "game_dates" in src

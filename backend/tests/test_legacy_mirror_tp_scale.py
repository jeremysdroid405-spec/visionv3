"""
Scale-conversion contract for the legacy mirror (2026-05-27).

ROOT CAUSE this pins:
  After landing the SSOT-faithful mirror, the Top-25 results showed
  Δcal values of -2308% — mathematically impossible if `tp` is a
  probability in [0, 1]. Diagnosis: the production_replay_runner
  stores `tp` as a PERCENT in [0, 100] (replay_metrics_builder.py:88
  multiplies fair_probability by 100). My mirror was passing that
  percent-scale value straight through to the optimizer, which
  expects `tp` in PROBABILITY scale (0..1, matching model_probability
  and fair_probability).

  Result: avg_tp came out as ~23 (i.e. 23.5 in percent), Δcal = HR -
  avg_tp = 0.43 - 23.5 = -23.07, displayed as -2307%. Nonsense.

CONTRACT:
  The mirror MUST divide the runner's `tp` (percent-scale, 0..100) by
  100 before storing in the legacy collection. The downstream
  optimizer contract requires `tp` to be a PROBABILITY (0..1), same
  scale as `model_probability` and `fair_probability`.
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest


MIRROR = Path("/app/backend/scripts/sgo/historical_full_pipeline_replay.py")
RUNNER_BUILDER = Path("/app/backend/services/replay/replay_metrics_builder.py")


def test_runner_stores_tp_in_percent_scale():
    """First sanity check: confirm the production_replay_runner's
    metrics builder stores `tp` in PERCENT scale (× 100). If this ever
    changes (e.g. someone unifies to decimal scale), this test fails
    and we know to remove the /100 in the mirror."""
    src = RUNNER_BUILDER.read_text()
    # The exact pattern from line 88 of replay_metrics_builder.py:
    #   tp = round(float(fair_p) * 100.0, 4) if fair_p is not None else None
    has_scale_conversion = re.search(
        r"tp\s*=.*float\(fair_p\)\s*\*\s*100\.0", src)
    assert has_scale_conversion, (
        "replay_metrics_builder.py no longer stores `tp` as percent "
        "(× 100). If this was an intentional change, REMOVE the /100 "
        "in _mirror_to_legacy's `tp` field assignment. The scales "
        "must match between writer and reader.")


def test_mirror_converts_tp_back_to_probability_scale():
    """The mirror must divide the runner's percent-scale `tp` by 100
    before storing in the legacy collection. The optimizer downstream
    expects `tp` in [0, 1]."""
    src = MIRROR.read_text()
    # Look for /100 (or / 100) applied to `tp_runner` somewhere in
    # the mirror. Allow whitespace variations.
    has_conversion = (
        "tp_runner") in src and re.search(
        r"tp_runner.*?/\s*100", src)
    assert has_conversion, (
        "_mirror_to_legacy must convert the runner's percent-scale "
        "`tp` back to probability scale by dividing by 100 before "
        "writing to the legacy collection. Without this, the "
        "optimizer downstream sees tp in [0, 100] and produces "
        "nonsensical calibration_delta values (e.g. -2308%).")


def test_mirror_does_not_overwrite_tp_with_model_probability():
    """The original bug we fixed earlier — pin it: tp must come from
    `tp_runner`, NOT from `model_probability`."""
    src = MIRROR.read_text()
    bad_patterns = [
        '"tp": g.get("model_probability")',
        '"tp":  g.get("model_probability")',
    ]
    for pat in bad_patterns:
        assert pat not in src, (
            f"_mirror_to_legacy is overwriting `tp` with "
            f"`model_probability` again, deleting the runner's "
            f"multi-book devigged TP. Pattern found: {pat!r}")

"""Static audit of the cross-sport replay infrastructure contract.

Read SSOT policy:  `services/replay/contract.py`

Two invariants enforced:

  1. ELIGIBILITY BYPASS — every replay engine must NOT pre-filter
     rows by today's production eligibility chain
     (`apply_production_eligibility`). It must either:
       (a) call `recompute_sport(**REPLAY_RECOMPUTE_KWARGS)` —
           which includes `bypass_eligibility=True`, or
       (b) have its own scoring path that does NOT invoke
           `apply_production_eligibility` / `filter_priceable` /
           `filter_pp_playable` at any point.

  2. COMPLIANCE DECLARATION — every replay engine module listed in
     `services.replay.contract.COMPLIANT_REPLAY_ENGINES` must
     declare `REPLAY_CONTRACT_COMPLIANT = True` at module scope.
     This is a hard tripwire — accidentally removing the flag (or
     adding a new sport without setting it) fails this test
     before the engine can ship.

This is a pure static-analysis test — runs in milliseconds, no DB,
no mocking. Suitable to run on every commit / in CI.
"""
from __future__ import annotations
import importlib
import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, "/app/backend")

from services.replay.contract import (
    COMPLIANT_REPLAY_ENGINES,
    REPLAY_RECOMPUTE_KWARGS,
    PIPELINE_REGISTRY,
    PIPELINE_MODES,
)


# Patterns that signal a replay engine is applying production
# eligibility filters in violation of the contract.
_VIOLATION_PATTERNS = (
    re.compile(r"\bapply_production_eligibility\s*\("),
    re.compile(r"\bfilter_priceable\s*\("),
    re.compile(r"\bfilter_pp_playable\s*\("),
)
# Pattern that confirms a `recompute_sport` call uses the contract.
_RECOMPUTE_CONTRACT_PATTERN = re.compile(
    r"REPLAY_RECOMPUTE_KWARGS|bypass_eligibility\s*=\s*True"
)
# Pattern detecting any `recompute_sport` call at all.
_RECOMPUTE_CALL_PATTERN = re.compile(r"\brecompute_sport\s*\(")


def test_contract_constants_immutable():
    """REPLAY_RECOMPUTE_KWARGS must be a MappingProxyType so no
    runtime caller can flip `bypass_eligibility=False`."""
    import types
    assert isinstance(REPLAY_RECOMPUTE_KWARGS, types.MappingProxyType), (
        "REPLAY_RECOMPUTE_KWARGS must be immutable (MappingProxyType)")
    # Verify the contract values themselves.
    assert REPLAY_RECOMPUTE_KWARGS["dry_run"] is True
    assert REPLAY_RECOMPUTE_KWARGS["write_mode"] == "upsert"
    assert REPLAY_RECOMPUTE_KWARGS["bypass_eligibility"] is True


def test_every_compliant_engine_imports_cleanly():
    for sport, module_path in COMPLIANT_REPLAY_ENGINES.items():
        try:
            importlib.import_module(module_path)
        except Exception as e:  # noqa: BLE001
            raise AssertionError(
                f"Replay engine for sport={sport!r} "
                f"({module_path}) failed to import: {e!r}"
            )


def test_every_compliant_engine_declares_compliance_flag():
    """Each registered replay engine module must declare
    `REPLAY_CONTRACT_COMPLIANT = True` at module scope so static
    audits can verify the contract without runtime introspection."""
    for sport, module_path in COMPLIANT_REPLAY_ENGINES.items():
        mod = importlib.import_module(module_path)
        flag = getattr(mod, "REPLAY_CONTRACT_COMPLIANT", None)
        assert flag is True, (
            f"Replay engine for sport={sport!r} ({module_path}) does "
            f"NOT declare `REPLAY_CONTRACT_COMPLIANT = True` at "
            f"module scope. Per replay-contract policy, every "
            f"registered engine must declare its compliance "
            f"explicitly so future regressions are caught at lint "
            f"time. Got: {flag!r}"
        )


def _read_source(module_path: str) -> str:
    mod = importlib.import_module(module_path)
    src_file = inspect.getsourcefile(mod)
    assert src_file, f"could not locate source for {module_path}"
    return Path(src_file).read_text()


def test_no_engine_invokes_production_eligibility():
    """Source-level audit: no replay engine source file may contain
    `apply_production_eligibility(...)` / `filter_priceable(...)` /
    `filter_pp_playable(...)` invocations.

    String comments and docstrings that REFERENCE these by name are
    fine — we match only call-site syntax (`name(`).
    """
    for sport, module_path in COMPLIANT_REPLAY_ENGINES.items():
        src = _read_source(module_path)
        # Strip docstring / comment lines so doc-references don't
        # false-positive. Cheap heuristic: remove lines that start
        # with `#` and lines that look like docstring content
        # (within triple-quoted blocks). Production source uses
        # only one or two top-of-file docstrings, so a strip on a
        # leading `\"\"\"...\"\"\"` block is sufficient.
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src_no_docstring = src[end + 3:]
            else:
                src_no_docstring = src
        else:
            src_no_docstring = src
        # Remove `# …` comment lines.
        src_no_comments = re.sub(r"^\s*#.*$", "", src_no_docstring,
                                  flags=re.MULTILINE)
        for pat in _VIOLATION_PATTERNS:
            m = pat.search(src_no_comments)
            assert m is None, (
                f"Replay engine for sport={sport!r} ({module_path}) "
                f"contains a call to {m.group(0)!r} — this VIOLATES "
                f"the replay-contract eligibility bypass invariant. "
                f"Replay must score every prop and persist gate state "
                f"as METADATA only, never as a row filter. Either "
                f"remove the filter call or, if the engine uses "
                f"`recompute_sport`, pass `**REPLAY_RECOMPUTE_KWARGS` "
                f"so `bypass_eligibility=True` is enforced."
            )


def test_recompute_callers_use_contract_kwargs():
    """If a replay engine calls `recompute_sport`, the call MUST be
    contract-compliant — either passing `**REPLAY_RECOMPUTE_KWARGS`
    or explicitly setting `bypass_eligibility=True`."""
    for sport, module_path in COMPLIANT_REPLAY_ENGINES.items():
        src = _read_source(module_path)
        if _RECOMPUTE_CALL_PATTERN.search(src) is None:
            # Engine has its own scoring path — no recompute_sport
            # call at all. Bypasses by construction.
            continue
        assert _RECOMPUTE_CONTRACT_PATTERN.search(src) is not None, (
            f"Replay engine for sport={sport!r} ({module_path}) "
            f"calls `recompute_sport` but does NOT use the contract "
            f"kwargs. Replace the call shape with "
            f"`await recompute_sport(db, sport=..., version_tag=..., "
            f"props=..., **REPLAY_RECOMPUTE_KWARGS)` to enforce "
            f"`bypass_eligibility=True` / `dry_run=True` / "
            f"`write_mode='upsert'` at one SSOT location."
        )


def test_recompute_sport_signature_carries_bypass_kwarg():
    """The `recompute_sport` signature itself MUST expose
    `bypass_eligibility` — protects against an accidental removal
    of the kwarg that would silently make every replay caller
    fall back to today's production eligibility chain."""
    from services.scoring.recompute import recompute_sport
    sig = inspect.signature(recompute_sport)
    assert "bypass_eligibility" in sig.parameters, (
        "`services.scoring.recompute.recompute_sport` is missing "
        "the `bypass_eligibility` kwarg. This is the cross-sport "
        "replay-contract handle — removing it silently makes every "
        "replay caller fall back to live-serving eligibility "
        "filters (which drop PP-only props and tank optimizer "
        "input volume). Restore the kwarg before merging."
    )
    p = sig.parameters["bypass_eligibility"]
    assert p.default is False, (
        "`bypass_eligibility` must default to False so live-serving "
        f"callers (e.g. `services/board/engine.py`) keep their "
        f"production filtering. Replay callers opt in by passing "
        f"`bypass_eligibility=True` (via REPLAY_RECOMPUTE_KWARGS). "
        f"Got default: {p.default!r}"
    )


# ── 2-Pipeline contract (locked 2026-06-02). ─────────────────────────
def test_pipeline_registry_has_exactly_two_pipelines():
    """PropVision has exactly TWO pipelines: PLAYER and TEAM.
    Adding a third is an architecture red flag — new surfaces MUST
    consume the output of one of the two pipelines, not introduce
    a third. See /app/memory/ARCHITECTURE.md."""
    assert set(PIPELINE_REGISTRY.keys()) == {"player", "team"}, (
        "PIPELINE_REGISTRY must contain EXACTLY two pipelines "
        f"(`player`, `team`). Got: {sorted(PIPELINE_REGISTRY.keys())}. "
        "Adding a new pipeline is an architecture-level decision — "
        "see /app/memory/ARCHITECTURE.md before changing this."
    )


def test_every_pipeline_has_live_and_backtest_modes():
    """Each pipeline must have a LIVE mode and a BACKTEST mode. The
    BACKTEST mode is production-pipeline replay — same predictor,
    same Vision pipeline, different input/output endpoints."""
    for name, pipeline in PIPELINE_REGISTRY.items():
        assert set(pipeline.keys()) == {"live", "backtest"}, (
            f"Pipeline {name!r} must declare `live` and `backtest` "
            f"modes. Got: {sorted(pipeline.keys())}."
        )


def test_pipeline_modes_covers_all_four_quadrants():
    """`PIPELINE_MODES` enumerates (pipeline, mode) tuples used by
    audit loops. It must include exactly 4 entries — the cross of
    {player, team} × {live, backtest}."""
    assert set(PIPELINE_MODES) == {
        ("player", "live"), ("player", "backtest"),
        ("team",   "live"), ("team",   "backtest"),
    }, f"PIPELINE_MODES drift: {PIPELINE_MODES!r}"


def test_player_pipeline_uses_recompute_sport_for_both_modes():
    """Locks Rule 2: LIVE and BACKTEST player pipelines share the
    same predictor entry point (`recompute_sport`). The ONLY thing
    that changes is input/output endpoints + REPLAY_RECOMPUTE_KWARGS."""
    live = PIPELINE_REGISTRY["player"]["live"]
    bt = PIPELINE_REGISTRY["player"]["backtest"]
    assert live["predictor_entry_point"] == bt["predictor_entry_point"], (
        "Player LIVE and BACKTEST must share predictor entry point. "
        f"Got LIVE={live['predictor_entry_point']!r}, "
        f"BACKTEST={bt['predictor_entry_point']!r}."
    )
    assert bt.get("predictor_kwargs") == "REPLAY_RECOMPUTE_KWARGS", (
        "Player BACKTEST mode must declare `predictor_kwargs = "
        "'REPLAY_RECOMPUTE_KWARGS'` so it gets the eligibility-bypass "
        "shape. Got: " + repr(bt.get("predictor_kwargs"))
    )


def test_team_pipeline_uses_team_scorer_for_both_modes():
    """Locks Rule 2 for the team pipeline: LIVE wraps the team
    scorer; BACKTEST must call the SAME underlying team model
    (`team_xgb_loader.score_team_props_batch`)."""
    live = PIPELINE_REGISTRY["team"]["live"]
    bt = PIPELINE_REGISTRY["team"]["backtest"]
    # LIVE wraps the scorer.
    assert "team_live_xgb_scorer" in live["predictor_entry_point"], (
        f"Team LIVE predictor entry point drifted: "
        f"{live['predictor_entry_point']!r}"
    )
    # BACKTEST calls the same underlying batch predictor.
    assert "team_xgb_loader" in bt["predictor_entry_point"], (
        f"Team BACKTEST must use `team_xgb_loader.score_team_props_batch` "
        f"(the same model wrapped by the LIVE scorer). Got: "
        f"{bt['predictor_entry_point']!r}"
    )


if __name__ == "__main__":
    import traceback
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures += 1
            print(f"  ✗ {name}")
            print(f"     {e}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {name} (uncaught exception)")
            traceback.print_exc(limit=2)
    print()
    if failures:
        print(f"  {failures} test(s) FAILED")
        sys.exit(1)
    print(f"  All replay-contract audit tests PASSED")

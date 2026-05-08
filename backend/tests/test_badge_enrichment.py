"""
Universal badge-enrichment dispatcher tests.
============================================

Pinned contract (2026-05-08, universal badge architecture):

  * `services/badge_enrichment.py::enrich_pick_badges` is the SINGLE
    entry point routes use for badge enrichment.
  * Service may NOT import from `routes.*` (no circular dependency).
  * Output shape — these four canonical fields are always normalized:
        pick["scout_badges"]                   - list[dict]
        pick["context_badges"]                 - list[dict | str]
        pick["active_badges"]                  - list (default [])
        pick["intel_suite"]["context_badges"]  - list when intel_suite
                                                  itself is a dict
  * NBA path: universal scout step only.
  * MLB path: universal scout step + environmental adapter.
  * Every step is failure-isolated. The board must always load.
  * Per the 2026-05-08 directive (d): MLB env badges are written ONLY
    to `scout_badges`, NOT to `intel_suite.context_badges`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List

# Backend root on path.
_BACKEND_ROOT = Path("/app/backend")
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# ─── Architectural guard: dispatcher must not import routes.* ────────
def test_dispatcher_does_not_import_routes_module() -> None:
    src = (_BACKEND_ROOT / "services" / "badge_enrichment.py").read_text()
    assert "from routes." not in src, (
        "services/badge_enrichment.py must NOT import from routes.* "
        "(would create a service→route backward dependency)"
    )
    assert "import routes" not in src.split("\n")[0:50] or all(
        "routes." not in ln for ln in src.split("\n")
        if ln.strip().startswith("import ")
    ), "no route imports allowed"


# ─── NBA path — only the universal scout step runs ───────────────────
def test_nba_path_runs_universal_scout_only(monkeypatch) -> None:
    import services.performance_badges as perf
    import services.badge_enrichment as be

    calls: Dict[str, int] = {"perf": 0, "mlb_env": 0}

    def fake_perf(pick):
        calls["perf"] += 1
        return [{"badge_key": "hot_streak", "id": "hot_streak", "name": "Hot Streak"}]

    monkeypatch.setattr(perf, "generate_performance_badges", fake_perf)

    async def fake_mlb_env(pick, db):
        calls["mlb_env"] += 1

    # Even though MLB path won't run, force-attach the spy so any
    # accidental call would be visible.
    import services.mlb_environmental_badges as mlb_env
    monkeypatch.setattr(mlb_env, "apply_mlb_environmental_badges", fake_mlb_env)

    pick = {"recommendation": "OVER", "player_name": "Test Player",
            "stat_type": "PTS", "line": 22.5}
    asyncio.run(be.enrich_pick_badges(pick, sport="nba", db=None))

    assert calls["perf"] == 1
    assert calls["mlb_env"] == 0
    assert pick["scout_badges"] == [
        {"badge_key": "hot_streak", "id": "hot_streak", "name": "Hot Streak"}
    ]
    # Normalization shape contract.
    assert pick["context_badges"] == []
    assert pick["active_badges"] == []


# ─── UNDER picks skip the universal scout step (parity with route) ──
def test_under_pick_skips_universal_scout(monkeypatch) -> None:
    import services.performance_badges as perf
    import services.badge_enrichment as be

    calls = {"perf": 0}
    monkeypatch.setattr(
        perf, "generate_performance_badges",
        lambda p: (calls.__setitem__("perf", calls["perf"] + 1) or [{"badge_key": "x", "id": "x"}]),
    )

    pick = {"recommendation": "UNDER", "player_name": "U Player",
            "stat_type": "PTS", "line": 22.5}
    asyncio.run(be.enrich_pick_badges(pick, sport="nba", db=None))
    assert calls["perf"] == 0      # UNDER is handled by route-level rewire
    assert pick["scout_badges"] == []


# ─── MLB path — both universal + environmental run, scout dedup ──────
def test_mlb_path_runs_universal_and_environmental(monkeypatch) -> None:
    import services.performance_badges as perf
    import services.badge_enrichment as be
    import services.mlb_environmental_badges as mlb_env

    monkeypatch.setattr(
        perf, "generate_performance_badges",
        lambda p: [{"badge_key": "hot_streak", "id": "hot_streak"}],
    )

    calls = {"env": 0}

    async def fake_apply(pick, db):
        calls["env"] += 1
        # Adapter writes env badges to scout_badges.
        pick["scout_badges"] = list(pick.get("scout_badges") or []) + [
            {"id": "wind_boost", "name": "Wind Boost"},
        ]

    monkeypatch.setattr(mlb_env, "apply_mlb_environmental_badges", fake_apply)

    pick = {"recommendation": "OVER", "player_name": "MLB Player",
            "stat_type": "Total Bases", "line": 1.5,
            "team": "NYY", "home_team": "NYY", "away_team": "BOS"}
    asyncio.run(be.enrich_pick_badges(pick, sport="mlb", db=None))

    assert calls["env"] == 1
    ids = {b["id"] for b in pick["scout_badges"]}
    assert ids == {"hot_streak", "wind_boost"}


# ─── Failure isolation: env adapter raises, board still loads ────────
def test_environmental_failure_does_not_erase_scout(monkeypatch) -> None:
    import services.performance_badges as perf
    import services.badge_enrichment as be
    import services.mlb_environmental_badges as mlb_env

    monkeypatch.setattr(
        perf, "generate_performance_badges",
        lambda p: [{"badge_key": "floor_lock", "id": "floor_lock"}],
    )

    async def boom(pick, db):
        raise RuntimeError("simulated env failure")

    monkeypatch.setattr(mlb_env, "apply_mlb_environmental_badges", boom)

    pick = {"recommendation": "OVER", "player_name": "MLB Player",
            "stat_type": "Total Bases", "line": 1.5}
    # Must not raise.
    asyncio.run(be.enrich_pick_badges(pick, sport="mlb", db=None))
    # Universal scout output preserved.
    assert pick["scout_badges"] == [{"badge_key": "floor_lock", "id": "floor_lock"}]


# ─── Universal scout failure: board still loads, env still attempted ─
def test_scout_failure_does_not_block_environmental(monkeypatch) -> None:
    import services.performance_badges as perf
    import services.badge_enrichment as be
    import services.mlb_environmental_badges as mlb_env

    def boom(_pick):
        raise RuntimeError("simulated scout failure")

    monkeypatch.setattr(perf, "generate_performance_badges", boom)

    calls = {"env": 0}

    async def fake_env(pick, db):
        calls["env"] += 1
        pick["scout_badges"] = (pick.get("scout_badges") or []) + [
            {"id": "cold_zone"}
        ]

    monkeypatch.setattr(mlb_env, "apply_mlb_environmental_badges", fake_env)

    pick = {"recommendation": "OVER", "player_name": "MLB Player",
            "stat_type": "Total Bases", "line": 1.5}
    asyncio.run(be.enrich_pick_badges(pick, sport="mlb", db=None))

    assert calls["env"] == 1
    assert any(b.get("id") == "cold_zone" for b in pick["scout_badges"])


# ─── Field-shape normalization always runs ───────────────────────────
def test_field_shape_normalization() -> None:
    import services.badge_enrichment as be

    pick: Dict[str, Any] = {
        "recommendation": "UNDER",   # forces no scout work
        "context_badges": None,      # was nullable in cached_board
        "intel_suite": {"context_badges": None},
    }
    asyncio.run(be.enrich_pick_badges(pick, sport="nba", db=None))
    assert pick["scout_badges"] == []
    assert pick["context_badges"] == []
    assert pick["active_badges"] == []
    assert pick["intel_suite"]["context_badges"] == []


# ─── Existing context_badges (NBA cached_board overlay) preserved ────
def test_existing_context_badges_preserved_for_nba(monkeypatch) -> None:
    import services.performance_badges as perf
    import services.badge_enrichment as be

    monkeypatch.setattr(perf, "generate_performance_badges", lambda p: [])
    pick = {
        "recommendation": "OVER",
        "context_badges": [
            {"badge_key": "home_cookin", "name": "Home Cookin'"},
        ],
    }
    asyncio.run(be.enrich_pick_badges(pick, sport="nba", db=None))
    assert len(pick["context_badges"]) == 1
    assert pick["context_badges"][0]["badge_key"] == "home_cookin"


# ─── Per directive (d): env step does NOT touch intel_suite.context_badges ─
def test_environmental_does_not_write_intel_suite_context(monkeypatch) -> None:
    import services.performance_badges as perf
    import services.badge_enrichment as be
    import services.mlb_environmental_badges as mlb_env

    monkeypatch.setattr(perf, "generate_performance_badges", lambda p: [])

    async def fake_env(pick, db):
        # Real adapter only writes to scout_badges.
        pick["scout_badges"] = (pick.get("scout_badges") or []) + [
            {"id": "wind_boost"}
        ]

    monkeypatch.setattr(mlb_env, "apply_mlb_environmental_badges", fake_env)

    pick: Dict[str, Any] = {
        "recommendation": "OVER",
        "player_name": "P",
        "stat_type": "Total Bases",
        "line": 1.5,
        "intel_suite": {"context_badges": ["wind_tunnel"]},
    }
    asyncio.run(be.enrich_pick_badges(pick, sport="mlb", db=None))
    # intel_suite.context_badges remains exactly what the existing
    # enrich_*_intel_suite step had set; env step does not touch it.
    assert pick["intel_suite"]["context_badges"] == ["wind_tunnel"]
    assert any(b.get("id") == "wind_boost" for b in pick["scout_badges"])


# ─── MLB env adapter: dedup + no-op when no context available ───────
def test_mlb_env_adapter_no_op_when_no_context(monkeypatch) -> None:
    import services.mlb_environmental_badges as mlb_env

    # Stub weather + umpire to return None; no opponent_pitcher on pick.
    async def no_weather(_team):
        return None

    monkeypatch.setattr(mlb_env, "_read_weather", no_weather)
    monkeypatch.setattr(mlb_env, "_read_umpire_for_team", lambda *a, **k: None)

    # Spy: badge service must NOT be called.
    calls = {"badge_service": 0}

    class _SpyBS:
        async def evaluate_all_badges(self, **kw):
            calls["badge_service"] += 1
            return []

    import services.mlb_badge_system as mbs
    monkeypatch.setattr(mbs, "get_mlb_badge_service", lambda db: _SpyBS())

    pick = {"player_name": "P", "stat_type": "Total Bases", "line": 1.5}
    asyncio.run(mlb_env.apply_mlb_environmental_badges(pick, db=None))
    assert calls["badge_service"] == 0
    assert pick.get("scout_badges") in (None, [])

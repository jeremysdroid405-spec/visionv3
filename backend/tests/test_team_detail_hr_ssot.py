"""
Regression test — SSOT hit-rate consistency.

User report 2026-06-03 (screenshot):
  OVER 112.5 Team Total
  L5 AVG 121.4 / L10 AVG 119.9 / SEASON AVG 116.0
  HIT RATE: L20 10% · L10 20% · L5 20%
  "these hr numbers dont make sense use the same ssot"

Every game in the L10 window scored above 112.5 (average 119.9). The
hit rate of 20% was impossible because it was being graded against the
HISTORICAL book line that day (e.g. 119.5), not the CURRENT prop line
(112.5).

Fix: `_hit_rate_from_game_logs` re-grades the SAME `game_logs` the
averages are built from against the CURRENT line. This test pins
two invariants:

  1. OVER hit% + UNDER hit% ≈ 100 (every game grades to exactly one side)
  2. If L10 avg > line, hit rate for OVER must be > 50%.
     If L10 avg < line, hit rate for UNDER must be > 50%.
"""
import os
import pytest


@pytest.mark.asyncio
async def test_team_detail_hit_rates_consistent_with_averages():
    from motor.motor_asyncio import AsyncIOMotorClient
    import routes.team_with_badges as twb

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    twb.set_team_with_badges_db(db)
    try:
        res = await twb.get_team_with_badges(team_id="nba_bos", sport="nba")
        props = (res.get("player") or {}).get("props") or []
        if not props:
            pytest.skip("nba_bos has no detail props")

        # Group by (stat_type, line) so we can compare OVER vs UNDER.
        by_pair = {}
        for p in props:
            k = (p.get("stat_type"), p.get("line"))
            by_pair.setdefault(k, []).append(p)

        checked_pairs = 0
        for (stat, line), sides in by_pair.items():
            if stat in ("MONEYLINE",) or line is None:
                continue
            if len(sides) != 2:
                continue  # one-sided prop, skip

            over = next(
                (p for p in sides if p.get("direction") == "OVER"), None)
            under = next(
                (p for p in sides if p.get("direction") == "UNDER"), None)
            if over is None or under is None:
                continue
            h_over  = over.get("hit_rate_l10")
            h_under = under.get("hit_rate_l10")
            if h_over is None or h_under is None:
                continue

            # Invariant 1: OVER + UNDER ≈ 100%
            total = h_over + h_under
            assert 95.0 <= total <= 105.0, (
                f"OVER% + UNDER% must ≈ 100 for {stat} line {line} — "
                f"got {h_over} + {h_under} = {total}"
            )

            # Invariant 2: side with avg-above-line wins hit-rate majority
            l10_avg = over.get("l10_avg")
            if l10_avg is None:
                continue
            if stat == "SPREAD":
                # SPREAD: avg is team's margin. Threshold = -line.
                # If margin > -line → team covers → OVER/HOME wins.
                threshold = -line
                if l10_avg > threshold:
                    assert h_over >= 50, (
                        f"SPREAD margin {l10_avg} > threshold {threshold}, "
                        f"OVER hit-rate must be ≥50 — got {h_over}"
                    )
                elif l10_avg < threshold:
                    assert h_under >= 50, (
                        f"SPREAD margin {l10_avg} < threshold {threshold}, "
                        f"UNDER hit-rate must be ≥50 — got {h_under}"
                    )
            else:
                if l10_avg > line:
                    assert h_over >= 50, (
                        f"{stat} L10 avg {l10_avg} > line {line}, "
                        f"OVER hit-rate must be ≥50 — got {h_over}"
                    )
                elif l10_avg < line:
                    assert h_under >= 50, (
                        f"{stat} L10 avg {l10_avg} < line {line}, "
                        f"UNDER hit-rate must be ≥50 — got {h_under}"
                    )
            checked_pairs += 1

        assert checked_pairs > 0, "no OVER/UNDER pairs checked"
    finally:
        client.close()

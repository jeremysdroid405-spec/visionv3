"""Integration test for the team-historical endpoint.

Verifies the three surfaces consumed by `useTeamMasterStats` /
`TeamDetailPage`:

  • `recent_outcomes`  — last-N graded rows per team
  • `scoring_split`    — per-game team_score / opp_score breakdown
  • `h2h_outcomes`     — same but filtered by opponent_team_id
  • `summary.last_10_hit_rate`

Drives the test through the local supervisor backend (localhost:8001)
rather than via the production domain (the user clarified
propvision.bet is prod; preview env is :8001).
"""
from __future__ import annotations
import json
import sys
import urllib.request
import urllib.parse


API = "http://localhost:8001"


def _get(path: str, **params):
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{API}{path}?{qs}" if qs else f"{API}{path}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_basic_nba_team_historical_returns_data():
    status, body = _get(
        "/api/v3/team/historical/nba_bos",
        sport="nba", limit=5,
    )
    assert status == 200
    assert body["team_id"] == "nba_bos"
    assert body["sport"] == "nba"
    assert isinstance(body["recent_outcomes"], list)
    assert isinstance(body["scoring_split"], list)
    assert isinstance(body["h2h_outcomes"], list)
    assert body["h2h_outcomes"] == []   # no opponent_team_id supplied
    assert "summary" in body
    assert "last_10_hit_rate" in body["summary"]


def test_recent_outcomes_carry_grading_fields():
    _, body = _get(
        "/api/v3/team/historical/nba_bos", sport="nba", limit=5,
    )
    if not body["recent_outcomes"]:
        # Acceptable for sparse data dates; smoke ensures shape only.
        return
    r = body["recent_outcomes"][0]
    for field in ("market_category", "market_key", "side",
                   "odds", "hit", "outcome"):
        assert field in r, f"row missing {field!r}: {r}"


def test_scoring_split_breakdown():
    _, body = _get(
        "/api/v3/team/historical/nba_bos", sport="nba", limit=10,
    )
    if not body["scoring_split"]:
        return
    g = body["scoring_split"][0]
    for field in ("team_score", "opp_score", "diff",
                   "home_away", "opponent_team_id"):
        assert field in g, f"scoring row missing {field!r}: {g}"
    assert g["diff"] == g["team_score"] - g["opp_score"]


def test_h2h_outcomes_filter_applied():
    # Use an arbitrary opponent — even if no rows, response must not 500.
    _, body = _get(
        "/api/v3/team/historical/nba_bos",
        sport="nba", opponent_team_id="nba_lal", limit=5,
    )
    # Every returned row must carry that opponent_team_id.
    for r in body["h2h_outcomes"]:
        assert r.get("opponent_team_id") == "nba_lal", (
            f"h2h row has wrong opponent: {r}")


def test_market_category_filter_narrows_recent_outcomes():
    _, body = _get(
        "/api/v3/team/historical/nba_bos",
        sport="nba", market_category="team_total", limit=10,
    )
    for r in body["recent_outcomes"]:
        assert r.get("market_category") == "team_total", (
            f"row leaked through filter: {r}")


def test_unsupported_sport_rejected():
    try:
        status, _ = _get(
            "/api/v3/team/historical/ncaaf_bama", sport="ncaaf",
        )
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"expected 400, got {e.code}"
        return
    assert False, "expected 400 for unsupported sport"


def test_blank_team_id_rejected():
    try:
        # Empty path segment routes to 404 (FastAPI), not 400 — fine.
        urllib.request.urlopen(
            f"{API}/api/v3/team/historical/?sport=nba", timeout=5)
    except urllib.error.HTTPError as e:
        assert e.code in (400, 404, 405), f"unexpected code {e.code}"
        return
    assert False, "expected 4xx for blank team_id"


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
            print(f"  ✗ {name}: {e}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {name} (uncaught)")
            traceback.print_exc(limit=2)
    print()
    if failures:
        sys.exit(1)
    print("  All team-historical endpoint tests PASSED")

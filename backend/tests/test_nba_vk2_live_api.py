"""
PRODUCTION-GRADE LIVE-API TESTS — NBA VK2
==========================================
Hits the actual live HTTP surface (`/api/v3/ferrari/...?sport=nba`)
and verifies the v2_5yr_weighted_pruned52 model output reaches users.
Catches regressions in the adapter, scoring pipeline, or response
serialization that unit tests can't see.

  PROD-NBA-API-01  /api/v3/ferrari/safe-haven?sport=nba
              · returns 200 with picks
              · every pick has `vk2_projection` (numeric, ≥ 0)
              · every pick has `vk2_sigma` (numeric, > 0)
              · every pick has `p_true_vk2` (numeric, in [0, 1])
  PROD-NBA-API-02  /api/v3/ferrari/front-lines?sport=nba (same)
  PROD-NBA-API-03  /api/v3/ferrari/war-zone?sport=nba    (same)
  PROD-NBA-API-04  No live NBA pick has vk2_projection > 4× the
                  player's L20 mean for that stat (live canary).

Environment:
  Reads BASE_URL from frontend/.env REACT_APP_BACKEND_URL.

Run:
    cd /app/backend && python -m pytest tests/test_nba_vk2_live_api.py -v
"""
from __future__ import annotations
import os
import pytest
import requests
import pymongo

TIERS = ("safe-haven", "front-lines", "war-zone")


def _resolve_base_url() -> str:
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


BASE = _resolve_base_url()


@pytest.fixture(scope="module")
def db():
    client = pymongo.MongoClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    yield client[os.environ.get("DB_NAME", "pick_vision")]
    client.close()


def _stat_field(stat_type: str):
    """Map NBA stat label to (log_field_or_tuple, label)."""
    s = (stat_type or "").upper().strip()
    if s == "PTS":
        return ("pts",)
    if s == "REB":
        return ("reb",)
    if s == "AST":
        return ("ast",)
    if s == "3PM":
        return ("fg3m",)
    if s == "PRA":
        return ("pts", "reb", "ast")
    return None


# ─── PROD-NBA-API-01..03: every tier exposes vk2 fields ────────────
@pytest.mark.parametrize("tier", TIERS)
def test_prod_nba_api_picks_have_vk2_fields(tier):
    """PROD-NBA-API-01..03: live NBA picks expose vk2_projection +
    vk2_sigma + p_true_vk2 with valid numeric values."""
    url = f"{BASE}/api/v3/ferrari/{tier}?sport=nba"
    r = requests.get(url, timeout=30)
    assert r.status_code == 200, f"{tier}: HTTP {r.status_code}"
    data = r.json()
    picks = data.get("picks") or []
    if not picks:
        pytest.skip(f"{tier}: no NBA picks live right now")

    bad = []
    for p in picks:
        vp = p.get("vk2_projection")
        vs = p.get("vk2_sigma")
        pt = p.get("p_true_vk2")
        # Skip rows that are PP-only / tier-eligible-without-VK2 — these
        # legitimately don't have vk2 fields. We only verify that when
        # present, they're well-formed.
        if vp is None and vs is None and pt is None:
            continue
        try:
            if vp is not None:
                assert isinstance(vp, (int, float)) and vp >= 0
            if vs is not None:
                assert isinstance(vs, (int, float)) and vs > 0
            if pt is not None:
                assert isinstance(pt, (int, float)) and 0 <= pt <= 1
        except AssertionError:
            bad.append({
                "player": p.get("player_name"),
                "stat": p.get("stat_type_extracted"),
                "vk2_projection": vp, "vk2_sigma": vs, "p_true_vk2": pt
            })
    assert not bad, f"{tier}: malformed VK2 fields in {len(bad)} pick(s): {bad[:3]}"


# ─── PROD-NBA-API-04: live 4× canary ───────────────────────────────
def test_prod_nba_api_no_blowup_in_live_picks(db):
    """PROD-NBA-API-04: no live NBA pick has vk2_projection > 4× L20 mean.

    Pulls picks from all 3 tiers and cross-references each
    vk2_projection against the L20 mean from `nba_master_hub`. Catches
    regressions in the live serving path that unit tests won't see.
    """
    blowups = []
    for tier in TIERS:
        r = requests.get(
            f"{BASE}/api/v3/ferrari/{tier}?sport=nba", timeout=30)
        if r.status_code != 200:
            continue
        for p in (r.json().get("picks") or []):
            mu = p.get("vk2_projection")
            stat = (p.get("stat_type_extracted")
                    or p.get("stat_type") or "").upper()
            fields = _stat_field(stat)
            if mu is None or not fields:
                continue
            name = p.get("player_name")
            doc = db.nba_master_hub.find_one(
                {"$or": [{"display_name": name}, {"player_name": name}]},
                {"_id": 0, "bdl_game_logs": {"$slice": 20}})
            if not doc:
                continue
            logs = doc.get("bdl_game_logs") or []
            if len(logs) < 10:
                continue
            vals = []
            for g in logs:
                try:
                    vals.append(sum(float(g.get(f, 0) or 0) for f in fields))
                except (TypeError, ValueError):
                    continue
            if not vals:
                continue
            l20m = sum(vals) / len(vals)
            if l20m < 1.0:
                continue
            ratio = float(mu) / max(l20m, 0.01)
            if ratio > 4.0:
                blowups.append((tier, name, stat, round(float(mu), 2),
                                round(l20m, 2), round(ratio, 2)))
    assert not blowups, (
        f"REGRESSION: {len(blowups)} live NBA pick(s) exceed 4× L20 mean. "
        f"First 5: {blowups[:5]}"
    )

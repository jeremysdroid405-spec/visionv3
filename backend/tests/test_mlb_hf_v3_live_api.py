"""
PRODUCTION-GRADE LIVE-API TESTS — MLB HF v3.0_bayes
====================================================
Hits the actual live HTTP surface (`/api/v3/ferrari/...`) and verifies
the v3.0_bayes model output reaches users — closing the loop from
xgboost artifact → FastAPI response. Catches regressions in the
adapter, scoring pipeline, or response serialization that unit tests
can't see.

  PROD-API-01  /api/v3/ferrari/safe-haven?sport=mlb
              · returns 200 with a non-empty `picks` list
              · every pick has `projection_model_version='MLB_HF_v3.0_bayes'`
  PROD-API-02  /api/v3/ferrari/front-lines?sport=mlb (same checks)
  PROD-API-03  /api/v3/ferrari/war-zone?sport=mlb    (same checks)
  PROD-API-04  No live MLB pick has a μ that exceeds 4× the player's
              L20 mean for that stat (the 4× canary, on the LIVE pool).

Environment:
  Reads BASE_URL from REACT_APP_BACKEND_URL (frontend/.env).

Run:
    cd /app/backend && python -m pytest tests/test_mlb_hf_v3_live_api.py -v
"""
from __future__ import annotations
import os
import re
import pytest
import requests
import pymongo

EXPECTED_VERSION = "MLB_HF_v3.0_bayes"
TIERS = ("safe-haven", "front-lines", "war-zone")


def _resolve_base_url() -> str:
    # Prefer REACT_APP_BACKEND_URL from frontend/.env (production proxy);
    # fall back to localhost for offline runs.
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


def _normalize_stat(s: str) -> str:
    """Approximate MLBHighFrictionModel._normalize_stat for the
    blow-up canary lookup. Keeps this test independent of the model
    object."""
    aliases = {
        "k": "pitcher_strikeouts", "ks": "pitcher_strikeouts",
        "pitcher k": "pitcher_strikeouts", "Pitcher K": "pitcher_strikeouts",
        "tb": "total_bases", "rbi": "rbis", "sb": "stolen_bases",
        "hr": "home_runs", "h": "hits", "r": "runs",
        "hrr": "hits+runs+rbis", "hits+runs+rbi": "hits+runs+rbis",
        "batter_strikeouts": "strikeouts", "batter_walks": "walks",
        "walks_allowed": "pitcher_walks",
    }
    s_l = (s or "").strip().lower().replace(" ", "_")
    return aliases.get(s_l, s_l)


# ─── PROD-API-01..03: every tier's MLB picks stamp v3.0_bayes ───────
@pytest.mark.parametrize("tier", TIERS)
def test_prod_api_mlb_picks_stamp_v3(tier):
    """PROD-API-01..03: live MLB picks across all tiers stamp v3.0_bayes."""
    url = f"{BASE}/api/v3/ferrari/{tier}?sport=mlb"
    r = requests.get(url, timeout=30)
    assert r.status_code == 200, f"{tier}: HTTP {r.status_code} from {url}"
    data = r.json()
    picks = data.get("picks") or []
    if not picks:
        pytest.skip(f"{tier}: no MLB picks live right now")
    # Every pick must have v3.0_bayes either at top level or inside
    # `_mlb_score_doc`.
    bad = []
    for p in picks:
        v = (p.get("projection_model_version")
             or (p.get("_mlb_score_doc") or {}).get("projection_model_version"))
        if v != EXPECTED_VERSION:
            bad.append((p.get("player_name"), p.get("stat_type"), v))
    assert not bad, f"{tier}: {len(bad)} picks NOT stamped v3.0_bayes: {bad[:5]}"


# ─── PROD-API-04: 4× L20 canary against the LIVE pool ───────────────
def test_prod_api_no_blowup_in_live_picks(db):
    """PROD-API-04: no live MLB pick has μ > 4× the player's L20 mean.

    Pulls picks from all 3 tiers and cross-references each predicted
    value against the L20 mean from `mlb_master_hub_2026.bdl_game_logs`.
    A failure here means the live serving path produced an inflated
    projection that the unit-test canary missed (e.g., a different
    stat-name, a unique scoring adapter path, etc.).
    """
    blowups = []
    for tier in TIERS:
        r = requests.get(
            f"{BASE}/api/v3/ferrari/{tier}?sport=mlb", timeout=30)
        if r.status_code != 200:
            continue
        for p in (r.json().get("picks") or []):
            mu = (p.get("projected") or p.get("predicted")
                  or (p.get("_mlb_score_doc") or {}).get("predicted"))
            stat = _normalize_stat(p.get("stat_type") or "")
            name = p.get("player_name")
            if mu is None or stat not in ("hits", "total_bases", "rbis",
                                           "runs", "hits+runs+rbis",
                                           "home_runs", "doubles",
                                           "singles", "walks", "strikeouts",
                                           "pitcher_strikeouts",
                                           "earned_runs", "hits_allowed",
                                           "pitcher_walks", "pitcher_outs"):
                continue
            # Look up L20 mean from master_hub.
            doc = db.mlb_master_hub_2026.find_one(
                {"$or": [{"display_name": name}, {"player_name": name},
                         {"mlb_full_name": name}]},
                {"_id": 0, "bdl_game_logs": {"$slice": 20}})
            if not doc:
                continue
            logs = doc.get("bdl_game_logs") or []
            if len(logs) < 10:
                continue
            # Compute L20 mean for the matching stat.
            if stat == "hits+runs+rbis":
                vals = [(float(g.get("hits", 0) or 0)
                         + float(g.get("runs", 0) or 0)
                         + float(g.get("rbis", 0) or 0))
                         for g in logs]
            elif stat == "singles":
                vals = []
                for g in logs:
                    h = float(g.get("hits", 0) or 0)
                    d = float(g.get("doubles", 0) or 0)
                    t = float(g.get("triples", 0) or 0)
                    hr = float(g.get("home_runs", 0) or 0)
                    vals.append(max(0, h - d - t - hr))
            else:
                vals = [float(g.get(stat, 0) or 0) for g in logs]
            l20m = sum(vals) / len(vals) if vals else 0.0
            if l20m < 0.5:  # very low-volume stat — skip canary
                continue
            ratio = float(mu) / max(l20m, 0.01)
            if ratio > 4.0:
                blowups.append((tier, name, p.get("stat_type"),
                                round(float(mu), 2), round(l20m, 2),
                                round(ratio, 2)))
    assert not blowups, (
        f"REGRESSION: {len(blowups)} live MLB pick(s) exceed 4× L20 mean. "
        f"First 5: {blowups[:5]}"
    )

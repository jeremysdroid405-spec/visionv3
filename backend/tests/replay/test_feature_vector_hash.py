"""Feature-vector hash regression tests.

Computes a deterministic SHA over the sorted (feature_name, value)
pairs in the canonical post-fix feature dict for a small set of
known players. Any silent feature-builder change trips these tests
immediately.

To re-bless the hashes after an INTENTIONAL feature-set change, run:
    cd /app/backend && python tests/replay/test_feature_vector_hash.py --regen
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, "/app/backend")

import pytest

from services.replay.mlb_replay_engine import (
    _build_player_dict, _build_game_logs, _opp_team_from_event,
    _derive_batter_hand_from_hub,
)
from services.replay.mlb_feature_cache import (
    SOURCE_VERSION as FCACHE_V, normalize_player_name,
)


# Players we lock hashes for. Each is "stable" enough — a regular
# starter with > 30 games on the master_hub doc by 2026-05-06.
LOCKED = [
    {"name": "Matt Olson",       "date": "2026-05-06",
     "stat": "total_bases",      "line": 1.5},
    {"name": "Aaron Judge",      "date": "2026-05-06",
     "stat": "hits",             "line": 0.5},
    {"name": "Mookie Betts",     "date": "2026-05-06",
     "stat": "total_bases",      "line": 1.5},
]

# Hashes filled in by the --regen pass below or by the conftest helper.
EXPECTED_HASHES_FILE = "/app/backend/tests/replay/_feature_hashes.json"


def _hash_features(d):
    """Stable SHA-256 over sorted (key, rounded value) pairs.

    Values are rounded to 6 dp to absorb float64 jitter without
    masking real changes.
    """
    h = hashlib.sha256()
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, bool):
            piece = f"{k}={int(v)}"
        elif isinstance(v, (int, float)):
            piece = f"{k}={round(float(v), 6)}"
        elif v is None:
            piece = f"{k}=null"
        else:
            piece = f"{k}={v}"
        h.update(piece.encode("utf-8")); h.update(b"\n")
    return h.hexdigest()


def _build_replay_features(model, db, *, name, date, stat, line):
    """Reproduce the EXACT feature build replay_one() does (post-fix)."""
    norm = normalize_player_name(name)
    cache_row = db.mlb_replay_feature_cache.find_one(
        {"game_date": date, "player_name_normalized": norm,
         "stat_family": stat, "source_version": FCACHE_V},
        {"_id": 0})
    if not cache_row:
        return None, f"no cache row for {name} {date} {stat}"

    odds_row = db.mlb_historical_alt_odds_raw.find_one(
        {"game_date": date, "snapshot_iso": f"{date}T11:00:00Z",
         "player_name_normalized": norm, "line": line},
        {"_id": 0}) or {
            "home_team": "", "away_team": ""}

    hub_extras = db.mlb_master_hub_2026.find_one(
        {"$or": [{"bdl_id":         cache_row["bdl_id"]},
                 {"bdl_player_id":  cache_row["bdl_id"]}]},
        {"_id": 0, "bdl_id": 1, "vs_left": 1, "vs_right": 1,
         "home_splits": 1, "away_splits": 1,
         "bats_throws": 1, "bats": 1, "throws": 1})

    player = _build_player_dict(cache_row, hub_extras=hub_extras)
    logs = _build_game_logs(cache_row)
    opp, is_away = _opp_team_from_event(
        cache_row, odds_row.get("home_team") or "",
        odds_row.get("away_team") or "")
    park = cache_row.get("team") if not is_away else opp
    bh = _derive_batter_hand_from_hub(hub_extras)
    pa_cache = model._get_pa_cache()
    pa = pa_cache.batter_features(int(cache_row["player_id"]), date) \
        if pa_cache else None
    feats = model._build_friction_features(
        player, logs, stat,
        opponent=opp, park_team=park, dk_odds=None, line=line,
        statcast_features=cache_row.get("statcast_self_as_of"),
        pa_batter_features=pa,
        batter_hand=bh,
        opp_pitcher_throws=cache_row.get("opp_pitcher_throws"),
    )
    return feats, None


@pytest.fixture(scope="module")
def expected_hashes():
    if not os.path.exists(EXPECTED_HASHES_FILE):
        pytest.skip(
            f"baseline hashes not yet seeded — "
            f"run `python tests/replay/test_feature_vector_hash.py --regen` "
            f"to create {EXPECTED_HASHES_FILE}"
        )
    with open(EXPECTED_HASHES_FILE) as fh:
        return json.load(fh)


@pytest.mark.parametrize("ctx", LOCKED,
                         ids=[f"{c['name']}_{c['stat']}" for c in LOCKED])
def test_feature_hash_matches_baseline(ctx, model, db, expected_hashes):
    key = f"{ctx['name']}|{ctx['date']}|{ctx['stat']}|{ctx['line']}"
    expected = expected_hashes.get(key)
    if expected is None:
        pytest.skip(f"no baseline entry for {key}")

    feats, err = _build_replay_features(model, db, **ctx)
    if err:
        pytest.skip(err)
    actual = _hash_features(feats)
    assert actual == expected, (
        f"feature hash for {key} drifted:\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}\n"
        f"If this drift is INTENTIONAL, run --regen to update the "
        f"baseline."
    )


# ── CLI: regenerate baseline hashes ──────────────────────────────────
def _regen():
    """One-shot regeneration of the baseline-hashes file."""
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    from pymongo import MongoClient
    from services.mlb_high_friction_model import MLBHighFrictionModel

    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    model = MLBHighFrictionModel(db)
    model.load_models()
    out = {}
    for ctx in LOCKED:
        key = f"{ctx['name']}|{ctx['date']}|{ctx['stat']}|{ctx['line']}"
        feats, err = _build_replay_features(model, db, **ctx)
        if err:
            print(f"  ⚠️  skip {key}: {err}")
            continue
        h = _hash_features(feats)
        out[key] = h
        print(f"  {key:>55}  {h}")
    with open(EXPECTED_HASHES_FILE, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"\nwrote {EXPECTED_HASHES_FILE}")
    client.close()


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regen()
    else:
        print("usage: python test_feature_vector_hash.py --regen")

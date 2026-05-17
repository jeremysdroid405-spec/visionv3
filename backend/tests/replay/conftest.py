"""Shared fixtures for replay tests — load model and Olson context once
per pytest session to keep total runtime + RAM bounded."""
import os
# Pin OMP thread limits BEFORE numpy/xgboost import to keep any
# third-party libs from spawning workers during pytest collection.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("XGBOOST_N_THREADS", "1")

import sys
sys.path.insert(0, "/app/backend")

import pytest
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient

from services.mlb_high_friction_model import MLBHighFrictionModel
from services.replay.mlb_feature_cache import (
    SOURCE_VERSION as FCACHE_V, normalize_player_name,
)


@pytest.fixture(scope="session")
def db():
    """Read-only pymongo handle. No teardown writes."""
    client = MongoClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]
    client.close()


@pytest.fixture(scope="session")
def model(db):
    """One MLBHighFrictionModel per test session.

    `load_models()` applies the single-thread guard unless
    `MLB_HF_ALLOW_MULTITHREAD=1`. Tests assume the default.
    """
    m = MLBHighFrictionModel(db)
    m.load_models()
    return m


# ── Olson 2026-05-06 — the canonical fixture for hydration tests ───
OLSON_DATE = "2026-05-06"
OLSON_SNAPSHOT = "2026-05-06T11:00:00Z"
OLSON_STAT = "total_bases"


@pytest.fixture(scope="session")
def olson_hub(db):
    o = db.mlb_master_hub_2026.find_one(
        {"display_name": "Matt Olson"}, {"_id": 0})
    assert o, "Matt Olson not found in mlb_master_hub_2026"
    return o


@pytest.fixture(scope="session")
def olson_norm():
    return normalize_player_name("Matt Olson")


@pytest.fixture(scope="session")
def olson_cache_row(db, olson_norm):
    c = db.mlb_replay_feature_cache.find_one(
        {"game_date": OLSON_DATE,
         "player_name_normalized": olson_norm,
         "stat_family": OLSON_STAT,
         "source_version": FCACHE_V},
        {"_id": 0})
    assert c, f"no replay feature cache row for Olson {OLSON_DATE} {OLSON_STAT}"
    return c


@pytest.fixture(scope="session")
def olson_hub_extras(db, olson_cache_row):
    proj = {"_id": 0, "bdl_id": 1, "vs_left": 1, "vs_right": 1,
            "home_splits": 1, "away_splits": 1,
            "bats_throws": 1, "bats": 1, "throws": 1}
    he = db.mlb_master_hub_2026.find_one(
        {"$or": [{"bdl_id":        olson_cache_row["bdl_id"]},
                 {"bdl_player_id": olson_cache_row["bdl_id"]}]}, proj)
    assert he, "no hub_extras for Olson"
    return he


@pytest.fixture(scope="session")
def olson_odds_row(db, olson_norm):
    o = db.mlb_historical_alt_odds_raw.find_one(
        {"game_date": OLSON_DATE, "snapshot_iso": OLSON_SNAPSHOT,
         "player_name_normalized": olson_norm,
         "market": {"$in": ["batter_total_bases",
                              "batter_total_bases_alternate"]},
         "line": 1.5}, {"_id": 0})
    assert o, "no Olson odds row for line 1.5"
    return o

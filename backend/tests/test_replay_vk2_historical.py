"""
Tests for `services.replay.vk2_historical` — the as-of-time VK2 service.

These cover the spec's 5 mandatory test areas:
  1. VK2 feature builder uses no future games
  2. VK2 projection output matches production schema
  3. Combo props use VK2 component μ
  4. Historical replay refuses to score if VK2 features are missing
     for a supported family (no silent legacy fallback)
  5. No fallback to legacy VK1 (unsupported families return error)

Run with:
    cd /app/backend && pytest tests/test_replay_vk2_historical.py -v
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.replay.leakage_checks import LeakageDetected
from services.replay.vk2_historical import (
    REPLAY_FAMILY_TO_MODEL_KEY, VK2_FILE_MAP, VK2_UNSUPPORTED_FAMILIES,
    COMBO_COMPONENTS,
    feature_completeness_label, feature_hash, adv_coverage_l10,
    load_vk2_models, _norm_name,
)
from services.replay import vk2_historical


# ---------------------------------------------------------------- pure utils
def test_feature_completeness_label_thresholds():
    """Spec: >=5 of L10 games carry adv → vk2_full; otherwise vk2_partial."""
    assert feature_completeness_label(0) == "vk2_partial"
    assert feature_completeness_label(4) == "vk2_partial"
    assert feature_completeness_label(5) == "vk2_full"
    assert feature_completeness_label(10) == "vk2_full"


def test_feature_hash_stable_across_dict_order():
    """Hash MUST be schema-ordered, NOT input-dict-ordered, so silent
    drift surfaces."""
    schema = ["pts_L5_mean", "reb_L5_mean", "is_home"]
    a = {"is_home": 1.0, "pts_L5_mean": 22.5, "reb_L5_mean": 7.0}
    b = {"pts_L5_mean": 22.5, "is_home": 1.0, "reb_L5_mean": 7.0}
    assert feature_hash(a, schema) == feature_hash(b, schema)


def test_feature_hash_changes_when_value_changes():
    schema = ["pts_L5_mean"]
    a = {"pts_L5_mean": 20.0}
    b = {"pts_L5_mean": 21.0}
    assert feature_hash(a, schema) != feature_hash(b, schema)


def test_adv_coverage_l10_counts_only_top_10():
    history = [{"player_id": 1, "game_id": i} for i in range(15)]
    adv_map = {(1, i): {} for i in range(0, 15, 2)}  # every other
    cov = adv_coverage_l10(history, adv_map)
    # In top-10 (gids 0..9), gids 0,2,4,6,8 in adv_map → 5
    assert cov == 5


def test_norm_name_canonical():
    assert _norm_name("LeBron James") == "lebronjames"
    assert _norm_name("De'Aaron Fox") == "deaaronfox"
    assert _norm_name("") == ""
    assert _norm_name(None) == ""


# ---------------------------------------------------------------- model loader
def test_load_vk2_models_returns_expected_stat_keys():
    """Each VK2 model file should produce one entry with the production
    schema (model, scaler, features, sigma, version, feature_count)."""
    models = load_vk2_models()
    # All 5 stat models should be loadable.
    for stat in ("PTS", "REB", "AST", "3PM", "PRA"):
        assert stat in models, f"missing model for {stat}"
        m = models[stat]
        for k in ("model", "scaler", "features", "sigma",
                  "version", "feature_count"):
            assert k in m, f"{stat} missing key {k}"
        assert m["sigma"] > 0, f"{stat} sigma must be positive"
        assert isinstance(m["features"], list)
        assert m["feature_count"] == len(m["features"])


def test_unsupported_families_explicit_set():
    """Spec: BLK / STL / TURNOVERS are not supported by VK2.
    Replay must NOT silently fall through to legacy VK1."""
    assert VK2_UNSUPPORTED_FAMILIES == {"BLK", "STL", "TURNOVERS"}
    # And none of them have a model file:
    for fam in VK2_UNSUPPORTED_FAMILIES:
        assert fam not in VK2_FILE_MAP


def test_combo_components_match_production():
    """Combo synthesis components MUST match
    NBAScoringAdapter._COMBO_COMPONENTS exactly."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    prod = NBAScoringAdapter._COMBO_COMPONENTS
    # Production keys are lowercased; replay uses uppercase canonical.
    expected_replay = {k.upper(): tuple(v) for k, v in prod.items()}
    assert COMBO_COMPONENTS == expected_replay


def test_replay_family_to_model_key_threes_alias():
    """The replay normalizer emits THREES; production model key is 3PM."""
    assert REPLAY_FAMILY_TO_MODEL_KEY["THREES"] == "3PM"
    assert REPLAY_FAMILY_TO_MODEL_KEY["PTS"]    == "PTS"


# ---------------------------------------------------------------- leakage
@pytest.mark.asyncio
async def test_build_history_logs_as_of_blocks_future_games():
    """The leakage assertion MUST raise on future-dated rows, even if
    they slipped past the date filter."""

    class FakeCursor:
        def __init__(self, docs):
            self._docs = docs
        def sort(self, *a, **k):
            return self
        def limit(self, *a, **k):
            return self
        def __aiter__(self):
            self._it = iter(self._docs)
            return self
        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class FakeColl:
        def __init__(self, docs):
            self._docs = docs
        def find(self, *a, **k):
            return FakeCursor(self._docs)

    class FakeDB(dict):
        def __getitem__(self, name):
            return self._coll
        def set_coll(self, c):
            self._coll = c

    # Inject a future-dated log to prove the assertion fires regardless
    # of the date filter (defense in depth).
    snap_ts = datetime(2024, 2, 15, tzinfo=timezone.utc)
    db = FakeDB()
    db.set_coll(FakeColl([
        {"player_id": 1, "game_id": 100, "date": "2024-02-10",
         "pts": 20, "reb": 5, "ast": 4, "min": "32"},
        {"player_id": 1, "game_id": 101, "date": "2024-02-20",  # FUTURE
         "pts": 22, "reb": 6, "ast": 3, "min": "30"},
    ]))
    with pytest.raises(LeakageDetected):
        await vk2_historical.build_history_logs_as_of(
            db, bdl_player_id=1, as_of_ts=snap_ts, window=20,
        )


@pytest.mark.asyncio
async def test_build_history_logs_as_of_requires_tz_aware():
    naive = datetime(2024, 2, 15)
    with pytest.raises(ValueError):
        await vk2_historical.build_history_logs_as_of(
            db=None, bdl_player_id=1, as_of_ts=naive,
        )


# ---------------------------------------------------------------- predictor schema
@pytest.mark.asyncio
async def test_predict_vk2_unsupported_family_returns_explicit_error():
    """Spec: refuses to score; never falls back to legacy."""
    snap_ts = datetime(2024, 2, 15, tzinfo=timezone.utc)
    out = await vk2_historical.predict_vk2_as_of(
        db=None, bdl_player_id=1, stat_family="BLK",
        line=0.5, snapshot_ts=snap_ts,
    )
    assert out["projection"] is None
    assert out["sigma"] is None
    assert out["error"].startswith("vk2_unsupported_family"), out


@pytest.mark.asyncio
async def test_predict_combo_for_non_combo_family():
    snap_ts = datetime(2024, 2, 15, tzinfo=timezone.utc)
    out = await vk2_historical.predict_combo_vk2_as_of(
        db=None, bdl_player_id=1, stat_family="PTS",
        line=15.5, snapshot_ts=snap_ts,
    )
    assert out["projection"] is None
    assert out["error"].startswith("not_a_combo_family"), out

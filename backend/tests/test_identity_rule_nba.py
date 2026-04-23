"""
Global Identity Rule (2026-04-23) — NBA regression tests.

Verifies:
  * `NBAScoringAdapter` joins game logs strictly by `bdl_player_id`.
  * No name-based fallback in scoring.
  * `_compute_cv_and_hit_rate` returns `missing_bdl_id` status when the
    prop has no `bdl_player_id`.
  * Ingest-time identity normalizer handles punctuation / suffix
    variation deterministically.
"""
from services.scoring.adapters.nba_scoring import NBAScoringAdapter
from services.universal_odds_sync import _normalize_player_name_for_ingest


def test_identity_normalizer_handles_initials():
    # "C.J. McCollum" must collapse to the same key the hub stores.
    assert _normalize_player_name_for_ingest("C.J. McCollum") == "cj mccollum"
    assert _normalize_player_name_for_ingest("CJ McCollum") == "cj mccollum"
    assert _normalize_player_name_for_ingest("cj mccollum") == "cj mccollum"


def test_identity_normalizer_drops_suffixes():
    assert _normalize_player_name_for_ingest("Kelly Oubre Jr.") == "kelly oubre"
    assert _normalize_player_name_for_ingest("Jabari Smith Jr") == "jabari smith"
    assert _normalize_player_name_for_ingest("Tim Hardaway Jr") == "tim hardaway"
    assert _normalize_player_name_for_ingest("Isaiah Stewart II") == "isaiah stewart"


def test_identity_normalizer_handles_hyphens():
    assert (
        _normalize_player_name_for_ingest("Collin Murray-Boyles")
        == "collin murray boyles"
    )


def test_identity_normalizer_empty():
    assert _normalize_player_name_for_ingest("") == ""
    assert _normalize_player_name_for_ingest(None) == ""


def test_cv_hr_returns_missing_bdl_id_when_no_id():
    """Scoring must NOT fall back to name matching. When the prop has
    no `bdl_player_id`, CV + HR are skipped with status=missing_bdl_id."""
    adapter = NBAScoringAdapter()
    # Seed a synthetic log entry so we can prove it would have been
    # used IF scoring still resolved by name — it must NOT be.
    adapter._logs_by_id[999] = [
        {"pts": 20, "reb": 5, "ast": 5, "date": "2026-01-01"}
    ] * 20
    adapter._logs_loaded = True

    (cv, cv_status, hr, ceiling, hr_over, hr_under, hr_status) = \
        adapter._compute_cv_and_hit_rate(
            bdl_player_id=None, stat_type="PTS", line=15.0,
            direction="OVER", window=20,
        )
    assert cv is None
    assert cv_status == "missing_bdl_id"
    assert hr is None
    assert hr_status == "missing_bdl_id"


def test_cv_hr_computes_when_id_resolves():
    """When the prop has a resolved `bdl_player_id` that matches a
    preloaded log, metrics compute normally."""
    adapter = NBAScoringAdapter()
    adapter._logs_by_id[999] = [
        {"pts": 20 + i, "reb": 5, "ast": 5, "date": f"2026-01-{i:02d}"}
        for i in range(1, 21)
    ]
    adapter._logs_loaded = True

    (cv, cv_status, hr, ceiling, hr_over, hr_under, hr_status) = \
        adapter._compute_cv_and_hit_rate(
            bdl_player_id=999, stat_type="PTS", line=25.0,
            direction="OVER", window=20,
        )
    assert cv is not None
    assert cv_status == "computed"
    assert hr is not None
    assert hr_status == "computed"


def test_get_logs_by_id_no_name_fallback():
    """`_get_logs_by_id` returns [] for unknown IDs and never consults
    any name cache (no such cache exists)."""
    adapter = NBAScoringAdapter()
    adapter._logs_by_id[1] = [{"pts": 10}]
    adapter._logs_loaded = True

    assert adapter._get_logs_by_id(1) == [{"pts": 10}]
    assert adapter._get_logs_by_id(2) == []
    assert adapter._get_logs_by_id(None) == []
    # No `_logs_cache` / `_name_to_id` attributes exist on the adapter
    # post Global Identity Rule refactor.
    assert not hasattr(adapter, "_logs_cache")
    assert not hasattr(adapter, "_name_to_id")
    assert not hasattr(adapter, "_normalize_name")
    assert not hasattr(adapter, "_resolve_player_id")
    assert not hasattr(adapter, "_get_logs_for_player")

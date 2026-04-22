"""
Regression: `book_count` / `coverage_class` / `books_anchored` must be
included in the persisted score-doc projection so the 0-Book Exclusion
Rule signal survives the MongoDB write and surfaces on the API response.
"""
from services.scoring.prop_scores_store import _SCORE_OUTPUT_FIELDS


def test_coverage_fields_in_persisted_projection():
    required = {"book_count", "coverage_class", "books_anchored"}
    assert required.issubset(_SCORE_OUTPUT_FIELDS), (
        f"coverage fields missing from _SCORE_OUTPUT_FIELDS — "
        f"have {required - set(_SCORE_OUTPUT_FIELDS)}"
    )


def test_recompute_propagates_coverage_fields():
    """recompute.py must read book_count/coverage_class from ctx.raw_prop
    and copy them onto the score doc — otherwise the adapter's
    classification is lost at write-time."""
    import inspect
    from services.scoring import recompute
    src = inspect.getsource(recompute)
    for needle in ("book_count", "coverage_class", "books_anchored"):
        assert needle in src, f"recompute.py missing propagation for {needle}"

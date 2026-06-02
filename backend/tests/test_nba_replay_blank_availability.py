"""Regression: `_classify_availability` survives blank/missing log
dates without throwing `ValueError: Invalid isoformat string: ''`.

Replay invariant: replay must NEVER raise on legitimate historical
data shapes. Empty `date` fields appear in `bdl_game_logs` (legacy
ingest gaps); the production scorer must handle them gracefully.
Same contract for live serving — production logs can also carry
malformed dates, and we don't want a single bad row to take down
slate scoring.
"""
from __future__ import annotations
import sys

sys.path.insert(0, "/app/backend")


def test_classify_availability_handles_blank_dates():
    """Two adjacent logs with blank `date` fields must NOT raise.
    The function should skip the pair, continue with subsequent
    logs, and return a complete dict."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    cls = NBAScoringAdapter
    logs = [
        {"date": "2025-10-21", "min": "32", "pts": 22},
        {"date": "",          "min": "30", "pts": 18},   # blank
        {"date": None,        "min": "28", "pts": 14},   # missing
        {"date": "2025-10-15", "min": "31", "pts": 20},
        {"date": "2025-10-13", "min": "33", "pts": 24},
    ]
    # Should not raise.
    info = cls._classify_availability(logs, before_date=None)
    assert isinstance(info, dict)
    assert "status" in info
    assert "restriction_factor" in info


def test_classify_availability_all_blank_dates():
    """Pathological: every log has blank/missing date. The function
    must STILL return a valid dict, not throw."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    cls = NBAScoringAdapter
    logs = [{"date": "", "min": "32", "pts": 22} for _ in range(10)]
    info = cls._classify_availability(logs, before_date=None)
    assert isinstance(info, dict)
    assert info.get("status") is not None


def test_classify_availability_no_logs():
    """Empty log list → 'no_logs' reason, valid neutral state."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    info = NBAScoringAdapter._classify_availability(
        logs=[], before_date=None)
    assert info["reason"] == "no_logs"
    assert info["restriction_factor"] == 1.0
    assert info["status"] == "UNKNOWN"


def test_classify_availability_with_before_date_cutoff():
    """`before_date` cutoff still applies even when some logs have
    blank dates. Empty-string `date` sorts as "" < any real date,
    so the cutoff naturally excludes them — must still not throw."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    logs = [
        {"date": "2025-10-22", "min": "33", "pts": 24},  # excluded
        {"date": "2025-10-20", "min": "32", "pts": 22},
        {"date": "",          "min": "30", "pts": 18},   # blank
        {"date": "2025-10-18", "min": "31", "pts": 21},
    ]
    info = NBAScoringAdapter._classify_availability(
        logs, before_date="2025-10-22")
    assert isinstance(info, dict)


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
            print(f"  ✗ {name} (uncaught exception)")
            traceback.print_exc(limit=2)
    print()
    if failures:
        print(f"  {failures} test(s) FAILED")
        sys.exit(1)
    print(f"  All blank-availability tests PASSED")

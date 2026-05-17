"""Replay regression tests.

Tier 1 fast canaries that lock in the Path A hydration fix and the
single-thread inference guard. All tests should run in < 10s combined
and stay under 500 MB RSS.

Run:
    cd /app/backend && pytest tests/replay/ -v
"""

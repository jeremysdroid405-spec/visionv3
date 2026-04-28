"""
NBA Historical Odds Backfill — The Odds API v4
================================================
Dormant module. Activated only when ODDS_API_KEY is set in the environment
and `run_backfill.py` is explicitly invoked. Has zero side-effects on
production scoring.

Layout:
    schema.py         — Collection bootstrap + indexes for `historical_odds_full`
    client.py         — Async HTTP client (rate-limited, retry-on-429, credit-aware)
    orchestrator.py   — Slate iteration + idempotent MongoDB upsert
    validate_slate.py — Single-slate dry run (lowest-cost validation gate)
    run_backfill.py   — CLI entrypoint for full backfill
"""

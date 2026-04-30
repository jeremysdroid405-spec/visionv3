# Observability — Structured Error Logging

## What this is
A system-level immune response for the single biggest source of regressions:
silently-caught exceptions (`except Exception: pass`) that were hiding bugs
across 76k LOC of services + routes.

## Why it exists
Before 2026-04-30, the codebase had 44 `except [Exception]: pass` handlers
that swallowed errors with no log, no metric, no trace. Every new sport/
schema/field rename silently broke one or more of them, the sync would
still write `status=success`, the UI would serve stale data, and the fix
from three days ago would be back as a regression.

**This system exists so that can never happen again without leaving
evidence.**

## Architecture

```
         caller code
              │
              ▼
    try:
        risky_call()
    except Exception as e:
        log_silent_failure("subsystem.function", e)
              │
              ▼
┌─────────────────────────────────────┐
│ services/observability/error_log.py │
│   log_caught_exception  (async)     │
│   log_silent_failure    (sync)      │
└─────────────────────────────────────┘
              │
              ▼
    error_log collection (Mongo, TTL 14d)
              │
              ▼
    GET /api/v3/admin/errors/summary?hours=24
    GET /api/v3/admin/errors/recent?subsystem=...
```

## Invariants — DO NOT BREAK

1. **The logger never raises.** A logger crash must never cascade into a
   new error. `test_log_caught_exception_never_raises_on_db_failure` enforces this.
2. **TTL index on `ts` is maintained** so `error_log` never grows without
   bound. Enforced by `test_indexes_are_created_idempotently`.
3. **Long tracebacks are truncated** at 16 KB. A single crashing sync
   must not blow out the document limit. Enforced by
   `test_traceback_truncation_respects_doc_limit`.
4. **Silent `except: pass` is banned.** Any new pass-only handler must
   be replaced with `log_silent_failure(...)` at review time. Use
   `scripts/sweep_silent_handlers.py` to enforce idempotently.

## Files

| Path | Purpose |
|---|---|
| `services/observability/error_log.py` | Logger primitive — async + sync variants |
| `services/observability/__init__.py` | Public exports |
| `routes/admin_errors.py` | `/api/v3/admin/errors/{summary,recent}` |
| `scripts/sweep_silent_handlers.py` | Idempotent bulk converter |
| `tests/test_error_log_observability.py` | 5 invariant tests |

## How to add a new handler correctly

❌ **Don't do this:**
```python
try:
    do_thing()
except Exception:
    pass
```

✅ **Do this instead:**
```python
from services.observability import log_silent_failure
try:
    do_thing()
except Exception as e:
    log_silent_failure("my_module.my_func", e,
                       context={"player_id": pid})
```

If you have a `db` handle and want persistent rows (most cases):
```python
from services.observability import log_caught_exception
try:
    await do_thing()
except Exception as e:
    await log_caught_exception(
        db, e,
        subsystem="my_module.my_func",
        sport="mlb",
        context={"player_id": pid, "event_id": eid},
    )
```

## Triage

Weekly: `GET /api/v3/admin/errors/summary?hours=168` — shows top-N
(subsystem, exception_type) pairs by count. Fix the top 3 each week.

If you see a spike in one subsystem, use
`GET /api/v3/admin/errors/recent?subsystem=<name>` to pull the last 50
tracebacks + context for that path.

## Re-running the sweep (idempotent)

```
cd /app/backend && python scripts/sweep_silent_handlers.py
```

- First run on 2026-04-30: 44 → 7 remaining (84% coverage).
- Subsequent runs: 0 changes if no new silent handlers introduced.

The 7 remaining handlers are in files with pre-existing syntax issues
the AST parser couldn't handle; they can be converted manually when
those files are next touched.

## Anti-regression

The sweep script + tests are the immune system. Specifically:

- A PR introducing a new `except: pass` anywhere in services/ or routes/
  is caught by re-running the sweep in CI (or locally) — the count goes
  back up.
- The invariant tests fail loudly if the logger primitive is broken.
- The admin endpoint proves the library works end-to-end in production.

## Related — do not conflate

- `services/observability/shadow_divergence_monitor.py` — unrelated;
  compares final-sport-rt vs final-sport-rt-shadow tag drift. Different
  observability lane.
- `logging.getLogger(...)` — still valid for DEBUG/INFO lines. This
  system is specifically for caught exceptions that would otherwise be
  silently swallowed.

# Test Credentials

## User Login (Dashboard)
- Use the **"Demo Mode"** button on the frontend login page.

## Admin Debug Token (internal / observability)
- Env var: `ADMIN_DEBUG_TOKEN`
- Current value (in `/app/backend/.env`): `pv-debug-b3979964da413c497a079d7b`
- Usage (read-only, admin/debug only):

  ```
  curl -H "X-Admin-Token: pv-debug-b3979964da413c497a079d7b" \
       https://<host>/api/injury-rescore-stats

  curl -H "X-Admin-Token: pv-debug-b3979964da413c497a079d7b" \
       https://<host>/api/full-sync-stats
  ```

- Behaviour (identical for both endpoints):
  - `ADMIN_DEBUG_TOKEN` unset ⇒ endpoint returns **503** (disabled by default).
  - Header missing / wrong ⇒ **401**.
  - Correct token + `/api/injury-rescore-stats` ⇒ **200** with
    `{events_received, recomputes, last_latency_ms, last_players_patched_count, last_trigger}`.
  - Correct token + `/api/full-sync-stats` ⇒ **200** with
    `{last_full_sync_at, last_full_sync_duration_ms, last_full_sync_props_written, last_trigger}`.

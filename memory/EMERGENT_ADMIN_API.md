# Emergent Admin API — Reference

Scoped backend access for E1 agents. Mounted at **`/api/emergent-admin/*`**.

## Auth
- Header: `X-Admin-Token: <token>` (constant-time compared against
  `EMERGENT_ADMIN_TOKEN` from `backend/.env`).
- Optional header: `X-Agent-Id: <agent-name>` (appears in audit log).
- Fail-closed: if the env var is unset, every endpoint returns **503**.

Every authenticated request is appended to `emergent_admin_audit_log`
(`ts, ip, route, action, status_code, params_redacted, response_summary,
agent_id, token_hash`).

---

## Endpoints

### `/auth`
| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/whoami` | Smoke-test token; returns `agent_id`, `token_hash`. |

### `/policy`
| Method | Path | Purpose |
|---|---|---|
| GET | `/policy/` | Dump the live allowlists (collections, jobs, services). |

### `/collections/{coll}`
Honors `policy.PROTECTED_COLLECTIONS` (READ-ONLY) and
`policy.WRITABLE_COLLECTIONS` (R/W). Forbidden Mongo operators
(`$where, $function, $accumulator, $out, $merge`) are rejected at parse-time.

| Method | Path | Purpose |
|---|---|---|
| POST | `/collections/{coll}/find` | `{filter, projection, sort, limit≤2000, skip}` |
| POST | `/collections/{coll}/aggregate` | `{pipeline, limit}` ($out/$merge banned) |
| POST | `/collections/{coll}/count` | `{filter}` |
| POST | `/collections/{coll}/distinct` | `{field, filter}` |
| POST | `/collections/{coll}/insert` | `{docs: [..≤1000]}` — writable only |
| POST | `/collections/{coll}/update` | `{filter, update, upsert, many}` — writable only |
| POST | `/collections/{coll}/delete` | `{filter, many, confirm_token}` — writable only |
| GET  | `/collections/{coll}/indexes` | List index info (no destructive ops). |

`delete` refuses empty filter; `many=true` requires `confirm_token = "I_UNDERSTAND_BULK_DELETE"`.

### `/jobs`
Spawns allowlisted Python modules as subprocesses (no shell, fixed argv).
Captured stdout streams into `emergent_admin_jobs.log` in chunks of 50 lines.

| Method | Path | Purpose |
|---|---|---|
| POST | `/jobs/run` | `{module, args[]}` — both must be in `policy.ALLOWED_JOBS`. |
| GET  | `/jobs` / `/jobs/?status=&limit=` | List recent jobs (log omitted). |
| GET  | `/jobs/{job_id}?include_log=bool` | Single job detail. |
| GET  | `/jobs/{job_id}/log?tail=N` | Tail captured log. |
| POST | `/jobs/{job_id}/cancel` | `{confirm:true}` → SIGTERM the pid. |

### `/configs` — candidate-config workflow
States: `draft → active → archived`. Activating archives any existing active
config of the same `(kind, scope)`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/configs/draft` | `{kind, scope, config, note, parent_id?}` |
| GET  | `/configs/?kind=&scope=&status=&limit=` | List configs. |
| GET  | `/configs/{config_id}` | Single config detail. |
| POST | `/configs/{config_id}/activate` | Activate (archives prior actives). |
| POST | `/configs/{config_id}/rollback` | `{target_config_id, confirm:true}` |

### `/services`
Allowlist: `policy.ALLOWED_SERVICES = {"backend"}`.

| Method | Path | Purpose |
|---|---|---|
| GET  | `/services/status/{service}` | `supervisorctl status …` |
| POST | `/services/restart` | `{service, confirm:true}` → `supervisorctl restart …` |

### `/audit` — read-only
| Method | Path | Purpose |
|---|---|---|
| GET | `/audit/?action=&agent_id=&status_code=&limit=&skip=` | Recent audit entries. |
| GET | `/audit/summary?hours=N` | Action × status_code counts. |

---

## Hard constraints (encoded in code)
- No shell exec, ever (`asyncio.create_subprocess_exec` with `list[str]`).
- Job cwd locked to `/app/backend` (or `/var/www/app/backend`).
- Token comparison: `hmac.compare_digest`.
- Tokens are never persisted; only a 16-char SHA-256 fingerprint is logged.
- Protected collections include raw SGO archive, auth/users, payments, prop-
  scoring SSOT — they are **read-only** through this API.
- Jobs that write to protected destinations are listed with `enabled: false`
  in `policy.ALLOWED_JOBS`.

---

## Smoke test (curl)
```bash
B=http://localhost:8001
T=$EMERGENT_ADMIN_TOKEN
curl -s -H "X-Admin-Token: $T" "$B/api/emergent-admin/auth/whoami"
curl -s -H "X-Admin-Token: $T" "$B/api/emergent-admin/policy/" | jq '.allowed_jobs | keys'
curl -s -X POST -H "X-Admin-Token: $T" -H "Content-Type: application/json" \
  -d '{"module":"scripts.sgo.verify_sgo_player_stats_coverage","args":["--league=MLB"]}' \
  "$B/api/emergent-admin/jobs/run"
```

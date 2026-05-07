#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# P0 Phase 4A — second-run verification harness.
#
# Purpose:
#   Run this exactly once, ~30 minutes after the Phase 4A migration,
#   to prove that:
#     (i)   `edge_pct` STAYS at 0 in Mongo (no writer regressed it)
#     (ii)  `edge_pct` and `vk_edge` STAY out of API tier responses
#     (iii) Ingestion / score / detection freshness SLOs still PASS
#     (iv)  Adaptive engine cycled cleanly through the window
#
# Usage:
#   sudo bash /app/backend/scripts/p0_phase4a_verify.sh
#
# Outputs to stdout. Exit 0 = all pass, non-zero = any failure
# (script never falls back; failures are loud).
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

# Locate env files
ENV_BACKEND=/app/backend/.env
ENV_FRONTEND=/app/frontend/.env
if [[ ! -f "$ENV_BACKEND" ]] || [[ ! -f "$ENV_FRONTEND" ]]; then
    echo "[FAIL] missing .env files" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1090
source "$ENV_BACKEND"
set +a
API_URL=$(grep '^REACT_APP_BACKEND_URL=' "$ENV_FRONTEND" | cut -d '=' -f2- | tr -d '"')

echo "================================================================"
echo "P0 PHASE 4A — VERIFICATION HARNESS"
echo "  T          = $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  api_url    = $API_URL"
echo "  mongo_db   = $DB_NAME"
echo "================================================================"

FAIL=0

# ─── (i) DB-side edge_pct count must stay 0 ────────────────────────
echo ""
echo "[i] DB-side edge_pct presence (must stay 0)"
DB_OUT=$(python3 - <<'PY'
import os, asyncio, json
from motor.motor_asyncio import AsyncIOMotorClient
async def main():
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    out = {}
    for coll in ('nba_prop_scores','mlb_prop_scores','nba_cached_board','mlb_cached_board'):
        n = await db[coll].count_documents({'edge_pct': {'$exists': True}})
        out[coll] = n
    print(json.dumps(out))
asyncio.run(main())
PY
)
echo "  $DB_OUT"
if echo "$DB_OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if all(v==0 for v in d.values()) else 1)"; then
    echo "  [PASS] all 4 collections have 0 docs with edge_pct"
else
    echo "  [FAIL] at least one collection has edge_pct present — a writer regressed" >&2
    FAIL=1
fi

# ─── (ii) API-side edge_pct / vk_edge leak count must stay 0 ─────
echo ""
echo "[ii] API-side legacy field leakage (edge_pct + vk_edge across visible picks)"
for SPORT in nba mlb; do
    TMP=$(mktemp)
    curl -s "${API_URL}/api/v3/ferrari/all?sport=${SPORT}" > "$TMP"
    LEAKS=$(python3 -c "
import json
d = json.load(open('$TMP'))
total = 0; e = 0; v = 0; canon = 0
for tier in ('safe_haven','front_lines','war_zone'):
    for p in (d.get(tier) or {}).get('picks', []) or []:
        total += 1
        if 'edge_pct' in p: e += 1
        if 'vk_edge' in p: v += 1
        if p.get('edge_vs_fair') is not None: canon += 1
print(json.dumps({'picks': total, 'edge_pct_leaks': e, 'vk_edge_leaks': v, 'edge_vs_fair_present': canon}))
")
    rm -f "$TMP"
    echo "  ${SPORT}: ${LEAKS}"
    if echo "$LEAKS" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d['edge_pct_leaks']==0 and d['vk_edge_leaks']==0 else 1)"; then
        echo "    [PASS] no legacy edge field in API"
    else
        echo "    [FAIL] legacy edge field present in API — response stamper regressed" >&2
        FAIL=1
    fi
done

# ─── (iii) Run full SLO check (sections 1, 2, 4 are the bar) ─────
echo ""
echo "[iii] Full production_readiness_slo_check.py"
echo "     (sections 1/2/4 must PASS for Phase 4A bar; 3/5 are out of scope)"
SLO_OUT=$(timeout 60 python3 /app/backend/scripts/production_readiness_slo_check.py 2>&1 || true)
echo "$SLO_OUT" | grep -E '^\[(PASS|FAIL)\]' | sed 's/^/  /'
for SECTION in 1_ingestion_freshness 2_score_freshness 4_detection_source_freshness; do
    if echo "$SLO_OUT" | grep -q "\[PASS\] ${SECTION}"; then
        echo "  [PASS] ${SECTION}"
    else
        echo "  [FAIL] ${SECTION}" >&2
        FAIL=1
    fi
done
# Sections 3 + 5 are tracked but do not gate Phase 4A
if echo "$SLO_OUT" | grep -q "\[FAIL\] 3_tier_freshness"; then
    echo "  [INFO] 3_tier_freshness still FAIL (pre-existing — cached_board has no timestamp; tracked for next session)"
fi
if echo "$SLO_OUT" | grep -q "\[FAIL\] 5_api_correctness"; then
    echo "  [INFO] 5_api_correctness still FAIL (Phase 4B scope — h5_rate/h10_rate/h20_rate/hit_rate/hit_rates/model_hit_rate_* response shims; not P0 Phase 4A)"
fi

# ─── (iv) Watchdog/restart events in observation window ──────────
echo ""
echo "[iv] Watchdog / restart events"
WD_FROZEN=$(grep -cE 'WATCHDOG.*FROZEN|RESTART_STORM' /var/log/supervisor/backend.err.log || true)
if [[ "$WD_FROZEN" == "0" ]]; then
    echo "  [PASS] 0 watchdog FROZEN / RESTART_STORM events in current log"
else
    echo "  [FAIL] watchdog events present: $WD_FROZEN — investigate adaptive engine" >&2
    FAIL=1
fi

# ─── (v) Adaptive callback heartbeat ──────────────────────────────
echo ""
echo "[v] Adaptive sync heartbeat freshness"
HB_OUT=$(python3 - <<'PY'
import os, asyncio, json, datetime as dt
from motor.motor_asyncio import AsyncIOMotorClient
async def main():
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    hb = await db['adaptive_sync_heartbeat'].find_one({'_id':'adaptive_sync'})
    now = dt.datetime.now(dt.timezone.utc)
    out = {}
    if hb and isinstance(hb.get('last_heartbeat_at'), dt.datetime):
        last = hb['last_heartbeat_at']
        if last.tzinfo is None: last = last.replace(tzinfo=dt.timezone.utc)
        out['last_heartbeat_at'] = last.isoformat()
        out['age_s'] = round((now - last).total_seconds(), 1)
        out['next_poll_in_seconds'] = hb.get('next_poll_in_seconds')
    print(json.dumps(out))
asyncio.run(main())
PY
)
echo "  $HB_OUT"
if echo "$HB_OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('age_s',9999) < 600 else 1)"; then
    echo "  [PASS] heartbeat under 600s"
else
    echo "  [FAIL] heartbeat over 600s — engine may be stalled" >&2
    FAIL=1
fi

# ─── Summary ─────────────────────────────────────────────────────
echo ""
echo "================================================================"
if [[ $FAIL -eq 0 ]]; then
    echo "OVERALL: PASS — Phase 4A holds at T+30 (and beyond)"
else
    echo "OVERALL: FAIL — investigate the section(s) flagged above"
fi
echo "================================================================"
exit $FAIL

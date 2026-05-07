#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# P0 Phase 4B — `hit_rate` SSOT cleanup verification harness.
#
# Purpose:
#   Run after Phase 4B to prove that:
#     (i)   API tier responses ship ONLY canonical hit-rate fields
#     (ii)  Canonical fields are 100% present on every visible pick
#     (iii) Phase 4A guarantees still hold (no edge_pct/vk_edge regression)
#     (iv)  Production-readiness SLO §1, §2, §4 still PASS
#     (v)   §5 (api_correctness) now PASSES — that's the Phase 4B win
#     (vi)  Watchdog stable, heartbeat fresh
#
# Usage:
#   sudo bash /app/backend/scripts/p0_phase4b_verify.sh
#
# Exit 0 = all pass, non-zero = any failure.
# ─────────────────────────────────────────────────────────────────────
set -uo pipefail

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
echo "P0 PHASE 4B — VERIFICATION HARNESS (hit_rate cleanup)"
echo "  T          = $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  api_url    = $API_URL"
echo "================================================================"
FAIL=0

# ─── (i) API legacy hit-rate field leakage MUST be 0 ────────────────
echo ""
echo "[i] API-side legacy hit-rate field leakage (must be 0 across all visible picks)"
LEAKS_OUT=$(python3 /app/backend/scripts/p0_phase4b_canonical_audit.py 2>&1 || true)
echo "$LEAKS_OUT" | python3 -c "
import json, sys
text = sys.stdin.read()
# extract last JSON object in stream
last_brace = text.rfind('}')
first_brace = text.find('{')
d = json.loads(text[first_brace:last_brace+1])
import sys
for sport, s in d['by_sport'].items():
    leaks = s['legacy_present']
    bad = [(k,v) for k,v in leaks.items() if v != 0]
    if bad:
        print(f'  [FAIL] {sport}: legacy fields still present: {bad}', file=sys.stderr)
        sys.exit(1)
    else:
        print(f'  [PASS] {sport}: 0 legacy hit-rate fields ({s[\"total_visible\"]} picks audited)')
sys.exit(0)
"
if [[ $? -ne 0 ]]; then FAIL=1; fi

# ─── (ii) Canonical hit-rate fields MUST be 100% present ────────────
echo ""
echo "[ii] Canonical hit-rate field coverage (must be 100% on visible picks)"
echo "$LEAKS_OUT" | python3 -c "
import json, sys
text = sys.stdin.read()
last_brace = text.rfind('}'); first_brace = text.find('{')
d = json.loads(text[first_brace:last_brace+1])
bad = []
for cf, status in d['go_no_go'].items():
    if status.startswith('GO'):
        print(f'  [PASS] {cf}: {status}')
    else:
        print(f'  [FAIL] {cf}: {status}', file=sys.stderr)
        bad.append(cf)
if d.get('missing_canonical_picks'):
    print(f'  [FAIL] {len(d[\"missing_canonical_picks\"])} picks missing canonical fields', file=sys.stderr)
    bad.append('any')
sys.exit(1 if bad else 0)
"
if [[ $? -ne 0 ]]; then FAIL=1; fi

# ─── (iii) Phase 4A guarantees still hold ──────────────────────────
echo ""
echo "[iii] Phase 4A regression check (edge_pct / vk_edge must remain 0)"
for SPORT in nba mlb; do
    TMP=$(mktemp)
    curl -s "${API_URL}/api/v3/ferrari/all?sport=${SPORT}" > "$TMP"
    OUT=$(python3 -c "
import json
d = json.load(open('$TMP'))
e = v = 0
for tier in ('safe_haven','front_lines','war_zone'):
    for p in (d.get(tier) or {}).get('picks', []) or []:
        if 'edge_pct' in p: e += 1
        if 'vk_edge' in p: v += 1
print(f'edge_pct_leaks={e} vk_edge_leaks={v}')
")
    rm -f "$TMP"
    echo "  ${SPORT}: ${OUT}"
    if echo "$OUT" | grep -qE "edge_pct_leaks=0 vk_edge_leaks=0"; then
        echo "    [PASS] no Phase 4A regression"
    else
        echo "    [FAIL] Phase 4A regression detected" >&2
        FAIL=1
    fi
done

# ─── (iv) Production SLO §1, §2, §4 must PASS ──────────────────────
echo ""
echo "[iv] Production-readiness SLO §1 / §2 / §4 (Phase 4A bar)"
SLO_OUT=$(timeout 60 python3 /app/backend/scripts/production_readiness_slo_check.py 2>&1 || true)
for SECTION in 1_ingestion_freshness 2_score_freshness 4_detection_source_freshness; do
    if echo "$SLO_OUT" | grep -q "\[PASS\] ${SECTION}"; then
        echo "  [PASS] ${SECTION}"
    else
        echo "  [FAIL] ${SECTION}" >&2
        FAIL=1
    fi
done

# ─── (v) SLO §5 (api_correctness) is the Phase 4B win ──────────────
echo ""
echo "[v] Production-readiness SLO §5 api_correctness (the Phase 4B win)"
if echo "$SLO_OUT" | grep -q "\[PASS\] 5_api_correctness"; then
    echo "  [PASS] 5_api_correctness"
else
    echo "  [FAIL] 5_api_correctness — at least one legacy alias still in API" >&2
    echo "$SLO_OUT" | grep -A3 "\[FAIL\] 5_api_correctness" | head -20 | sed 's/^/    /'
    FAIL=1
fi

# ─── (vi) Watchdog / heartbeat ─────────────────────────────────────
echo ""
echo "[vi] Watchdog / heartbeat health"
WD=$(grep -cE 'WATCHDOG.*FROZEN|RESTART_STORM' /var/log/supervisor/backend.err.log 2>/dev/null)
WD=${WD:-0}
if [[ "$WD" -eq 0 ]] 2>/dev/null; then
    echo "  [PASS] 0 watchdog FROZEN / RESTART_STORM events"
else
    echo "  [FAIL] watchdog events present: $WD" >&2
    FAIL=1
fi
HB_AGE=$(python3 -c "
import os, asyncio, datetime as dt
from motor.motor_asyncio import AsyncIOMotorClient
async def main():
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    hb = await db['adaptive_sync_heartbeat'].find_one({'_id':'adaptive_sync'})
    now = dt.datetime.now(dt.timezone.utc)
    if hb and isinstance(hb.get('last_heartbeat_at'), dt.datetime):
        last = hb['last_heartbeat_at']
        if last.tzinfo is None: last = last.replace(tzinfo=dt.timezone.utc)
        print(int((now-last).total_seconds()))
    else:
        print(99999)
asyncio.run(main())
")
echo "  heartbeat_age_s = ${HB_AGE}"
if [[ "$HB_AGE" -lt 600 ]]; then
    echo "  [PASS] heartbeat under 600s"
else
    echo "  [FAIL] heartbeat over 600s" >&2
    FAIL=1
fi

# ─── Summary ───────────────────────────────────────────────────────
echo ""
echo "================================================================"
if [[ $FAIL -eq 0 ]]; then
    echo "OVERALL: PASS — Phase 4B holds. SSOT enforced for hit-rate."
else
    echo "OVERALL: FAIL — investigate the section(s) flagged above"
fi
echo "================================================================"
exit $FAIL

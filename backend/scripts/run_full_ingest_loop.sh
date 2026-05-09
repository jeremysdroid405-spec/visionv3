#!/usr/bin/env bash
# Resilient driver for the 30-day ingest. Retries until all checkpoints
# are terminal. Each retry resumes from `replay_ingest_progress`.
set -u
cd /app
set -a; source backend/.env; set +a

MAX_TRIES=20
SLEEP_BETWEEN=8
LOG=/tmp/full_ingest_30day_loop.log
OUT=/app/audit_reports/replay_full_ingest_2024-02-01_to_2024-03-01.json

: > "$LOG"
echo "[loop] starting at $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"

for i in $(seq 1 $MAX_TRIES); do
  echo "" | tee -a "$LOG"
  echo "[loop] === attempt $i/$MAX_TRIES at $(date -u +%H:%M:%S) ===" \
      | tee -a "$LOG"
  python /app/backend/scripts/run_full_ingest.py --execute \
      --start 2024-02-01 --end 2024-03-01 \
      --label "phase1_30day_attempt_${i}" \
      --out "$OUT" \
      --telemetry-every 50 --safety-every 999999 \
      >> "$LOG" 2>&1
  RC=$?
  echo "[loop] attempt $i exit code: $RC" | tee -a "$LOG"

  STATUS=$(python3 - <<'PY'
import os, asyncio
from motor.motor_asyncio import AsyncIOMotorClient
async def go():
    try:
        c = AsyncIOMotorClient(os.environ['MONGO_URL'],
                                serverSelectionTimeoutMS=5000)
        db = c[os.environ['DB_NAME']]
        agg = [{'$group': {'_id': '$status', 'n': {'$sum': 1}}}]
        out = {}
        async for d in db['replay_ingest_progress'].aggregate(agg):
            out[d['_id']] = d['n']
        print(' '.join(f"{k}={v}" for k, v in out.items()))
        nonterminal = (out.get('pending', 0) + out.get('in_flight', 0))
        print('NONTERMINAL=' + str(nonterminal))
        c.close()
    except Exception as e:
        print('CHECK_ERROR=' + repr(e))
asyncio.run(go())
PY
  )
  echo "[loop] progress: $STATUS" | tee -a "$LOG"
  if echo "$STATUS" | grep -q "NONTERMINAL=0"; then
    echo "[loop] all checkpoints terminal — stopping." | tee -a "$LOG"
    break
  fi
  sleep $SLEEP_BETWEEN
done

echo "" | tee -a "$LOG"
echo "[loop] finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"

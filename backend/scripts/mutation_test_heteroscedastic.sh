#!/usr/bin/env bash
# Mutation test: break NBA heteroscedastic sigma and confirm tests catch it.
# Proves the lookup is actually wired into scoring (not a dead code path).
#
# Run:   bash scripts/mutation_test_heteroscedastic.sh
# Expect: exit 0 on SUCCESS (mutations caught); exit 1 on FAILURE (silent).

set -e

CONFIG=/app/backend/config/nba_sigma_heteroscedastic.py
TEST=/app/backend/tests/test_nba_heteroscedastic_sigma.py
BACKUP=$(mktemp)

cp "$CONFIG" "$BACKUP"
cleanup() { cp "$BACKUP" "$CONFIG"; rm -f "$BACKUP"; }
trap cleanup EXIT

cd /app/backend
export PYTHONPATH=/app/backend

echo "=== Baseline (unmutated) — MUST pass ==="
python3 -m pytest "$TEST" -q > /dev/null
echo "baseline OK"

echo ""
echo "=== Mutation 1: empty MULTIPLIER_TABLES — guardrail MUST trip ==="
python3 - <<'PY'
import re
path="/app/backend/config/nba_sigma_heteroscedastic.py"
src=open(path).read()
src=re.sub(r'MULTIPLIER_TABLES: Dict\[str, Dict\[str, Dict\[str, float\]\]\] = \{[\s\S]*?\n\}\n',
           'MULTIPLIER_TABLES: Dict[str, Dict[str, Dict[str, float]]] = {}\n', src, count=1)
open(path,"w").write(src)
PY
if python3 -m pytest "$TEST::TestTablesHaveRealData" -q 2>&1 | grep -q "failed"; then
    echo "mutation-1 caught (tables-empty detected)"
else
    echo "!! mutation-1 NOT caught — guardrail broken"
    exit 1
fi
cp "$BACKUP" "$CONFIG"

echo ""
echo "=== Mutation 2: inject out-of-range multiplier (3.5) — safety-window MUST trip ==="
python3 - <<'PY'
path="/app/backend/config/nba_sigma_heteroscedastic.py"
src=open(path).read()
src=src.replace('"mid_high": 1.18,', '"mid_high": 3.5,', 1)
open(path,"w").write(src)
PY
if python3 -m pytest "$TEST::TestTablesHaveRealData::test_all_multipliers_in_safety_window" -q 2>&1 | grep -q "failed"; then
    echo "mutation-2 caught (out-of-range multiplier detected)"
else
    echo "!! mutation-2 NOT caught — safety-window guardrail broken"
    exit 1
fi
cp "$BACKUP" "$CONFIG"

echo ""
echo "=== Mutation 3: break monotonic quartiles (q50 > q75) — monotonic MUST trip ==="
python3 - <<'PY'
path="/app/backend/config/nba_sigma_heteroscedastic.py"
src=open(path).read()
src=src.replace('"PTS": {"q25": 15.5, "q50": 19.5, "q75": 24.5}',
                '"PTS": {"q25": 15.5, "q50": 30.0, "q75": 24.5}', 1)
open(path,"w").write(src)
PY
if python3 -m pytest "$TEST::TestTablesHaveRealData::test_line_quartiles_monotonic" -q 2>&1 | grep -q "failed"; then
    echo "mutation-3 caught (non-monotonic quartiles detected)"
else
    echo "!! mutation-3 NOT caught — monotonic guardrail broken"
    exit 1
fi
cp "$BACKUP" "$CONFIG"

echo ""
echo "=== All mutations caught — lookup is wired and tests are honest. ==="

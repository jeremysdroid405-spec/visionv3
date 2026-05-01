#!/bin/bash
# =============================================================================
# MUTATION TESTING — NBA VK2 v2_5yr_weighted_pruned52
# =============================================================================
# Mirror of `mutation_test_v3.sh` for the NBA production model. Injects
# faults into the live serving path and asserts the test suite catches
# every one. Restores original code via bash trap.
#
# Mutations:
#   N1  Strip VK2 model_version stamp
#   N2  Force projection to constant (prove integration tests catch it)
#   N3  Disable negative-projection clamp (regression of the bug we just fixed)
#   N4  Break vk2 model loader (load no models)
#   N5  Break feature builder (return None always)
#   N6  Force sigma=0 for all stats (sigma_invalid path)
#   N7  Sabotage PRA additivity (corrupt one of PTS/REB/AST predictions)
#
# Usage:
#   PYTHON=/root/.venv/bin/python bash /app/backend/scripts/mutation_test_nba_vk2.sh
# =============================================================================

set -u

cd /app/backend
ADAPTER=services/scoring/adapters/nba_scoring.py
FEATBUILD=services/scoring/nba_vk2_features.py
BACKUP_DIR=/tmp/mutation_backup_nba_$$
mkdir -p "$BACKUP_DIR"

PYTHON=${PYTHON:-/root/.venv/bin/python}
REGRESSION_SUITE="tests/test_nba_vk2_validation.py tests/test_nba_vk2_production_integration.py tests/test_nba_vk2_calibration.py"
PASS_CHAR="✓"
FAIL_CHAR="✗"
GREEN="\033[0;32m"
RED="\033[0;31m"
NC="\033[0m"
TOTAL=0
DETECTED=0
MUTATION_FAILURES=()

backup() {
    cp "$1" "$BACKUP_DIR/$(basename $1).orig"
}

restore_all() {
    for f in $BACKUP_DIR/*.orig; do
        [ -e "$f" ] || continue
        target="$(basename $f .orig)"
        if [ "$target" = "nba_scoring.py" ]; then
            cp "$f" "$ADAPTER"
        elif [ "$target" = "nba_vk2_features.py" ]; then
            cp "$f" "$FEATBUILD"
        fi
    done
}
trap "restore_all; rm -rf $BACKUP_DIR" EXIT

backup "$ADAPTER"
backup "$FEATBUILD"

run_mutation() {
    local name="$1"; shift
    local target_tests="$1"; shift
    TOTAL=$((TOTAL + 1))
    echo ""
    echo "─── Mutation $name ───"
    timeout 90 $PYTHON -m pytest $target_tests -x --tb=no -q > /tmp/mut_nba_$$.txt 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        DETECTED=$((DETECTED + 1))
        echo -e "  ${GREEN}${PASS_CHAR} DETECTED${NC} — tests failed as expected"
        grep -E "FAILED|AssertionError" /tmp/mut_nba_$$.txt | head -3 | sed 's/^/    /'
    else
        echo -e "  ${RED}${FAIL_CHAR} NOT DETECTED${NC} — tests still passed"
        MUTATION_FAILURES+=("$name")
    fi
    rm -f /tmp/mut_nba_$$.txt
    cp "$BACKUP_DIR/nba_scoring.py.orig" "$ADAPTER"
    cp "$BACKUP_DIR/nba_vk2_features.py.orig" "$FEATBUILD"
}


# =============================================================================
# N1 — Strip VK2 model_version stamp
# Expected: PROD-NBA-INT-08 + REQ-NBA-1 fail
# =============================================================================
N1() {
    $PYTHON -c "
with open('$ADAPTER') as f: src = f.read()
new = src.replace(
    '\"version\": payload.get(\"version\"),',
    '\"version\": \"BROKEN\",',
    1
)
assert new != src, 'N1 patch failed'
with open('$ADAPTER','w') as f: f.write(new)
"
    run_mutation "N1: strip VK2 version stamp" \
        "tests/test_nba_vk2_validation.py::test_nba_vk2_live_metrics_consistent tests/test_nba_vk2_production_integration.py::test_prod_nba_int_08_model_version"
}


# =============================================================================
# N2 — Force projection to insane constant
# Expected: blow-up canary catches it (μ > 4× L20)
# =============================================================================
N2() {
    $PYTHON -c "
with open('$ADAPTER') as f: src = f.read()
new = src.replace(
    'projection = float(m[\"model\"].predict(row_s)[0])',
    'projection = 99.0  # MUTATION: insane constant',
    1
)
assert new != src, 'N2 patch failed'
with open('$ADAPTER','w') as f: f.write(new)
"
    run_mutation "N2: project=99 constant" \
        "tests/test_nba_vk2_calibration.py::test_prod_nba_cal_02_no_blowup_in_sample tests/test_nba_vk2_calibration.py::test_prod_nba_cal_01_mu_distribution"
}


# =============================================================================
# N3 — Disable the negative-projection clamp (post-intercept)
# Expected: predicted_nonneg test fails (low-volume players + intercept
# adjustment can push projection negative).
# =============================================================================
N3() {
    $PYTHON -c "
with open('$ADAPTER') as f: src = f.read()
# Target the post-intercept clamp block.
target = '        if projection < 0:\n            projection = 0.0'
new = src.replace(target, '        # MUTATION: clamp disabled\n        pass', 1)
assert new != src, 'N3 patch failed'
with open('$ADAPTER','w') as f: f.write(new)
"
    run_mutation "N3: disable negative-clamp" \
        "tests/test_nba_vk2_production_integration.py::test_prod_nba_int_07_negative_clamp_deterministic"
}


# =============================================================================
# N4 — Break model loader (load no models)
# Expected: every integration test fails
# =============================================================================
N4() {
    $PYTHON -c "
with open('$ADAPTER') as f: src = f.read()
new = src.replace(
    '            self._vk2_models[stat] = {',
    '            continue  # MUTATION: skip load\n            self._vk2_models[stat] = {',
    1
)
assert new != src, 'N4 patch failed'
with open('$ADAPTER','w') as f: f.write(new)
"
    run_mutation "N4: break model loader" \
        "tests/test_nba_vk2_validation.py::test_nba_vk2_live_models_loaded tests/test_nba_vk2_production_integration.py::test_prod_nba_int_08_model_version"
}


# =============================================================================
# N5 — Break feature builder (return None always)
# Expected: feature_build_failed → coverage / schema tests fail
# =============================================================================
N5() {
    $PYTHON -c "
with open('$FEATBUILD') as f: src = f.read()
new = src.replace(
    'def build_features(',
    'def build_features(*args, **kwargs):\n    return None  # MUTATION\n\ndef build_features_orig(',
    1
)
assert new != src, 'N5 patch failed'
with open('$FEATBUILD','w') as f: f.write(new)
"
    run_mutation "N5: feature_builder returns None" \
        "tests/test_nba_vk2_production_integration.py::test_prod_nba_int_06_per_stat_coverage tests/test_nba_vk2_production_integration.py::test_prod_nba_int_01_vk2_schema"
}


# =============================================================================
# N6 — Force sigma=0 for all stats
# Expected: sigma_invalid → schema test catches missing p_over
#           sigma_matches_rmse fails too
# =============================================================================
N6() {
    $PYTHON -c "
with open('$ADAPTER') as f: src = f.read()
new = src.replace(
    '\"sigma\": float(payload[\"residual_sigma_empirical\"]),',
    '\"sigma\": 0.0,  # MUTATION',
    1
)
assert new != src, 'N6 patch failed'
with open('$ADAPTER','w') as f: f.write(new)
"
    run_mutation "N6: force sigma=0" \
        "tests/test_nba_vk2_calibration.py::test_prod_nba_cal_03_sigma_matches_rmse tests/test_nba_vk2_production_integration.py::test_prod_nba_int_01_vk2_schema"
}


# =============================================================================
# N7 — Sabotage one of PTS/REB/AST → break PRA additivity
# Expected: PRA correlation test fails
# =============================================================================
N7() {
    $PYTHON -c "
with open('$ADAPTER') as f: src = f.read()
# Multiply PTS projection by random factor breaking the PRA correlation
new = src.replace(
    'projection = float(m[\"model\"].predict(row_s)[0])',
    'import random as _r; projection = float(m[\"model\"].predict(row_s)[0]) * (_r.uniform(0.1,2.5) if stat_type==\"PTS\" else 1.0)',
    1
)
assert new != src, 'N7 patch failed'
with open('$ADAPTER','w') as f: f.write(new)
"
    run_mutation "N7: corrupt PTS predictions" \
        "tests/test_nba_vk2_calibration.py::test_prod_nba_cal_04_pra_additive_correlation tests/test_nba_vk2_production_integration.py::test_prod_nba_int_02_determinism"
}


echo "=========================================="
echo "NBA VK2 v2_5yr_weighted_pruned52 Mutation Test Battery"
echo "=========================================="
N1
N2
N3
N4
N5
N6
N7

restore_all

echo ""
echo "─── Final regression sanity (post-restore) ───"
$PYTHON -m pytest $REGRESSION_SUITE --tb=no -q > /tmp/final_nba_$$.txt 2>&1
final_rc=$?
if [ $final_rc -eq 0 ]; then
    final_pass=$(grep -E "passed" /tmp/final_nba_$$.txt | tail -1)
    echo -e "  ${GREEN}${PASS_CHAR}${NC} all regressions pass post-restore: $final_pass"
else
    echo -e "  ${RED}${FAIL_CHAR}${NC} restore failed!"
    cat /tmp/final_nba_$$.txt | tail -10
fi
rm -f /tmp/final_nba_$$.txt

echo ""
echo "=========================================="
echo "MUTATION TEST RESULT: $DETECTED / $TOTAL detected"
echo "=========================================="
if [ ${#MUTATION_FAILURES[@]} -gt 0 ]; then
    echo "UNDETECTED MUTATIONS:"
    for m in "${MUTATION_FAILURES[@]}"; do
        echo "  - $m"
    done
    exit 1
fi
if [ $final_rc -ne 0 ]; then
    exit 2
fi
exit 0

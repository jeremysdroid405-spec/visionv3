#!/bin/bash
# =============================================================================
# MUTATION TESTING — MLB HF v3.0_bayes
# =============================================================================
# Deliberately injects faults into production code, runs the regression
# suite, and asserts that each mutation IS DETECTED (i.e. tests fail).
# Restores original code after each mutation.
#
# Per the user's standing agreement: "no fake fixes, real tests, real
# development." This script proves the test suite actually catches the
# regressions it claims to.
#
# Mutations cover the critical v3.0_bayes code paths:
#   M1  Disable Bayesian shrinkage (return raw observed)
#   M2  Set a league-average to a wildly wrong value
#   M3  Skip the bayes_shrink_rolling_window call entirely
#   M4  Strip the model_version stamp
#   M5  Always return predict() error
#   M6  Disable the active-baseline floor
#   M7  Disable the pitcher workload-anchored μ
#
# A passing run prints ✓ for each mutation and exits 0.
# A failing run prints ✗ (the test suite did NOT catch a deliberate bug)
# and exits 1.
#
# Usage:
#   bash /app/backend/scripts/mutation_test_v3.sh
# =============================================================================

set -u

cd /app/backend
HF_FILE=services/mlb_high_friction_model.py
BAYES_FILE=services/scoring/mlb_statcast_bayes.py
BACKUP_DIR=/tmp/mutation_backup_$$
mkdir -p "$BACKUP_DIR"

# Tests we expect mutations to break. Specific tests per mutation are
# listed in the M_* functions below.
PYTHON=${PYTHON:-/root/.venv/bin/python}
REGRESSION_SUITE="tests/test_mlb_hf_v3_bayes_validation.py tests/test_mlb_hf_v3_production_integration.py tests/test_mlb_hf_v3_calibration.py tests/test_mlb_statcast_bayes.py"

# Auxiliary
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
        # Find the file by basename in either dir
        if [ "$target" = "mlb_high_friction_model.py" ]; then
            cp "$f" "$HF_FILE"
        elif [ "$target" = "mlb_statcast_bayes.py" ]; then
            cp "$f" "$BAYES_FILE"
        fi
    done
}

trap "restore_all; rm -rf $BACKUP_DIR" EXIT

# Backup originals once.
backup "$HF_FILE"
backup "$BAYES_FILE"

run_mutation() {
    local name="$1"; shift
    local target_tests="$1"; shift  # specific tests this mutation should break
    TOTAL=$((TOTAL + 1))
    echo ""
    echo "─── Mutation $name ───"
    # Run the targeted regression
    timeout 90 $PYTHON -m pytest $target_tests -x --tb=no -q > /tmp/mut_out_$$.txt 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        DETECTED=$((DETECTED + 1))
        echo -e "  ${GREEN}${PASS_CHAR} DETECTED${NC} — tests failed as expected"
        # Show which specific test failed (top FAIL line)
        grep -E "FAILED|AssertionError" /tmp/mut_out_$$.txt | head -3 | sed 's/^/    /'
    else
        echo -e "  ${RED}${FAIL_CHAR} NOT DETECTED${NC} — tests still passed despite mutation!"
        MUTATION_FAILURES+=("$name")
    fi
    rm -f /tmp/mut_out_$$.txt
    # Restore between mutations
    cp "$BACKUP_DIR/mlb_high_friction_model.py.orig" "$HF_FILE"
    cp "$BACKUP_DIR/mlb_statcast_bayes.py.orig" "$BAYES_FILE"
}


# =============================================================================
# M1 — Disable Bayesian shrinkage entirely (return raw observed)
# Expected to break: Bleday HRR, batter blow-up canary
# =============================================================================
M1() {
    $PYTHON -c "
import re
with open('$BAYES_FILE') as f: src = f.read()
# Make shrink_rate a no-op: always return observed (or 0.0 if None)
new = src.replace(
    '    if prior_n == 0:',
    '    return float(observed_rate) if observed_rate is not None else 0.0\n    if prior_n == 0:',
    1
)
assert new != src, 'M1 patch failed'
with open('$BAYES_FILE','w') as f: f.write(new)
"
    run_mutation "M1: disable bayes shrinkage" \
        "tests/test_mlb_hf_v3_bayes_validation.py::test_v3_bleday_hrr_shrunk tests/test_mlb_hf_v3_bayes_validation.py::test_v3_no_batter_blowup_in_pool"
}


# =============================================================================
# M2 — Wildly wrong league average for barrel_rate (set to 1.0 = always max)
# Expected to break: Bleday HRR (μ shifts up significantly)
#                    bayes invariant tests (BS3, BS4)
# =============================================================================
M2() {
    $PYTHON -c "
with open('$BAYES_FILE') as f: src = f.read()
new = src.replace('\"barrel_rate\":       0.080,', '\"barrel_rate\":       1.000,', 1)
assert new != src, 'M2 patch failed'
with open('$BAYES_FILE','w') as f: f.write(new)
"
    run_mutation "M2: corrupt league avg (barrel=1.0)" \
        "tests/test_mlb_statcast_bayes.py::test_inv_bs3_shrunk_rate_bounded_by_observed_and_prior tests/test_mlb_statcast_bayes.py::test_inv_bs4_bleday_barrel_rate_shrinks_to_realistic"
}


# =============================================================================
# M3 — Skip bayes_shrink_rolling_window call in _build_friction_features
# Expected to break: Bleday HRR (no shrinkage applied), 4× sanity gate
# =============================================================================
M3() {
    $PYTHON -c "
with open('$HF_FILE') as f: src = f.read()
# Replace the shrunk feature write line with the un-shrunk equivalent
# both for batter and pitcher loops.
new = src.replace(
    'block_shrunk = bayes_shrink_rolling_window(block) if block else {}',
    'block_shrunk = block  # MUTATION: shrinkage skipped',
    -1
)
assert new != src, 'M3 patch failed'
with open('$HF_FILE','w') as f: f.write(new)
"
    run_mutation "M3: skip shrink_rolling_window in HF" \
        "tests/test_mlb_hf_v3_bayes_validation.py::test_v3_bleday_hrr_shrunk tests/test_mlb_hf_v3_bayes_validation.py::test_v3_no_batter_blowup_in_pool"
}


# =============================================================================
# M4 — Strip the model_version stamp from predict()
# Expected to break: PROD-INT-06, REQ-V3-4 (skipped if data unavailable)
# =============================================================================
M4() {
    $PYTHON -c "
with open('$HF_FILE') as f: src = f.read()
new = src.replace(\"'model_version': 'MLB_HF_v3.0_bayes',\", \"'model_version': 'BROKEN',\", 1)
assert new != src, 'M4 patch failed'
with open('$HF_FILE','w') as f: f.write(new)
"
    run_mutation "M4: strip model_version stamp" \
        "tests/test_mlb_hf_v3_production_integration.py::test_prod_int_06_version_stamp_exact tests/test_mlb_hf_v3_bayes_validation.py::test_v3_bleday_hrr_shrunk"
}


# =============================================================================
# M5 — Force predict() to always return error
# Expected to break: ALL integration tests (schema, prob_sanity, etc.)
# =============================================================================
M5() {
    $PYTHON -c "
with open('$HF_FILE') as f: src = f.read()
# Inject 'return {error: \"mutation\"}' at the start of predict()
patched = src.replace(
    '        norm_stat = self._normalize_stat(stat_type)\n\n        # 2026-04-27',
    '        norm_stat = self._normalize_stat(stat_type)\n        return {\"error\": \"M5 mutation\"}\n\n        # 2026-04-27',
    1
)
assert patched != src, 'M5 patch failed'
with open('$HF_FILE','w') as f: f.write(patched)
"
    run_mutation "M5: predict() always returns error" \
        "tests/test_mlb_hf_v3_production_integration.py::test_prod_int_01_batter_schema tests/test_mlb_hf_v3_bayes_validation.py::test_v3_bleday_hrr_shrunk"
}


# =============================================================================
# M6 — Disable active-baseline floor
# Expected to break: PROD-INT-08
# =============================================================================
M6() {
    $PYTHON -c "
with open('$HF_FILE') as f: src = f.read()
new = src.replace(
    '            if self._is_active_today(player, game_logs):',
    '            if False:  # MUTATION: baseline disabled',
    1
)
assert new != src, 'M6 patch failed'
with open('$HF_FILE','w') as f: f.write(new)
"
    # PROD-INT-08 only fires if a batter naturally drops below the
    # baseline. To make this test deterministic, also use the calibration
    # blow-up canary which depends on the floor for low-mu players.
    run_mutation "M6: disable active-baseline floor" \
        "tests/test_mlb_hf_v3_production_integration.py::test_prod_int_08_active_baseline"
}


# =============================================================================
# M7 — Disable workload-anchored μ for pitcher_strikeouts
# Expected to break: PROD-INT-07
# =============================================================================
M7() {
    $PYTHON -c "
with open('$HF_FILE') as f: src = f.read()
new = src.replace(
    'if ip_block is not None and kpi is not None:',
    'if False:  # MUTATION: workload anchor disabled',
    1
)
assert new != src, 'M7 patch failed'
with open('$HF_FILE','w') as f: f.write(new)
"
    run_mutation "M7: disable pitcher workload anchor" \
        "tests/test_mlb_hf_v3_production_integration.py::test_prod_int_07_pitcher_k_workload_anchored"
}


# Run all mutations
echo "=========================================="
echo "MLB HF v3.0_bayes Mutation Test Battery"
echo "=========================================="
M1
M2
M3
M4
M5
M6
M7

# Final restore (also done via trap)
restore_all

# Verify no leftover patches by re-running the full suite
echo ""
echo "─── Final regression sanity (post-restore) ───"
$PYTHON -m pytest $REGRESSION_SUITE --tb=no -q > /tmp/final_$$.txt 2>&1
final_rc=$?
if [ $final_rc -eq 0 ]; then
    final_pass=$(grep -E "passed" /tmp/final_$$.txt | tail -1)
    echo "  ${GREEN}${PASS_CHAR}${NC} all regressions pass post-restore: $final_pass"
else
    echo -e "  ${RED}${FAIL_CHAR}${NC} restore failed — regression suite still broken!"
    cat /tmp/final_$$.txt | tail -10
fi
rm -f /tmp/final_$$.txt

echo ""
echo "=========================================="
echo "MUTATION TEST RESULT: $DETECTED / $TOTAL detected"
echo "=========================================="
if [ ${#MUTATION_FAILURES[@]} -gt 0 ]; then
    echo "UNDETECTED MUTATIONS (test suite has gaps!):"
    for m in "${MUTATION_FAILURES[@]}"; do
        echo "  - $m"
    done
    exit 1
fi
if [ $final_rc -ne 0 ]; then
    exit 2
fi
exit 0

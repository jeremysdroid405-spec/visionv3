#!/usr/bin/env bash
# Mutation test harness for the universal L5 sub-gate + Safe Haven 80
# floor (2026-05-01).
#
# Strategy: temporarily flip a single byte / constant in production
# code, verify pytest FAILS as expected, then revert. A passing
# mutation harness proves the test suite actually constrains the
# behaviour (instead of just decorating it).
#
# Run: bash backend/scripts/mutation_test_l5_subgate.sh
# Required: working tree clean for the touched files (we revert via
# git checkout -- {file}).

set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1
ROOT="$(pwd)"

ENGINE="$ROOT/backend/services/scoring/gates/engine.py"
THRESH="$ROOT/backend/services/scoring/gates/thresholds.py"
NBA="$ROOT/backend/services/scoring/adapters/nba_scoring.py"
TESTS="tests/test_l5_subgate_and_floors.py tests/test_hit_rate_subwindow_plumbing.py"

PASS_COUNT=0
FAIL_COUNT=0
TOTAL=0

run_mutation() {
  local name="$1"
  local file="$2"
  local sed_expr="$3"
  TOTAL=$((TOTAL + 1))
  echo
  echo "── Mutation [$TOTAL]: $name"
  cp "$file" "$file.bak"
  sed -i "$sed_expr" "$file" || {
    echo "  sed FAILED — bug in mutation script."
    mv "$file.bak" "$file"
    FAIL_COUNT=$((FAIL_COUNT + 1)); return
  }
  if diff -q "$file" "$file.bak" >/dev/null 2>&1; then
    echo "  ❌ no-op mutation — sed did not change the file."
    mv "$file.bak" "$file"
    FAIL_COUNT=$((FAIL_COUNT + 1)); return
  fi
  pushd "$ROOT/backend" >/dev/null
  if python -m pytest $TESTS -q 2>&1 | tail -3 | grep -qE "passed|0 failed"; then
    OUT=$(python -m pytest $TESTS -q 2>&1 | tail -3)
    if echo "$OUT" | grep -qE "failed|error"; then
      echo "  ✅ caught (tests failed as expected)"
      PASS_COUNT=$((PASS_COUNT + 1))
    else
      echo "  ❌ MISSED — mutation slipped past the test suite!"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
  else
    echo "  ✅ caught (tests failed as expected)"
    PASS_COUNT=$((PASS_COUNT + 1))
  fi
  popd >/dev/null
  mv "$file.bak" "$file"
}

echo "=== Mutation test harness — L5 sub-gate + Safe Haven 80 ==="

# 1. Safe Haven floor flip 80 → 75. Should fail
#    `test_safe_haven_floor_is_80`.
run_mutation \
  "Safe Haven floor 80 → 75" \
  "$THRESH" \
  's|"hit_rate_gate":  {"min": 80.0, "window": "default"},|"hit_rate_gate":  {"min": 75.0, "window": "default"},|'

# 2. Flip the L5 sub-gate comparator < to > (always passes).
#    Should fail test_l5_below_floor_fails_safe_haven.
run_mutation \
  "L5 sub-gate LT to GT inverts comparison" \
  "$ENGINE" \
  's|and m.hit_rate_l5 < min_val|and m.hit_rate_l5 > min_val|'

# 3. Disable the sub-gate entirely (and m.hit_rate_l5 is not None to False).
#    Should fail test_l5_below_floor_fails_safe_haven.
run_mutation \
  "L5 sub-gate is-not-None to is-None disables the check" \
  "$ENGINE" \
  's|and m.hit_rate_l5 is not None|and m.hit_rate_l5 is None|'

# 4. NBA strict-denominator: window=20 → window=len(logs).
#    Should fail `test_nba_strict_20_window_when_20_logs_available`
#    (the variable-denom bug is back).
run_mutation \
  "NBA strict 20 window → len(logs_sorted)" \
  "$NBA" \
  's|chosen_window = 20|chosen_window = min(20, len(logs_sorted))|'

# 5. NBA strict 10 fallback removed — flip the >=10 branch to >=8.
#    Less strict means players with 8 logs would still get a value,
#    breaking `test_nba_insufficient_sample_under_10_logs`.
run_mutation \
  "NBA insufficient-sample threshold 10 → 8" \
  "$NBA" \
  's|elif len(logs_sorted) >= 10:|elif len(logs_sorted) >= 8:|'

echo
echo "=== Summary ==="
echo "  Mutations caught: $PASS_COUNT / $TOTAL"
echo "  Mutations missed: $FAIL_COUNT"
if [ "$FAIL_COUNT" -eq 0 ]; then
  echo "  🟢 ALL MUTATIONS CAUGHT — test suite has full constraint coverage."
  exit 0
else
  echo "  🔴 ONE OR MORE MUTATIONS SLIPPED PAST — tests must be tightened."
  exit 1
fi

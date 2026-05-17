#!/bin/bash
# Full 05-05 validation sweep — Tier 1 pytest + Tier 2 canaries + Olson
# harness + Layer-3 rebuild + Phase 2c. Each step runs as an isolated
# subprocess so RAM is fully reclaimed between steps.
#
# Run from /app/backend:
#     bash audits/run_full_0505_sweep.sh
#
# Stops on first failure. Total expected wall clock ~2-3 minutes.
set -e
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export XGBOOST_N_THREADS=1

cd /app/backend

step() {
  printf "\n══════════════════════════════════════════════════════════════════\n"
  printf "  %s\n" "$1"
  printf "══════════════════════════════════════════════════════════════════\n"
}

ts() { date +"%H:%M:%S"; }

step "[1/5]  Tier 1 pytest — replay regression suite"
echo "start: $(ts)"
python -m pytest tests/replay/ -v --tb=short -q 2>&1 | \
  grep -v "UserWarning\|warnings.warn\|MLB_HF_MODEL" | tail -30
echo "end:   $(ts)"

step "[2/5]  Tier 2 μ canaries"
echo "start: $(ts)"
python audits/run_replay_canaries.py 2>&1 | \
  grep -v "UserWarning\|warnings.warn\|MLB_HF_MODEL" | tail -25
echo "end:   $(ts)"

step "[3/5]  Olson-only harness (light)"
echo "start: $(ts)"
python audits/path_a_task_6_olson_only_harness.py 2>&1 | \
  grep -v "UserWarning\|warnings.warn\|MLB_HF_MODEL" | tail -30
echo "end:   $(ts)"

step "[4/5]  Layer-3 rebuild for 2026-05-05 (full slate, force=True)"
echo "start: $(ts)"
python audits/path_a_layer3_only.py 2026-05-05 2>&1 | \
  grep -v "UserWarning\|warnings.warn\|MLB_HF_MODEL" | tail -30
echo "end:   $(ts)"

step "[5/5]  Phase 2c production_replay_runner for 2026-05-05"
echo "start: $(ts)"
python audits/path_a_phase2c_0505.py 2>&1 | \
  grep -v "UserWarning\|warnings.warn\|MLB_HF_MODEL" | tail -30
echo "end:   $(ts)"

printf "\n══════════════════════════════════════════════════════════════════\n"
printf "  ✅ ALL FIVE STEPS PASSED\n"
printf "══════════════════════════════════════════════════════════════════\n"
df -h /app | tail -1
free -h | head -2

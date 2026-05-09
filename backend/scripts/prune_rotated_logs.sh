#!/usr/bin/env bash
# Rotated log pruner — deletes /var/log/**/*.log.[N] (and .gz) files older
# than 24h. Active logs (`*.log` without numeric suffix) are NEVER touched.
#
# USAGE
#   prune_rotated_logs.sh                 # execute (default)
#   prune_rotated_logs.sh --dry-run       # list candidates only
#   prune_rotated_logs.sh --max-age 48    # threshold in hours (default 24)
#
# Designed for cron / supervisor. Idempotent. Safe to run repeatedly.
set -uo pipefail

DRY_RUN=0
MAX_AGE_HOURS=24
LOG_TAG="[prune_rotated_logs]"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)  DRY_RUN=1; shift ;;
    --max-age)  MAX_AGE_HOURS="$2"; shift 2 ;;
    -h|--help)
      grep -E '^#( |$)' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *)
      echo "$LOG_TAG unknown arg: $1" >&2; exit 2 ;;
  esac
done

MAX_AGE_MIN=$((MAX_AGE_HOURS * 60))

# Patterns: rotated logs only — must contain a numeric suffix on .log.
# Examples that MATCH:
#   /var/log/mongodb.out.log.1
#   /var/log/mongodb.out.log.10
#   /var/log/supervisor/backend.err.log.5
#   /var/log/mongodb.out.log.1.gz
# Examples that DO NOT MATCH (active logs):
#   /var/log/mongodb.out.log
#   /var/log/supervisor/backend.err.log

scan_paths=( /var/log /var/log/supervisor /var/log/mongodb )
matched=()

for root in "${scan_paths[@]}"; do
  [[ -d "$root" ]] || continue
  while IFS= read -r -d '' f; do
    matched+=("$f")
  done < <(find "$root" -maxdepth 2 -type f \
      \( -regex '.*\.log\.[0-9]+\(\.gz\)?' \) \
      -mmin +$MAX_AGE_MIN \
      -print0 2>/dev/null)
done

# Deduplicate (some roots overlap).
mapfile -t uniq_files < <(printf '%s\n' "${matched[@]:-}" | sort -u | grep -v '^$')

if [[ ${#uniq_files[@]} -eq 0 ]]; then
  echo "$LOG_TAG no rotated logs older than ${MAX_AGE_HOURS}h to prune."
  exit 0
fi

bytes_total=0
for f in "${uniq_files[@]}"; do
  size=$(stat -c%s "$f" 2>/dev/null || echo 0)
  bytes_total=$((bytes_total + size))
done
human=$(numfmt --to=iec --format='%.1f' "$bytes_total" 2>/dev/null || \
        echo "${bytes_total}B")

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "$LOG_TAG DRY-RUN: would delete ${#uniq_files[@]} file(s), ${human}:"
  for f in "${uniq_files[@]}"; do
    echo "  $f"
  done
  exit 0
fi

deleted=0
for f in "${uniq_files[@]}"; do
  if rm -f -- "$f" 2>/dev/null; then
    deleted=$((deleted + 1))
  else
    echo "$LOG_TAG WARN: could not delete $f" >&2
  fi
done
echo "$LOG_TAG deleted ${deleted}/${#uniq_files[@]} rotated log file(s) " \
     "(~${human})."

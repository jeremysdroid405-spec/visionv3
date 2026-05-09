#!/usr/bin/env python3
"""
PropVision Historical Replay — comparison CLI (Phase 0 STUB).

In Phase 0 this script ONLY validates argument shape and prints the planned
diff scope. It does NOT read MongoDB.

USAGE (Phase 0)
---------------
    python /app/backend/scripts/compare_replay_runs.py \\
        --base       <run_id_a> \\
        --candidate  <run_id_b> \\
        --plan-only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.replay import REPLAY_COLLECTIONS  # noqa: E402


_RUN_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Diff two replay runs (Phase 0 stub).",
    )
    p.add_argument("--base", required=True,
                   help="Baseline replay_run_id (uuid4 hex).")
    p.add_argument("--candidate", required=True,
                   help="Candidate replay_run_id (uuid4 hex).")
    p.add_argument("--plan-only", action="store_true", default=True,
                   help="Phase 0: only --plan-only is supported.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    for label, value in (("--base", args.base),
                          ("--candidate", args.candidate)):
        if not _RUN_ID_RE.match(value):
            print(f"ERROR: {label} must be a 32-char hex run_id "
                  f"(got: {value!r})", file=sys.stderr)
            return 2
    if args.base == args.candidate:
        print("ERROR: --base and --candidate must differ", file=sys.stderr)
        return 2

    plan = {
        "phase": "0_plan_only",
        "base": args.base,
        "candidate": args.candidate,
        "diff_axes": [
            "pick_count_delta",
            "tier_movement",
            "roi_delta_by_tier",
            "hit_rate_delta_by_tier",
            "calibration_delta",
            "gate_fail_delta",
            "newly_qualified_props",
            "removed_props",
            "tier_changed_props",
            "side_flipped_props",
            "model_projection_material_changes",
            "edge_material_changes",
            "config_hash_diff",
        ],
        "collections_to_be_read_in_phase4": [
            "replay_runs",
            "replay_evaluations",
            "replay_outcomes",
            "replay_calibration_reports",
        ],
        "all_known_collections": REPLAY_COLLECTIONS,
        "output_path_pattern":
            "/app/audit_reports/replay_diffs/{base}_vs_{candidate}.md",
    }
    print(json.dumps(plan, indent=2))
    print("\n[Phase 0] No DB reads. Stub only.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

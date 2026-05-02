"""Build heteroscedastic sigma multipliers for NBA from settled outcomes.

Reads `forward_test_outcomes` (NBA, settled), computes per-stat residuals
against `vk_predicted`, and derives bucket-level std multipliers for
`BASE_SIGMAS` in `config/nba_sigma_heteroscedastic.py`.

Two feature axes are built (the axes where this dataset has enough
signal):

  1. `minutes_bucket` — derived from `full_prop_data.avg_mins`
        (0-22, 22-28, 28-34, 34+)

  2. `line_bucket` — derived from per-stat quartiles of `line`
        (low, mid_low, mid_high, high)

Multiplier = std(z_bucket) / std(z_overall)   (z = (actual - proj) / base_sigma)

Clipped to [0.5, 2.0] per the scaffold design doc. A bucket with fewer
than `MIN_BUCKET_N` samples gets multiplier = 1.0 (no adjustment) rather
than a noisy over-fit.

Emits a Python dict ready to paste into `MULTIPLIER_TABLES`, plus a
provenance YAML at `/app/backend/config/nba_sigma_buckets_provenance.yaml`
for auditability.
"""
from __future__ import annotations

import math
import os
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from pymongo import MongoClient

from config.nba_sigma_heteroscedastic import BASE_SIGMAS


MIN_BUCKET_N = 8          # skip buckets with < 8 settled residuals
MULT_MIN = 0.5
MULT_MAX = 2.0


def _minutes_bucket(avg_mins: float) -> str:
    if avg_mins is None:
        return "unknown"
    try:
        m = float(avg_mins)
    except (TypeError, ValueError):
        return "unknown"
    if m < 22.0:
        return "0_22"
    if m < 28.0:
        return "22_28"
    if m < 34.0:
        return "28_34"
    return "34_plus"


def _line_quartiles(lines: List[float]) -> Tuple[float, float, float]:
    """Return (q25, q50, q75) — used to label each line's bucket."""
    sorted_lines = sorted(lines)
    n = len(sorted_lines)
    if n < 4:
        # fallback: single bucket ("all") — returned as equal boundaries
        m = sorted_lines[n // 2] if n else 0.0
        return (m, m, m)
    return (
        sorted_lines[n // 4],
        sorted_lines[n // 2],
        sorted_lines[(3 * n) // 4],
    )


def _line_bucket(line: float, q25: float, q50: float, q75: float) -> str:
    if line is None:
        return "unknown"
    try:
        lf = float(line)
    except (TypeError, ValueError):
        return "unknown"
    if lf <= q25:
        return "low"
    if lf <= q50:
        return "mid_low"
    if lf <= q75:
        return "mid_high"
    return "high"


def build_tables() -> Dict[str, Dict[str, Dict[str, float]]]:
    mc = MongoClient(os.environ["MONGO_URL"])
    db = mc[os.environ["DB_NAME"]]
    rows: List[Dict] = list(db.forward_test_outcomes.find(
        {"sport": "nba", "actual_value": {"$ne": None}},
        {
            "_id": 0, "stat_type": 1, "line": 1, "actual_value": 1,
            "vk_predicted": 1, "full_prop_data": 1,
        },
    ))

    # Group residuals per stat first
    per_stat: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        stat = (r.get("stat_type") or "").upper()
        proj = r.get("vk_predicted")
        actual = r.get("actual_value")
        line = r.get("line")
        fpd = r.get("full_prop_data") or {}
        avg_mins = fpd.get("avg_mins")
        base_sigma = BASE_SIGMAS.get(stat)
        if proj is None or actual is None or base_sigma is None or base_sigma <= 0:
            continue
        try:
            z = (float(actual) - float(proj)) / float(base_sigma)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        per_stat[stat].append({
            "z": z, "line": line, "avg_mins": avg_mins,
        })

    tables: Dict[str, Dict[str, Dict[str, float]]] = {}
    provenance: Dict[str, Dict] = {}

    for stat, entries in per_stat.items():
        if len(entries) < MIN_BUCKET_N:
            continue
        overall_std = statistics.pstdev([e["z"] for e in entries])
        if overall_std == 0:
            continue

        # -- Minutes bucket --------------------------------------------
        minutes_groups: Dict[str, List[float]] = defaultdict(list)
        for e in entries:
            mb = _minutes_bucket(e["avg_mins"])
            if mb == "unknown":
                continue
            minutes_groups[mb].append(e["z"])

        minutes_mults: Dict[str, float] = {}
        for bucket, zs in minutes_groups.items():
            if len(zs) < MIN_BUCKET_N:
                continue
            bstd = statistics.pstdev(zs)
            mult = bstd / overall_std
            mult = max(MULT_MIN, min(MULT_MAX, mult))
            minutes_mults[bucket] = round(mult, 2)

        # -- Line bucket -----------------------------------------------
        lines = [float(e["line"]) for e in entries if e["line"] is not None]
        q25, q50, q75 = _line_quartiles(lines)
        line_groups: Dict[str, List[float]] = defaultdict(list)
        for e in entries:
            lb = _line_bucket(e["line"], q25, q50, q75)
            if lb == "unknown":
                continue
            line_groups[lb].append(e["z"])

        line_mults: Dict[str, float] = {}
        for bucket, zs in line_groups.items():
            if len(zs) < MIN_BUCKET_N:
                continue
            bstd = statistics.pstdev(zs)
            mult = bstd / overall_std
            mult = max(MULT_MIN, min(MULT_MAX, mult))
            line_mults[bucket] = round(mult, 2)

        tables[stat] = {
            "minutes_bucket": minutes_mults,
            "line_bucket": line_mults,
        }
        provenance[stat] = {
            "n": len(entries),
            "overall_std_z": round(overall_std, 4),
            "line_quartiles": {
                "q25": round(q25, 2), "q50": round(q50, 2), "q75": round(q75, 2)
            },
            "minutes_counts": {k: len(v) for k, v in minutes_groups.items()},
            "line_counts": {k: len(v) for k, v in line_groups.items()},
        }

    _emit_output(tables, provenance)
    return tables


def _emit_output(
    tables: Dict[str, Dict[str, Dict[str, float]]],
    provenance: Dict[str, Dict],
) -> None:
    """Print the dict literal and write provenance YAML."""
    print("MULTIPLIER_TABLES = {")
    for stat in sorted(tables.keys()):
        axes = tables[stat]
        print(f'    "{stat}": {{')
        for axis in sorted(axes.keys()):
            buckets = axes[axis]
            if not buckets:
                continue
            print(f'        "{axis}": {{')
            for bname in sorted(buckets.keys()):
                print(f'            "{bname}": {buckets[bname]},')
            print("        },")
        print("    },")
    print("}")

    yaml_path = Path("/app/backend/config/nba_sigma_buckets_provenance.yaml")
    lines = ["# Heteroscedastic-sigma bucket provenance (auto-generated).",
             "# Source: forward_test_outcomes (sport=nba, settled).",
             ""]
    for stat in sorted(provenance.keys()):
        p = provenance[stat]
        lines.append(f"{stat}:")
        lines.append(f"  n: {p['n']}")
        lines.append(f"  overall_std_z: {p['overall_std_z']}")
        lines.append(f"  line_quartiles: {p['line_quartiles']}")
        lines.append(f"  minutes_counts: {p['minutes_counts']}")
        lines.append(f"  line_counts: {p['line_counts']}")
        lines.append("")
    yaml_path.write_text("\n".join(lines))
    print(f"\nProvenance written to {yaml_path}")


if __name__ == "__main__":
    build_tables()

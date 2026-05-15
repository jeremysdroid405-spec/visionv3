#!/usr/bin/env python3
"""CI lint script — fails if any module outside the universal_edge
allowlist contains a duplicate edge writer.

Exit code 0 = clean, 1 = violations found.

Usage:
    python /app/backend/scripts/lint_universal_edge.py
"""
import sys
sys.path.insert(0, "/app/backend")

from services.scoring.universal_edge import audit_edge_writers


def main() -> int:
    res = audit_edge_writers()
    print(f"Universal Edge SSOT lint — scanned {res['scanned']} files")
    if not res["violations"]:
        print("  ✓ NO violations — SSOT contract holds.")
        return 0
    print(f"  ✗ {len(res['violations'])} violation(s) found:")
    for v in res["violations"]:
        print(f"    {v['module']}:{v['line']:>4}  "
               f"pattern={v['pattern']!r}\n"
               f"      → {v['snippet']}")
    print("\n  Fix: route the calculation through "
           "services.scoring.universal_edge.compute_edge_bundle().")
    return 1


if __name__ == "__main__":
    sys.exit(main())

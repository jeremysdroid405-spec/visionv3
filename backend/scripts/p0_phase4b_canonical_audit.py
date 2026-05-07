#!/usr/bin/env python3
"""
P0 Phase 4B — canonical-field-presence audit.

Per user instruction: "Before deleting any legacy field writer, log
the exact canonical replacement field, whether the canonical field is
guaranteed populated on every visible pick, and which endpoint
currently serves it. If a canonical field is missing on even one
visible pick, STOP and report before deleting the writer."

This script:
  1. Calls /api/v3/ferrari/all for NBA and MLB.
  2. For every visible tier pick (safe_haven + front_lines + war_zone),
     records:
       - presence/None of the 5 canonical fields (hit_rate_l5, l10,
         l20, over, under).
       - presence of each legacy alias (h5_rate, h10_rate, h20_rate,
         hit_rate, hit_rates, model_hit_rate_over, model_hit_rate_under).
       - any divergence between canonical and legacy (e.g., hit_rate_l5
         != h5_rate).
  3. Returns a JSON summary with go/no-go per canonical field and a
     drill-down list of any picks that fail the 100%-presence bar.

Read-only. Safe to run anytime.
"""
import json
import sys
import urllib.request


CANONICAL = ["hit_rate_l5", "hit_rate_l10", "hit_rate_l20",
             "hit_rate_over", "hit_rate_under"]
LEGACY = ["h5_rate", "h10_rate", "h20_rate", "hit_rate", "hit_rates",
          "model_hit_rate_over", "model_hit_rate_under"]
LEGACY_TO_CANONICAL = {
    "h5_rate":              "hit_rate_l5",
    "h10_rate":             "hit_rate_l10",
    "h20_rate":             "hit_rate_l20",
    "hit_rate":             "hit_rate_over (active-side alias)",
    "hit_rates":            "hit_rate_l5/l10/l20 (nested → flat)",
    "model_hit_rate_over":  "hit_rate_over",
    "model_hit_rate_under": "hit_rate_under",
}


def _http(url: str, timeout: float = 15.0) -> dict:
    # Preview ingress rejects plain urllib UA; shell out to curl which
    # has a sane default UA + follows redirects identically to the UI.
    import subprocess
    r = subprocess.run(
        ["curl", "-sSf", "--max-time", str(int(timeout)), url],
        capture_output=True, text=True, timeout=timeout + 5,
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl failed for {url}: rc={r.returncode} err={r.stderr[:200]}")
    return json.loads(r.stdout)


def main(api_url: str) -> int:
    summary = {
        "endpoint": "/api/v3/ferrari/all?sport={nba|mlb}",
        "by_sport": {},
        "go_no_go": {},  # canonical_field -> "GO" / "STOP"
        "diverged_picks": [],
        "missing_canonical_picks": [],
    }
    for sport in ("nba", "mlb"):
        d = _http(f"{api_url}/api/v3/ferrari/all?sport={sport}")
        per_sport: dict = {
            "total_visible": 0,
            "canonical_present": {f: 0 for f in CANONICAL},
            "legacy_present": {f: 0 for f in LEGACY},
            "diverged_count": {f: 0 for f in
                               ["h5_rate", "h10_rate", "h20_rate",
                                "model_hit_rate_over",
                                "model_hit_rate_under"]},
        }
        for tier_key in ("safe_haven", "front_lines", "war_zone"):
            for p in (d.get(tier_key) or {}).get("picks", []) or []:
                per_sport["total_visible"] += 1
                for cf in CANONICAL:
                    if p.get(cf) is not None:
                        per_sport["canonical_present"][cf] += 1
                    else:
                        summary["missing_canonical_picks"].append({
                            "sport": sport,
                            "tier": tier_key,
                            "canonical_key": p.get("canonical_key"),
                            "missing": cf,
                        })
                for lf in LEGACY:
                    if lf in p and p.get(lf) is not None:
                        per_sport["legacy_present"][lf] += 1
                # Divergence: same numeric value (rounded) for paired fields
                pairs = [
                    ("hit_rate_l5",   "h5_rate"),
                    ("hit_rate_l10",  "h10_rate"),
                    ("hit_rate_l20",  "h20_rate"),
                    ("hit_rate_over", "model_hit_rate_over"),
                    ("hit_rate_under", "model_hit_rate_under"),
                ]
                for canon, leg in pairs:
                    cv = p.get(canon)
                    lv = p.get(leg)
                    if cv is not None and lv is not None:
                        try:
                            if abs(float(cv) - float(lv)) > 0.5:
                                per_sport["diverged_count"][leg] += 1
                                summary["diverged_picks"].append({
                                    "sport": sport,
                                    "tier": tier_key,
                                    "canonical_key": p.get("canonical_key"),
                                    "canonical": {canon: cv},
                                    "legacy":    {leg:   lv},
                                    "diff":      round(float(cv) - float(lv), 2),
                                })
                        except (TypeError, ValueError):
                            pass
        summary["by_sport"][sport] = per_sport

    # GO/NO-GO judgement: canonical must be 100% present across both sports.
    overall_total = sum(s["total_visible"] for s in summary["by_sport"].values())
    for cf in CANONICAL:
        present = sum(s["canonical_present"][cf] for s in summary["by_sport"].values())
        coverage = (100.0 * present / overall_total) if overall_total else 0.0
        if coverage == 100.0:
            summary["go_no_go"][cf] = f"GO ({present}/{overall_total})"
        else:
            summary["go_no_go"][cf] = f"STOP ({present}/{overall_total} = {coverage:.1f}%)"
    summary["overall_total_picks"] = overall_total

    print(json.dumps(summary, indent=2))
    bad = any(v.startswith("STOP") for v in summary["go_no_go"].values())
    return 1 if bad else 0


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else None
    if not base:
        # Read from frontend .env
        for ln in open("/app/frontend/.env"):
            if ln.startswith("REACT_APP_BACKEND_URL="):
                base = ln.split("=", 1)[1].strip().strip('"')
                break
    if not base:
        print("ERROR: REACT_APP_BACKEND_URL not provided", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(base))

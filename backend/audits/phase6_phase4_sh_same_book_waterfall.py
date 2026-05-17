"""Phase 6 Phase 4 — Gate-failure waterfall audit
for SH-routed canonical props with devig_method='same_book'.

Read-only. Touches no production code. Outputs:
  • Console summary
  • JSON artifact at audits/phase6_phase4_sh_same_book_waterfall.json

Scope filter (HARD):
  replay_serial = MLB-PRODREPLAY-20260505-SH-1100UTC-00074
  routed_tier   = safe_haven
  devig_method  = same_book
  ⇒ excludes one_sided, cross_book-only, and any extreme-alt
    placeholders.

Audit dimensions per failing gate:
  failure count / %, stat_family breakdown, odds_bucket breakdown,
  avg TP / edge_canonical / CV / HR_L5,L10,L20, near-miss count.

Plus:
  • singleton-failure cohort (props failing exactly 1 gate) —
    highest-value recovery candidates.
  • top-25 near-miss props sorted by (n_failed asc, tp desc,
    hr_l10 desc, cv asc).
"""
from __future__ import annotations
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient

from services.scoring.gates.thresholds import resolve_thresholds


SERIAL = "MLB-PRODREPLAY-20260505-SH-1100UTC-00074"
OUT_PATH = Path("/app/backend/audits/phase6_phase4_sh_same_book_waterfall.json")


# ── American odds ↔ implied prob ───────────────────────────────────
def _american_to_prob(o):
    if o is None:
        return None
    o = int(o)
    return 100.0 / (o + 100.0) if o > 0 else (-o) / ((-o) + 100.0)


def _safe_avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _odds_bucket(odds):
    """SH-routed odds banding for context (NOT a router decision)."""
    if odds is None:
        return "unknown"
    if odds <= -1000:
        return "deep_chalk_-1000_or_lower"
    if odds <= -500:
        return "heavy_chalk_-500_to_-999"
    if odds <= -300:
        return "sh_band_-300_to_-499"
    return "lighter_than_sh_-301_or_higher"


def main():
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    coll = db["mlb_production_replay_outputs"]

    rows = list(coll.find(
        {"replay_serial": SERIAL,
         "routed_tier": "safe_haven",
         "devig_method": "same_book"},
        {"_id": 0},
    ))
    n_total = len(rows)
    print(f"\n=== AUDIT cohort: SH-routed × devig_method=same_book ===")
    print(f"serial={SERIAL}")
    print(f"cohort size = {n_total}")

    # Enrich each row with canonical-adjusted edge:
    # `edge_canonical_pp` = (canonical_devig_prob_for_side) -
    #                       implied(tier_reference_odds), in pp.
    for r in rows:
        side = (r.get("side") or "OVER").upper()
        if side == "OVER":
            devig_p = r.get("canonical_devig_over_prob")
        else:
            devig_p = r.get("canonical_devig_under_prob")
        ref_odds = r.get("tier_reference_odds")
        impl = _american_to_prob(ref_odds)
        if devig_p is not None and impl is not None:
            r["_canonical_edge_pp"] = round(
                (float(devig_p) - float(impl)) * 100.0, 4,
            )
            r["_tp_pp"] = round(float(devig_p) * 100.0, 4)
        else:
            r["_canonical_edge_pp"] = None
            r["_tp_pp"] = None

    # ── Threshold cache (per stat_family × side) ────────────────────
    cfg_cache = {}
    def cfg_for(stat_family, side):
        k = (stat_family, side)
        if k not in cfg_cache:
            cfg_cache[k] = resolve_thresholds(
                "mlb", "safe_haven", stat_family, side=side,
            )
        return cfg_cache[k]

    # ── Aggregate failures ──────────────────────────────────────────
    n_passed = sum(1 for r in rows if r.get("gate_pass"))
    n_failed = n_total - n_passed
    print(f"passed={n_passed}, failed={n_failed}")

    # Total failure occurrences per gate (a single prop can contribute
    # to multiple gates).
    gate_fail_counter = Counter()
    for r in rows:
        for g in r.get("failed_gates") or []:
            gate_fail_counter[g] += 1

    # Multi-gate counts per prop
    n_failed_gates_dist = Counter()
    for r in rows:
        n_failed_gates_dist[len(r.get("failed_gates") or [])] += 1

    print("\n=== Gate-failure waterfall (per gate, total failures) ===")
    waterfall = []
    for g, n in gate_fail_counter.most_common():
        sub = [r for r in rows if g in (r.get("failed_gates") or [])]
        stat_fam_breakdown = Counter(r.get("stat_family") for r in sub)
        odds_bucket_breakdown = Counter(
            _odds_bucket(r.get("tier_reference_odds")) for r in sub
        )
        side_breakdown = Counter(r.get("side") for r in sub)
        entry = {
            "gate": g,
            "n_failed": n,
            "pct_of_cohort": round(100.0 * n / n_total, 2),
            "by_stat_family": dict(stat_fam_breakdown.most_common()),
            "by_odds_bucket": dict(odds_bucket_breakdown.most_common()),
            "by_side": dict(side_breakdown.most_common()),
            "avg_tp_pp": _safe_avg(r.get("_tp_pp") for r in sub),
            "avg_canonical_edge_pp": _safe_avg(
                r.get("_canonical_edge_pp") for r in sub
            ),
            "avg_cv": _safe_avg(r.get("cv") for r in sub),
            "avg_hit_rate_l5":  _safe_avg(r.get("hit_rate_l5")  for r in sub),
            "avg_hit_rate_l10": _safe_avg(r.get("hit_rate_l10") for r in sub),
            "avg_hit_rate_l20": _safe_avg(r.get("hit_rate_l20") for r in sub),
        }
        # ── Near-miss counts vs SH thresholds (per gate semantics) ──
        near_misses = 0
        for r in sub:
            cfg = cfg_for(r.get("stat_family"), r.get("side"))
            if g == "hit_rate_gate":
                hr = r.get("hit_rate_l20") or r.get("hit_rate_l10") or r.get("hit_rate_l5")
                hr_min = cfg.get("hit_rate_gate", {}).get("min")
                if hr is not None and hr_min is not None and (hr_min - hr) <= 5:
                    near_misses += 1
            elif g == "tp_gate":
                tp = r.get("_tp_pp")
                tp_min = cfg.get("tp_gate", {}).get("min")
                if tp is not None and tp_min is not None and (tp_min - tp) <= 3:
                    near_misses += 1
            elif g == "edge_gate":
                ed = r.get("_canonical_edge_pp")
                ed_min = cfg.get("edge_gate", {}).get("min")
                if ed is not None and ed_min is not None and (ed_min - ed) <= 2:
                    near_misses += 1
            elif g == "cv_gate":
                cv = r.get("cv")
                cv_max = cfg.get("cv_gate", {}).get("max")
                if cv is not None and cv_max is not None and (cv - cv_max) <= 0.10:
                    near_misses += 1
            elif g == "margin_gate":
                # margin_gate is only active at line=0.5 (MLB). The
                # near-miss definition depends on the family cfg;
                # we report the count of failures with line == 0.5
                # as the eligible pool but flag no near-miss heuristic.
                if (r.get("line") or 0.0) == 0.5:
                    near_misses += 1
            elif g == "tp_source_gate":
                # Shouldn't appear in same_book cohort (we excluded
                # one_sided). If it does, flag for investigation.
                pass
            elif g == "direction_gate":
                # Direction = strict OVER side-lean. No near-miss.
                pass
        entry["near_miss_count"] = near_misses
        waterfall.append(entry)
        print(f"  {g:24s}  n={n:3d} ({entry['pct_of_cohort']:>5.1f}%)  "
              f"avg_tp={entry['avg_tp_pp']}  "
              f"avg_edge={entry['avg_canonical_edge_pp']}  "
              f"avg_cv={entry['avg_cv']}  "
              f"hr_l10={entry['avg_hit_rate_l10']}  "
              f"near_miss={near_misses}")

    # ── Singleton failures (props failing exactly ONE gate) ─────────
    singletons = [r for r in rows if len(r.get("failed_gates") or []) == 1]
    singleton_by_gate = Counter(r["failed_gates"][0] for r in singletons)
    print(f"\n=== Singleton-failure cohort (1 gate failed) ===")
    print(f"  total={len(singletons)}")
    for g, n in singleton_by_gate.most_common():
        print(f"  {g:24s}  n={n}")

    # ── Distribution: # gates failed per prop ───────────────────────
    print(f"\n=== Distribution of #gates failed per prop ===")
    for k in sorted(n_failed_gates_dist):
        print(f"  {k} gates failed: {n_failed_gates_dist[k]} props")

    # ── Failure-domain concentration ────────────────────────────────
    domains = {
        "TP":        gate_fail_counter.get("tp_gate", 0),
        "edge":      gate_fail_counter.get("edge_gate", 0),
        "hit_rate":  gate_fail_counter.get("hit_rate_gate", 0),
        "CV":        gate_fail_counter.get("cv_gate", 0),
        "margin":    gate_fail_counter.get("margin_gate", 0),
        "direction": gate_fail_counter.get("direction_gate", 0),
        "coverage":  gate_fail_counter.get("coverage_gate", 0),
        "tp_source": gate_fail_counter.get("tp_source_gate", 0),
    }
    print(f"\n=== Failure-domain concentration (total occurrences) ===")
    for k, v in sorted(domains.items(), key=lambda kv: -kv[1]):
        print(f"  {k:12s} {v}")

    # ── Top 25 near-miss props ──────────────────────────────────────
    # Sort: (n_failed asc, tp desc, hr_l10 desc, cv asc)
    def _sort_key(r):
        return (
            len(r.get("failed_gates") or []),
            -(r.get("_tp_pp") or -1e9),
            -(r.get("hit_rate_l10") or -1e9),
            (r.get("cv") if r.get("cv") is not None else 1e9),
        )

    top25 = sorted(
        [r for r in rows if not r.get("gate_pass")],
        key=_sort_key,
    )[:25]
    print(f"\n=== Top 25 near-miss props "
          f"(sort: fewest gates → highest TP → highest HR → lowest CV) ===")
    print(f"{'#':>2} {'player':24s} {'stat':14s} {'L':>4s} "
          f"{'sd':<5s} {'odds':>6s} {'tp':>6s} {'edge':>6s} "
          f"{'cv':>5s} {'hr5/10/20':>14s}  failed_gates")
    top25_payload = []
    for i, r in enumerate(top25, 1):
        line = f"{r.get('player_name') or '?':24.24s}"
        stat = f"{r.get('stat_family') or '?':14.14s}"
        ln = r.get("line")
        odds = r.get("tier_reference_odds")
        tp = r.get("_tp_pp")
        edge = r.get("_canonical_edge_pp")
        cv = r.get("cv")
        hrs = (
            f"{r.get('hit_rate_l5') or '-':>4}/"
            f"{r.get('hit_rate_l10') or '-':>4}/"
            f"{r.get('hit_rate_l20') or '-':>4}"
        )
        side = r.get("side") or "?"
        print(f"{i:>2} {line} {stat} {str(ln):>4s} "
              f"{side:<5s} {str(odds):>6s} {str(tp):>6.6s} "
              f"{str(edge):>6.6s} {str(cv):>5.5s} {hrs:>14s}  "
              f"{','.join(r.get('failed_gates') or [])}")
        top25_payload.append({
            "rank": i,
            "player_name": r.get("player_name"),
            "stat_family": r.get("stat_family"),
            "market": r.get("market"),
            "canonical_market_key": r.get("canonical_market_key"),
            "line": ln,
            "side": side,
            "tier_reference_odds": odds,
            "tier_reference_book": r.get("tier_reference_book"),
            "tp_pp": tp,
            "canonical_edge_pp": edge,
            "cv": cv,
            "hit_rate_l5": r.get("hit_rate_l5"),
            "hit_rate_l10": r.get("hit_rate_l10"),
            "hit_rate_l20": r.get("hit_rate_l20"),
            "failed_gates": r.get("failed_gates"),
            "n_failed_gates": len(r.get("failed_gates") or []),
            "devig_method": r.get("devig_method"),
            "same_book_pair_count": r.get("same_book_pair_count"),
            "books_used": r.get("books_used"),
            "over_books": r.get("over_books"),
            "under_books": r.get("under_books"),
        })

    # ── Recovery questions (which single gate change recovers most) ─
    # For each gate, count props whose ONLY failure(s) are that gate
    # (alone or paired with another gate) — i.e. removing this gate
    # would not auto-pass them unless ALL their failed_gates collapse.
    print(f"\n=== Recovery-impact: 'fail ONLY this gate' counts ===")
    only_this_gate = Counter()
    for r in rows:
        fg = r.get("failed_gates") or []
        if len(fg) == 1:
            only_this_gate[fg[0]] += 1
    for g, n in only_this_gate.most_common():
        # Show "would also still pass tp_source / margin / coverage"
        # — these are healthy props blocked by exactly one gate.
        print(f"  removing {g:24s} → +{n} props could qualify (1-gate-only)")

    # Two-gate cohorts where ONE gate change unlocks BOTH:
    print(f"\n=== Two-gate cohorts (props failing exactly 2 gates) ===")
    two_gate_pairs = Counter()
    for r in rows:
        fg = sorted(r.get("failed_gates") or [])
        if len(fg) == 2:
            two_gate_pairs[tuple(fg)] += 1
    for pair, n in two_gate_pairs.most_common():
        print(f"  {pair[0]:24s} + {pair[1]:24s} → {n}")

    # ── Structural-quality cohort definition ────────────────────────
    # Props that are STRUCTURALLY STRONG (high TP, high HR, low CV)
    # but fail. Heuristic anchor for "would maintain strong quality
    # if recovered":
    #     tp_pp ≥ 70, hr_l10 ≥ 70, cv ≤ 1.0 (or line == 0.5)
    structurally_strong = [
        r for r in rows
        if not r.get("gate_pass")
        and (r.get("_tp_pp") or 0) >= 70
        and (r.get("hit_rate_l10") or 0) >= 70
        and ((r.get("cv") or 1e9) <= 1.0 or (r.get("line") or 0) == 0.5)
    ]
    print(f"\n=== Structurally-strong failing cohort "
          f"(tp ≥ 70 ∧ hr_l10 ≥ 70 ∧ (cv ≤ 1.0 ∨ line=0.5)) ===")
    print(f"  size = {len(structurally_strong)}")
    strong_by_gate = Counter()
    for r in structurally_strong:
        for g in r.get("failed_gates") or []:
            strong_by_gate[g] += 1
    for g, n in strong_by_gate.most_common():
        print(f"    {g:24s}  n={n}")

    # ── Compose JSON artifact ──────────────────────────────────────
    artifact = {
        "audit_kind": "sh_same_book_devig_gate_waterfall",
        "serial": SERIAL,
        "cohort_filter": {
            "routed_tier": "safe_haven",
            "devig_method": "same_book",
            "excludes": ["one_sided", "cross_book_only",
                          "extreme_alt_placeholders"],
        },
        "cohort_size": n_total,
        "passed": n_passed,
        "failed": n_failed,
        "waterfall": waterfall,
        "n_failed_gates_distribution": dict(n_failed_gates_dist),
        "singletons": {
            "total": len(singletons),
            "by_gate": dict(singleton_by_gate),
        },
        "two_gate_pairs": [
            {"pair": list(k), "n": v} for k, v in two_gate_pairs.most_common()
        ],
        "failure_domain_concentration": domains,
        "top_25_near_miss": top25_payload,
        "recovery_impact_only_this_gate": dict(only_this_gate),
        "structurally_strong_failing": {
            "criteria": "tp_pp >= 70 AND hr_l10 >= 70 AND "
                        "(cv <= 1.0 OR line == 0.5)",
            "size": len(structurally_strong),
            "by_gate": dict(strong_by_gate),
            "props": [
                {
                    "player_name": r.get("player_name"),
                    "stat_family": r.get("stat_family"),
                    "line": r.get("line"),
                    "side": r.get("side"),
                    "tier_reference_odds": r.get("tier_reference_odds"),
                    "tp_pp": r.get("_tp_pp"),
                    "canonical_edge_pp": r.get("_canonical_edge_pp"),
                    "cv": r.get("cv"),
                    "hit_rate_l10": r.get("hit_rate_l10"),
                    "failed_gates": r.get("failed_gates"),
                    "devig_method": r.get("devig_method"),
                }
                for r in structurally_strong
            ],
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(artifact, indent=2, default=str))
    print(f"\n[audit] wrote {OUT_PATH}")
    client.close()
    return artifact


if __name__ == "__main__":
    main()

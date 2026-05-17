"""Phase 2a smoke test — byte-identical verification.

Runs `evaluate_tier_with_overrides()` from BOTH the new (post-change)
module AND a freshly-loaded copy of the pre-change file, over the same
200 production prop_scores, and hashes the serialized outputs.

If hashes match, the change is byte-identical for live-path callers.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import pickle
import sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv("/app/backend/.env")

# Load the PRE-change module from /tmp under a synthetic name
spec = importlib.util.spec_from_file_location(
    "tier_evaluator_PRE", "/tmp/tier_evaluator_PRE.py")
pre_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre_mod)

# Load the POST-change (production) module normally
from services.scoring import tier_evaluator as post_mod
from services.scoring.metrics_builder import build_metrics_from_context
from services.scoring.gates.schema import GateEvalResult


def serialize(r: GateEvalResult) -> dict:
    """Reproducible JSON-safe dict for hashing."""
    return {
        "passed":   r.passed,
        "tier":     getattr(r, "tier", None),
        "reasons":  sorted(getattr(r, "reasons", []) or []),
        "warnings": sorted(getattr(r, "warnings", []) or []),
    }


with open("/app/backend/audits/phase2a/smoke_input_props.pkl", "rb") as f:
    props = pickle.load(f)

print(f"Replaying {len(props)} prop_scores rows through BOTH evaluators...")

pre_results = []
post_results = []
errors_pre = errors_post = 0

for p in props:
    sport = p.get("sport") or "mlb"
    target_tier = p.get("tier") or p.get("target_tier") or "war_zone"
    stat_raw = p.get("stat_type") or p.get("stat_family")
    side = p.get("side") or "OVER"
    try:
        metrics = build_metrics_from_context(
            prop=p, sport=sport, target_tier=target_tier,
            stat_raw=stat_raw, side=side,
            ref_book=p.get("ref_book"), ref_odds=p.get("ref_odds"),
            book_count=p.get("book_count"),
            cv=p.get("cv"),
            hit_rate=p.get("hit_rate") or p.get("hit_rate_l20"),
            edge_pct=p.get("edge_pct"),
            tp=p.get("tp"),
            ceiling_rate=p.get("ceiling_rate"),
            p_model_pct=p.get("p_model_pct") or p.get("model_probability"),
            cv_cap_override=None,
        )
    except Exception as e:
        errors_pre += 1; errors_post += 1
        continue

    try:
        pre_r = pre_mod.evaluate_tier_with_overrides(metrics)
        pre_results.append(serialize(pre_r))
    except Exception:
        errors_pre += 1
    try:
        post_r = post_mod.evaluate_tier_with_overrides(metrics)
        post_results.append(serialize(post_r))
    except Exception:
        errors_post += 1

pre_blob  = json.dumps(pre_results,  sort_keys=True, default=str).encode()
post_blob = json.dumps(post_results, sort_keys=True, default=str).encode()

pre_sha  = hashlib.sha256(pre_blob).hexdigest()
post_sha = hashlib.sha256(post_blob).hexdigest()

print(f"\n  PRE  results count: {len(pre_results):>4}   SHA-256: {pre_sha}")
print(f"  POST results count: {len(post_results):>4}   SHA-256: {post_sha}")
print(f"  PRE  errors:        {errors_pre}")
print(f"  POST errors:        {errors_post}")
print()
if pre_sha == post_sha and pre_results == post_results:
    print("  ✅ BYTE-IDENTICAL — feature_provider=None default is non-breaking")
else:
    print("  ❌ MISMATCH — investigate before continuing")
    # Show first 3 diffs
    diffs = 0
    for i, (a, b) in enumerate(zip(pre_results, post_results)):
        if a != b:
            print(f"  row {i}: PRE={a}  POST={b}")
            diffs += 1
            if diffs >= 5: break

# Also test the NEW signature (passing feature_provider=None explicitly)
# matches default behavior
sample_metrics_test = None
for p in props[:10]:
    try:
        m = build_metrics_from_context(
            prop=p, sport=p.get("sport") or "mlb",
            target_tier=p.get("tier") or "war_zone",
            stat_raw=p.get("stat_type"), side=p.get("side") or "OVER",
            ref_book=None, ref_odds=None, book_count=None,
            cv=p.get("cv"), hit_rate=p.get("hit_rate"),
            edge_pct=p.get("edge_pct"), tp=p.get("tp"),
            ceiling_rate=None, p_model_pct=p.get("p_model_pct"),
            cv_cap_override=None,
        )
        sample_metrics_test = m
        break
    except Exception:
        continue
if sample_metrics_test is not None:
    r1 = post_mod.evaluate_tier_with_overrides(sample_metrics_test)
    r2 = post_mod.evaluate_tier_with_overrides(sample_metrics_test, feature_provider=None)
    print()
    print(f"  Explicit feature_provider=None test:  "
          f"{'✅ identical' if serialize(r1) == serialize(r2) else '❌ DIFFERS'}")

# Verify the NEW signature accepts a dummy provider object without error
class _DummyProvider:
    pass
if sample_metrics_test is not None:
    try:
        r3 = post_mod.evaluate_tier_with_overrides(sample_metrics_test,
                                                     feature_provider=_DummyProvider())
        same = serialize(r1) == serialize(r3)
        print(f"  Passing a dummy provider object:      "
              f"{'✅ accepted, identical output' if same else '❌ DIFFERS'}")
    except Exception as e:
        print(f"  ❌ Passing provider raised: {e!r}")

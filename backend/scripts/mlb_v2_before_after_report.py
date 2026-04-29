"""
MLB HF v2 Retrain — Before/After Snapshot Generator
====================================================
Captures three states:
  1. BEFORE:  current `mlb_prop_scores` (active=True, latest version) —
              produced by the OLD MLB_VK_v3.0_3yr artifacts before this run.
  2. RETRAIN: trained model artifact metrics from `_train_report_v2.json`.
  3. AFTER:   `mlb_prop_scores` after `recompute_sport(mlb)` runs with
              the new `MLB_HF_v2.0_statcast` artifacts.

Outputs a markdown report and a json blob to:
  /app/backend/data/snapshots/mlb_hf_v2_<ts>.md
  /app/backend/data/snapshots/mlb_hf_v2_<ts>.json
"""
from __future__ import annotations
import asyncio, json, os, pickle, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv; load_dotenv()
from motor.motor_asyncio import AsyncIOMotorClient

OUT_DIR = "/app/backend/data/snapshots"
os.makedirs(OUT_DIR, exist_ok=True)
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
REPORT_MD = os.path.join(OUT_DIR, f"mlb_hf_v2_{TS}.md")
REPORT_JSON = os.path.join(OUT_DIR, f"mlb_hf_v2_{TS}.json")

NEW_VERSION_TAG = f"mlb-hf-v2-{TS}"
OLD_VERSION_PREFIX = ""  # snapshot whichever is currently active


async def _snapshot_state(db, label: str, *, version_tag: str | None = None):
    q = {"active": True}
    if version_tag is not None:
        q["version_tag"] = version_tag
    n_total = await db.mlb_prop_scores.count_documents(q)
    pipeline = [
        {"$match": q},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]
    tiers: dict = {}
    async for r in db.mlb_prop_scores.aggregate(pipeline):
        tiers[r["_id"] or "(none)"] = r["n"]

    # Stat-family breakdown
    pipeline2 = [
        {"$match": q},
        {"$group": {"_id": "$stat_type", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    stats: dict = {}
    async for r in db.mlb_prop_scores.aggregate(pipeline2):
        stats[r["_id"] or "(none)"] = r["n"]

    # Pull all docs to compute distributions
    proj = {"_id": 0, "model_projection": 1, "model_sigma": 1,
            "edge_pct": 1, "p_model": 1, "tier": 1, "stat_type": 1,
            "probability_method": 1, "lom_disabled": 1,
            "recommendation": 1}
    n_pos_edge = 0
    n_zero_edge = 0
    n_neg_edge = 0
    n_lom_off = 0
    n_dist_method = 0
    proj_means: dict = defaultdict(list)
    edge_means: dict = defaultdict(list)
    async for d in db.mlb_prop_scores.find(q, proj):
        e = d.get("edge_pct")
        if e is not None:
            if e > 0: n_pos_edge += 1
            elif e == 0: n_zero_edge += 1
            else: n_neg_edge += 1
        if d.get("lom_disabled") is True:
            n_lom_off += 1
        if (d.get("probability_method") or "").lower().startswith("distribution"):
            n_dist_method += 1
        st = d.get("stat_type") or "?"
        if d.get("model_projection") is not None:
            proj_means[st].append(float(d["model_projection"]))
        if e is not None:
            edge_means[st].append(float(e))

    proj_summary = {st: {"n": len(v), "mean": round(sum(v)/len(v), 3) if v else None,
                          "max": round(max(v), 2) if v else None}
                    for st, v in proj_means.items()}
    edge_summary = {st: {"n": len(v),
                          "mean": round(sum(v)/len(v), 3) if v else None}
                    for st, v in edge_means.items()}

    return {
        "label": label,
        "version_tag_filter": version_tag,
        "n_active_docs": n_total,
        "tiers": tiers,
        "stat_counts": stats,
        "pos_edge": n_pos_edge,
        "zero_edge": n_zero_edge,
        "neg_edge": n_neg_edge,
        "lom_disabled_count": n_lom_off,
        "distribution_method_count": n_dist_method,
        "projection_summary": proj_summary,
        "edge_summary": edge_summary,
    }


async def _trout_hrr(db, label: str):
    docs: list = []
    async for d in db.mlb_prop_scores.find(
        {"active": True,
         "player_name": {"$regex": "Trout", "$options": "i"},
         "stat_type": {"$regex": "hits.*runs.*rbi", "$options": "i"},
         "line": 0.5},
        {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1,
         "recommendation": 1, "tier": 1, "model_projection": 1,
         "model_sigma": 1, "p_model": 1, "edge_pct": 1,
         "probability_method": 1, "lom_disabled": 1,
         "version_tag": 1, "model_version": 1}):
        docs.append(d)
    return {"label": label, "rows": docs}


def _collect_train_report() -> dict:
    p = "/app/backend/models/mlb_hf/_train_report_v2.json"
    if not os.path.exists(p):
        return {"error": "no train report found"}
    with open(p) as fh: return json.load(fh)


def _feature_inventory() -> dict:
    """Old vs new feature counts and the new-only feature list."""
    backup_glob = "/app/backend/models/mlb_hf_backup_"
    backups = sorted(d for d in os.listdir("/app/backend/models")
                       if d.startswith("mlb_hf_backup_"))
    if not backups:
        return {"error": "no backup folder"}
    old_dir = f"/app/backend/models/{backups[-1]}"
    out = {"backup_used": old_dir, "stats": {}}
    for fn in sorted(os.listdir(old_dir)):
        if not fn.endswith(".pkl"): continue
        with open(os.path.join(old_dir, fn), "rb") as fh:
            old = pickle.load(fh)
        with open(os.path.join("/app/backend/models/mlb_hf", fn), "rb") as fh:
            new = pickle.load(fh)
        old_feats = set(old.get("features") or [])
        new_feats = set(new.get("features") or [])
        out["stats"][fn] = {
            "old_count": len(old_feats),
            "new_count": len(new_feats),
            "added": sorted(new_feats - old_feats),
            "removed": sorted(old_feats - new_feats),
            "old_version": old.get("version"),
            "new_version": new.get("version"),
            "old_r2_test": old.get("r2_test"),
            "new_r2_test": new.get("r2_test"),
        }
    return out


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print("=== STEP 1: BEFORE snapshot (active props with OLD artifacts) ===")
    before = await _snapshot_state(db, "BEFORE")
    trout_before = await _trout_hrr(db, "BEFORE")

    print("=== STEP 2: Feature inventory & train report ===")
    inv = _feature_inventory()
    train_rep = _collect_train_report()

    print("=== STEP 3: Recompute MLB with new artifacts ===")
    from services.scoring.recompute import recompute_sport
    t0 = time.time()
    recompute_result = await recompute_sport(
        db=db, sport="mlb", version_tag=NEW_VERSION_TAG, dry_run=False)
    dt = time.time() - t0

    print("=== STEP 4: AFTER snapshot ===")
    after = await _snapshot_state(db, "AFTER", version_tag=NEW_VERSION_TAG)
    trout_after = await _trout_hrr(db, "AFTER")

    blob = {
        "ts": TS,
        "new_version_tag": NEW_VERSION_TAG,
        "recompute_seconds": round(dt, 1),
        "recompute_result": (recompute_result if isinstance(recompute_result, dict)
                              else str(recompute_result)),
        "before": before,
        "after": after,
        "trout_before": trout_before,
        "trout_after": trout_after,
        "feature_inventory": inv,
        "train_report": train_rep,
    }
    with open(REPORT_JSON, "w") as fh:
        json.dump(blob, fh, indent=2, default=str)
    print(f"JSON → {REPORT_JSON}")

    # Markdown
    lines = []
    lines.append(f"# MLB HF v2 Retrain — Before/After Report\n")
    lines.append(f"_Generated: {TS} UTC_\n")
    lines.append(f"_New version_tag: `{NEW_VERSION_TAG}`_\n")
    lines.append(f"_Recompute took {dt:.1f}s_\n")
    lines.append("---\n")
    lines.append("## 1. Feature Inventory (old vs new artifacts)\n")
    if "stats" in inv:
        lines.append(f"_Backup directory: `{inv['backup_used']}`_\n")
        lines.append(
            "| Stat | Old #feat | New #feat | Old R²_te | New R²_te | Added |")
        lines.append("|---|---|---|---|---|---|")
        for fn, s in inv["stats"].items():
            stat = fn.replace("mlb_hf_", "").replace(".pkl", "")
            added = ", ".join(s["added"][:5]) + ("…" if len(s["added"]) > 5 else "")
            lines.append(f"| {stat} | {s['old_count']} | {s['new_count']} | "
                          f"{s['old_r2_test']} | {s['new_r2_test']} | {len(s['added'])} new ({added}) |")
    lines.append("")
    lines.append("## 2. Train Report (top-20 importances)\n")
    if "stats" in train_rep:
        for stat, m in train_rep["stats"].items():
            lines.append(f"### {stat}\n")
            lines.append(f"- samples: **{m['samples']:,}** · feat: **{m['feature_count']}**")
            lines.append(f"- R²_test: **{m['r2_test']}** · MAE_test: **{m['mae_test']}**")
            lines.append(f"- SC hit-rate: **{m['sc_hit_rate']*100:.1f}%**")
            if m.get("fully_zero_features"):
                lines.append(f"- 100%-imputed (fully zero) features: {len(m['fully_zero_features'])} → "
                              f"`{', '.join(m['fully_zero_features'][:8])}{'…' if len(m['fully_zero_features']) > 8 else ''}`")
            else:
                lines.append("- 100%-imputed features: **none** ✅")
            tops = m.get("top_features") or []
            lines.append("- Top-20 importances:")
            for k, v in tops:
                lines.append(f"  - `{k}` = {v:.4f}")
            lines.append("")
    lines.append("## 3. Live Score Pool — Tier Counts\n")
    lines.append("| Tier | BEFORE | AFTER |")
    lines.append("|---|---|---|")
    keys = sorted(set(before["tiers"]) | set(after["tiers"]))
    for k in keys:
        lines.append(f"| {k} | {before['tiers'].get(k, 0):,} | {after['tiers'].get(k, 0):,} |")
    lines.append("")
    lines.append("## 4. Stat-Type Counts\n")
    lines.append("| Stat | BEFORE | AFTER |")
    lines.append("|---|---|---|")
    keys = sorted(set(before["stat_counts"]) | set(after["stat_counts"]))
    for k in keys:
        lines.append(f"| {k} | {before['stat_counts'].get(k, 0):,} | {after['stat_counts'].get(k, 0):,} |")
    lines.append("")
    lines.append("## 5. Edge Distribution\n")
    lines.append(f"| | BEFORE | AFTER |")
    lines.append(f"|---|---|---|")
    lines.append(f"| pos edge | {before['pos_edge']:,} | {after['pos_edge']:,} |")
    lines.append(f"| zero edge | {before['zero_edge']:,} | {after['zero_edge']:,} |")
    lines.append(f"| neg edge | {before['neg_edge']:,} | {after['neg_edge']:,} |")
    lines.append(f"| LOM disabled (sanity) | {before['lom_disabled_count']:,} | {after['lom_disabled_count']:,} |")
    lines.append(f"| distribution-method (sanity) | {before['distribution_method_count']:,} | {after['distribution_method_count']:,} |")
    lines.append("")
    lines.append("## 6. Mike Trout HRR 0.5 — Before vs After\n")
    lines.append("### BEFORE\n")
    if trout_before["rows"]:
        for r in trout_before["rows"]:
            lines.append(f"- `{r}`")
    else:
        lines.append("(no Trout HRR 0.5 row found)")
    lines.append("\n### AFTER\n")
    if trout_after["rows"]:
        for r in trout_after["rows"]:
            lines.append(f"- `{r}`")
    else:
        lines.append("(no Trout HRR 0.5 row found)")
    lines.append("\n## 7. Sanity Invariants\n")
    rec = blob.get("recompute_result")
    if isinstance(rec, dict):
        for k, v in rec.items():
            lines.append(f"- **{k}**: {v}")
    lines.append("")

    with open(REPORT_MD, "w") as fh: fh.write("\n".join(lines))
    print(f"MARKDOWN → {REPORT_MD}")


if __name__ == "__main__":
    asyncio.run(main())

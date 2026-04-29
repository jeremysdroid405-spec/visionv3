"""
MLB HF Old-vs-New Comparison Harness
=====================================
Scores the EXACT SAME live slate twice — once with the v1 backup
artifacts and once with the v2 statcast artifacts — and emits a
diff report.

Workflow:
  1. Backup new (v2) artifacts to a swap location.
  2. Restore the v1 (`/app/backend/models/mlb_hf_backup_<ts>/`)
     artifacts into `/app/backend/models/mlb_hf/`.
  3. Run `recompute_sport(dry_run=False, version_tag=cmp-old-<ts>)` —
     writes a NEW set of MLB prop_scores under a one-off tag.
  4. Restore the v2 artifacts (live state preserved).
  5. Read both tagged scoring batches, join on
     `event_id|player|stat|line|recommendation`, diff every metric.
  6. Emit markdown + json report into `/app/backend/data/snapshots/`.

NEVER touches live artifacts permanently. NEVER modifies gates.
NEVER changes NBA / frontend / LOM toggles.
"""
from __future__ import annotations
import asyncio, json, os, shutil, sys, time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv; load_dotenv()
from motor.motor_asyncio import AsyncIOMotorClient

LIVE_DIR = "/app/backend/models/mlb_hf"
SWAP_DIR = "/tmp/_mlb_hf_v2_swap"
BACKUP_DIR = "/app/backend/models/mlb_hf_backup_20260429T060326"

OUT_DIR = "/app/backend/data/snapshots"
os.makedirs(OUT_DIR, exist_ok=True)
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
REPORT_MD = os.path.join(OUT_DIR, f"mlb_compare_old_vs_new_{TS}.md")
REPORT_JSON = os.path.join(OUT_DIR, f"mlb_compare_old_vs_new_{TS}.json")

NEW_VERSION_TAG = "mlb-hf-v2-20260429T062033"  # already in DB
OLD_VERSION_TAG = f"cmp-old-{TS}"

WATCH_PLAYERS = [
    ("Mike Trout", None),
    ("Aaron Judge", None),
    ("Yordan Alvarez", None),
    ("Brandon Sproat", None),
]


def _swap(src: str, dst: str) -> None:
    """Move all .pkl + .json files from src → dst."""
    os.makedirs(dst, exist_ok=True)
    for fn in os.listdir(src):
        if fn.endswith(".pkl") or fn.endswith(".json"):
            shutil.move(os.path.join(src, fn), os.path.join(dst, fn))


def _copy(src: str, dst: str) -> None:
    """Copy .pkl files (NOT JSON metadata) from src → dst."""
    os.makedirs(dst, exist_ok=True)
    for fn in os.listdir(src):
        if fn.endswith(".pkl"):
            shutil.copy2(os.path.join(src, fn), os.path.join(dst, fn))


def _key(d: dict) -> str:
    return (f"{d.get('event_id')}|{d.get('player_name')}|"
            f"{d.get('stat_type')}|{d.get('line')}|{d.get('recommendation')}")


def _f(v):
    if v is None: return None
    try: return float(v)
    except (TypeError, ValueError): return None


async def _load_scores(db, version_tag: str) -> dict:
    out: dict = {}
    proj = {
        "_id": 0, "event_id": 1, "player_name": 1, "stat_type": 1,
        "line": 1, "recommendation": 1, "tier": 1,
        "model_projection": 1, "model_sigma": 1,
        "p_distribution": 1, "p_true_active": 1,
        "distribution_p_over": 1, "distribution_effective_mu": 1,
        "distribution_sigma": 1, "distribution_kind": 1,
        "edge_pct": 1, "edge_vs_fair": 1, "fair_prob": 1,
        "true_probability": 1, "tier_reference_odds": 1,
        "tier_reference_book": 1, "anchor_book": 1,
        "hit_rate_over": 1, "hit_rate_under": 1, "cv": 1,
        "lom_disabled": 1, "probability_method": 1,
        "expected_ip_used": 1, "mu_pitcher_workload_anchored": 1,
        "mu_active_baseline_applied": 1, "feature_health": 1,
        "model_version": 1, "version_tag": 1,
    }
    async for d in db.mlb_prop_scores.find(
            {"version_tag": version_tag, "active": True}, proj):
        out[_key(d)] = d
    return out


async def _run_old_recompute(db) -> dict:
    """Swap in OLD artifacts, run recompute, swap NEW back. Returns
    the recompute result dict."""
    # Step 1: stash NEW
    print(f"[swap] new artifacts → {SWAP_DIR}")
    if os.path.exists(SWAP_DIR):
        shutil.rmtree(SWAP_DIR)
    os.makedirs(SWAP_DIR)
    for fn in os.listdir(LIVE_DIR):
        if fn.endswith(".pkl") or fn.endswith(".json"):
            shutil.move(os.path.join(LIVE_DIR, fn),
                        os.path.join(SWAP_DIR, fn))

    # Step 2: copy OLD into LIVE_DIR
    print(f"[swap] old artifacts {BACKUP_DIR} → {LIVE_DIR}")
    for fn in os.listdir(BACKUP_DIR):
        if fn.endswith(".pkl"):
            shutil.copy2(os.path.join(BACKUP_DIR, fn),
                          os.path.join(LIVE_DIR, fn))

    # Step 3: reset model singleton & recompute
    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None
    from services.scoring.recompute import recompute_sport
    print(f"[recompute] writing OLD-side scores under {OLD_VERSION_TAG}")
    t0 = time.time()
    result = await recompute_sport(
        db=db, sport="mlb", version_tag=OLD_VERSION_TAG, dry_run=False)
    dt = time.time() - t0
    print(f"  took {dt:.1f}s")

    # Step 4: restore NEW
    print(f"[swap] restoring v2 artifacts to {LIVE_DIR}")
    for fn in os.listdir(LIVE_DIR):
        if fn.endswith(".pkl") or fn.endswith(".json"):
            os.remove(os.path.join(LIVE_DIR, fn))
    for fn in os.listdir(SWAP_DIR):
        shutil.move(os.path.join(SWAP_DIR, fn),
                    os.path.join(LIVE_DIR, fn))
    shutil.rmtree(SWAP_DIR)

    # Reset model singleton so subsequent code uses v2.
    import services.mlb_high_friction_model as hfm  # noqa: F811
    hfm._mlb_hf_instance = None
    return {"recompute_seconds": round(dt, 1),
             "recompute_result": result if isinstance(result, dict) else str(result)}


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    # --- A. Run OLD-side recompute (live artifacts swap-in/out) ---
    swap_meta = await _run_old_recompute(db)

    # --- B. Load both score sets ---
    print(f"[load] new scores tag={NEW_VERSION_TAG}")
    new_map = await _load_scores(db, NEW_VERSION_TAG)
    print(f"  loaded {len(new_map):,}")
    print(f"[load] old scores tag={OLD_VERSION_TAG}")
    old_map = await _load_scores(db, OLD_VERSION_TAG)
    print(f"  loaded {len(old_map):,}")

    # --- C. Build paired diff ---
    keys = sorted(set(old_map) & set(new_map))
    rows: list = []
    only_old: list = []
    only_new: list = []
    for k in old_map.keys() - new_map.keys():
        only_old.append(old_map[k])
    for k in new_map.keys() - old_map.keys():
        only_new.append(new_map[k])
    for k in keys:
        o = old_map[k]; n = new_map[k]
        op = _f(o.get("model_projection"))
        np_ = _f(n.get("model_projection"))
        oe = _f(o.get("edge_pct"))
        ne = _f(n.get("edge_pct"))
        opd = _f(o.get("p_distribution") or o.get("distribution_p_over"))
        npd = _f(n.get("p_distribution") or n.get("distribution_p_over"))
        rows.append({
            "key": k,
            "player_name": n.get("player_name"),
            "stat_type": n.get("stat_type"),
            "line": n.get("line"),
            "recommendation": n.get("recommendation"),
            "tier_old": o.get("tier"),
            "tier_new": n.get("tier"),
            "ref_odds": n.get("tier_reference_odds"),
            "ref_book": n.get("tier_reference_book"),
            "fair_prob": _f(n.get("fair_prob")),  # market TP same on both runs
            "proj_old": op,
            "proj_new": np_,
            "delta_proj": (None if op is None or np_ is None else round(np_ - op, 4)),
            "p_dist_old": opd,
            "p_dist_new": npd,
            "delta_p":   (None if opd is None or npd is None else round(npd - opd, 4)),
            "edge_old": oe,
            "edge_new": ne,
            "delta_edge": (None if oe is None or ne is None else round(ne - oe, 2)),
            "hit_rate_over": _f(n.get("hit_rate_over")),
            "cv": _f(n.get("cv")),
            "sigma_old": _f(o.get("model_sigma")),
            "sigma_new": _f(n.get("model_sigma")),
        })

    # --- D. Sort buckets ---
    by_dproj = sorted([r for r in rows if r["delta_proj"] is not None],
                       key=lambda r: -abs(r["delta_proj"]))[:25]
    by_dedge = sorted([r for r in rows if r["delta_edge"] is not None],
                       key=lambda r: -abs(r["delta_edge"]))[:25]

    # Tier-flip lists: old tiered (SH/FL/WZ) → new unqualified, and vice-versa
    TIERED = {"safe_haven", "front_lines", "war_zone"}
    old_liked_new_rejects = [r for r in rows
                                if r["tier_old"] in TIERED
                                and r["tier_new"] not in TIERED]
    new_likes_old_rejects = [r for r in rows
                                if r["tier_new"] in TIERED
                                and r["tier_old"] not in TIERED]

    # --- E. Watch-list spot checks ---
    watch_results: dict = {}
    for player, _ in WATCH_PLAYERS:
        hits = [r for r in rows if r["player_name"]
                  and player.lower() in (r["player_name"] or "").lower()]
        watch_results[player] = sorted(
            hits,
            key=lambda r: (-abs(r["delta_edge"] or 0), r["stat_type"] or ""),
        )[:6]

    # --- F. Tier counts on the SAME slate ---
    def _counts(score_map):
        c: dict = defaultdict(int)
        for d in score_map.values(): c[d.get("tier") or "(none)"] += 1
        return dict(c)
    tier_counts_old = _counts(old_map)
    tier_counts_new = _counts(new_map)

    blob = {
        "ts": TS,
        "old_version_tag": OLD_VERSION_TAG,
        "new_version_tag": NEW_VERSION_TAG,
        "swap_meta": swap_meta,
        "n_paired": len(rows),
        "only_old_count": len(only_old),
        "only_new_count": len(only_new),
        "tier_counts_old": tier_counts_old,
        "tier_counts_new": tier_counts_new,
        "top_proj_movers": by_dproj,
        "top_edge_movers": by_dedge,
        "old_liked_new_rejects": sorted(
            old_liked_new_rejects,
            key=lambda r: (r["tier_old"] != "safe_haven", -(r["edge_old"] or 0))),
        "new_likes_old_rejects": sorted(
            new_likes_old_rejects,
            key=lambda r: (r["tier_new"] != "safe_haven", -(r["edge_new"] or 0))),
        "watch_results": watch_results,
    }

    with open(REPORT_JSON, "w") as fh:
        json.dump(blob, fh, indent=2, default=str)
    print(f"JSON → {REPORT_JSON}")

    # --- G. Markdown ---
    L = []
    L.append(f"# MLB HF v1 vs v2 — Live Slate Comparison\n")
    L.append(f"_Generated: {TS} UTC_\n")
    L.append(f"_old version_tag (this run): `{OLD_VERSION_TAG}`_\n")
    L.append(f"_new version_tag (existing): `{NEW_VERSION_TAG}`_\n")
    L.append(f"_paired props: **{len(rows):,}**, only-old: {len(only_old)}, only-new: {len(only_new)}_\n")
    L.append(f"_OLD recompute took {swap_meta['recompute_seconds']}s_\n")
    L.append("---\n")

    L.append("## 1. Tier counts on the SAME slate\n")
    L.append(f"| tier | OLD (v1) | NEW (v2) |")
    L.append("|---|---|---|")
    for k in sorted(set(tier_counts_old) | set(tier_counts_new)):
        L.append(f"| {k} | {tier_counts_old.get(k, 0):,} | {tier_counts_new.get(k, 0):,} |")
    L.append("")

    def _fmt_table(rs, title):
        L.append(f"## {title}\n")
        L.append("| player | stat | line | side | tier old → new | proj old → new (Δ) | p_dist old → new (Δ) | edge old → new (Δ) |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r in rs:
            L.append(
                f"| {r['player_name']} | {r['stat_type']} | {r['line']} | "
                f"{r['recommendation']} | {r['tier_old']} → {r['tier_new']} | "
                f"{r['proj_old']} → {r['proj_new']} ({r['delta_proj']:+.3f}) | "
                f"{r['p_dist_old']} → {r['p_dist_new']} ({(r['delta_p'] or 0):+.4f}) | "
                f"{r['edge_old']:+.1f}% → {r['edge_new']:+.1f}% ({(r['delta_edge'] or 0):+.1f}) |"
            )
        L.append("")

    _fmt_table(by_dproj, "2. Top-25 biggest projection movers (|Δ proj|)")
    _fmt_table(by_dedge, "3. Top-25 biggest edge movers (|Δ edge|)")

    L.append("## 4. Picks OLD liked, NEW rejects (tier flip out of SH/FL/WZ)\n")
    L.append(f"_count: {len(old_liked_new_rejects)}_\n")
    if old_liked_new_rejects:
        _fmt_table(old_liked_new_rejects[:50], "4a. Top 50 old-likes-new-rejects")
    L.append("## 5. Picks NEW likes, OLD rejects (tier flip into SH/FL/WZ)\n")
    L.append(f"_count: {len(new_likes_old_rejects)}_\n")
    if new_likes_old_rejects:
        _fmt_table(new_likes_old_rejects[:50], "5a. Top 50 new-likes-old-rejects")

    L.append("## 6. Watch-list spot checks\n")
    for player, _ in WATCH_PLAYERS:
        L.append(f"### {player}\n")
        rs = watch_results.get(player) or []
        if not rs:
            L.append("(no rows on the live slate)\n")
            continue
        _fmt_table(rs, f"{player} props")
    L.append("")

    L.append("## 7. Summary\n")
    n_pos_old = sum(1 for r in rows if (r['edge_old'] or 0) > 0)
    n_pos_new = sum(1 for r in rows if (r['edge_new'] or 0) > 0)
    n_old_tier = sum(1 for r in rows if r['tier_old'] in TIERED)
    n_new_tier = sum(1 for r in rows if r['tier_new'] in TIERED)
    proj_drops = [r for r in rows if (r['delta_proj'] or 0) < 0]
    proj_rises = [r for r in rows if (r['delta_proj'] or 0) > 0]
    L.append(f"- positive-edge props: OLD={n_pos_old:,}  ·  NEW={n_pos_new:,}")
    L.append(f"- tiered props (SH/FL/WZ): OLD={n_old_tier:,}  ·  NEW={n_new_tier:,}")
    L.append(f"- projection went DOWN on {len(proj_drops):,} props, UP on {len(proj_rises):,} props")
    L.append(f"- old-liked → new-rejects: {len(old_liked_new_rejects):,}")
    L.append(f"- new-likes → old-rejects: {len(new_likes_old_rejects):,}")
    L.append("")

    with open(REPORT_MD, "w") as fh: fh.write("\n".join(L))
    print(f"MARKDOWN → {REPORT_MD}")


if __name__ == "__main__":
    asyncio.run(main())

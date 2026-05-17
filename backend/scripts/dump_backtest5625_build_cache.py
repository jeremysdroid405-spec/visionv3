"""Build a fast-load pickle cache of the 2026-05-06 backtest dataset."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.mlb_replay_multi_tier_eval import (
    REPLAY_ENGINE_VERSION, FEATURE_CACHE_VERSION, TIER_CONFIGS,
    eval_safe_haven, eval_front_lines, eval_war_zone,
)
from services.replay.mlb_replay_engine import SCORING_CONFIG_VERSION
from services.replay.historical_alt_odds_ingest import normalize_player_name

GAME_DATE = "2026-05-06"
SNAPSHOT  = f"{GAME_DATE}T11:00:00Z"
ROOT      = Path("/app/backend/backtest5625")
CACHE     = ROOT / "cache"

EVALS = {"safe_haven": eval_safe_haven,
         "front_lines": eval_front_lines,
         "war_zone": eval_war_zone}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def amain():
    CACHE.mkdir(parents=True, exist_ok=True)
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    t0 = time.time()
    print(f"Building cache for {GAME_DATE} @ {SNAPSHOT}...")

    # 1. All model outputs
    print(f"  [1/4] model_outputs.pkl ...", flush=True)
    cursor = db.mlb_replay_model_outputs.find(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT}, {"_id": 0})
    model_outputs = []
    async for r in cursor: model_outputs.append(r)
    with (CACHE / "model_outputs.pkl").open("wb") as f:
        pickle.dump(model_outputs, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"        {len(model_outputs):,} rows")

    # 2. Qualified per tier (recompute deterministically from model_outputs)
    print(f"  [2/4] qualified_<tier>.pkl ...", flush=True)
    qual = {t: [] for t in EVALS}
    for r in model_outputs:
        for t, fn in EVALS.items():
            passed, failed = fn(r)
            if passed:
                qual[t].append({**r, "_failed_gates": failed})
    for tier, picks in qual.items():
        with (CACHE / f"qualified_{tier}.pkl").open("wb") as f:
            pickle.dump(picks, f, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"        qualified_{tier}.pkl  → {len(picks):,} picks")

    # 3. Game logs
    print(f"  [3/4] game_logs.pkl ...", flush=True)
    pipeline = [
        {"$project": {"logs": "$bdl_game_logs",
                      "display_name": 1, "player_name": 1, "mlb_full_name": 1}},
        {"$unwind": "$logs"},
        {"$project": {
            "d": {"$ifNull": [
                {"$substr": ["$logs.date", 0, 10]},
                {"$substr": ["$logs.game_date", 0, 10]}]},
            "stats": "$logs",
            "name_canon": {"$ifNull": ["$display_name",
                            {"$ifNull": ["$player_name", "$mlb_full_name"]}]},
        }},
        {"$match": {"d": GAME_DATE}},
    ]
    game_logs = []
    async for r in db.mlb_master_hub_2026.aggregate(pipeline, allowDiskUse=True):
        nk = normalize_player_name(r.get("name_canon") or "")
        if not nk: continue
        game_logs.append({
            "player_name_normalized": nk,
            "player_name": r.get("name_canon"),
            "ts": r["stats"].get("date") or r["stats"].get("game_date"),
            "game_id": r["stats"].get("game_id"),
            "team": r["stats"].get("team"),
            "opponent": r["stats"].get("opponent"),
            "hits": r["stats"].get("hits"),
            "total_bases": r["stats"].get("total_bases"),
            "runs": r["stats"].get("runs"),
            "rbis": r["stats"].get("rbis"),
            "home_runs": r["stats"].get("home_runs"),
            "walks": r["stats"].get("walks"),
            "strikeouts": r["stats"].get("strikeouts"),
            "at_bats": r["stats"].get("at_bats"),
            "plate_appearances": r["stats"].get("plate_appearances"),
            "pitcher_strikeouts": r["stats"].get("pitcher_strikeouts"),
            "pitcher_walks": r["stats"].get("pitcher_walks"),
            "earned_runs": r["stats"].get("earned_runs"),
            "pitcher_outs": r["stats"].get("pitcher_outs"),
        })
    with (CACHE / "game_logs.pkl").open("wb") as f:
        pickle.dump(game_logs, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"        {len(game_logs):,} rows")

    # 4. Manifest with checksums + version pins
    print(f"  [4/4] manifest.json ...", flush=True)
    cli.close()
    files = {}
    for name in ("model_outputs.pkl", "qualified_safe_haven.pkl",
                 "qualified_front_lines.pkl", "qualified_war_zone.pkl",
                 "game_logs.pkl"):
        p = CACHE / name
        files[name] = {
            "bytes": p.stat().st_size,
            "sha256": _sha(p),
        }
    manifest = {
        "game_date": GAME_DATE,
        "snapshot_iso": SNAPSHOT,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_build_s": round(time.time() - t0, 3),
        "version_pins": {
            "scoring_config_version": SCORING_CONFIG_VERSION,
            "replay_engine_version":  REPLAY_ENGINE_VERSION,
            "feature_cache_version":  FEATURE_CACHE_VERSION,
            "tier_gate_configs": {t: TIER_CONFIGS[t]["version"] for t in EVALS},
        },
        "counts": {
            "model_outputs":           len(model_outputs),
            "qualified_safe_haven":    len(qual["safe_haven"]),
            "qualified_front_lines":   len(qual["front_lines"]),
            "qualified_war_zone":      len(qual["war_zone"]),
            "game_logs":               len(game_logs),
        },
        "files": files,
        "load_api": "from backtest5625.cache.load import load; data = load()",
    }
    with (CACHE / "manifest.json").open("w") as f:
        json.dump(manifest, f, indent=2)
    print(f"        manifest.json written")

    # 5. Loader module
    loader = f'''"""Quick-load cached 2026-05-06 backtest dataset.

Usage:
    from backtest5625.cache.load import load
    data = load()
    data["model_outputs"]          # list of 37,691 dicts
    data["qualified_safe_haven"]   # list of 669 picks
    data["qualified_front_lines"]  # list of 250 picks
    data["qualified_war_zone"]     # list of 1,080 picks
    data["game_logs"]              # list of 553 outcome rows
    data["manifest"]               # version pins, checksums, counts
"""
import json
import pickle
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent


def load(verify_checksums: bool = False) -> dict:
    t0 = time.time()
    out = {{}}
    with (CACHE_DIR / "model_outputs.pkl").open("rb") as f:
        out["model_outputs"] = pickle.load(f)
    for tier in ("safe_haven", "front_lines", "war_zone"):
        with (CACHE_DIR / f"qualified_{{tier}}.pkl").open("rb") as f:
            out[f"qualified_{{tier}}"] = pickle.load(f)
    with (CACHE_DIR / "game_logs.pkl").open("rb") as f:
        out["game_logs"] = pickle.load(f)
    with (CACHE_DIR / "manifest.json").open() as f:
        out["manifest"] = json.load(f)
    if verify_checksums:
        import hashlib
        for name, meta in out["manifest"]["files"].items():
            h = hashlib.sha256()
            with (CACHE_DIR / name).open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            assert h.hexdigest() == meta["sha256"], f"checksum mismatch: {{name}}"
    out["_load_elapsed_ms"] = round((time.time() - t0) * 1000, 2)
    return out


if __name__ == "__main__":
    d = load(verify_checksums=True)
    m = d["manifest"]
    print(f"Loaded in {{d['_load_elapsed_ms']}} ms (checksums verified)")
    print(f"  game_date     : {{m['game_date']}}")
    print(f"  built_at_utc  : {{m['built_at_utc']}}")
    print(f"  counts        : {{m['counts']}}")
'''
    (CACHE / "load.py").write_text(loader)
    (CACHE / "__init__.py").write_text("")

    # 6. Summary
    print()
    print("=" * 78)
    print("CACHE BUILD COMPLETE")
    print("=" * 78)
    print(f"  location : {CACHE}")
    print(f"  built_in : {round(time.time() - t0, 2)}s")
    print()
    total_bytes = sum(v["bytes"] for v in files.values())
    print(f"  {'File':<32}  {'Size':>10}  {'SHA-256 (16)':<18}")
    for name, meta in files.items():
        sz = meta["bytes"]
        sz_str = f"{sz/1024:.1f} KB" if sz < 1024*1024 else f"{sz/1024/1024:.2f} MB"
        print(f"  {name:<32}  {sz_str:>10}  {meta['sha256'][:16]}")
    print(f"  {'TOTAL':<32}  {total_bytes/1024/1024:>9.2f} MB")
    print()
    print(f"  Load API: `from backtest5625.cache.load import load; data = load()`")


if __name__ == "__main__":
    asyncio.run(amain())

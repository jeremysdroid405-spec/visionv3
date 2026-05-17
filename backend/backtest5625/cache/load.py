"""Quick-load cached 2026-05-06 backtest dataset.

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
    out = {}
    with (CACHE_DIR / "model_outputs.pkl").open("rb") as f:
        out["model_outputs"] = pickle.load(f)
    for tier in ("safe_haven", "front_lines", "war_zone"):
        with (CACHE_DIR / f"qualified_{tier}.pkl").open("rb") as f:
            out[f"qualified_{tier}"] = pickle.load(f)
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
            assert h.hexdigest() == meta["sha256"], f"checksum mismatch: {name}"
    out["_load_elapsed_ms"] = round((time.time() - t0) * 1000, 2)
    return out


if __name__ == "__main__":
    d = load(verify_checksums=True)
    m = d["manifest"]
    print(f"Loaded in {d['_load_elapsed_ms']} ms (checksums verified)")
    print(f"  game_date     : {m['game_date']}")
    print(f"  built_at_utc  : {m['built_at_utc']}")
    print(f"  counts        : {m['counts']}")

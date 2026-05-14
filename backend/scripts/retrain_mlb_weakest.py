"""Retrain the 4 weakest MLB HF models (2026-05-14).

Targets (R²_test from /app/backend/models/mlb_hf/_train_report_v3.json):
    stolen_bases    R² = 0.007  (essentially random)
    doubles         R² = 0.012
    rbis            R² = 0.072
    home_runs       R² = 0.079

Process:
  1. Unlock the model directory (it's read-only per the lock manifest).
  2. Run `MLBHighFrictionModel.train(stat)` for each target.
  3. Save the new pickles via `save_models()`.
  4. Write a v4 training report so the metrics improvement is auditable.
  5. Re-lock the directory.

Run from /app/backend with:
    python3 scripts/retrain_mlb_weakest.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# Bootstrap the same env the live service uses.
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("retrain_mlb")

MODEL_DIR = "/app/backend/models/mlb_hf"
WEAKEST_TARGETS = ["stolen_bases", "doubles", "rbis", "home_runs"]


def unlock_model_dir() -> None:
    """chmod the dir + all .pkl files to writable so `save_models` can
    overwrite them. The .LOCKED manifest is preserved (we'll update it
    after retrain)."""
    os.chmod(MODEL_DIR, 0o755)
    for fname in os.listdir(MODEL_DIR):
        path = os.path.join(MODEL_DIR, fname)
        if os.path.isfile(path):
            os.chmod(path, 0o644)
    logger.info(f"[UNLOCK] {MODEL_DIR} → writable")


def relock_model_dir() -> None:
    """Restore read-only mode."""
    for fname in os.listdir(MODEL_DIR):
        path = os.path.join(MODEL_DIR, fname)
        if os.path.isfile(path):
            os.chmod(path, 0o444)
    os.chmod(MODEL_DIR, 0o555)
    logger.info(f"[RELOCK] {MODEL_DIR} → read-only")


def previous_metrics() -> dict:
    """Return R²_test/MAE_test from v3 report for diffing."""
    try:
        with open(os.path.join(MODEL_DIR, "_train_report_v3.json")) as f:
            r = json.load(f)
        return {
            stat: {
                "r2_test": v.get("r2_test"),
                "mae_test": v.get("mae_test"),
                "samples": v.get("samples"),
            }
            for stat, v in (r.get("stats") or {}).items()
        }
    except FileNotFoundError:
        return {}


def main() -> None:
    mongo = MongoClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]

    from services.mlb_high_friction_model import MLBHighFrictionModel
    hf = MLBHighFrictionModel(db)
    hf.load_models()  # bring up existing weights so non-retrained stats remain

    prev = previous_metrics()
    new_report: dict = {
        "trained_at":     datetime.now(timezone.utc).isoformat(),
        "version":        "MLB_HF_v4.0_targeted_2026_05_14",
        "scope":          "weakest_4_only",
        "targets":        WEAKEST_TARGETS,
        "stats":          {},
        "previous_v3":    {s: prev.get(s, {}) for s in WEAKEST_TARGETS},
    }

    unlock_model_dir()
    try:
        for stat in WEAKEST_TARGETS:
            t0 = time.time()
            logger.info(f"[RETRAIN] === {stat} starting ===")
            try:
                metrics = hf.train(stat)
            except Exception as exc:  # noqa: BLE001
                metrics = {"error": str(exc)}
            elapsed = round(time.time() - t0, 1)
            metrics["elapsed_s"] = elapsed

            # Diff vs v3
            v3 = prev.get(stat, {})
            test = metrics.get("test", {}) or {}
            new_r2 = test.get("r2")
            old_r2 = v3.get("r2_test")
            if new_r2 is not None and old_r2 is not None:
                delta_r2 = round(new_r2 - old_r2, 4)
                metrics["delta_r2_vs_v3"] = delta_r2
                logger.info(
                    f"[RETRAIN] {stat}: R²={new_r2:.4f} (Δ={delta_r2:+.4f}) "
                    f"MAE={test.get('mae'):.4f} elapsed={elapsed}s"
                )
            else:
                logger.info(f"[RETRAIN] {stat}: result={metrics}")
            new_report["stats"][stat] = metrics

        # Persist new pickles for the 4 targets only.
        # save_models() writes every stat in self.models; we only
        # mutated the 4 trained ones — others are still the loaded v3
        # weights, so we filter to avoid resaving unchanged blobs.
        for stat in WEAKEST_TARGETS:
            if stat not in hf.models:
                logger.warning(f"[RETRAIN] {stat} missing from hf.models — skipping save")
                continue
            data = {
                "model":      hf.models[stat],
                "scaler":     hf.scalers[stat],
                "features":   hf.feature_cols[stat],
                "version":    new_report["version"],
                "trained_at": new_report["trained_at"],
            }
            import pickle
            path = os.path.join(MODEL_DIR, f"mlb_hf_{stat}.pkl")
            with open(path, "wb") as f:
                pickle.dump(data, f)
            logger.info(f"[RETRAIN] saved {path}")

        # Write the v4 report alongside v3 (keeps audit trail).
        v4_path = os.path.join(MODEL_DIR, "_train_report_v4_2026_05_14.json")
        with open(v4_path, "w") as f:
            json.dump(new_report, f, indent=2)
        logger.info(f"[RETRAIN] wrote {v4_path}")

    finally:
        relock_model_dir()

    print()
    print("=== TRAINING SUMMARY ===")
    print(f'{"stat":<20} {"n":>8} {"R²_v3":>8} {"R²_v4":>8} {"Δ R²":>8} {"MAE_v4":>8}')
    print("-" * 65)
    for stat in WEAKEST_TARGETS:
        m = new_report["stats"].get(stat, {})
        v3m = prev.get(stat, {})
        t = m.get("test", {}) or {}
        new_r2 = t.get("r2")
        new_mae = t.get("mae")
        n = m.get("n_samples")
        old_r2 = v3m.get("r2_test")
        d = (new_r2 - old_r2) if (new_r2 is not None and old_r2 is not None) else None
        print(
            f'{stat:<20} '
            f'{n if n is not None else "-":>8} '
            f'{old_r2 if old_r2 is not None else "-":>8} '
            f'{new_r2 if new_r2 is not None else "-":>8} '
            f'{(f"{d:+.4f}" if d is not None else "-"):>8} '
            f'{new_mae if new_mae is not None else "-":>8}'
        )


if __name__ == "__main__":
    main()

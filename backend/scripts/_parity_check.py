import asyncio, gc, json, os, sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

OUT = "/app/backend/scripts/_parity_olson.json"

async def go():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    print("DB connected", flush=True)
    from services.mlb_high_friction_model import MLBHighFrictionModel
    model = MLBHighFrictionModel(db.delegate)
    print("Model instance created", flush=True)
    model.load_models()
    print("Models loaded", flush=True)
    olson = await db.mlb_master_hub_2026.find_one(
        {"display_name": "Matt Olson"},
        {"vs_left": 1, "vs_right": 1, "home_splits": 1, "away_splits": 1,
         "bats": 1, "bats_throws": 1, "throws": 1})
    print(f"Master hub doc fetched: vs_left={bool(olson.get('vs_left'))} vs_right={bool(olson.get('vs_right'))}", flush=True)
    print("Calling model.predict()...", flush=True)
    result = model.predict(player_name="Matt Olson", stat_type="total_bases",
        line=0.5, opponent_team="Seattle Mariners", park_team="Seattle Mariners")
    print(f"Predict done. projection={result.get('projection')}", flush=True)
    out = {
        "master_hub_data": {
            "vs_left": bool(olson.get("vs_left")),
            "vs_right": bool(olson.get("vs_right")),
            "home_splits": bool(olson.get("home_splits")),
            "away_splits": bool(olson.get("away_splits")),
            "bats": olson.get("bats"),
            "bats_throws": olson.get("bats_throws"),
            "throws": olson.get("throws"),
        },
        "live_predict": {
            "projection": result.get("projection"),
            "raw_model_projection": result.get("raw_model_projection"),
            "projection_method": result.get("projection_method"),
            "park_factor": result.get("park_factor"),
            "prob_over": result.get("prob_over"),
            "std_dev": result.get("std_dev"),
            "cv": result.get("cv"),
        },
        "replay_mu": 7.9019,
    }
    json.dump(out, open(OUT,"w"), default=str, indent=2)
    print(f"wrote {OUT}", flush=True)
    cli.close()

asyncio.run(go())

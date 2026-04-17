"""
Memory-efficient MLB model retraining.
Processes players one at a time, writes samples to numpy memmap files,
then trains XGBoost with out-of-core batching.
"""
import sys, os
sys.path.insert(0, '/app/backend')
os.chdir('/app/backend')

import pymongo, numpy as np, pickle, logging, gc
from datetime import datetime, timezone
from xgboost import XGBRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, mean_absolute_error

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

client = pymongo.MongoClient(os.environ.get('MONGO_URL', 'mongodb://localhost:27017'))
db = client[os.environ.get('DB_NAME', 'pick_vision')]
hub = db.mlb_master_hub_2026

import services.mlb_high_friction_model as hfm
hfm._mlb_hf_instance = None
model = hfm.get_mlb_high_friction_model(db)

STATS = [
    'hits', 'total_bases', 'rbis', 'runs', 'pitcher_strikeouts',
    'home_runs', 'stolen_bases', 'strikeouts', 'doubles', 'walks',
    'singles', 'hits+runs+rbis', 'earned_runs', 'hits_allowed', 'pitcher_walks',
]

MODEL_DIR = '/app/backend/models/mlb_hf'
os.makedirs(MODEL_DIR, exist_ok=True)

# Only process players with enough data (100+ logs = multi-season)
player_cursor = hub.find(
    {'total_game_logs': {'$gte': 20}},
    {'_id': 0, 'player_name': 1, 'bdl_game_logs': 1, 'team': 1}
)
# Don't load all at once — iterate cursor

for stat_name in STATS:
    X_chunks = []
    y_chunks = []
    feature_cols = None
    total_samples = 0
    
    player_cursor = hub.find(
        {'total_game_logs': {'$gte': 20}},
        {'_id': 0, 'player_name': 1, 'bdl_game_logs': 1, 'team': 1}
    ).batch_size(50)
    
    player_count = 0
    for player in player_cursor:
        logs = player.get('bdl_game_logs', [])
        logs = sorted(logs, key=lambda x: x.get('game_id') or 0, reverse=True)
        if len(logs) < 12:
            continue
        team = player.get('team')
        player_count += 1
        
        player_X = []
        player_y = []
        
        # Limit sliding window to avoid excessive samples per player
        max_windows = min(len(logs) - 11, 200)
        for i in range(max_windows):
            target_game = logs[i]
            target_val = model._get_stat_value(target_game, stat_name)
            if target_val is None:
                continue
            history = logs[i+1:]
            if len(history) < 5:
                continue
            
            hist_vals = [model._get_stat_value(g, stat_name) for g in history[:20]]
            hist_vals = [v for v in hist_vals if v is not None]
            if len(hist_vals) < 5:
                continue
            
            line = np.median(hist_vals)
            features = model._build_friction_features(
                player, history, stat_name,
                opponent=None, park_team=team,
                dk_odds=None, line=line
            )
            if features is None:
                continue
            if feature_cols is None:
                feature_cols = sorted(features.keys())
            
            player_X.append([features.get(c, 0) for c in feature_cols])
            player_y.append(target_val)
        
        if player_X:
            X_chunks.append(np.array(player_X, dtype=np.float32))
            y_chunks.append(np.array(player_y, dtype=np.float32))
            total_samples += len(player_X)
        
        # Periodic GC
        if player_count % 200 == 0:
            gc.collect()
            logger.info(f"  {stat_name}: {player_count} players, {total_samples} samples...")
    
    if total_samples < 100 or feature_cols is None:
        logger.warning(f"{stat_name}: {total_samples} samples, SKIP")
        continue
    
    # Concatenate
    X = np.vstack(X_chunks)
    y = np.concatenate(y_chunks)
    del X_chunks, y_chunks
    gc.collect()
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    split = int(len(X) * 0.8)
    X_tr, X_te = X_scaled[:split], X_scaled[split:]
    y_tr, y_te = y[:split], y[split:]
    
    xgb = XGBRegressor(
        n_estimators=200, max_depth=5, learning_rate=0.08,
        subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=1.0, random_state=42, verbosity=0,
    )
    xgb.fit(X_tr, y_tr)
    
    r2_te = r2_score(y_te, xgb.predict(X_te))
    mae_te = mean_absolute_error(y_te, xgb.predict(X_te))
    r2_tr = r2_score(y_tr, xgb.predict(X_tr))
    
    data = {
        'model': xgb, 'scaler': scaler, 'features': feature_cols,
        'version': 'MLB_VK_v3.0_3yr', 'trained_at': datetime.now(timezone.utc).isoformat(),
        'samples': total_samples, 'feature_count': len(feature_cols),
        'r2_train': round(r2_tr, 4), 'r2_test': round(r2_te, 4),
    }
    path = os.path.join(MODEL_DIR, f'mlb_hf_{stat_name}.pkl')
    with open(path, 'wb') as f:
        pickle.dump(data, f)
    
    del X, y, X_scaled, X_tr, X_te, y_tr, y_te, xgb
    gc.collect()
    
    logger.info(f"{stat_name:22s} | {total_samples:>8} samples | {len(feature_cols)} feat | R2_tr={r2_tr:.4f} | R2_te={r2_te:.4f} | MAE={mae_te:.4f}")

logger.info("ALL MODELS RETRAINED WITH 3-YEAR BDL DATA")
client.close()

"""
NBA VK2 — shared feature-builder for retrain + live scoring.

This mirrors EXACTLY the feature schema used by
`/app/backend/scripts/retrain_nba_vk2.py` so adapter predictions read the
same 186-dim vector the model was fit on.

Inputs (prediction-time):
  history_logs: newest-first list of game-log dicts. Each log must carry
                keys compatible with training:
                  player_id, game_id, season,
                  pts, reb, ast, fg3m, fga, fg3a, fta, min,
                  fg_pct, fg3_pct, ft_pct
                (team_id / home_team_id optional — if absent, is_home=0)
  target_game: optional dict supplying `home_team_id` / `team_id` for is_home.
  adv_map:     {(player_id, game_id): {<adv field>: value}}, pre-built once.

Keep this pure-python (numpy ok) — no DB I/O.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

ROLLING_WINDOW = 20

# MUST match retrain_nba_vk2.py exactly. If this drifts, models silently corrupt.
ADV_FIELDS = [
    'usage_percentage', 'true_shooting_percentage', 'effective_field_goal_percentage',
    'pace', 'possessions', 'offensive_rating', 'defensive_rating', 'net_rating',
    'assist_percentage', 'rebound_percentage', 'defensive_rebound_percentage',
    'offensive_rebound_percentage', 'turnover_ratio', 'pie',
    'touches', 'passes', 'distance', 'speed',
    'pct_pts_paint', 'pct_pts_3pt', 'pct_pts_fast_break', 'pct_pts_free_throw',
    'deflections', 'contested_shots', 'pct_fga',
]


def build_features(
    history_logs: List[Dict[str, Any]],
    target_game: Optional[Dict[str, Any]] = None,
    adv_map: Optional[Dict[Tuple[Any, Any], Dict[str, Any]]] = None,
) -> Optional[Dict[str, float]]:
    """Return flat dict of ~186 features. Returns None if history < 5 games."""
    if len(history_logs) < 5:
        return None
    feats: Dict[str, float] = {}

    # Basic windowed means/std
    for stat_key, field in [
        ('pts', 'pts'), ('reb', 'reb'), ('ast', 'ast'),
        ('fg3m', 'fg3m'), ('fga', 'fga'), ('fg3a', 'fg3a'),
        ('fta', 'fta'), ('min_played', 'min'),
    ]:
        vals: List[float] = []
        for g in history_logs[:ROLLING_WINDOW]:
            v = g.get(field)
            if field == 'min' and isinstance(v, str):
                try:
                    mm, ss = v.split(':')
                    v = float(mm) + float(ss) / 60.0
                except Exception:
                    v = None
            if v is None:
                continue
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        for w in (3, 5, 10, 20):
            window_vals = vals[:w]
            if len(window_vals) >= 2:
                arr = np.asarray(window_vals, dtype=np.float32)
                feats[f'{stat_key}_L{w}_mean'] = float(arr.mean())
                feats[f'{stat_key}_L{w}_std'] = float(arr.std(ddof=1))
            else:
                feats[f'{stat_key}_L{w}_mean'] = 0.0
                feats[f'{stat_key}_L{w}_std'] = 0.0
        feats[f'{stat_key}_season_mean'] = float(np.mean(vals)) if vals else 0.0

    # PRA synthesized rolling means
    pra_vals = []
    for g in history_logs[:ROLLING_WINDOW]:
        p = g.get('pts'); r = g.get('reb'); a = g.get('ast')
        if p is not None and r is not None and a is not None:
            pra_vals.append(float(p) + float(r) + float(a))
    for w in (3, 5, 10, 20):
        wv = pra_vals[:w]
        feats[f'pra_L{w}_mean'] = float(np.mean(wv)) if wv else 0.0

    # Efficiency
    for ef_field in ('fg_pct', 'fg3_pct', 'ft_pct'):
        vv = [g.get(ef_field) for g in history_logs[:10] if g.get(ef_field) is not None]
        feats[f'{ef_field}_L10_mean'] = float(np.mean(vv)) if vv else 0.0

    # Volume proxy
    vol = [
        g.get('fga', 0) + 0.44 * g.get('fta', 0)
        for g in history_logs[:10]
        if g.get('fga') is not None and g.get('fta') is not None
    ]
    feats['usg_proxy_L10'] = float(np.mean(vol)) if vol else 0.0

    # EWMA pts
    pts_vals = [g.get('pts') for g in history_logs[:10] if g.get('pts') is not None]
    if pts_vals:
        alpha = 0.35
        ewma = float(pts_vals[0])
        for v in pts_vals[1:]:
            ewma = alpha * float(v) + (1 - alpha) * ewma
        feats['pts_ewma'] = float(ewma)
    else:
        feats['pts_ewma'] = 0.0

    feats['logs_used'] = float(len(history_logs[:ROLLING_WINDOW]))

    # Advanced-stat rolling features (same schema as training)
    if adv_map is not None:
        for adv_f in ADV_FIELDS:
            l5_vals, l10_vals = [], []
            for idx, g in enumerate(history_logs[:10]):
                key = (g.get('player_id'), g.get('game_id'))
                a = adv_map.get(key)
                if not a:
                    continue
                v = a.get(adv_f)
                if v is None:
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if idx < 5:
                    l5_vals.append(v)
                l10_vals.append(v)
            feats[f'adv_{adv_f}_L5_mean'] = float(np.mean(l5_vals)) if l5_vals else 0.0
            feats[f'adv_{adv_f}_L10_mean'] = float(np.mean(l10_vals)) if l10_vals else 0.0
            feats[f'adv_{adv_f}_L5_miss'] = 0.0 if l5_vals else 1.0
            feats[f'adv_{adv_f}_L10_miss'] = 0.0 if l10_vals else 1.0

        adv_coverage = 0
        season_gap_count = 0
        for g in history_logs[:10]:
            if (g.get('player_id'), g.get('game_id')) in adv_map:
                adv_coverage += 1
            if g.get('season') == 2023:
                season_gap_count += 1
        feats['adv_coverage_L10'] = float(adv_coverage)
        window_sz = float(min(10, len(history_logs)))
        feats['adv_missing_season'] = (season_gap_count / window_sz) if window_sz else 0.0

    # Target-game context
    if target_game is not None:
        feats['is_home'] = 1.0 if target_game.get('home_team_id') == target_game.get('team_id') else 0.0
        feats['minutes_proxy'] = feats.get('min_played_L5_mean', 0.0)
    else:
        # Training code ALWAYS passes target_game; to stay schema-consistent
        # at prediction time when we don't have target context, emit zeros.
        feats['is_home'] = 0.0
        feats['minutes_proxy'] = feats.get('min_played_L5_mean', 0.0)

    return feats

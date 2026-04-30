"""
MLB Vacuum Routes - MLB Usage Vacuum API Endpoints
==================================================
API endpoints for the MLB InjuryVacuumService.

Endpoints:
- GET /api/v3/mlb/vacuum/updates - Get current MLB vacuum state
- POST /api/v3/mlb/vacuum/check - Trigger manual MLB injury check
- GET /api/v3/mlb/vacuum/active - Get all active MLB usage vacuums
- GET /api/v3/mlb/vacuum/live-alerts - Get formatted alerts for UI
- POST /api/v3/mlb/vacuum/clear/{injured_player} - Clear a vacuum
"""
from fastapi import APIRouter, HTTPException, Response, Path
from typing import Dict, Any
from datetime import datetime, timezone
import logging

from services.mlb_injury_vacuum_service import get_mlb_vacuum_service
from services.observability import log_silent_failure

logger = logging.getLogger(__name__)
router = APIRouter(tags=["MLB Usage Vacuum"])

# Database reference
_db = None


def set_mlb_vacuum_db(db):
    """Set the database reference for MLB Vacuum service."""
    global _db
    _db = db


def get_service():
    """Get the MLB Vacuum service instance."""
    if _db is None:
        raise HTTPException(status_code=500, detail="MLB Vacuum service not initialized")
    return get_mlb_vacuum_service(_db)


@router.get("/v3/mlb/vacuum/updates")
async def get_mlb_vacuum_updates(response: Response):
    """
    Get current MLB vacuum state.
    
    Returns:
        JSON payload with vacuum state for MLB injury advantage.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.get_vacuum_updates()


@router.post("/v3/mlb/vacuum/check")
async def check_mlb_injuries():
    """
    Manually trigger an MLB injury check.
    
    This fetches the latest MLB injury report and:
    1. Identifies star players who are OUT/IL/DTD
    2. Calculates beneficiaries (lineup movers)
    3. Creates vacuum alerts for the UI
    
    Returns:
        Dict with triggered vacuums and status changes.
    """
    service = get_service()
    result = await service.check_injuries()
    return result


@router.get("/v3/mlb/vacuum/active")
async def get_active_mlb_vacuums(response: Response):
    """
    Get all currently active MLB usage vacuums.
    
    Returns:
        List of active vacuums with injured players and beneficiaries.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    vacuums = await service.get_active_vacuums_for_today()
    
    return {
        "count": len(vacuums),
        "vacuums": vacuums,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/v3/mlb/vacuum/live-alerts")
async def get_mlb_live_alerts(
    response: Response,
    refresh: bool = False
):
    """
    Get formatted alerts for the MLB "Live Injury Advantage" UI section.

    UNIVERSAL PIPELINE (2026-04-29):
    Re-routed to use the same `compute_injury_advantages` engine NBA
    uses, so MLB injuries flow through the canonical
    `injuries_normalized` → board-pick join → advantage scoring path.
    The legacy `MLBInjuryVacuumService` (BDL/ESPN refetch + hardcoded
    `MLB_STAR_PROFILES` and `MLB_BENEFICIARY_MAPPINGS`) is bypassed
    entirely for this endpoint — the dashboard component receives the
    same alert shape it expected, but the source-of-truth is now the
    universal engine.

    Args:
        refresh: Accepted for API back-compat; ignored (the universal
            engine reads `injuries_normalized` which is kept fresh by
            `services.injury_sensor` / `services.live_injury_micro_sync`).

    Returns:
        Same legacy alert shape the dashboard already renders:
            injured_player, injured_team, injury_reason, injured_ops,
            time_ago, is_late_scratch, beneficiary_name, beneficiary_team,
            minutes_bump, usage_bump, stat_type, line, board_tier
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    if _db is None:
        return {
            "success": False, "alerts": [], "count": 0,
            "last_check": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    try:
        from services.injury_advantage import (
            compute_injury_advantages,
            _get_recency_window,
            RECENCY_PREGAME_HOURS,
        )
        from services.mlb_lineup_delta import (
            build_lineup_delta_index,
            extract_deltas_for_player,
        )

        advantages = await compute_injury_advantages(_db, "mlb")
        window_hours = await _get_recency_window(_db, "mlb")

        # 2026-04-29 — Lineup-delta lookup (one query per response).
        # When the projected/canonical lineup collections are empty
        # this returns {} so EVERY downstream filter row becomes
        # "no real delta" and is dropped — exactly the contract.
        lineup_index = await build_lineup_delta_index(_db)

        # ── Reshape universal advantage rows → legacy MLB alert shape ─
        now = datetime.now(timezone.utc)
        alerts = []
        for adv in advantages:
            # Best-effort `time_ago`: derive from the raw injury doc.
            injured_team = None
            injury_reason = adv.get("injury_description") or ""
            time_ago = None
            is_late_scratch = window_hours <= RECENCY_PREGAME_HOURS

            inj = await _db["injuries_normalized"].find_one(
                {"sport": "mlb", "player_name": adv.get("injured_player")},
                {"_id": 0, "team": 1, "status_changed_at": 1,
                 "display_only": 1},
            )
            if inj:
                injured_team = inj.get("team")
                changed = inj.get("status_changed_at")
                if isinstance(changed, str):
                    try:
                        ts = datetime.fromisoformat(changed.replace("Z", "+00:00"))
                        if not ts.tzinfo:
                            ts = ts.replace(tzinfo=timezone.utc)
                        delta = (now - ts).total_seconds()
                        if delta < 3600:
                            time_ago = f"{int(delta // 60)}m ago"
                        elif delta < 86400:
                            time_ago = f"{int(delta // 3600)}h ago"
                        else:
                            time_ago = f"{int(delta // 86400)}d ago"
                    except (ValueError, TypeError) as _swept_exc:
                        log_silent_failure("routes.mlb_vacuum.get_mlb_live_alerts", _swept_exc)  # sweep-auto-converted
                if not injury_reason:
                    disp = inj.get("display_only") or {}
                    injury_reason = (disp.get("description")
                                     or disp.get("short_comment") or "")[:120]

            # ── Compute REAL MLB lineup deltas for the beneficiary ──
            #   lineup_delta        = previous_slot - current_slot
            #                         (positive = moved up the order)
            #   projected_ab_delta  = current_expected_PA - previous_expected_PA
            # If the lineup data is missing, both come back as None → row
            # is dropped below by the strict filter contract.
            deltas = extract_deltas_for_player(
                lineup_index,
                adv.get("beneficiary_name"),
                adv.get("beneficiary_team"),
            )
            lineup_delta = deltas.get("lineup_delta")
            projected_ab_delta = deltas.get("projected_ab_delta")
            previous_slot = deltas.get("previous_lineup_slot")
            current_slot = deltas.get("current_lineup_slot")
            current_expected_pa = deltas.get("current_expected_pa")

            alerts.append({
                "id": f"{adv['injured_player']}-{adv['beneficiary_name']}".replace(" ", "-").lower(),
                # Injured player block (legacy field names the UI groups by)
                "injured_player": adv.get("injured_player"),
                "injured_team": injured_team or adv.get("beneficiary_team"),
                "injured_status": adv.get("injured_status"),
                "injured_tier_level": adv.get("injured_tier_level"),
                "injury_return_date": adv.get("injury_return_date"),
                "injury_reason": injury_reason,
                "injured_ops": None,            # not used by current UI
                "time_ago": time_ago,
                "is_late_scratch": is_late_scratch,
                # Beneficiary block
                "beneficiary_name": adv.get("beneficiary_name"),
                "beneficiary_team": adv.get("beneficiary_team"),
                "beneficiary_rank": adv.get("rank"),
                "usage_rank": adv.get("usage_rank"),
                "usage_source": adv.get("usage_source"),
                "stat_type": adv.get("stat_type"),
                "line": adv.get("line"),
                "board_tier": adv.get("board_tier"),
                # Canonical MLB delta fields (the UI reads these now)
                "lineup_delta":        lineup_delta,
                "projected_ab_delta":  projected_ab_delta,
                "previous_lineup_slot": previous_slot,
                "current_lineup_slot":  current_slot,
                # Absolute today's projected PA/AB — used by the UI
                # "X AB projected" column because a PA-based delta
                # overstates new-starter signal (you can't gain
                # +4 AB vs. an unknown baseline).
                "current_expected_pa": current_expected_pa,
                # Option B (2026-04-30): surface new-starter status so
                # the UI can render "new starter" copy instead of a
                # +N-slots shift.
                "is_new_starter":      bool(deltas.get("is_new_starter")),
                # Internal NBA-shape fields kept for cross-sport readers
                # (universal Vacuum tools / debugging dashboards). They
                # are NOT consumed by the MLB Live Injury Advantage UI.
                "minutes_bump": adv.get("minutes_bump"),
                "usage_bump":   adv.get("usage_bump"),
                "has_active_prop": True,
                "display_text": (
                    f"{adv['beneficiary_name']} ({adv['stat_type']} {adv['line']}) — "
                    f"{adv['injured_player']} {adv['injured_status']}."
                ),
            })

        # ── MLB Lineup Opportunity contract (2026-04-30, updated A+B) ──
        # A row qualifies when EITHER:
        #   (a) A real slot shift exists (lineup_delta >= 1, with both
        #       previous_lineup_slot and current_lineup_slot numeric), OR
        #   (b) NEW STARTER — player entered the lineup today (previous_slot
        #       is None, current_slot numeric, projected_ab_delta ≥ 0.5).
        # We do NOT accept placeholder "+0 lineup spots" rows in either path.
        # Cap to top-5 (sorted projected_ab_delta desc, then lineup_delta).
        def _is_numeric(x: Any) -> bool:
            if x is None or isinstance(x, bool):
                return False
            try:
                float(x)
                return True
            except (TypeError, ValueError):
                return False

        def _row_qualifies(row: Dict[str, Any]) -> bool:
            if not row.get("beneficiary_name"):
                return False
            cur_slot = row.get("current_lineup_slot")
            if not _is_numeric(cur_slot):
                return False  # No current slot → not a lineup opportunity
            ad = row.get("projected_ab_delta")
            ld = row.get("lineup_delta")

            # Path (b): new-starter — prev slot is None, current is numeric,
            # and projected AB delta is a real signal.
            if row.get("is_new_starter") is True:
                return _is_numeric(ad) and float(ad) >= 0.5

            # Path (a): day-over-day slot shift.
            prev_slot = row.get("previous_lineup_slot")
            if not _is_numeric(prev_slot):
                return False  # Neither new-starter NOR valid shift
            if not _is_numeric(ld) or float(ld) < 1.0:
                return False
            # lineup_delta explicitly non-zero (placeholder guard).
            if ld in (0, None):
                return False
            return _is_numeric(ad)

        filtered = [a for a in alerts if _row_qualifies(a)]
        filtered.sort(
            key=lambda a: (
                -float(a.get("projected_ab_delta") or 0.0),
                -float(a.get("lineup_delta") or 0.0),
            )
        )
        capped = filtered[:5]

        dropped = len(alerts) - len(filtered)
        if dropped:
            logger.info(
                "[MLB_INJURY_ADV] dropped %d/%d rows lacking real lineup deltas; "
                "kept %d (cap=5)",
                dropped, len(alerts), len(capped),
            )

        # ── Runtime Contract Enforcer (2026-04-29, STRICT MODE) ────────
        # Belt-and-braces validation on the FINAL response shape. Any row
        # whose lineup_delta / projected_ab_delta degrades to a placeholder
        # value before serialization is suppressed and counted in
        # /api/health/contracts. NO model / scoring / gate touched.
        try:
            from services.contract_enforcer import (
                enforce_lineup_opportunity_contract,
            )
            capped = await enforce_lineup_opportunity_contract(_db, capped, sport="mlb")
        except Exception as _ce_err:
            logger.error(
                f"[CONTRACT_ENFORCER:mlb:lineup_opportunity] failed: {_ce_err}",
                exc_info=True,
            )

        return {
            "success": True,
            "alerts": capped,
            "count": len(capped),
            "raw_advantage_count": len(alerts),
            "filtered_dropped": dropped,
            "last_check": now.isoformat(),
            "recency_window_hours": window_hours,
            "engine": "universal_injury_advantage",  # provenance flag
            "timestamp": now.isoformat(),
        }

    except Exception as e:
        logger.error(f"[MLB_INJURY_ADV] Error: {e}", exc_info=True)
        return {
            "success": False, "alerts": [], "count": 0,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.post("/v3/mlb/vacuum/clear/{injured_player}")
async def clear_mlb_vacuum(
    injured_player: str = Path(..., description="Name of the injured player to clear")
):
    """
    Clear a vacuum when a player returns from injury.
    
    Args:
        injured_player: Name of the injured player
    
    Returns:
        Success status and message.
    """
    service = get_service()
    result = await service.clear_vacuum(injured_player)
    
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    
    return result

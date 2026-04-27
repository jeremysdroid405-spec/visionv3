"""
Master Hub CRON Scheduler
=========================
SINGLE SOURCE OF TRUTH (SSOT) Architecture - PIPE 1: Stats Vault

Schedules daily sync of NBA Master Hub at 0400 EST using OFFICIAL NBA API.

ENGINE SWAP (2026-03-16):
- DEPRECATED: BDL Fantasy Stats API (data quality issues)
- NEW: Official NBA API via nba_api package

Data Flow:
  NBA Official API → 0400 CRON → nba_master_hub_2026 → All App Components

CRON JOBS:
1. 0400 EST - Master Hub Sync (NBA Official API + BDL)
2. 1830 EST - Forward-Testing Capture (Daily prop snapshots)

CRITICAL RULES:
1. This CRON is the ONLY code allowed to call external stat APIs
2. Sync ONLY overwrites: baseline_stats, game_logs, stats metadata
3. Sync NEVER touches: player_id, player_name, display_name, photo_url, headshot_url
4. All other components read from nba_master_hub_2026 ONLY

Data Source: Official NBA Stats API (nba_api package)
"""

import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[AsyncIOScheduler] = None


async def run_daily_sync():
    """
    Daily sync job - runs at 0400 EST (0900 UTC).
    
    SSOT PIPE 1: The ONLY authorized external stats API caller.
    
    ENGINES:
    1. Official NBA API - baseline stats with L5/L10 hit rates
    2. BallDontLie API - shooting/defensive stats (fg_pct, fg3_pct, stl, blk)
    
    Updates ONLY statistical fields:
    - baseline_stats: {PTS: {l5_avg, l10_avg, season_avg}, ...}
    - game_logs: [{gameID, pts, reb, ast, ...}, ...]
    - BDL stats: fg_pct, fg3_pct, ft_pct, stl, blk, min
    
    NEVER modifies structural fields:
    - player_id, player_name, display_name
    - photo_url, headshot_url
    - team, position
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.nba_official_sync import get_nba_official_sync_service
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    
    logger.info("[CRON] ========================================")
    logger.info("[CRON] SSOT PIPE 1: Starting 0400 EST Multi-Engine Sync")
    logger.info("[CRON] ENGINE 1: nba_api (Hit Rates)")
    logger.info("[CRON] ENGINE 2: BallDontLie (Shooting/Defense)")
    logger.info("[CRON] ========================================")
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        logger.error("[CRON] MONGO_URL not configured")
        return
    
    try:
        # MongoDB Atlas-compatible connection settings
        is_atlas = 'mongodb.net' in mongo_url or 'mongodb+srv' in mongo_url
        
        connection_opts = {
            'serverSelectionTimeoutMS': 30000,
            'connectTimeoutMS': 30000,
            'socketTimeoutMS': 60000,
            'maxPoolSize': 10,
            'retryWrites': True,
        }
        if is_atlas:
            connection_opts['tls'] = True
        
        client = AsyncIOMotorClient(mongo_url, **connection_opts)
        db = client[db_name]
        
        # PHASE 1: Run Official NBA API stats sync (hit rates, game logs)
        logger.info("[CRON] Phase 1: NBA Official API sync...")
        service = get_nba_official_sync_service(db)
        nba_result = await service.sync_all_players()
        
        logger.info(f"[CRON] NBA sync: {nba_result.get('players_updated', 0)} players updated")
        
        # PHASE 2: Run BDL sync for PrizePicks players (shooting/defensive stats)
        logger.info("[CRON] Phase 2: BDL API sync (PrizePicks players)...")
        bdl_service = get_bdl_sync_service(db)
        bdl_result = await bdl_service.sync_prizepicks_players()
        
        logger.info(f"[CRON] BDL sync: {bdl_result.get('success', 0)} players updated")
        logger.info("[CRON] ========================================")
        logger.info(f"[CRON] TOTAL: NBA={nba_result.get('players_updated', 0)}, BDL={bdl_result.get('success', 0)}")
        logger.info("[CRON] ========================================")
        
    except Exception as e:
        logger.error(f"[CRON] SSOT sync failed: {e}")


async def run_forward_test_capture():
    """
    Forward-Testing daily capture job - runs at 1830 EST (2330 UTC).
    
    Captures all tier props (Safe Haven, Front Lines, War Zone) for both
    NBA and MLB to enable historical performance tracking.
    
    This builds the dataset for:
    - Model calibration validation
    - Tier performance analysis
    - A/B testing of threshold changes
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.forward_testing_service import get_forward_testing_service
    
    logger.info("[CRON] ========================================")
    logger.info("[CRON] FORWARD-TEST: Starting 1830 EST Daily Capture")
    logger.info("[CRON] ========================================")
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        logger.error("[CRON] MONGO_URL not configured")
        return
    
    try:
        # MongoDB connection
        is_atlas = 'mongodb.net' in mongo_url or 'mongodb+srv' in mongo_url
        
        connection_opts = {
            'serverSelectionTimeoutMS': 30000,
            'connectTimeoutMS': 30000,
            'socketTimeoutMS': 60000,
            'maxPoolSize': 10,
            'retryWrites': True,
        }
        if is_atlas:
            connection_opts['tls'] = True
        
        client = AsyncIOMotorClient(mongo_url, **connection_opts)
        db = client[db_name]
        
        # Run forward-test capture for all sports
        service = get_forward_testing_service(db)
        result = await service.capture_all_sports(capture_reason="scheduled_1830_et")
        
        # Log results
        total_props = 0
        for sport, data in result.get("sports", {}).items():
            sport_total = data.get("total_props", 0)
            total_props += sport_total
            tiers = data.get("tiers", {})
            logger.info(f"[CRON] {sport.upper()}: {sport_total} props captured")
            for tier, count in tiers.items():
                logger.info(f"[CRON]   - {tier}: {count}")
        
        logger.info("[CRON] ========================================")
        logger.info(f"[CRON] FORWARD-TEST COMPLETE: {total_props} total props captured")
        logger.info("[CRON] ========================================")

        # Shadow capture — parallel, read-only feature store for the
        # forward-test shadow pipeline. Strictly additive; if it fails
        # we never break the production capture path.
        try:
            from services.shadow.shadow_capture_service import capture_all_shadow
            shadow_res = await capture_all_shadow(
                db, capture_reason="scheduled_1830_et"
            )
            for sp, info in shadow_res.get("sports", {}).items():
                logger.info(
                    f"[SHADOW_CAPTURE_CRON] sport={sp} "
                    f"date={info.get('capture_date')} "
                    f"fts={info.get('fts_rows')} "
                    f"written={info.get('shadow_written')} "
                    f"ctx_hits={info.get('ctx_hits')} "
                    f"coverage={info.get('ctx_coverage')}"
                )
        except Exception as shadow_exc:
            logger.error(
                f"[SHADOW_CAPTURE_CRON] failed (non-fatal): {shadow_exc!r}",
                exc_info=True,
            )
        
    except Exception as e:
        logger.error(f"[CRON] Forward-test capture failed: {e}")
        import traceback
        logger.error(traceback.format_exc())



async def run_forward_test_resolve():
    """
    Forward-Testing daily resolution job — runs after the master-hub
    sync (which refreshes last-night's game logs) and before the next
    day's capture.

    Walks `forward_test_snapshots` for any `outcome IS None` row whose
    `capture_date` is older than today and resolves it via the
    `forward_testing_service.resolve_outcomes` path. Idempotent —
    already-resolved rows are skipped by the inner query.

    Backfill window: last 14 capture dates that still have any
    unresolved snapshot (per sport).

    Log line format (per prompt):
      [FT_RESOLVE_CRON] sport=nba date=YYYY-MM-DD resolved=N hits=H misses=M pushes=P unable=U
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.forward_testing_service import (
        get_forward_testing_service,
    )

    logger.info("[CRON] ========================================")
    logger.info("[CRON] FORWARD-TEST RESOLVE: Starting nightly resolver")
    logger.info("[CRON] ========================================")

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    if not mongo_url:
        logger.error("[CRON] MONGO_URL not configured")
        return

    try:
        client = AsyncIOMotorClient(
            mongo_url, serverSelectionTimeoutMS=30000,
            connectTimeoutMS=30000, socketTimeoutMS=60000, maxPoolSize=10,
        )
        db = client[db_name]
        service = get_forward_testing_service(db)

        for sport in ("nba", "mlb"):
            # Collect distinct unresolved capture_dates for this sport
            # (limit to most recent 14 so we don't blow up if a long
            # tail accumulates).
            pipe = [
                {"$match": {"sport": sport, "outcome": None}},
                {"$group": {"_id": "$capture_date",
                             "n": {"$sum": 1}}},
                {"$sort": {"_id": -1}},
                {"$limit": 14},
            ]
            unresolved_dates = [
                d["_id"] async for d in
                db["forward_test_snapshots"].aggregate(pipe)
                if d["_id"]
            ]
            for date in unresolved_dates:
                try:
                    res = await service.resolve_outcomes(sport, date)
                except Exception as e:
                    logger.error(
                        f"[FT_RESOLVE_CRON] sport={sport} date={date} "
                        f"failed: {e}"
                    )
                    continue
                logger.info(
                    f"[FT_RESOLVE_CRON] sport={sport} date={date} "
                    f"resolved={res.get('total_resolved', 0)} "
                    f"hits={res.get('hits', 0)} "
                    f"misses={res.get('misses', 0)} "
                    f"pushes={res.get('pushes', 0)} "
                    f"unable={res.get('unable_to_resolve', 0)}"
                )
        logger.info("[CRON] ========================================")
        logger.info("[CRON] FORWARD-TEST RESOLVE COMPLETE")
        logger.info("[CRON] ========================================")

        # Shadow resolve — copies outcome/actual_value from FTS rows
        # onto their sibling shadow_vk_snapshots rows. Idempotent.
        try:
            from services.shadow.shadow_capture_service import resolve_shadow_outcomes
            shadow_res = await resolve_shadow_outcomes(db)
            logger.info(
                f"[SHADOW_RESOLVE_CRON] dates_checked="
                f"{shadow_res.get('dates_checked')} "
                f"resolved={shadow_res.get('resolved')} "
                f"by_sport={shadow_res.get('by_sport')}"
            )
        except Exception as shadow_exc:
            logger.error(
                f"[SHADOW_RESOLVE_CRON] failed (non-fatal): {shadow_exc!r}",
                exc_info=True,
            )
    except Exception as e:
        logger.error(f"[CRON] Forward-test resolve failed: {e}")
        import traceback
        logger.error(traceback.format_exc())


async def run_pra_audit_settle():
    """
    PRA dual-projection audit settle job.

    Walks `nba_pra_projection_audit` and resolves actual
    pts / reb / ast for any row whose game has concluded by joining
    with `nba_master_hub_2026.bdl_game_logs`. Idempotent — already-
    settled rows are skipped. Safe to run repeatedly.

    Scheduled to run 30 minutes after the 0400 EST master-hub sync
    (which refreshes last-night's game logs), so by 0430 EST any
    previous-slate PRA audit row should be resolvable.

    Log line format (per prompt):
      [PRA_AUDIT_CRON] settled=N skipped=M total=T pending=P
    """
    try:
        import os
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo_url = os.environ["MONGO_URL"]
        db_name = os.environ["DB_NAME"]
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]

        audit = db["nba_pra_projection_audit"]
        hub = db["nba_master_hub_2026"]

        # Preload game logs by display_name (lowercased) — cheaper
        # than N per-row DB lookups for a few hundred audit rows.
        logs_by_name: dict = {}
        async for h in hub.find(
            {"bdl_game_logs_count": {"$gt": 0}},
            {"_id": 0, "display_name": 1, "bdl_game_logs": 1},
        ):
            nm = (h.get("display_name") or "").lower()
            logs_by_name[nm] = h.get("bdl_game_logs") or []

        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        settled = 0
        skipped = 0
        async for row in audit.find({"settled": {"$ne": True}}, {"_id": 0}):
            game_start = row.get("game_start_utc")
            if not game_start:
                skipped += 1
                continue
            try:
                dt = game_start
                if isinstance(dt, str):
                    dt = _dt.fromisoformat(dt.replace("Z", "+00:00"))
                target_dates = {
                    dt.date().isoformat(),
                    (dt.date() - _td(days=1)).isoformat(),
                }
            except Exception:
                skipped += 1
                continue
            player_logs = logs_by_name.get(
                (row.get("player_name") or "").lower()
            ) or []
            hit = None
            for g in player_logs:
                if str(g.get("date") or "") in target_dates:
                    hit = g
                    break
            if hit is None:
                skipped += 1
                continue
            pts = hit.get("pts"); reb = hit.get("reb"); ast = hit.get("ast")
            if pts is None or reb is None or ast is None:
                skipped += 1
                continue
            pra_actual = float(pts) + float(reb) + float(ast)
            await audit.update_one(
                {
                    "event_id": row["event_id"],
                    "player_name": row["player_name"],
                    "line": row["line"],
                    "recommendation": row["recommendation"],
                },
                {"$set": {
                    "actual_pts": int(pts),
                    "actual_reb": int(reb),
                    "actual_ast": int(ast),
                    "actual_pra": round(pra_actual, 2),
                    "settled": True,
                    "settled_at": _dt.now(_tz.utc),
                }},
            )
            settled += 1

        total = await audit.count_documents({})
        pending = await audit.count_documents({"settled": {"$ne": True}})
        logger.info(
            f"[PRA_AUDIT_CRON] settled={settled} skipped={skipped} "
            f"total={total} pending={pending}"
        )
    except Exception as exc:
        logger.error(
            f"[PRA_AUDIT_CRON] settle job failed: {exc!r}",
            exc_info=True,
        )



def start_scheduler():
    """
    Start the CRON scheduler for daily Master Hub sync.
    
    Schedule: 0400 EST (0900 UTC) daily
    
    This scheduler manages PIPE 1 (Stats Vault) of the SSOT architecture.
    """
    global _scheduler
    
    if _scheduler is not None:
        logger.warning("[CRON] Scheduler already running")
        return _scheduler
    
    _scheduler = AsyncIOScheduler()
    
    # Schedule daily sync at 0400 EST (0900 UTC)
    # EST is UTC-5, so 4 AM EST = 9 AM UTC
    _scheduler.add_job(
        run_daily_sync,
        CronTrigger(hour=9, minute=0, timezone='UTC'),  # 0400 EST = 0900 UTC
        id='master_hub_daily_sync',
        name='SSOT PIPE 1: NBA Master Hub Daily Sync (Official NBA API)',
        replace_existing=True
    )
    
    # Schedule forward-test capture at 1830 EST (2330 UTC)
    # EST is UTC-5, so 6:30 PM EST = 11:30 PM UTC
    # Note: During EDT (summer), this runs at 10:30 PM UTC
    _scheduler.add_job(
        run_forward_test_capture,
        CronTrigger(hour=22, minute=30, timezone='UTC'),  # 1830 ET ≈ 2230 UTC (summer)
        id='forward_test_daily_capture',
        name='Forward-Testing: Daily Prop Capture (6:30 PM ET)',
        replace_existing=True
    )

    # 2026-05 — Forward-test resolver. Runs at 0500 EST (0930 UTC),
    # 30 min after the master-hub sync that refreshes last-night's
    # `bdl_game_logs`. Resolves outcomes for any unresolved snapshot
    # in the last 14 days (idempotent).
    _scheduler.add_job(
        run_forward_test_resolve,
        CronTrigger(hour=9, minute=30, timezone='UTC'),
        id='forward_test_daily_resolve',
        name='Forward-Testing: Daily Outcome Resolver (5:00 AM ET)',
        replace_existing=True,
    )
    
    _scheduler.start()
    logger.info("[CRON] SSOT Scheduler started:")
    logger.info("[CRON]   - Master Hub sync at 0400 EST daily")
    logger.info("[CRON]   - Forward-Test capture at 1830 ET daily")
    logger.info("[CRON]   - Forward-Test resolve at 0500 ET daily")
    
    return _scheduler


def stop_scheduler():
    """Stop the CRON scheduler."""
    global _scheduler
    
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("[CRON] Scheduler stopped")


def get_scheduler_status():
    """Get current scheduler status."""
    global _scheduler
    
    if _scheduler is None:
        return {"running": False, "jobs": [], "engine": "nba_official"}
    
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None
        })
    
    return {
        "running": _scheduler.running,
        "jobs": jobs,
        "engines": ["nba_official", "balldontlie"],
        "sync_includes": ["hit_rates", "fg_pct", "fg3_pct", "ft_pct", "stl", "blk", "min"],
        "forward_test_schedule": "1830 ET daily"
    }

"""
Unified Pipeline Framework
===========================
One architecture, two sports. Shared phase structure with sport-specific adapters.

Phases:
  1. LOAD     — Read from board collection, flatten, deduplicate
  2. ENRICH   — Stats, hit rates, CV, context (sport-specific adapter)
  3. SCORE    — True probability, edge, board scores (sport-specific adapter)
  4. VALIDATE — Attach validation metadata to every prop
  5. SELECT   — Tier classification + gate checks + top-N selection
  6. INTEL    — Gemini enrichment (async, non-blocking, never crashes pipeline)
  7. PUBLISH  — Atomic writes (temp collection + rename)

Contracts:
  - Every prop leaving Phase 4 MUST have a `validation` dict
  - Gemini failure marks has_gemini=false, does NOT block
  - MLR/Lasso availability is tracked explicitly in validation
  - Atomic writes ensure collections are never empty during swap
  - Empty tiers are returned honestly, never padded with low-quality picks
"""

import os
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

CACHE_DIR = os.environ.get("CACHE_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")


class SportAdapter(ABC):
    """
    Abstract base for sport-specific pipeline logic.
    
    Subclasses implement WHAT happens in each phase.
    The UnifiedPipeline enforces HOW phases are orchestrated.
    """

    @property
    @abstractmethod
    def sport(self) -> str:
        """'nba' or 'mlb'"""

    @property
    @abstractmethod
    def tier_collections(self) -> Dict[str, str]:
        """Map tier name → MongoDB collection name.
        Example: {'safe_haven': 'elite_safe_haven', 'front_lines': 'elite_front_lines', ...}
        """

    @abstractmethod
    async def load_board(self, db) -> List[Dict]:
        """Phase 1: Load and flatten all props from the cached board.
        Must deduplicate by player_name|stat_type|line.
        Returns flat list of prop dicts.
        """

    @abstractmethod
    async def enrich_and_score(self, props: List[Dict], db) -> List[Dict]:
        """Phase 2+3: Enrich props with stats, then score.
        Each returned prop MUST include a 'validation' dict:
        {
            'has_market_data': bool,
            'has_hit_rates': bool,
            'has_context': bool,
            'has_mlr': bool,
            'has_gemini': bool,
            'is_fully_validated': bool,
        }
        Props that fail safety gates should be excluded from the returned list.
        """

    @abstractmethod
    def select_tiers(self, scored_props: List[Dict], previous_tiers: Optional[Dict[str, List[Dict]]] = None) -> Dict[str, List[Dict]]:
        """Phase 5: Classify props into tiers with retention logic.

        Qualified Capped Set rules:
          1. Props from previous_tiers that still pass qualification gates → RETAINED
          2. New qualified props fill remaining capacity, sorted by score
          3. Displacement only when retained + new > capacity
          4. Empty tiers returned as empty lists — never padded

        Args:
            scored_props: All validated+scored props from Phase 4
            previous_tiers: Optional previous board state for retention.
                            If None, pure top-N selection (first run).

        Returns {'safe_haven': [...], 'front_lines': [...], 'war_zone': [...]}.
        """

    async def enrich_intel(self, tiers: Dict[str, List[Dict]], db) -> Dict[str, List[Dict]]:
        """Phase 6: Optional Gemini enrichment. Default: no-op.
        Override in adapter to add async Gemini calls.
        MUST be non-blocking. On failure, mark has_gemini=false and continue.
        """
        return tiers


class PipelineResult:
    """Structured result from a pipeline run."""

    def __init__(self, sport: str, run_id: str):
        self.sport = sport
        self.run_id = run_id
        self.started_at = datetime.now(timezone.utc)
        self.completed_at: Optional[datetime] = None
        self.success = False
        self.phases: Dict[str, Dict] = {}
        self.tiers: Dict[str, int] = {}
        self.validation_stats: Dict[str, Any] = {}
        self.errors: List[str] = []

    def to_dict(self) -> Dict:
        return {
            "sport": self.sport,
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": (self.completed_at - self.started_at).total_seconds() if self.completed_at else None,
            "success": self.success,
            "phases": self.phases,
            "tiers": self.tiers,
            "validation_stats": self.validation_stats,
            "errors": self.errors,
        }


class UnifiedPipeline:
    """
    Shared pipeline framework for NBA and MLB.
    
    Usage:
        pipeline = UnifiedPipeline(NBAAdapter(), db)
        result = await pipeline.run()
    """

    def __init__(self, adapter: SportAdapter, db):
        self.adapter = adapter
        self.db = db
        self.run_id = uuid.uuid4().hex[:8]

    # Tier collection names per sport
    _TIER_COLS = {
        "nba": {"safe_haven": "elite_safe_haven", "front_lines": "elite_front_lines", "war_zone": "elite_war_zone"},
        "mlb": {"safe_haven": "mlb_safe_haven", "front_lines": "mlb_front_lines", "war_zone": "mlb_war_zone"},
    }

    async def _load_previous_tiers(self) -> Dict[str, List[Dict]]:
        """Load the current published board as the previous tier state.
        Used for retention logic in select_tiers."""
        cols = self._TIER_COLS.get(self.adapter.sport, {})
        prev = {}
        for tier_name, col_name in cols.items():
            cursor = self.db[col_name].find(
                {},
                {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1, "market": 1},
            )
            prev[tier_name] = await cursor.to_list(length=20)
        return prev

    async def run(self) -> PipelineResult:
        """Execute the full pipeline: Load → Enrich → Score → Validate → Select → Intel → Publish."""
        result = PipelineResult(self.adapter.sport, self.run_id)
        sport = self.adapter.sport.upper()

        logger.info("=" * 70)
        logger.info(f"[{sport}_PIPELINE] [{self.run_id}] Starting Unified Pipeline")
        logger.info("=" * 70)

        try:
            # ============================================================
            # PHASE 1: LOAD
            # ============================================================
            phase_start = datetime.now(timezone.utc)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 1: Loading board...")

            props = await self.adapter.load_board(self.db)

            phase_dur = (datetime.now(timezone.utc) - phase_start).total_seconds()
            result.phases["1_load"] = {"duration_s": round(phase_dur, 2), "props_loaded": len(props)}
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 1 complete: {len(props)} props loaded ({phase_dur:.1f}s)")

            if not props:
                result.errors.append("Phase 1 produced 0 props — board is empty")
                logger.warning(f"[{sport}_PIPELINE] [{self.run_id}] ABORT: No props in board")
                result.completed_at = datetime.now(timezone.utc)
                return result

            # ============================================================
            # PHASE 2+3: ENRICH & SCORE
            # ============================================================
            phase_start = datetime.now(timezone.utc)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 2-3: Enriching and scoring {len(props)} props...")

            scored = await self.adapter.enrich_and_score(props, self.db)

            phase_dur = (datetime.now(timezone.utc) - phase_start).total_seconds()
            result.phases["2_3_enrich_score"] = {
                "duration_s": round(phase_dur, 2),
                "input": len(props),
                "scored": len(scored),
                "kill_rate": f"{(1 - len(scored) / max(len(props), 1)) * 100:.1f}%",
            }
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 2-3 complete: {len(scored)} scored ({phase_dur:.1f}s)")

            if not scored:
                result.errors.append("Phase 2-3 produced 0 scored props — all filtered out")
                logger.warning(f"[{sport}_PIPELINE] [{self.run_id}] ABORT: No props survived scoring")
                result.completed_at = datetime.now(timezone.utc)
                return result

            # ============================================================
            # PHASE 4: VALIDATE (enforce validation dict on every prop)
            # ============================================================
            phase_start = datetime.now(timezone.utc)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 4: Validating {len(scored)} props...")

            validated = self._enforce_validation(scored)

            # Compute validation stats
            v_stats = self._compute_validation_stats(validated)
            result.validation_stats = v_stats

            phase_dur = (datetime.now(timezone.utc) - phase_start).total_seconds()
            result.phases["4_validate"] = {
                "duration_s": round(phase_dur, 2),
                "total": len(validated),
                "fully_validated": v_stats["fully_validated"],
                "pct_market": v_stats["pct_market"],
                "pct_hit_rates": v_stats["pct_hit_rates"],
                "pct_mlr": v_stats["pct_mlr"],
                "pct_gemini": v_stats["pct_gemini"],
            }

            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 4 complete:")
            logger.info(f"  Fully validated: {v_stats['fully_validated']}/{len(validated)}")
            logger.info(f"  Market data: {v_stats['pct_market']}%")
            logger.info(f"  Hit rates:   {v_stats['pct_hit_rates']}%")
            logger.info(f"  MLR/Lasso:   {v_stats['pct_mlr']}%")
            logger.info(f"  Gemini:      {v_stats['pct_gemini']}%")

            # ============================================================
            # PHASE 5: SELECT TIERS (Qualified Capped Set with Retention)
            # Load previous board for retention: qualified picks that were on
            # the board stay unless the pool is full and a higher-ranked pick
            # displaces them.
            # ============================================================
            phase_start = datetime.now(timezone.utc)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 5: Selecting tiers...")

            # Load previous tiers from live collections
            previous_tiers = await self._load_previous_tiers()
            tiers = self.adapter.select_tiers(validated, previous_tiers=previous_tiers)

            phase_dur = (datetime.now(timezone.utc) - phase_start).total_seconds()
            tier_counts = {k: len(v) for k, v in tiers.items()}
            result.phases["5_select"] = {"duration_s": round(phase_dur, 2), "tiers": tier_counts}

            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 5 complete:")
            for tier_name, picks in tiers.items():
                logger.info(f"  {tier_name}: {len(picks)} picks")

            # ============================================================
            # PHASE 5b: ADAPTER ENRICHMENT (non-Gemini, pre-publish)
            # MLB: overlay cache, tempo, intel_suite, context badges
            # NBA: validation flag pass
            # ============================================================
            try:
                tiers = await self.adapter.enrich_intel(tiers, self.db)
            except Exception as e:
                logger.warning(f"[{sport}_PIPELINE] [{self.run_id}] Adapter enrich_intel failed (non-fatal): {e}")

            # ============================================================
            # PHASE 6: PUBLISH (atomic writes) — BEFORE Gemini
            # Tiers are published immediately so the board is never empty.
            # ============================================================
            phase_start = datetime.now(timezone.utc)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 6: Atomic publish...")

            publish_result = await self._atomic_publish(tiers)

            phase_dur = (datetime.now(timezone.utc) - phase_start).total_seconds()
            result.phases["6_publish"] = {"duration_s": round(phase_dur, 2), **publish_result}

            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 6 complete ({phase_dur:.1f}s)")
            for col_name, count in publish_result.items():
                logger.info(f"  {col_name}: {count}")

            # ============================================================
            # PHASE 6b: MARKET MOVES DIFF (board-diff tracking)
            # Compare old visible board vs new, classify exit reasons.
            # Pass validated candidate pool so the engine can distinguish
            # displaced_by_higher vs no_longer_qualified.
            # ============================================================
            try:
                from services.market_moves_engine import diff_and_update_from_tiers
                mm_events = await diff_and_update_from_tiers(
                    self.db, self.adapter.sport, tiers,
                    candidate_pool=validated,
                )
            except Exception as e:
                logger.warning(f"[{sport}_PIPELINE] [{self.run_id}] Market Moves diff failed (non-fatal): {e}")

            # ============================================================
            # PHASE 7: GEMINI INTEL ENRICHMENT (post-publish, non-blocking)
            # Board is already live. Gemini enriches in-place on the
            # published collections. If it fails, picks still serve
            # with fallback vision_intel from _generate_vision_fallback.
            # ============================================================
            phase_start = datetime.now(timezone.utc)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 7: Gemini batch enrichment (non-blocking)...")

            gemini_stats = {"attempted": 0, "success": 0, "failed": 0, "skipped": 0}
            try:
                gemini_stats = await self._run_gemini_enrichment(tiers)
                gemini_ok = gemini_stats.get("success", 0) > 0
            except Exception as e:
                logger.warning(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 7 Gemini failed (non-fatal): {e}")
                gemini_ok = False

            phase_dur = (datetime.now(timezone.utc) - phase_start).total_seconds()
            result.phases["7_gemini"] = {
                "duration_s": round(phase_dur, 2),
                "gemini_ok": gemini_ok,
                **gemini_stats,
            }
            logger.info(
                f"[{sport}_PIPELINE] [{self.run_id}] PHASE 7 complete: "
                f"gemini={'OK' if gemini_ok else 'FAILED/SKIPPED'} "
                f"({gemini_stats.get('success', 0)}/{gemini_stats.get('attempted', 0)} enriched, {phase_dur:.1f}s)"
            )

            # ============================================================
            # DONE
            # ============================================================
            result.success = True
            result.tiers = tier_counts
            result.completed_at = datetime.now(timezone.utc)
            total = (result.completed_at - result.started_at).total_seconds()

            logger.info("=" * 70)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PIPELINE COMPLETE ({total:.1f}s)")
            logger.info(f"  Safe Haven:  {tier_counts.get('safe_haven', 0)}")
            logger.info(f"  Front Lines: {tier_counts.get('front_lines', 0)}")
            logger.info(f"  War Zone:    {tier_counts.get('war_zone', 0)}")
            logger.info(f"  Validation:  {v_stats['fully_validated']}/{len(validated)} fully validated")
            logger.info("=" * 70)

            return result

        except Exception as e:
            logger.error(f"[{sport}_PIPELINE] [{self.run_id}] FATAL: {e}")
            import traceback
            traceback.print_exc()
            result.errors.append(str(e))
            result.completed_at = datetime.now(timezone.utc)
            return result

    # ------------------------------------------------------------------
    # INTERNAL: Validation enforcement
    # ------------------------------------------------------------------

    def _enforce_validation(self, props: List[Dict]) -> List[Dict]:
        """Ensure every prop has a complete validation dict. Fill missing fields."""
        for prop in props:
            v = prop.get("validation")
            if not v or not isinstance(v, dict):
                # Infer from available data
                prop["validation"] = {
                    "has_market_data": prop.get("dk_odds") is not None and prop.get("dk_odds") != 0,
                    "has_hit_rates": (prop.get("l10_rate") or prop.get("h10_rate") or 0) > 0,
                    "has_context": bool(prop.get("intel_suite")) or bool(prop.get("blowout_risk")),
                    "has_mlr": bool(prop.get("mlr_features_used") or prop.get("vk_predicted")),
                    "has_gemini": bool(prop.get("vision_intel") or prop.get("is_vision_enriched")),
                    "is_fully_validated": False,
                }
            v = prop["validation"]
            # Compute is_fully_validated
            v["is_fully_validated"] = all([
                v.get("has_market_data", False),
                v.get("has_hit_rates", False),
                v.get("has_mlr", False),
            ])
        return props

    def _compute_validation_stats(self, props: List[Dict]) -> Dict:
        """Aggregate validation stats for observability."""
        total = len(props) or 1
        counts = {"market": 0, "hit_rates": 0, "context": 0, "mlr": 0, "gemini": 0, "full": 0}
        for p in props:
            v = p.get("validation", {})
            if v.get("has_market_data"):
                counts["market"] += 1
            if v.get("has_hit_rates"):
                counts["hit_rates"] += 1
            if v.get("has_context"):
                counts["context"] += 1
            if v.get("has_mlr"):
                counts["mlr"] += 1
            if v.get("has_gemini"):
                counts["gemini"] += 1
            if v.get("is_fully_validated"):
                counts["full"] += 1

        return {
            "total": len(props),
            "fully_validated": counts["full"],
            "pct_market": round(counts["market"] / total * 100, 1),
            "pct_hit_rates": round(counts["hit_rates"] / total * 100, 1),
            "pct_context": round(counts["context"] / total * 100, 1),
            "pct_mlr": round(counts["mlr"] / total * 100, 1),
            "pct_gemini": round(counts["gemini"] / total * 100, 1),
        }

    # ------------------------------------------------------------------
    # INTERNAL: Atomic publish (temp collection + rename)
    # ------------------------------------------------------------------

    async def _atomic_publish(self, tiers: Dict[str, List[Dict]]) -> Dict:
        """
        Atomic write: for each tier, write to a temp collection then rename.
        If the new tier has picks → swap. If empty → leave existing data intact.
        This ensures collections are NEVER empty during a write operation.
        """
        result = {}
        col_map = self.adapter.tier_collections

        for tier_name, picks in tiers.items():
            target_col = col_map.get(tier_name)
            if not target_col:
                continue

            if not picks:
                # Empty tier — log honestly but do NOT wipe existing collection
                existing = await self.db[target_col].count_documents({})
                logger.info(f"[PUBLISH] {target_col}: 0 new picks (keeping {existing} existing)")
                result[target_col] = existing
                continue

            # Strip _id from picks to avoid duplicate key errors
            clean_picks = [{k: v for k, v in p.items() if k != "_id"} for p in picks]

            # Write to temp collection
            temp_col = f"_tmp_{target_col}_{self.run_id}"
            try:
                await self.db[temp_col].insert_many(clean_picks)

                # Atomic swap: drop target, rename temp → target
                await self.db[target_col].drop()
                await self.db[temp_col].rename(target_col)

                result[target_col] = len(picks)
                logger.info(f"[PUBLISH] {target_col}: {len(picks)} picks (atomic swap)")
            except Exception as e:
                # Cleanup temp on failure, leave original intact
                logger.error(f"[PUBLISH] {target_col} atomic write failed: {e}")
                try:
                    await self.db[temp_col].drop()
                except Exception:
                    pass
                existing = await self.db[target_col].count_documents({})
                result[target_col] = existing

        return result

    # ------------------------------------------------------------------
    # INTERNAL: Post-publish Gemini batch enrichment
    # ------------------------------------------------------------------

    async def _run_gemini_enrichment(self, tiers: Dict[str, List[Dict]]) -> Dict:
        """
        Run batch Gemini enrichment AFTER publish.
        
        Writes vision_intel directly to the live tier collections.
        Non-blocking: failures are logged but never crash the pipeline.
        Also updates the sport-specific cache JSON for serve-time overlay.
        """
        from services.gemini_scout_engine import batch_generate_scout_intel

        sport = self.adapter.sport
        col_map = self.adapter.tier_collections
        tier_labels = {"safe_haven": "Safe Haven", "front_lines": "Front Lines", "war_zone": "War Zone"}

        # Build payloads from all tier picks
        payloads = []
        pick_refs = []  # (collection_name, player_name, stat_type, line)

        for tier_name, picks in tiers.items():
            tier_label = tier_labels.get(tier_name, "Front Lines")
            for pick in picks:
                payloads.append({
                    "player": pick.get("player_name", "?"),
                    "stat": pick.get("stat_type", "?"),
                    "line": pick.get("line", 0),
                    "tier": tier_label,
                    "direction": "OVER" if (pick.get("vk_edge") or pick.get("true_edge") or 0) >= 0 else "UNDER",
                    "lasso_proj": pick.get("vk_predicted"),
                    "edge": pick.get("vk_edge") or pick.get("true_edge") or 0,
                    "edge_pct": pick.get("true_edge") or pick.get("edge_pct") or 0,
                    "h10_rate": pick.get("h10_rate") or pick.get("l10_rate") or pick.get("true_hit_rate") or 0,
                    "h20_rate": pick.get("true_hit_rate") or pick.get("h20_rate") or 0,
                    "cv": pick.get("cv"),
                    "matchup_opponent": pick.get("opponent") or pick.get("opponent_abbr"),
                    "dvp_rank": (pick.get("momentum_data") or {}).get("dvp_rank"),
                    "vacuum_data": pick.get("vacuum_data"),
                    "sport": sport,
                })
                pick_refs.append((
                    col_map.get(tier_name, ""),
                    pick.get("player_name"),
                    pick.get("stat_type"),
                    pick.get("line"),
                ))

        if not payloads:
            return {"attempted": 0, "success": 0, "failed": 0, "skipped": 0}

        stats = {"attempted": len(payloads), "success": 0, "failed": 0, "skipped": 0}

        # Batch call Gemini
        results = await batch_generate_scout_intel(payloads, batch_size=10)

        # Write summaries directly to published collections
        for i, (col_name, player, stat, line) in enumerate(pick_refs):
            key = f"{payloads[i]['player']}|{payloads[i]['stat']}|{payloads[i]['line']}"
            text = results.get(key, "")

            if not text or len(text) < 50:
                stats["failed"] += 1
                continue

            try:
                await self.db[col_name].update_one(
                    {"player_name": player, "stat_type": stat, "line": line},
                    {"$set": {"vision_intel": text, "vision_summary": text,
                              "validation.has_gemini": True}},
                )
                stats["success"] += 1
            except Exception as e:
                logger.warning(f"[GEMINI_ENRICH] Failed to write {player} {stat}: {e}")
                stats["failed"] += 1

        # Update cache JSON for serve-time overlay
        cache_path = os.path.join(CACHE_DIR, f"{sport}_master_active_cache.json")
        try:
            with open(cache_path) as f:
                cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cache = {"props": {}}

        for key, text in results.items():
            if text and len(text) >= 50:
                parts = key.split("|")
                cache_key = f"{parts[0]}_{parts[1]}_{parts[2]}"
                if cache_key not in cache["props"]:
                    cache["props"][cache_key] = {}
                cache["props"][cache_key]["vision_intel"] = text
                cache["props"][cache_key]["player_name"] = parts[0]
                cache["props"][cache_key]["stat_type"] = parts[1]

        try:
            with open(cache_path, "w") as f:
                json.dump(cache, f)
        except Exception as e:
            logger.warning(f"[GEMINI_ENRICH] Cache write failed: {e}")

        logger.info(
            f"[GEMINI_ENRICH] {sport.upper()} | "
            f"success={stats['success']} failed={stats['failed']} "
            f"cache={cache_path}"
        )
        return stats


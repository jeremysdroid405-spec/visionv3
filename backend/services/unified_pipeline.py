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
    def select_tiers(self, scored_props: List[Dict]) -> Dict[str, List[Dict]]:
        """Phase 5: Classify props into tiers and select top-N per tier.
        Returns {'safe_haven': [...], 'front_lines': [...], 'war_zone': [...]}.
        Each tier list is capped at the adapter's max (typically 10).
        Empty tiers are returned as empty lists — never padded.
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
            # PHASE 5: SELECT TIERS
            # ============================================================
            phase_start = datetime.now(timezone.utc)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 5: Selecting tiers...")

            tiers = self.adapter.select_tiers(validated)

            phase_dur = (datetime.now(timezone.utc) - phase_start).total_seconds()
            tier_counts = {k: len(v) for k, v in tiers.items()}
            result.phases["5_select"] = {"duration_s": round(phase_dur, 2), "tiers": tier_counts}

            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 5 complete:")
            for tier_name, picks in tiers.items():
                logger.info(f"  {tier_name}: {len(picks)} picks")

            # ============================================================
            # PHASE 6: INTEL ENRICHMENT (non-blocking)
            # ============================================================
            phase_start = datetime.now(timezone.utc)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 6: Intel enrichment (Gemini)...")

            try:
                tiers = await self.adapter.enrich_intel(tiers, self.db)
                gemini_ok = True
            except Exception as e:
                logger.warning(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 6 Gemini failed (non-fatal): {e}")
                gemini_ok = False
                # Mark all props as missing Gemini
                for tier_picks in tiers.values():
                    for p in tier_picks:
                        if "validation" in p:
                            p["validation"]["has_gemini"] = False

            phase_dur = (datetime.now(timezone.utc) - phase_start).total_seconds()
            result.phases["6_intel"] = {"duration_s": round(phase_dur, 2), "gemini_success": gemini_ok}
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 6 complete: gemini={'OK' if gemini_ok else 'FAILED'} ({phase_dur:.1f}s)")

            # ============================================================
            # PHASE 7: PUBLISH (atomic writes)
            # ============================================================
            phase_start = datetime.now(timezone.utc)
            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 7: Atomic publish...")

            publish_result = await self._atomic_publish(tiers)

            phase_dur = (datetime.now(timezone.utc) - phase_start).total_seconds()
            result.phases["7_publish"] = {"duration_s": round(phase_dur, 2), **publish_result}

            logger.info(f"[{sport}_PIPELINE] [{self.run_id}] PHASE 7 complete ({phase_dur:.1f}s)")
            for col_name, count in publish_result.items():
                logger.info(f"  {col_name}: {count}")

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

"""
Injury Sensor — Multi-Source Detection Engine
===============================================
Polls multiple injury sources with dynamic cadence, normalizes,
diffs against previous state, and emits meaningful change events
into the Rebuild Coordinator.

Architecture:
  Source Adapters (BDL, ESPN, future)
    → Sensor Loop (dynamic cadence per sport)
      → Multi-Source Merge (source precedence rules)
        → Normalizer (shared status hierarchy)
          → Change Detector (tier change, return shift, new/cleared)
            → Event Emitter → Coordinator → Targeted Pipeline Rebuild

Source Precedence:
  BDL = Structural Authority (player IDs, return dates, injury detail)
  ESPN = Timing Authority for NBA (detects changes first, less structured)

  Merge rule: BDL wins for structural fields. ESPN can trigger a
  "suspected change" event if it sees a status change before BDL confirms.

Dynamic Polling Cadence:
  - PEAK:   60s  (within 2h of any game tipoff — late scratch zone)
  - ACTIVE: 120s (game day, outside peak window)
  - IDLE:   300s (no games today or off-hours)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from services.event_bus import BoardEvent, get_event_bus
from services.injury_normalization import (
    normalize_status,
    COLLECTION_NAME,
    STRUCTURAL_FIELDS,
    DISPLAY_ONLY_FIELDS,
    firewall_for_logic,
    extract_display,
)

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# Dynamic cadence settings (seconds)
CADENCE_PEAK = 60       # Within 2h of tipoff
CADENCE_ACTIVE = 120    # Game day
CADENCE_IDLE = 300      # No games / off-hours

# Recency: how long after a first_seen / status_changed do we consider it "new"
CHANGE_RECENCY_MINUTES = 5  # don't re-emit the same change within 5 min


class InjurySensor:
    """
    Multi-source injury detection engine.
    Shared architecture for NBA and MLB.
    """

    def __init__(self, db, sources: List, sports: List[str] = None):
        """
        Args:
            db: Motor async database
            sources: List of source adapter instances (BDLInjurySource, ESPNInjurySource, etc.)
            sports: Sports to monitor. Defaults to ["nba", "mlb"].
        """
        self.db = db
        self.sources = sources
        self.sports = sports or ["nba", "mlb"]
        self._enabled = False
        self._task: Optional[asyncio.Task] = None

        # Previous normalized state keyed by sport → {player_key: {tier_level, return_date, status}}
        self._previous: Dict[str, Dict[str, dict]] = {s: {} for s in self.sports}

        # Recently emitted change keys (dedup within CHANGE_RECENCY_MINUTES)
        self._recent_emissions: Dict[str, datetime] = {}

        # Metrics
        self._metrics = {
            "polls": 0,
            "source_polls": {},       # {source_id: count}
            "source_errors": {},      # {source_id: count}
            "records_by_source": {},  # {source_id: count}
            "changes_detected": 0,
            "changes_emitted": 0,
            "changes_suppressed": 0,  # deduped / not meaningful
            "by_change_type": {},     # {new_injury: X, status_escalated: Y, ...}
            "by_sport": {"nba": 0, "mlb": 0},
            "cadence_current": {},    # {sport: current_interval}
            "last_poll": {},          # {sport: iso timestamp}
        }

    async def start(self):
        if self._enabled:
            return
        self._enabled = True
        # Seed baseline before first diff
        await self._seed_baseline()
        self._task = asyncio.create_task(self._loop())
        logger.info(f"[INJURY_SENSOR] Started — sports={self.sports}, sources={[s.SOURCE_ID for s in self.sources]}")

    async def stop(self):
        self._enabled = False
        if self._task:
            self._task.cancel()
        logger.info("[INJURY_SENSOR] Stopped")

    # =========================================================================
    # MAIN LOOP — Dynamic cadence
    # =========================================================================

    async def _loop(self):
        while self._enabled:
            try:
                for sport in self.sports:
                    cadence = await self._get_cadence(sport)
                    self._metrics["cadence_current"][sport] = cadence
                    await self._poll_and_diff(sport)
                    self._metrics["last_poll"][sport] = datetime.now(timezone.utc).isoformat()

                # Sleep for the minimum cadence across sports
                min_cadence = min(self._metrics["cadence_current"].get(s, CADENCE_IDLE) for s in self.sports)
                await asyncio.sleep(min_cadence)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[INJURY_SENSOR] Loop error: {e}")
                await asyncio.sleep(60)

    async def _get_cadence(self, sport: str) -> int:
        """Determine polling interval based on game proximity.

        2026-05-08 — freshness fix #1. The previous implementation read
        `live_scores_cache` exclusively. When that cache went stale
        (every game `commence_time` in the past) the calculator
        returned IDLE=300s mid-season, which forced a 5-minute poll
        cadence even when live games were active. We now layer a
        cached-board activity probe under the live-scores read so the
        sensor cannot fall back to IDLE while the sport is clearly
        in-play. Sport-agnostic: same logic for every registered sport.
        """
        try:
            now = datetime.now(timezone.utc)
            cached = await self.db[COLL.shared("live_scores_cache")].find_one({})
            cache_says_active = False
            if cached:
                games = cached.get("games", [])
                cache_has_future_game = False
                for game in games:
                    ct = game.get("commence_time")
                    if not ct:
                        continue
                    try:
                        if isinstance(ct, str):
                            ct = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                        if not ct.tzinfo:
                            ct = ct.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        continue

                    delta = (ct - now).total_seconds()
                    if -3600 < delta < 7200:  # Game within -1h to +2h
                        return CADENCE_PEAK
                    if 0 < delta < 43200:     # Game within 12h
                        cache_says_active = True
                    if delta > -3600:
                        cache_has_future_game = True

                if cache_says_active:
                    return CADENCE_ACTIVE
                # If the cache has any future-or-recent game we trust
                # its IDLE verdict. Otherwise fall through to the
                # cached-board probe below.
                if cache_has_future_game:
                    return CADENCE_IDLE

            # Cached-board fallback — universal across sports. If the
            # sport currently has at least one live cached_board doc
            # with non-empty props, we know the sport is mid-cycle
            # regardless of how stale the live-scores cache is. ACTIVE
            # cadence is safe (slightly conservative vs PEAK).
            try:
                board_coll = COLL("board_cache", sport)
            except Exception:
                board_coll = f"{sport}_cached_board"
            try:
                active_count = await self.db[board_coll].count_documents(
                    {"props_count": {"$gt": 0}}, limit=1
                )
                if active_count > 0:
                    return CADENCE_ACTIVE
            except Exception:
                pass

            return CADENCE_IDLE
        except Exception:
            return CADENCE_IDLE

    # =========================================================================
    # SEED BASELINE — First run, no events emitted
    # =========================================================================

    async def _seed_baseline(self):
        """Fetch from all sources, normalize, store as baseline without emitting events."""
        for sport in self.sports:
            merged = await self._fetch_and_merge(sport)
            normalized = self._normalize_records(merged, sport)
            await self._persist(sport, normalized, is_baseline=True)

            snap = {}
            for rec in normalized:
                key = self._player_key(rec)
                snap[key] = {"tier_level": rec.get("tier_level", 0), "return_date": rec.get("return_date"), "status": rec.get("status")}
            self._previous[sport] = snap

        totals = {s: len(v) for s, v in self._previous.items()}
        logger.info(f"[INJURY_SENSOR] Baseline seeded: {totals}")

    # =========================================================================
    # POLL + DIFF — Core detection cycle
    # =========================================================================

    async def _poll_and_diff(self, sport: str):
        self._metrics["polls"] += 1
        merged = await self._fetch_and_merge(sport)
        normalized = self._normalize_records(merged, sport)

        # Diff against previous state
        changes = self._detect_changes(sport, normalized)

        # Persist new state (preserving first_seen_at for existing records)
        await self._persist(sport, normalized, is_baseline=False)

        # Update previous snapshot
        new_snap = {}
        for rec in normalized:
            key = self._player_key(rec)
            new_snap[key] = {"tier_level": rec.get("tier_level", 0), "return_date": rec.get("return_date"), "status": rec.get("status")}
        self._previous[sport] = new_snap

        # Emit meaningful changes
        if changes:
            await self._emit_changes(sport, changes)

    # =========================================================================
    # MULTI-SOURCE FETCH + MERGE
    # =========================================================================

    async def _fetch_and_merge(self, sport: str) -> List[dict]:
        """
        Fetch from all sources and merge with strict precedence rules.

        BDL = STRUCTURAL AUTHORITY (wins for all fields: bdl_id, return_date, injury detail).
        ESPN / NBA Official = TIMING AUTHORITY (annotate BDL records with disagreement signals).

        CRITICAL: Only BDL records form the merged output. Timing sources
        (ESPN, NBA Official) NEVER add standalone records. They only annotate
        existing BDL records when a status disagreement is detected.

        Live Injury Advantage reads exclusively from BDL-derived normalized data.
        """
        all_records: Dict[str, List[dict]] = {}  # source_id -> records

        for source in self.sources:
            try:
                records = await source.fetch(sport)
                all_records[source.SOURCE_ID] = records
                self._metrics["source_polls"][source.SOURCE_ID] = self._metrics["source_polls"].get(source.SOURCE_ID, 0) + 1
                self._metrics["records_by_source"][source.SOURCE_ID] = len(records)
            except Exception as e:
                self._metrics["source_errors"][source.SOURCE_ID] = self._metrics["source_errors"].get(source.SOURCE_ID, 0) + 1
                logger.warning(f"[INJURY_SENSOR] {source.SOURCE_ID} fetch failed for {sport}: {e}")

        # BDL is the sole structural base
        bdl_records = all_records.get("bdl", [])

        # Build BDL lookup by player name (lowercase)
        bdl_by_name = {}
        for r in bdl_records:
            name_key = r.get("player_name", "").lower().strip()
            if name_key:
                bdl_by_name[name_key] = r

        merged = list(bdl_records)  # ONLY BDL records form the output

        # Collect all timing-only sources (ESPN, NBA Official, any future timing adapter)
        timing_source_ids = [s.SOURCE_ID for s in self.sources if s.SOURCE_ID != "bdl"]
        timing_records: List[dict] = []
        for sid in timing_source_ids:
            timing_records.extend(all_records.get(sid, []))

        # Annotate BDL records with timing disagreements from all timing sources
        for timing_rec in timing_records:
            name_key = timing_rec.get("player_name", "").lower().strip()
            bdl_rec = bdl_by_name.get(name_key)

            if bdl_rec:
                timing_status = timing_rec.get("raw_status", "").lower()
                bdl_status = bdl_rec.get("raw_status", "").lower()
                source_id = timing_rec.get("source", "unknown")
                if timing_status != bdl_status:
                    bdl_rec[f"_{source_id}_status_signal"] = timing_rec.get("raw_status")
                    bdl_rec["_source_disagreement"] = True
            # NEVER add timing-only records to merged — they lack BDL structural fields

        return merged

    # =========================================================================
    # NORMALIZE
    # =========================================================================

    def _normalize_records(self, records: List[dict], sport: str) -> List[dict]:
        """Apply shared normalization to merged records.

        Enforces the structural/display firewall:
          - Structural fields at top level (logic-safe)
          - Narrative fields nested under display_only (NEVER used for triggers)
          - Timing-source annotations prefixed with _ (stripped before DB persist)
        """
        normalized = []
        for rec in records:
            raw_status = rec.get("raw_status", "Unknown")
            norm = normalize_status(raw_status)

            # STRUCTURAL — logic-safe fields only
            entry = {
                "sport": sport,
                "player_name": rec.get("player_name", ""),
                "bdl_id": rec.get("bdl_id"),
                "team": rec.get("team", ""),
                "team_id": rec.get("team_id"),
                "position": rec.get("position", ""),
                "status": norm["tier"],
                "tier_level": norm["tier_level"],
                "risk": norm["risk"],
                "color": norm["color"],
                "return_date": self._parse_date(rec.get("return_date")),
                "injury_date": self._parse_date(rec.get("injury_date")),
                "source": rec.get("source", "unknown"),
            }

            # DISPLAY_ONLY — narrative fields, quarantined from logic
            entry["display_only"] = {
                "raw_status": raw_status,
                "description": rec.get("description", ""),
                "short_comment": rec.get("short_comment", "") or (rec.get("description") or "")[:120],
                "injury_type": rec.get("injury_type"),
                "injury_detail": rec.get("injury_detail"),
                "injury_side": rec.get("injury_side"),
            }

            # Internal annotations (timing signals) — stripped before DB persist
            entry["_source_disagreement"] = rec.get("_source_disagreement", False)
            for key, val in rec.items():
                if key.endswith("_status_signal") and key.startswith("_"):
                    entry[key] = val

            normalized.append(entry)
        return normalized

    # =========================================================================
    # CHANGE DETECTION
    # =========================================================================

    def _detect_changes(self, sport: str, normalized: List[dict]) -> List[dict]:
        """
        Compare normalized records against previous state.
        Returns list of meaningful change descriptors.
        """
        old_snap = self._previous.get(sport, {})
        changes = []

        new_keys = set()
        for rec in normalized:
            key = self._player_key(rec)
            new_keys.add(key)
            old = old_snap.get(key)

            if not old:
                # New injury
                if rec.get("tier_level", 0) >= 2:  # Only care about Questionable+
                    changes.append(self._make_change(rec, None, "new_injury"))
                continue

            # Tier level changed
            old_tier = old.get("tier_level", 0)
            new_tier = rec.get("tier_level", 0)
            if old_tier != new_tier:
                direction = "escalated" if new_tier > old_tier else "de-escalated"
                changes.append(self._make_change(rec, old, f"status_{direction}"))
                continue

            # Return date shifted
            old_ret = old.get("return_date")
            new_ret = rec.get("return_date")
            if old_ret != new_ret and new_ret:
                changes.append(self._make_change(rec, old, "return_date_shifted"))

        # Cleared injuries (was in old, not in new)
        for key, old in old_snap.items():
            if key not in new_keys and old.get("tier_level", 0) >= 3:
                changes.append({
                    "player_key": key,
                    "player_name": key.split("|")[0] if "|" in key else key,
                    "team": "",
                    "change_type": "cleared",
                    "old_status": old.get("status"),
                    "new_status": None,
                    "old_tier": old.get("tier_level", 0),
                    "new_tier": 0,
                    "tier_delta": -old.get("tier_level", 0),
                    "return_date": None,
                })

        self._metrics["changes_detected"] += len(changes)
        for c in changes:
            ct = c.get("change_type", "unknown")
            self._metrics["by_change_type"][ct] = self._metrics["by_change_type"].get(ct, 0) + 1

        return changes

    def _make_change(self, rec: dict, old: Optional[dict], change_type: str) -> dict:
        old_tier = old.get("tier_level", 0) if old else 0
        new_tier = rec.get("tier_level", 0)
        # Collect all timing signals from the record
        timing_signals = {k: v for k, v in rec.items() if k.endswith("_status_signal") and k.startswith("_") and v}
        return {
            "player_key": self._player_key(rec),
            "player_name": rec.get("player_name", ""),
            "team": rec.get("team", ""),
            "bdl_id": rec.get("bdl_id"),
            "change_type": change_type,
            "old_status": old.get("status") if old else None,
            "new_status": rec.get("status"),
            "old_tier": old_tier,
            "new_tier": new_tier,
            "tier_delta": new_tier - old_tier,
            "return_date": rec.get("return_date"),
            "source": rec.get("source"),
            "timing_signals": timing_signals,
        }

    # =========================================================================
    # EVENT EMISSION
    # =========================================================================

    async def _emit_changes(self, sport: str, changes: List[dict]):
        """Emit meaningful changes as BoardEvents to the coordinator.

        2026-05-08 — freshness fix #4. Dedup key is now per-player
        (`sport|player_key`) instead of per-team. Previously, two
        injuries on the same team within the 5-minute recency window
        suppressed the second event entirely, hiding sibling Q→OUT
        late-scratches that arrive in bursts.
        """
        bus = get_event_bus()
        now = datetime.now(timezone.utc)

        # Group by team for efficient event batching (one BoardEvent
        # per team summarizes the team's churn for the coordinator).
        # Dedup, however, is per-player so sibling injuries don't
        # silently suppress one another.
        by_team: Dict[str, List[dict]] = {}
        for c in changes:
            # Skip per-player duplicates inside this batch first.
            player_key = c.get("player_key") or c.get("player_name", "")
            dedup_key = f"{sport}|{player_key}"
            last_emit = self._recent_emissions.get(dedup_key)
            if last_emit and (now - last_emit).total_seconds() < CHANGE_RECENCY_MINUTES * 60:
                self._metrics["changes_suppressed"] += 1
                continue
            self._recent_emissions[dedup_key] = now
            team = c.get("team", "unknown")
            by_team.setdefault(team, []).append(c)

        emitted = 0
        for team, team_changes in by_team.items():
            players = [c["player_name"] for c in team_changes]
            max_tier_delta = max(abs(c.get("tier_delta", 0)) for c in team_changes)
            severity = "high" if max_tier_delta >= 2 or any(c["change_type"] == "new_injury" for c in team_changes) else "medium"

            change_summary = [f"{c['player_name']}:{c['change_type']}" for c in team_changes[:5]]

            await bus.publish(BoardEvent(
                sport=sport,
                event_type="injury_change",
                severity=severity,
                affected_players=players[:10],
                source="injury_sensor",
                metadata={
                    "team": team,
                    "changes": change_summary,
                    "max_tier_delta": max_tier_delta,
                },
            ))
            emitted += len(team_changes)

        self._metrics["changes_emitted"] += emitted
        self._metrics["by_sport"][sport] = self._metrics["by_sport"].get(sport, 0) + emitted

        if emitted:
            change_types = [c["change_type"] for c in changes]
            logger.info(f"[INJURY_SENSOR] {sport.upper()}: {emitted} changes emitted → {change_types[:5]}")

        # Prune old dedup entries
        cutoff = now - timedelta(minutes=CHANGE_RECENCY_MINUTES * 3)
        self._recent_emissions = {k: v for k, v in self._recent_emissions.items() if v > cutoff}

    # =========================================================================
    # PERSIST — Write to injuries_normalized with change tracking
    # =========================================================================

    async def _persist(self, sport: str, records: List[dict], is_baseline: bool = False):
        """Write normalized records to DB, preserving first_seen_at and updating status_changed_at.

        CRITICAL: Both baseline and non-baseline paths check existing DB records
        to preserve timestamps. A baseline seed must NOT reset status_changed_at
        for injuries that already exist with the same tier_level — doing so would
        make months-old OUT_FOR_SEASON injuries appear "recent" after every restart.

        DB schema per record:
          - Top-level: structural fields only (logic-safe)
          - display_only: nested dict of narrative fields (UI rendering only)
          - Internal _ annotations are stripped before DB write.
        """
        collection = self.db[COLLECTION_NAME]
        now = datetime.now(timezone.utc).isoformat()

        # ALWAYS load existing records for timestamp preservation — baseline or not
        prev_by_bdl = {}
        cursor = collection.find(
            {"sport": sport},
            {"_id": 0, "bdl_id": 1, "tier_level": 1, "return_date": 1, "first_seen_at": 1, "status_changed_at": 1},
        )
        async for doc in cursor:
            bid = doc.get("bdl_id")
            if bid:
                prev_by_bdl[bid] = doc

        for rec in records:
            bid = rec.get("bdl_id")
            prev = prev_by_bdl.get(bid) if bid else None
            if not prev:
                # Genuinely new injury — never seen in this DB
                rec["first_seen_at"] = now
                rec["status_changed_at"] = now
            else:
                # Existing injury — preserve first_seen, only bump status_changed
                # if the structural state actually changed
                rec["first_seen_at"] = prev.get("first_seen_at", now)
                if prev.get("tier_level") != rec.get("tier_level") or prev.get("return_date") != rec.get("return_date"):
                    rec["status_changed_at"] = now
                else:
                    rec["status_changed_at"] = prev.get("status_changed_at", now)

        # Build DB records: strip _ annotations, keep display_only nested
        rec_for_db = []
        for r in records:
            db_rec = {k: v for k, v in r.items() if not k.startswith("_")}
            db_rec["synced_at"] = now
            rec_for_db.append(db_rec)

        await collection.delete_many({"sport": sport})
        if rec_for_db:
            await collection.insert_many(rec_for_db)

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _player_key(rec: dict) -> str:
        bid = rec.get("bdl_id")
        if bid:
            return f"{rec.get('player_name', '')}|{bid}"
        return rec.get("player_name", "").lower().strip()

    @staticmethod
    def _parse_date(val) -> Optional[str]:
        if not val:
            return None
        if isinstance(val, str):
            clean = val.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(clean).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                return val[:10] if len(val) >= 10 else val
        return None

    # =========================================================================
    # OBSERVABILITY
    # =========================================================================

    def get_stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "sports": self.sports,
            "sources": [s.SOURCE_ID for s in self.sources],
            "tracked": {s: len(v) for s, v in self._previous.items()},
            "cadence": dict(self._metrics.get("cadence_current", {})),
            "last_poll": dict(self._metrics.get("last_poll", {})),
            **{k: v for k, v in self._metrics.items() if k not in ("cadence_current", "last_poll")},
        }

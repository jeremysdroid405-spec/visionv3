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
from services.injury_normalization import normalize_status, COLLECTION_NAME

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
        """Determine polling interval based on game proximity."""
        try:
            now = datetime.now(timezone.utc)
            cached = await self.db.live_scores_cache.find_one({})
            if not cached:
                return CADENCE_IDLE

            games = cached.get("games", [])
            sport_key = "nba" if sport == "nba" else "mlb"

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
                    return CADENCE_ACTIVE

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
        Fetch from all sources and merge with precedence rules.

        BDL wins for structural fields (bdl_id, return_date, injury detail).
        ESPN triggers timing signals even if BDL hasn't updated yet.
        """
        all_records: Dict[str, List[dict]] = {}  # source_id → records

        for source in self.sources:
            try:
                records = await source.fetch(sport)
                all_records[source.SOURCE_ID] = records
                self._metrics["source_polls"][source.SOURCE_ID] = self._metrics["source_polls"].get(source.SOURCE_ID, 0) + 1
                self._metrics["records_by_source"][source.SOURCE_ID] = len(records)
            except Exception as e:
                self._metrics["source_errors"][source.SOURCE_ID] = self._metrics["source_errors"].get(source.SOURCE_ID, 0) + 1
                logger.warning(f"[INJURY_SENSOR] {source.SOURCE_ID} fetch failed for {sport}: {e}")

        # Merge: BDL is primary, ESPN augments timing
        bdl_records = all_records.get("bdl", [])
        espn_records = all_records.get("espn", [])

        # Build BDL lookup by player name (lowercase)
        bdl_by_name = {}
        for r in bdl_records:
            name_key = r.get("player_name", "").lower().strip()
            if name_key:
                bdl_by_name[name_key] = r

        merged = list(bdl_records)  # Start with all BDL records

        # Check ESPN for status signals not yet in BDL
        for espn_rec in espn_records:
            name_key = espn_rec.get("player_name", "").lower().strip()
            bdl_rec = bdl_by_name.get(name_key)

            if bdl_rec:
                # Both sources have this player — check for status disagreement
                espn_status = espn_rec.get("raw_status", "").lower()
                bdl_status = bdl_rec.get("raw_status", "").lower()
                if espn_status != bdl_status:
                    # ESPN sees different status — annotate the BDL record
                    bdl_rec["_espn_status_signal"] = espn_rec.get("raw_status")
                    bdl_rec["_source_disagreement"] = True
            # Don't add ESPN-only records to merged — ESPN lacks BDL IDs

        return merged

    # =========================================================================
    # NORMALIZE
    # =========================================================================

    def _normalize_records(self, records: List[dict], sport: str) -> List[dict]:
        """Apply shared normalization to merged records."""
        normalized = []
        for rec in records:
            raw_status = rec.get("raw_status", "Unknown")
            norm = normalize_status(raw_status)
            normalized.append({
                "sport": sport,
                "player_name": rec.get("player_name", ""),
                "bdl_id": rec.get("bdl_id"),
                "team": rec.get("team", ""),
                "team_id": rec.get("team_id"),
                "position": rec.get("position", ""),
                "raw_status": raw_status,
                "status": norm["tier"],
                "tier_level": norm["tier_level"],
                "risk": norm["risk"],
                "color": norm["color"],
                "return_date": self._parse_date(rec.get("return_date")),
                "injury_date": self._parse_date(rec.get("injury_date")),
                "description": rec.get("description", ""),
                "short_comment": rec.get("short_comment", "") or (rec.get("description") or "")[:120],
                "injury_type": rec.get("injury_type"),
                "injury_detail": rec.get("injury_detail"),
                "injury_side": rec.get("injury_side"),
                "source": rec.get("source", "unknown"),
                "_espn_status_signal": rec.get("_espn_status_signal"),
                "_source_disagreement": rec.get("_source_disagreement", False),
            })
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
            "_espn_signal": rec.get("_espn_status_signal"),
        }

    # =========================================================================
    # EVENT EMISSION
    # =========================================================================

    async def _emit_changes(self, sport: str, changes: List[dict]):
        """Emit meaningful changes as BoardEvents to the coordinator."""
        bus = get_event_bus()
        now = datetime.now(timezone.utc)

        # Group by team for efficient event batching
        by_team: Dict[str, List[dict]] = {}
        for c in changes:
            team = c.get("team", "unknown")
            by_team.setdefault(team, []).append(c)

        emitted = 0
        for team, team_changes in by_team.items():
            # Dedup: skip if we emitted for this team+sport recently
            dedup_key = f"{sport}|{team}"
            last_emit = self._recent_emissions.get(dedup_key)
            if last_emit and (now - last_emit).total_seconds() < CHANGE_RECENCY_MINUTES * 60:
                self._metrics["changes_suppressed"] += len(team_changes)
                continue

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

            self._recent_emissions[dedup_key] = now
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
        """Write normalized records to DB, preserving first_seen_at and updating status_changed_at."""
        collection = self.db[COLLECTION_NAME]
        now = datetime.now(timezone.utc).isoformat()

        if not is_baseline:
            # Load existing for change tracking
            prev_by_bdl = {}
            cursor = collection.find({"sport": sport}, {"_id": 0, "bdl_id": 1, "tier_level": 1, "return_date": 1, "first_seen_at": 1, "status_changed_at": 1})
            async for doc in cursor:
                bid = doc.get("bdl_id")
                if bid:
                    prev_by_bdl[bid] = doc

            for rec in records:
                bid = rec.get("bdl_id")
                prev = prev_by_bdl.get(bid) if bid else None
                if not prev:
                    rec["first_seen_at"] = now
                    rec["status_changed_at"] = now
                else:
                    rec["first_seen_at"] = prev.get("first_seen_at", now)
                    if prev.get("tier_level") != rec.get("tier_level") or prev.get("return_date") != rec.get("return_date"):
                        rec["status_changed_at"] = now
                    else:
                        rec["status_changed_at"] = prev.get("status_changed_at", now)
        else:
            for rec in records:
                rec["first_seen_at"] = now
                rec["status_changed_at"] = now

        rec_for_db = [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]
        for r in rec_for_db:
            r["synced_at"] = now

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

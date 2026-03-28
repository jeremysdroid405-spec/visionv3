"""
Line Movement Tracker Service
=============================
Tracks prop line changes between syncs to identify "hot" picks.

Line movement is the best indicator of where money is flowing:
- Line moves UP (24.5 -> 25.5) = Heavy OVER action
- Line moves DOWN (24.5 -> 23.5) = Heavy UNDER action
- Big moves (1+ point) = Sharp money or heavy public action

This replaces the redundant "Top Picks" with genuinely trending props.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


class LineMovementTracker:
    """Tracks line movement between syncs to identify popular/sharp picks."""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.line_history = db["line_history"]  # Stores historical lines
        self.line_movements = db["line_movements"]  # Stores detected movements
    
    async def setup_indexes(self):
        """Create indexes for efficient queries."""
        await self.line_history.create_index([
            ("player_name", 1),
            ("stat_type", 1),
            ("recorded_at", -1)
        ])
        await self.line_movements.create_index([
            ("detected_at", -1)
        ])
        await self.line_movements.create_index([
            ("player_name", 1),
            ("stat_type", 1)
        ])
        logger.info("[LINE_TRACKER] Indexes created")
    
    async def record_current_lines(self, players: List[Dict]) -> int:
        """
        Record current lines for all players.
        Called during each sync.
        
        Returns number of lines recorded.
        """
        now = datetime.now(timezone.utc)
        lines_recorded = 0
        
        for player in players:
            player_name = player.get("player_name")
            team = player.get("team")
            props = player.get("props", [])
            
            for prop in props:
                stat_type = prop.get("stat_type")
                line = prop.get("line")
                
                if not all([player_name, stat_type, line]):
                    continue
                
                # Record this line
                await self.line_history.insert_one({
                    "player_name": player_name,
                    "team": team,
                    "stat_type": stat_type,
                    "line": float(line),
                    "recorded_at": now
                })
                lines_recorded += 1
        
        logger.info(f"[LINE_TRACKER] Recorded {lines_recorded} current lines")
        return lines_recorded
    
    async def detect_line_movements(self, players: List[Dict]) -> List[Dict]:
        """
        Compare current lines to previous sync and detect movements.
        
        Returns list of significant line movements.
        """
        now = datetime.now(timezone.utc)
        movements = []
        
        # Get the previous sync timestamp (look for lines from 30min - 24hr ago)
        lookback_start = now - timedelta(hours=24)
        lookback_end = now - timedelta(minutes=30)
        
        for player in players:
            player_name = player.get("player_name")
            team = player.get("team")
            photo_url = player.get("photo_url")
            props = player.get("props", [])
            
            for prop in props:
                stat_type = prop.get("stat_type")
                current_line = prop.get("line")
                h5_rate = prop.get("h5_rate")
                h10_rate = prop.get("h10_rate")
                season_avg = prop.get("season_avg")
                
                if not all([player_name, stat_type, current_line]):
                    continue
                
                current_line = float(current_line)
                
                # Find the most recent previous line for this player/stat
                previous = await self.line_history.find_one(
                    {
                        "player_name": player_name,
                        "stat_type": stat_type,
                        "recorded_at": {"$gte": lookback_start, "$lte": lookback_end}
                    },
                    sort=[("recorded_at", -1)]
                )
                
                if previous:
                    previous_line = previous.get("line", current_line)
                    movement = current_line - previous_line
                    
                    # Only track significant movements (0.5+ points)
                    if abs(movement) >= 0.5:
                        direction = "up" if movement > 0 else "down"
                        
                        # Determine what this movement means
                        if movement > 0:
                            action = "OVER"  # Line went up = heavy over action
                            sentiment = "🔥 Heavy OVER action"
                        else:
                            action = "UNDER"  # Line went down = heavy under action
                            sentiment = "📉 Heavy UNDER action"
                        
                        movement_data = {
                            "player_name": player_name,
                            "team": team,
                            "photo_url": photo_url,
                            "stat_type": stat_type,
                            "previous_line": previous_line,
                            "current_line": current_line,
                            "movement": round(movement, 1),
                            "movement_abs": round(abs(movement), 1),
                            "direction": direction,
                            "action": action,
                            "sentiment": sentiment,
                            "h5_rate": h5_rate,
                            "h10_rate": h10_rate,
                            "season_avg": season_avg,
                            "detected_at": now,
                            "previous_recorded_at": previous.get("recorded_at")
                        }
                        
                        movements.append(movement_data)
        
        # Sort by absolute movement (biggest moves first)
        movements.sort(key=lambda x: x.get("movement_abs", 0), reverse=True)
        
        # Store movements in database
        if movements:
            # Clear old movements (keep last 24h)
            await self.line_movements.delete_many({
                "detected_at": {"$lt": now - timedelta(hours=24)}
            })
            
            # Insert new movements
            await self.line_movements.insert_many(movements)
            
            logger.info(f"[LINE_TRACKER] Detected {len(movements)} line movements")
            for m in movements[:5]:
                logger.info(f"  {m['player_name']} {m['stat_type']}: {m['previous_line']} -> {m['current_line']} ({m['movement']:+.1f})")
        
        return movements
    
    async def get_trending_picks(self, limit: int = 12) -> List[Dict]:
        """
        Get the most trending picks based on line movement.
        
        Returns picks sorted by absolute line movement (biggest movers first).
        """
        now = datetime.now(timezone.utc)
        
        # Get movements from last 24 hours
        movements = await self.line_movements.find(
            {"detected_at": {"$gte": now - timedelta(hours=24)}},
            {"_id": 0}
        ).sort("movement_abs", -1).limit(limit).to_list(limit)
        
        # Enrich with pick data
        for m in movements:
            # Add line display
            m["line"] = m.get("current_line")
            m["line_movement_display"] = f"{m['previous_line']} → {m['current_line']}"
            
            # Categorize movement magnitude
            movement_abs = m.get("movement_abs", 0)
            if movement_abs >= 2:
                m["movement_category"] = "MASSIVE"
                m["movement_badge"] = "🚨 MAJOR MOVE"
            elif movement_abs >= 1:
                m["movement_category"] = "SIGNIFICANT" 
                m["movement_badge"] = "🔥 HOT"
            else:
                m["movement_category"] = "MODERATE"
                m["movement_badge"] = "📈 MOVING"
        
        return movements
    
    async def cleanup_old_history(self, days_to_keep: int = 7):
        """Clean up line history older than specified days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
        result = await self.line_history.delete_many({"recorded_at": {"$lt": cutoff}})
        logger.info(f"[LINE_TRACKER] Cleaned up {result.deleted_count} old line history records")


# Singleton pattern
_tracker_instance = None

def get_line_tracker(db: AsyncIOMotorDatabase) -> LineMovementTracker:
    """Get or create the line movement tracker."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = LineMovementTracker(db)
    return _tracker_instance

"""
Gemini Scout Intelligence Engine
==================================
Replaces static f-string templates with real generative AI.
Uses Gemini Flash via emergentintegrations for gritty scout summaries.
Concurrent batch processing for Ferrari Tier enrichment cycles.
"""

import os
import json
import logging
import asyncio
import uuid
from typing import Dict, Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")

SYSTEM_PROMPT = """You are a highly analytical NBA/MLB daily fantasy sports scout with 20 years of experience. You speak in sharp, confident, punchy prose — like a seasoned Vegas sharp texting his inner circle.

I will provide you with a JSON payload containing raw data from a Lasso regression model and contextual matchup data. Write exactly two gritty, punchy sentences explaining why we are taking the OVER or UNDER on this prop.

Rules:
- Highlight the specific numerical edge and the top mathematical driver feature
- Reference the Lasso projection vs the line with exact numbers
- If the edge is large (>15%), be aggressive and confident
- If the edge is small (<5%), be cautious and note the risk
- If the hit rate is high (>70%), mention the streak
- If the matchup is soft (DvP rank >20), call it out
- If the matchup is tough (DvP rank <8), flag the friction
- Never use generic sports cliches like "giving 110%" or "stepping up"
- Never mention JSON, payloads, data structures, or technical terms like "Lasso" or "regression" or "R-squared"
- Never use emojis
- Output ONLY the final two-sentence scout text, nothing else"""


async def generate_gemini_scout_intel(payload: Dict) -> str:
    """
    Call Gemini Flash to generate a two-sentence scout summary.
    
    payload keys:
        player, stat, line, direction, lasso_proj, edge, edge_pct,
        r_squared, confidence_tier, top_drivers, h10_rate, l10_avg,
        matchup_opponent, dvp_rank, dvp_label, pace_delta, stability_score,
        usage_bump, sport
    """
    if not EMERGENT_LLM_KEY:
        return _fallback(payload)

    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"scout-{uuid.uuid4().hex[:8]}",
            system_message=SYSTEM_PROMPT,
        ).with_model("gemini", "gemini-3-flash-preview")

        user_msg = UserMessage(text=json.dumps(payload, default=str))
        response = await asyncio.wait_for(
            chat.send_message(user_msg),
            timeout=10.0,
        )

        text = str(response).strip()
        if len(text) < 20:
            return _fallback(payload)
        return text

    except asyncio.TimeoutError:
        logger.warning(f"[GEMINI] Timeout for {payload.get('player')}")
        return _fallback(payload)
    except Exception as e:
        logger.warning(f"[GEMINI] Error for {payload.get('player')}: {e}")
        return _fallback(payload)


async def batch_generate_scout_intel(payloads: list) -> list:
    """Process a batch of payloads concurrently."""
    tasks = [generate_gemini_scout_intel(p) for p in payloads]
    return await asyncio.gather(*tasks, return_exceptions=False)


def build_scout_payload(
    player_name: str,
    stat_type: str,
    line: float,
    lasso_result: Optional[Dict] = None,
    board_pick: Optional[Dict] = None,
    intel_suite: Optional[Dict] = None,
    sport: str = "nba",
) -> Dict:
    """Build the strict JSON payload for the Gemini scout call."""
    proj = lasso_result.get("projection", 0) if lasso_result else 0
    edge = (proj - line) if proj and line else 0
    edge_pct = (edge / line * 100) if line else 0
    top_contribs = lasso_result.get("top_contributors", []) if lasso_result else []

    from services.intel_suite_calculator import _humanize_driver, FEATURE_DISPLAY
    drivers = []
    for c in top_contribs[:3]:
        fname = c.get("feature", "")
        drivers.append({
            "name": _humanize_driver(fname),
            "contribution": c.get("contribution", 0),
            "raw_value": c.get("raw_value", 0),
        })

    dvp = (intel_suite or {}).get("matchup_dvp", {})
    pace = (intel_suite or {}).get("pace_delta", {})
    stab = (intel_suite or {}).get("stability_index", {})
    usage = (intel_suite or {}).get("usage_ripple", {})

    return {
        "player": player_name,
        "stat": stat_type,
        "line": line,
        "direction": "OVER" if edge > 0 else "UNDER",
        "lasso_proj": round(proj, 1) if proj else None,
        "edge": round(edge, 2) if edge else 0,
        "edge_pct": round(edge_pct, 1),
        "confidence_tier": (lasso_result or {}).get("confidence_tier"),
        "top_drivers": drivers,
        "h10_rate": (board_pick or {}).get("h10_rate") or 0,
        "l10_avg": (board_pick or {}).get("l10_avg") or 0,
        "matchup_opponent": dvp.get("opponent", "Unknown"),
        "dvp_rank": dvp.get("rank"),
        "dvp_label": dvp.get("friction_label"),
        "pace_delta": pace.get("possessions", 0),
        "tempo_label": pace.get("tempo_label"),
        "stability_score": stab.get("score"),
        "usage_bump": usage.get("bump_percent", 0),
        "sport": sport,
    }


def _fallback(payload: Dict) -> str:
    """Baseline fallback if LLM fails."""
    player = payload.get("player", "Player")
    stat = payload.get("stat", "stat")
    proj = payload.get("lasso_proj", "?")
    line = payload.get("line", "?")
    edge = payload.get("edge", 0)
    direction = payload.get("direction", "OVER")
    return f"{player} {stat} — Projection: {proj} vs Line: {line} ({direction} {edge:+.1f} edge)."

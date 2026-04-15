"""
Gemini Scout Intelligence Engine
==================================
Replaces static f-string templates with real generative AI.
Uses Gemini Flash via litellm for gritty scout summaries.
Concurrent batch processing for Ferrari Tier enrichment cycles.
"""

import os
import json
import logging
import asyncio
from typing import Dict, Optional

import litellm

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

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


async def generate_gemini_scout_intel(payload: Dict, max_retries: int = 3) -> str:
    """
    Call Gemini Flash to generate a two-sentence scout summary.
    Retries with exponential backoff on rate limits / transient errors.
    """
    if not GOOGLE_API_KEY:
        return _fallback(payload)

    player = payload.get("player", "?")
    user_text = json.dumps(payload, default=str)

    for attempt in range(max_retries):
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model="gemini/gemini-3-flash-preview",
                    api_key=GOOGLE_API_KEY,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text},
                    ],
                    max_tokens=200,
                    temperature=0.7,
                ),
                timeout=12.0,
            )

            text = response.choices[0].message.content.strip()
            if len(text) >= 20:
                return text

        except asyncio.TimeoutError:
            logger.warning(f"[GEMINI] Timeout for {player} (attempt {attempt+1}/{max_retries})")
        except Exception as e:
            err = str(e)
            is_retryable = any(k in err.lower() for k in [
                "rate", "limit", "429", "503", "unavailable", "reset",
                "connect", "timeout", "overloaded", "capacity",
            ])
            if is_retryable and attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.info(f"[GEMINI] Retryable error for {player}, waiting {wait}s (attempt {attempt+1}): {err[:80]}")
                await asyncio.sleep(wait)
                continue
            logger.warning(f"[GEMINI] Failed for {player} after {attempt+1} attempts: {err[:120]}")
            return _fallback(payload)

    return _fallback(payload)


async def batch_generate_scout_intel(payloads: list, concurrency: int = 5) -> list:
    """Process a batch of payloads with limited concurrency to avoid rate limits."""
    sem = asyncio.Semaphore(concurrency)

    async def _run(p):
        async with sem:
            return await generate_gemini_scout_intel(p)

    return await asyncio.gather(*[_run(p) for p in payloads])


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
    """Baseline fallback if LLM fails — still punchy."""
    player = payload.get("player", "Player")
    stat = payload.get("stat", "stat")
    proj = payload.get("lasso_proj")
    line = payload.get("line", "?")
    edge = payload.get("edge", 0)
    edge_pct = payload.get("edge_pct", 0)
    direction = payload.get("direction", "OVER")
    h10 = payload.get("h10_rate", 0)
    dvp_rank = payload.get("dvp_rank")
    dvp_label = payload.get("dvp_label")
    
    # Build a two-sentence summary with whatever data we have
    parts = []
    
    # Sentence 1: The edge call
    if proj and edge:
        if abs(edge_pct) >= 15:
            parts.append(f"{player} is projecting {proj} on {stat} against a line of {line} — that's a {edge_pct:+.1f}% edge the books haven't priced in.")
        else:
            parts.append(f"{player} {stat} projects to {proj} vs a {line} line ({direction} {edge:+.1f}).")
    else:
        parts.append(f"{player} {stat} at {line} — riding the {direction.lower()} side here.")
    
    # Sentence 2: Supporting context
    if h10 >= 80:
        parts.append(f"Hitting {h10:.0f}% over the last 10 — this is a heater you don't fade.")
    elif h10 >= 65:
        parts.append(f"L10 hit rate sits at {h10:.0f}%, steady enough to back with confidence.")
    elif dvp_rank and dvp_rank >= 20:
        parts.append(f"Matchup grades out soft ({dvp_label or 'bottom-10 defense'}) — volume should be there tonight.")
    elif dvp_rank and dvp_rank <= 8:
        parts.append(f"Tough matchup on paper (#{dvp_rank} defense) — proceed with caution.")
    else:
        parts.append(f"The math leans {direction.lower()} but stay disciplined with your unit size.")
    
    return " ".join(parts)

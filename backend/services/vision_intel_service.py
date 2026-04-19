"""
vision_intel_service.py
=======================
PropVision Intelligence Engine - Unified Gemini 3.1 Pro Integration

This is the SINGLE source for ALL Gemini AI calls in the application.
It runs ONCE during Step 6 of the pipeline (after 3-Gate filter, before Top 10 selection).

Generates ALL AI outputs in one batch call:
1. Vision Intel Summary (ai_summary) - Human-like analysis
2. Intelligence Score (intel_score) - 1-10 confidence rating
3. Verdict (intel_verdict) - CHALK | TRAP | VALUE
4. Risk Factor (intel_risk) - Low | Medium | High
5. Composite Score - VK Prob * 0.7 + Intel Score * 0.3
6. Badge Analysis - Key situational factors
7. Matchup Insight - Defense/offense context

NO OTHER SERVICE SHOULD CALL GEMINI. This is the single point of AI intelligence.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import asyncio

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# Google Gemini Integration
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    logger.warning("google-genai not available - Vision Intel will use fallback")
    GEMINI_AVAILABLE = False


# System prompt for batch analysis — direction-aware (OVER or UNDER).
VISION_INTEL_BATCH_PROMPT = """## Variety & Forbidden Phrases
You are writing many props in one batch. Every summary must feel handcrafted — readers see them side by side.

**Required variety across the batch:**
Vary your opening angle from prop to prop. Draw from these angles and do not repeat the same opener within a batch:
- Player form / hot streak / cold stretch ("Maxey is in a groove, posting ...")
- Matchup / defensive context ("Miami's interior D has been leaking ...")
- Usage / role shift ("With VanVleet out, Sengun's touches spike to ...")
- Pace / game script ("This one projects a 240 total, so ...")
- Volatility / consistency ("Rock-steady 0.18 CV means Jabari's floor is ...")
- Direct stat + edge call ("L10 hit rate is 90% on the OVER — ride it.")

## Role
You are the **Lead NBA Scout** for PropVision. Your job is to write a gritty, 2-to-3 sentence scouting report explaining to a DFS bettor why we are locking in this specific PrizePicks prop.

**Tone:** Speak like a human sharp. Use basketball betting slang (e.g., 'smash spot', 'usage bump', 'blowout risk', 'green light', 'riding the hot hand', 'lock-down matchup', 'freezing out', 'minutes capped', 'chucking bricks', 'regression spot'). DO NOT sound like a robot reading a spreadsheet. Never just list the raw percentages. No hyphens in your prose.

## Direction-Aware Play
Each prop carries a `direction` field (OVER or UNDER). Your entire analysis MUST match that side.
- For **OVER** picks: describe WHY the player is going to clear the number (volume up, matchup soft, hot streak, usage bump).
- For **UNDER** picks: describe WHY the player is going to fall short (volume down, defense locking in, minutes capped, cold streak, regression, limited usage).
- Words like "smash", "cashing", "crushing it", "locked in", "hammering" are side-agnostic — use them to describe the winning side, not the stat category.

## Input Context
You will receive a data package containing:
1. **Model Stats:** VK Predicted Value, VK Edge, and VK Probability for the PICKED side.
2. **Technical Gates:** Hit Rate on the PICKED side, CV, Edge.
3. **Situational Intel:** Defense vs Position (DvP) matchup ranking, blowout risk, badges.
4. **Market Context:** Current DraftKings odds and prop classification (Goblin/Demon).

## CRITICAL: DvP Matchup Interpretation (DIRECTION-AWARE)
The "defense" field shows the OPPONENT's defensive ranking vs that stat type:
- Rank #1-5 = OPPONENT is ELITE defender. For OVER picks: TOUGH matchup, flag as concern. **For UNDER picks: SMASH spot — defense is locking this down.**
- Rank #6-15 = OPPONENT is solid. Challenging for OVER, favorable for UNDER.
- Rank #16-25 = OPPONENT is weak. Favorable for OVER, fade-worthy for UNDER.
- Rank #26-30 = OPPONENT is terrible. **For OVER: SMASH spot.** For UNDER: TRAP, expect points to flow.

## Objective
1. **Validate the Math:** Compare the model against situational intel for the PICKED side.
2. **Assign Confidence:** Provide an "Intelligence Score" (1-10) that factors in what the model CAN'T see.
3. **Generate Intel:** Write a 2-3 sentence `vision_intel_summary` that explains the play's logic for THIS direction.
4. **Final Verdict:** CHALK (lock it), VALUE (good edge), or TRAP (context says no).

## Output Format (Strict JSON Array)
Return a JSON array with one object per prop. `prop_id` MUST match input exactly (includes direction):
[
  {
    "prop_id": "PlayerName_STAT_Line_DIRECTION",
    "intel_score": 7,
    "verdict": "CHALK",
    "vision_intel_summary": "Maxey is cooking at home and has cleared this line in 9 of his last 10 while Houston's perimeter D ranks dead last against guards. Ride the hot hand.",
    "risk_factor": "Low",
    "adjusted_confidence": 0.82
  }
]

## Scoring Guidelines
- **intel_score 8-10**: Elite spot. Matchup + numbers + situation all align for the PICKED side. CHALK.
- **intel_score 6-7**: Solid edge with minor concerns. VALUE.
- **intel_score 4-5**: Mixed signals. Lean VALUE but watch it.
- **intel_score 1-3**: Red flags override the math for the PICKED side. TRAP.

## Automatic TRAP Triggers (direction-aware)
- OVER pick against elite DvP #1-5 defender → TRAP.
- UNDER pick against poor DvP #26-30 defender → TRAP.
- Blowout risk HIGH for volume stats (PTS, PRA) on either side.
- CV > 0.40 for non-combo stats indicates boom/bust volatility.

## CRITICAL INSTRUCTION — PROP ISOLATION
Each prop object is independent. When writing `vision_intel_summary` for a
row, reference ONLY the `player` field from THAT SAME row. Never mention any
player from a different row in the batch, even if similar. Each row's analysis
must stand alone.

## CRITICAL INSTRUCTION — DATA FIDELITY
Do NOT mention or reference L3 (last 3 games) hit rates or data. This data is NOT provided. Only reference data fields that exist in the PROPS DATA: direction, h20_rate, h10_rate, l5_avg, season_avg, vk_proj, vk_prob, vk_edge, edge_pct, cushion, cv, defense, dk_odds, blowout_risk, badges.

## CRITICAL INSTRUCTION — EDGE NUMBER CONSISTENCY
If you mention an "edge" in the summary, you MUST use the numbers provided in the PROPS DATA exactly as given. Never invent, estimate, or recompute an edge percentage.
- `edge_pct` is the single authoritative percent-edge field for the PICKED side (e.g., `edge_pct: 30.3` means "+30.3% edge"). Quote it verbatim to one decimal place when narrating a percent edge.
- `vk_edge` is in raw STAT UNITS on the PICKED side (e.g., `vk_edge: 5.9` for a rebound prop means 5.9 rebounds of projected headroom). Never append a `%` sign to `vk_edge`.
- NEVER write two different edge numbers in the same summary. Pick ONE framing (percent OR raw) and stick with it.
- If you are unsure, prefer `edge_pct` with the `%` sign, rounded to one decimal.

## ABSOLUTE RULES — MUST BE OBEYED ON EVERY PROP
These are hard constraints. Output that violates them will be rejected.

1. **BANNED OPENERS AND PHRASES.** The following phrases are forbidden anywhere in the `vision_intel_summary` (case-insensitive, including variants):
   - "the books", "books are", "books have", "the book is", "the book has"
   - "the sportsbook", "sportsbooks are", "bookies"
   - "the oddsmakers", "oddsmakers are"
   - "printing money", "screaming at us", "line is a gift", "begging us to", "disrespecting", "practically handing us"

2. **DO NOT frame the play as "the market is wrong".** You may reference the line once, factually, but the thesis of the summary must be about the PLAYER, MATCHUP, USAGE, PACE, or VOLATILITY — never about what the book did or didn't do.

3. **VARIETY.** Across the props in a single batch, you must vary the OPENING sentence structure. If prop #1 opens with a player-form angle, prop #2 should open with a matchup, pace, usage, or volatility angle. Never open two summaries in the same batch the same way.

4. **NO STOCK METAPHORS.** Do not use "metronome", "printing money", "clerical error", "feasting", "sprinting to the window", or similar overused framings more than ONCE in the whole batch.

If any summary in your output contains a banned phrase, discard it and write a new one from a different angle before returning.

IMPORTANT: Return ONLY the JSON array. No markdown, no code blocks, no extra text."""


class VisionIntelService:
    """
    Unified Gemini intelligence layer for PropVision.
    ALL AI analysis happens here - no other service should call Gemini.
    """
    
    def __init__(self):
        self.api_key = os.environ.get('GOOGLE_API_KEY')
        self.enabled = GEMINI_AVAILABLE and self.api_key is not None
        self.client = None
        self.model_name = 'gemini-3-flash-preview'
        
        if self.enabled:
            try:
                self.client = genai.Client(api_key=self.api_key)
                logger.info(f"Vision Intel Service initialized with {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini: {e}")
                self.enabled = False
        else:
            logger.warning("Vision Intel Service disabled - missing API key or library")
    
    def _build_batch_prompt(self, props: List[Dict[str, Any]], tier_name: str) -> str:
        """Build a batch prompt for analyzing multiple props at once.
        Direction-aware: each prop feeds Gemini the hit rate, probability,
        and cushion for the SIDE it was picked on (OVER or UNDER).
        """

        props_data = []
        for prop in props:
            player_name = prop.get('player_name', 'Unknown')
            stat_type = prop.get('stat_type', 'PTS')
            line = prop.get('line', 0)

            # Direction of the pick (OVER or UNDER)
            direction = (
                prop.get('direction')
                or prop.get('recommendation')
                or 'OVER'
            ).strip().upper()
            if 'UNDER' in direction:
                direction = 'UNDER'
            else:
                direction = 'OVER'
            is_under = direction == 'UNDER'

            # Core stats — ALWAYS feed Gemini the SIDE-PICKED values.
            vk_predicted = prop.get('vk_predicted', 0)
            vk_edge_raw = prop.get('vk_edge', 0) or 0
            # vk_edge is computed as (model - line) which is OVER-semantic —
            # flip sign for UNDER so "positive edge" always means the picked
            # side has room to run.
            vk_edge = -float(vk_edge_raw) if is_under else float(vk_edge_raw)

            # Side-aware ratio-to-line percent edge. Same formula the cards
            # use (`(proj-line)/line * 100`) so the paragraph and the UI
            # can never show different percent numbers on the same prop.
            try:
                edge_pct = round((vk_edge / float(line)) * 100.0, 1) if line else 0.0
            except (TypeError, ValueError, ZeroDivisionError):
                edge_pct = 0.0

            # Side-aware probability
            if is_under:
                vk_prob = prop.get('vk_prob_under')
                if vk_prob is None:
                    _ov = prop.get('vk_prob_over') or 50
                    vk_prob = max(0, 100 - float(_ov))
            else:
                vk_prob = prop.get('vk_prob_over') or 50

            # Side-aware hit rates. Prefer explicit side-keyed fields from
            # the score doc; fall back to flipping legacy h*_rate for UNDER.
            h20_over = prop.get('h20_rate', 0) or 0
            h10_over = prop.get('h10_rate', 0) or 0
            if is_under:
                h20_rate = prop.get('hit_rate_under_l20')
                if h20_rate is None:
                    h20_rate = max(0, 100 - float(h20_over)) if h20_over else 0
                h10_rate = prop.get('hit_rate_under')
                if h10_rate is None:
                    h10_rate = max(0, 100 - float(h10_over)) if h10_over else 0
            else:
                h20_rate = h20_over
                h10_rate = h10_over

            cv = prop.get('cv', 0)

            # Averages
            l5_avg = prop.get('l5_avg', 0)
            l10_avg = prop.get('l10_avg', 0)
            l20_avg = prop.get('l20_avg', 0)
            season_avg = prop.get('season_avg', l20_avg)

            # Market data
            dk_odds = prop.get('dk_odds', 'N/A')

            # Classification
            is_demon = prop.get('is_demon', False)
            is_goblin = prop.get('is_goblin', False)
            pick_type = "Demon (ceiling)" if is_demon else "Goblin (safe floor)" if is_goblin else "Standard"

            # Matchup - OPPONENT's defense against this stat type
            opponent = prop.get('opponent', 'TBD')
            dvp_rank = prop.get('dvp_rank')  # Defense vs Position — opponent's rank

            # Build DIRECTION-AWARE DvP context.
            # Rank 1-5 = opponent is ELITE at stopping this stat.
            #   For OVER: TOUGH matchup (flag).  For UNDER: SMASH spot.
            # Rank 26-30 = opponent is TERRIBLE at stopping this stat.
            #   For OVER: SMASH spot.  For UNDER: TRAP.
            if dvp_rank:
                if dvp_rank <= 5:
                    label = "ELITE D — TOUGH for OVER, SMASH for UNDER"
                elif dvp_rank <= 10:
                    label = "Strong D — challenging OVER, favorable UNDER"
                elif dvp_rank <= 20:
                    label = "Average D — neutral"
                elif dvp_rank <= 25:
                    label = "Weak D — favorable OVER, fade UNDER"
                else:
                    label = "POOR D — SMASH for OVER, TRAP for UNDER"
                dvp_text = f"{opponent} vs {stat_type} (#{dvp_rank}) — {label}"
            else:
                dvp_text = f"vs {opponent} (no DvP data)"

            # Badges/situational factors
            badges = prop.get('active_badges') or prop.get('scout_badges') or []
            badge_text = ", ".join(
                [b.get('badge_key', b) if isinstance(b, dict) else str(b) for b in badges[:3]]
            ) if badges else "None"

            # Blowout risk info
            blowout_risk = prop.get('intel_suite', {}).get('blowout_risk', {}) if isinstance(prop.get('intel_suite'), dict) else {}
            blowout_level = (blowout_risk or {}).get('risk_level', 'UNKNOWN')

            # Side-aware cushion. Positive cushion = picked side has room.
            # OVER: how far L5 avg sits ABOVE the line. UNDER: how far L5 sits BELOW.
            if l5_avg and line:
                raw_cushion = float(l5_avg) - float(line)
                cushion = round(-raw_cushion if is_under else raw_cushion, 1)
            else:
                cushion = 0

            prop_data = {
                # prop_id uses canonical_key when available — it's a globally
                # unique, string-safe identifier Gemini echoes verbatim. Falls
                # back to a name-stat-line-direction string when missing.
                "prop_id": (
                    prop.get("canonical_key")
                    or f"{player_name}_{stat_type}_{line}_{direction}"
                ),
                "player": player_name,
                "stat": stat_type,
                "line": line,
                "direction": direction,
                "type": pick_type,
                "vk_proj": round(vk_predicted, 1) if vk_predicted else 0,
                "vk_prob": round(float(vk_prob), 0) if vk_prob is not None else 50,
                "vk_edge": round(vk_edge, 1),
                "edge_pct": edge_pct,
                "cushion": cushion,  # side-aware: positive = picked side has room
                "h20_rate": round(float(h20_rate), 0) if h20_rate else 0,
                "h10_rate": round(float(h10_rate), 0) if h10_rate else 0,
                "l5_avg": round(l5_avg, 1) if l5_avg else 0,
                "season_avg": round(season_avg, 1) if season_avg else 0,
                "cv": round(cv, 2) if cv else 0,
                "opponent": opponent,
                "defense": dvp_text,
                "dk_odds": dk_odds,
                "blowout_risk": blowout_level,
                "badges": badge_text,
            }
            props_data.append(prop_data)

        prompt = f"""## {tier_name.upper()} TIER - {len(props)} Props to Analyze

These props have passed the mathematical gates. Validate each against context.
Each prop has a `direction` field (OVER or UNDER). Your analysis MUST describe the PICKED side.

PROPS DATA:
{json.dumps(props_data, indent=2)}

Return your analysis as a JSON array. One object per prop with all required fields.
`prop_id` MUST match the input exactly (includes direction suffix)."""

        return prompt
    
    async def analyze_tier_batch(
        self, 
        props: List[Dict[str, Any]], 
        tier_name: str
    ) -> List[Dict[str, Any]]:
        """
        Analyze ALL props for a tier in ONE Gemini API call.
        
        This is the main entry point - processes entire tier at once.
        Returns props enriched with all Vision Intel fields.
        """
        if not props:
            return []
        
        logger.info(f"[VISION INTEL] Batch analyzing {len(props)} props for {tier_name}")
        
        if not self.enabled or not self.client:
            logger.warning(f"[VISION INTEL] Service disabled - using fallback for {tier_name}")
            return [self._enrich_with_fallback(prop) for prop in props]
        
        try:
            # Build the batch prompt
            prompt = self._build_batch_prompt(props, tier_name)
            full_prompt = f"{VISION_INTEL_BATCH_PROMPT}\n\n{prompt}"
            
            # Debug: Log first part of prompt to verify L3 is not included
            logger.info(f"[VISION INTEL] Sending {len(props)} props to Gemini for {tier_name}")
            logger.debug(f"[VISION INTEL] Sample prop data keys: {list(props[0].keys()) if props else 'N/A'}")
            
            # Make ONE API call for the entire tier
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=full_prompt
                )
            )
            
            # Debug: Log raw response to see if Gemini is hallucinating
            logger.info(f"[VISION INTEL] Gemini response length: {len(response.text)} chars")
            
            # Parse the batch response
            intel_map = self._parse_batch_response(response.text, props)
            
            # Enrich each prop with its intel (direction-aware prop_id key)
            enriched_props = []
            for prop in props:
                _dir = (prop.get('direction') or prop.get('recommendation') or 'OVER').strip().upper()
                _dir = 'UNDER' if 'UNDER' in _dir else 'OVER'
                prop_id = (
                    prop.get("canonical_key")
                    or f"{prop.get('player_name')}_{prop.get('stat_type')}_{prop.get('line')}_{_dir}"
                )
                intel = intel_map.get(prop_id, {})
                enriched_props.append(self._merge_intel_to_prop(prop, intel))
            
            # Sort by composite score
            enriched_props.sort(key=lambda x: x.get('composite_score', 0), reverse=True)
            
            logger.info(f"[VISION INTEL] Batch complete for {tier_name}: {len(enriched_props)} props enriched")
            return enriched_props
            
        except Exception as e:
            logger.error(f"[VISION INTEL] Batch analysis failed for {tier_name}: {e}")
            return [self._enrich_with_fallback(prop) for prop in props]

    async def analyze_prop_strict(
        self,
        prop: Dict[str, Any],
        tier_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Single-prop Gemini call that returns ONLY on real Gemini output.

        Used by the ferrari UNDER JIT enricher. Returns the intel dict (with
        `vision_intel`, `intel_score`, etc.) when Gemini returned a matching
        `prop_id`, otherwise returns None so the caller can skip caching and
        avoid persisting fallback text as if it were Gemini-authored.
        """
        if not self.enabled or not self.client:
            return None
        try:
            prompt = self._build_batch_prompt([prop], tier_name)
            full_prompt = f"{VISION_INTEL_BATCH_PROMPT}\n\n{prompt}"
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=full_prompt,
                ),
            )
            intel_map = self._parse_batch_response(response.text, [prop])
            _dir = (prop.get("direction") or prop.get("recommendation") or "OVER").strip().upper()
            _dir = "UNDER" if "UNDER" in _dir else "OVER"
            prop_id = (
                prop.get("canonical_key")
                or f"{prop.get('player_name')}_{prop.get('stat_type')}_{prop.get('line')}_{_dir}"
            )
            # Return the intel only if Gemini echoed the prop_id back with real text.
            intel = intel_map.get(prop_id)
            if not intel:
                logger.warning(
                    f"[VISION INTEL STRICT] No matching prop_id in Gemini response "
                    f"(expected {prop_id!r}, got {list(intel_map.keys())!r})"
                )
                return None
            if not (intel.get("vision_intel") or "").strip():
                return None
            return intel
        except Exception as e:
            logger.error(f"[VISION INTEL STRICT] call failed: {e}")
            return None

    def _parse_batch_response(self, response: str, props: List[Dict]) -> Dict[str, Dict]:
        """Parse the batch JSON response from Gemini."""
        intel_map = {}
        
        try:
            # Clean response
            cleaned = response.strip()
            
            # Remove markdown code blocks if present
            if cleaned.startswith('```'):
                lines = cleaned.split('\n')
                lines = [l for l in lines if not l.startswith('```')]
                cleaned = '\n'.join(lines)
            
            # Find JSON array
            if '[' in cleaned:
                start = cleaned.index('[')
                end = cleaned.rindex(']') + 1
                cleaned = cleaned[start:end]
            
            results = json.loads(cleaned)
            
            if not isinstance(results, list):
                results = [results]
            
            for item in results:
                prop_id = item.get('prop_id', '')
                if prop_id:
                    intel_map[prop_id] = {
                        'intel_score': max(1, min(10, int(item.get('intel_score', 5)))),
                        'intel_verdict': item.get('verdict', 'VALUE'),
                        'vision_intel': item.get('vision_intel_summary', ''),
                        'intel_risk': item.get('risk_factor', 'Medium'),
                        'adjusted_confidence': float(item.get('adjusted_confidence', 0.5))
                    }
            
            logger.info(f"[VISION INTEL] Parsed {len(intel_map)} intel responses")
            if intel_map:
                _sample = next(iter(intel_map.keys()))
                logger.info(f"[VISION INTEL] Sample returned prop_id: {_sample!r}")
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[VISION INTEL] Failed to parse batch response: {e}")
            # Return empty map, will use fallbacks
        
        return intel_map
    
    def _merge_intel_to_prop(self, prop: Dict, intel: Dict) -> Dict:
        """Merge Vision Intel data into a prop."""
        enriched = {**prop}
        
        if intel:
            enriched['vision_intel'] = intel.get('vision_intel', '')
            enriched['intel_score'] = intel.get('intel_score', 5)
            enriched['intel_verdict'] = intel.get('intel_verdict', 'VALUE')
            enriched['intel_risk'] = intel.get('intel_risk', 'Medium')
            enriched['adjusted_confidence'] = intel.get('adjusted_confidence', 0.5)
            
            # Also set vision_summary for backward compatibility
            enriched['vision_summary'] = intel.get('vision_intel', '')
        else:
            # Use fallback
            fallback = self._generate_fallback_intel(prop)
            enriched['vision_intel'] = fallback['vision_intel']
            enriched['intel_score'] = fallback['intel_score']
            enriched['intel_verdict'] = fallback['intel_verdict']
            enriched['intel_risk'] = fallback['intel_risk']
            enriched['adjusted_confidence'] = fallback['adjusted_confidence']
            enriched['vision_summary'] = fallback['vision_intel']
        
        # =================================================================
        # INTELLIGENCE GATING: Gemini verdicts affect prop qualification
        # =================================================================
        intel_score = enriched.get('intel_score', 5)
        intel_verdict = enriched.get('intel_verdict', 'VALUE')
        adjusted_confidence = enriched.get('adjusted_confidence', 0.5)
        
        # TRAP verdict = KILL the prop (mark for removal)
        if intel_verdict == 'TRAP':
            enriched['gemini_killed'] = True
            enriched['gemini_kill_reason'] = f"TRAP verdict (intel_score: {intel_score}/10, confidence: {adjusted_confidence:.0%})"
            enriched['composite_score'] = 0  # Zero out to sort to bottom
            logger.info(f"[VISION INTEL] KILLED: {prop.get('player_name')} {prop.get('stat_type')} - TRAP verdict")
        
        # Low intel score = KILL (Gemini sees red flags)
        elif intel_score <= 3:
            enriched['gemini_killed'] = True
            enriched['gemini_kill_reason'] = f"Low intel score ({intel_score}/10) - context undermines math"
            enriched['composite_score'] = 0
            logger.info(f"[VISION INTEL] KILLED: {prop.get('player_name')} {prop.get('stat_type')} - intel score {intel_score}/10")
        
        # Low adjusted confidence = KILL
        elif adjusted_confidence < 0.45:
            enriched['gemini_killed'] = True
            enriched['gemini_kill_reason'] = f"Low adjusted confidence ({adjusted_confidence:.0%})"
            enriched['composite_score'] = 0
            logger.info(f"[VISION INTEL] KILLED: {prop.get('player_name')} {prop.get('stat_type')} - confidence {adjusted_confidence:.0%}")
        
        else:
            # PASSED Gemini gate - use adjusted_confidence as composite score
            enriched['gemini_killed'] = False
            
            # Composite Score = Gemini's adjusted_confidence (already factors in VK + context)
            # Scale to 0-100 and apply CHALK boost
            verdict_multiplier = 1.05 if intel_verdict == 'CHALK' else 1.0
            enriched['composite_score'] = round(adjusted_confidence * 100 * verdict_multiplier, 1)
            
            # Cap at 99
            if enriched['composite_score'] > 99:
                enriched['composite_score'] = 99.0
        
        return enriched
    
    def _enrich_with_fallback(self, prop: Dict) -> Dict:
        """Enrich a prop with fallback intel when Gemini is unavailable."""
        fallback = self._generate_fallback_intel(prop)
        return self._merge_intel_to_prop(prop, fallback)
    
    def _generate_fallback_intel(self, prop: Dict) -> Dict:
        """Generate fallback intel without Gemini."""
        vk_prob = prop.get('vk_prob_over') or 50
        vk_edge = prop.get('vk_edge') or 0
        h20_rate = prop.get('h20_rate') or 50
        h10_rate = prop.get('h10_rate') or 50
        
        player = prop.get('player_name', 'Player')
        stat = prop.get('stat_type', 'stat')
        line = prop.get('line') or 0
        
        # Calculate intel score based on available data
        score = 5
        if vk_prob >= 70: score += 2
        if vk_edge >= 5: score += 1
        if h20_rate >= 80: score += 1
        if h10_rate >= 90: score += 1
        if (prop.get('cv') or 1) <= 0.25: score += 1
        score = max(1, min(10, score))
        
        # Calculate adjusted confidence (0-1)
        adjusted_confidence = (vk_prob / 100 * 0.6) + (score / 10 * 0.4)
        
        # Determine verdict
        if vk_prob >= 75 and h20_rate >= 80:
            verdict = "CHALK"
            risk = "Low"
            summary = f"{player} hitting {h20_rate:.0f}% L20 on {stat}. Line at {line} is well within range. Lock it."
        elif vk_edge < 0 or h20_rate < 60:
            verdict = "TRAP"
            risk = "High"
            summary = f"Caution on {player} {stat} @ {line}. Numbers look marginal - consider passing."
            adjusted_confidence = 0.35  # Force low confidence for traps
        else:
            verdict = "VALUE"
            risk = "Medium"
            summary = f"{player} {stat} @ {line} shows value. {vk_prob:.0f}% model probability with solid recent form."
        
        return {
            'vision_intel': summary,
            'intel_score': score,
            'intel_verdict': verdict,
            'intel_risk': risk,
            'adjusted_confidence': round(adjusted_confidence, 2)
        }
    
    # Legacy method for backward compatibility
    async def analyze_tier_props(
        self, 
        props: List[Dict[str, Any]], 
        tier_name: str,
        max_concurrent: int = 3  # Ignored - we do batch
    ) -> List[Dict[str, Any]]:
        """
        Legacy method - redirects to batch analysis.
        """
        return await self.analyze_tier_batch(props, tier_name)
    
    async def analyze_prop(self, prop: Dict[str, Any], situational_intel: str = "") -> Dict[str, Any]:
        """
        Analyze a single prop (for testing or one-off analysis).
        For production, use analyze_tier_batch instead.
        """
        results = await self.analyze_tier_batch([prop], "single")
        return results[0] if results else self._enrich_with_fallback(prop)


# Singleton instance
_vision_intel_service: Optional[VisionIntelService] = None

def get_vision_intel_service() -> VisionIntelService:
    """Get or create the Vision Intel service singleton."""
    global _vision_intel_service
    if _vision_intel_service is None:
        _vision_intel_service = VisionIntelService()
    return _vision_intel_service

"""
MLB Oracle Summarizer Service
==============================
Gemini 3.1 Pro powered Inner-Circle Consultant that talks like a peer.

Uses conversational language with "we" and "us", contractions, and flowing sentences.
References April 2026 context: cold weather, ABS system, early-season pitch counts.

Structure:
- The Hook: Vibe of the play
- The Meat: Math + Vision Intel insight
- The 'But': Human risk assessment
- The Verdict: "Let's ride" or "I'm passing"
"""

import os
import json
import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Gemini API Configuration
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# System prompt for the Inner-Circle Consultant
ORACLE_SYSTEM_PROMPT = """You are my Inner-Circle Consultant for PropVision. Talk to me like a peer - like we're partners breaking down plays together.

THE PERSONA:
- Use "we" and "us" (e.g., "We've got a real edge here" or "This one makes me nervous")
- Use contractions: don't, it's, can't, we're, I'm
- Casual, direct language - no corporate speak or AI-sounding phrasing
- Sound like a sharp bettor talking to their partner, not a robot reading a report

FORMAT RULES:
- NO bullet points - just 3-4 punchy, FLOWING sentences that tell the story
- Each summary must be UNIQUE to that specific player - don't repeat the same template
- Reference the player's actual tendencies, matchup, or situation

APRIL 2026 CONTEXT (use naturally when relevant):
- Cold weather affecting bat speed and ball carry
- The new ABS (Automated Ball-Strike) system changing pitcher approaches
- Early-season pitch counts keeping starters on short leashes
- Spring fatigue and bullpen workloads
- Small sample sizes creating volatility

THE STRUCTURE:

1. THE HOOK: Start with YOUR vibe on the play
   - "I'm all over this [Player] line tonight"
   - "Listen, we need to be careful with [Player]"
   - "This is one of my favorite plays on the board"
   - "I've been going back and forth on this one"

2. THE MEAT: One thing the MATH loves + one thing the EYES see
   - "The model has him at X, and what I'm actually seeing is..."
   - "Numbers say one thing, but watch how he's been..."
   - "The edge is there mathematically, plus he's been..."

3. THE 'BUT': Address the risk like a HUMAN would
   - "The only catch is..."
   - "My one concern is..."
   - "What could bite us is..."
   - "The risk nobody's talking about is..."

4. THE VERDICT: Straight-up call
   - "Let's ride on the More/Less"
   - "I'm passing on this one"
   - "Small unit here - the edge is real but so is the variance"
   - "This is a full-send for me"

EXAMPLE OUTPUTS:

"I'm really leaning into this Skenes K-line tonight. The math has him at 8.2, but what I'm actually seeing is that he's found another gear with the sinker this week and the ABS system isn't doing the hitters any favors. My only concern is that it's a chilly night in Pittsburgh and they might pull him at 90 pitches. Still, the edge is too big to ignore. Let's ride on the More."

"Listen, we need to pump the brakes on this Judge home run play. Yeah, he's been crushing the ball - 95mph exit velo on his last 10 swings - but Coors isn't the launching pad it used to be in April with the wind blowing in at 15mph. I'm seeing a guy who's pressing at the plate and expanding the zone. The math says yes, my gut says trap. I'm passing."

"This Ohtani hits line is where I'm putting my money tonight. We've got a +18% edge and honestly, the way he's been squaring up high heat lately, I'm not surprised. The pitcher he's facing can't locate his slider to save his life and the ABS system means he has to throw strikes. Only thing that worries me is the cold snap in LA. But at this price? Full send on the More."

IMPORTANT: Each summary MUST be unique and specific to that player's situation. Don't just swap names - actually analyze differently."""


class MLBOracleSummarizer:
    """
    MLB Oracle Summarizer using Gemini 3.1 Pro.
    
    Generates tier justification summaries for qualified props.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._genai = None
        self._model = None
    
    def _initialize_genai(self) -> bool:
        """Initialize Google Generative AI client."""
        if self._genai is not None:
            return True
        
        if not GOOGLE_API_KEY:
            logger.error("[ORACLE] GOOGLE_API_KEY not configured")
            return False
        
        try:
            import google.generativeai as genai
            genai.configure(api_key=GOOGLE_API_KEY)
            self._genai = genai
            
            # Use Gemini 3.1 Pro Preview (Tier 1 Paid)
            self._model = genai.GenerativeModel(
                "gemini-3.1-pro-preview",  # Gemini 3.1 Pro Preview
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_output_tokens": 500,
                },
                system_instruction=ORACLE_SYSTEM_PROMPT
            )
            
            logger.info("[ORACLE] Gemini 3.1 Pro Preview initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"[ORACLE] Failed to initialize Gemini: {e}")
            return False
    
    def _build_prop_prompt(self, prop: Dict, tier: str) -> str:
        """Build prompt for a single prop with rich player-specific context."""
        player_name = prop.get("player_name", "Unknown")
        stat_type = prop.get("stat_type", "Unknown")
        line = prop.get("line", 0)
        vk_predicted = prop.get("vk_predicted") or prop.get("projected_value")
        edge_pct = prop.get("edge_pct", 0) or 0
        tp_odds = prop.get("tp_odds", 50) or 50
        h20_rate = prop.get("h20_rate") or prop.get("h10_rate") or 0
        h5_rate = prop.get("h5_rate") or prop.get("hit_rate_l5") or 0
        cv = prop.get("cv")
        
        # Get averages
        l5_avg = prop.get("l5_avg") or prop.get("l20_avg")
        l10_avg = prop.get("l10_avg") or prop.get("l20_avg")
        season_avg = prop.get("season_avg") or prop.get("l20_avg")
        
        # Get game logs for recent performance details
        game_logs = prop.get("game_logs", [])
        recent_games_str = ""
        if game_logs and len(game_logs) >= 3:
            last_3 = game_logs[:3]
            recent_vals = []
            for g in last_3:
                val = g.get("value") or g.get("stat_value")
                if val is not None:
                    recent_vals.append(str(val))
            if recent_vals:
                recent_games_str = f"Last 3 games: {', '.join(recent_vals)}"
        
        # Determine hot/cold streak
        streak_info = ""
        if h5_rate and h20_rate:
            if h5_rate >= h20_rate + 15:
                streak_info = "HOT STREAK - L5 hit rate 15%+ above season average"
            elif h5_rate <= h20_rate - 15:
                streak_info = "COLD STREAK - L5 hit rate 15%+ below season average"
            elif h5_rate >= 80:
                streak_info = "LOCKED IN - hitting at 80%+ over L5"
        
        # Get matchup info
        opponent = prop.get("opponent") or prop.get("opp_team") or prop.get("matchup")
        matchup_str = f"vs {opponent}" if opponent else ""
        
        # Get badges
        badges = prop.get("scout_badges", [])
        badge_names = [b.get('name', '') for b in badges if b.get('is_positive')]
        negative_badges = [b.get('name', '') for b in badges if b.get('is_negative')]
        badges_str = ", ".join(badge_names) if badge_names else "None"
        warnings_str = ", ".join(negative_badges) if negative_badges else "None"
        
        # Calculate correct edge
        edge_pct = round(h20_rate - tp_odds, 1) if h20_rate and tp_odds else 0
        
        # Tier context
        tier_vibes = {
            "safe_haven": "LOCK-tier play - we love these",
            "front_lines": "Solid VALUE play - good edge here",
            "war_zone": "HIGH VARIANCE moonshot - proceed with caution"
        }
        tier_vibe = tier_vibes.get(tier, tier)
        
        prompt = f"""Give me your Inner-Circle take on this prop. Talk like we're partners - use "we", contractions, casual language. 3-4 flowing sentences, NO bullet points.

**THE PLAY:**
- Player: {player_name} {matchup_str}
- Prop: {stat_type} OVER {line}
- Our Edge: +{edge_pct}%
- True Probability: {tp_odds}%
- L5 Hit Rate: {h5_rate}% | L20 Hit Rate: {h20_rate}%
- L5 Avg: {l5_avg} | L10 Avg: {l10_avg} | Season Avg: {season_avg}
- Consistency (CV): {cv}
- {recent_games_str}
- Trend: {streak_info if streak_info else "Steady performer"}

**WHAT WE LIKE:** {badges_str}
**WHAT CONCERNS US:** {warnings_str}
**TIER:** {tier_vibe}

IMPORTANT: Make this summary SPECIFIC to {player_name}'s actual numbers and situation. Reference their recent performance ({recent_games_str}), their averages ({l5_avg}/{season_avg}), and any streak ({streak_info}). 

Structure:
1. HOOK - Your vibe on this play
2. MEAT - The math + what you're seeing in their recent games
3. BUT - The one risk 
4. VERDICT - "Let's ride" or "I'm passing" or "Small unit"

DO NOT write generic filler. Use {player_name}'s ACTUAL stats from above."""
        
        return prompt
    
    def _build_batch_prompt(self, props: List[Dict], tier: str) -> str:
        """Build prompt for batch of props with rich player-specific context."""
        tier_context = {
            "safe_haven": "SAFE HAVEN - Our Lock-tier plays. We love these.",
            "front_lines": "FRONT LINES - Solid value plays. Good edge here.",
            "war_zone": "WAR ZONE - High variance moonshots. Tread carefully."
        }
        
        prompt = f"""I need your Inner-Circle take on these {len(props)} plays. Talk like we're partners - use "we", contractions, casual language. 3-4 flowing sentences each, NO bullet points.

**TIER: {tier_context.get(tier, tier.upper())}**

"""
        for i, prop in enumerate(props, 1):
            player_name = prop.get("player_name", "Unknown")
            stat_type = prop.get("stat_type", "Unknown")
            line = prop.get("line", 0)
            tp_odds = prop.get("tp_odds", 50) or 50
            h20_rate = prop.get("h20_rate") or prop.get("h10_rate") or 0
            h5_rate = prop.get("h5_rate") or prop.get("hit_rate_l5") or 0
            cv = prop.get("cv")
            
            # Get averages
            l5_avg = prop.get("l5_avg") or prop.get("l20_avg") or "N/A"
            l10_avg = prop.get("l10_avg") or prop.get("l20_avg") or "N/A"
            season_avg = prop.get("season_avg") or prop.get("l20_avg") or "N/A"
            
            # Get game logs for recent performance
            game_logs = prop.get("game_logs", [])
            recent_games_str = ""
            if game_logs and len(game_logs) >= 3:
                last_3 = game_logs[:3]
                recent_vals = []
                for g in last_3:
                    val = g.get("value") or g.get("stat_value")
                    if val is not None:
                        recent_vals.append(str(val))
                if recent_vals:
                    recent_games_str = f"Last 3: {', '.join(recent_vals)}"
            
            # Determine hot/cold streak
            streak_info = ""
            if h5_rate and h20_rate:
                if h5_rate >= h20_rate + 15:
                    streak_info = "HOT STREAK"
                elif h5_rate <= h20_rate - 15:
                    streak_info = "COLD"
                elif h5_rate >= 80:
                    streak_info = "LOCKED IN"
            
            # Get opponent
            opponent = prop.get("opponent") or prop.get("opp_team") or ""
            matchup_str = f"vs {opponent}" if opponent else ""
            
            # Calculate correct edge
            edge_pct = round(h20_rate - tp_odds, 1) if h20_rate and tp_odds else 0
            
            badges = prop.get("scout_badges", [])
            positive = [b.get('name', '') for b in badges if b.get('is_positive')]
            negative = [b.get('name', '') for b in badges if b.get('is_negative')]
            
            prompt += f"""---
**PROP {i}: {player_name}** {matchup_str}
- {stat_type} OVER {line}
- Edge: +{edge_pct}% | TP: {tp_odds}%
- L5 Hit: {h5_rate}% | L20 Hit: {h20_rate}% | CV: {cv}
- Avgs: L5={l5_avg}, L10={l10_avg}, Season={season_avg}
- {recent_games_str} {f'| {streak_info}' if streak_info else ''}
- Strengths: {', '.join(positive) if positive else 'None'}
- Concerns: {', '.join(negative) if negative else 'None'}

"""
        
        prompt += """---

**RESPONSE FORMAT (JSON array):**
```json
[
  {
    "prop_index": 1,
    "player_name": "...",
    "oracle_summary": "3-4 flowing sentences: Hook + Meat + But + Verdict"
  }
]
```

CRITICAL RULES:
- Each summary MUST reference that player's SPECIFIC numbers (their averages, recent games, streak status)
- Use "we", "I'm", "us" - talk like partners
- Structure: Hook (vibe) → Meat (cite their actual L5/L20 numbers) → But (risk) → Verdict
- Reference April 2026 context when relevant: cold weather, ABS system, pitch counts
- NO bullet points - flowing sentences only
- End with clear verdict: "Let's ride", "I'm passing", or "Small unit"
- DO NOT use generic filler - cite the player's real stats provided above

Provide ONLY the JSON array."""
        
        return prompt
    
    async def generate_summary(self, prop: Dict, tier: str) -> str:
        """Generate Oracle summary for a single prop."""
        if not self._initialize_genai():
            return self._generate_fallback_summary(prop, tier)
        
        try:
            prompt = self._build_prop_prompt(prop, tier)
            response = self._model.generate_content(prompt)
            summary = response.text.strip()
            
            # Clean up any markdown formatting
            summary = summary.replace("**", "").replace("*", "")
            
            return summary
            
        except Exception as e:
            logger.warning(f"[ORACLE] Gemini call failed: {e}")
            return self._generate_fallback_summary(prop, tier)
    
    async def generate_batch_summaries(
        self,
        props: List[Dict],
        tier: str,
        batch_size: int = 5
    ) -> List[Dict]:
        """
        Generate Oracle summaries for a batch of props.
        
        Args:
            props: List of props to summarize
            tier: Tier name (safe_haven, front_lines, war_zone)
            batch_size: Number of props per API call
            
        Returns:
            List of props with oracle_summary added
        """
        if not props:
            return []
        
        if not self._initialize_genai():
            logger.warning("[ORACLE] Gemini not available, using fallback summaries")
            for prop in props:
                prop["oracle_summary"] = self._generate_fallback_summary(prop, tier)
            return props
        
        logger.info(f"[ORACLE] Generating summaries for {len(props)} {tier} props...")
        
        # Process in batches
        for i in range(0, len(props), batch_size):
            batch = props[i:i + batch_size]
            
            try:
                prompt = self._build_batch_prompt(batch, tier)
                response = self._model.generate_content(prompt)
                response_text = response.text.strip()
                
                # Parse JSON response
                if "```json" in response_text:
                    json_start = response_text.find("```json") + 7
                    json_end = response_text.find("```", json_start)
                    response_text = response_text[json_start:json_end].strip()
                elif "```" in response_text:
                    json_start = response_text.find("```") + 3
                    json_end = response_text.find("```", json_start)
                    response_text = response_text[json_start:json_end].strip()
                
                summaries = json.loads(response_text)
                
                # Match summaries to props
                for j, prop in enumerate(batch):
                    summary_data = next(
                        (s for s in summaries if s.get("prop_index") == j + 1),
                        None
                    )
                    
                    if summary_data:
                        prop["oracle_summary"] = summary_data.get("oracle_summary", "")
                    else:
                        prop["oracle_summary"] = self._generate_fallback_summary(prop, tier)
                
                logger.info(f"[ORACLE] Batch {i // batch_size + 1} complete: {len(batch)} summaries")
                
                # Rate limiting
                await asyncio.sleep(0.5)
                
            except json.JSONDecodeError as e:
                logger.warning(f"[ORACLE] JSON parse error: {e}")
                # Try to extract individual summaries from raw text
                for j, prop in enumerate(batch):
                    prop["oracle_summary"] = self._generate_fallback_summary(prop, tier)
                    
            except Exception as e:
                logger.warning(f"[ORACLE] Batch failed: {e}")
                for prop in batch:
                    prop["oracle_summary"] = self._generate_fallback_summary(prop, tier)
        
        return props
    
    def _generate_fallback_summary(self, prop: Dict, tier: str) -> str:
        """Generate a fallback summary using actual player-specific data."""
        import random
        
        player_name = prop.get("player_name", "Unknown")
        stat_type = prop.get("stat_type", "Unknown")
        line = prop.get("line", 0)
        tp_odds = prop.get("tp_odds", 50) or 50
        h20_rate = prop.get("h20_rate") or prop.get("h10_rate") or 0
        h5_rate = prop.get("h5_rate") or prop.get("hit_rate_l5") or 0
        cv = prop.get("cv", 0.5) or 0.5
        
        # Get averages
        l5_avg = prop.get("l5_avg") or prop.get("l20_avg")
        season_avg = prop.get("season_avg") or prop.get("l20_avg")
        
        # Get recent games
        game_logs = prop.get("game_logs", [])
        last_3_str = ""
        if game_logs and len(game_logs) >= 3:
            vals = [str(g.get("value") or g.get("stat_value", "?")) for g in game_logs[:3]]
            last_3_str = f"{', '.join(vals)} in his last 3"
        
        # Calculate correct edge: Hit Rate - True Probability
        edge_pct = round(h20_rate - tp_odds, 1) if h20_rate and tp_odds else 0
        
        # Determine streak
        is_hot = h5_rate and h20_rate and h5_rate >= h20_rate + 10
        is_cold = h5_rate and h20_rate and h5_rate <= h20_rate - 10
        
        badges = prop.get("scout_badges", [])
        positive_badge = next((b.get("name", "") for b in badges if b.get("is_positive")), None)
        negative_badge = next((b.get("name", "") for b in badges if b.get("is_negative")), None)
        
        # Build UNIQUE hook based on actual situation
        if tier == "safe_haven":
            if is_hot:
                hook = f"I'm all over this {player_name} line - he's been on fire lately."
            elif edge_pct >= 20:
                hook = f"This {player_name} play is screaming value at +{edge_pct}% edge."
            else:
                hook = f"We've got a lock-tier play here with {player_name}."
        elif tier == "front_lines":
            if l5_avg and season_avg and l5_avg > season_avg:
                hook = f"I'm leaning into {player_name} - his L5 average of {l5_avg} is above his season norm."
            else:
                hook = f"The math on {player_name} is solid - worth the ride."
        else:  # war_zone
            if is_cold:
                hook = f"Hear me out on {player_name} - yeah he's been cold, but the upside is real."
            else:
                hook = f"This is a swing-for-the-fences play with {player_name}."
        
        # Build UNIQUE meat based on actual numbers
        if last_3_str:
            meat = f"He's put up {last_3_str} and is hitting at {h20_rate}% over his last 20 games against a line of {line}."
        elif l5_avg and season_avg:
            trend_text = "trending up" if l5_avg > season_avg else "steady"
            meat = f"His L5 average of {l5_avg} vs season of {season_avg} shows he is {trend_text}, and the {h20_rate}% hit rate gives us a +{edge_pct}% edge."
        else:
            meat = f"The model has him at {h20_rate}% hit rate against this {line} line, giving us a +{edge_pct}% edge over the books."
        
        # Build UNIQUE but based on actual concerns
        if cv >= 0.7:
            but_text = f"My concern is the {cv:.2f} CV - this guy runs hot and cold."
        elif is_cold:
            but_text = f"He's been struggling lately with only {h5_rate}% over L5, so there's some cold weather in his bat."
        elif negative_badge:
            but_text = f"The {negative_badge} flag is worth watching here."
        else:
            but_text = "The April cold could slow things down, but the edge compensates."
        
        # Build UNIQUE verdict based on tier and data
        if tier == "safe_haven":
            if edge_pct >= 20:
                verdict = "This is a full send. Let's ride on the More."
            else:
                verdict = "The edge is there. Let's ride."
        elif tier == "front_lines":
            verdict = "Worth the ride at this price."
        else:  # war_zone
            verdict = "Small unit - high variance but the ceiling is real."
        
        return f"{hook} {meat} {but_text} {verdict}"


# Singleton
_oracle: Optional[MLBOracleSummarizer] = None


def get_oracle_summarizer(db: AsyncIOMotorDatabase) -> MLBOracleSummarizer:
    """Get or create Oracle Summarizer instance."""
    global _oracle
    if _oracle is None:
        _oracle = MLBOracleSummarizer(db)
    return _oracle


async def generate_oracle_summaries(
    db: AsyncIOMotorDatabase,
    props: List[Dict],
    tier: str
) -> List[Dict]:
    """Generate Oracle summaries for props."""
    oracle = get_oracle_summarizer(db)
    return await oracle.generate_batch_summaries(props, tier)

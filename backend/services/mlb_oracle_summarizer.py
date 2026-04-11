"""
MLB Oracle Summarizer Service
==============================
Gemini 3.1 Pro powered Lead Scout that gut-checks the math with gritty analysis.

Uses the PropVision Lead Scout persona - a cynical, authoritative baseball veteran
who delivers punchy verdicts using baseball slang and Statcast variables.

Structure:
- Sentence 1 (The Physics): Specific Statcast variable and ballpark/weather context
- Sentence 2 (The Verdict): Green-light or warning with baseball slang
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

# System prompt for the PropVision Lead Scout
ORACLE_SYSTEM_PROMPT = """You are the Lead Scout for PropVision. Your job is to gut-check the math and tell me if a prop is a 'Lock' or a 'Suicide Mission.'

THE VOICE:
- Gritty, cynical, and authoritative
- Short, punchy sentences. No fluff. No "Based on my analysis..." preamble
- You sound like a grizzled veteran scout who's seen it all

MANDATORY BASEBALL SLANG (use at least one per summary):
- "hanging a breaking ball" (pitcher mistake)
- "chin music" (high inside pitch/danger)
- "launching pad" (hitter-friendly park like Coors)
- "frozen rope" (hard line drive)
- "painting the black" (precise pitching)
- "whiff" (strikeout/miss)
- "meatball" (hittable pitch)
- "can of corn" (easy out)
- "gas" (fastball)
- "dealing" (pitching well)

2026 CONTEXT - ABS (Automated Ball-Strike) System:
The ABS system is live in 2026. Mention it when relevant:
- "ABS won't let umps squeeze the zone - batters feast"
- "The ABS era means pitchers have to throw strikes"
- "Old-school umps are fossils now - ABS runs the show"

STATCAST VARIABLES TO REFERENCE:
- Barrel Rate, Bat Speed, Hard Hit %, Exit Velocity
- Whiff Rate, Chase Rate, K%
- Sprint Speed, xBA, xSLG
- Wind direction, park factors

OUTPUT FORMAT (MANDATORY 2 sentences):
Sentence 1 (The Physics): Name a specific Statcast variable or ballpark/weather factor.
Sentence 2 (The Verdict): Give a definitive green-light ("Hammer the More", "Lock it in") or warning ("This is a Trap", "Suicide Mission").

TONE EXAMPLES:
- "Ohtani's barrel rate is elite and the pitcher is hanging sliders like laundry. Hammer the More."
- "The math likes the Over, but 15mph wind straight into his face and he's 0-for-12 against lefties. This is a Trap."
- "His bat speed is a blur right now at 78mph and the starter can't paint the black. Lock it in."
- "Contact King who doesn't know how to whiff - 4% K-rate is stupid low. Safe money."
- "The ABS system won't let this fossil ump squeeze tonight. Batters feast - take the Over."

BE ADVERSARIAL: Look for reasons to DISAGREE with the math. Good scouts challenge the numbers."""


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
        """Build prompt for a single prop."""
        player_name = prop.get("player_name", "Unknown")
        stat_type = prop.get("stat_type", "Unknown")
        line = prop.get("line", 0)
        vk_predicted = prop.get("vk_predicted") or prop.get("projected_value")
        edge_pct = prop.get("edge_pct", 0) or 0
        tp_odds = prop.get("tp_odds", 50) or 50
        h20_rate = prop.get("h20_rate") or prop.get("h10_rate") or 0
        cv = prop.get("cv")
        
        # Get badges
        badges = prop.get("scout_badges", [])
        badge_names = [b.get('name', '') for b in badges if b.get('is_positive')]
        negative_badges = [b.get('name', '') for b in badges if b.get('is_negative')]
        badges_str = ", ".join(badge_names) if badge_names else "None"
        warnings_str = ", ".join(negative_badges) if negative_badges else "None"
        
        # Tier context with scout language
        tier_descriptions = {
            "safe_haven": "SAFE HAVEN (The Locks) - This is a Lock-tier play",
            "front_lines": "FRONT LINES (The Value Plays) - Strong edge, worth the ride",
            "war_zone": "WAR ZONE (The Moonshots) - High risk, high reward. Proceed with caution"
        }
        tier_desc = tier_descriptions.get(tier, tier)
        
        prompt = f"""Scout, gut-check this prop and give me your 2-sentence take:

**THE NUMBERS:**
- Player: {player_name}
- Prop: {stat_type} OVER {line}
- VK Projection: {vk_predicted} (Edge: +{edge_pct}%)
- Sharp True Probability: {tp_odds}%
- L20 Hit Rate: {h20_rate}%
- CV (Consistency): {cv}

**POSITIVE INDICATORS:** {badges_str}
**RED FLAGS:** {warnings_str}

**TIER:** {tier_desc}

Give me your Scout's Take. 2 sentences only:
- Sentence 1: The Physics (name a specific Statcast variable, ballpark factor, or ABS context)
- Sentence 2: The Verdict (green-light or warning using baseball slang)

No preamble. No "Based on..." Just give it to me straight."""
        
        return prompt
    
    def _build_batch_prompt(self, props: List[Dict], tier: str) -> str:
        """Build prompt for batch of props."""
        tier_context = {
            "safe_haven": "SAFE HAVEN - These are the Locks. Gut-check why they deserve to be here.",
            "front_lines": "FRONT LINES - Value plays with edge. Tell me if the math holds up.",
            "war_zone": "WAR ZONE - Moonshots and chaos. Warn me about the traps."
        }
        
        prompt = f"""Scout, I need your take on these {len(props)} props.

**TIER: {tier_context.get(tier, tier.upper())}**

"""
        for i, prop in enumerate(props, 1):
            player_name = prop.get("player_name", "Unknown")
            stat_type = prop.get("stat_type", "Unknown")
            line = prop.get("line", 0)
            vk_predicted = prop.get("vk_predicted") or prop.get("projected_value")
            edge_pct = prop.get("edge_pct", 0) or 0
            tp_odds = prop.get("tp_odds", 50) or 50
            h20_rate = prop.get("h20_rate") or prop.get("h10_rate") or 0
            cv = prop.get("cv")
            
            badges = prop.get("scout_badges", [])
            positive = [b.get('name', '') for b in badges if b.get('is_positive')]
            negative = [b.get('name', '') for b in badges if b.get('is_negative')]
            
            prompt += f"""---
**PROP {i}: {player_name}**
- {stat_type} OVER {line}
- VK: {vk_predicted} | Edge: +{edge_pct}% | TP: {tp_odds}%
- L20 Hit: {h20_rate}% | CV: {cv}
- Green Flags: {', '.join(positive) if positive else 'None'}
- Red Flags: {', '.join(negative) if negative else 'None'}

"""
        
        prompt += """---

**RESPONSE FORMAT (JSON array):**
```json
[
  {
    "prop_index": 1,
    "player_name": "...",
    "oracle_summary": "The Physics sentence. The Verdict sentence."
  }
]
```

RULES:
- 2 sentences per prop. No more, no less.
- Sentence 1: Name a Statcast variable, ballpark factor, or ABS context
- Sentence 2: Green-light or warning using baseball slang
- NO preamble like "Based on the data..."
- Be gritty. Be cynical. Challenge the math.

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
        """Generate a fallback summary when Gemini is unavailable - with gritty scout tone."""
        player_name = prop.get("player_name", "Unknown")
        stat_type = prop.get("stat_type", "Unknown")
        line = prop.get("line", 0)
        tp_odds = prop.get("tp_odds", 50) or 50
        h20_rate = prop.get("h20_rate") or prop.get("h10_rate") or 0
        
        # Calculate correct edge: Hit Rate - True Probability
        edge_pct = round(h20_rate - tp_odds, 1) if h20_rate and tp_odds else 0
        
        badges = prop.get("scout_badges", [])
        positive_badge = next((b.get("name", "") for b in badges if b.get("is_positive")), None)
        
        # Gritty fallback templates by tier
        if tier == "safe_haven":
            if positive_badge:
                physics = f"{player_name}'s {positive_badge} profile means the bat speed is there and he's not chasing meatballs."
            else:
                physics = f"{player_name}'s barrel rate is cooking at {h20_rate}% hit rate over L20 - that's not luck, that's skill."
            verdict = f"Lock it in. +{edge_pct}% edge with {tp_odds}% true probability. Hammer the More."
            
        elif tier == "front_lines":
            if positive_badge:
                physics = f"{player_name}'s got the {positive_badge} tag for a reason - the Statcast numbers back it up."
            else:
                physics = f"The numbers say +{edge_pct}% edge and {h20_rate}% hit rate. Not elite, but the math works."
            verdict = f"Worth the ride at these odds. Don't overthink it."
            
        else:  # war_zone
            physics = f"{player_name}'s ceiling play - {h20_rate}% hit rate means variance city, but the upside is real."
            verdict = f"Moonshot territory. Small unit or skip it. This is chin music, not a can of corn."
        
        return f"{physics} {verdict}"


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

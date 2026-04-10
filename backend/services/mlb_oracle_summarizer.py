"""
MLB Oracle Summarizer Service
==============================
Gemini 3.1 Pro powered Oracle that generates tier justification summaries.

Uses the PropVision Oracle persona to explain why each prop was placed
in its tier using sharp, betting-focused language.

Structure:
- Sentence 1 (The Math): VK Edge and Sharp TP Odds
- Sentence 2 (The Scout): MLB context using badges
- Sentence 3 (The Verdict): Definitive closing statement
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

# System prompt for the PropVision Oracle
ORACLE_SYSTEM_PROMPT = """You are the PropVision Oracle, an elite MLB quantitative analyst with decades of experience in sports betting markets.

Your role is to provide sharp, concise, betting-focused analysis. You speak with authority and precision.

RULES:
- NO fluff or filler words
- Strictly 3 sentences per prop
- Use specific numbers and percentages
- Reference the assigned badges when relevant
- Sound like a veteran quant, not a chatbot

OUTPUT STRUCTURE (MANDATORY):
Sentence 1 (The Math): State the VK Edge and Sharp TP Odds with conviction.
Sentence 2 (The Scout): Explain the MLB context using the player's badges and recent form.
Sentence 3 (The Verdict): A definitive closing statement justifying the tier placement."""


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
        badge_names = [f"{b.get('emoji', '')} {b.get('name', '')}" for b in badges]
        badges_str = ", ".join(badge_names) if badge_names else "None"
        
        # Tier context
        tier_descriptions = {
            "safe_haven": "Safe Haven (The Locks) - Elite consistency and high probability",
            "front_lines": "Front Lines (The Value Plays) - Strong edge with acceptable risk",
            "war_zone": "War Zone (The Moonshots) - High volatility ceiling plays"
        }
        tier_desc = tier_descriptions.get(tier, tier)
        
        prompt = f"""Analyze this MLB prop and provide your Oracle Summary:

**PROP DATA:**
- Player: {player_name}
- Stat: {stat_type}
- Line: {line}
- VK Projection: {vk_predicted}
- VK Edge: {edge_pct}%
- Sharp TP Odds: {tp_odds}%
- L20 Hit Rate: {h20_rate}%
- CV (Volatility): {cv}

**ASSIGNED BADGES:** {badges_str}

**TIER PLACEMENT:** {tier_desc}

Generate your 3-sentence Oracle Summary now. Remember:
- Sentence 1: The Math (VK Edge + TP Odds)
- Sentence 2: The Scout (Badges + Context)
- Sentence 3: The Verdict (Tier justification)"""
        
        return prompt
    
    def _build_batch_prompt(self, props: List[Dict], tier: str) -> str:
        """Build prompt for batch of props."""
        prompt = f"""Analyze these {len(props)} MLB props and provide Oracle Summaries for each.

TIER: {tier.upper().replace('_', ' ')}

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
            badge_names = [f"{b.get('emoji', '')} {b.get('name', '')}" for b in badges]
            badges_str = ", ".join(badge_names) if badge_names else "None"
            
            prompt += f"""---
**PROP {i}:**
- Player: {player_name}
- Stat: {stat_type} @ {line}
- VK Projection: {vk_predicted}
- VK Edge: {edge_pct}%
- Sharp TP: {tp_odds}%
- L20 Hit Rate: {h20_rate}%
- CV: {cv}
- Badges: {badges_str}

"""
        
        prompt += """---

**RESPONSE FORMAT (JSON array):**
```json
[
  {
    "prop_index": 1,
    "player_name": "...",
    "oracle_summary": "Sentence 1. Sentence 2. Sentence 3."
  }
]
```

Provide ONLY the JSON array, no additional text. Each summary must be exactly 3 sentences."""
        
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
        """Generate a fallback summary when Gemini is unavailable."""
        player_name = prop.get("player_name", "Unknown")
        stat_type = prop.get("stat_type", "Unknown")
        line = prop.get("line", 0)
        edge_pct = prop.get("edge_pct", 0) or 0
        tp_odds = prop.get("tp_odds", 50) or 50
        h20_rate = prop.get("h20_rate") or prop.get("h10_rate") or 0
        
        badges = prop.get("scout_badges", [])
        badge_str = badges[0].get("name", "") if badges else "solid metrics"
        
        tier_verdicts = {
            "safe_haven": f"This is a Lock-tier play.",
            "front_lines": f"Strong value at these odds.",
            "war_zone": f"High-upside moonshot with ceiling potential."
        }
        
        summary = (
            f"The math is clear: {edge_pct}% VK Edge with {tp_odds}% implied probability. "
            f"{player_name}'s {badge_str} profile supports {stat_type} O{line} at {h20_rate}% L20 hit rate. "
            f"{tier_verdicts.get(tier, 'Tier-appropriate risk/reward.')}"
        )
        
        return summary


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

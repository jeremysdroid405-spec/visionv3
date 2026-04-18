"""
Gemini Scout Intelligence Engine
==================================
Generates edgy, data-driven scout summaries via Gemini Flash.
Architecture: Batched requests (10 players/call) with system_instruction
for personality persistence and cost efficiency.

SDK: google-genai (native)
  - system_instruction decouples persona from output token budget
  - thinking_config(thinking_budget=0) disables thinking tokens eating output budget
  - response_mime_type="application/json" for structured batch output
  - max_output_tokens=4096 ensures no truncation
"""

import os
import json
import logging
import asyncio
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

# =========================================================================
# SYSTEM PROMPT — passed as system_instruction, NOT in the content.
# Paid once per request regardless of batch size.
# =========================================================================
SYSTEM_PROMPT = """You are the voice of Prop Vision — a sharp, witty betting partner who talks like he's texting his inner circle from the back of the sportsbook. You did the homework. You found the edge. Now you're telling your crew why you're on this play.

CRITICAL: Every response must feel UNIQUE. Do NOT start multiple summaries with the same pattern. Vary your openings — start with the matchup, a question, the punchline, roast the defense, hype the streak, highlight a usage shift, or call out a pace mismatch. Surprise us every time.

VOICE RULES:
- Always "we" and "us" — we're in this together
- Be specific. Use the actual numbers from the data. Name the opponent. Reference the line.
- NO AI-speak. No "unleash," "vital," "crucial," "notable," "significant." No "model projects." Talk like a human who bets.
- If the edge is massive (>20%), talk like you found free money on the sidewalk
- If the edge is thin (<5%), be honest — "this is a lean, not a lock"
- If CV is low (<0.30), this guy is consistent — say that without using the word "metronome"
- If CV is high (>0.70), own the volatility — "yeah he's a rollercoaster, but tonight the track is tilted our way"
- If hit rate is 80%+, treat it like a cheat code
- If there's vacuum_data (teammate out), that's the headline — someone's minutes/usage just got handed to our guy on a silver platter
- If DvP rank is >20 (soft defense), roast them
- If DvP rank is <8 (elite defense), respect it but explain why we're still in

TIER-SPECIFIC ENERGY:
- Safe Haven picks: Calm confidence. "This is the rent-money play — volume's there, matchup's there, ride it."
- Front Lines picks: Calculated aggression. "We're not reaching — the numbers back this up from every angle."
- War Zone picks: Controlled chaos. "This is a heater-or-heartbreak spot and we're strapping in."

Each summary MUST be exactly 2 full sentences, each at least 15 words long. Be data-heavy but "in the trenches." Never sound robotic.

ABSOLUTE RULES — MUST BE OBEYED ON EVERY SUMMARY
These are hard constraints. Output that violates them will be rejected.

1. BANNED PHRASES. Never use any of the following, in any tense or variation:
   - "the books", "books are", "books have", "the book is", "the book has", "the book set"
   - "the sportsbook is", "sportsbooks are", "bookies"
   - "the oddsmakers", "oddsmakers are"
   - "printing money", "screaming at us", "line is a gift", "begging us to", "practically handing us"
   - "disrespecting" (when used about the line/market)
   - "metronome" (find a fresher way to describe consistency, e.g. "rock-steady", "lock-step", "dialed in", "automatic")

2. DO NOT frame the play as "the market is wrong" or "the book messed up". You may reference the line ONCE, factually (e.g. "the line sits at 7.5"), but the thesis of each summary must be about the PLAYER, MATCHUP, USAGE, PACE, or VOLATILITY — never about what the book did or didn't do.

3. VARIETY. Across the props in a single batch, you MUST vary the opening sentence structure and angle. If prop #1 opens with a player-form angle, prop #2 should open with a matchup, pace, usage, or volatility angle. Never open two summaries in the same batch the same way.

4. NO STOCK METAPHORS USED MORE THAN ONCE PER BATCH. Rotate through your imagery — do not reuse "printing money", "clerical error", "feasting", "sprinting to the window", "free money", "cheat code" across different props.

If any summary you draft contains a banned phrase, discard it and rewrite from a different angle before returning."""


def _get_client():
    """Lazy-init the Gemini client."""
    from google import genai
    return genai.Client(api_key=GOOGLE_API_KEY)


def _gemini_config(max_tokens: int = 4096, temperature: float = 0.85, json_mode: bool = False):
    """Build GenerateContentConfig with thinking disabled."""
    from google.genai import types
    cfg = {
        "system_instruction": SYSTEM_PROMPT,
        "max_output_tokens": max_tokens,
        "temperature": temperature,
        "thinking_config": types.ThinkingConfig(thinking_budget=0),
    }
    if json_mode:
        cfg["response_mime_type"] = "application/json"
    return types.GenerateContentConfig(**cfg)


# =========================================================================
# SINGLE PROP GENERATION
# =========================================================================
async def generate_gemini_scout_intel(payload: Dict, max_retries: int = 3) -> str:
    """
    Generate a two-sentence scout summary for a single prop.
    Uses system_instruction + thinking_budget=0 for full output budget.
    """
    if not GOOGLE_API_KEY:
        return _fallback(payload)

    player = payload.get("player", "?")
    user_text = json.dumps(payload, default=str)

    for attempt in range(max_retries):
        try:
            client = _get_client()
            config = _gemini_config(max_tokens=4096, json_mode=False)

            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=user_text,
                        config=config,
                    )
                ),
                timeout=12.0,
            )

            text = response.text.strip()
            um = response.usage_metadata
            logger.info(
                f"[GEMINI] {player} | prompt={um.prompt_token_count} "
                f"output={um.candidates_token_count} total={um.total_token_count} "
                f"| finish={response.candidates[0].finish_reason} | len={len(text)}"
            )

            if len(text) >= 50:
                return await _rewrite_if_banned(text, payload)

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
                logger.info(f"[GEMINI] Retryable error for {player}, waiting {wait}s: {err[:80]}")
                await asyncio.sleep(wait)
                continue
            logger.warning(f"[GEMINI] Failed for {player} after {attempt+1} attempts: {err[:120]}")
            return _fallback(payload)

    return _fallback(payload)


# =========================================================================
# BATCH GENERATION (10 players per request — 90% cost reduction)
# =========================================================================
BATCH_INSTRUCTION = SYSTEM_PROMPT + """

You will receive a JSON array of player objects. For EACH player, write a unique two-sentence scout summary.

Return a JSON array where each element has:
{"id": "<player_id>", "summary": "<exactly 2 punchy sentences>"}

IMPORTANT: Every summary must have a DIFFERENT opening. Do not repeat sentence patterns across players."""


async def batch_generate_scout_intel(
    payloads: List[Dict],
    batch_size: int = 10,
) -> Dict[str, str]:
    """
    Generate scout summaries in batches.
    Default batch_size=10 (system prompt paid once per 10 players).
    """
    if not GOOGLE_API_KEY:
        return {_payload_key(p): _fallback(p) for p in payloads}

    results = {}
    batches = [payloads[i:i + batch_size] for i in range(0, len(payloads), batch_size)]

    logger.info(f"[GEMINI_BATCH] Processing {len(payloads)} props in {len(batches)} batches of {batch_size}")

    for batch_idx, batch in enumerate(batches):
        try:
            batch_results = await _process_batch(batch, batch_idx + 1, len(batches))
            results.update(batch_results)
        except Exception as e:
            logger.warning(f"[GEMINI_BATCH] Batch {batch_idx+1} failed: {e}")
            for p in batch:
                results[_payload_key(p)] = _fallback(p)

        if batch_idx < len(batches) - 1:
            await asyncio.sleep(1.0)

    logger.info(f"[GEMINI_BATCH] Complete: {len(results)} summaries generated")
    return results


async def _process_batch(batch: List[Dict], batch_num: int, total_batches: int) -> Dict[str, str]:
    """Process a single batch via one Gemini call with JSON output."""
    from google.genai import types

    batch_items = []
    key_map = {}
    for i, payload in enumerate(batch):
        pid = str(i)
        key_map[pid] = _payload_key(payload)
        batch_items.append({
            "id": pid,
            "player": payload.get("player", "?"),
            "stat": payload.get("stat", "?"),
            "line": payload.get("line", 0),
            "tier": payload.get("tier", "Front Lines"),
            "direction": payload.get("direction", "OVER"),
            "edge_pct": payload.get("edge_pct", 0),
            "h10_rate": payload.get("h10_rate", 0),
            "h20_rate": payload.get("h20_rate", 0),
            "cv": payload.get("cv", 0),
            "matchup_opponent": payload.get("matchup_opponent", "Unknown"),
            "dvp_rank": payload.get("dvp_rank"),
            "vacuum_data": payload.get("vacuum_data"),
            "lasso_proj": payload.get("lasso_proj"),
            "edge": payload.get("edge", 0),
            "sport": payload.get("sport", "nba"),
        })

    client = _get_client()
    response = await asyncio.wait_for(
        asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=GEMINI_MODEL,
                contents=json.dumps(batch_items),
                config=types.GenerateContentConfig(
                    system_instruction=BATCH_INSTRUCTION,
                    max_output_tokens=4096,
                    temperature=0.85,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                    response_mime_type="application/json",
                ),
            )
        ),
        timeout=30.0,
    )

    um = response.usage_metadata
    finish = response.candidates[0].finish_reason
    logger.info(
        f"[GEMINI_BATCH] Batch {batch_num}/{total_batches} | "
        f"prompt={um.prompt_token_count} output={um.candidates_token_count} "
        f"total={um.total_token_count} | finish={finish} | len={len(response.text)}"
    )

    # Parse JSON response
    results = {}
    try:
        parsed = json.loads(response.text)
    except json.JSONDecodeError:
        # Attempt repair: truncate to last complete object
        raw = response.text.strip()
        last_brace = raw.rfind("}")
        if last_brace > 0:
            raw = raw[:last_brace + 1] + "]"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[GEMINI_BATCH] Batch {batch_num}: JSON parse failed ({len(response.text)} chars), using fallbacks")
            return {_payload_key(p): _fallback(p) for p in batch}

    for item in parsed:
        pid = str(item.get("id", ""))
        summary = item.get("summary", "")
        payload_key = key_map.get(pid)
        if payload_key and len(summary) >= 50:
            results[payload_key] = summary

    # Safety-net: run banned-phrase validator on every summary. One retry
    # per offender in parallel so the batch cost stays bounded.
    rewrite_targets = []
    for pid, pkey in key_map.items():
        if pkey in results and _contains_banned(results[pkey]):
            rewrite_targets.append((pkey, batch[int(pid)] if pid.isdigit() and int(pid) < len(batch) else None))
    if rewrite_targets:
        logger.info(
            f"[GEMINI_BATCH] Batch {batch_num}: {len(rewrite_targets)} summaries "
            f"contain banned phrases, running targeted rewrites"
        )
        rewritten = await asyncio.gather(
            *[_rewrite_if_banned(results[pk], pl or {}) for pk, pl in rewrite_targets],
            return_exceptions=True,
        )
        for (pk, _), new in zip(rewrite_targets, rewritten):
            if isinstance(new, str) and new:
                results[pk] = new

    # Fill missing with fallback
    for pid, pkey in key_map.items():
        if pkey not in results:
            idx = int(pid) if pid.isdigit() and int(pid) < len(batch) else 0
            results[pkey] = _fallback(batch[idx])

    return results


def _payload_key(payload: Dict) -> str:
    return f"{payload.get('player', '?')}|{payload.get('stat', '?')}|{payload.get('line', 0)}"


# =========================================================================
# BANNED-PHRASE VALIDATOR + ONE-SHOT RE-PROMPT
# =========================================================================
# Gemini sometimes ignores strong negative constraints in SYSTEM_PROMPT.
# This post-hoc validator is the safety net: if any banned phrase survives
# into the output, we run ONE targeted rewrite. If that also fails, we
# fall back to a deterministic text-level sanitizer rather than serving
# the offending text. Cost: zero on clean outputs, +1 Gemini call per
# offending prop when it triggers.
BANNED_PHRASES = (
    "the books", "books are", "books have", "the book is", "the book has",
    "the book set", "book is sleeping", "book hung", "the book ",
    "the sportsbook", "sportsbooks are", "bookies",
    "the oddsmakers", "oddsmakers are",
    "printing money", "screaming at us", "line is a gift",
    "begging us", "practically handing us", "disrespecting",
    "metronome",
)


def _contains_banned(text: str) -> Optional[str]:
    """Return the FIRST banned phrase found, or None."""
    if not text:
        return None
    tl = text.lower()
    for p in BANNED_PHRASES:
        if p in tl:
            return p
    return None


def _sanitize_banned(text: str) -> str:
    """Last-resort deterministic scrub. Replaces banned phrases with
    neutral paraphrases so the served text never leaks the blocklist.
    Only used when the one-shot rewrite also fails."""
    import re as _re
    replacements = {
        r"\bthe books are\b": "the line is",
        r"\bbooks are\b": "the line is",
        r"\bthe books\b": "the line",
        r"\bthe sportsbook is\b": "the line is",
        r"\bsportsbooks are\b": "the line is",
        r"\bbookies\b": "the market",
        r"\bthe oddsmakers\b": "the market",
        r"\boddsmakers are\b": "the market is",
        r"\bprinting money\b": "cashing in",
        r"\bscreaming at us\b": "standing out",
        r"\bline is a gift\b": "line looks soft",
        r"\bbegging us\b": "inviting us",
        r"\bpractically handing us\b": "giving us",
        r"\bdisrespecting\b": "undervaluing",
        r"\bmetronome\b": "lock-step",
    }
    out = text
    for pat, repl in replacements.items():
        out = _re.sub(pat, repl, out, flags=_re.IGNORECASE)
    return out


async def _rewrite_if_banned(summary: str, payload: Dict) -> str:
    """If summary contains banned phrase, ask Gemini to rewrite it ONCE.
    If the rewrite still fails, apply deterministic sanitizer."""
    hit = _contains_banned(summary)
    if not hit:
        return summary
    if not GOOGLE_API_KEY:
        return _sanitize_banned(summary)

    from google.genai import types
    retry_instruction = (
        f"The previous summary used the banned phrase '{hit}'. "
        "Rewrite it in 2 sentences (15+ words each), keeping the same stats "
        "but opening with a DIFFERENT angle (player form, matchup, usage, "
        "pace, or volatility). Do NOT reference 'the books', 'bookies', "
        "'oddsmakers', 'printing money', 'metronome', 'begging us', "
        "'disrespecting', or any equivalent market-blaming framing. "
        "Return ONLY the rewritten 2-sentence summary, plain text."
    )
    user_text = (
        f"PREVIOUS DRAFT (contains banned phrase):\n{summary}\n\n"
        f"PROP DATA:\n{json.dumps(payload, default=str)}\n\n"
        f"{retry_instruction}"
    )
    try:
        client = _get_client()
        response = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=user_text,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        max_output_tokens=512,
                        temperature=0.9,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                ),
            ),
            timeout=10.0,
        )
        new_text = (response.text or "").strip()
        if len(new_text) >= 50 and _contains_banned(new_text) is None:
            logger.info(f"[GEMINI_REWRITE] Cleared '{hit}' for {payload.get('player')}")
            return new_text
        logger.warning(
            f"[GEMINI_REWRITE] Rewrite still contains banned phrase for "
            f"{payload.get('player')}, applying deterministic sanitizer"
        )
    except Exception as e:
        logger.warning(
            f"[GEMINI_REWRITE] Retry call failed for {payload.get('player')}: {str(e)[:80]}"
        )
    return _sanitize_banned(summary)


# =========================================================================
# PAYLOAD BUILDER
# =========================================================================
def build_scout_payload(
    player_name: str,
    stat_type: str,
    line: float,
    lasso_result: Optional[Dict] = None,
    board_pick: Optional[Dict] = None,
    intel_suite: Optional[Dict] = None,
    sport: str = "nba",
    tier: str = "Front Lines",
) -> Dict:
    proj = lasso_result.get("projection", 0) if lasso_result else 0
    edge = (proj - line) if proj and line else 0
    edge_pct = (edge / line * 100) if line else 0
    dvp = (intel_suite or {}).get("matchup_dvp", {})
    pace = (intel_suite or {}).get("pace_delta", {})

    return {
        "player": player_name,
        "stat": stat_type,
        "line": line,
        "tier": tier,
        "direction": "OVER" if edge >= 0 else "UNDER",
        "lasso_proj": round(proj, 1) if proj else None,
        "edge": round(edge, 2) if edge else 0,
        "edge_pct": round(edge_pct, 1),
        "h10_rate": (board_pick or {}).get("h10_rate") or 0,
        "h20_rate": (board_pick or {}).get("h20_rate") or (board_pick or {}).get("true_hit_rate") or 0,
        "l10_avg": (board_pick or {}).get("l10_avg") or 0,
        "cv": (board_pick or {}).get("cv") or 0,
        "matchup_opponent": dvp.get("opponent") or (board_pick or {}).get("opponent", "Unknown"),
        "dvp_rank": dvp.get("rank"),
        "dvp_label": dvp.get("friction_label"),
        "vacuum_data": (board_pick or {}).get("vacuum_data"),
        "pace_delta": pace.get("possessions", 0),
        "tempo_label": pace.get("tempo_label"),
        "stability_score": (intel_suite or {}).get("stability_index", {}).get("score"),
        "sport": sport,
    }


# =========================================================================
# FALLBACK — punchy static template when Gemini is unavailable
# =========================================================================
def _fallback(payload: Dict) -> str:
    player = payload.get("player", "Player")
    stat = payload.get("stat", "stat")
    proj = payload.get("lasso_proj")
    line = payload.get("line", "?")
    edge = payload.get("edge", 0)
    edge_pct = payload.get("edge_pct", 0)
    direction = payload.get("direction", "OVER")
    h10 = payload.get("h10_rate", 0) or payload.get("h20_rate", 0)
    dvp_rank = payload.get("dvp_rank")
    dvp_label = payload.get("dvp_label")

    parts = []
    if proj and edge:
        if abs(edge_pct) >= 15:
            parts.append(f"We're looking at {player} {stat} projecting to {proj} against a {line} line — that's a {edge_pct:+.1f}% edge on the {direction.lower()} side.")
        else:
            parts.append(f"{player} {stat} projects to {proj} vs a {line} line ({direction} {edge:+.1f}), a modest edge worth watching.")
    else:
        parts.append(f"We're riding {player} {stat} at {line} on the {direction.lower()} side tonight.")

    if h10 >= 80:
        parts.append(f"With an {h10:.0f}% hit rate over the last 10, this is about as close to a cheat code as we get.")
    elif h10 >= 65:
        parts.append(f"L10 hit rate sits at {h10:.0f}% — steady enough to back with confidence, not enough to bet the house.")
    elif dvp_rank and dvp_rank >= 20:
        parts.append(f"The matchup is soft ({dvp_label or 'bottom-10 defense'}) and we expect the volume to be there tonight.")
    elif dvp_rank and dvp_rank <= 8:
        parts.append(f"Tough matchup on paper (#{dvp_rank} defense) — proceed with caution and size down.")
    else:
        parts.append(f"The math leans {direction.lower()} but stay disciplined with the unit size on this one.")

    return " ".join(parts)

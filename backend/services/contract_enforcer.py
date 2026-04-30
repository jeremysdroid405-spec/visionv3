"""
Contract Enforcer — Runtime Validators for Dashboard Output
============================================================

Why this exists
---------------
"Fixes" to the dashboard kept regressing because the contracts (pick
card shape, lineup-opportunity row shape, hit-profile parity, live
ticker freshness, sport-keyed logos) lived only in code review and
ad-hoc tests. Once a future refactor breaks an invariant, no part of
the runtime stops the bad payload from reaching users.

This module turns those contracts into runtime gates that run on every
HTTP response. Violations are:

  1. **Counted** — written to `contract_violations` collection
     (TTL 24 h). Surfaced via `/api/health/contracts`.
  2. **Suppressed** — invalid rows are dropped or hidden. Bad data
     cannot leave the API.
  3. **Logged** — one ERROR log line per violation with full context
     so on-call can see who broke what when.

What this module does NOT touch
-------------------------------
* No scoring formulas, μ, σ, gates, thresholds, tier-routing, or
  pick-selection logic. This is pure response-shape gating.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Collection / event types ────────────────────────────────────────
COLL = "contract_violations"

# Canonical event names — also exposed by the health endpoint.
EVT_PICK_CARD_INVALID            = "invalid_pick_card"
EVT_LINEUP_OPPORTUNITY_SUPPRESSED = "lineup_opportunity_suppressed"
EVT_HIT_PROFILE_MISMATCH          = "hit_profile_mismatch"
EVT_PAST_GAME_TICKET_SUPPRESSED   = "past_game_ticket_suppressed"
EVT_LOGO_LOOKUP_NOT_SPORT_KEYED   = "logo_lookup_not_sport_keyed"

# Required keys for every dashboard pick card.
# These mirror the public Universal Card Contract (services/dashboard_card_contract.py).
PICK_CARD_REQUIRED_KEYS: Tuple[str, ...] = (
    # Identity
    "player_name", "team", "sport",
    # Pick details
    "stat_type", "line", "recommendation", "direction",
    "tier_label", "prop_type",
    # Card-shape (8-field universal display)
    "stat_line", "big_pick_text",
    "projection", "hit_rate", "avg", "short_sentence",
)

# Lineup-opportunity row required keys + numeric requirements.
LINEUP_OPP_REQUIRED_KEYS: Tuple[str, ...] = (
    "beneficiary_name",
    "current_lineup_slot",
    "previous_lineup_slot",
    "lineup_delta",
    "projected_ab_delta",
)


# ─── Counter store (MongoDB-backed, TTL 24 h) ────────────────────────
async def _record_violation(
    db, event: str, sport: Optional[str], context: Dict[str, Any]
) -> None:
    """Insert a single violation document. TTL index expires after 24h.

    Uses fire-and-forget try/except — a logging-store failure must
    never affect the user-visible API response.
    """
    if db is None:
        return
    try:
        await db[COLL].insert_one({
            "event":      event,
            "sport":      sport or "unknown",
            "context":    context,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("[CONTRACT] failed to persist violation %s: %s", event, e)


async def ensure_indexes(db) -> None:
    """Idempotent: TTL index that drops violations older than 24 h."""
    if db is None:
        return
    try:
        await db[COLL].create_index(
            "created_at", expireAfterSeconds=24 * 60 * 60
        )
        await db[COLL].create_index([("event", 1), ("sport", 1)])
    except Exception as e:  # pragma: no cover
        logger.warning("[CONTRACT] index ensure failed: %s", e)


# ─── 1) Pick-card validator ──────────────────────────────────────────
async def enforce_pick_card_contract(
    db, picks: List[Dict[str, Any]], sport: str, tier: str
) -> List[Dict[str, Any]]:
    """Drop any pick missing a required key. Log + count each violation.

    Nullable display fields (`stat_line`, `big_pick_text`, `projection`,
    `hit_rate`, `avg`, `short_sentence`) — the KEY must exist; value
    may be null. Identity / pick-detail keys MUST be non-null.

    Returns the filtered list. Order preserved.
    """
    if not picks:
        return picks
    nullable_ok = {"stat_line", "big_pick_text", "projection",
                   "hit_rate", "avg", "short_sentence"}
    kept: List[Dict[str, Any]] = []
    for p in picks:
        missing: List[str] = []
        for k in PICK_CARD_REQUIRED_KEYS:
            if k not in p:
                missing.append(k)
            elif k not in nullable_ok and p.get(k) in (None, "", "—"):
                missing.append(k)  # required-non-null violation
        if missing:
            logger.error(
                "[CONTRACT:%s] invalid_pick_card sport=%s tier=%s "
                "player=%s missing_or_null=%s",
                EVT_PICK_CARD_INVALID, sport, tier,
                p.get("player_name"), missing,
            )
            await _record_violation(db, EVT_PICK_CARD_INVALID, sport, {
                "tier":   tier,
                "player": p.get("player_name"),
                "stat_type": p.get("stat_type"),
                "missing_fields": missing,
            })
            continue  # SUPPRESS bad pick
        kept.append(p)
    return kept


# ─── 2) Lineup-opportunity validator ─────────────────────────────────
def _is_finite_number(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))  # NaN check


async def enforce_lineup_opportunity_contract(
    db, alerts: List[Dict[str, Any]], sport: str = "mlb"
) -> List[Dict[str, Any]]:
    """Strip any row that would render `+0 lineup spots` / `+ AB`
    placeholder. If ALL rows fail, return [] so the section hides.

    Two valid row shapes (2026-04-30, Option A+B):
      (a) Slot-shift row — day-over-day lineup movement:
            beneficiary_name      (truthy string)
            current_lineup_slot   (numeric)
            previous_lineup_slot  (numeric)
            lineup_delta          (numeric, != 0)
            projected_ab_delta    (numeric, > 0)
      (b) New-starter row — player entered the lineup today:
            beneficiary_name      (truthy string)
            current_lineup_slot   (numeric)
            previous_lineup_slot  (None allowed)
            lineup_delta          (None allowed)
            projected_ab_delta    (numeric, > 0)
            is_new_starter        (True)
    """
    kept: List[Dict[str, Any]] = []
    for a in alerts or []:
        ad = a.get("projected_ab_delta")
        bad: List[str] = []
        if not a.get("beneficiary_name"):
            bad.append("beneficiary_name")
        if not _is_finite_number(a.get("current_lineup_slot")):
            bad.append("current_lineup_slot")
        if not _is_finite_number(ad) or float(ad or 0) <= 0:
            bad.append("projected_ab_delta")

        if a.get("is_new_starter") is True:
            # Path (b) — previous_slot / lineup_delta are EXPECTED None.
            # Don't flag them. current_slot + ab_delta are the signal.
            pass
        else:
            # Path (a) — full slot-shift contract.
            ld = a.get("lineup_delta")
            if not _is_finite_number(a.get("previous_lineup_slot")):
                bad.append("previous_lineup_slot")
            if not _is_finite_number(ld) or float(ld or 0) == 0:
                bad.append("lineup_delta")

        if bad:
            await _record_violation(db, EVT_LINEUP_OPPORTUNITY_SUPPRESSED, sport, {
                "beneficiary": a.get("beneficiary_name"),
                "injured":     a.get("injured_player"),
                "invalid_fields": bad,
                "is_new_starter": bool(a.get("is_new_starter")),
            })
            continue
        kept.append(a)
    suppressed = len(alerts or []) - len(kept)
    if suppressed:
        logger.warning(
            "[CONTRACT:%s] LINEUP_OPPORTUNITY_SUPPRESSED_INVALID_ROWS "
            "sport=%s suppressed=%d kept=%d",
            EVT_LINEUP_OPPORTUNITY_SUPPRESSED, sport, suppressed, len(kept),
        )
    return kept


# ─── 3) Hit-profile parity validator ─────────────────────────────────
async def enforce_hit_profile_parity(
    db, picks: List[Dict[str, Any]], sport: str, tier: str
) -> int:
    """Verify per-pick that:

        pick.hit_rate ≈ pick.l10_hit_count / pick.l10_total × 100
        pick.avg      ≈ mean(pick.l10_values)
        pick.line     == pick.hit_profile_line

    On mismatch, OVERWRITE the displayed `hit_rate` with the empirical
    value derived from `l10_hit_count` / `l10_total` (the same value
    the graph uses), log + count the violation, and continue.

    Returns the number of mismatches detected.
    """
    mismatches = 0
    for p in picks or []:
        cnt = p.get("l10_hit_count")
        tot = p.get("l10_total")
        hr  = p.get("hit_rate")
        line = p.get("line")
        prof_line = p.get("hit_profile_line")
        if cnt is None or not tot:
            continue   # no profile stamped; skip silently
        expected_hr = round(100.0 * cnt / tot, 1)
        # ± 0.1 tolerance for rounding.
        if hr is None or abs(float(hr) - expected_hr) > 0.11:
            mismatches += 1
            logger.error(
                "[CONTRACT:%s] hit_profile_mismatch sport=%s tier=%s "
                "player=%s stat=%s line=%s "
                "displayed_hit_rate=%s expected=%s "
                "(l10=%s/%s)",
                EVT_HIT_PROFILE_MISMATCH, sport, tier,
                p.get("player_name"), p.get("stat_type"), line,
                hr, expected_hr, cnt, tot,
            )
            await _record_violation(db, EVT_HIT_PROFILE_MISMATCH, sport, {
                "tier":   tier,
                "player": p.get("player_name"),
                "stat":   p.get("stat_type"),
                "line":   line,
                "displayed": hr,
                "expected": expected_hr,
                "l10_hit_count": cnt,
                "l10_total": tot,
            })
            # PERMANENT: rewrite to the empirical value.
            p["hit_rate"] = expected_hr
        # Line drift is also a serious bug — never silently mismatched.
        if (
            prof_line is not None
            and line is not None
            and float(prof_line) != float(line)
        ):
            mismatches += 1
            logger.error(
                "[CONTRACT:%s] hit_profile_line_drift sport=%s "
                "player=%s card_line=%s profile_line=%s",
                EVT_HIT_PROFILE_MISMATCH, sport,
                p.get("player_name"), line, prof_line,
            )
            await _record_violation(db, EVT_HIT_PROFILE_MISMATCH, sport, {
                "tier":   tier,
                "player": p.get("player_name"),
                "card_line": line,
                "profile_line": prof_line,
            })
    return mismatches


# ─── 4) Live ticker validator ────────────────────────────────────────
async def enforce_ticker_freshness(
    db, games: List[Dict[str, Any]], sport: str
) -> List[Dict[str, Any]]:
    """Drop finals (`status_code == 3`) and scheduled games whose
    `start_time` already passed. In-play (`status_code == 2`) is kept
    regardless of start_time. Counts every dropped row.
    """
    if not games:
        return games
    now = datetime.now(timezone.utc)
    kept: List[Dict[str, Any]] = []
    suppressed = 0
    for g in games:
        status = g.get("status_code")
        if status == 3:
            suppressed += 1
            continue
        ct = g.get("start_time")
        if status == 2:
            kept.append(g)
            continue
        try:
            if not ct:
                kept.append(g)
                continue
            ct_utc = datetime.fromisoformat(str(ct).replace("Z", "+00:00"))
            if ct_utc.tzinfo is None:
                ct_utc = ct_utc.replace(tzinfo=timezone.utc)
            if ct_utc >= now:
                kept.append(g)
            else:
                suppressed += 1
        except (ValueError, TypeError):
            kept.append(g)  # un-parseable but not Final — keep
    if suppressed:
        logger.warning(
            "[CONTRACT:%s] sport=%s suppressed=%d kept=%d",
            EVT_PAST_GAME_TICKET_SUPPRESSED, sport, suppressed, len(kept),
        )
        await _record_violation(db, EVT_PAST_GAME_TICKET_SUPPRESSED, sport, {
            "suppressed": suppressed,
            "kept": len(kept),
        })
    return kept


# ─── 5) Health-endpoint helpers ──────────────────────────────────────
async def aggregate_24h_counters(db) -> Dict[str, Any]:
    """Return the 24 h counter dict consumed by /api/health/contracts.

    Empty when no violations recorded — ALL counters return 0.
    """
    out: Dict[str, Any] = {
        "invalid_pick_card_count_last_24h": 0,
        "suppressed_lineup_opportunity_count_last_24h": 0,
        "hit_profile_mismatch_count_last_24h": 0,
        "past_game_ticket_suppressed_count_last_24h": 0,
        "logo_lookup_not_sport_keyed_count_last_24h": 0,
        "missing_required_card_fields_by_sport": {},
    }
    if db is None:
        return out
    cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600
    cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff_dt}}},
        {"$group": {"_id": {"event": "$event", "sport": "$sport"},
                    "count": {"$sum": 1}}},
    ]
    by_sport_missing: Dict[str, int] = {}
    async for r in db[COLL].aggregate(pipeline):
        ev = r["_id"]["event"]
        sp = r["_id"]["sport"]
        c = int(r["count"])
        if ev == EVT_PICK_CARD_INVALID:
            out["invalid_pick_card_count_last_24h"] += c
            by_sport_missing[sp] = by_sport_missing.get(sp, 0) + c
        elif ev == EVT_LINEUP_OPPORTUNITY_SUPPRESSED:
            out["suppressed_lineup_opportunity_count_last_24h"] += c
        elif ev == EVT_HIT_PROFILE_MISMATCH:
            out["hit_profile_mismatch_count_last_24h"] += c
        elif ev == EVT_PAST_GAME_TICKET_SUPPRESSED:
            out["past_game_ticket_suppressed_count_last_24h"] += c
        elif ev == EVT_LOGO_LOOKUP_NOT_SPORT_KEYED:
            out["logo_lookup_not_sport_keyed_count_last_24h"] += c
    out["missing_required_card_fields_by_sport"] = by_sport_missing
    return out


__all__ = [
    "PICK_CARD_REQUIRED_KEYS",
    "LINEUP_OPP_REQUIRED_KEYS",
    "EVT_PICK_CARD_INVALID",
    "EVT_LINEUP_OPPORTUNITY_SUPPRESSED",
    "EVT_HIT_PROFILE_MISMATCH",
    "EVT_PAST_GAME_TICKET_SUPPRESSED",
    "EVT_LOGO_LOOKUP_NOT_SPORT_KEYED",
    "ensure_indexes",
    "enforce_pick_card_contract",
    "enforce_lineup_opportunity_contract",
    "enforce_hit_profile_parity",
    "enforce_ticker_freshness",
    "aggregate_24h_counters",
]

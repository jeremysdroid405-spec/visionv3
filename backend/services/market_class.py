"""Market-class SSOT (2026-05-17 odds-pipeline hardening).

A SINGLE place that classifies an odds-API ``market_key`` into one of:

    "standard"   — primary book market (`batter_total_bases`, `h2h`, …)
    "alternate"  — alt-line ladder (`batter_total_bases_alternate`, …)
    "sgp"        — same-game-parlay / leg-priced market (placeholder —
                   the odds-API doesn't emit a public flag for these
                   today, so we only ever tag them when an explicit
                   feed metadata hint is present)
    "promo"      — promotional / boost market (placeholder — same
                   provenance constraint as ``sgp``)
    "unknown"    — explicit fallback when ``market_key`` is missing
                   or unparseable

Why is this its OWN module?
---------------------------
Every layer of the pipeline (raw snapshot writer, canonical-key
builder, live-props join, score-doc writer, audit endpoints, tests)
needs to agree on the classification. Centralising it here lets us
add new rules in one place and keeps the classifier strictly
side-effect free.

The companion ``build_canonical_v2`` constructor extends the legacy
canonical_key with a trailing ``|<market_class>`` segment so the
augmented identity is structurally unable to collide across market
classes. The legacy ``canonical_key`` is preserved verbatim so
existing joins are not invalidated.
"""
from __future__ import annotations

from typing import Optional

# ── Public allowed values ────────────────────────────────────────
ALLOWED_MARKET_CLASSES = ("standard", "alternate", "sgp", "promo", "unknown")


def classify_market_key(market_key: Optional[str]) -> str:
    """Pure classifier — no DB / no I/O.

    Rules
    -----
    * ``None`` or empty → ``"unknown"``
    * Contains ``"_alternate"`` or ends with ``"_alt"`` → ``"alternate"``
    * Contains ``"_sgp"`` token → ``"sgp"`` (defensive — odds-API does
      not currently emit this but a vendor or replay backfill might)
    * Contains ``"_promo"`` token → ``"promo"`` (same caveat as sgp)
    * Otherwise → ``"standard"``

    Returns
    -------
    One of :data:`ALLOWED_MARKET_CLASSES`.
    """
    if not market_key or not isinstance(market_key, str):
        return "unknown"
    mk = market_key.lower()
    if "_alternate" in mk or mk.endswith("_alt"):
        return "alternate"
    if "_sgp" in mk:
        return "sgp"
    if "_promo" in mk:
        return "promo"
    return "standard"


def is_alternate(market_key: Optional[str]) -> bool:
    """Convenience boolean — equivalent to
    ``classify_market_key(...) == "alternate"``.

    Mirrors the legacy ``"alternate" in market_key`` test used in
    several call sites, but routes through the SSOT so future
    classification refinements (e.g. ``_sgp_alternate``) stay coherent.
    """
    return classify_market_key(market_key) == "alternate"


def build_canonical_v2(canonical_key: str, market_class: str) -> str:
    """Append the market_class segment to a legacy canonical key.

    Legacy form:   ``"mlb|<event>|<player>|<stat>|<line>|<side>"``
    v2 form:       ``"mlb|<event>|<player>|<stat>|<line>|<side>|<class>"``

    The trailing class is forced into the allowed set; anything
    unexpected collapses to ``"unknown"`` so the augmented key is
    always well-formed. We do NOT mutate the legacy key in place —
    callers persist both forms.
    """
    mc = market_class if market_class in ALLOWED_MARKET_CLASSES else "unknown"
    if not canonical_key:
        return canonical_key
    # Idempotent: if the legacy key already carries a class suffix
    # (e.g. an over-eager caller passed canonical_key_v2 in) leave it
    # alone rather than double-stamp.
    parts = canonical_key.split("|")
    if len(parts) >= 7 and parts[-1] in ALLOWED_MARKET_CLASSES:
        return canonical_key
    return f"{canonical_key}|{mc}"

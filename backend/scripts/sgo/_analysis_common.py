"""
Shared helpers for the SGO analysis suite (sgo_*_analysis.py / sgo_*_coverage.py
/ sgo_*_summary.py).

Read-only. Imports nothing from `scripts.sgo.client` or `.ingest` so it can be
audited in isolation.
"""
from __future__ import annotations
import os
from typing import Any, Optional

# ─── Output directory ──────────────────────────────────────────────────────
# Resolves to <backend>/audits/sgo_analysis/ on both preview and production
# regardless of which user runs the script.
_HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_DIR = os.path.normpath(os.path.join(_HERE, "..", "..",
                                            "audits", "sgo_analysis"))


def ensure_audit_dir() -> str:
    os.makedirs(AUDIT_DIR, exist_ok=True)
    return AUDIT_DIR


# ─── Odds buckets (deep-chalk → longshot, 19 buckets) ──────────────────────
ODDS_BUCKETS = [
    ("[-inf,-5000]",  -10**12, -5000),
    ("[-4999,-3000]", -4999,   -3000),
    ("[-2999,-2000]", -2999,   -2000),
    ("[-1999,-1500]", -1999,   -1500),
    ("[-1499,-1000]", -1499,   -1000),
    ("[-999,-800]",    -999,    -800),
    ("[-799,-600]",    -799,    -600),
    ("[-599,-500]",    -599,    -500),
    ("[-499,-400]",    -499,    -400),
    ("[-399,-300]",    -399,    -300),
    ("[-299,-200]",    -299,    -200),
    ("[-199,-150]",    -199,    -150),
    ("[-149,-110]",    -149,    -110),
    ("[-109,+100]",    -109,     100),
    ("[+101,+200]",     101,     200),
    ("[+201,+500]",     201,     500),
    ("[+501,+1000]",    501,    1000),
    ("[+1001,+2000]",  1001,    2000),
    ("[+2001,+inf]",   2001,   10**12),
]
BUCKET_LABELS = [b[0] for b in ODDS_BUCKETS]


def bucket_for(price: Optional[float]) -> str:
    if price is None:
        return "unknown"
    try:
        p = int(price)
    except (TypeError, ValueError):
        return "unknown"
    for label, lo, hi in ODDS_BUCKETS:
        if lo <= p <= hi:
            return label
    return "unknown"


# ─── American → implied probability + de-vig helpers ───────────────────────
def implied_prob(american: Optional[float]) -> Optional[float]:
    if american is None:
        return None
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    if a > 0:
        return 100.0 / (a + 100.0)
    return -a / (-a + 100.0)


def devig_two_way(p_yes: Optional[float],
                  p_no: Optional[float]) -> Optional[tuple]:
    """Return (yes_devig, no_devig) using proportional shrink. None if either side missing."""
    if p_yes is None or p_no is None:
        return None
    s = p_yes + p_no
    if s <= 0:
        return None
    return (p_yes / s, p_no / s)


def fmt_pct(v: Optional[float], digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{100*v:.{digits}f}%"


def fmt_num(v: Optional[float]) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


# ─── Mongo connector ───────────────────────────────────────────────────────
def get_db():
    """Lazy import — keeps `--help` fast."""
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient
    # Try both env paths so this works on preview and production.
    for candidate in ("/var/www/app/backend/.env", "/app/backend/.env"):
        if os.path.exists(candidate):
            load_dotenv(candidate)
            break
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise RuntimeError("MONGO_URL / DB_NAME not set in env")
    client = AsyncIOMotorClient(mongo_url)
    return client, client[db_name]


# ─── CSV writer ────────────────────────────────────────────────────────────
def write_csv(path: str, header: list[str], rows: list[list[Any]]) -> None:
    import csv
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def write_json(path: str, payload: Any) -> None:
    import json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

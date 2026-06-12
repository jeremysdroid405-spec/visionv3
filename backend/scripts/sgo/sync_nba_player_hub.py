"""
sync_nba_player_hub.py — Expands nba_master_hub_2026 to include every player
who appears in nba_player_historical_props or bdl_historical_game_logs but is
not yet in the hub.

Algorithm:
  1. Collect all unique SGO player_ids from nba_player_historical_props.
  2. Build a BDL player roster {bdl_player_id -> player_name} from bdl_historical_game_logs.
  3. For each historical-prop player NOT already in hub (matched by sgo_player_id):
       - Derive normalized_name from the SGO id (strip _1_NBA, lower, _ -> space)
       - Fuzzy-match against BDL player names (strip non-alnum, case-fold)
       - Upsert into nba_master_hub_2026 with roster_status="historical"
  4. Print a summary.

Usage:
    python -m scripts.sgo.sync_nba_player_hub --sport nba
    python -m scripts.sgo.sync_nba_player_hub --sport nba --fix-missing-bdl-ids
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

import httpx

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv

for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from rapidfuzz import process as fuzz_process
from rapidfuzz import fuzz

HUB_COLL = "nba_master_hub_2026"
HIST_PROPS_COLL = "nba_player_historical_props"
BDL_LOGS_COLL = "bdl_historical_game_logs"

FUZZY_THRESHOLD = 88  # min score to accept a BDL name match
BATCH_SIZE = 500
BDL_BASE = "https://api.balldontlie.io/v1"
_SUFFIX_RE = re.compile(r"\s*(jr\.?|sr\.?|ii|iii|iv)$", re.I)


def _slug(name: str) -> str:
    """Normalize a name for fuzzy comparison: lowercase, keep only alnum."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _sgo_to_normalized(sgo_player_id: str) -> str:
    """AUSTIN_REAVES_1_NBA  ->  austin reaves"""
    base = re.sub(r"_1_NBA$", "", sgo_player_id)
    return base.lower().replace("_", " ")


def _normalized_to_sgo(normalized_name: str) -> str:
    """austin reaves  ->  AUSTIN_REAVES_1_NBA"""
    return normalized_name.strip().upper().replace(" ", "_") + "_1_NBA"


async def amain(sport: str) -> None:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    now = datetime.now(timezone.utc)

    # ── 1. Collect all SGO player_ids from historical props ──────────────────
    print("Loading historical props player_ids…")
    hp_ids: set[str] = set(
        await db[HIST_PROPS_COLL].distinct("player_id")
    )
    print(f"  {len(hp_ids):,} unique player_ids in {HIST_PROPS_COLL}")

    # ── 2. Build BDL player roster from game logs ────────────────────────────
    print("Loading BDL player roster from game logs…")
    bdl_id_to_name: dict[int, str] = {}
    async for doc in db[BDL_LOGS_COLL].find(
        {},
        {"player_id": 1, "player_name": 1, "_id": 0},
    ):
        pid = doc.get("player_id")
        pname = doc.get("player_name", "").strip()
        if pid and pname and pid not in bdl_id_to_name:
            bdl_id_to_name[pid] = pname

    print(f"  {len(bdl_id_to_name):,} unique BDL players")

    # Build slug→(bdl_id, original_name) lookup for fast exact matching
    bdl_slug_index: dict[str, tuple[int, str]] = {
        _slug(name): (bdl_id, name)
        for bdl_id, name in bdl_id_to_name.items()
    }
    bdl_slugs = list(bdl_slug_index.keys())

    # Also keep lower-cased names for token-set fuzzy matching
    bdl_lower_index: dict[str, tuple[int, str]] = {
        name.lower(): (bdl_id, name)
        for bdl_id, name in bdl_id_to_name.items()
    }
    bdl_lower_names = list(bdl_lower_index.keys())

    # Build last-name → list[(bdl_id, name)] index for abbreviated-name fallback
    bdl_lastname_index: dict[str, list[tuple[int, str]]] = {}
    for bdl_id, name in bdl_id_to_name.items():
        # Use the last token before suffixes like "Jr.", "II", "III"
        tokens = name.lower().replace(".", "").split()
        last = tokens[-1] if tokens[-1] not in ("jr", "ii", "iii", "iv") else (tokens[-2] if len(tokens) > 1 else tokens[-1])
        bdl_lastname_index.setdefault(last, []).append((bdl_id, name))

    # ── 3. Collect hub's existing sgo_player_ids ─────────────────────────────
    print("Loading existing hub sgo_player_ids…")
    hub_sgo_ids: set[str] = set(
        x for x in await db[HUB_COLL].distinct("sgo_player_id") if x
    )
    print(f"  {len(hub_sgo_ids):,} players already in hub")

    # ── 4. Identify gaps ─────────────────────────────────────────────────────
    missing_sgo_ids = hp_ids - hub_sgo_ids
    print(f"\nHistorical props total:  {len(hp_ids):,}")
    print(f"Already in hub:          {len(hp_ids) - len(missing_sgo_ids):,}")
    print(f"Need to add:             {len(missing_sgo_ids):,}")

    if not missing_sgo_ids:
        print("\nHub is already complete — nothing to do.")
        client.close()
        return

    # ── 5. Fuzzy-match and upsert ─────────────────────────────────────────────
    newly_added = 0
    unmatched: list[str] = []
    ops: list[UpdateOne] = []

    for sgo_id in sorted(missing_sgo_ids):
        normalized = _sgo_to_normalized(sgo_id)
        query_slug = _slug(normalized)

        # 1. Exact slug match (fast path)
        bdl_match: Optional[tuple[int, str]] = bdl_slug_index.get(query_slug)
        matched_score = 100 if bdl_match else 0

        # 2. Token-set ratio on original lowercased names — handles word order
        #    and abbreviated first names (CAM vs Cameron, NIC vs Nicolas, etc.)
        if bdl_match is None:
            result = fuzz_process.extractOne(
                normalized,
                bdl_lower_names,
                scorer=fuzz.token_set_ratio,
                score_cutoff=FUZZY_THRESHOLD,
            )
            if result:
                matched_lower, matched_score, _ = result
                bdl_match = bdl_lower_index[matched_lower]

        # 3. Last-name-unique fallback — for single-initial SGO IDs like R_RUPERT
        if bdl_match is None:
            tokens = normalized.replace(".", "").split()
            last_tokens = [t for t in tokens if t not in ("jr", "ii", "iii", "iv")]
            last_name = last_tokens[-1] if last_tokens else None
            if last_name:
                candidates = bdl_lastname_index.get(last_name, [])
                if len(candidates) == 1:
                    bdl_match = candidates[0]
                    matched_score = 70  # last-name-only match, lower confidence

        if bdl_match is None:
            unmatched.append(sgo_id)
            continue

        bdl_player_id, bdl_player_name = bdl_match

        doc_fields = {
            "sgo_player_id": sgo_id,
            "normalized_name": normalized,
            "bdl_player_id": bdl_player_id,
            "bdl_id": bdl_player_id,  # canonical hub field; unique index requires it
            "player_name": bdl_player_name,
            "sport": sport,
            "roster_status": "historical",
            "last_updated": now,
        }

        ops.append(
            UpdateOne(
                {"bdl_id": bdl_player_id},
                {"$set": doc_fields},
                upsert=True,
            )
        )
        newly_added += 1

        if len(ops) >= BATCH_SIZE:
            await db[HUB_COLL].bulk_write(ops, ordered=False)
            ops = []

    if ops:
        await db[HUB_COLL].bulk_write(ops, ordered=False)

    client.close()

    # ── 6. Summary ────────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"Total historical players:  {len(hp_ids):,}")
    print(f"Already in hub:            {len(hp_ids) - len(missing_sgo_ids):,}")
    print(f"Newly added:               {newly_added:,}")
    print(f"Unmatched (no BDL match):  {len(unmatched):,}")

    if unmatched:
        print("\nUnmatched SGO player_ids:")
        for uid in sorted(unmatched):
            print(f"  {uid}")


async def _bdl_search_player(
    http: httpx.AsyncClient,
    api_key: str,
    last_name: str,
    normalized_full: str,
) -> Optional[int]:
    """Search BDL /players by last name only, then match on full normalized name."""
    players: list = []
    for attempt in range(4):
        try:
            resp = await http.get(
                f"{BDL_BASE}/players",
                params={"search": last_name, "per_page": 50},
                headers={"Authorization": api_key},
                timeout=30.0,
            )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            players = resp.json().get("data", [])
            break
        except Exception as exc:
            if attempt == 3:
                print(f"    BDL search error for '{last_name}': {exc}")
                return None
            await asyncio.sleep(2 ** attempt)

    target_slug = _slug(normalized_full)
    target_no_sfx = _slug(_SUFFIX_RE.sub("", normalized_full).strip())

    for player in players:
        full = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        if _slug(full) == target_slug:
            return player["id"]
        # Also match after stripping suffix (Jr., Sr., II, III, IV)
        if _slug(_SUFFIX_RE.sub("", full).strip()) == target_no_sfx and target_no_sfx:
            return player["id"]

    return None


async def fix_missing_bdl_ids(sport: str) -> None:
    """Second-pass: find hub docs with sgo_player_id but no bdl_player_id, resolve via BDL API."""
    api_key = os.environ.get("BALLDONTLIE_API_KEY") or os.environ.get("BDL_API_KEY")
    if not api_key:
        print("ERROR: BALLDONTLIE_API_KEY or BDL_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    now = datetime.now(timezone.utc)

    docs = await db[HUB_COLL].find(
        {
            "sgo_player_id": {"$exists": True, "$ne": ""},
            "$or": [{"bdl_player_id": {"$exists": False}}, {"bdl_player_id": None}],
        },
        {"_id": 1, "sgo_player_id": 1, "normalized_name": 1},
    ).to_list(None)

    print(f"Hub docs with sgo_player_id but no bdl_player_id: {len(docs):,}")
    if not docs:
        print("Nothing to fix.")
        client.close()
        return

    resolved = 0
    unresolved: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as http:
        for doc in docs:
            normalized = (doc.get("normalized_name") or "").strip()
            if not normalized:
                normalized = _sgo_to_normalized(doc.get("sgo_player_id", ""))

            parts = [p.replace(".", "").lower() for p in normalized.split()]
            suffix_tokens = {"jr", "sr", "ii", "iii", "iv"}
            non_suffix = [p for p in parts if p not in suffix_tokens]
            last_name = non_suffix[-1] if non_suffix else (parts[-1] if parts else "")

            if not last_name:
                unresolved.append(doc.get("sgo_player_id", str(doc["_id"])))
                continue

            bdl_id = await _bdl_search_player(http, api_key, last_name, normalized)
            await asyncio.sleep(0.1)  # gentle rate-limiting

            if bdl_id is None:
                unresolved.append(doc.get("sgo_player_id", str(doc["_id"])))
                continue

            await db[HUB_COLL].update_one(
                {"_id": doc["_id"]},
                {"$set": {"bdl_player_id": bdl_id, "bdl_id": bdl_id, "last_updated": now}},
            )
            resolved += 1

    client.close()

    print(f"\nNewly resolved bdl_player_ids: {resolved:,}")
    print(f"Still unresolved:              {len(unresolved):,}")
    if unresolved:
        print("\nUnresolved:")
        for u in sorted(unresolved):
            print(f"  {u}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync NBA player hub from historical data")
    parser.add_argument("--sport", default="nba", choices=["nba"], help="Sport (currently nba only)")
    parser.add_argument(
        "--fix-missing-bdl-ids",
        action="store_true",
        help="Second pass: resolve bdl_player_id for hub docs that have sgo_player_id but no bdl_player_id",
    )
    args = parser.parse_args()
    if args.fix_missing_bdl_ids:
        asyncio.run(fix_missing_bdl_ids(args.sport))
    else:
        asyncio.run(amain(args.sport))


if __name__ == "__main__":
    main()

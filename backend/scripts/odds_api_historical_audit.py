"""
The Odds API — Historical Alternate Player Prop Audit (READ-ONLY)
=================================================================
Strict 10-step checklist execution per user directive (2026-05-09).

GOAL: Determine the exact recipe to fetch historical NBA alternate player
prop ladders (especially combo stats) from The Odds API.

NO PRODUCTION CODE TOUCHED. NO MUTATIONS. NO STORAGE.

Cost guard: hard cap at 35 credits. Single-market calls only (each
historical /odds call = 10 credits per market per region).

Output: writes raw responses + summary report to:
    /app/audit_reports/odds_api_historical_audit_2026-05-09/
"""
import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------- config
API_KEY = os.environ.get("ODDS_API_KEY") or os.environ.get("THE_ODDS_API_KEY")
if not API_KEY:
    raise SystemExit("ODDS_API_KEY missing from /app/backend/.env")

BASE = "https://api.the-odds-api.com/v4"
SPORT = "basketball_nba"
REGION = "us"
SAMPLE_DATE = "2024-03-01"            # mid-season Friday slate
EVENTS_SNAPSHOT = f"{SAMPLE_DATE}T18:00:00Z"  # 1pm ET — pre-game listings

OUT_DIR = Path("/app/audit_reports/odds_api_historical_audit_2026-05-09")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CREDIT_BUDGET = 35
credits_used_estimate = 0

# Markets we want to probe — single market per call to control cost
ALT_MARKETS_PROBE_ORDER = [
    "player_points_alternate",
    "player_points_rebounds_assists_alternate",   # combo PRA
    "player_points_rebounds_alternate",            # combo PR
    "player_rebounds_assists_alternate",           # combo RA
    "player_points_assists_alternate",             # combo PA
    "player_rebounds_alternate",
    "player_assists_alternate",
    "player_threes_alternate",
]

# ---------------------------------------------------------------- http
session = requests.Session()
log: List[Dict] = []


def _redact(url: str) -> str:
    return url.replace(API_KEY, "***REDACTED***")


def call(endpoint: str, params: Dict, label: str) -> Tuple[Optional[Dict], Dict]:
    """Make a single HTTP GET; return (json_or_None, headers_subset)."""
    p = dict(params)
    p["apiKey"] = API_KEY
    url = f"{BASE}{endpoint}"
    t0 = time.time()
    r = session.get(url, params=p, timeout=30)
    dt = time.time() - t0

    headers_subset = {
        "x-requests-used": r.headers.get("x-requests-used"),
        "x-requests-remaining": r.headers.get("x-requests-remaining"),
        "x-requests-last": r.headers.get("x-requests-last"),
        "status": r.status_code,
        "elapsed_seconds": round(dt, 3),
    }

    log_entry = {
        "label": label,
        "url_redacted": _redact(r.url),
        "status": r.status_code,
        "headers": headers_subset,
    }
    log.append(log_entry)

    print(f"\n=== {label} ===")
    print(f"URL: {_redact(r.url)}")
    print(f"Status: {r.status_code}")
    print(f"Headers: {headers_subset}")

    if r.status_code != 200:
        print(f"Body (truncated): {r.text[:500]}")
        return None, headers_subset

    try:
        return r.json(), headers_subset
    except Exception as e:
        print(f"JSON parse failed: {e}")
        return None, headers_subset


# ---------------------------------------------------------------- step 1 + 2 + 3
print(f"\nThe Odds API HISTORICAL alternate prop audit")
print(f"API key: ***...{API_KEY[-4:]} (redacted)")
print(f"Sample slate: {SAMPLE_DATE}")
print(f"Credit budget: {CREDIT_BUDGET}")

# --- Call A: historical events for the date ---
events_payload, h_events = call(
    f"/historical/sports/{SPORT}/events",
    {"date": EVENTS_SNAPSHOT},
    label="A. historical_events",
)

if not events_payload or "data" not in events_payload:
    print("\nFATAL: historical events list returned no payload — stopping.")
    sys.exit(1)

events = events_payload["data"] or []
print(f"\nEvents returned: {len(events)}")
for ev in events[:5]:
    print(f"  - {ev.get('id')} | {ev.get('commence_time')} | "
          f"{ev.get('away_team')} @ {ev.get('home_team')}")

(OUT_DIR / "01_events.json").write_text(json.dumps(events_payload, indent=2))

if not events:
    print("\nFATAL: no events on sample date — stopping.")
    sys.exit(1)

# Pick first event scheduled cleanly after 7pm ET to ensure pre-game lines exist
chosen = None
for ev in events:
    ct = ev.get("commence_time", "")
    # Prefer a 7pm+ ET tip; just take first if none match
    if ct >= f"{SAMPLE_DATE}T23:00:00Z":
        chosen = ev
        break
chosen = chosen or events[0]
event_id = chosen["id"]
commence_time = chosen["commence_time"]
print(f"\nChosen event: {event_id} | {chosen.get('away_team')} @ "
      f"{chosen.get('home_team')} | tip {commence_time}")

# Snapshot timestamp ~ 2 hours before tip (when alt ladders are most populated)
tip_dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
snap_dt = tip_dt.replace(microsecond=0)
# back off 2 hours
from datetime import timedelta
snap_dt = snap_dt - timedelta(hours=2)
snapshot_ts = snap_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
print(f"Snapshot timestamp: {snapshot_ts}")

# --- Call B-...: one alt market per call (10 credits each) ---
findings: Dict[str, Dict] = {}
remaining_calls_budget = CREDIT_BUDGET // 10  # ~3 alt-market calls at 10/each
print(f"\nAlt-market call budget (at 10 credits/call): {remaining_calls_budget}")

probed = 0
for mkt in ALT_MARKETS_PROBE_ORDER:
    if probed >= remaining_calls_budget:
        print(f"\nBudget exhausted after {probed} alt-market calls — halting probes.")
        break
    payload, hdrs = call(
        f"/historical/sports/{SPORT}/events/{event_id}/odds",
        {
            "regions": REGION,
            "markets": mkt,
            "oddsFormat": "american",
            "date": snapshot_ts,
        },
        label=f"B{probed+1}. alt_market::{mkt}",
    )
    probed += 1

    finding = {
        "market_key_requested": mkt,
        "status": hdrs.get("status"),
        "x_requests_last": hdrs.get("x-requests-last"),
        "x_requests_used": hdrs.get("x-requests-used"),
        "x_requests_remaining": hdrs.get("x-requests-remaining"),
        "snapshot_returned": None,
        "previous_snapshot": None,
        "next_snapshot": None,
        "bookmakers": [],
        "market_keys_returned_by_book": {},
        "outcome_count_by_book_market": {},
        "alt_line_density_by_player": {},
        "samples": [],
    }

    if payload and isinstance(payload, dict):
        # historical event odds returns an envelope:
        # { timestamp, previous_timestamp, next_timestamp, data: { ...event with bookmakers } }
        finding["snapshot_returned"] = payload.get("timestamp")
        finding["previous_snapshot"] = payload.get("previous_timestamp")
        finding["next_snapshot"] = payload.get("next_timestamp")

        data = payload.get("data") or {}
        bms = data.get("bookmakers") or []
        finding["bookmakers"] = [b.get("key") for b in bms]

        for b in bms:
            bkey = b.get("key")
            mkts = b.get("markets") or []
            finding["market_keys_returned_by_book"][bkey] = [m.get("key") for m in mkts]

            for m in mkts:
                mk = m.get("key")
                outs = m.get("outcomes") or []
                finding["outcome_count_by_book_market"][f"{bkey}::{mk}"] = len(outs)

                for o in outs:
                    player = o.get("description")
                    if not player:
                        continue
                    by_player = finding["alt_line_density_by_player"].setdefault(player, set())
                    pt = o.get("point")
                    if pt is not None:
                        by_player.add(pt)

                    # collect up to 10 sample rows across all books/markets
                    if len(finding["samples"]) < 10:
                        finding["samples"].append({
                            "bookmaker": bkey,
                            "market": mk,
                            "player": player,
                            "side": o.get("name"),
                            "line": pt,
                            "price_american": o.get("price"),
                            "last_update": m.get("last_update"),
                        })

        # convert sets to sorted lists / counts
        finding["alt_line_density_by_player"] = {
            p: {"distinct_lines": len(s), "lines": sorted(s)}
            for p, s in finding["alt_line_density_by_player"].items()
        }

        # save raw envelope
        safe_mkt = mkt.replace("/", "_")
        (OUT_DIR / f"02_{probed:02d}_{safe_mkt}.raw.json").write_text(
            json.dumps(payload, indent=2)
        )

    findings[mkt] = finding

# ---------------------------------------------------------------- summary
report = {
    "audit_date_utc": datetime.now(timezone.utc).isoformat(),
    "api": "the-odds-api v4 historical",
    "sport": SPORT,
    "sample_date": SAMPLE_DATE,
    "events_snapshot": EVENTS_SNAPSHOT,
    "chosen_event": {
        "id": event_id,
        "commence_time": commence_time,
        "home_team": chosen.get("home_team"),
        "away_team": chosen.get("away_team"),
    },
    "snapshot_timestamp_used": snapshot_ts,
    "events_returned_count": len(events),
    "credit_budget": CREDIT_BUDGET,
    "alt_markets_probed": list(findings.keys()),
    "calls_log": log,
    "per_market_findings": findings,
}
(OUT_DIR / "03_summary.json").write_text(json.dumps(report, indent=2))

# --- Markdown report ---
md = []
md.append("# The Odds API — Historical Alt-Prop Audit (NBA, read-only)\n")
md.append(f"_Generated_: {report['audit_date_utc']}\n")
md.append(f"_Sample slate_: **{SAMPLE_DATE}** (event id `{event_id}`)\n")
md.append(f"_Snapshot ts_: `{snapshot_ts}` (≈ 2h before tip {commence_time})\n")
md.append("\n## Endpoint shapes (URLs redacted)\n")
md.append("```\n")
md.append(f"# events list (cost: 1 credit)\n")
md.append(f"GET {BASE}/historical/sports/{SPORT}/events?date={EVENTS_SNAPSHOT}&apiKey=***\n\n")
md.append(f"# alt-market historical event odds (cost: 10 × markets × regions)\n")
md.append(f"GET {BASE}/historical/sports/{SPORT}/events/{{eventId}}/odds"
          f"?regions={REGION}&markets={{ONE_MARKET_KEY}}&oddsFormat=american"
          f"&date={snapshot_ts}&apiKey=***\n")
md.append("```\n")

md.append(f"\n## Events list result\n- events returned: **{len(events)}**\n")

md.append("\n## Per-market findings\n")
md.append("| market_key | status | x-requests-last | snapshot_returned | "
          "books_with_market | total_outcomes | distinct_players | sample_density |\n")
md.append("|---|---|---|---|---|---|---|---|\n")
for mkt, f in findings.items():
    books_with = sum(1 for v in f["market_keys_returned_by_book"].values() if mkt in v)
    total_outs = sum(v for k, v in f["outcome_count_by_book_market"].items()
                     if k.endswith(f"::{mkt}"))
    distinct_players = len(f["alt_line_density_by_player"])
    avg_lines_per_player = (
        round(
            sum(d["distinct_lines"] for d in f["alt_line_density_by_player"].values())
            / max(distinct_players, 1),
            2,
        )
        if distinct_players else 0
    )
    md.append(
        f"| `{mkt}` | {f['status']} | {f['x_requests_last']} | "
        f"`{f['snapshot_returned']}` | {books_with}/{len(f['bookmakers'])} | "
        f"{total_outs} | {distinct_players} | {avg_lines_per_player} alt lines/player |\n"
    )

md.append("\n## Per-market sample rows (first 10 per market)\n")
for mkt, f in findings.items():
    md.append(f"\n### `{mkt}`\n")
    if not f["samples"]:
        md.append("_no samples returned_\n")
        continue
    md.append("| player | market | line | side | price (am.) | book | last_update |\n")
    md.append("|---|---|---|---|---|---|---|\n")
    for s in f["samples"]:
        md.append(
            f"| {s['player']} | `{s['market']}` | {s['line']} | {s['side']} | "
            f"{s['price_american']} | {s['bookmaker']} | {s['last_update']} |\n"
        )

md.append("\n## Calls log (with quota headers)\n")
md.append("| label | status | x-requests-last | x-requests-used | x-requests-remaining |\n")
md.append("|---|---|---|---|---|\n")
for entry in log:
    h = entry["headers"]
    md.append(
        f"| {entry['label']} | {h.get('status')} | {h.get('x-requests-last')} | "
        f"{h.get('x-requests-used')} | {h.get('x-requests-remaining')} |\n"
    )

(OUT_DIR / "REPORT.md").write_text("".join(md))

print(f"\n\nDONE. Report dir: {OUT_DIR}")
print(f"  - 01_events.json")
print(f"  - 02_*.raw.json (per market)")
print(f"  - 03_summary.json")
print(f"  - REPORT.md")

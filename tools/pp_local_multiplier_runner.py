#!/usr/bin/env python3
"""
PP Local Multiplier Runner — runs ON YOUR COMPUTER ONLY.
=========================================================
Connects to your already-running Chrome (started with
`--remote-debugging-port=9222`), drives the PrizePicks UI to select
two props at a time, captures the network responses PrizePicks
returns to YOUR browser, and POSTs the captured payout result to
your PropVision backend's `/api/admin/pp-multiplier-lab/ingest-captured-test`.

This is intentionally NOT a server-side automation. It re-uses
your own authenticated Chrome session, so:

  - NO bots, NO captcha bypass, NO PerimeterX/px-cloud touching
  - NO proxy rotation
  - NO entries / picks-submit / auth endpoints touched
  - NO bets placed (we only click prop cards and clear the slip)
  - The browser already does its normal anti-bot dance; we just
    listen passively to the HTTP responses you'd see anyway.

Hard guarantees in code (not just promises):
  - URL safety filter blocks any navigation to `px-cloud`,
    `perimeterx`, `/entries`, `/auth`, `/picks/submit`, `captcha`,
    `bot-defender` BEFORE the browser is asked.
  - "Submit" / "Place Entry" / "Confirm" buttons are NEVER clicked.
  - Randomized 8-15s sleep between combos.
  - Hard cap of 25 combos per run.

Usage
-----
1. Close all Chrome windows.
2. Launch Chrome with remote debugging:
     macOS:  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
               --remote-debugging-port=9222 --user-data-dir=$HOME/chrome-debug-pp
     Linux:  google-chrome --remote-debugging-port=9222 \\
               --user-data-dir=$HOME/.chrome-debug-pp
     Win:    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
               --remote-debugging-port=9222 --user-data-dir=%TEMP%\\chrome-debug-pp
3. Sign in to PrizePicks normally in that window once.
4. Open the league/board you want to test.
5. Run this script:
     pip install playwright httpx
     python -m playwright install chromium  # only for type-stubs;
                                            # we connect to YOUR Chrome
     python tools/pp_local_multiplier_runner.py \\
         --backend-url https://your-pv.com \\
         --admin-token <YOUR_ADMIN_DEBUG_TOKEN> \\
         --sport NBA \\
         --num-combos 5
"""
from __future__ import annotations

import os
import sys
import re
import json
import time
import random
import asyncio
import logging
import argparse
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
except ImportError:
    sys.exit("missing dep: pip install httpx")
try:
    from playwright.async_api import (
        async_playwright, Page, BrowserContext, Browser,
        TimeoutError as PWTimeout,
    )
except ImportError:
    sys.exit(
        "missing dep: pip install playwright "
        "(then once: python -m playwright install chromium)"
    )


# ─── Constants & safety ─────────────────────────────────────────────
PRIZEPICKS_BOARD_URL = "https://app.prizepicks.com/"
CHROME_CDP_DEFAULT = "http://127.0.0.1:9222"
DEFAULT_BACKEND = os.environ.get("PV_BACKEND_URL", "http://localhost:8001")
DEFAULT_TOKEN = os.environ.get("PV_ADMIN_TOKEN", "")

HARD_CAP_COMBOS = 25
MIN_DELAY_S = 8.0
MAX_DELAY_S = 15.0

# These fragments may NEVER appear in a URL the script asks the
# browser to navigate to. (We don't navigate the browser to PP's API
# at all — but defense in depth.)
FORBIDDEN_NAV_FRAGMENTS = (
    "px-cloud", "perimeterx", "/entries", "/auth", "/picks/submit",
    "captcha", "bot-defender", "/picks/post",
)
# Buttons whose text we will NEVER click — even if the user's UI
# state somehow exposed them.
FORBIDDEN_BUTTON_TEXTS = (
    "submit entry", "submit", "place entry", "place bet",
    "confirm entry", "confirm bet", "pay $", "deposit",
)

logger = logging.getLogger("pp_local_runner")


# ─── Backend client ─────────────────────────────────────────────────
class BackendClient:
    """Thin wrapper around our backend's admin endpoints."""

    def __init__(self, base_url: str, admin_token: str):
        self.base = base_url.rstrip("/")
        self.headers = {
            "X-Admin-Token": admin_token,
            "Content-Type": "application/json",
        }
        self._http = httpx.AsyncClient(headers=self.headers, timeout=30.0)

    async def close(self):
        await self._http.aclose()

    async def seed_projection_ids(
        self, league_id: str, projection_ids: List[str],
        sport: Optional[str] = None,
    ) -> Dict[str, Any]:
        r = await self._http.post(
            f"{self.base}/api/admin/pp-multiplier-lab/seed-projection-ids",
            json={"league_id": str(league_id), "sport": sport,
                  "projection_ids": projection_ids},
        )
        r.raise_for_status()
        return r.json()

    async def next_candidates(
        self, sport: str = "NBA", league_id: Optional[str] = None,
        leg_count: int = 2, limit: int = 5,
    ) -> Dict[str, Any]:
        params = {"sport": sport, "leg_count": leg_count, "limit": limit,
                  "skip_already_tested": "true"}
        if league_id:
            params["league_id"] = league_id
        r = await self._http.get(
            f"{self.base}/api/admin/pp-multiplier-lab/next-candidates",
            params=params,
        )
        r.raise_for_status()
        return r.json()

    async def ingest_captured_test(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = await self._http.post(
            f"{self.base}/api/admin/pp-multiplier-lab/ingest-captured-test",
            json=payload,
        )
        r.raise_for_status()
        return r.json()


# ─── URL safety ─────────────────────────────────────────────────────
def assert_safe_navigation(url: str) -> None:
    low = url.lower()
    for frag in FORBIDDEN_NAV_FRAGMENTS:
        if frag in low:
            raise RuntimeError(
                f"REFUSING navigation: forbidden fragment {frag!r} in {url!r}"
            )


def is_forbidden_button_text(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    return any(f in t for f in FORBIDDEN_BUTTON_TEXTS)


# ─── Network capture — we listen passively to PP responses ─────────
class CaptureBucket:
    """Per-combo bucket for the responses we care about."""
    def __init__(self):
        self.projections_for_ids: Optional[Dict[str, Any]] = None
        self.game_types: Optional[Dict[str, Any]] = None
        self.projections_url: Optional[str] = None
        self.last_projections_global: Optional[Dict[str, Any]] = None
        self.errors: List[str] = []

    def is_complete(self) -> bool:
        return self.projections_for_ids is not None and self.game_types is not None


# ─── PP UI driver ───────────────────────────────────────────────────
class PrizePicksDriver:
    """Drives the operator's existing Chrome session to select 2 props
    and capture the resulting payout response. Read-only/observational
    interactions only — never submits an entry.
    """

    def __init__(self, page: Page):
        self.page = page
        self._bucket: Optional[CaptureBucket] = None
        self._bucket_lock = asyncio.Lock()

    async def attach_listeners(self):
        """Attach a single response-listener for the lifetime of the
        page. The listener routes responses into whichever bucket is
        currently active."""
        async def _on_response(resp):
            try:
                url = resp.url
            except Exception:
                return
            low = url.lower()
            # Hard refuse: never log/save anything from forbidden frags.
            if any(f in low for f in FORBIDDEN_NAV_FRAGMENTS):
                return
            # Only care about prizepicks.com api.
            if "api.prizepicks.com" not in low:
                return
            try:
                if "/projections" in low and "ids=" in low:
                    body = await resp.json()
                    async with self._bucket_lock:
                        if self._bucket is not None:
                            self._bucket.projections_for_ids = body
                            self._bucket.projections_url = url
                elif "/projections" in low and "ids=" not in low:
                    body = await resp.json()
                    async with self._bucket_lock:
                        if self._bucket is not None:
                            self._bucket.last_projections_global = body
                elif "/game_types" in low:
                    body = await resp.json()
                    async with self._bucket_lock:
                        if self._bucket is not None:
                            self._bucket.game_types = body
            except Exception as e:
                async with self._bucket_lock:
                    if self._bucket is not None:
                        self._bucket.errors.append(f"{type(e).__name__}: {e}")

        self.page.on("response", lambda r: asyncio.create_task(_on_response(r)))

    async def open_board(self, url: str = PRIZEPICKS_BOARD_URL):
        assert_safe_navigation(url)
        cur = self.page.url or ""
        if "prizepicks.com" not in cur.lower():
            await self.page.goto(url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(2500)

    async def collect_visible_projection_ids(
        self, max_ids: int = 80,
    ) -> List[str]:
        """Pull projection IDs the user's PP board ALREADY rendered.

        Strategy: scrape any element with `data-projection-id`,
        `data-id`, or an HTML attribute matching the PP convention
        (PP historically renders projection cards with one of these).
        """
        ids: List[str] = await self.page.evaluate(
            """() => {
                const set = new Set();
                const sel = [
                    '[data-projection-id]',
                    '[data-test-id^="projection-"]',
                    '[id^="projection-"]'
                ];
                for (const s of sel) {
                    document.querySelectorAll(s).forEach(el => {
                        const v = el.getAttribute('data-projection-id')
                                || el.getAttribute('data-test-id')
                                || el.id;
                        if (!v) return;
                        const m = String(v).match(/(\\d{6,})/);
                        if (m) set.add(m[1]);
                    });
                }
                return Array.from(set);
            }"""
        )
        return list(dict.fromkeys(ids))[:max_ids]

    async def click_prop_by_projection_id(self, projection_id: str) -> bool:
        """Click the More side of the card whose projection_id matches.

        Returns True if a click was issued; False if the card wasn't
        found on screen (operator should scroll to bring it in view
        before re-running).

        We deliberately click the OVER/MORE side of the card. We do
        NOT click any "submit" / "place entry" / "confirm" elements.
        """
        # PP's card layouts vary; build a tolerant selector.
        sel = f'[data-projection-id="{projection_id}"]'
        loc = self.page.locator(sel).first
        try:
            await loc.scroll_into_view_if_needed(timeout=4000)
        except PWTimeout:
            return False
        # Try to click a "More" button inside the card if it exists,
        # otherwise click the card itself.
        more_btn = loc.locator(
            "button:has-text('More'), button:has-text('Higher'), "
            "[data-test-id$='-more-button']"
        ).first
        target = more_btn if await more_btn.count() > 0 else loc

        # Final paranoia: never click a forbidden-text button.
        try:
            txt = (await target.inner_text(timeout=1000)) or ""
        except Exception:
            txt = ""
        if is_forbidden_button_text(txt):
            logger.warning(
                "REFUSING click on element with forbidden text: %r", txt
            )
            return False
        await target.click(timeout=4000)
        return True

    async def clear_slip(self) -> bool:
        """Click the slip's "Clear" button (or remove each leg).
        Never clicks submit / pay / deposit."""
        # Try a "Clear" button.
        for clear_sel in (
            "button:has-text('Clear'):not(:has-text('Pay'))",
            "[data-test-id='clear-slip-button']",
        ):
            loc = self.page.locator(clear_sel).first
            if await loc.count() > 0:
                try:
                    txt = await loc.inner_text(timeout=1000)
                except Exception:
                    txt = ""
                if is_forbidden_button_text(txt):
                    continue
                await loc.click(timeout=4000)
                await self.page.wait_for_timeout(800)
                return True
        # Fallback: click each "remove" / × on the slip legs.
        removes = self.page.locator(
            "[data-test-id^='remove-leg'], button[aria-label*='Remove']"
        )
        n = await removes.count()
        clicked = 0
        for i in range(n):
            try:
                el = removes.nth(i)
                txt = await el.inner_text(timeout=500)
                if is_forbidden_button_text(txt):
                    continue
                await el.click(timeout=3000)
                clicked += 1
                await self.page.wait_for_timeout(400)
            except Exception:
                continue
        return clicked > 0

    async def run_combo(
        self, combo: List[str], wait_seconds: float = 18.0,
    ) -> CaptureBucket:
        """Click each projection in `combo` in order, then wait for
        BOTH `/projections?ids=` and `/game_types` responses or
        until timeout. Returns the capture bucket either way."""
        async with self._bucket_lock:
            self._bucket = CaptureBucket()
        deadline = time.time() + wait_seconds

        for pid in combo:
            ok = await self.click_prop_by_projection_id(pid)
            if not ok:
                async with self._bucket_lock:
                    self._bucket.errors.append(
                        f"projection {pid} not visible on board")
            await self.page.wait_for_timeout(800)

        while time.time() < deadline:
            async with self._bucket_lock:
                if self._bucket.is_complete():
                    break
            await asyncio.sleep(0.4)

        async with self._bucket_lock:
            bucket = self._bucket
            self._bucket = None
        return bucket


# ─── Connect to existing Chrome ─────────────────────────────────────
async def connect_to_chrome(cdp_url: str) -> Tuple[Browser, BrowserContext, Page]:
    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(cdp_url)
    contexts = browser.contexts
    if not contexts:
        raise RuntimeError(
            f"No browser context found at {cdp_url}. Did you launch "
            "Chrome with --remote-debugging-port=9222 ?"
        )
    ctx = contexts[0]
    # Pick an existing PP tab if present, else open a new one.
    page: Optional[Page] = None
    for pg in ctx.pages:
        if "prizepicks.com" in (pg.url or "").lower():
            page = pg
            break
    if page is None:
        page = await ctx.new_page()
        assert_safe_navigation(PRIZEPICKS_BOARD_URL)
        await page.goto(PRIZEPICKS_BOARD_URL, wait_until="domcontentloaded")
    return browser, ctx, page


# ─── Main runner ────────────────────────────────────────────────────
async def main(args) -> int:
    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.num_combos > HARD_CAP_COMBOS:
        logger.warning(
            "num_combos=%d > hard cap %d; clamping to %d",
            args.num_combos, HARD_CAP_COMBOS, HARD_CAP_COMBOS,
        )
        args.num_combos = HARD_CAP_COMBOS

    backend = BackendClient(args.backend_url, args.admin_token)
    try:
        # 1. Connect to operator's Chrome.
        logger.info("Connecting to Chrome at %s …", args.cdp_url)
        browser, ctx, page = await connect_to_chrome(args.cdp_url)

        driver = PrizePicksDriver(page)
        await driver.attach_listeners()
        await driver.open_board()
        # Give the page time to load and trigger its initial /projections.
        await page.wait_for_timeout(3500)

        # 2. Auto-seed the projection-ID cache from what the board
        #    rendered, so the backend can build candidate combos.
        visible = await driver.collect_visible_projection_ids(max_ids=120)
        logger.info("Visible projection IDs scraped from board: %d", len(visible))
        if visible:
            try:
                seed_resp = await backend.seed_projection_ids(
                    league_id=args.league_id, sport=args.sport,
                    projection_ids=visible,
                )
                logger.info("Seeded backend cache: %s", seed_resp)
            except httpx.HTTPError as e:
                logger.warning("seed call failed: %s — continuing", e)

        # 3. Get next-candidates from backend.
        nxt = await backend.next_candidates(
            sport=args.sport, league_id=args.league_id,
            leg_count=args.leg_count, limit=args.num_combos,
        )
        combos = nxt.get("combos") or []
        logger.info(
            "Backend returned %d candidate combos (pool_size=%s, "
            "skipped_tested=%s)",
            len(combos), nxt.get("pool_size"), nxt.get("skipped_tested"),
        )
        if not combos:
            logger.error(
                "No combos available. Make sure the PP board is loaded "
                "in your Chrome and projection IDs were scraped. "
                "Backend response: %s", nxt,
            )
            return 2

        if args.dry_run:
            print(json.dumps({"dry_run": True, "combos": combos}, indent=2))
            return 0

        # 4. Drive each combo.
        results: List[Dict[str, Any]] = []
        for idx, combo in enumerate(combos, 1):
            ids = combo["projection_ids"]
            logger.info("[%d/%d] Driving combo: %s", idx, len(combos), ids)
            bucket = await driver.run_combo(ids, wait_seconds=18.0)
            if not bucket.is_complete():
                logger.warning(
                    "  combo incomplete: projections_captured=%s "
                    "game_types_captured=%s errors=%s",
                    bucket.projections_for_ids is not None,
                    bucket.game_types is not None,
                    bucket.errors,
                )
                # Still try to clear and continue.
                await driver.clear_slip()
                await asyncio.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))
                continue

            # 5. Send to backend.
            payload = {
                "sport": args.sport,
                "league_id": str(args.league_id),
                "leg_count": args.leg_count,
                "selected_projection_ids": ids,
                "projections_response": bucket.projections_for_ids,
                "game_types_response": bucket.game_types,
                "capture_metadata": {
                    "captured_url": bucket.projections_url,
                    "ts": time.time(),
                },
                "notes": "captured via tools/pp_local_multiplier_runner.py",
            }
            try:
                ingest = await backend.ingest_captured_test(payload)
                logger.info(
                    "  saved test_id=%s mix=%s mult=%s adj=%s srp=%s",
                    ingest.get("test_id"), ingest.get("mix_type"),
                    ingest.get("power_play_multiplier"),
                    ingest.get("is_adjusted"), ingest.get("srp_multiplier"),
                )
                results.append(ingest)
            except httpx.HTTPError as e:
                logger.error("  ingest failed: %s", e)

            # 6. Clear slip then random delay.
            await driver.clear_slip()
            await asyncio.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))

        # Final summary.
        print(json.dumps({
            "ok": True,
            "combos_attempted": len(combos),
            "tests_saved": len(results),
            "saved_test_ids": [r.get("test_id") for r in results],
            "multipliers": sorted({
                r.get("power_play_multiplier") for r in results
                if r.get("power_play_multiplier") is not None
            }),
        }, indent=2, default=str))
        return 0
    finally:
        await backend.close()


# ─── CLI ────────────────────────────────────────────────────────────
def parse_args(argv: Optional[List[str]] = None):
    ap = argparse.ArgumentParser(
        description=(
            "Local PP multiplier runner — connects to YOUR Chrome on "
            "port 9222 and posts captured payout results to your "
            "PropVision backend. NEVER places bets or bypasses bot "
            "protection."
        )
    )
    ap.add_argument("--backend-url", default=DEFAULT_BACKEND,
                    help="PropVision backend root URL")
    ap.add_argument("--admin-token", default=DEFAULT_TOKEN,
                    help="Backend ADMIN_DEBUG_TOKEN (X-Admin-Token)")
    ap.add_argument("--cdp-url", default=CHROME_CDP_DEFAULT,
                    help="Chrome DevTools URL (default 127.0.0.1:9222)")
    ap.add_argument("--sport", default="NBA",
                    choices=["NBA", "MLB", "NFL", "NHL", "WNBA"])
    ap.add_argument("--league-id", default=None,
                    help="PP league_id; auto-resolved from --sport")
    ap.add_argument("--leg-count", type=int, default=2)
    ap.add_argument("--num-combos", type=int, default=5,
                    help=f"How many combos to drive (cap {HARD_CAP_COMBOS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Connect, scrape IDs, fetch combos, but DO NOT "
                         "click anything in PrizePicks.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if not args.admin_token:
        ap.error("--admin-token is required (or set $PV_ADMIN_TOKEN)")
    if not args.league_id:
        args.league_id = {
            "NBA": "7", "MLB": "2", "NFL": "9", "NHL": "8",
            "WNBA": "3",
        }.get(args.sport, args.sport)
    return args


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))

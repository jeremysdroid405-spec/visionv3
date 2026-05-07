/**
 * normalizeFerrariPick — Client-side payload shape adapter.
 *
 * Ferrari tier endpoints (`/api/v3/ferrari/{safe-haven,front-lines,war-zone}`)
 * and the player-detail endpoint (`/api/v3/player-with-badges/{name}`) both
 * return the SAME canonical per-pick shape. The React cards and detail page
 * read a handful of canonical fields (L5/L10/L20 hit rate, season average,
 * chart data, stat_type_extracted) which are populated on every visible
 * tier pick by the backend (verified by the Phase 4B canonical-presence
 * audit prior to this rewrite).
 *
 * 2026-05-07 P0 Phase 4B: this adapter no longer emits the legacy
 * `h5_rate` / `h10_rate` / `h20_rate` aliases. Backend stopped writing
 * them on tier picks; we now expose canonical `hit_rate_l5` /
 * `hit_rate_l10` / `hit_rate_l20` directly, and ALL frontend readers
 * have been migrated to those names. The cascade chain below pulls
 * from canonical first; legacy fields are not consulted.
 *
 * This adapter does NOT invent data. It only:
 *   - cascades existing values across windows when one is missing
 *     (l5 → l10 → over/under fallback at the appropriate window)
 *   - constructs a `chart_data` array from already-present averages +
 *     hit rates
 *   - sets `stat_type_extracted` from `stat_type` for grouping
 *   - normalizes `market` (strips `_alternate` suffix) so the existing
 *     constants.getCategoryKey keeps working
 *
 * Applied at every fetch boundary that returns Ferrari picks, so every
 * React component receives a consistent shape without any component
 * change.
 */

const _n = (v) => (v === null || v === undefined || v === "" ? null : v);

const _firstNumber = (...vals) => {
  for (const v of vals) {
    if (v === null || v === undefined || v === "") continue;
    const num = Number(v);
    if (Number.isFinite(num)) return num;
  }
  return null;
};

export function normalizeFerrariPick(pick) {
  if (!pick || typeof pick !== "object") return pick;
  const side = String(pick.recommendation || pick.direction || "OVER")
    .toUpperCase()
    .trim();
  const sideHitRate =
    side === "UNDER" ? _n(pick.hit_rate_under) : _n(pick.hit_rate_over);

  // Treat `0` as "no data" ONLY for hit-rate windows (cached_board
  // writes 0 on alt-market entries that have no recent-game samples).
  // We keep `0` as a valid signal for averages (a real game log can
  // legitimately be a 0-point shot chart prop).
  const _hrOrFallback = (...vals) => {
    for (const v of vals) {
      if (v === null || v === undefined || v === "") continue;
      const num = Number(v);
      if (!Number.isFinite(num)) continue;
      if (num === 0) continue; // treat 0-hit-rate as missing, cascade onward
      return num;
    }
    // If everything is 0 or empty, return 0 if we had at least one 0
    // (so the UI still renders "0%" rather than a dash), else null.
    for (const v of vals) {
      const num = Number(v);
      if (Number.isFinite(num)) return num;
    }
    return null;
  };

  // 2026-05-07 P0 Phase 4B: canonical-only window cascades.
  // hit_rate_l10 is the SSOT 10-game window; hit_rate_l5 / hit_rate_l20
  // similarly. If the score doc was missing one of the granular
  // windows for a particular pick, fall back to the active-side
  // window-aware over/under value (still SSOT, just a different lens).
  const hitRateL10 = _hrOrFallback(pick.hit_rate_l10, sideHitRate);
  const hitRateL5  = _hrOrFallback(pick.hit_rate_l5,  pick.hit_rate_l10, sideHitRate);
  const hitRateL20 = _hrOrFallback(pick.hit_rate_l20, pick.hit_rate_l10, sideHitRate);

  const l10 = _firstNumber(pick.l10_avg, pick.season_avg, pick.eb_player_career_mean);
  const l5 = _firstNumber(pick.l5_avg, pick.l10_avg, pick.season_avg, pick.eb_player_career_mean);
  const l20 = _firstNumber(pick.l20_avg, pick.season_avg, pick.l10_avg, pick.eb_player_career_mean);
  const seasonAvg = _firstNumber(
    pick.season_avg,
    pick.l20_avg,
    pick.l10_avg,
    pick.eb_player_career_mean,  // MLB EB-prior career mean (no NBA equivalent — safe fallback)
  );

  // Construct chart_data for the bar chart components (only when we have
  // at least an L10 average — otherwise return null so the UI falls back
  // to its "no chart" state gracefully).
  let chartData = pick.chart_data || pick.recent_games || null;
  if ((chartData === null || chartData === undefined) && l10 !== null) {
    const line = _firstNumber(pick.line);
    const build = (label, avg, hit) => ({
      window: label,
      avg,
      line,
      hit_rate: hit,
      // A single "delta vs line" point so simple bar charts can colour
      // green/red based on sign.
      delta: line !== null && avg !== null ? +(avg - line).toFixed(2) : null,
    });
    chartData = [
      build("L5", l5, hitRateL5),
      build("L10", l10, hitRateL10),
      build("L20", l20, hitRateL20),
      build("SEASON", seasonAvg, null),
    ].filter((d) => d.avg !== null);
  }

  // Extracted stat for grouping on the player-detail page. Prefer the
  // already-set backend value; fall back to `stat_type`. When `stat_type`
  // arrives as a raw market key (e.g. `player_points_assists_alternate`),
  // collapse it to the short code the detail page uses (`PA`, `PR`, etc.).
  const MARKET_TO_SHORT = {
    player_points: "PTS",
    player_rebounds: "REB",
    player_assists: "AST",
    player_threes: "3PM",
    player_steals: "STL",
    player_blocks: "BLK",
    player_turnovers: "TO",
    player_points_rebounds_assists: "PRA",
    player_points_rebounds: "PR",
    player_points_assists: "PA",
    player_rebounds_assists: "RA",
    player_steals_blocks: "BLST",
    player_double_double: "DD",
    player_triple_double: "TD",
    player_field_goals: "FGM",
    player_free_throws_made: "FTM",
  };

  const collapseToShortCode = (val) => {
    if (typeof val !== "string") return val;
    const key = val.replace(/_alternate$/i, "").toLowerCase();
    return MARKET_TO_SHORT[key] || val;
  };

  const statType = collapseToShortCode(pick.stat_type);
  const statTypeExtracted =
    pick.stat_type_extracted ||
    collapseToShortCode(pick.stat_type_extracted) ||
    statType ||
    null;

  // Market normalization: strip `_alternate` suffix so the detail page's
  // `getCategoryKey(market)` resolves to the correct category.
  let marketNormalized = pick.market;
  if (typeof marketNormalized === "string") {
    marketNormalized = marketNormalized
      .replace(/_alternate$/i, "")
      .toLowerCase();
  }
  // When `market` is absent (common on `/player-with-badges`), derive it
  // from either the short code (already collapsed above) or the raw
  // stat_type which may itself be a full market string.
  if (!marketNormalized) {
    const STAT_TO_MARKET = {
      PTS: "player_points",
      REB: "player_rebounds",
      AST: "player_assists",
      "3PM": "player_threes",
      STL: "player_steals",
      BLK: "player_blocks",
      TO: "player_turnovers",
      PRA: "player_points_rebounds_assists",
      "P+R": "player_points_rebounds",
      PR: "player_points_rebounds",
      "P+A": "player_points_assists",
      PA: "player_points_assists",
      "R+A": "player_rebounds_assists",
      RA: "player_rebounds_assists",
      "BLK+STL": "player_steals_blocks",
      BLST: "player_steals_blocks",
    };
    const shortKey = String(statType || pick.stat_type || "")
      .toUpperCase()
      .trim();
    marketNormalized = STAT_TO_MARKET[shortKey] || null;
    if (!marketNormalized && typeof pick.stat_type === "string") {
      marketNormalized = pick.stat_type
        .replace(/_alternate$/i, "")
        .toLowerCase();
    }
  }

  return {
    ...pick,
    // 2026-05-07 P0 Phase 4B: canonical-only window outputs. Legacy
    // `h5_rate` / `h10_rate` / `h20_rate` no longer emitted; readers
    // must consume `hit_rate_l5/l10/l20` (which the backend now
    // ships unconditionally on every visible pick).
    hit_rate_l5: hitRateL5,
    hit_rate_l10: hitRateL10,
    hit_rate_l20: hitRateL20,
    l5_avg: l5,
    l10_avg: l10,
    l20_avg: l20,
    season_avg: seasonAvg,
    chart_data: chartData,
    recent_games: chartData,
    stat_type: statType ?? pick.stat_type,
    stat_type_extracted: statTypeExtracted,
    market: marketNormalized ?? pick.market,
    // Keep the raw market accessible for debugging / alt-market display.
    market_raw: pick.market,
    // Direction alias some components consume.
    direction: pick.direction || pick.recommendation || "Over",
  };
}

export function normalizeFerrariPicks(picks) {
  if (!Array.isArray(picks)) return picks;
  return picks.map(normalizeFerrariPick);
}

export default normalizeFerrariPick;

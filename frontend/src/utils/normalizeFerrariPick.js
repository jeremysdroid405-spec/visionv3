/**
 * normalizeFerrariPick — Client-side payload shape adapter.
 *
 * Ferrari tier endpoints (`/api/v3/ferrari/{safe-haven,front-lines,war-zone}`)
 * and the player-detail endpoint (`/api/v3/player-with-badges/{name}`) both
 * return the SAME canonical per-pick shape. The React cards and detail page
 * read a handful of legacy field aliases (L5 hit rate, season average, chart
 * data, stat_type_extracted) which are NOT always populated on every pick —
 * the backend's source-of-truth is cached_board + score docs, and some combo /
 * alt markets carry only partial window aggregates.
 *
 * This adapter does NOT invent data. It only:
 *   - cascades existing values (h5 → h10 → hit_rate_over/under; l20 → season)
 *   - constructs a `chart_data` array from already-present averages + hit rates
 *   - sets `stat_type_extracted` from `stat_type` for grouping
 *   - normalizes `market` (strips `_alternate` suffix) so the existing
 *     constants.getCategoryKey keeps working
 *
 * Applied at every fetch boundary that returns Ferrari picks, so every React
 * component receives a consistent shape without any component change.
 */

const SIDE_FIELD_MAP = { OVER: "hit_rate_over", UNDER: "hit_rate_under" };

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

  // Window-fallback cascades — do NOT overwrite populated values.
  const h10 = _hrOrFallback(pick.h10_rate, sideHitRate);
  const h5 = _hrOrFallback(pick.h5_rate, pick.h10_rate, sideHitRate);
  const h20 = _hrOrFallback(pick.h20_rate, pick.h10_rate, sideHitRate);

  const l10 = _firstNumber(pick.l10_avg, pick.season_avg);
  const l5 = _firstNumber(pick.l5_avg, pick.l10_avg, pick.season_avg);
  const l20 = _firstNumber(pick.l20_avg, pick.season_avg, pick.l10_avg);
  const seasonAvg = _firstNumber(
    pick.season_avg,
    pick.l20_avg,
    pick.l10_avg,
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
      build("L5", l5, h5),
      build("L10", l10, h10),
      build("L20", l20, h20),
      build("SEASON", seasonAvg, null),
    ].filter((d) => d.avg !== null);
  }

  // Extracted stat for grouping on the player-detail page. Prefer the
  // already-set backend value; fall back to `stat_type`.
  const statTypeExtracted =
    pick.stat_type_extracted || pick.stat_type || null;

  // Market normalization: strip `_alternate` suffix so the detail page's
  // `getCategoryKey(market)` resolves to the correct category.
  let marketNormalized = pick.market;
  if (typeof marketNormalized === "string") {
    marketNormalized = marketNormalized
      .replace(/_alternate$/i, "")
      .toLowerCase();
  }
  if (!marketNormalized && pick.stat_type) {
    // Map short stat codes to canonical market keys when no market is
    // present on the payload (some war-zone combo picks).
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
    };
    const k = String(pick.stat_type).toUpperCase().trim();
    marketNormalized = STAT_TO_MARKET[k] || null;
  }

  return {
    ...pick,
    h5_rate: h5,
    h10_rate: h10,
    h20_rate: h20,
    l5_avg: l5,
    l10_avg: l10,
    l20_avg: l20,
    season_avg: seasonAvg,
    chart_data: chartData,
    recent_games: chartData,
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

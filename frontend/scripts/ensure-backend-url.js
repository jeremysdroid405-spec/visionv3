#!/usr/bin/env node
/**
 * ensure-backend-url.js
 *
 * Idempotent guard: every time the frontend starts or builds, this script
 * verifies that REACT_APP_BACKEND_URL in /app/frontend/.env points at the
 * production backend (propvision.bet). If the Emergent platform ever resets
 * the value to the preview URL — during a redeploy, fork init, or any
 * "protected variable" reconciliation — this hook forces it back.
 *
 * Behavior:
 *   • Reads /app/frontend/.env
 *   • If REACT_APP_BACKEND_URL is missing OR equals the Emergent preview URL,
 *     rewrites it to the canonical production URL.
 *   • Logs every action so the override is visible in CI / supervisor logs.
 *   • Never throws on read/write errors that would block startup.
 */
const fs = require("fs");
const path = require("path");

const ENV_FILE = path.resolve(__dirname, "../.env");
const KEY = "REACT_APP_BACKEND_URL";
const PRODUCTION_URL = "https://propvision.bet";
const EMERGENT_PREVIEW_PATTERN = /\.preview\.emergentagent\.com/i;

function log(msg) {
  console.log(`[ensure-backend-url] ${msg}`);
}

try {
  if (!fs.existsSync(ENV_FILE)) {
    log(`.env missing at ${ENV_FILE} — creating with ${KEY}=${PRODUCTION_URL}`);
    fs.writeFileSync(ENV_FILE, `${KEY}=${PRODUCTION_URL}\n`, "utf8");
    process.exit(0);
  }

  const raw = fs.readFileSync(ENV_FILE, "utf8");
  const lines = raw.split(/\r?\n/);
  let foundIdx = -1;
  let currentValue = null;

  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^\s*REACT_APP_BACKEND_URL\s*=\s*(.*?)\s*$/);
    if (m) {
      foundIdx = i;
      currentValue = m[1];
      break;
    }
  }

  if (foundIdx === -1) {
    log(`${KEY} not found — appending ${KEY}=${PRODUCTION_URL}`);
    lines.push(`${KEY}=${PRODUCTION_URL}`);
    fs.writeFileSync(ENV_FILE, lines.join("\n"), "utf8");
    process.exit(0);
  }

  const isPreview = EMERGENT_PREVIEW_PATTERN.test(currentValue || "");
  const isProd = currentValue === PRODUCTION_URL;

  if (isProd) {
    log(`${KEY}=${currentValue} (production — OK)`);
    process.exit(0);
  }

  if (isPreview) {
    log(`DETECTED Emergent preview URL: ${currentValue}`);
    log(`OVERRIDING with production URL: ${PRODUCTION_URL}`);
  } else {
    log(`Non-production value detected: ${currentValue}`);
    log(`OVERRIDING with production URL: ${PRODUCTION_URL}`);
  }

  lines[foundIdx] = `${KEY}=${PRODUCTION_URL}`;
  fs.writeFileSync(ENV_FILE, lines.join("\n"), "utf8");
  log(`Wrote ${ENV_FILE}`);
  process.exit(0);
} catch (err) {
  log(`ERROR (non-fatal): ${err && err.message ? err.message : err}`);
  // Never block startup — proceed regardless.
  process.exit(0);
}

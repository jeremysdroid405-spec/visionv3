/* eslint-disable react-hooks/exhaustive-deps */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

/**
 * Admin Testing — Private Universal Historical Testing Command Center.
 *
 * Token-protected (X-Admin-Token, validated via /api/emergent-admin/auth/whoami).
 * Token kept ONLY in localStorage. Route is unlinked; direct URL only.
 *
 * Talks to /api/emergent-admin/* exclusively.
 */
const API = process.env.REACT_APP_BACKEND_URL;
const ADMIN = `${API}/api/emergent-admin`;

const BG = '#09090B';
const SURFACE = '#18181B';
const SURFACE_2 = '#0F0F11';
const BORDER = '#27272A';
const BORDER_STRONG = '#3F3F46';
const MUTED = '#71717A';
const DIM = '#52525B';
const TEXT = '#FAFAFA';
const ACCENT = '#A78BFA';
const ACCENT_2 = '#34D399';
const WARN = '#FBBF24';
const BAD = '#F87171';

const TOKEN_KEY = 'emergentAdminToken';
const PRESETS_KEY = 'emergentAdminGridPresets';

// ── helpers ────────────────────────────────────────────────────────────────
function fmtTs(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return String(iso);
  }
}

function statusColor(s) {
  if (s === 'succeeded') return ACCENT_2;
  if (s === 'running' || s === 'queued') return ACCENT;
  if (s === 'failed' || s === 'errored' || s === 'cancelled') return BAD;
  return MUTED;
}

async function apiFetch(token, path, init = {}) {
  const headers = {
    'Content-Type': 'application/json',
    'X-Admin-Token': token,
    'X-Agent-Id': 'admin-testing-ui',
    ...(init.headers || {}),
  };
  const res = await fetch(`${ADMIN}${path}`, { ...init, headers });
  const body = await res.text();
  let parsed;
  try {
    parsed = body ? JSON.parse(body) : {};
  } catch {
    parsed = { _raw: body };
  }
  if (!res.ok) {
    const err = new Error(parsed?.detail || parsed?.message || `HTTP ${res.status}`);
    err.status = res.status;
    err.body = parsed;
    throw err;
  }
  return parsed;
}

// ── primitives ─────────────────────────────────────────────────────────────
function Section({ title, subtitle, right, children, testId }) {
  return (
    <div
      data-testid={testId}
      style={{
        background: SURFACE,
        border: `1px solid ${BORDER}`,
        borderRadius: 12,
        padding: 18,
        marginBottom: 16,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 12,
          gap: 12,
        }}
      >
        <div>
          <div
            style={{
              fontSize: 11,
              color: MUTED,
              textTransform: 'uppercase',
              letterSpacing: 0.7,
            }}
          >
            {title}
          </div>
          {subtitle && (
            <div style={{ fontSize: 12, color: DIM, marginTop: 3 }}>{subtitle}</div>
          )}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

function Btn({ children, variant = 'default', testId, ...rest }) {
  const palette = {
    default: { bg: BORDER, fg: TEXT, br: BORDER_STRONG },
    primary: { bg: ACCENT, fg: BG, br: ACCENT },
    danger: { bg: BAD, fg: BG, br: BAD },
    ghost: { bg: 'transparent', fg: TEXT, br: BORDER },
  }[variant];
  return (
    <button
      data-testid={testId}
      {...rest}
      style={{
        background: palette.bg,
        color: palette.fg,
        border: `1px solid ${palette.br}`,
        borderRadius: 6,
        padding: '7px 12px',
        fontSize: 12,
        fontWeight: 600,
        cursor: rest.disabled ? 'not-allowed' : 'pointer',
        opacity: rest.disabled ? 0.5 : 1,
        transition: 'opacity 120ms ease',
        ...(rest.style || {}),
      }}
    >
      {children}
    </button>
  );
}

function Input({ testId, ...rest }) {
  return (
    <input
      data-testid={testId}
      {...rest}
      style={{
        background: SURFACE_2,
        border: `1px solid ${BORDER}`,
        borderRadius: 6,
        padding: '7px 10px',
        color: TEXT,
        fontSize: 12,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        ...(rest.style || {}),
      }}
    />
  );
}

function Select({ testId, options = [], ...rest }) {
  return (
    <select
      data-testid={testId}
      {...rest}
      style={{
        background: SURFACE_2,
        border: `1px solid ${BORDER}`,
        borderRadius: 6,
        padding: '7px 10px',
        color: TEXT,
        fontSize: 12,
        ...(rest.style || {}),
      }}
    >
      {options.map((o) =>
        typeof o === 'string' ? (
          <option key={o} value={o}>
            {o}
          </option>
        ) : (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        )
      )}
    </select>
  );
}

function Field({ label, children, hint }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      <span
        style={{
          fontSize: 10,
          color: MUTED,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
        }}
      >
        {label}
      </span>
      {children}
      {hint && <span style={{ fontSize: 10, color: DIM }}>{hint}</span>}
    </label>
  );
}

// ── Token Gate ─────────────────────────────────────────────────────────────
function TokenGate({ token, setToken, whoami, setWhoami, onAuthed }) {
  const [input, setInput] = useState(token || '');
  const [validating, setValidating] = useState(false);
  const [err, setErr] = useState(null);

  const validate = useCallback(
    async (candidate) => {
      const t = (candidate ?? input).trim();
      if (!t) {
        setErr('Token required');
        return;
      }
      setValidating(true);
      setErr(null);
      try {
        const me = await apiFetch(t, '/auth/whoami');
        setWhoami(me);
        setToken(t);
        localStorage.setItem(TOKEN_KEY, t);
        toast.success(`Authed as ${me.agent_id || 'agent'}`);
        if (onAuthed) onAuthed();
      } catch (e) {
        setErr(e.message);
        toast.error(`Auth failed: ${e.message}`);
      } finally {
        setValidating(false);
      }
    },
    [input, setToken, setWhoami, onAuthed]
  );

  // auto-validate on mount if we have a cached token
  useEffect(() => {
    if (token && !whoami) validate(token);
  }, []);

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    setToken('');
    setWhoami(null);
    setInput('');
    toast.info('Token cleared');
  };

  return (
    <Section
      testId="admin-testing-token-section"
      title="Admin Token"
      subtitle="X-Admin-Token — validated against /auth/whoami. Stored only in localStorage."
      right={
        whoami && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span
              data-testid="admin-testing-whoami"
              style={{
                fontSize: 11,
                color: ACCENT_2,
                background: `${ACCENT_2}22`,
                padding: '4px 10px',
                borderRadius: 999,
              }}
            >
              ● {whoami.agent_id || 'agent'} · {whoami.token_hash}
            </span>
            <Btn variant="ghost" testId="admin-testing-logout-btn" onClick={logout}>
              Clear
            </Btn>
          </div>
        )
      }
    >
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <Input
          testId="admin-testing-token-input"
          type="password"
          placeholder="EMERGENT_ADMIN_TOKEN"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && validate()}
          style={{ flex: 1 }}
          autoComplete="off"
        />
        <Btn
          variant="primary"
          testId="admin-testing-validate-btn"
          onClick={() => validate()}
          disabled={validating || !input}
        >
          {validating ? 'Validating…' : whoami ? 'Re-validate' : 'Authenticate'}
        </Btn>
      </div>
      {err && (
        <div
          data-testid="admin-testing-token-error"
          style={{ marginTop: 8, fontSize: 12, color: BAD }}
        >
          {err}
        </div>
      )}
    </Section>
  );
}

// ── Job spec UI ────────────────────────────────────────────────────────────
// Maps job module → input form schema (label, key, kind, default).
// `kind`: text | date | int | bool | textarea | select
const STAT_FAMILY_PRESETS = [
  'hits',
  'total_bases',
  'hits_runs_rbis',
  'rbis',
  'runs',
  'home_runs',
  'singles',
  'doubles',
  'batter_strikeouts',
  'pitcher_strikeouts',
  'pitcher_outs',
  'earned_runs',
  'hits_allowed',
  'walks_allowed',
  'stolen_bases',
  'points',
];

const JOB_SPECS = {
  'scripts.sgo.build_historical_outcomes': {
    label: 'Build Historical Outcomes',
    purpose: 'Grade enriched anchors vs player stats → sgo_pp_research_outcomes',
    fields: [
      { key: '--league', label: 'League', kind: 'select', options: ['MLB', 'NBA', 'NFL'], default: 'MLB' },
      { key: '--start', label: 'Start (YYYY-MM-DD)', kind: 'date' },
      { key: '--end', label: 'End (YYYY-MM-DD)', kind: 'date' },
      { key: '--limit', label: 'Limit', kind: 'int' },
      { key: '--resume', label: 'Resume', kind: 'bool' },
      { key: '--dry-run', label: 'Dry run', kind: 'bool', default: true },
      { key: '--debug-unresolved', label: 'Debug unresolved', kind: 'bool' },
    ],
  },
  'scripts.sgo.build_historical_model_features': {
    label: 'Build Historical Model Features',
    purpose: 'Pre-game features → sgo_pp_research_model_features',
    fields: [
      { key: '--league', label: 'League', kind: 'select', options: ['MLB', 'NBA', 'NFL'], default: 'MLB' },
      { key: '--start', label: 'Start', kind: 'date' },
      { key: '--end', label: 'End', kind: 'date' },
      { key: '--lookback-days', label: 'Lookback days', kind: 'int' },
      { key: '--resume', label: 'Resume', kind: 'bool' },
      { key: '--dry-run', label: 'Dry run', kind: 'bool', default: true },
    ],
  },
  'scripts.sgo.score_historical_with_live_mlb_hf': {
    label: 'Score with Live MLB-HF',
    purpose: 'Score SGO features with live MLB-HF model → sgo_pp_research_model_predictions',
    fields: [
      { key: '--league', label: 'League', kind: 'select', options: ['MLB'], default: 'MLB' },
      { key: '--start', label: 'Start', kind: 'date' },
      { key: '--end', label: 'End', kind: 'date' },
      { key: '--limit', label: 'Limit', kind: 'int' },
      { key: '--probe', label: 'Probe mode (cheap)', kind: 'bool' },
      { key: '--force', label: 'Force overwrite', kind: 'bool' },
      { key: '--dry-run', label: 'Dry run', kind: 'bool', default: true },
    ],
  },
  'scripts.sgo.historical_full_pipeline_replay': {
    label: 'Historical Full-Pipeline Replay',
    purpose: 'Replay historical SGO props through live PropVision scoring + gate pipeline',
    primary: true,
    fields: [
      { key: '--league', label: 'League', kind: 'select', options: ['MLB'], default: 'MLB' },
      { key: '--start', label: 'Start', kind: 'date' },
      { key: '--end', label: 'End', kind: 'date' },
      {
        key: '--exclude-stat-family',
        label: 'Exclude stat families (comma)',
        kind: 'text',
        default: 'fantasy_score',
      },
      { key: '--limit', label: 'Limit', kind: 'int' },
      { key: '--force', label: 'Force re-score', kind: 'bool' },
      { key: '--dry-run', label: 'Dry run', kind: 'bool' },
    ],
  },
  'scripts.sgo.historical_gate_replay_grid': {
    label: 'Gate Replay Grid Sweep',
    purpose: 'Per-tier × per-stat_family threshold sweep → research_grid_results + candidate_gate_configs',
    primary: true,
    fields: [
      { key: '--league', label: 'League', kind: 'select', options: ['MLB'], default: 'MLB' },
      { key: '--start', label: 'Start', kind: 'date' },
      { key: '--end', label: 'End', kind: 'date' },
      { key: '--min-bets', label: 'Min bets / cell', kind: 'int', default: 20 },
      { key: '--dry-run', label: 'Dry run', kind: 'bool' },
    ],
  },
  'scripts.sgo.reshape_sgo_to_replay_odds': {
    label: 'Reshape SGO → Replay Odds',
    purpose: 'Reshape SGO enriched → mlb_historical_alt_odds_raw schema',
    fields: [
      { key: '--league', label: 'League', kind: 'select', options: ['MLB'], default: 'MLB' },
      { key: '--start', label: 'Start', kind: 'date' },
      { key: '--end', label: 'End', kind: 'date' },
      { key: '--limit', label: 'Limit', kind: 'int' },
    ],
  },
  'scripts.sgo.run_sgo_production_replay': {
    label: 'Run SGO Production Replay',
    purpose: 'Run live production replay pipeline over SGO odds → mlb_sgo_replay_*',
    fields: [
      { key: '--start', label: 'Start', kind: 'date' },
      { key: '--end', label: 'End', kind: 'date' },
      { key: '--tier', label: 'Tier', kind: 'select', options: ['', 'safe_haven', 'front_lines', 'war_zone'] },
      { key: '--gate-path', label: 'Gate path', kind: 'text' },
      { key: '--canonical-path', label: 'Canonical path', kind: 'bool' },
      { key: '--limit-dates', label: 'Limit dates', kind: 'int' },
      { key: '--dry-run', label: 'Dry run', kind: 'bool' },
    ],
  },
  'scripts.sgo.ingest_historical_player_stats': {
    label: 'Ingest Historical Player Stats',
    purpose: 'Re-ingest player stats from SGO API → sgo_player_stats',
    fields: [
      { key: '--league', label: 'League', kind: 'select', options: ['MLB', 'NBA', 'NFL'], default: 'MLB' },
      { key: '--start', label: 'Start', kind: 'date' },
      { key: '--end', label: 'End', kind: 'date' },
      { key: '--source', label: 'Source', kind: 'text' },
      { key: '--limit', label: 'Limit', kind: 'int' },
      { key: '--resume', label: 'Resume', kind: 'bool' },
      { key: '--dry-run', label: 'Dry run', kind: 'bool' },
      { key: '--debug-unresolved', label: 'Debug unresolved', kind: 'bool' },
    ],
  },
  'scripts.sgo.verify_sgo_player_stats_coverage': {
    label: 'Verify SGO Player Stats Coverage',
    purpose: 'Read-only coverage report (safe to run)',
    fields: [
      { key: '--league', label: 'League', kind: 'select', options: ['MLB', 'NBA', 'NFL'], default: 'MLB' },
      { key: '--start', label: 'Start', kind: 'date' },
      { key: '--end', label: 'End', kind: 'date' },
    ],
  },
  'scripts.research.grid_sweep': {
    label: 'Generic Grid Sweep (outcomes)',
    purpose: 'Outcome-side grid sweep over sgo_pp_research_outcomes',
    fields: [
      { key: '--league', label: 'League', kind: 'select', options: ['MLB', 'NBA', 'NFL'], default: 'MLB' },
      { key: '--start', label: 'Start', kind: 'date' },
      { key: '--end', label: 'End', kind: 'date' },
      { key: '--dataset', label: 'Dataset', kind: 'text' },
      { key: '--exclude-stat-family', label: 'Exclude families', kind: 'text', default: 'fantasy_score' },
      { key: '--min-bets', label: 'Min bets', kind: 'int', default: 20 },
      { key: '--config', label: 'Config (JSON path)', kind: 'text' },
      { key: '--dry-run', label: 'Dry run', kind: 'bool' },
    ],
  },
  'scripts.research.backfill_stat_family_canonical': {
    label: 'Backfill Canonical stat_family',
    purpose: 'Normalize legacy stat_family on replay collections (idempotent)',
    fields: [
      {
        key: '--collection',
        label: 'Collection',
        kind: 'select',
        options: ['', 'mlb_replay_feature_cache', 'mlb_replay_model_outputs'],
      },
      { key: '--league', label: 'League', kind: 'select', options: ['', 'MLB'], default: '' },
      { key: '--chunk-size', label: 'Chunk size', kind: 'int', default: 1000 },
      { key: '--sample-limit', label: 'Sample limit', kind: 'int' },
      { key: '--commit', label: 'Commit writes (vs default dry-run)', kind: 'bool' },
      { key: '--dry-run', label: 'Dry run', kind: 'bool', default: true },
    ],
  },
};

function buildArgvFromForm(spec, formState) {
  const argv = [];
  for (const f of spec.fields) {
    const v = formState[f.key];
    if (v === undefined || v === null || v === '') continue;
    if (f.kind === 'bool') {
      if (v === true) argv.push(f.key);
      continue;
    }
    argv.push(f.key);
    argv.push(String(v));
  }
  return argv;
}

function JobCard({ moduleName, spec, token, onLaunched }) {
  const initial = useMemo(() => {
    const s = {};
    for (const f of spec.fields) {
      if (f.default !== undefined) s[f.key] = f.default;
    }
    return s;
  }, [moduleName]);
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);

  const update = (k, v) => setForm((p) => ({ ...p, [k]: v }));
  const reset = () => setForm(initial);

  const run = async () => {
    const argv = buildArgvFromForm(spec, form);
    setBusy(true);
    try {
      const res = await apiFetch(token, '/jobs/run', {
        method: 'POST',
        body: JSON.stringify({ module: moduleName, args: argv }),
      });
      toast.success(`Queued ${spec.label} → ${res.job_id.slice(0, 8)}…`);
      if (onLaunched) onLaunched(res.job_id);
    } catch (e) {
      toast.error(`Run failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const previewArgv = buildArgvFromForm(spec, form);

  return (
    <div
      data-testid={`job-card-${moduleName}`}
      style={{
        background: SURFACE_2,
        border: `1px solid ${spec.primary ? ACCENT : BORDER}`,
        borderLeft: spec.primary ? `4px solid ${ACCENT}` : `1px solid ${BORDER}`,
        borderRadius: 8,
        padding: 14,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: spec.primary ? ACCENT : TEXT }}>
            {spec.label}
          </div>
          <div style={{ fontSize: 11, color: MUTED, marginTop: 3 }}>{spec.purpose}</div>
          <div style={{ fontSize: 10, color: DIM, marginTop: 4, fontFamily: 'monospace' }}>
            {moduleName}
          </div>
        </div>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
          gap: 10,
          marginTop: 12,
        }}
      >
        {spec.fields.map((f) => (
          <Field key={f.key} label={f.label} hint={f.key}>
            {f.kind === 'bool' ? (
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 0',
                  cursor: 'pointer',
                }}
              >
                <input
                  data-testid={`job-${moduleName}-${f.key}`}
                  type="checkbox"
                  checked={!!form[f.key]}
                  onChange={(e) => update(f.key, e.target.checked)}
                />
                <span style={{ fontSize: 11, color: form[f.key] ? ACCENT_2 : DIM }}>
                  {form[f.key] ? 'on' : 'off'}
                </span>
              </label>
            ) : f.kind === 'select' ? (
              <Select
                testId={`job-${moduleName}-${f.key}`}
                value={form[f.key] ?? ''}
                onChange={(e) => update(f.key, e.target.value)}
                options={f.options.map((o) => ({ value: o, label: o || '(none)' }))}
              />
            ) : (
              <Input
                testId={`job-${moduleName}-${f.key}`}
                type={f.kind === 'date' ? 'text' : f.kind === 'int' ? 'number' : 'text'}
                placeholder={
                  f.kind === 'date' ? 'YYYY-MM-DD' : f.kind === 'int' ? 'integer' : ''
                }
                value={form[f.key] ?? ''}
                onChange={(e) => update(f.key, e.target.value)}
              />
            )}
          </Field>
        ))}
      </div>

      <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <code
          style={{
            fontSize: 10,
            color: DIM,
            fontFamily: 'ui-monospace, monospace',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
          }}
          title={previewArgv.join(' ')}
        >
          {previewArgv.length ? `argv: ${previewArgv.join(' ')}` : '(no flags)'}
        </code>
        <Btn variant="ghost" onClick={reset} testId={`job-${moduleName}-reset`}>
          Reset
        </Btn>
        <Btn
          variant="primary"
          onClick={run}
          disabled={busy || !token}
          testId={`job-${moduleName}-run`}
        >
          {busy ? 'Queueing…' : 'Run Job'}
        </Btn>
      </div>
    </div>
  );
}

// ── Per-Stat Sweep Builder ─────────────────────────────────────────────────
const STAT_THRESHOLDS = [
  { key: 'hr_l20_min', label: 'HR L20 min', step: 0.05, default: 0.7 },
  { key: 'hr_l5_min', label: 'HR L5 min', step: 0.05, default: 0.6 },
  { key: 'cv_max', label: 'CV max', step: 0.05, default: 0.9 },
  { key: 'edge_min', label: 'Edge min', step: 0.005, default: 0.05 },
  { key: 'tp_min', label: 'TP min', step: 0.05, default: 0.5 },
];

const TIERS = ['safe_haven', 'front_lines', 'war_zone'];

function defaultPresetState() {
  const fams = {};
  for (const f of STAT_FAMILY_PRESETS) {
    fams[f] = { enabled: false, thresholds: {} };
    for (const t of STAT_THRESHOLDS) fams[f].thresholds[t.key] = t.default;
  }
  return {
    name: '',
    league: 'MLB',
    start: '',
    end: '',
    tiers: { safe_haven: true, front_lines: true, war_zone: true },
    odds_buckets: {
      'odds_lt_-200': false,
      'odds_-200_-100': true,
      'odds_-100_-0': true,
      'odds_+0_+150': true,
      'odds_+150_+300': true,
      'odds_+300p': false,
    },
    min_bets: 20,
    families: fams,
  };
}

function StatSweepBuilder({ token }) {
  const [presets, setPresets] = useState(() => {
    try {
      const raw = localStorage.getItem(PRESETS_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  });
  const [draft, setDraft] = useState(defaultPresetState);
  const [activePresetId, setActivePresetId] = useState(null);
  const [busy, setBusy] = useState(false);

  const savePresets = (next) => {
    setPresets(next);
    localStorage.setItem(PRESETS_KEY, JSON.stringify(next));
  };

  const upd = (patch) => setDraft((p) => ({ ...p, ...patch }));
  const updFamily = (fam, patch) =>
    setDraft((p) => ({
      ...p,
      families: { ...p.families, [fam]: { ...p.families[fam], ...patch } },
    }));
  const updFamThresh = (fam, key, value) =>
    setDraft((p) => ({
      ...p,
      families: {
        ...p.families,
        [fam]: {
          ...p.families[fam],
          thresholds: { ...p.families[fam].thresholds, [key]: value },
        },
      },
    }));

  const savePreset = () => {
    if (!draft.name.trim()) {
      toast.error('Preset name required');
      return;
    }
    const id = activePresetId || `preset_${Date.now()}`;
    const entry = { ...draft, id, savedAt: new Date().toISOString() };
    const next = activePresetId
      ? presets.map((p) => (p.id === activePresetId ? entry : p))
      : [...presets, entry];
    savePresets(next);
    setActivePresetId(id);
    toast.success(`Saved preset "${entry.name}"`);
  };

  const loadPreset = (id) => {
    const p = presets.find((x) => x.id === id);
    if (!p) return;
    setDraft(p);
    setActivePresetId(id);
  };

  const deletePreset = (id) => {
    savePresets(presets.filter((p) => p.id !== id));
    if (activePresetId === id) {
      setActivePresetId(null);
      setDraft(defaultPresetState());
    }
  };

  const enabledFamilies = Object.entries(draft.families).filter(([, v]) => v.enabled);

  const launchSweep = async () => {
    if (!draft.start || !draft.end) {
      toast.error('Start and end dates required');
      return;
    }
    setBusy(true);
    try {
      // Persist current draft as ephemeral pending-config for traceability
      const payload = {
        kind: 'admin_testing_sweep_config',
        scope: `${draft.league}:${draft.start}..${draft.end}`,
        config: {
          name: draft.name || '(unnamed)',
          tiers: Object.entries(draft.tiers).filter(([, v]) => v).map(([k]) => k),
          odds_buckets: Object.entries(draft.odds_buckets).filter(([, v]) => v).map(([k]) => k),
          min_bets: draft.min_bets,
          per_stat_family: enabledFamilies.map(([fam, cfg]) => ({
            stat_family: fam,
            ...cfg.thresholds,
          })),
        },
        note: 'Saved from /admin/testing UI before sweep kickoff',
      };
      try {
        await apiFetch(token, '/configs/draft', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      } catch (e) {
        // Soft-fail: config drafting is bookkeeping only
        console.warn('[sweep] candidate-config draft failed', e);
      }

      const args = [
        '--league', draft.league,
        '--start', draft.start,
        '--end', draft.end,
        '--min-bets', String(draft.min_bets || 20),
      ];
      const res = await apiFetch(token, '/jobs/run', {
        method: 'POST',
        body: JSON.stringify({
          module: 'scripts.sgo.historical_gate_replay_grid',
          args,
        }),
      });
      toast.success(`Sweep queued — job ${res.job_id.slice(0, 8)}…`);
    } catch (e) {
      toast.error(`Sweep failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section
      testId="sweep-builder-section"
      title="Per-Stat Sweep Builder"
      subtitle="Independent thresholds per stat_family. Saves to candidate_gate_configs and triggers historical_gate_replay_grid."
      right={
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {presets.length > 0 && (
            <Select
              testId="sweep-preset-load"
              value={activePresetId || ''}
              onChange={(e) => {
                const v = e.target.value;
                if (v === '__new__') {
                  setActivePresetId(null);
                  setDraft(defaultPresetState());
                } else if (v) {
                  loadPreset(v);
                }
              }}
              options={[
                { value: '', label: '— preset —' },
                { value: '__new__', label: '+ New' },
                ...presets.map((p) => ({ value: p.id, label: p.name || p.id })),
              ]}
            />
          )}
          <Btn variant="ghost" onClick={savePreset} testId="sweep-save-btn">
            Save Preset
          </Btn>
          {activePresetId && (
            <Btn
              variant="danger"
              onClick={() => deletePreset(activePresetId)}
              testId="sweep-delete-btn"
            >
              Delete
            </Btn>
          )}
        </div>
      }
    >
      {/* Top controls */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
          gap: 10,
          marginBottom: 14,
        }}
      >
        <Field label="Preset Name">
          <Input
            testId="sweep-name"
            value={draft.name}
            onChange={(e) => upd({ name: e.target.value })}
            placeholder="e.g. May-2025-MLB-hits"
          />
        </Field>
        <Field label="League">
          <Select
            testId="sweep-league"
            value={draft.league}
            onChange={(e) => upd({ league: e.target.value })}
            options={['MLB']}
          />
        </Field>
        <Field label="Start">
          <Input
            testId="sweep-start"
            value={draft.start}
            placeholder="YYYY-MM-DD"
            onChange={(e) => upd({ start: e.target.value })}
          />
        </Field>
        <Field label="End">
          <Input
            testId="sweep-end"
            value={draft.end}
            placeholder="YYYY-MM-DD"
            onChange={(e) => upd({ end: e.target.value })}
          />
        </Field>
        <Field label="Min bets / cell">
          <Input
            testId="sweep-min-bets"
            type="number"
            value={draft.min_bets}
            onChange={(e) => upd({ min_bets: parseInt(e.target.value || '0', 10) })}
          />
        </Field>
      </div>

      {/* Tiers + odds buckets */}
      <div style={{ display: 'flex', gap: 24, marginBottom: 14, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>
            Tiers
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {TIERS.map((t) => (
              <label
                key={t}
                data-testid={`sweep-tier-${t}`}
                style={{
                  background: draft.tiers[t] ? `${ACCENT}22` : SURFACE_2,
                  border: `1px solid ${draft.tiers[t] ? ACCENT : BORDER}`,
                  color: draft.tiers[t] ? ACCENT : MUTED,
                  borderRadius: 999,
                  padding: '5px 12px',
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: 'pointer',
                  textTransform: 'uppercase',
                  letterSpacing: 0.4,
                }}
              >
                <input
                  type="checkbox"
                  checked={!!draft.tiers[t]}
                  onChange={(e) =>
                    upd({ tiers: { ...draft.tiers, [t]: e.target.checked } })
                  }
                  style={{ display: 'none' }}
                />
                {t.replace('_', ' ')}
              </label>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>
            Odds Buckets
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {Object.keys(draft.odds_buckets).map((b) => (
              <label
                key={b}
                data-testid={`sweep-odds-${b}`}
                style={{
                  background: draft.odds_buckets[b] ? `${ACCENT_2}22` : SURFACE_2,
                  border: `1px solid ${draft.odds_buckets[b] ? ACCENT_2 : BORDER}`,
                  color: draft.odds_buckets[b] ? ACCENT_2 : MUTED,
                  borderRadius: 999,
                  padding: '5px 10px',
                  fontSize: 10,
                  fontFamily: 'monospace',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={!!draft.odds_buckets[b]}
                  onChange={(e) =>
                    upd({
                      odds_buckets: { ...draft.odds_buckets, [b]: e.target.checked },
                    })
                  }
                  style={{ display: 'none' }}
                />
                {b.replace('odds_', '')}
              </label>
            ))}
          </div>
        </div>
      </div>

      {/* Per-family table */}
      <div
        style={{
          fontSize: 10,
          color: MUTED,
          textTransform: 'uppercase',
          letterSpacing: 0.6,
          marginBottom: 8,
        }}
      >
        Per-Family Thresholds — independent grid per stat family
      </div>
      <div
        data-testid="sweep-family-grid"
        style={{
          background: SURFACE_2,
          border: `1px solid ${BORDER}`,
          borderRadius: 8,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: `28px 1.4fr repeat(${STAT_THRESHOLDS.length}, 1fr)`,
            gap: 10,
            padding: '8px 12px',
            fontSize: 10,
            color: DIM,
            textTransform: 'uppercase',
            background: SURFACE,
            borderBottom: `1px solid ${BORDER}`,
          }}
        >
          <div></div>
          <div>Stat Family</div>
          {STAT_THRESHOLDS.map((t) => (
            <div key={t.key}>{t.label}</div>
          ))}
        </div>
        {STAT_FAMILY_PRESETS.map((fam) => {
          const cfg = draft.families[fam];
          return (
            <div
              key={fam}
              data-testid={`sweep-fam-row-${fam}`}
              style={{
                display: 'grid',
                gridTemplateColumns: `28px 1.4fr repeat(${STAT_THRESHOLDS.length}, 1fr)`,
                gap: 10,
                padding: '8px 12px',
                alignItems: 'center',
                borderBottom: `1px solid ${BORDER}`,
                background: cfg.enabled ? `${ACCENT}0a` : 'transparent',
              }}
            >
              <input
                data-testid={`sweep-fam-enable-${fam}`}
                type="checkbox"
                checked={cfg.enabled}
                onChange={(e) => updFamily(fam, { enabled: e.target.checked })}
              />
              <div style={{ fontSize: 12, fontFamily: 'monospace', color: cfg.enabled ? TEXT : DIM }}>
                {fam}
              </div>
              {STAT_THRESHOLDS.map((t) => (
                <Input
                  key={t.key}
                  testId={`sweep-${fam}-${t.key}`}
                  type="number"
                  step={t.step}
                  value={cfg.thresholds[t.key]}
                  disabled={!cfg.enabled}
                  onChange={(e) =>
                    updFamThresh(fam, t.key, parseFloat(e.target.value))
                  }
                  style={{ opacity: cfg.enabled ? 1 : 0.4 }}
                />
              ))}
            </div>
          );
        })}
      </div>

      <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ fontSize: 11, color: DIM }}>
          {enabledFamilies.length} families enabled ·{' '}
          {Object.values(draft.tiers).filter(Boolean).length} tiers ·{' '}
          {Object.values(draft.odds_buckets).filter(Boolean).length} odds buckets
        </div>
        <Btn
          variant="primary"
          testId="sweep-launch-btn"
          onClick={launchSweep}
          disabled={busy || !token}
        >
          {busy ? 'Launching…' : 'Launch Grid Sweep'}
        </Btn>
      </div>
    </Section>
  );
}

// ── Jobs panel (live tail) ─────────────────────────────────────────────────
function JobsPanel({ token, focusJobId, setFocusJobId }) {
  const [jobs, setJobs] = useState([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [log, setLog] = useState({ jobId: null, lines: [], total: 0, status: null });
  const [autoPoll, setAutoPoll] = useState(true);
  const pollRef = useRef(null);

  const fetchJobs = useCallback(async () => {
    if (!token) return;
    try {
      const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}&limit=50` : '?limit=50';
      const res = await apiFetch(token, `/jobs/${qs}`);
      setJobs(res.jobs || []);
    } catch (e) {
      // silent — toast spam is annoying when polling
      console.error('[jobs] list error', e.message);
    }
  }, [token, statusFilter]);

  const fetchLog = useCallback(
    async (jobId) => {
      if (!token || !jobId) return;
      try {
        const res = await apiFetch(token, `/jobs/${jobId}/log?tail=400`);
        setLog({
          jobId,
          lines: res.lines || [],
          total: res.total_lines || 0,
          status: res.status,
        });
      } catch (e) {
        console.error('[jobs] log error', e.message);
      }
    },
    [token]
  );

  // poll list every 3s
  useEffect(() => {
    if (!token || !autoPoll) return;
    fetchJobs();
    const id = setInterval(fetchJobs, 3000);
    return () => clearInterval(id);
  }, [token, autoPoll, fetchJobs]);

  // poll focused job log every 2s
  useEffect(() => {
    if (!focusJobId || !token) return;
    fetchLog(focusJobId);
    pollRef.current = setInterval(() => fetchLog(focusJobId), 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [focusJobId, token, fetchLog]);

  const cancel = async (jobId) => {
    if (!window.confirm(`Cancel job ${jobId.slice(0, 8)}…?`)) return;
    try {
      await apiFetch(token, `/jobs/${jobId}/cancel`, {
        method: 'POST',
        body: JSON.stringify({ confirm: true }),
      });
      toast.success('Cancel signal sent');
      fetchJobs();
    } catch (e) {
      toast.error(`Cancel failed: ${e.message}`);
    }
  };

  return (
    <Section
      testId="jobs-panel-section"
      title="Recent Jobs"
      subtitle="Polls /jobs every 3s. Click a job to tail its log (2s cadence)."
      right={
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Select
            testId="jobs-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            options={[
              { value: '', label: 'all' },
              { value: 'queued', label: 'queued' },
              { value: 'running', label: 'running' },
              { value: 'succeeded', label: 'succeeded' },
              { value: 'failed', label: 'failed' },
              { value: 'errored', label: 'errored' },
              { value: 'cancelled', label: 'cancelled' },
            ]}
          />
          <label
            style={{
              fontSize: 11,
              color: autoPoll ? ACCENT_2 : MUTED,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              cursor: 'pointer',
            }}
          >
            <input
              data-testid="jobs-autopoll"
              type="checkbox"
              checked={autoPoll}
              onChange={(e) => setAutoPoll(e.target.checked)}
            />
            auto-poll
          </label>
          <Btn variant="ghost" onClick={fetchJobs} testId="jobs-refresh">
            Refresh
          </Btn>
        </div>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.4fr)', gap: 14 }}>
        <div
          data-testid="jobs-list"
          style={{
            background: SURFACE_2,
            border: `1px solid ${BORDER}`,
            borderRadius: 8,
            maxHeight: 520,
            overflowY: 'auto',
          }}
        >
          {jobs.length === 0 ? (
            <div style={{ padding: 18, color: DIM, fontSize: 12, textAlign: 'center' }}>
              No jobs yet — queue one above.
            </div>
          ) : (
            jobs.map((j) => {
              const active = focusJobId === j.job_id;
              return (
                <div
                  key={j.job_id}
                  data-testid={`job-row-${j.job_id}`}
                  onClick={() => setFocusJobId(j.job_id)}
                  style={{
                    padding: '10px 12px',
                    cursor: 'pointer',
                    background: active ? `${ACCENT}14` : 'transparent',
                    borderBottom: `1px solid ${BORDER}`,
                    borderLeft: active ? `3px solid ${ACCENT}` : '3px solid transparent',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                    <code
                      style={{
                        fontSize: 11,
                        color: TEXT,
                        fontFamily: 'monospace',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        flex: 1,
                      }}
                    >
                      {j.module}
                    </code>
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        color: statusColor(j.status),
                        background: `${statusColor(j.status)}1f`,
                        padding: '2px 8px',
                        borderRadius: 999,
                        textTransform: 'uppercase',
                      }}
                    >
                      {j.status}
                    </span>
                  </div>
                  <div style={{ fontSize: 10, color: DIM, marginTop: 4, fontFamily: 'monospace' }}>
                    {j.job_id.slice(0, 8)} · {fmtTs(j.queued_at)}
                    {j.exit_code !== undefined && j.exit_code !== null && ` · rc=${j.exit_code}`}
                  </div>
                  {j.args && j.args.length > 0 && (
                    <div
                      style={{
                        fontSize: 10,
                        color: MUTED,
                        marginTop: 2,
                        fontFamily: 'monospace',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {j.args.join(' ')}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        <div
          data-testid="jobs-log-pane"
          style={{
            background: '#000',
            border: `1px solid ${BORDER}`,
            borderRadius: 8,
            minHeight: 300,
            maxHeight: 520,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '8px 12px',
              borderBottom: `1px solid ${BORDER}`,
              fontSize: 11,
              color: MUTED,
              fontFamily: 'monospace',
            }}
          >
            <span data-testid="jobs-log-header">
              {focusJobId ? `${focusJobId.slice(0, 8)} · ${log.status || '…'} · ${log.total} lines` : 'select a job'}
            </span>
            {focusJobId && log.status === 'running' && (
              <Btn variant="danger" onClick={() => cancel(focusJobId)} testId="jobs-cancel-btn">
                Cancel
              </Btn>
            )}
          </div>
          <pre
            data-testid="jobs-log-body"
            style={{
              flex: 1,
              margin: 0,
              padding: 12,
              fontSize: 11,
              fontFamily: 'ui-monospace, monospace',
              color: '#A1A1AA',
              overflowY: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {focusJobId
              ? log.lines.length
                ? log.lines.join('')
                : '(no output yet)'
              : '— pick a job from the list to tail its log —'}
          </pre>
        </div>
      </div>
    </Section>
  );
}

// ── Policy & Deploy ────────────────────────────────────────────────────────
function PolicyAndDeploy({ token }) {
  const [policy, setPolicy] = useState(null);
  const [deployStatus, setDeployStatus] = useState(null);
  const [deploying, setDeploying] = useState(false);

  const fetchPolicy = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiFetch(token, '/policy/');
      setPolicy(res);
    } catch (e) {
      console.error('[policy]', e.message);
    }
  }, [token]);

  const fetchDeploy = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiFetch(token, '/deploy/status');
      setDeployStatus(res);
    } catch (e) {
      console.error('[deploy/status]', e.message);
    }
  }, [token]);

  useEffect(() => {
    fetchPolicy();
    fetchDeploy();
  }, [fetchPolicy, fetchDeploy]);

  const deploy = async () => {
    if (
      !window.confirm(
        `Pull origin/${deployStatus?.allowed_branches?.[0] || 'newestbuild'} and restart backend?`
      )
    )
      return;
    setDeploying(true);
    try {
      const res = await apiFetch(token, '/deploy/pull-and-restart', {
        method: 'POST',
        body: JSON.stringify({
          branch: deployStatus?.allowed_branches?.[0] || 'newestbuild',
          stash: false,
          restart_backend: true,
          confirm: true,
        }),
      });
      if (res.ok) {
        toast.success(
          res.no_op
            ? 'No-op — already at latest commit'
            : `Deployed → ${res.new_sha?.slice(0, 8)}`
        );
      } else {
        toast.error(`Deploy aborted: ${res.aborted || 'unknown'}`);
      }
      fetchDeploy();
    } catch (e) {
      toast.error(`Deploy failed: ${e.message}`);
    } finally {
      setDeploying(false);
    }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
      <Section
        testId="policy-section"
        title="Policy"
        subtitle="Allowlisted jobs / collections / services — read-only."
        right={
          <Btn variant="ghost" onClick={fetchPolicy} testId="policy-refresh">
            Refresh
          </Btn>
        }
      >
        {!policy ? (
          <div style={{ color: DIM, fontSize: 12 }}>—</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12 }}>
            <div>
              <div style={{ color: MUTED, fontSize: 10, textTransform: 'uppercase' }}>
                Writable Collections ({policy.writable_collections?.length || 0})
              </div>
              <div style={{ fontFamily: 'monospace', fontSize: 11, color: ACCENT_2, marginTop: 4 }}>
                {(policy.writable_collections || []).join(', ')}
              </div>
            </div>
            <div>
              <div style={{ color: MUTED, fontSize: 10, textTransform: 'uppercase' }}>
                Protected Collections ({policy.protected_collections?.length || 0})
              </div>
              <div style={{ fontFamily: 'monospace', fontSize: 11, color: WARN, marginTop: 4 }}>
                {(policy.protected_collections || []).join(', ')}
              </div>
            </div>
            <div>
              <div style={{ color: MUTED, fontSize: 10, textTransform: 'uppercase' }}>
                Allowed Jobs
              </div>
              <div style={{ marginTop: 4, fontFamily: 'monospace', fontSize: 11 }}>
                {Object.entries(policy.allowed_jobs || {}).map(([m, v]) => (
                  <div
                    key={m}
                    style={{
                      color: v.enabled ? ACCENT_2 : DIM,
                      padding: '2px 0',
                    }}
                  >
                    {v.enabled ? '●' : '○'} {m}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </Section>

      <Section
        testId="deploy-section"
        title="Deploy"
        subtitle="git pull --ff-only + py_compile + supervisor restart"
        right={
          <Btn
            variant="primary"
            onClick={deploy}
            disabled={deploying || !deployStatus?.ok}
            testId="deploy-btn"
          >
            {deploying ? 'Deploying…' : 'Pull + Restart'}
          </Btn>
        }
      >
        {!deployStatus ? (
          <div style={{ color: DIM, fontSize: 12 }}>—</div>
        ) : (
          <div style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div>
              <span style={{ color: MUTED }}>branch: </span>
              <code style={{ color: ACCENT, fontFamily: 'monospace' }}>
                {deployStatus.current_branch}
              </code>
            </div>
            <div>
              <span style={{ color: MUTED }}>head: </span>
              <code style={{ color: TEXT, fontFamily: 'monospace', fontSize: 11 }}>
                {deployStatus.current_commit_summary}
              </code>
            </div>
            <div>
              <span style={{ color: MUTED }}>dirty: </span>
              <span style={{ color: deployStatus.is_dirty ? BAD : ACCENT_2 }}>
                {deployStatus.is_dirty ? 'yes' : 'clean'}
              </span>
            </div>
            {deployStatus.is_dirty && (
              <div
                style={{
                  fontSize: 10,
                  color: WARN,
                  fontFamily: 'monospace',
                  background: SURFACE_2,
                  padding: 6,
                  borderRadius: 4,
                  maxHeight: 80,
                  overflow: 'auto',
                }}
              >
                {(deployStatus.dirty_files_preview || []).join('\n')}
              </div>
            )}
            <div>
              <span style={{ color: MUTED }}>backend: </span>
              <code
                style={{
                  color: deployStatus.backend_service_status?.includes('RUNNING') ? ACCENT_2 : WARN,
                  fontFamily: 'monospace',
                  fontSize: 11,
                }}
              >
                {(deployStatus.backend_service_status || '').trim().slice(0, 120)}
              </code>
            </div>
          </div>
        )}
      </Section>
    </div>
  );
}

// ── Root page ──────────────────────────────────────────────────────────────
export default function AdminTesting() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '');
  const [whoami, setWhoami] = useState(null);
  const [focusJobId, setFocusJobId] = useState(null);

  const authed = !!whoami;

  return (
    <div
      data-testid="admin-testing-page"
      style={{
        minHeight: '100vh',
        background: BG,
        color: TEXT,
        fontFamily: "'Inter', system-ui, sans-serif",
        padding: 20,
      }}
    >
      <div style={{ maxWidth: 1400, margin: '0 auto' }}>
        <div style={{ marginBottom: 16 }}>
          <h1
            data-testid="admin-testing-title"
            style={{
              fontSize: 26,
              margin: 0,
              fontWeight: 800,
              letterSpacing: -0.5,
              display: 'flex',
              alignItems: 'center',
              gap: 10,
            }}
          >
            <span style={{ color: ACCENT }}>●</span>
            Universal Historical Testing — Command Center
          </h1>
          <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>
            Private / unlinked · Drives /api/emergent-admin/* exclusively · Token never leaves localStorage.
          </div>
        </div>

        <TokenGate
          token={token}
          setToken={setToken}
          whoami={whoami}
          setWhoami={setWhoami}
          onAuthed={() => {}}
        />

        {authed ? (
          <>
            <StatSweepBuilder token={token} />

            <Section
              testId="job-runner-section"
              title="Job Runner"
              subtitle="Each card maps to one allowlisted Python module. Args are validated server-side against policy.ALLOWED_JOBS."
            >
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))',
                  gap: 12,
                }}
              >
                {Object.entries(JOB_SPECS).map(([m, spec]) => (
                  <JobCard
                    key={m}
                    moduleName={m}
                    spec={spec}
                    token={token}
                    onLaunched={(jid) => setFocusJobId(jid)}
                  />
                ))}
              </div>
            </Section>

            <JobsPanel token={token} focusJobId={focusJobId} setFocusJobId={setFocusJobId} />

            <PolicyAndDeploy token={token} />
          </>
        ) : (
          <div
            data-testid="admin-testing-locked"
            style={{
              padding: 40,
              textAlign: 'center',
              color: DIM,
              fontSize: 13,
              background: SURFACE,
              border: `1px dashed ${BORDER}`,
              borderRadius: 12,
            }}
          >
            🔒 Authenticate with the Emergent admin token to unlock the command center.
          </div>
        )}

        <div
          style={{
            marginTop: 28,
            fontSize: 10,
            color: DIM,
            textAlign: 'center',
            lineHeight: 1.6,
          }}
        >
          Talks to <code>{ADMIN}</code> · Token kept in <code>localStorage[{TOKEN_KEY}]</code> ·
          Audit-logged server-side per request.
        </div>
      </div>
    </div>
  );
}

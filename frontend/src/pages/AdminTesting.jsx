/* eslint-disable react-hooks/exhaustive-deps */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

/**
 * Admin Testing — Internal Quant/Research Terminal.
 *
 * Token-gated, unlinked. Talks only to /api/emergent-admin/*.
 * Token + pipeline + candidates kept in localStorage.
 *
 * Tabs:
 *   1. Workflow         — guided 7-step pipeline with auto-chaining
 *   2. Sweep            — per-stat-family threshold sweep (12 axes/family)
 *   3. Results          — auto-loaded aggregates, plain-English summary
 *   4. Candidates       — save/compare/export/mark-ready
 *   5. Models           — universal model registry across MLB/NBA/NFL
 *   6. Diagnostics      — preflight: deps, counts, warnings, fix-jobs
 */
const API = process.env.REACT_APP_BACKEND_URL;
const ADMIN = `${API}/api/emergent-admin`;

const BG = '#09090B', SURFACE = '#18181B', SURFACE_2 = '#0F0F11', SURFACE_3 = '#1F1F23';
const BORDER = '#27272A', BORDER_STRONG = '#3F3F46';
const MUTED = '#71717A', DIM = '#52525B', TEXT = '#FAFAFA';
const ACCENT = '#A78BFA', ACCENT_2 = '#34D399', ACCENT_3 = '#60A5FA';
const WARN = '#FBBF24', BAD = '#F87171';

const TOKEN_KEY = 'emergentAdminToken';
const PIPELINE_KEY = 'emergentAdminPipeline';
const CANDIDATES_KEY = 'emergentAdminCandidates';
const SWEEP_KEY = 'emergentAdminSweepConfig';
const REPLAY_COLL = 'sgo_propvision_full_pipeline_replay';
const REPLAY_DIFF_COLL = 'sgo_propvision_full_pipeline_replay_diff';
const GRID_RUNS_COLL = 'research_grid_runs';
const GRID_RESULTS_COLL = 'research_grid_results';

// ── HTTP ────────────────────────────────────────────────────────────
async function apiFetch(token, path, init = {}) {
  const headers = {
    'Content-Type': 'application/json',
    'X-Admin-Token': token, 'X-Agent-Id': 'admin-testing-ui',
    ...(init.headers || {}),
  };
  const res = await fetch(`${ADMIN}${path}`, { ...init, headers });
  const body = await res.text();
  let parsed;
  try { parsed = body ? JSON.parse(body) : {}; } catch { parsed = { _raw: body }; }
  if (!res.ok) {
    const err = new Error(parsed?.detail || parsed?.message || `HTTP ${res.status}`);
    err.status = res.status; err.body = parsed; throw err;
  }
  return parsed;
}

// ── helpers ─────────────────────────────────────────────────────────
const fmtPct = (v, d = 1) => v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(d)}%`;
const fmtNum = (v, d = 2) => v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(d);
const fmtInt = (v) => v == null || Number.isNaN(v) ? '—' : Number(v).toLocaleString();
const fmtTs  = (iso) => { if (!iso) return '—'; try { return new Date(iso).toLocaleString(); } catch { return String(iso); } };
const fmtDur = (a, b) => {
  if (!a) return '—';
  const t1 = new Date(a).getTime();
  const t2 = b ? new Date(b).getTime() : Date.now();
  const s = Math.max(0, Math.floor((t2 - t1) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};

// ── primitives ──────────────────────────────────────────────────────
function Section({ title, subtitle, right, children, testId, accent }) {
  return (
    <div data-testid={testId} style={{
      background: SURFACE, border: `1px solid ${BORDER}`,
      borderLeft: accent ? `3px solid ${accent}` : `1px solid ${BORDER}`,
      borderRadius: 12, padding: 18, marginBottom: 16,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, gap: 12 }}>
        <div>
          <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', letterSpacing: 0.7 }}>{title}</div>
          {subtitle && <div style={{ fontSize: 12, color: DIM, marginTop: 3 }}>{subtitle}</div>}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}
function Btn({ children, variant = 'default', testId, ...rest }) {
  const p = {
    default: { bg: BORDER, fg: TEXT, br: BORDER_STRONG },
    primary: { bg: ACCENT, fg: BG, br: ACCENT },
    success: { bg: ACCENT_2, fg: BG, br: ACCENT_2 },
    danger:  { bg: BAD, fg: BG, br: BAD },
    warn:    { bg: WARN, fg: BG, br: WARN },
    ghost:   { bg: 'transparent', fg: TEXT, br: BORDER },
  }[variant];
  return <button data-testid={testId} {...rest} style={{
    background: p.bg, color: p.fg, border: `1px solid ${p.br}`, borderRadius: 6,
    padding: '7px 12px', fontSize: 12, fontWeight: 600,
    cursor: rest.disabled ? 'not-allowed' : 'pointer', opacity: rest.disabled ? 0.5 : 1,
    ...(rest.style || {}),
  }}>{children}</button>;
}
function Input({ testId, ...rest }) {
  return <input data-testid={testId} {...rest} style={{
    background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 6,
    padding: '7px 10px', color: TEXT, fontSize: 12,
    fontFamily: 'ui-monospace, monospace', ...(rest.style || {}),
  }} />;
}
function Select({ testId, options = [], ...rest }) {
  return (
    <select data-testid={testId} {...rest} style={{
      background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 6,
      padding: '7px 10px', color: TEXT, fontSize: 12, ...(rest.style || {}),
    }}>
      {options.map((o) => typeof o === 'string'
        ? <option key={o} value={o}>{o}</option>
        : <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}
function Field({ label, children, hint }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      <span style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</span>
      {children}
      {hint && <span style={{ fontSize: 10, color: DIM }}>{hint}</span>}
    </label>
  );
}
function StatCard({ label, value, hint, color = TEXT, testId }) {
  return (
    <div data-testid={testId} style={{
      background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12,
    }}>
      <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', letterSpacing: 0.6 }}>{label}</div>
      <div style={{ fontSize: 22, color, fontWeight: 700, marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      {hint && <div style={{ fontSize: 10, color: DIM, marginTop: 4 }}>{hint}</div>}
    </div>
  );
}
function Badge({ children, color = MUTED }) {
  return <span style={{
    fontSize: 10, fontWeight: 700, color, background: `${color}1f`,
    padding: '2px 8px', borderRadius: 999, textTransform: 'uppercase', letterSpacing: 0.4,
  }}>{children}</span>;
}
const th = { padding: '6px 8px', textAlign: 'left', borderBottom: `1px solid ${BORDER}`, fontWeight: 600 };
const td = { padding: '6px 8px', color: TEXT, fontFamily: 'ui-monospace, monospace', fontSize: 11 };

// ── Token Gate + Lock ───────────────────────────────────────────────
function TokenGate({ token, setToken, whoami, setWhoami, locked, setLocked }) {
  const [input, setInput] = useState(token || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const validate = useCallback(async (cand) => {
    const t = (cand ?? input).trim();
    if (!t) { setErr('Token required'); return; }
    setBusy(true); setErr(null);
    try {
      const me = await apiFetch(t, '/auth/whoami');
      setWhoami(me); setToken(t);
      localStorage.setItem(TOKEN_KEY, t);
      toast.success(`Authed as ${me.agent_id || 'agent'}`);
    } catch (e) { setErr(e.message); toast.error(`Auth failed: ${e.message}`); }
    finally { setBusy(false); }
  }, [input]);
  useEffect(() => { if (token && !whoami && !locked) validate(token); }, []);
  return (
    <Section testId="admin-testing-token-section" title="Admin Token"
      subtitle="X-Admin-Token — validated via /auth/whoami. Stored only in localStorage."
      right={whoami && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span data-testid="admin-testing-whoami" style={{
            fontSize: 11, color: ACCENT_2, background: `${ACCENT_2}22`, padding: '4px 10px', borderRadius: 999,
          }}>● {whoami.agent_id} · {whoami.token_hash}</span>
          <Btn variant="warn" testId="admin-testing-lock-btn" onClick={() => {
            setLocked(true); setWhoami(null); toast.info('Page locked');
          }}>🔒 Lock Page</Btn>
          <Btn variant="ghost" testId="admin-testing-logout-btn" onClick={() => {
            localStorage.removeItem(TOKEN_KEY); setToken(''); setWhoami(null); setInput(''); setLocked(false);
            toast.info('Token cleared');
          }}>Clear</Btn>
        </div>
      )}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <Input testId="admin-testing-token-input" type="password" placeholder="EMERGENT_ADMIN_TOKEN"
          value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && validate()} style={{ flex: 1 }} autoComplete="off" />
        <Btn variant="primary" testId="admin-testing-validate-btn"
          onClick={() => { setLocked(false); validate(); }} disabled={busy || !input}>
          {busy ? 'Validating…' : whoami ? 'Re-validate' : 'Authenticate'}
        </Btn>
      </div>
      {err && <div data-testid="admin-testing-token-error" style={{ marginTop: 8, fontSize: 12, color: BAD }}>{err}</div>}
    </Section>
  );
}

function WarningBanner() {
  return (
    <div data-testid="admin-warning-banner" style={{
      background: '#3F1D1D', border: `1px solid ${BAD}`, color: BAD,
      padding: '10px 14px', borderRadius: 8, marginBottom: 14,
      fontSize: 12, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase',
      display: 'flex', alignItems: 'center', gap: 10,
    }}>
      <span style={{ fontSize: 18 }}>⚠</span>
      Private internal testing tool · all actions audit-logged server-side
    </div>
  );
}

// ── Universal sport adapters ────────────────────────────────────────
const SPORT_ADAPTERS = {
  MLB: {
    label: 'MLB',
    steps: {
      ingest:   { module: 'scripts.sgo.ingest_historical_player_stats',     league: 'MLB' },
      features: { module: 'scripts.sgo.build_historical_model_features',    league: 'MLB' },
      score:    { module: 'scripts.sgo.score_historical_with_live_mlb_hf',  league: 'MLB' },
      replay:   { module: 'scripts.sgo.historical_full_pipeline_replay',    league: 'MLB' },
      grid:     { module: 'scripts.sgo.historical_gate_replay_grid',        league: 'MLB' },
    },
    statFamilies: [
      'hits', 'total_bases', 'hits_runs_rbis', 'rbis', 'runs', 'home_runs',
      'singles', 'doubles', 'batter_strikeouts', 'pitcher_strikeouts',
      'pitcher_outs', 'earned_runs', 'hits_allowed', 'walks_allowed',
      'stolen_bases',
    ],
  },
  NBA: {
    label: 'NBA',
    steps: {
      ingest:   { module: 'scripts.sgo.ingest_historical_player_stats',     league: 'NBA' },
      features: { module: 'scripts.sgo.build_historical_model_features',    league: 'NBA' },
      score:    null, // no NBA score-historical job yet → flagged in Diagnostics
      replay:   null, // pending universal NBA replay adapter
      grid:     { module: 'scripts.research.grid_sweep',                    league: 'NBA' },
    },
    statFamilies: [
      'points', 'rebounds', 'assists', 'three_pointers_made',
      'steals', 'blocks', 'turnovers', 'pra', 'pr', 'pa', 'ra',
    ],
  },
  NFL: {
    label: 'NFL',
    steps: {
      ingest:   { module: 'scripts.sgo.ingest_historical_player_stats', league: 'NFL' },
      features: null,
      score:    null,
      replay:   null,
      grid:     null,
    },
    statFamilies: [
      'passing_yards', 'rushing_yards', 'receiving_yards', 'receptions',
      'passing_tds', 'rushing_tds', 'receiving_tds', 'interceptions',
    ],
  },
};

// ── Pipeline definition ─────────────────────────────────────────────
const PIPELINE_STEPS = [
  { key: 'ingest_stats',  label: '1. Backfill Stats',       stepKey: 'ingest',   skippable: true,
    purpose: 'Pulls historical player stats from SGO API.',
    next: 'After this completes, features can be built.' },
  { key: 'build_features',label: '2. Build Features',       stepKey: 'features', skippable: true,
    purpose: 'Builds pre-game model features for every prop.',
    next: 'Required before scoring.' },
  { key: 'score_model',   label: '3. Score Through Model',  stepKey: 'score',    skippable: true,
    purpose: 'Runs the live model over historical features.',
    next: 'Required before pipeline replay.' },
  { key: 'full_replay',   label: '4. Full Pipeline Replay', stepKey: 'replay',   skippable: false,
    purpose: 'Drives every prop through live PropVision scoring + gates.',
    next: 'Required before grid sweep.' },
  { key: 'grid_sweep',    label: '5. Gate Grid',            stepKey: 'grid',     skippable: false,
    purpose: 'Per-tier × per-stat_family threshold sweep.',
    next: 'Writes research_grid_results — Results tab will auto-populate.' },
  { key: 'view_results',  label: '6. View Results',         stepKey: null,       skippable: false,
    purpose: 'Auto-loads from research_grid_results + replay collection.',
    next: 'Pick winning configs and save as candidates.' },
  { key: 'save_candidate',label: '7. Save Candidate',       stepKey: null,       skippable: false,
    purpose: 'Persist a config you want to act on.',
    next: 'Mark Ready to write to emergent_candidate_configs.' },
];

function loadPipeline() { try { const r = localStorage.getItem(PIPELINE_KEY); return r ? JSON.parse(r) : null; } catch { return null; } }
function savePipeline(p) { p === null ? localStorage.removeItem(PIPELINE_KEY) : localStorage.setItem(PIPELINE_KEY, JSON.stringify(p)); }

function buildStepArgs(sportKey, stepKey, cfg) {
  const adapter = SPORT_ADAPTERS[sportKey];
  const spec = adapter?.steps?.[stepKey];
  if (!spec) return null;
  const a = ['--league', spec.league, '--start', cfg.start, '--end', cfg.end];
  if (stepKey === 'grid') a.push('--min-bets', String(cfg.minBets || 20));
  if (stepKey === 'replay') {
    if (cfg.excludeFamilies) a.push('--exclude-stat-family', cfg.excludeFamilies);
    // SSOT audit toggle — default ON, size 200. Each run snapshots
    // pre-existing legacy rows and writes a per-row diff doc into
    // sgo_propvision_full_pipeline_replay_diff so the Results tab can
    // prove that the new run is using the production SSOT pipeline
    // and not legacy inlined gates.
    if (cfg.sampleDiffEnabled && cfg.sampleDiffSize > 0) {
      a.push('--sample-diff', String(cfg.sampleDiffSize));
    }
    if (cfg.gatePath && cfg.gatePath !== 'universal') {
      a.push('--gate-path', cfg.gatePath);
    }
  }
  return { module: spec.module, args: a };
}

// ── Workflow tab ────────────────────────────────────────────────────
function WorkflowTab({ token, onPipelineFinished }) {
  const [config, setConfig] = useState({
    sport: 'MLB', start: '', end: '', minBets: 20,
    excludeFamilies: 'fantasy_score',
    skip: { ingest_stats: true, build_features: true, score_model: true },
    // SSOT audit defaults — diff ON, size 200, universal gate path
    sampleDiffEnabled: true,
    sampleDiffSize: 200,
    gatePath: 'universal',
  });
  const [pipeline, setPipeline] = useState(loadPipeline());
  const [tail, setTail] = useState([]);
  const pollRef = useRef(null);

  useEffect(() => { savePipeline(pipeline); }, [pipeline]);

  useEffect(() => {
    if (!pipeline || !token) { if (pollRef.current) clearInterval(pollRef.current); return; }
    const drive = async () => {
      const cur = pipeline.steps.findIndex(s => s.status === 'running' || s.status === 'queued');
      if (cur < 0) {
        const next = pipeline.steps.findIndex(s => s.status === 'pending');
        if (next < 0) {
          setPipeline(p => p && p.status !== 'completed'
            ? { ...p, status: 'completed', finished_at: new Date().toISOString() } : p);
          if (pollRef.current) clearInterval(pollRef.current);
          if (onPipelineFinished) onPipelineFinished();
          return;
        }
        const stepDef = PIPELINE_STEPS[next];
        if (!stepDef.stepKey) {
          // virtual
          setPipeline(p => {
            const steps = [...p.steps];
            steps[next] = { ...steps[next], status: 'succeeded', finished_at: new Date().toISOString() };
            return { ...p, steps };
          });
          return;
        }
        const built = buildStepArgs(pipeline.config.sport, stepDef.stepKey, pipeline.config);
        if (!built) {
          setPipeline(p => {
            const steps = [...p.steps];
            steps[next] = { ...steps[next], status: 'failed',
              error: `No job mapped for ${pipeline.config.sport}/${stepDef.stepKey}. See Diagnostics.` };
            return { ...p, status: 'halted', steps };
          });
          toast.error(`No ${pipeline.config.sport} job for ${stepDef.label}`);
          return;
        }
        try {
          const res = await apiFetch(token, '/jobs/run', {
            method: 'POST', body: JSON.stringify(built),
          });
          setPipeline(p => {
            const steps = [...p.steps];
            steps[next] = {
              ...steps[next], status: 'queued', job_id: res.job_id,
              module: built.module, args: built.args, started_at: new Date().toISOString(),
            };
            return { ...p, steps };
          });
          toast.info(`▶ ${stepDef.label}`);
        } catch (e) {
          setPipeline(p => {
            const steps = [...p.steps];
            steps[next] = { ...steps[next], status: 'failed', error: e.message };
            return { ...p, status: 'halted', steps };
          });
          toast.error(`Step failed: ${e.message}`);
        }
        return;
      }      // poll active
      const active = pipeline.steps[cur];
      if (!active.job_id) return;
      try {
        const j = await apiFetch(token, `/jobs/${active.job_id}`);
        const job = j.job;
        try { const lg = await apiFetch(token, `/jobs/${active.job_id}/log?tail=200`);
          setTail(lg.lines || []);
          // Surface failure context onto the active step doc so the
          // pipeline visualization shows it even when the user is not
          // looking at the tail panel.
          if (lg.status && lg.status !== 'running' && lg.status !== 'queued') {
            setPipeline(p => {
              if (!p) return p;
              const steps = [...p.steps];
              const idx = steps.findIndex(s => s.job_id === active.job_id);
              if (idx >= 0 && !steps[idx].tail_preview) {
                steps[idx] = {
                  ...steps[idx],
                  tail_preview: lg.tail_preview || lg.lines?.slice(-30) || [],
                  spawn_error: lg.error || null,
                  spawn_traceback: lg.traceback || null,
                };
              }
              return { ...p, steps };
            });
          }
        } catch {}
        if (job.status === 'succeeded') {
          setPipeline(p => {
            const steps = [...p.steps];
            steps[cur] = { ...steps[cur], status: 'succeeded', finished_at: new Date().toISOString(), exit_code: job.exit_code };
            return { ...p, steps };
          });
          toast.success(`✓ ${PIPELINE_STEPS[cur].label}`);
        } else if (['failed','errored','cancelled'].includes(job.status)) {
          setPipeline(p => {
            const steps = [...p.steps];
            steps[cur] = { ...steps[cur], status: job.status, finished_at: new Date().toISOString(), exit_code: job.exit_code, error: job.error };
            return { ...p, status: 'halted', steps };
          });
          toast.error(`✗ ${active.key} ${job.status}`);
        } else if (steps_status_changed(pipeline.steps[cur].status, job.status)) {
          setPipeline(p => {
            const steps = [...p.steps];
            steps[cur] = { ...steps[cur], status: job.status };
            return { ...p, steps };
          });
        }
      } catch (e) { console.error('[pipe] poll', e); }
    };
    drive();
    pollRef.current = setInterval(drive, 2500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [pipeline?.id, pipeline?.status, token]);

  const start = () => {
    if (!config.start || !config.end) { toast.error('Start and end dates required'); return; }
    const steps = PIPELINE_STEPS.map(s => ({
      key: s.key,
      status: (s.skippable && config.skip[s.key]) ? 'skipped' : 'pending',
    }));
    setPipeline({ id: `pipe_${Date.now()}`, config: { ...config },
      status: 'running', steps, started_at: new Date().toISOString() });
    setTail([]);
    toast.success(`Pipeline started · ${config.sport} ${config.start}..${config.end}`);
  };
  const reset = () => { if (!window.confirm('Discard current pipeline?')) return;
    setPipeline(null); setTail([]); savePipeline(null); };
  const cancel = async () => {
    if (!pipeline) return;
    const a = pipeline.steps.find(s => s.status === 'running' || s.status === 'queued');
    if (!a?.job_id) return;
    try { await apiFetch(token, `/jobs/${a.job_id}/cancel`, { method: 'POST', body: JSON.stringify({ confirm: true }) }); toast.success('Cancel sent'); }
    catch (e) { toast.error(`Cancel failed: ${e.message}`); }
  };
  const active = pipeline?.steps.find(s => s.status === 'running' || s.status === 'queued');
  const adapter = SPORT_ADAPTERS[config.sport];

  return (
    <Section testId="pipeline-section" accent={ACCENT} title="Guided Workflow"
      subtitle="Single click runs the entire historical → grid pipeline using the sport's adapter."
      right={pipeline && (
        <div style={{ display: 'flex', gap: 8 }}>
          {active && <Btn variant="danger" onClick={cancel} testId="pipeline-cancel">Cancel</Btn>}
          <Btn variant="ghost" onClick={reset} testId="pipeline-reset">Reset</Btn>
        </div>
      )}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 10, marginBottom: 14 }}>
        <Field label="Sport">
          <Select testId="pipe-sport" value={config.sport}
            onChange={(e) => setConfig({ ...config, sport: e.target.value })}
            options={Object.keys(SPORT_ADAPTERS)} />
        </Field>
        <Field label="Start (YYYY-MM-DD)"><Input testId="pipe-start" value={config.start}
          onChange={(e) => setConfig({ ...config, start: e.target.value })} placeholder="2026-06-01" /></Field>
        <Field label="End (YYYY-MM-DD)"><Input testId="pipe-end" value={config.end}
          onChange={(e) => setConfig({ ...config, end: e.target.value })} placeholder="2026-06-30" /></Field>
        <Field label="Min bets / cell"><Input testId="pipe-minbets" type="number" value={config.minBets}
          onChange={(e) => setConfig({ ...config, minBets: parseInt(e.target.value || '0', 10) })} /></Field>
        <Field label="Exclude families"><Input testId="pipe-excl" value={config.excludeFamilies}
          onChange={(e) => setConfig({ ...config, excludeFamilies: e.target.value })} placeholder="fantasy_score,points" /></Field>
      </div>

      {/* Adapter coverage hint */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14, fontSize: 11 }}>
        {Object.entries(adapter.steps).map(([k, v]) => (
          <Badge key={k} color={v ? ACCENT_2 : BAD}>{k}: {v ? '✓' : '✗ no adapter'}</Badge>
        ))}
      </div>

      {/* SSOT audit controls — proves production pipeline path is in use */}
      <div data-testid="ssot-audit-controls" style={{
        background: SURFACE_2, border: `1px solid ${ACCENT_2}`,
        borderLeft: `3px solid ${ACCENT_2}`,
        borderRadius: 6, padding: 10, marginBottom: 14,
      }}>
        <div style={{ fontSize: 10, color: ACCENT_2, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>
          SSOT Audit · Replay step (default ON)
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <label data-testid="ssot-diff-enable" style={{
            display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
            fontSize: 12, color: config.sampleDiffEnabled ? ACCENT_2 : MUTED,
          }}>
            <input type="checkbox" checked={config.sampleDiffEnabled}
              onChange={(e) => setConfig({ ...config, sampleDiffEnabled: e.target.checked })} />
            Sample-diff audit
          </label>
          <Field label="Sample size">
            <Input testId="ssot-diff-size" type="number" min="0" max="5000"
              value={config.sampleDiffSize}
              disabled={!config.sampleDiffEnabled}
              onChange={(e) => setConfig({ ...config, sampleDiffSize: parseInt(e.target.value || '0', 10) })}
              style={{ width: 90 }} />
          </Field>
          <Field label="Gate path">
            <Select testId="ssot-gate-path" value={config.gatePath}
              onChange={(e) => setConfig({ ...config, gatePath: e.target.value })}
              options={[
                { value: 'universal', label: 'universal (SH+FL+WZ via SSOT)' },
                { value: 'legacy_wz', label: 'legacy_wz (WZ-only, byte-identical)' },
              ]} />
          </Field>
          <div style={{ fontSize: 10, color: DIM, flex: 1, minWidth: 220 }}>
            Snapshots {config.sampleDiffSize} pre-existing legacy rows BEFORE the run,
            then writes per-row diffs (legacy decision vs SSOT decision) to
            <code style={{ color: ACCENT_2, marginLeft: 4 }}>{REPLAY_DIFF_COLL}</code>.
            Results tab surfaces the audit summary.
          </div>
        </div>
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Skip prep steps:</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {PIPELINE_STEPS.filter(s => s.skippable).map(s => (
            <label key={s.key} data-testid={`pipe-skip-${s.key}`} style={{
              background: config.skip[s.key] ? `${WARN}22` : SURFACE_2,
              border: `1px solid ${config.skip[s.key] ? WARN : BORDER}`,
              color: config.skip[s.key] ? WARN : MUTED,
              borderRadius: 6, padding: '5px 10px', fontSize: 11, cursor: 'pointer',
            }}>
              <input type="checkbox" checked={!!config.skip[s.key]}
                onChange={(e) => setConfig({ ...config, skip: { ...config.skip, [s.key]: e.target.checked } })}
                style={{ marginRight: 6, verticalAlign: 'middle' }} />
              skip {s.label.split('. ')[1]}
            </label>
          ))}
        </div>
      </div>

      {!pipeline && (
        <>
          {/* Live coverage check — auto-loaded when start/end set */}
          {config.start && config.end && (
            <WarehouseCoverage token={token}
              defaultSport={config.sport}
              defaultStart={config.start}
              defaultEnd={config.end}
              compact />
          )}
          <Btn variant="primary" testId="pipeline-start" onClick={start}>▶ Run Full {config.sport} Replay Pipeline</Btn>
        </>
      )}

      {pipeline && (
        <>
          <div data-testid="pipeline-steps" style={{
            display: 'grid', gridTemplateColumns: `repeat(${PIPELINE_STEPS.length}, 1fr)`, gap: 6, marginBottom: 14,
          }}>
            {PIPELINE_STEPS.map((s, i) => {
              const st = pipeline.steps[i];
              const c = st.status === 'succeeded' ? ACCENT_2
                : ['running','queued'].includes(st.status) ? ACCENT
                : ['failed','errored','cancelled'].includes(st.status) ? BAD
                : st.status === 'skipped' ? DIM : MUTED;
              return (
                <div key={s.key} data-testid={`pipe-step-${s.key}`} style={{
                  background: SURFACE_2, border: `1px solid ${c === MUTED ? BORDER : c}`,
                  borderLeft: `3px solid ${c}`, borderRadius: 6, padding: 8,
                }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: c, textTransform: 'uppercase' }}>{st.status}</div>
                  <div style={{ fontSize: 11, color: TEXT, fontWeight: 600, marginTop: 4 }}>{s.label}</div>
                  <div style={{ fontSize: 9, color: DIM, marginTop: 4, fontFamily: 'monospace' }}>
                    {st.job_id ? `${st.job_id.slice(0,8)} · ${fmtDur(st.started_at, st.finished_at)}` :
                      st.status === 'skipped' ? '(skipped)' : ''}
                  </div>
                  <div style={{ fontSize: 10, color: c === ACCENT ? ACCENT : MUTED, marginTop: 4, lineHeight: 1.3 }}>
                    {st.status === 'succeeded' ? s.next : s.purpose}
                  </div>
                  {st.error && <div style={{ fontSize: 10, color: BAD, marginTop: 4 }}>{String(st.error).slice(0, 100)}</div>}
                  {st.tail_preview && st.tail_preview.length > 0
                    && ['failed','errored','cancelled'].includes(st.status) && (
                    <details style={{ marginTop: 6 }} data-testid={`pipe-tail-${s.key}`}>
                      <summary style={{ fontSize: 10, color: BAD, cursor: 'pointer' }}>
                        last {st.tail_preview.length} lines (click to expand)
                      </summary>
                      <pre style={{
                        fontSize: 9, color: '#A1A1AA',
                        background: '#000', padding: 6, borderRadius: 4,
                        margin: '4px 0 0', maxHeight: 200, overflow: 'auto',
                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                        fontFamily: 'ui-monospace, monospace',
                      }}>{st.tail_preview.join('')}</pre>
                    </details>
                  )}
                </div>
              );
            })}
          </div>

          {active && (
            <div data-testid="pipeline-active-log" style={{
              background: '#000', border: `1px solid ${BORDER}`, borderRadius: 6, overflow: 'hidden',
            }}>
              <div style={{ padding: '6px 10px', borderBottom: `1px solid ${BORDER}`, fontSize: 10, color: MUTED, fontFamily: 'monospace' }}>
                ● live · {PIPELINE_STEPS.find(s => s.key === active.key).label} · {active.job_id?.slice(0,8)} · {fmtDur(active.started_at)}
              </div>
              <pre style={{
                margin: 0, padding: 10, fontSize: 10, color: '#A1A1AA', fontFamily: 'ui-monospace, monospace',
                maxHeight: 200, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>{tail.length ? tail.slice(-50).join('') : '(starting…)'}</pre>
            </div>
          )}

          {pipeline.status === 'completed' && (
            <div style={{ marginTop: 12, padding: 10, background: `${ACCENT_2}1a`, border: `1px solid ${ACCENT_2}`, borderRadius: 6, fontSize: 12, color: ACCENT_2 }}>
              ✓ Pipeline complete. Open the Results tab.
            </div>
          )}
          {pipeline.status === 'halted' && (
            <div style={{ marginTop: 12, padding: 10, background: `${BAD}1a`, border: `1px solid ${BAD}`, borderRadius: 6, fontSize: 12, color: BAD }}>
              ✗ Pipeline halted. Run Diagnostics tab to identify the blocker, then Reset and re-run.
            </div>
          )}
        </>
      )}
    </Section>
  );
}
function steps_status_changed(a, b) { return a !== b; }

// ── Sweep tab (Per-Stat-Family threshold builder, 12 axes) ──────────
const SWEEP_AXES = [
  { key: 'hr_l20_min',              label: 'HR L20 min',         step: 0.05, default: 0.70, kind: 'pct' },
  { key: 'hr_l10_min',              label: 'HR L10 min',         step: 0.05, default: 0.65, kind: 'pct' },
  { key: 'hr_l5_min',               label: 'HR L5 min',          step: 0.05, default: 0.60, kind: 'pct' },
  { key: 'cv_max',                  label: 'CV max',             step: 0.05, default: 0.90, kind: 'num' },
  { key: 'edge_min',                label: 'Edge min',           step: 0.01, default: 0.05, kind: 'pct' },
  { key: 'tp_min',                  label: 'TP min',             step: 0.05, default: 0.50, kind: 'pct' },
  { key: 'projection_margin_min',   label: 'Proj margin min',    step: 0.10, default: 0.50, kind: 'num' },
  { key: 'vision_score_min',        label: 'Vision score min',   step: 5,    default: 60,   kind: 'num' },
  { key: 'sharp_book_count_min',    label: 'Sharp books min',    step: 1,    default: 2,    kind: 'num' },
  { key: 'devig_book_count_min',    label: 'Devig books min',    step: 1,    default: 3,    kind: 'num' },
  { key: 'market_width_max',        label: 'Market width max',   step: 0.01, default: 0.20, kind: 'num' },
  { key: 'consensus_disagreement_max', label: 'Consensus disag max', step: 0.01, default: 0.15, kind: 'num' },
];

function loadSweep() {
  try { const r = localStorage.getItem(SWEEP_KEY); return r ? JSON.parse(r) : null; } catch { return null; }
}
function defaultSweep(sport) {
  const fams = {};
  for (const f of SPORT_ADAPTERS[sport].statFamilies) {
    fams[f] = { enabled: false, thresholds: {} };
    for (const ax of SWEEP_AXES) fams[f].thresholds[ax.key] = ax.default;
  }
  return {
    sport, start: '', end: '', league: SPORT_ADAPTERS[sport].steps.grid?.league || sport,
    tiers: { safe_haven: true, front_lines: true, war_zone: true },
    odds_buckets: ['odds_-200_-100','odds_-100_-0','odds_+0_+150','odds_+150_+300']
      .reduce((m, k) => { m[k] = true; return m; }, {}),
    min_bets: 20, families: fams,
  };
}

function SweepTab({ token }) {
  const [draft, setDraft] = useState(() => loadSweep() || defaultSweep('MLB'));
  const [busy, setBusy] = useState(false);

  // On first mount, ask the backend if a "testing default" preset was
  // promoted from the Auto-Optimizer. If so, apply its best thresholds
  // as the universal default in the per-family grid (user can still
  // toggle individual families on/off and tweak).
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(token, '/optimizer/_meta/testing_default');
        if (cancelled || !res?.doc?.best) return;
        const b = res.doc.best;
        // Seed every family's threshold row with the optimizer's best
        // values. Non-destructive: existing localStorage draft wins.
        setDraft(p => {
          if (Object.values(p.families).some(f => f.enabled)) return p;
          const families = { ...p.families };
          for (const fam of Object.keys(families)) {
            families[fam] = {
              ...families[fam],
              thresholds: {
                ...families[fam].thresholds,
                hr_l20_min: b.hr_l20_min ?? families[fam].thresholds.hr_l20_min,
                hr_l10_min: b.hr_l10_min ?? families[fam].thresholds.hr_l10_min,
                hr_l5_min:  b.hr_l5_min  ?? families[fam].thresholds.hr_l5_min,
                cv_max:     b.cv_max     ?? families[fam].thresholds.cv_max,
                edge_min:   b.edge_min   ?? families[fam].thresholds.edge_min,
                tp_min:     b.tp_min     ?? families[fam].thresholds.tp_min,
              },
            };
          }
          return { ...p, families };
        });
        toast.info(`Sweep defaults preloaded from optimizer run ${res.doc.source_run_id?.slice?.(0, 12) || ''}`);
      } catch (e) {
        // soft-fail — no preset is fine
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  useEffect(() => { localStorage.setItem(SWEEP_KEY, JSON.stringify(draft)); }, [draft]);

  const setSport = (sport) => setDraft(defaultSweep(sport));
  const upd = (patch) => setDraft(p => ({ ...p, ...patch }));
  const updFamily = (fam, patch) => setDraft(p => ({
    ...p, families: { ...p.families, [fam]: { ...p.families[fam], ...patch } } }));
  const updFamThresh = (fam, k, v) => setDraft(p => ({
    ...p, families: { ...p.families, [fam]: { ...p.families[fam],
      thresholds: { ...p.families[fam].thresholds, [k]: v } } } }));

  const enabledFamilies = Object.entries(draft.families).filter(([, v]) => v.enabled);

  const launch = async () => {
    if (!draft.start || !draft.end) { toast.error('Start and end dates required'); return; }
    if (!enabledFamilies.length) { toast.error('Enable at least one stat family'); return; }
    setBusy(true);
    try {
      const payload = {
        kind: 'admin_testing_sweep_config',
        scope: `${draft.sport}:${draft.start}..${draft.end}`,
        config: {
          sport: draft.sport,
          tiers: Object.entries(draft.tiers).filter(([, v]) => v).map(([k]) => k),
          odds_buckets: Object.entries(draft.odds_buckets).filter(([, v]) => v).map(([k]) => k),
          min_bets: draft.min_bets,
          per_stat_family: enabledFamilies.map(([fam, cfg]) => ({ stat_family: fam, ...cfg.thresholds })),
        },
        note: 'Saved from /admin/testing Sweep tab',
      };
      try { await apiFetch(token, '/configs/draft', { method: 'POST', body: JSON.stringify(payload) }); }
      catch (e) { console.warn('config draft soft-fail', e); }
      const args = ['--league', draft.league, '--start', draft.start, '--end', draft.end, '--min-bets', String(draft.min_bets || 20)];
      const grid = SPORT_ADAPTERS[draft.sport].steps.grid;
      if (!grid) { toast.error(`No grid job adapter for ${draft.sport}`); setBusy(false); return; }
      const res = await apiFetch(token, '/jobs/run', { method: 'POST', body: JSON.stringify({ module: grid.module, args }) });
      toast.success(`Grid sweep queued · ${(res.job_id || '').slice(0,8)}`);
    } catch (e) { toast.error(`Sweep failed: ${e.message}`); }
    finally { setBusy(false); }
  };

  const adapter = SPORT_ADAPTERS[draft.sport];

  return (
    <Section testId="sweep-section" accent={ACCENT_3} title="Per-Stat-Family Sweep Builder"
      subtitle="Independent thresholds per stat family. 12 axes per family. Saved to candidate_gate_configs on launch."
      right={
        <Btn variant="primary" testId="sweep-launch" onClick={launch} disabled={busy || !token}>
          {busy ? 'Launching…' : 'Launch Grid Sweep'}
        </Btn>
      }>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 10, marginBottom: 14 }}>
        <Field label="Sport">
          <Select testId="sweep-sport" value={draft.sport} onChange={(e) => setSport(e.target.value)}
            options={Object.keys(SPORT_ADAPTERS)} />
        </Field>
        <Field label="Start"><Input testId="sweep-start" value={draft.start}
          onChange={(e) => upd({ start: e.target.value })} placeholder="YYYY-MM-DD" /></Field>
        <Field label="End"><Input testId="sweep-end" value={draft.end}
          onChange={(e) => upd({ end: e.target.value })} placeholder="YYYY-MM-DD" /></Field>
        <Field label="Min bets / cell"><Input testId="sweep-minbets" type="number" value={draft.min_bets}
          onChange={(e) => upd({ min_bets: parseInt(e.target.value || '0', 10) })} /></Field>
      </div>

      <div style={{ display: 'flex', gap: 24, marginBottom: 14, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Tiers</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {['safe_haven','front_lines','war_zone'].map(t => (
              <label key={t} data-testid={`sweep-tier-${t}`} style={{
                background: draft.tiers[t] ? `${ACCENT}22` : SURFACE_2,
                border: `1px solid ${draft.tiers[t] ? ACCENT : BORDER}`,
                color: draft.tiers[t] ? ACCENT : MUTED,
                borderRadius: 999, padding: '5px 12px', fontSize: 11, cursor: 'pointer', textTransform: 'uppercase',
              }}>
                <input type="checkbox" checked={!!draft.tiers[t]}
                  onChange={(e) => upd({ tiers: { ...draft.tiers, [t]: e.target.checked } })}
                  style={{ display: 'none' }} />
                {t.replace('_', ' ')}
              </label>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Odds Buckets</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {Object.keys(draft.odds_buckets).map(b => (
              <label key={b} data-testid={`sweep-odds-${b}`} style={{
                background: draft.odds_buckets[b] ? `${ACCENT_2}22` : SURFACE_2,
                border: `1px solid ${draft.odds_buckets[b] ? ACCENT_2 : BORDER}`,
                color: draft.odds_buckets[b] ? ACCENT_2 : MUTED,
                borderRadius: 999, padding: '5px 10px', fontSize: 10, fontFamily: 'monospace', cursor: 'pointer',
              }}>
                <input type="checkbox" checked={!!draft.odds_buckets[b]}
                  onChange={(e) => upd({ odds_buckets: { ...draft.odds_buckets, [b]: e.target.checked } })}
                  style={{ display: 'none' }} />
                {b.replace('odds_', '')}
              </label>
            ))}
          </div>
        </div>
      </div>

      <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>
        Per-Family Thresholds — {adapter.statFamilies.length} families × {SWEEP_AXES.length} axes
      </div>
      <div data-testid="sweep-family-grid" style={{
        background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, overflow: 'auto', maxHeight: 480,
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
          <thead style={{ position: 'sticky', top: 0, background: SURFACE, zIndex: 1 }}>
            <tr style={{ color: DIM, textTransform: 'uppercase' }}>
              <th style={{ ...th, width: 24 }}></th>
              <th style={{ ...th, minWidth: 160 }}>Stat Family</th>
              {SWEEP_AXES.map(a => <th key={a.key} style={th}>{a.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {adapter.statFamilies.map(fam => {
              const cfg = draft.families[fam];
              if (!cfg) return null;
              return (
                <tr key={fam} data-testid={`sweep-fam-row-${fam}`}
                  style={{ borderTop: `1px solid ${BORDER}`, background: cfg.enabled ? `${ACCENT}0a` : 'transparent' }}>
                  <td style={td}>
                    <input type="checkbox" data-testid={`sweep-fam-enable-${fam}`} checked={cfg.enabled}
                      onChange={(e) => updFamily(fam, { enabled: e.target.checked })} />
                  </td>
                  <td style={{ ...td, color: cfg.enabled ? TEXT : DIM, fontFamily: 'monospace', fontWeight: 600 }}>{fam}</td>
                  {SWEEP_AXES.map(a => (
                    <td key={a.key} style={td}>
                      <input type="number" step={a.step} value={cfg.thresholds[a.key]}
                        disabled={!cfg.enabled}
                        onChange={(e) => updFamThresh(fam, a.key, parseFloat(e.target.value))}
                        data-testid={`sweep-${fam}-${a.key}`}
                        style={{
                          width: 64, background: SURFACE_3, border: `1px solid ${BORDER}`,
                          borderRadius: 4, padding: '3px 5px', color: TEXT, fontSize: 10,
                          opacity: cfg.enabled ? 1 : 0.4, fontFamily: 'monospace',
                        }} />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 10, fontSize: 11, color: DIM }}>
        {enabledFamilies.length} families enabled · backend grid_sweep script honors hr_l20/hr_l5/cv/edge/tp directly;
        extra axes (vision_score, sharp_book_count, market_width, consensus_disagreement, projection_margin) are
        persisted on the candidate config doc and used for downstream filtering — see Results &amp; Diagnostics tabs.
      </div>
    </Section>
  );
}

// ── SSOT Diff Summary (Results tab) ─────────────────────────────────
function DiffSummarySection({ token }) {
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState(null);
  const [stats, setStats] = useState(null);
  const [legacyReasons, setLegacyReasons] = useState([]);
  const [ssotReasons, setSsotReasons] = useState([]);
  const [examples, setExamples] = useState([]);
  const [loading, setLoading] = useState(false);

  // Fetch list of diff runs
  const fetchRuns = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiFetch(token, `/collections/${REPLAY_DIFF_COLL}/aggregate`, {
        method: 'POST',
        body: JSON.stringify({
          pipeline: [
            { $group: {
                _id: '$diff_run_id',
                n: { $sum: 1 },
                emitted: { $max: '$diff_emitted_at' },
                deltas: { $sum: { $cond: ['$tier_delta', 1, 0] } },
            } },
            { $sort: { emitted: -1 } },
            { $limit: 30 },
          ], limit: 30,
        }),
      });
      setRuns(res.docs || []);
      if (!selected && res.docs?.length) setSelected(res.docs[0]._id);
    } catch (e) {
      // soft-fail — the collection may not exist yet
      console.warn('[diff-summary] list:', e.message);
    }
  }, [token, selected]);
  useEffect(() => { fetchRuns(); }, [fetchRuns]);

  // Fetch detailed stats for the selected run
  const fetchStats = useCallback(async () => {
    if (!token || !selected) return;
    setLoading(true);
    try {
      const flip = (tier) => ({
        $cond: [{ $ne: [
          `$legacy_inlined_gates.${tier}_pass`,
          `$ssot_production_runner.${tier}_pass`,
        ] }, 1, 0],
      });
      const promotions = (tier) => ({
        $cond: [{ $and: [
          { $eq: [`$ssot_production_runner.${tier}_pass`, true] },
          { $ne:  [`$legacy_inlined_gates.${tier}_pass`, true] },
        ] }, 1, 0],
      });
      const demotions = (tier) => ({
        $cond: [{ $and: [
          { $eq: [`$legacy_inlined_gates.${tier}_pass`, true] },
          { $ne:  [`$ssot_production_runner.${tier}_pass`, true] },
        ] }, 1, 0],
      });
      const [statsRes, legacyReasonsRes, ssotReasonsRes, examplesRes] = await Promise.all([
        // aggregate counts
        apiFetch(token, `/collections/${REPLAY_DIFF_COLL}/aggregate`, {
          method: 'POST',
          body: JSON.stringify({
            pipeline: [
              { $match: { diff_run_id: selected } },
              { $group: {
                  _id: null,
                  n: { $sum: 1 },
                  tier_delta: { $sum: { $cond: ['$tier_delta', 1, 0] } },
                  sh_flipped: { $sum: flip('safe_haven') },
                  fl_flipped: { $sum: flip('front_lines') },
                  wz_flipped: { $sum: flip('war_zone') },
                  sh_promoted: { $sum: promotions('safe_haven') },
                  fl_promoted: { $sum: promotions('front_lines') },
                  wz_promoted: { $sum: promotions('war_zone') },
                  sh_demoted: { $sum: demotions('safe_haven') },
                  fl_demoted: { $sum: demotions('front_lines') },
                  wz_demoted: { $sum: demotions('war_zone') },
                  missing_in_ssot: { $sum: { $cond: [
                    { $eq: ['$ssot_production_runner._missing', true] }, 1, 0,
                  ] } },
              } },
            ], limit: 1,
          }),
        }),
        // reason-code histogram (legacy side)
        apiFetch(token, `/collections/${REPLAY_DIFF_COLL}/aggregate`, {
          method: 'POST',
          body: JSON.stringify({
            pipeline: [
              { $match: { diff_run_id: selected } },
              { $project: { reasons: { $concatArrays: [
                { $ifNull: ['$legacy_inlined_gates.safe_haven_failed_reasons', []] },
                { $ifNull: ['$legacy_inlined_gates.front_lines_failed_reasons', []] },
                { $ifNull: ['$legacy_inlined_gates.war_zone_failed_reasons', []] },
              ] } } },
              { $unwind: '$reasons' },
              { $group: { _id: '$reasons', n: { $sum: 1 } } },
              { $sort: { n: -1 } },
              { $limit: 25 },
            ], limit: 25,
          }),
        }),
        // reason-code histogram (SSOT side)
        apiFetch(token, `/collections/${REPLAY_DIFF_COLL}/aggregate`, {
          method: 'POST',
          body: JSON.stringify({
            pipeline: [
              { $match: { diff_run_id: selected } },
              { $project: { reasons: { $concatArrays: [
                { $ifNull: ['$ssot_production_runner.safe_haven_failed_reasons', []] },
                { $ifNull: ['$ssot_production_runner.front_lines_failed_reasons', []] },
                { $ifNull: ['$ssot_production_runner.war_zone_failed_reasons', []] },
              ] } } },
              { $unwind: '$reasons' },
              { $group: { _id: '$reasons', n: { $sum: 1 } } },
              { $sort: { n: -1 } },
              { $limit: 25 },
            ], limit: 25,
          }),
        }),
        // examples of changed decisions (tier_delta=true)
        apiFetch(token, `/collections/${REPLAY_DIFF_COLL}/find`, {
          method: 'POST',
          body: JSON.stringify({
            filter: { diff_run_id: selected, tier_delta: true },
            limit: 20,
            sort: { 'key.game_date': -1 },
          }),
        }),
      ]);
      setStats((statsRes.docs && statsRes.docs[0]) || null);
      setLegacyReasons(legacyReasonsRes.docs || []);
      setSsotReasons(ssotReasonsRes.docs || []);
      setExamples(examplesRes.docs || []);
    } catch (e) {
      toast.error(`Diff stats: ${e.message}`);
    } finally { setLoading(false); }
  }, [token, selected]);
  useEffect(() => { fetchStats(); }, [fetchStats]);

  // Build a unique-to-each side reason set for plain-English display
  const reasonOverlay = useMemo(() => {
    const lset = new Set(legacyReasons.map(r => r._id));
    const sset = new Set(ssotReasons.map(r => r._id));
    return {
      onlyLegacy: legacyReasons.filter(r => !sset.has(r._id)),
      onlySsot: ssotReasons.filter(r => !lset.has(r._id)),
      shared: legacyReasons.filter(r => sset.has(r._id)),
    };
  }, [legacyReasons, ssotReasons]);

  return (
    <Section testId="diff-summary-section" accent={ACCENT_2}
      title="SSOT Diff Audit"
      subtitle="Per-row comparison between legacy inlined-gate rows and the new production-SSOT rows. Proves the historical replay is using the live pipeline."
      right={
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Select testId="diff-run-select" value={selected || ''}
            onChange={(e) => setSelected(e.target.value)}
            options={[
              { value: '', label: '— pick diff run —' },
              ...runs.map(r => ({
                value: r._id,
                label: `${r._id} · n=${r.n} · Δtier=${r.deltas}`,
              })),
            ]} />
          <Btn variant="ghost" onClick={() => { fetchRuns(); fetchStats(); }} testId="diff-refresh">Refresh</Btn>
        </div>
      }>
      {!stats ? (
        <div data-testid="diff-empty" style={{ padding: 20, textAlign: 'center', color: DIM, fontSize: 12 }}>
          No diff audit runs yet. The next full pipeline replay will emit one
          automatically (Workflow tab → SSOT Audit toggle, default ON).
        </div>
      ) : (
        <>
          <div data-testid="diff-headline" style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
            gap: 10, marginBottom: 14,
          }}>
            <StatCard testId="diff-total" label="Total Sampled" value={fmtInt(stats.n)} color={ACCENT_3} />
            <StatCard testId="diff-tier-delta" label="Tier Decisions Changed"
              value={fmtInt(stats.tier_delta)}
              hint={`${fmtPct((stats.tier_delta || 0) / (stats.n || 1))} of sample`}
              color={(stats.tier_delta || 0) === 0 ? ACCENT_2 : (stats.tier_delta || 0) > stats.n / 2 ? BAD : WARN} />
            <StatCard testId="diff-sh-flipped" label="SH Pass Flipped"
              value={fmtInt(stats.sh_flipped)}
              hint={`+${stats.sh_promoted} promoted · -${stats.sh_demoted} demoted`}
              color={ACCENT_2} />
            <StatCard testId="diff-fl-flipped" label="FL Pass Flipped"
              value={fmtInt(stats.fl_flipped)}
              hint={`+${stats.fl_promoted} promoted · -${stats.fl_demoted} demoted`}
              color={ACCENT_3} />
            <StatCard testId="diff-wz-flipped" label="WZ Pass Flipped"
              value={fmtInt(stats.wz_flipped)}
              hint={`+${stats.wz_promoted} promoted · -${stats.wz_demoted} demoted`}
              color={WARN} />
            {stats.missing_in_ssot > 0 && (
              <StatCard testId="diff-missing" label="Missing in SSOT"
                value={fmtInt(stats.missing_in_ssot)}
                hint="Legacy row had no SSOT counterpart"
                color={BAD} />
            )}
          </div>

          {/* Plain-English summary */}
          <div data-testid="diff-plain-english" style={{
            background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8,
            padding: 12, marginBottom: 14, fontSize: 12, lineHeight: 1.7,
          }}>
            <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Audit Verdict</div>
            {stats.tier_delta === 0 ? (
              <div style={{ color: ACCENT_2 }}>
                ✓ All {fmtInt(stats.n)} sampled props evaluated to the <strong>same tier decision</strong> under SSOT and legacy. Confidence high that the new SSOT path is at parity with the (presumed-correct) legacy data already in the collection.
              </div>
            ) : (
              <div>
                <div style={{ color: WARN }}>
                  ⚠ <strong>{fmtInt(stats.tier_delta)}</strong> of {fmtInt(stats.n)} sampled props ({fmtPct(stats.tier_delta / (stats.n || 1))}) changed tier decision under SSOT.
                </div>
                <div style={{ color: TEXT, marginTop: 6 }}>
                  Net SH movement: <strong style={{ color: ACCENT_2 }}>+{stats.sh_promoted}</strong> promoted / <strong style={{ color: BAD }}>-{stats.sh_demoted}</strong> demoted.{' '}
                  Net FL: <strong style={{ color: ACCENT_2 }}>+{stats.fl_promoted}</strong> / <strong style={{ color: BAD }}>-{stats.fl_demoted}</strong>.{' '}
                  Net WZ: <strong style={{ color: ACCENT_2 }}>+{stats.wz_promoted}</strong> / <strong style={{ color: BAD }}>-{stats.wz_demoted}</strong>.
                </div>
                <div style={{ color: DIM, marginTop: 6, fontSize: 11 }}>
                  Expected — old hardcoded `_SH_SPEC`/`_FL_SPEC`/`_WZ_SPEC` thresholds vs the production `evaluate_tier_with_overrides` threshold dict.
                  Inspect the example rows below to confirm the deltas line up with the threshold differences (not a bug).
                </div>
              </div>
            )}
          </div>

          {/* Reason code overlap */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginBottom: 14 }}>
            <BreakdownTable title="Reasons only in LEGACY" testId="diff-reasons-legacy"
              data={reasonOverlay.onlyLegacy}
              cols={[
                { key: 'code', label: 'reason', acc: (r) => r._id },
                { key: 'n', label: 'count', acc: (r) => fmtInt(r.n) },
              ]} />
            <BreakdownTable title="Reasons only in SSOT" testId="diff-reasons-ssot"
              data={reasonOverlay.onlySsot}
              cols={[
                { key: 'code', label: 'reason', acc: (r) => r._id },
                { key: 'n', label: 'count', acc: (r) => fmtInt(r.n) },
              ]} />
            <BreakdownTable title="Shared (count delta)" testId="diff-reasons-shared"
              data={reasonOverlay.shared.map((l) => {
                const s = ssotReasons.find(x => x._id === l._id) || { n: 0 };
                return { ...l, ssot_n: s.n, delta: (s.n || 0) - (l.n || 0) };
              })}
              cols={[
                { key: 'code',  label: 'reason', acc: (r) => r._id },
                { key: 'leg',   label: 'legacy', acc: (r) => fmtInt(r.n) },
                { key: 'ssot',  label: 'ssot',   acc: (r) => fmtInt(r.ssot_n) },
                { key: 'delta', label: 'Δ',      acc: (r) => (r.delta > 0 ? '+' : '') + r.delta,
                  color: (r) => r.delta > 0 ? ACCENT_2 : r.delta < 0 ? BAD : MUTED },
              ]} />
          </div>

          {/* Examples of changed decisions */}
          {examples.length > 0 && (
            <div data-testid="diff-examples" style={{
              background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, overflow: 'hidden',
            }}>
              <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', padding: '8px 12px', borderBottom: `1px solid ${BORDER}` }}>
                Examples of Changed Decisions ({examples.length})
              </div>
              <div style={{ maxHeight: 400, overflowY: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                  <thead><tr style={{ background: SURFACE, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
                    <th style={th}>date</th><th style={th}>player</th><th style={th}>family</th>
                    <th style={th}>line/side</th><th style={th}>legacy → ssot</th>
                    <th style={th}>legacy reasons</th><th style={th}>ssot reasons</th>
                  </tr></thead>
                  <tbody>
                    {examples.map((d, i) => (
                      <tr key={i} style={{ borderTop: `1px solid ${BORDER}` }}>
                        <td style={td}>{d.key?.game_date}</td>
                        <td style={{ ...td, fontWeight: 600 }}>{d.key?.player_name}</td>
                        <td style={{ ...td, color: ACCENT }}>{d.key?.stat_family}</td>
                        <td style={td}>{d.key?.line} / {d.key?.side}</td>
                        <td style={td}>
                          <span style={{ color: MUTED }}>{d.legacy_inlined_gates?.selected_tier || 'none'}</span>
                          {' → '}
                          <span style={{ color: d.ssot_production_runner?._missing ? BAD : ACCENT_2, fontWeight: 600 }}>
                            {d.ssot_production_runner?._missing ? '∅ missing' : (d.ssot_production_runner?.selected_tier || 'none')}
                          </span>
                        </td>
                        <td style={{ ...td, fontSize: 10, color: DIM }}>
                          {[
                            ...(d.legacy_inlined_gates?.safe_haven_failed_reasons || []),
                            ...(d.legacy_inlined_gates?.front_lines_failed_reasons || []),
                            ...(d.legacy_inlined_gates?.war_zone_failed_reasons || []),
                          ].slice(0, 4).join(', ') || '—'}
                        </td>
                        <td style={{ ...td, fontSize: 10, color: DIM }}>
                          {[
                            ...(d.ssot_production_runner?.safe_haven_failed_reasons || []),
                            ...(d.ssot_production_runner?.front_lines_failed_reasons || []),
                            ...(d.ssot_production_runner?.war_zone_failed_reasons || []),
                          ].slice(0, 4).join(', ') || '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {loading && <div style={{ fontSize: 11, color: ACCENT, marginTop: 8 }}>loading…</div>}
        </>
      )}
    </Section>
  );
}

// ── Results tab ─────────────────────────────────────────────────────
function ResultsTab({ token, refreshKey, onSaveCandidate }) {
  const [runs, setRuns] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const [cells, setCells] = useState([]);
  const [roiByTier, setRoiByTier] = useState([]);
  const [roiByFam, setRoiByFam] = useState([]);
  const [roiByOdds, setRoiByOdds] = useState([]);
  const [roiBySide, setRoiBySide] = useState([]);
  const [reasonCounts, setReasonCounts] = useState([]);
  const [dailyRoi, setDailyRoi] = useState([]);
  const [filter, setFilter] = useState({ tier: '', stat_family: '', side: '', slice: 'TIER_FAMILY' });
  const [loading, setLoading] = useState(false);

  const fetchRuns = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiFetch(token, `/collections/${GRID_RUNS_COLL}/find`, {
        method: 'POST', body: JSON.stringify({ filter: {}, sort: { started_at: -1 }, limit: 30 }),
      });
      setRuns(res.docs || []);
      if (!selectedRun && res.docs?.length) setSelectedRun(res.docs[0].run_id);
    } catch (e) { toast.error(`Load runs: ${e.message}`); }
  }, [token, selectedRun]);
  useEffect(() => { fetchRuns(); }, [fetchRuns, refreshKey]);

  const runMeta = useMemo(() => runs.find(r => r.run_id === selectedRun), [runs, selectedRun]);

  const loadCells = useCallback(async () => {
    if (!token || !selectedRun) return;
    setLoading(true);
    try {
      const filt = { run_id: selectedRun };
      if (filter.slice) filt.slice = filter.slice;
      if (filter.tier) filt.tier = filter.tier;
      if (filter.stat_family) filt.stat_family = filter.stat_family;
      if (filter.side) filt.side = filter.side;
      const res = await apiFetch(token, `/collections/${GRID_RESULTS_COLL}/find`, {
        method: 'POST', body: JSON.stringify({ filter: filt, sort: { hit_rate: -1 }, limit: 2000 }),
      });
      setCells(res.docs || []);
    } catch (e) { toast.error(`Load cells: ${e.message}`); }
    finally { setLoading(false); }
  }, [token, selectedRun, filter]);
  useEffect(() => { loadCells(); }, [loadCells]);

  const loadRoi = useCallback(async () => {
    if (!token || !runMeta?.params) return;
    const { params } = runMeta;
    const baseMatch = {
      league_id: params.league || 'MLB',
      game_date: { $gte: params.start, $lte: params.end },
      outcome_resolved: true, selected_tier: { $ne: null },
    };
    const winCond = { $cond: [{ $eq: ['$outcome_numeric', 1] }, 1, 0] };
    const payoutCond = { $cond: [
      { $eq: ['$outcome_numeric', 1] },
      { $cond: [{ $gt: ['$odds', 0] }, { $divide: ['$odds', 100] }, { $divide: [100, { $abs: '$odds' }] }] },
      -1,
    ]};
    const grp = (k) => ([
      { $match: baseMatch },
      { $group: {
          _id: k, n: { $sum: 1 }, wins: { $sum: winCond },
          roi: { $avg: payoutCond }, avg_tp: { $avg: '$tp' },
          avg_cv: { $avg: '$cv' }, avg_edge: { $avg: '$edge' },
          avg_hr20: { $avg: '$hit_rate_l20' },
      }},
      { $sort: { roi: -1 } }, { $limit: 200 },
    ]);
    try {
      const [a,b,c,d,e] = await Promise.all([
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, { method: 'POST', body: JSON.stringify({ pipeline: grp('$selected_tier'), limit: 100 }) }),
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, { method: 'POST', body: JSON.stringify({ pipeline: grp({ tier: '$selected_tier', stat_family: '$stat_family' }), limit: 400 }) }),
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, { method: 'POST', body: JSON.stringify({ pipeline: grp({ tier: '$selected_tier', odds_bucket: '$odds_bucket' }), limit: 200 }) }),
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, { method: 'POST', body: JSON.stringify({ pipeline: grp({ tier: '$selected_tier', side: '$side' }), limit: 100 }) }),
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, { method: 'POST', body: JSON.stringify({
          pipeline: [
            { $match: baseMatch },
            { $group: { _id: '$game_date', n: { $sum: 1 }, wins: { $sum: winCond }, roi: { $avg: payoutCond } } },
            { $sort: { _id: 1 } }, { $limit: 400 },
          ], limit: 400,
        })}),
      ]);
      setRoiByTier(a.docs || []);
      setRoiByFam(b.docs || []);
      setRoiByOdds(c.docs || []);
      setRoiBySide(d.docs || []);
      setDailyRoi(e.docs || []);
    } catch (err) { toast.error(`ROI aggregate: ${err.message}`); }
  }, [token, runMeta]);
  useEffect(() => { loadRoi(); }, [loadRoi]);

  const loadReasons = useCallback(async () => {
    if (!token || !runMeta?.params) return;
    const { params } = runMeta;
    try {
      const res = await apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, {
        method: 'POST', body: JSON.stringify({
          pipeline: [
            { $match: { league_id: params.league || 'MLB', game_date: { $gte: params.start, $lte: params.end } } },
            { $project: { reasons: { $concatArrays: [
              { $ifNull: ['$safe_haven_failed_reasons', []] },
              { $ifNull: ['$front_lines_failed_reasons', []] },
              { $ifNull: ['$war_zone_failed_reasons', []] },
            ] } } },
            { $unwind: '$reasons' },
            { $group: { _id: '$reasons', n: { $sum: 1 } } },
            { $sort: { n: -1 } }, { $limit: 40 },
          ], limit: 40,
        }),
      });
      setReasonCounts(res.docs || []);
    } catch (e) { console.error('[reasons]', e.message); }
  }, [token, runMeta]);
  useEffect(() => { loadReasons(); }, [loadReasons]);

  const sortedByHr = useMemo(() => [...cells]
    .filter(c => (c.n_bets || 0) >= (runMeta?.params?.min_bets || 20))
    .sort((a, b) => (b.hit_rate || 0) - (a.hit_rate || 0)), [cells, runMeta]);

  const top = sortedByHr.slice(0, 15);
  const worst = sortedByHr.slice(-15).reverse();

  const bestByTier = useMemo(() => {
    const m = {};
    for (const c of sortedByHr) {
      if (c.slice !== 'TIER_FAMILY') continue;
      if (!m[c.tier] || (c.hit_rate || 0) > (m[c.tier].hit_rate || 0)) m[c.tier] = c;
    }
    return m;
  }, [sortedByHr]);
  const bestByFam = useMemo(() => {
    const m = {};
    for (const c of sortedByHr) {
      if (c.slice !== 'TIER_FAMILY') continue;
      const k = c.stat_family;
      if (!m[k] || (c.hit_rate || 0) > (m[k].hit_rate || 0)) m[k] = c;
    }
    return Object.values(m).sort((a, b) => (b.hit_rate || 0) - (a.hit_rate || 0));
  }, [sortedByHr]);

  const summary = useMemo(() => {
    if (!cells.length) return null;
    const sh = bestByTier['safe_haven']; const fl = bestByTier['front_lines']; const wz = bestByTier['war_zone'];
    const bestFam = bestByFam[0];
    const worstFam = [...bestByFam].sort((a,b) => (a.hit_rate || 0) - (b.hit_rate || 0))[0];
    const bestOdds = [...roiByOdds].sort((a,b) => (b.roi || 0) - (a.roi || 0))[0];
    return { sh, fl, wz, bestFam, worstFam, bestOdds };
  }, [bestByTier, bestByFam, roiByOdds, cells]);

  const maxDrawdown = useMemo(() => {
    if (!dailyRoi.length) return null;
    let peak = 0, cum = 0, dd = 0;
    for (const d of dailyRoi) {
      cum += (d.roi || 0) * (d.n || 0);
      if (cum > peak) peak = cum;
      if (peak - cum > dd) dd = peak - cum;
    }
    return dd;
  }, [dailyRoi]);

  return (
    <>
    <DiffSummarySection token={token} />
    <Section testId="results-section" accent={ACCENT_3} title="Results Dashboard"
      subtitle="research_grid_results + sgo_propvision_full_pipeline_replay aggregations · no Mongo Compass required"
      right={
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Select testId="results-run-select" value={selectedRun || ''}
            onChange={(e) => setSelectedRun(e.target.value)}
            options={[
              { value: '', label: '— pick run —' },
              ...runs.map(r => ({
                value: r.run_id,
                label: `${r.params?.league} ${r.params?.start}..${r.params?.end} · ${r.run_id.slice(0,8)} · ${r.status}`,
              })),
            ]} />
          <Btn variant="ghost" onClick={() => { fetchRuns(); loadCells(); loadRoi(); loadReasons(); }} testId="results-refresh">Refresh</Btn>
        </div>
      }>
      {!runMeta ? (
        <div data-testid="results-empty" style={{ padding: 30, textAlign: 'center', color: DIM, fontSize: 13 }}>
          No grid runs yet — run the Workflow tab's pipeline first.
        </div>
      ) : (
        <>
          <div style={{ marginBottom: 14, fontSize: 12, color: MUTED }}>
            <span style={{ color: TEXT, fontFamily: 'monospace' }}>{runMeta.run_id}</span>
            {' · '}<span style={{ color: ACCENT_2 }}>{runMeta.params?.league}</span>
            {' · '}{runMeta.params?.start} → {runMeta.params?.end}
            {' · '}cells={fmtInt(runMeta.n_cells_total)}
            {' · '}qualified={fmtInt(runMeta.n_cells_qualified)}
          </div>

          <div data-testid="results-headline" style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 10, marginBottom: 14,
          }}>
            {roiByTier.map(r => (
              <StatCard key={r._id || 'none'} testId={`headline-tier-${r._id}`}
                label={`Tier · ${r._id || 'none'}`}
                value={fmtPct(r.roi, 1)}
                hint={`n=${fmtInt(r.n)} · HR=${fmtPct((r.wins || 0) / (r.n || 1))} · TP=${fmtPct(r.avg_tp)} · CV=${fmtNum(r.avg_cv)}`}
                color={(r.roi || 0) > 0 ? ACCENT_2 : (r.roi || 0) < -0.02 ? BAD : WARN} />
            ))}
            {maxDrawdown != null && (
              <StatCard testId="headline-maxdd" label="Max Drawdown (u)"
                value={fmtNum(maxDrawdown, 1)} hint="From sequential daily P&L"
                color={maxDrawdown > 50 ? BAD : maxDrawdown > 20 ? WARN : ACCENT_2} />
            )}
          </div>

          {summary && (
            <div data-testid="results-plain-english" style={{
              background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8,
              padding: 14, marginBottom: 14, fontSize: 13, lineHeight: 1.7,
            }}>
              <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 }}>Plain-English Summary</div>
              {summary.sh && <div><strong style={{ color: ACCENT_2 }}>Best Safe Haven:</strong> {summary.sh.stat_family} @ hr_l20≥{fmtPct(summary.sh.hr_l20_min)}, cv≤{fmtNum(summary.sh.cv_max)}, edge≥{fmtPct(summary.sh.edge_min)} → <strong>{fmtPct(summary.sh.hit_rate)}</strong> on {fmtInt(summary.sh.n_bets)} bets.</div>}
              {summary.fl && <div><strong style={{ color: ACCENT_3 }}>Best Front Lines:</strong> {summary.fl.stat_family} @ hr_l20≥{fmtPct(summary.fl.hr_l20_min)}, hr_l5≥{fmtPct(summary.fl.hr_l5_min)} → <strong>{fmtPct(summary.fl.hit_rate)}</strong> on {fmtInt(summary.fl.n_bets)} bets.</div>}
              {summary.wz && <div><strong style={{ color: WARN }}>Best War Zone:</strong> {summary.wz.stat_family} @ hr_l20≥{fmtPct(summary.wz.hr_l20_min)} → <strong>{fmtPct(summary.wz.hit_rate)}</strong> on {fmtInt(summary.wz.n_bets)} bets.</div>}
              {summary.bestFam && <div><strong>Best stat family:</strong> <code style={{ color: ACCENT_2 }}>{summary.bestFam.stat_family}</code> ({fmtPct(summary.bestFam.hit_rate)} @ {summary.bestFam.tier}).</div>}
              {summary.worstFam && summary.worstFam.stat_family !== summary.bestFam?.stat_family && <div><strong>Worst stat family (DO NOT USE):</strong> <code style={{ color: BAD }}>{summary.worstFam.stat_family}</code> ({fmtPct(summary.worstFam.hit_rate)}).</div>}
              {summary.bestOdds && <div><strong>Best odds bucket:</strong> <code style={{ color: ACCENT_2 }}>{summary.bestOdds._id?.odds_bucket}</code> (tier {summary.bestOdds._id?.tier}) ROI {fmtPct(summary.bestOdds.roi)} on {fmtInt(summary.bestOdds.n)} bets.</div>}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase' }}>Filter</span>
            <Select testId="results-filter-tier" value={filter.tier} onChange={(e) => setFilter({ ...filter, tier: e.target.value })}
              options={[{ value: '', label: 'all tiers' }, 'safe_haven','front_lines','war_zone'].map(x => typeof x==='string'?{value:x,label:x}:x)} />
            <Select testId="results-filter-slice" value={filter.slice} onChange={(e) => setFilter({ ...filter, slice: e.target.value })}
              options={[{ value: 'TIER_FAMILY', label: 'tier × family' }, { value: 'TIER_FAMILY_SIDE', label: 'tier × family × side' }, { value: '', label: 'all slices' }]} />
            <Input testId="results-filter-family" placeholder="stat_family contains…" value={filter.stat_family}
              onChange={(e) => setFilter({ ...filter, stat_family: e.target.value })} style={{ width: 160 }} />
            {loading && <span style={{ color: ACCENT, fontSize: 11 }}>loading…</span>}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <CellTable title="Top 15 by Hit Rate" cells={top} testId="results-top-table" accent={ACCENT_2} onSave={onSaveCandidate} />
            <CellTable title="Bottom 15 (DO NOT USE)" cells={worst} testId="results-worst-table" accent={BAD} onSave={onSaveCandidate} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <BestByTier bestByTier={bestByTier} onSave={onSaveCandidate} />
            <BestByFamily bestByFam={bestByFam} onSave={onSaveCandidate} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginBottom: 14 }}>
            <BreakdownTable title="ROI · Tier × Side" data={roiBySide} testId="results-side-table"
              cols={[
                { key: 'tier', label: 'tier', acc: (r) => r._id?.tier },
                { key: 'side', label: 'side', acc: (r) => r._id?.side },
                { key: 'n', label: 'n', acc: (r) => fmtInt(r.n) },
                { key: 'hr', label: 'HR', acc: (r) => fmtPct((r.wins || 0)/(r.n || 1)) },
                { key: 'roi', label: 'ROI', acc: (r) => fmtPct(r.roi), color: (r) => (r.roi || 0) > 0 ? ACCENT_2 : BAD },
              ]} />
            <BreakdownTable title="ROI · Tier × Odds Bucket" data={roiByOdds} testId="results-odds-table"
              cols={[
                { key: 'tier', label: 'tier', acc: (r) => r._id?.tier },
                { key: 'bucket', label: 'bucket', acc: (r) => r._id?.odds_bucket },
                { key: 'n', label: 'n', acc: (r) => fmtInt(r.n) },
                { key: 'roi', label: 'ROI', acc: (r) => fmtPct(r.roi), color: (r) => (r.roi || 0) > 0 ? ACCENT_2 : BAD },
              ]} />
            <BreakdownTable title="ROI · Tier × Family" data={roiByFam} testId="results-fam-roi-table"
              cols={[
                { key: 'tier', label: 'tier', acc: (r) => r._id?.tier },
                { key: 'fam', label: 'family', acc: (r) => r._id?.stat_family },
                { key: 'n', label: 'n', acc: (r) => fmtInt(r.n) },
                { key: 'roi', label: 'ROI', acc: (r) => fmtPct(r.roi), color: (r) => (r.roi || 0) > 0 ? ACCENT_2 : BAD },
              ]} />
          </div>

          <div data-testid="results-reasons" style={{ background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12 }}>
            <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', marginBottom: 8 }}>Gate-Fail Reason Codes</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 6 }}>
              {reasonCounts.map(r => (
                <div key={r._id} style={{ display: 'flex', justifyContent: 'space-between',
                  padding: '5px 8px', background: SURFACE_3, borderRadius: 4, fontSize: 11 }}>
                  <code style={{ color: TEXT, fontFamily: 'monospace' }}>{r._id}</code>
                  <span style={{ color: MUTED, fontVariantNumeric: 'tabular-nums' }}>{fmtInt(r.n)}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </Section>
    </>
  );
}

function CellTable({ title, cells, testId, accent, onSave }) {
  return (
    <div data-testid={testId} style={{
      background: SURFACE_2, border: `1px solid ${BORDER}`, borderLeft: `3px solid ${accent}`, borderRadius: 8, overflow: 'hidden',
    }}>
      <div style={{ fontSize: 11, color: accent, textTransform: 'uppercase', padding: '8px 12px', borderBottom: `1px solid ${BORDER}` }}>{title}</div>
      <div style={{ maxHeight: 360, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead><tr style={{ background: SURFACE, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
            <th style={th}>tier</th><th style={th}>family</th><th style={th}>n</th>
            <th style={th}>HR</th><th style={th}>Δcal</th><th style={th}>edge</th>
            <th style={th}>cv</th><th style={th}>tp</th><th style={th}></th>
          </tr></thead>
          <tbody>
            {cells.length === 0 ? <tr><td colSpan={9} style={{ padding: 18, textAlign: 'center', color: DIM }}>(no cells)</td></tr> :
              cells.map((c, i) => (
                <tr key={i} style={{ borderTop: `1px solid ${BORDER}` }}>
                  <td style={td}>{c.tier}</td>
                  <td style={{ ...td, color: ACCENT }}>{c.stat_family}</td>
                  <td style={td}>{fmtInt(c.n_bets)}</td>
                  <td style={{ ...td, color: (c.hit_rate || 0) > 0.6 ? ACCENT_2 : (c.hit_rate || 0) < 0.5 ? BAD : TEXT, fontWeight: 600 }}>{fmtPct(c.hit_rate)}</td>
                  <td style={{ ...td, color: (c.calibration_delta || 0) > 0 ? ACCENT_2 : BAD }}>{fmtPct(c.calibration_delta, 2)}</td>
                  <td style={td}>{fmtPct(c.avg_edge, 2)}</td>
                  <td style={td}>{fmtNum(c.avg_cv)}</td>
                  <td style={td}>{fmtPct(c.avg_tp, 1)}</td>
                  <td style={td}><button onClick={() => onSave(c)} data-testid={`celltable-save-${i}`} style={{ background: 'transparent', border: `1px solid ${BORDER_STRONG}`, color: TEXT, borderRadius: 4, padding: '2px 6px', fontSize: 10, cursor: 'pointer' }}>save</button></td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
function BestByTier({ bestByTier, onSave }) {
  return (
    <div data-testid="results-best-by-tier" style={{ background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12 }}>
      <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', marginBottom: 8 }}>Best Config by Tier</div>
      {Object.entries(bestByTier).map(([tier, c]) => (
        <div key={tier} style={{ display: 'grid', gridTemplateColumns: '110px 1fr auto', gap: 8, alignItems: 'center', padding: '8px 0', borderTop: `1px solid ${BORDER}` }}>
          <Badge color={tier === 'safe_haven' ? ACCENT_2 : tier === 'front_lines' ? ACCENT_3 : WARN}>{tier}</Badge>
          <div style={{ fontSize: 12 }}>
            <code style={{ color: ACCENT, fontFamily: 'monospace' }}>{c.stat_family}</code> · hr_l20≥{fmtPct(c.hr_l20_min)} · cv≤{fmtNum(c.cv_max)} · edge≥{fmtPct(c.edge_min)}
            <div style={{ fontSize: 10, color: DIM }}>n={fmtInt(c.n_bets)} · HR={fmtPct(c.hit_rate)}</div>
          </div>
          <Btn variant="ghost" onClick={() => onSave(c)} testId={`save-bestby-${tier}`}>Save</Btn>
        </div>
      ))}
    </div>
  );
}
function BestByFamily({ bestByFam, onSave }) {
  return (
    <div data-testid="results-best-by-family" style={{
      background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12, maxHeight: 320, overflowY: 'auto',
    }}>
      <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', marginBottom: 8 }}>Best Config by Stat Family</div>
      {bestByFam.map((c, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 8, alignItems: 'center', padding: '6px 0', borderTop: `1px solid ${BORDER}` }}>
          <div style={{ fontSize: 12 }}>
            <code style={{ color: TEXT, fontFamily: 'monospace' }}>{c.stat_family}</code>
            <span style={{ color: DIM, marginLeft: 8 }}>({c.tier})</span>
          </div>
          <span style={{ fontSize: 12, color: (c.hit_rate || 0) > 0.6 ? ACCENT_2 : MUTED, fontWeight: 600 }}>{fmtPct(c.hit_rate)}</span>
          <Btn variant="ghost" onClick={() => onSave(c)} testId={`save-fam-${c.stat_family}`}>Save</Btn>
        </div>
      ))}
    </div>
  );
}
function BreakdownTable({ title, data, cols, testId }) {
  return (
    <div data-testid={testId} style={{ background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', padding: '8px 12px', borderBottom: `1px solid ${BORDER}` }}>{title}</div>
      <div style={{ maxHeight: 280, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead><tr style={{ background: SURFACE, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
            {cols.map(c => <th key={c.key} style={th}>{c.label}</th>)}
          </tr></thead>
          <tbody>
            {data.length === 0 ? <tr><td colSpan={cols.length} style={{ padding: 12, textAlign: 'center', color: DIM }}>—</td></tr> :
              data.map((r, i) => (
                <tr key={i} style={{ borderTop: `1px solid ${BORDER}` }}>
                  {cols.map(c => <td key={c.key} style={{ ...td, color: c.color ? c.color(r) : TEXT, fontWeight: c.color ? 600 : 400 }}>{c.acc(r)}</td>)}
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Candidates tab ──────────────────────────────────────────────────
function CandidatesTab({ token, pendingCandidate, setPendingCandidate }) {
  const [candidates, setCandidates] = useState(() => {
    try { const r = localStorage.getItem(CANDIDATES_KEY); return r ? JSON.parse(r) : []; } catch { return []; }
  });
  const [name, setName] = useState(''); const [tag, setTag] = useState('review');
  const [compare, setCompare] = useState([]);
  useEffect(() => { localStorage.setItem(CANDIDATES_KEY, JSON.stringify(candidates)); }, [candidates]);

  const save = () => {
    if (!pendingCandidate) return;
    if (!name.trim()) { toast.error('Name required'); return; }
    const e = {
      id: `cand_${Date.now()}`, name: name.trim(), tag, saved_at: new Date().toISOString(),
      tier: pendingCandidate.tier, stat_family: pendingCandidate.stat_family,
      side: pendingCandidate.side, slice: pendingCandidate.slice,
      thresholds: {
        hr_l20_min: pendingCandidate.hr_l20_min, hr_l5_min: pendingCandidate.hr_l5_min,
        cv_max: pendingCandidate.cv_max, edge_min: pendingCandidate.edge_min, tp_min: pendingCandidate.tp_min,
      },
      metrics: {
        n_bets: pendingCandidate.n_bets, hit_rate: pendingCandidate.hit_rate,
        calibration_delta: pendingCandidate.calibration_delta,
        avg_edge: pendingCandidate.avg_edge, avg_cv: pendingCandidate.avg_cv, avg_tp: pendingCandidate.avg_tp,
        daily_consistency: pendingCandidate.daily_consistency,
      },
      run_id: pendingCandidate.run_id,
    };
    setCandidates([e, ...candidates]); setName(''); setPendingCandidate(null);
    toast.success(`Saved "${e.name}"`);
  };
  const markReady = async (c) => {
    try {
      await apiFetch(token, '/configs/draft', { method: 'POST', body: JSON.stringify({
        kind: 'admin_testing_candidate_config', scope: `${c.tier}:${c.stat_family}`,
        config: { name: c.name, tier: c.tier, stat_family: c.stat_family, thresholds: c.thresholds, metrics: c.metrics, source_run_id: c.run_id },
        note: `Marked ready · tag=${c.tag}`,
      })});
      setCandidates(candidates.map(x => x.id === c.id ? { ...x, tag: 'ready', backend_saved_at: new Date().toISOString() } : x));
      toast.success(`Persisted "${c.name}"`);
    } catch (e) { toast.error(`Mark-ready failed: ${e.message}`); }
  };
  const del = (id) => { if (!window.confirm('Delete this candidate?')) return; setCandidates(candidates.filter(c => c.id !== id)); };
  const exportJson = () => {
    const blob = new Blob([JSON.stringify(candidates, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = `propvision-candidates-${new Date().toISOString().slice(0,10)}.json`;
    a.click(); URL.revokeObjectURL(url);
  };
  const toggleCmp = (id) => setCompare(c => c.includes(id) ? c.filter(x => x !== id) : c.length >= 4 ? c : [...c, id]);
  const cmpItems = useMemo(() => candidates.filter(c => compare.includes(c.id)), [candidates, compare]);

  return (
    <Section testId="candidate-section" accent={ACCENT_2} title="Candidate Manager"
      subtitle="Save winning configs. Mark Ready → persists to emergent_candidate_configs. NEVER auto-promotes to production."
      right={<Btn variant="ghost" onClick={exportJson} testId="cand-export" disabled={!candidates.length}>Export JSON</Btn>}>
      <div style={{
        background: pendingCandidate ? `${ACCENT}10` : SURFACE_2,
        border: `1px solid ${pendingCandidate ? ACCENT : BORDER}`, borderRadius: 8, padding: 12, marginBottom: 12,
      }}>
        {pendingCandidate ? (
          <>
            <div style={{ fontSize: 11, color: ACCENT, textTransform: 'uppercase', marginBottom: 8 }}>Pending Candidate</div>
            <div style={{ fontSize: 12, fontFamily: 'monospace', color: TEXT, marginBottom: 10 }}>
              {pendingCandidate.tier} · {pendingCandidate.stat_family} · hr_l20≥{fmtPct(pendingCandidate.hr_l20_min)} · cv≤{fmtNum(pendingCandidate.cv_max)} · edge≥{fmtPct(pendingCandidate.edge_min)} · n={fmtInt(pendingCandidate.n_bets)} · HR={fmtPct(pendingCandidate.hit_rate)}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Input testId="cand-name" placeholder="Candidate name" value={name} onChange={(e) => setName(e.target.value)} style={{ flex: 1 }} />
              <Select testId="cand-tag" value={tag} onChange={(e) => setTag(e.target.value)}
                options={[{value:'review',label:'tag: review'},{value:'ready',label:'tag: ready'},{value:'reject',label:'tag: do-not-use'}]} />
              <Btn variant="success" onClick={save} testId="cand-save-btn">Save</Btn>
              <Btn variant="ghost" onClick={() => setPendingCandidate(null)} testId="cand-cancel-btn">Cancel</Btn>
            </div>
          </>
        ) : (
          <div style={{ fontSize: 12, color: DIM, textAlign: 'center' }}>
            Click "save" on any row in the Results tab to stage a candidate here.
          </div>
        )}
      </div>

      {candidates.length === 0 ? (
        <div style={{ color: DIM, fontSize: 12, padding: 18, textAlign: 'center' }}>No candidates yet.</div>
      ) : (
        <div data-testid="cand-list" style={{ background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead><tr style={{ background: SURFACE, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
              <th style={th}>cmp</th><th style={th}>name</th><th style={th}>tier</th>
              <th style={th}>family</th><th style={th}>n</th><th style={th}>HR</th>
              <th style={th}>thresholds</th><th style={th}>tag</th><th style={th}></th>
            </tr></thead>
            <tbody>
              {candidates.map(c => (
                <tr key={c.id} style={{ borderTop: `1px solid ${BORDER}` }}>
                  <td style={td}><input type="checkbox" checked={compare.includes(c.id)} onChange={() => toggleCmp(c.id)} data-testid={`cand-cmp-${c.id}`} /></td>
                  <td style={{ ...td, color: TEXT, fontWeight: 600 }}>{c.name}</td>
                  <td style={td}>{c.tier}</td>
                  <td style={{ ...td, color: ACCENT }}>{c.stat_family}</td>
                  <td style={td}>{fmtInt(c.metrics?.n_bets)}</td>
                  <td style={{ ...td, color: (c.metrics?.hit_rate || 0) > 0.6 ? ACCENT_2 : TEXT, fontWeight: 600 }}>{fmtPct(c.metrics?.hit_rate)}</td>
                  <td style={{ ...td, fontSize: 10 }}>hr_l20≥{fmtPct(c.thresholds?.hr_l20_min)} · cv≤{fmtNum(c.thresholds?.cv_max)} · edge≥{fmtPct(c.thresholds?.edge_min)}</td>
                  <td style={td}><Badge color={c.tag === 'ready' ? ACCENT_2 : c.tag === 'reject' ? BAD : WARN}>{c.tag}</Badge></td>
                  <td style={td}>
                    <div style={{ display: 'flex', gap: 4 }}>
                      {c.tag !== 'ready' && <Btn variant="ghost" onClick={() => markReady(c)} testId={`cand-ready-${c.id}`}>Mark Ready</Btn>}
                      <Btn variant="ghost" onClick={() => del(c.id)} testId={`cand-del-${c.id}`}>×</Btn>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {cmpItems.length >= 2 && (
        <div data-testid="cand-compare" style={{ marginTop: 14, background: SURFACE_2, border: `1px solid ${ACCENT_3}`, borderRadius: 8, padding: 12 }}>
          <div style={{ fontSize: 11, color: ACCENT_3, textTransform: 'uppercase', marginBottom: 8 }}>Compare ({cmpItems.length}/4)</div>
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cmpItems.length}, 1fr)`, gap: 12 }}>
            {cmpItems.map(c => (
              <div key={c.id} style={{ background: SURFACE, padding: 10, borderRadius: 6, border: `1px solid ${BORDER}` }}>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>{c.name}</div>
                <div style={{ fontSize: 10, color: DIM, fontFamily: 'monospace', lineHeight: 1.6 }}>
                  <div>tier: {c.tier}</div>
                  <div>family: <span style={{ color: ACCENT }}>{c.stat_family}</span></div>
                  <div>n: {fmtInt(c.metrics?.n_bets)}</div>
                  <div>HR: <span style={{ color: ACCENT_2 }}>{fmtPct(c.metrics?.hit_rate)}</span></div>
                  <div>Δcal: {fmtPct(c.metrics?.calibration_delta, 2)}</div>
                  <div>edge: {fmtPct(c.metrics?.avg_edge, 2)}</div>
                  <div>cv: {fmtNum(c.metrics?.avg_cv)}</div>
                  <div style={{ marginTop: 6, paddingTop: 6, borderTop: `1px solid ${BORDER}` }}>
                    <div>hr_l20≥{fmtPct(c.thresholds?.hr_l20_min)}</div>
                    <div>hr_l5≥{fmtPct(c.thresholds?.hr_l5_min)}</div>
                    <div>cv≤{fmtNum(c.thresholds?.cv_max)}</div>
                    <div>edge≥{fmtPct(c.thresholds?.edge_min)}</div>
                    <div>tp≥{fmtPct(c.thresholds?.tp_min)}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}

// ── Models tab ──────────────────────────────────────────────────────
function ModelsTab({ token }) {
  const [models, setModels] = useState([]);
  const [filter, setFilter] = useState({ sport: '', mode: '' });
  const [draft, setDraft] = useState({ model_id: '', sport: 'MLB', family: 'HF', version: '',
    mode: 'candidate', artifact_path: '', feature_schema_version: '',
    compatible_stat_families: '', notes: '' });

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const qs = Object.entries(filter).filter(([,v]) => v).map(([k,v]) => `${k}=${v}`).join('&');
      const res = await apiFetch(token, `/models/?${qs}`);
      setModels(res.models || []);
    } catch (e) { toast.error(`Load models: ${e.message}`); }
  }, [token, filter]);
  useEffect(() => { load(); }, [load]);

  const submit = async () => {
    if (!draft.model_id || !draft.version) { toast.error('model_id + version required'); return; }
    try {
      await apiFetch(token, '/models/', { method: 'POST', body: JSON.stringify({
        ...draft, compatible_stat_families: draft.compatible_stat_families.split(',').map(s => s.trim()).filter(Boolean),
      }) });
      toast.success(`Registered ${draft.model_id}`);
      setDraft({ ...draft, model_id: '', version: '', notes: '' });
      load();
    } catch (e) { toast.error(`Register failed: ${e.message}`); }
  };
  const clone = async (m) => {
    const newId = window.prompt(`Clone ${m.model_id} → new model_id:`, `${m.model_id}_research`);
    if (!newId) return;
    try {
      await apiFetch(token, `/models/${encodeURIComponent(m.model_id)}/clone`, { method: 'POST',
        body: JSON.stringify({ new_model_id: newId, new_mode: 'research' }) });
      toast.success('Cloned'); load();
    } catch (e) { toast.error(`Clone failed: ${e.message}`); }
  };
  const validate = async (m, passed) => {
    const notes = window.prompt('Validation notes:', '') || '';
    try {
      await apiFetch(token, `/models/${encodeURIComponent(m.model_id)}/validate`, { method: 'POST',
        body: JSON.stringify({ passed, notes }) });
      toast.success(`Marked ${passed ? 'passed' : 'failed'}`); load();
    } catch (e) { toast.error(`Validate failed: ${e.message}`); }
  };
  const activate = async (m) => {
    if (!window.confirm(`Make ${m.model_id} the active research model for ${m.sport}?`)) return;
    try {
      await apiFetch(token, `/models/${encodeURIComponent(m.model_id)}/activate`, { method: 'POST' });
      toast.success('Activated'); load();
    } catch (e) { toast.error(`Activate failed: ${e.message}`); }
  };
  const archive = async (m) => {
    if (!window.confirm(`Archive ${m.model_id}?`)) return;
    try {
      await apiFetch(token, `/models/${encodeURIComponent(m.model_id)}/archive`, { method: 'POST' });
      toast.success('Archived'); load();
    } catch (e) { toast.error(`Archive failed: ${e.message}`); }
  };

  return (
    <Section testId="models-section" accent={ACCENT_3} title="Universal Model Registry"
      subtitle="Cross-sport model metadata · production / research / candidate / archived. Pickles untouched on disk."
      right={
        <div style={{ display: 'flex', gap: 6 }}>
          <Select testId="model-filter-sport" value={filter.sport} onChange={(e) => setFilter({ ...filter, sport: e.target.value })}
            options={[{ value: '', label: 'all sports' }, 'MLB','NBA','NFL'].map(x => typeof x==='string'?{value:x,label:x}:x)} />
          <Select testId="model-filter-mode" value={filter.mode} onChange={(e) => setFilter({ ...filter, mode: e.target.value })}
            options={[{ value: '', label: 'all modes' }, 'production','research','candidate','archived'].map(x => typeof x==='string'?{value:x,label:x}:x)} />
          <Btn variant="ghost" onClick={load} testId="model-refresh">Refresh</Btn>
        </div>
      }>
      <div style={{
        background: SURFACE_2, border: `1px solid ${ACCENT_3}`, borderRadius: 8, padding: 12, marginBottom: 14,
      }}>
        <div style={{ fontSize: 11, color: ACCENT_3, textTransform: 'uppercase', marginBottom: 8 }}>Register / Upsert Model</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 8 }}>
          <Field label="Model ID (unique)"><Input testId="model-id" value={draft.model_id}
            onChange={(e) => setDraft({ ...draft, model_id: e.target.value })}
            placeholder="MLB:HF:v3.2:candidate" /></Field>
          <Field label="Sport"><Select testId="model-sport" value={draft.sport}
            onChange={(e) => setDraft({ ...draft, sport: e.target.value })} options={['MLB','NBA','NFL']} /></Field>
          <Field label="Family"><Input testId="model-family" value={draft.family}
            onChange={(e) => setDraft({ ...draft, family: e.target.value })} placeholder="HF / VK2 / HF-pitcher" /></Field>
          <Field label="Version"><Input testId="model-version" value={draft.version}
            onChange={(e) => setDraft({ ...draft, version: e.target.value })} placeholder="v3.2_phase2b" /></Field>
          <Field label="Mode"><Select testId="model-mode" value={draft.mode}
            onChange={(e) => setDraft({ ...draft, mode: e.target.value })}
            options={['production','research','candidate','archived']} /></Field>
          <Field label="Artifact path"><Input testId="model-path" value={draft.artifact_path}
            onChange={(e) => setDraft({ ...draft, artifact_path: e.target.value })}
            placeholder="/app/backend/models/mlb_hf/…" /></Field>
          <Field label="Feature schema"><Input testId="model-feature-schema" value={draft.feature_schema_version}
            onChange={(e) => setDraft({ ...draft, feature_schema_version: e.target.value })}
            placeholder="phase2b_lineup_v1" /></Field>
          <Field label="Stat families (comma)"><Input testId="model-families" value={draft.compatible_stat_families}
            onChange={(e) => setDraft({ ...draft, compatible_stat_families: e.target.value })}
            placeholder="hits, total_bases, …" /></Field>
        </div>
        <Input testId="model-notes" value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
          placeholder="Notes" style={{ width: '100%', marginTop: 8 }} />
        <div style={{ marginTop: 10 }}>
          <Btn variant="primary" onClick={submit} testId="model-submit-btn">Register / Upsert</Btn>
        </div>
      </div>

      {models.length === 0 ? (
        <div style={{ padding: 18, textAlign: 'center', color: DIM, fontSize: 12 }}>
          No models registered yet. Use the form above to register your production MLB-HF pickles, then clone to research.
        </div>
      ) : (
        <div data-testid="models-list" style={{ background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead><tr style={{ background: SURFACE, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
              <th style={th}>model_id</th><th style={th}>sport</th><th style={th}>fam</th>
              <th style={th}>version</th><th style={th}>mode</th><th style={th}>schema</th>
              <th style={th}>valid</th><th style={th}>updated</th><th style={th}></th>
            </tr></thead>
            <tbody>
              {models.map(m => (
                <tr key={m.model_id} style={{ borderTop: `1px solid ${BORDER}` }}>
                  <td style={{ ...td, fontFamily: 'monospace', color: TEXT, fontWeight: 600 }}>{m.model_id}</td>
                  <td style={td}>{m.sport}</td>
                  <td style={td}>{m.family}</td>
                  <td style={td}>{m.version}</td>
                  <td style={td}>
                    <Badge color={m.mode === 'production' ? ACCENT_2 : m.mode === 'research' ? ACCENT : m.mode === 'archived' ? DIM : WARN}>{m.mode}</Badge>
                  </td>
                  <td style={{ ...td, fontSize: 10, color: MUTED }}>{m.feature_schema_version}</td>
                  <td style={td}>
                    <Badge color={m.validation_status === 'passed' ? ACCENT_2 : m.validation_status === 'failed' ? BAD : DIM}>{m.validation_status || 'untested'}</Badge>
                  </td>
                  <td style={{ ...td, fontSize: 10, color: DIM }}>{fmtTs(m.updated_at)}</td>
                  <td style={td}>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      <Btn variant="ghost" onClick={() => clone(m)} testId={`model-clone-${m.model_id}`}>Clone</Btn>
                      <Btn variant="ghost" onClick={() => validate(m, true)} testId={`model-valid-${m.model_id}`}>✓Validate</Btn>
                      <Btn variant="ghost" onClick={() => validate(m, false)} testId={`model-invalid-${m.model_id}`}>✗</Btn>
                      {m.mode !== 'production' && m.mode !== 'research' && <Btn variant="ghost" onClick={() => activate(m)} testId={`model-activate-${m.model_id}`}>Activate</Btn>}
                      {m.mode !== 'archived' && m.mode !== 'production' && <Btn variant="ghost" onClick={() => archive(m)} testId={`model-archive-${m.model_id}`}>Archive</Btn>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// ── Warehouse Coverage panel (Diagnostics tab) ──────────────────────
function WarehouseCoverage({ token, defaultStart, defaultEnd, defaultSport, compact }) {
  const [q, setQ] = useState({
    sport: defaultSport || 'MLB',
    start: defaultStart || '',
    end:   defaultEnd   || '',
  });
  const [cov, setCov] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token || !q.start || !q.end) return;
    setBusy(true);
    try {
      const r = await apiFetch(token,
        `/coverage/?sport=${encodeURIComponent(q.sport)}&start=${q.start}&end=${q.end}`);
      setCov(r);
    } catch (e) { toast.error(`Coverage: ${e.message}`); }
    finally { setBusy(false); }
  }, [token, q.sport, q.start, q.end]);

  // Auto-load on prop change (compact mode in Workflow)
  useEffect(() => {
    if (compact) load();
  }, [compact, load]);

  const runFix = async (entry) => {
    if (!entry?.fix_job) return;
    if (!window.confirm(`Run ${entry.fix_job} for ${q.sport} ${q.start}..${q.end}?`)) return;
    try {
      const res = await apiFetch(token, '/jobs/run', {
        method: 'POST',
        body: JSON.stringify({
          module: entry.fix_job,
          args: ['--league', q.sport, '--start', q.start, '--end', q.end],
        }),
      });
      toast.success(`Queued ${entry.fix_job.split('.').pop()} · ${(res.job_id || '').slice(0,8)}`);
    } catch (e) { toast.error(`Fix failed: ${e.message}`); }
  };

  const offline = cov?.offline_mode_available;

  return (
    <Section testId="warehouse-coverage-section" accent={offline ? ACCENT_2 : WARN}
      title={compact ? 'Local Replay Warehouse Coverage' : 'Local Replay Warehouse'}
      subtitle={compact
        ? 'Cache-first architecture — once green, the pipeline runs entirely from local DB. No SGO API calls.'
        : 'Per-collection coverage % across the local replay warehouse. Use Run Fix to backfill missing windows once.'}
      right={
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {!compact && (
            <>
              <Select testId="cov-sport" value={q.sport} onChange={(e) => setQ({ ...q, sport: e.target.value })}
                options={['MLB','NBA','NFL']} />
              <Input testId="cov-start" value={q.start} placeholder="YYYY-MM-DD"
                onChange={(e) => setQ({ ...q, start: e.target.value })} style={{ width: 130 }} />
              <Input testId="cov-end" value={q.end} placeholder="YYYY-MM-DD"
                onChange={(e) => setQ({ ...q, end: e.target.value })} style={{ width: 130 }} />
            </>
          )}
          <Btn variant="ghost" onClick={load} testId="cov-refresh" disabled={busy}>
            {busy ? 'Loading…' : 'Refresh'}
          </Btn>
        </div>
      }>
      {!cov ? (
        <div style={{ padding: 16, color: DIM, fontSize: 12, textAlign: 'center' }}>
          {q.start && q.end ? '—' : 'Pick a window above to load coverage.'}
        </div>
      ) : (
        <>
          {/* Offline-mode banner */}
          <div data-testid="cov-offline-banner" style={{
            padding: 10, marginBottom: 12, borderRadius: 6, fontSize: 12, fontWeight: 600,
            background: offline ? `${ACCENT_2}1a` : `${WARN}14`,
            border: `1px solid ${offline ? ACCENT_2 : WARN}`,
            color: offline ? ACCENT_2 : WARN,
          }}>
            {offline
              ? `✓ OFFLINE-MODE READY — all ${cov.days_in_window} days fully cached. Replay/optimizer/grid will run from local DB only. No SGO calls.`
              : `⚠ Replay-ready: ${cov.replay_ready_pct}% (${cov.replay_ready_days}/${cov.days_in_window} days). Some layers need backfill — see per-collection cards below.`}
          </div>

          {/* Per-collection cards */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
            gap: 10, marginBottom: 12,
          }}>
            {Object.values(cov.by_collection || {}).map(c => {
              const pct = c.coverage_pct || 0;
              const color = pct >= 100 ? ACCENT_2 : pct >= 80 ? ACCENT_3 : pct >= 50 ? WARN : BAD;
              return (
                <div key={c.key} data-testid={`cov-card-${c.key}`} style={{
                  background: SURFACE_2, border: `1px solid ${BORDER}`,
                  borderLeft: `3px solid ${color}`,
                  borderRadius: 8, padding: 12,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                    <div style={{ fontSize: 11, color: TEXT, fontWeight: 700 }}>{c.label}</div>
                    <span style={{
                      fontSize: 11, color, fontWeight: 700, fontVariantNumeric: 'tabular-nums',
                    }}>{pct.toFixed(1)}%</span>
                  </div>
                  <div style={{ fontSize: 10, color: DIM, fontFamily: 'monospace' }}>
                    {c.collection}
                  </div>
                  {/* Mini bar */}
                  <div style={{ background: SURFACE_3, height: 4, borderRadius: 2, overflow: 'hidden', margin: '8px 0' }}>
                    <div style={{ background: color, width: `${pct}%`, height: '100%' }} />
                  </div>
                  <div style={{ fontSize: 11, color: MUTED, marginTop: 4 }}>
                    {fmtInt(c.row_count)} rows · {c.days_with_rows}/{c.days_total} days
                    {c.days_missing > 0 && (
                      <span style={{ color: BAD, marginLeft: 6 }}>· {c.days_missing} missing</span>
                    )}
                    {c.days_stale > 0 && (
                      <span style={{ color: WARN, marginLeft: 6 }}>· {c.days_stale} stale</span>
                    )}
                  </div>
                  {c.preview_missing?.length > 0 && (
                    <details style={{ marginTop: 8 }}>
                      <summary style={{ fontSize: 10, color: DIM, cursor: 'pointer' }}>
                        first {c.preview_missing.length} missing
                      </summary>
                      <div style={{ fontSize: 10, color: DIM, fontFamily: 'monospace', marginTop: 4, lineHeight: 1.5 }}>
                        {c.preview_missing.join(', ')}
                      </div>
                    </details>
                  )}
                  {c.days_missing > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <Btn variant="warn" testId={`cov-fix-${c.key}`} onClick={() => runFix(c)}>
                        Run Fix → backfill once
                      </Btn>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Unready day preview */}
          {!offline && cov.preview_unready_days?.length > 0 && (
            <div data-testid="cov-unready" style={{
              background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 10,
            }}>
              <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>
                First {cov.preview_unready_days.length} days needing backfill
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 4 }}>
                {cov.preview_unready_days.map(d => (
                  <div key={d.date} style={{ fontSize: 11, fontFamily: 'monospace', color: TEXT, display: 'flex', justifyContent: 'space-between' }}>
                    <span>{d.date}</span>
                    <span style={{ color: WARN, fontSize: 10 }}>{d.missing_layers.join(',')}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </Section>
  );
}

// ── Diagnostics tab ─────────────────────────────────────────────────
function DiagnosticsTab({ token }) {
  const [pf, setPf] = useState(null);
  const [busy, setBusy] = useState(false);
  const [fixing, setFixing] = useState(null);

  const load = useCallback(async () => {
    if (!token) return;
    setBusy(true);
    try { const res = await apiFetch(token, '/preflight/'); setPf(res); }
    catch (e) { toast.error(`Preflight: ${e.message}`); }
    finally { setBusy(false); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const runFix = async (w) => {
    if (!w.fix_job) { toast.error('No fix-job available'); return; }
    if (!window.confirm(`Run ${w.fix_job} to fix "${w.code}"?`)) return;
    setFixing(w.code);
    try {
      const res = await apiFetch(token, '/jobs/run', { method: 'POST',
        body: JSON.stringify({ module: w.fix_job, args: w.fix_args || [] }) });
      toast.success(`Queued fix · ${(res.job_id || '').slice(0,8)}`);
      setTimeout(load, 3000);
    } catch (e) { toast.error(`Fix failed: ${e.message}`); }
    finally { setFixing(null); }
  };

  return (
    <Section testId="diagnostics-section" accent={WARN} title="Diagnostics · Preflight"
      subtitle="Read-only health snapshot. Surfaces missing deps + the exact job that fixes each gap."
      right={<Btn variant="ghost" onClick={load} disabled={busy} testId="diag-refresh">{busy ? 'Loading…' : 'Refresh'}</Btn>}>
      {!pf ? <div style={{ padding: 18, textAlign: 'center', color: DIM, fontSize: 12 }}>—</div> : (
        <>
          {/* Warnings */}
          {pf.warnings?.length > 0 && (
            <div data-testid="diag-warnings" style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 11, color: WARN, textTransform: 'uppercase', marginBottom: 6 }}>
                Warnings ({pf.warnings.length})
              </div>
              {pf.warnings.map(w => (
                <div key={w.code} style={{
                  background: w.severity === 'high' ? `${BAD}14` : w.severity === 'medium' ? `${WARN}14` : `${MUTED}14`,
                  border: `1px solid ${w.severity === 'high' ? BAD : w.severity === 'medium' ? WARN : BORDER}`,
                  borderRadius: 6, padding: 10, marginBottom: 8, fontSize: 12,
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10,
                }}>
                  <div>
                    <div style={{ color: w.severity === 'high' ? BAD : w.severity === 'medium' ? WARN : TEXT, fontWeight: 600 }}>
                      <code style={{ fontFamily: 'monospace' }}>{w.code}</code> · <span style={{ textTransform: 'uppercase', fontSize: 10 }}>{w.severity}</span>
                    </div>
                    <div style={{ marginTop: 4 }}>{w.message}</div>
                    {w.fix_job && <div style={{ fontSize: 10, color: DIM, marginTop: 4, fontFamily: 'monospace' }}>fix: {w.fix_job}</div>}
                  </div>
                  {w.fix_job && <Btn variant="primary" onClick={() => runFix(w)}
                    disabled={fixing === w.code} testId={`diag-fix-${w.code}`}>
                    {fixing === w.code ? 'Queueing…' : 'Run Fix'}
                  </Btn>}
                </div>
              ))}
            </div>
          )}

          {/* Local warehouse coverage — primary offline-mode signal */}
          <WarehouseCoverage token={token} />

          {/* Connection */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10, marginBottom: 14 }}>
            <StatCard testId="diag-api" label="Admin API" value="✓ connected" color={ACCENT_2}
              hint={`${pf.admin_api?.agent_id} · ${pf.admin_api?.token_hash}`} />
            <StatCard testId="diag-allowed-jobs" label="Allowed jobs"
              value={fmtInt(pf.policy?.allowed_jobs?.length)} color={ACCENT} />
            <StatCard testId="diag-collections" label="Tracked collections"
              value={fmtInt(Object.keys(pf.collections || {}).length)} color={ACCENT_3} />
            <StatCard testId="diag-warns" label="Warnings"
              value={fmtInt(pf.warnings?.length)}
              color={(pf.warnings?.length || 0) === 0 ? ACCENT_2 : WARN} />
          </div>

          {/* Models */}
          <div style={{
            background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12, marginBottom: 14,
          }}>
            <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', marginBottom: 8 }}>Model Artifact Directories</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10 }}>
              {Object.entries(pf.models || {}).map(([sport, info]) => (
                <div key={sport} data-testid={`diag-model-${sport}`} style={{
                  padding: 10, background: SURFACE_3, borderRadius: 6,
                  borderLeft: `3px solid ${info.exists ? ACCENT_2 : BAD}`,
                }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: TEXT }}>{sport}</div>
                  <div style={{ fontSize: 10, color: DIM, fontFamily: 'monospace', marginTop: 4, wordBreak: 'break-all' }}>
                    {info.path || '(not found)'}
                  </div>
                  <div style={{ fontSize: 11, color: info.n_pickles > 0 ? ACCENT_2 : BAD, marginTop: 4 }}>
                    {info.n_pickles} pickles
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Collection counts */}
          <div data-testid="diag-collections-table" style={{ background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, overflow: 'hidden', marginBottom: 14 }}>
            <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', padding: '8px 12px', borderBottom: `1px solid ${BORDER}` }}>Collection State</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead><tr style={{ background: SURFACE_3, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
                <th style={th}>collection</th><th style={th}>role</th><th style={th}>league</th>
                <th style={th}>exists</th><th style={th}>row count</th>
              </tr></thead>
              <tbody>
                {Object.entries(pf.collections || {}).map(([name, info]) => (
                  <tr key={name} style={{ borderTop: `1px solid ${BORDER}` }}>
                    <td style={{ ...td, color: TEXT, fontWeight: 600 }}>{name}</td>
                    <td style={td}><Badge color={info.role === 'write' ? ACCENT_2 : ACCENT_3}>{info.role}</Badge></td>
                    <td style={td}>{info.league}</td>
                    <td style={{ ...td, color: info.exists ? ACCENT_2 : BAD }}>{info.exists ? '✓' : '✗'}</td>
                    <td style={{ ...td, color: info.count > 0 ? TEXT : DIM, fontWeight: 600 }}>{fmtInt(info.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Recent jobs */}
          <div style={{ background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, overflow: 'hidden', marginBottom: 14 }}>
            <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', padding: '8px 12px', borderBottom: `1px solid ${BORDER}` }}>Recent Jobs (last 10)</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead><tr style={{ background: SURFACE_3, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
                <th style={th}>job_id</th><th style={th}>module</th><th style={th}>status</th>
                <th style={th}>queued</th><th style={th}>finished</th>
              </tr></thead>
              <tbody>
                {(pf.recent_jobs || []).map(j => (
                  <tr key={j.job_id} style={{ borderTop: `1px solid ${BORDER}` }}>
                    <td style={{ ...td, fontFamily: 'monospace' }}>{j.job_id?.slice(0,8)}</td>
                    <td style={{ ...td, fontFamily: 'monospace', fontSize: 10 }}>{j.module}</td>
                    <td style={td}><Badge color={j.status === 'succeeded' ? ACCENT_2 : ['failed','errored','cancelled'].includes(j.status) ? BAD : ACCENT}>{j.status}</Badge></td>
                    <td style={{ ...td, fontSize: 10, color: DIM }}>{fmtTs(j.queued_at)}</td>
                    <td style={{ ...td, fontSize: 10, color: DIM }}>{fmtTs(j.finished_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Isolation promise */}
          <div style={{
            background: SURFACE_3, border: `1px solid ${ACCENT_2}`, borderRadius: 8, padding: 12,
            fontSize: 11, color: ACCENT_2, lineHeight: 1.6,
          }}>
            <div style={{ fontSize: 10, textTransform: 'uppercase', marginBottom: 4 }}>Research Isolation</div>
            {pf.policy?.isolation_promise}
          </div>
        </>
      )}
    </Section>
  );
}

// ── Auto-Optimizer tab ─────────────────────────────────────────────
const OPTIMIZER_GOALS = [
  { value: 'balanced',    label: 'Balanced (recommended)' },
  { value: 'hit_rate',    label: 'Highest hit rate' },
  { value: 'roi',         label: 'Highest ROI / EV' },
  { value: 'calibration', label: 'Best calibration (|Δcal|→0)' },
  { value: 'stability',   label: 'Best stability (high consistency, low DD)' },
];

const OPTIMIZER_AXES = [
  { key: 'hr_l20_min', label: 'HR L20 min', default: '0.55, 0.65, 0.70, 0.75, 0.80' },
  { key: 'hr_l10_min', label: 'HR L10 min', default: '0.55, 0.65, 0.70' },
  { key: 'hr_l5_min',  label: 'HR L5 min',  default: '0.50, 0.60, 0.70' },
  { key: 'cv_max',     label: 'CV max',     default: '0.50, 0.70, 0.90, 1.10' },
  { key: 'edge_min',   label: 'Edge min',   default: '0.02, 0.05, 0.08, 0.10' },
  { key: 'tp_min',     label: 'TP min',     default: '0.50, 0.55, 0.60, 0.65' },
];

const OPTIMIZER_FILTERS = [
  { key: 'vision_score_min',           label: 'Vision score min',     ph: '60' },
  { key: 'sharp_book_count_min',       label: 'Sharp books min',      ph: '2' },
  { key: 'devig_book_count_min',       label: 'Devig books min',      ph: '3' },
  { key: 'market_width_max',           label: 'Market width max',     ph: '0.20' },
  { key: 'consensus_disagreement_max', label: 'Consensus disag max',  ph: '0.15' },
  { key: 'projection_margin_min',      label: 'Proj margin min',      ph: '0.50' },
];

function parseAxisList(s) {
  if (!s || !s.trim()) return null;
  const vals = s.split(',').map(x => parseFloat(x.trim())).filter(x => !Number.isNaN(x));
  return vals.length ? vals : null;
}

function OptimizerTab({ token }) {
  const [form, setForm] = useState({
    sport: 'MLB', start: '', end: '',
    tiers: { safe_haven: true, front_lines: true, war_zone: true },
    stat_families: '',   // empty = all (discovered server-side)
    odds_buckets: '',    // empty = all
    sides: { OVER: true, UNDER: true },
    min_bets: 30,
    max_configs_per_cell: 500,
    optimization_goal: 'balanced',
    worker_limit: 4,
    grid: Object.fromEntries(OPTIMIZER_AXES.map(a => [a.key, a.default])),
    filters: Object.fromEntries(OPTIMIZER_FILTERS.map(f => [f.key, ''])),
  });
  const [runId, setRunId] = useState(null);
  const [status, setStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);

  const launch = async () => {
    if (!form.start || !form.end) { toast.error('Start and end required'); return; }
    setBusy(true); setResults(null);
    try {
      const body = {
        sport: form.sport, start: form.start, end: form.end,
        tiers: Object.entries(form.tiers).filter(([, v]) => v).map(([k]) => k),
        stat_families: form.stat_families.trim()
          ? form.stat_families.split(',').map(s => s.trim()).filter(Boolean) : null,
        odds_buckets: form.odds_buckets.trim()
          ? form.odds_buckets.split(',').map(s => s.trim()).filter(Boolean) : null,
        sides: Object.entries(form.sides).filter(([, v]) => v).map(([k]) => k),
        min_bets: parseInt(form.min_bets, 10) || 30,
        max_configs_per_cell: parseInt(form.max_configs_per_cell, 10) || 500,
        optimization_goal: form.optimization_goal,
        worker_limit: parseInt(form.worker_limit, 10) || 4,
        grid: Object.fromEntries(OPTIMIZER_AXES.map(a => [a.key, parseAxisList(form.grid[a.key])])
          .filter(([, v]) => v)),
        filters: Object.fromEntries(OPTIMIZER_FILTERS
          .map(f => [f.key, form.filters[f.key] ? parseFloat(form.filters[f.key]) : null])
          .filter(([, v]) => v !== null && !Number.isNaN(v))),
      };
      const res = await apiFetch(token, '/optimizer/run', {
        method: 'POST', body: JSON.stringify(body),
      });
      setRunId(res.run_id); setStatus(null); setResults(null);
      toast.success(`Optimizer started · ${res.run_id} · ${fmtInt(res.replay_rows_in_window)} replay rows in window`);
    } catch (e) {
      toast.error(`Launch failed: ${e.message}`);
    } finally { setBusy(false); }
  };

  // Status polling
  useEffect(() => {
    if (!runId || !token) return;
    let stopped = false;
    const poll = async () => {
      try {
        const r = await apiFetch(token, `/optimizer/${runId}`);
        if (stopped) return;
        setStatus(r.state);
        if (r.state?.status === 'succeeded' || r.state?.status === 'cancelled' || r.state?.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current);
          if (r.state.status === 'succeeded') {
            // auto-load results
            try {
              const rr = await apiFetch(token, `/optimizer/${runId}/results?limit=25`);
              setResults(rr);
            } catch (e) { /* ignore */ }
          }
        }
      } catch (e) {
        if (e.status === 404) {
          // not ready yet — keep polling
        } else {
          console.error('[optimizer] poll', e.message);
        }
      }
    };
    poll();
    pollRef.current = setInterval(poll, 2000);
    return () => { stopped = true; if (pollRef.current) clearInterval(pollRef.current); };
  }, [runId, token]);

  const cancel = async () => {
    if (!runId) return;
    try { await apiFetch(token, `/optimizer/${runId}/cancel`, { method: 'POST' });
      toast.success('Cancel signal sent'); }
    catch (e) { toast.error(`Cancel failed: ${e.message}`); }
  };

  const saveAsCandidates = async () => {
    const k = parseInt(window.prompt('Save top-K configs as candidates:', '10'), 10);
    if (!k || k < 1) return;
    try {
      const res = await apiFetch(token, `/optimizer/${runId}/save_as_candidates`, {
        method: 'POST', body: JSON.stringify({ top_k: k, note: 'Saved from /admin/testing' }),
      });
      toast.success(`Saved ${res.saved} candidates → candidate_thresholds`);
    } catch (e) { toast.error(`Save failed: ${e.message}`); }
  };

  const setTestingDefault = async () => {
    try {
      const res = await apiFetch(token, `/optimizer/${runId}/set_testing_default`, { method: 'POST' });
      toast.success(`Set as testing default · best score ${fmtNum(res.doc?.best?.score, 2)}`);
    } catch (e) { toast.error(`Set default failed: ${e.message}`); }
  };

  const exportJson = () => {
    if (!results) return;
    const blob = new Blob([JSON.stringify(results, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = `optimizer-${runId}-${new Date().toISOString().slice(0,10)}.json`;
    a.click(); URL.revokeObjectURL(url);
  };

  const progressPct = status?.total_combos
    ? Math.min(100, (status.combos_tested || 0) * 100 / status.total_combos)
    : 0;

  return (
    <Section testId="optimizer-section" accent={ACCENT}
      title="Auto-Optimizer"
      subtitle="Sweeps hundreds-to-thousands of threshold combos per (tier × stat_family × odds_bucket) cell over the cached SSOT replay rows. Runs in parallel, ranks by your chosen goal, persists candidates."
      right={runId && status?.status === 'running' && (
        <Btn variant="danger" onClick={cancel} testId="optimizer-cancel">Cancel</Btn>
      )}>
      {/* Config form */}
      <div style={{
        background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12, marginBottom: 14,
      }}>
        <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 8 }}>Scope</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8, marginBottom: 12 }}>
          <Field label="Sport"><Select testId="opt-sport" value={form.sport}
            onChange={(e) => setForm({ ...form, sport: e.target.value })} options={['MLB','NBA','NFL']} /></Field>
          <Field label="Start"><Input testId="opt-start" value={form.start}
            onChange={(e) => setForm({ ...form, start: e.target.value })} placeholder="YYYY-MM-DD" /></Field>
          <Field label="End"><Input testId="opt-end" value={form.end}
            onChange={(e) => setForm({ ...form, end: e.target.value })} placeholder="YYYY-MM-DD" /></Field>
          <Field label="Min bets / cell"><Input testId="opt-minbets" type="number" value={form.min_bets}
            onChange={(e) => setForm({ ...form, min_bets: e.target.value })} /></Field>
          <Field label="Max configs / cell"><Input testId="opt-maxconfigs" type="number" value={form.max_configs_per_cell}
            onChange={(e) => setForm({ ...form, max_configs_per_cell: e.target.value })} /></Field>
          <Field label="Parallel workers"><Input testId="opt-workers" type="number" value={form.worker_limit}
            onChange={(e) => setForm({ ...form, worker_limit: e.target.value })} /></Field>
          <Field label="Goal"><Select testId="opt-goal" value={form.optimization_goal}
            onChange={(e) => setForm({ ...form, optimization_goal: e.target.value })}
            options={OPTIMIZER_GOALS} /></Field>
        </div>

        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Tiers</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {['safe_haven','front_lines','war_zone'].map(t => (
                <label key={t} data-testid={`opt-tier-${t}`} style={{
                  background: form.tiers[t] ? `${ACCENT}22` : SURFACE_3,
                  border: `1px solid ${form.tiers[t] ? ACCENT : BORDER}`,
                  color: form.tiers[t] ? ACCENT : MUTED,
                  borderRadius: 999, padding: '5px 12px', fontSize: 11, cursor: 'pointer',
                }}>
                  <input type="checkbox" checked={!!form.tiers[t]}
                    onChange={(e) => setForm({ ...form, tiers: { ...form.tiers, [t]: e.target.checked } })}
                    style={{ display: 'none' }} />
                  {t.replace('_', ' ')}
                </label>
              ))}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Sides</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {['OVER','UNDER'].map(s => (
                <label key={s} data-testid={`opt-side-${s}`} style={{
                  background: form.sides[s] ? `${ACCENT_3}22` : SURFACE_3,
                  border: `1px solid ${form.sides[s] ? ACCENT_3 : BORDER}`,
                  color: form.sides[s] ? ACCENT_3 : MUTED,
                  borderRadius: 999, padding: '5px 12px', fontSize: 11, cursor: 'pointer',
                }}>
                  <input type="checkbox" checked={!!form.sides[s]}
                    onChange={(e) => setForm({ ...form, sides: { ...form.sides, [s]: e.target.checked } })}
                    style={{ display: 'none' }} />
                  {s}
                </label>
              ))}
            </div>
          </div>
          <Field label="Stat families (comma — empty = all)">
            <Input testId="opt-families" value={form.stat_families}
              onChange={(e) => setForm({ ...form, stat_families: e.target.value })}
              placeholder="empty = discovered from replay cache" style={{ minWidth: 280 }} />
          </Field>
          <Field label="Odds buckets (comma — empty = all)">
            <Input testId="opt-buckets" value={form.odds_buckets}
              onChange={(e) => setForm({ ...form, odds_buckets: e.target.value })}
              placeholder="odds_-200_-100, odds_-100_-0, …" style={{ minWidth: 240 }} />
          </Field>
        </div>

        <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Grid axes (comma-separated values per axis)</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8, marginBottom: 12 }}>
          {OPTIMIZER_AXES.map(ax => (
            <Field key={ax.key} label={ax.label}>
              <Input testId={`opt-axis-${ax.key}`} value={form.grid[ax.key]}
                onChange={(e) => setForm({ ...form, grid: { ...form.grid, [ax.key]: e.target.value } })} />
            </Field>
          ))}
        </div>

        <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Required filters (single value, empty = ignored)</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 8, marginBottom: 12 }}>
          {OPTIMIZER_FILTERS.map(f => (
            <Field key={f.key} label={f.label}>
              <Input testId={`opt-filter-${f.key}`} value={form.filters[f.key]}
                onChange={(e) => setForm({ ...form, filters: { ...form.filters, [f.key]: e.target.value } })}
                placeholder={f.ph} />
            </Field>
          ))}
        </div>

        <Btn variant="primary" onClick={launch} testId="opt-launch-btn" disabled={busy || !token}>
          {busy ? 'Launching…' : '▶ Run Auto-Optimizer'}
        </Btn>
      </div>

      {/* Progress */}
      {status && (
        <div data-testid="opt-progress" style={{
          background: SURFACE_2, border: `1px solid ${status.status === 'running' ? ACCENT : status.status === 'succeeded' ? ACCENT_2 : status.status === 'failed' ? BAD : BORDER}`,
          borderRadius: 8, padding: 14, marginBottom: 14,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase' }}>
              Run {status.run_id} · <Badge color={statusColor(status.status)}>{status.status}</Badge>
            </div>
            <div style={{ fontSize: 11, color: DIM, fontFamily: 'monospace' }}>
              elapsed {status.elapsed_s ? fmtNum(status.elapsed_s, 1) : '—'}s
              {status.eta_s ? ` · ETA ${fmtNum(status.eta_s, 1)}s` : ''}
            </div>
          </div>
          <div style={{ background: SURFACE_3, height: 8, borderRadius: 4, overflow: 'hidden', marginBottom: 10 }}>
            <div data-testid="opt-progress-bar" style={{
              background: status.status === 'failed' ? BAD : ACCENT,
              width: `${progressPct}%`, height: '100%',
              transition: 'width 300ms ease',
            }} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 8 }}>
            <StatCard testId="opt-stat-tested" label="Combos tested"
              value={`${fmtInt(status.combos_tested)} / ${fmtInt(status.total_combos)}`} color={ACCENT} />
            <StatCard testId="opt-stat-cells" label="Cells done"
              value={`${fmtInt(status.cells_done)} / ${fmtInt(status.cells_total)}`} color={ACCENT_3} />
            <StatCard testId="opt-stat-skipped" label="Skipped low-sample"
              value={fmtInt(status.combos_skipped_low_sample)} color={WARN} />
            <StatCard testId="opt-stat-empty" label="Empty cells"
              value={fmtInt(status.cells_skipped_empty)} color={DIM} />
            <StatCard testId="opt-stat-failures" label="Failures"
              value={fmtInt(status.failures?.length)} color={(status.failures?.length || 0) > 0 ? BAD : ACCENT_2} />
          </div>
          {status.best && (
            <div style={{ marginTop: 10, fontSize: 12, color: TEXT }}>
              <span style={{ color: MUTED }}>best so far: </span>
              <code style={{ color: ACCENT }}>{status.best.tier} · {status.best.stat_family} · {status.best.odds_bucket}</code>
              {' → '}
              <strong style={{ color: ACCENT_2 }}>HR={fmtPct(status.best.hit_rate)}</strong> ·
              ROI={fmtPct(status.best.roi)} · n={fmtInt(status.best.n_bets)} · score={fmtNum(status.best.score, 2)}
            </div>
          )}
          {status.error && (
            <div style={{ marginTop: 10, fontSize: 11, color: BAD, fontFamily: 'monospace' }}>error: {status.error}</div>
          )}
        </div>
      )}

      {/* Results */}
      {results && (
        <div data-testid="opt-results" style={{
          background: SURFACE_2, border: `1px solid ${ACCENT_2}`, borderRadius: 8, padding: 14,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: ACCENT_2, textTransform: 'uppercase' }}>
              Results · {fmtInt(results.n_results)} configs ranked
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <Btn variant="success" onClick={saveAsCandidates} testId="opt-save-candidates">Save top-K → candidate_thresholds</Btn>
              <Btn variant="primary" onClick={setTestingDefault} testId="opt-set-default">Set as Testing Default</Btn>
              <Btn variant="ghost" onClick={exportJson} testId="opt-export">Export JSON</Btn>
            </div>
          </div>

          {/* Best by tier */}
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Best Config by Tier</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
              {Object.entries(results.best_by_tier || {}).map(([tier, r]) => (
                <div key={tier} style={{
                  background: SURFACE_3, border: `1px solid ${BORDER}`,
                  borderLeft: `3px solid ${tier === 'safe_haven' ? ACCENT_2 : tier === 'front_lines' ? ACCENT_3 : WARN}`,
                  borderRadius: 6, padding: 10,
                }}>
                  <Badge color={tier === 'safe_haven' ? ACCENT_2 : tier === 'front_lines' ? ACCENT_3 : WARN}>{tier}</Badge>
                  <div style={{ fontSize: 12, marginTop: 6 }}>
                    <code style={{ color: ACCENT }}>{r?.stat_family}</code> · {r?.odds_bucket}
                  </div>
                  <div style={{ fontSize: 11, color: TEXT, marginTop: 4 }}>
                    HR=<strong style={{ color: ACCENT_2 }}>{fmtPct(r?.hit_rate)}</strong> · ROI={fmtPct(r?.roi)} · Δcal={fmtPct(r?.calibration_delta, 2)} · n={fmtInt(r?.n_bets)} · score={fmtNum(r?.score, 2)}
                  </div>
                  <div style={{ fontSize: 10, color: DIM, marginTop: 4, fontFamily: 'monospace' }}>
                    hr_l20≥{fmtPct(r?.thresholds?.hr_l20_min)} · cv≤{fmtNum(r?.thresholds?.cv_max)} · edge≥{fmtPct(r?.thresholds?.edge_min)} · tp≥{fmtPct(r?.thresholds?.tp_min)}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Top 25 table */}
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Top 25 by Score</div>
            <ResultsRankTable rows={results.top || []} testId="opt-top-table" accent={ACCENT_2} />
          </div>

          {/* Best by family + bucket */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 14 }}>
            <div data-testid="opt-best-by-family" style={{ background: SURFACE_3, border: `1px solid ${BORDER}`, borderRadius: 6, padding: 10, maxHeight: 320, overflowY: 'auto' }}>
              <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Best by Stat Family</div>
              {Object.entries(results.best_by_stat_family || {})
                .sort(([, a], [, b]) => (b.score || 0) - (a.score || 0))
                .map(([fam, r]) => (
                <div key={fam} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0', borderTop: `1px solid ${BORDER}`, fontSize: 11 }}>
                  <code style={{ color: ACCENT }}>{fam}</code>
                  <span style={{ color: TEXT }}>HR={fmtPct(r.hit_rate)} · n={fmtInt(r.n_bets)} · {r.tier}</span>
                </div>
              ))}
            </div>
            <div data-testid="opt-best-by-bucket" style={{ background: SURFACE_3, border: `1px solid ${BORDER}`, borderRadius: 6, padding: 10, maxHeight: 320, overflowY: 'auto' }}>
              <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>Best by Odds Bucket</div>
              {Object.entries(results.best_by_odds_bucket || {})
                .sort(([, a], [, b]) => (b.score || 0) - (a.score || 0))
                .map(([bk, r]) => (
                <div key={bk} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0', borderTop: `1px solid ${BORDER}`, fontSize: 11 }}>
                  <code style={{ color: ACCENT_3, fontFamily: 'monospace' }}>{bk}</code>
                  <span style={{ color: TEXT }}>ROI={fmtPct(r.roi)} · HR={fmtPct(r.hit_rate)} · {r.tier}/{r.stat_family}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Overfit warnings */}
          {results.overfit_warnings?.length > 0 && (
            <div data-testid="opt-overfit-warnings" style={{
              background: `${WARN}14`, border: `1px solid ${WARN}`, borderRadius: 6, padding: 10, marginBottom: 14, fontSize: 12, color: WARN,
            }}>
              ⚠ {results.overfit_warnings.length} top-25 entries flagged as <strong>likely overfit</strong> (small sample). Inspect before saving as candidates.
            </div>
          )}

          {/* Worst */}
          <div>
            <div style={{ fontSize: 10, color: BAD, textTransform: 'uppercase', marginBottom: 6 }}>Worst configs (DO NOT USE)</div>
            <ResultsRankTable rows={results.worst || []} testId="opt-worst-table" accent={BAD} />
          </div>
        </div>
      )}
    </Section>
  );
}

function ResultsRankTable({ rows, testId, accent }) {
  return (
    <div data-testid={testId} style={{
      background: SURFACE_3, border: `1px solid ${BORDER}`, borderLeft: `3px solid ${accent}`,
      borderRadius: 6, overflow: 'hidden',
    }}>
      <div style={{ maxHeight: 360, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead><tr style={{ background: SURFACE_2, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
            <th style={th}>tier</th><th style={th}>family</th><th style={th}>bucket</th>
            <th style={th}>n</th><th style={th}>HR</th><th style={th}>ROI</th>
            <th style={th}>Δcal</th><th style={th}>cons.</th><th style={th}>DD</th>
            <th style={th}>thresholds</th><th style={th}>score</th>
          </tr></thead>
          <tbody>
            {rows.length === 0 ? <tr><td colSpan={11} style={{ padding: 18, textAlign: 'center', color: DIM }}>—</td></tr> :
              rows.map((r, i) => (
                <tr key={i} style={{
                  borderTop: `1px solid ${BORDER}`,
                  background: r.overfit_flag ? `${WARN}08` : 'transparent',
                }}>
                  <td style={td}>{r.tier}</td>
                  <td style={{ ...td, color: ACCENT }}>{r.stat_family}</td>
                  <td style={{ ...td, fontSize: 10 }}>{r.odds_bucket}</td>
                  <td style={td}>{fmtInt(r.n_bets)}{r.overfit_flag && <span style={{ color: WARN, marginLeft: 4 }}>⚠</span>}</td>
                  <td style={{ ...td, color: (r.hit_rate || 0) > 0.6 ? ACCENT_2 : (r.hit_rate || 0) < 0.5 ? BAD : TEXT, fontWeight: 600 }}>{fmtPct(r.hit_rate)}</td>
                  <td style={{ ...td, color: (r.roi || 0) > 0 ? ACCENT_2 : BAD }}>{fmtPct(r.roi)}</td>
                  <td style={td}>{fmtPct(r.calibration_delta, 2)}</td>
                  <td style={td}>{fmtNum(r.daily_consistency, 2)}</td>
                  <td style={td}>{fmtNum(r.max_drawdown_units, 1)}u</td>
                  <td style={{ ...td, fontSize: 10, color: DIM }}>
                    hr_l20≥{fmtPct(r.thresholds?.hr_l20_min)} · cv≤{fmtNum(r.thresholds?.cv_max)} · edge≥{fmtPct(r.thresholds?.edge_min)} · tp≥{fmtPct(r.thresholds?.tp_min)}
                  </td>
                  <td style={{ ...td, color: TEXT, fontWeight: 600 }}>{fmtNum(r.score, 2)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Root ────────────────────────────────────────────────────────────
const TABS = [
  { id: 'workflow',    label: 'Workflow' },
  { id: 'optimizer',   label: 'Optimizer' },
  { id: 'sweep',       label: 'Sweep' },
  { id: 'results',     label: 'Results' },
  { id: 'candidates',  label: 'Candidates' },
  { id: 'models',      label: 'Models' },
  { id: 'diagnostics', label: 'Diagnostics' },
];

export default function AdminTesting() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '');
  const [whoami, setWhoami] = useState(null);
  const [locked, setLocked] = useState(false);
  const [tab, setTab] = useState('workflow');
  const [pendingCandidate, setPendingCandidate] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const authed = !!whoami;

  return (
    <div data-testid="admin-testing-page" style={{
      minHeight: '100vh', background: BG, color: TEXT,
      fontFamily: "'Inter', system-ui, sans-serif", padding: 20,
    }}>
      <div style={{ maxWidth: 1500, margin: '0 auto' }}>
        <WarningBanner />

        <div style={{ marginBottom: 16 }}>
          <h1 data-testid="admin-testing-title" style={{
            fontSize: 26, margin: 0, fontWeight: 800, letterSpacing: -0.5,
            display: 'flex', alignItems: 'center', gap: 10,
          }}>
            <span style={{ color: ACCENT }}>●</span>
            Universal Historical Testing — Internal Quant Terminal
          </h1>
          <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>
            Self-service · Pipeline-driven · Sport-agnostic · Audit-logged · Read-isolated from production scoring
          </div>
        </div>

        <TokenGate token={token} setToken={setToken} whoami={whoami} setWhoami={setWhoami}
          locked={locked} setLocked={setLocked} />

        {authed && !locked ? (
          <>
            {/* Tab strip */}
            <div data-testid="admin-tabs" style={{
              display: 'flex', gap: 4, marginBottom: 14, borderBottom: `1px solid ${BORDER}`,
            }}>
              {TABS.map(t => (
                <button key={t.id} data-testid={`admin-tab-${t.id}`}
                  onClick={() => setTab(t.id)}
                  style={{
                    background: tab === t.id ? SURFACE : 'transparent',
                    color: tab === t.id ? ACCENT : MUTED,
                    border: 'none', borderBottom: tab === t.id ? `2px solid ${ACCENT}` : '2px solid transparent',
                    padding: '8px 16px', fontSize: 12, fontWeight: 600, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: 0.5,
                  }}>{t.label}</button>
              ))}
            </div>

            {tab === 'workflow'    && <WorkflowTab token={token} onPipelineFinished={() => setRefreshKey(k => k + 1)} />}
            {tab === 'optimizer'   && <OptimizerTab token={token} />}
            {tab === 'sweep'       && <SweepTab token={token} />}
            {tab === 'results'     && <ResultsTab token={token} refreshKey={refreshKey}
              onSaveCandidate={(c) => { setPendingCandidate(c); setTab('candidates'); }} />}
            {tab === 'candidates'  && <CandidatesTab token={token} pendingCandidate={pendingCandidate}
              setPendingCandidate={setPendingCandidate} />}
            {tab === 'models'      && <ModelsTab token={token} />}
            {tab === 'diagnostics' && <DiagnosticsTab token={token} />}
          </>
        ) : locked ? (
          <div data-testid="admin-testing-locked-banner" style={{
            padding: 40, textAlign: 'center', color: WARN, fontSize: 14,
            background: SURFACE, border: `1px solid ${WARN}`, borderRadius: 12,
          }}>
            🔒 Page locked. Re-authenticate above to continue.
          </div>
        ) : (
          <div data-testid="admin-testing-locked" style={{
            padding: 40, textAlign: 'center', color: DIM, fontSize: 13,
            background: SURFACE, border: `1px dashed ${BORDER}`, borderRadius: 12,
          }}>
            🔒 Authenticate with the Emergent admin token to unlock the command center.
          </div>
        )}

        <div style={{
          marginTop: 28, fontSize: 10, color: DIM, textAlign: 'center', lineHeight: 1.6,
        }}>
          Talks to <code>{ADMIN}</code> · Token in <code>localStorage[{TOKEN_KEY}]</code> ·
          Pipeline state in <code>localStorage[{PIPELINE_KEY}]</code> ·
          Candidates in <code>localStorage[{CANDIDATES_KEY}]</code>
        </div>
      </div>
    </div>
  );
}

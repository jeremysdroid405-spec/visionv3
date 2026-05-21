/* eslint-disable react-hooks/exhaustive-deps */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

/**
 * Admin Testing — Self-Service Historical Replay Command Center.
 *
 * Goal: user opens /admin/testing, enters token, picks sport/dates/tiers,
 * clicks a single button, watches a guided 7-step pipeline run end-to-end,
 * and gets plain-English results + candidate management — without ever
 * leaving the page or talking to Emergent.
 *
 * Token-protected (X-Admin-Token, validated against /auth/whoami).
 * Token + pipeline state kept ONLY in localStorage. Route is unlinked.
 */
const API = process.env.REACT_APP_BACKEND_URL;
const ADMIN = `${API}/api/emergent-admin`;

// Palette
const BG = '#09090B';
const SURFACE = '#18181B';
const SURFACE_2 = '#0F0F11';
const SURFACE_3 = '#1F1F23';
const BORDER = '#27272A';
const BORDER_STRONG = '#3F3F46';
const MUTED = '#71717A';
const DIM = '#52525B';
const TEXT = '#FAFAFA';
const ACCENT = '#A78BFA';
const ACCENT_2 = '#34D399';
const ACCENT_3 = '#60A5FA';
const WARN = '#FBBF24';
const BAD = '#F87171';

const TOKEN_KEY = 'emergentAdminToken';
const PIPELINE_KEY = 'emergentAdminPipeline';
const CANDIDATES_KEY = 'emergentAdminCandidates';
const REPLAY_COLL = 'sgo_propvision_full_pipeline_replay';
const GRID_RUNS_COLL = 'research_grid_runs';
const GRID_RESULTS_COLL = 'research_grid_results';

// ── HTTP ──────────────────────────────────────────────────────────────────
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
  try { parsed = body ? JSON.parse(body) : {}; } catch { parsed = { _raw: body }; }
  if (!res.ok) {
    const err = new Error(parsed?.detail || parsed?.message || `HTTP ${res.status}`);
    err.status = res.status; err.body = parsed;
    throw err;
  }
  return parsed;
}

// ── helpers ───────────────────────────────────────────────────────────────
const fmtPct = (v, digits = 1) => v === null || v === undefined || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`;
const fmtNum = (v, digits = 2) => v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(digits);
const fmtInt = (v) => v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toLocaleString();
const fmtTs = (iso) => { if (!iso) return '—'; try { return new Date(iso).toLocaleString(); } catch { return String(iso); } };
const statusColor = (s) => s === 'succeeded' ? ACCENT_2 : (s === 'running' || s === 'queued') ? ACCENT : (s === 'failed' || s === 'errored' || s === 'cancelled') ? BAD : MUTED;

// American odds → payout multiplier
function payoutFromOdds(odds) {
  if (odds === null || odds === undefined) return null;
  const o = Number(odds);
  if (Number.isNaN(o)) return null;
  return o > 0 ? o / 100 : 100 / Math.abs(o);
}

// ── primitives ────────────────────────────────────────────────────────────
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
  const palette = {
    default: { bg: BORDER, fg: TEXT, br: BORDER_STRONG },
    primary: { bg: ACCENT, fg: BG, br: ACCENT },
    success: { bg: ACCENT_2, fg: BG, br: ACCENT_2 },
    danger:  { bg: BAD, fg: BG, br: BAD },
    warn:    { bg: WARN, fg: BG, br: WARN },
    ghost:   { bg: 'transparent', fg: TEXT, br: BORDER },
  }[variant];
  return (
    <button data-testid={testId} {...rest} style={{
      background: palette.bg, color: palette.fg, border: `1px solid ${palette.br}`,
      borderRadius: 6, padding: '7px 12px', fontSize: 12, fontWeight: 600,
      cursor: rest.disabled ? 'not-allowed' : 'pointer',
      opacity: rest.disabled ? 0.5 : 1, transition: 'opacity 120ms ease', ...(rest.style || {}),
    }}>{children}</button>
  );
}

function Input({ testId, ...rest }) {
  return <input data-testid={testId} {...rest} style={{
    background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 6,
    padding: '7px 10px', color: TEXT, fontSize: 12,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', ...(rest.style || {}),
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
      background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8,
      padding: 12, minWidth: 0,
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

// ── Token Gate ────────────────────────────────────────────────────────────
function TokenGate({ token, setToken, whoami, setWhoami }) {
  const [input, setInput] = useState(token || '');
  const [validating, setValidating] = useState(false);
  const [err, setErr] = useState(null);

  const validate = useCallback(async (candidate) => {
    const t = (candidate ?? input).trim();
    if (!t) { setErr('Token required'); return; }
    setValidating(true); setErr(null);
    try {
      const me = await apiFetch(t, '/auth/whoami');
      setWhoami(me); setToken(t);
      localStorage.setItem(TOKEN_KEY, t);
      toast.success(`Authed as ${me.agent_id || 'agent'}`);
    } catch (e) { setErr(e.message); toast.error(`Auth failed: ${e.message}`); }
    finally { setValidating(false); }
  }, [input, setToken, setWhoami]);

  useEffect(() => { if (token && !whoami) validate(token); }, []);

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    setToken(''); setWhoami(null); setInput('');
    toast.info('Token cleared');
  };

  return (
    <Section testId="admin-testing-token-section" title="Admin Token"
      subtitle="X-Admin-Token — validated against /auth/whoami. Stored only in localStorage."
      right={whoami && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span data-testid="admin-testing-whoami" style={{
            fontSize: 11, color: ACCENT_2, background: `${ACCENT_2}22`,
            padding: '4px 10px', borderRadius: 999,
          }}>● {whoami.agent_id || 'agent'} · {whoami.token_hash}</span>
          <Btn variant="ghost" testId="admin-testing-logout-btn" onClick={logout}>Clear</Btn>
        </div>
      )}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <Input testId="admin-testing-token-input" type="password"
          placeholder="EMERGENT_ADMIN_TOKEN" value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && validate()}
          style={{ flex: 1 }} autoComplete="off" />
        <Btn variant="primary" testId="admin-testing-validate-btn"
          onClick={() => validate()} disabled={validating || !input}>
          {validating ? 'Validating…' : whoami ? 'Re-validate' : 'Authenticate'}
        </Btn>
      </div>
      {err && <div data-testid="admin-testing-token-error" style={{ marginTop: 8, fontSize: 12, color: BAD }}>{err}</div>}
    </Section>
  );
}

// ── Pipeline definition ───────────────────────────────────────────────────
const PIPELINE_STEPS = [
  {
    key: 'ingest_stats',
    label: '1. Backfill Stats',
    purpose: 'Pulls historical player stats from SGO API for the window.',
    module: 'scripts.sgo.ingest_historical_player_stats',
    skippable: true,
    buildArgs: (cfg) => ['--league', cfg.league, '--start', cfg.start, '--end', cfg.end],
  },
  {
    key: 'build_features',
    label: '2. Build Features',
    purpose: 'Builds pre-game model features for every prop in the window.',
    module: 'scripts.sgo.build_historical_model_features',
    skippable: true,
    buildArgs: (cfg) => ['--league', cfg.league, '--start', cfg.start, '--end', cfg.end],
  },
  {
    key: 'score_model',
    label: '3. Score Through Model',
    purpose: 'Runs the live MLB-HF model over historical features.',
    module: 'scripts.sgo.score_historical_with_live_mlb_hf',
    skippable: true,
    buildArgs: (cfg) => ['--league', cfg.league, '--start', cfg.start, '--end', cfg.end],
  },
  {
    key: 'full_replay',
    label: '4. Full Pipeline Replay',
    purpose: 'Drives every prop through live PropVision scoring + gates.',
    module: 'scripts.sgo.historical_full_pipeline_replay',
    skippable: false,
    buildArgs: (cfg) => {
      const args = ['--league', cfg.league, '--start', cfg.start, '--end', cfg.end];
      if (cfg.excludeFamilies) args.push('--exclude-stat-family', cfg.excludeFamilies);
      return args;
    },
  },
  {
    key: 'grid_sweep',
    label: '5. Grid Sweep',
    purpose: 'Per-tier × per-stat_family threshold sweep.',
    module: 'scripts.sgo.historical_gate_replay_grid',
    skippable: false,
    buildArgs: (cfg) => ['--league', cfg.league, '--start', cfg.start, '--end', cfg.end, '--min-bets', String(cfg.minBets || 20)],
  },
  {
    key: 'view_results',
    label: '6. View Results',
    purpose: 'Loaded automatically from research_grid_results + replay collection.',
    module: null, // virtual step
    skippable: false,
  },
  {
    key: 'save_candidate',
    label: '7. Save Candidate',
    purpose: 'Persist a config you want to act on.',
    module: null, // virtual step
    skippable: false,
  },
];

const STAT_FAMILY_PRESETS = [
  'hits', 'total_bases', 'hits_runs_rbis', 'rbis', 'runs', 'home_runs',
  'singles', 'doubles', 'batter_strikeouts', 'pitcher_strikeouts',
  'pitcher_outs', 'earned_runs', 'hits_allowed', 'walks_allowed',
  'stolen_bases', 'points',
];

// ── Pipeline runner (state machine in localStorage) ───────────────────────
function loadPipeline() {
  try { const raw = localStorage.getItem(PIPELINE_KEY); return raw ? JSON.parse(raw) : null; }
  catch { return null; }
}
function savePipeline(p) {
  if (p === null) localStorage.removeItem(PIPELINE_KEY);
  else localStorage.setItem(PIPELINE_KEY, JSON.stringify(p));
}

function PipelineRunner({ token, onPipelineFinished }) {
  const [config, setConfig] = useState({
    league: 'MLB', start: '', end: '', minBets: 20,
    excludeFamilies: 'fantasy_score',
    skip: { ingest_stats: true, build_features: true, score_model: true }, // default skip prep
  });
  const [pipeline, setPipeline] = useState(loadPipeline());
  const [tailLines, setTailLines] = useState([]);
  const pollRef = useRef(null);

  // Persist pipeline state
  useEffect(() => { savePipeline(pipeline); }, [pipeline]);

  // Active step driver
  useEffect(() => {
    if (!pipeline || !token) { if (pollRef.current) clearInterval(pollRef.current); return; }
    const driveStep = async () => {
      const currentIdx = pipeline.steps.findIndex(s => s.status === 'running' || s.status === 'queued');
      if (currentIdx < 0) {
        // No active step — find next non-skipped, non-done step
        const nextIdx = pipeline.steps.findIndex(s => s.status === 'pending');
        if (nextIdx < 0) {
          // Done
          setPipeline(p => p && p.status !== 'completed' ? { ...p, status: 'completed', finished_at: new Date().toISOString() } : p);
          if (pollRef.current) clearInterval(pollRef.current);
          if (onPipelineFinished) onPipelineFinished(pipeline);
          return;
        }
        const next = pipeline.steps[nextIdx];
        const stepDef = PIPELINE_STEPS.find(s => s.key === next.key);
        if (!stepDef.module) {
          // virtual step
          setPipeline(p => {
            const steps = [...p.steps];
            steps[nextIdx] = { ...steps[nextIdx], status: 'succeeded', finished_at: new Date().toISOString() };
            return { ...p, steps };
          });
          return;
        }
        try {
          const args = stepDef.buildArgs(pipeline.config);
          const res = await apiFetch(token, '/jobs/run', {
            method: 'POST', body: JSON.stringify({ module: stepDef.module, args }),
          });
          setPipeline(p => {
            const steps = [...p.steps];
            steps[nextIdx] = {
              ...steps[nextIdx], status: 'queued', job_id: res.job_id,
              args, started_at: new Date().toISOString(),
            };
            return { ...p, steps };
          });
          toast.info(`Started ${stepDef.label}`);
        } catch (e) {
          setPipeline(p => {
            const steps = [...p.steps];
            steps[nextIdx] = { ...steps[nextIdx], status: 'failed', error: e.message };
            return { ...p, status: 'halted', steps };
          });
          toast.error(`Step ${stepDef.label} failed to start: ${e.message}`);
        }
        return;
      }
      // poll the active job
      const active = pipeline.steps[currentIdx];
      if (!active.job_id) return;
      try {
        const j = await apiFetch(token, `/jobs/${active.job_id}`);
        const job = j.job;
        // also tail log
        try {
          const lg = await apiFetch(token, `/jobs/${active.job_id}/log?tail=200`);
          setTailLines(lg.lines || []);
        } catch { /* ignore log errors */ }
        if (job.status === 'succeeded') {
          setPipeline(p => {
            const steps = [...p.steps];
            steps[currentIdx] = { ...steps[currentIdx], status: 'succeeded', finished_at: new Date().toISOString(), exit_code: job.exit_code };
            return { ...p, steps };
          });
          toast.success(`${PIPELINE_STEPS.find(s => s.key === active.key).label} done`);
        } else if (['failed', 'errored', 'cancelled'].includes(job.status)) {
          setPipeline(p => {
            const steps = [...p.steps];
            steps[currentIdx] = { ...steps[currentIdx], status: job.status, finished_at: new Date().toISOString(), exit_code: job.exit_code, error: job.error };
            return { ...p, status: 'halted', steps };
          });
          toast.error(`${active.key} ${job.status}`);
        } else {
          // still running — update status
          setPipeline(p => {
            const steps = [...p.steps];
            if (steps[currentIdx].status !== job.status) steps[currentIdx] = { ...steps[currentIdx], status: job.status };
            return { ...p, steps };
          });
        }
      } catch (e) {
        console.error('[pipeline] poll error', e);
      }
    };
    driveStep();
    pollRef.current = setInterval(driveStep, 2500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [pipeline?.id, pipeline?.status, token]);

  const startPipeline = (chained) => {
    if (!config.start || !config.end) { toast.error('Start and end dates required'); return; }
    const steps = PIPELINE_STEPS.map(s => ({
      key: s.key,
      status: (chained === 'full_only' && s.skippable) ? 'skipped'
            : (config.skip[s.key]) ? 'skipped'
            : 'pending',
    }));
    const p = {
      id: `pipe_${Date.now()}`, config: { ...config },
      status: 'running', steps,
      started_at: new Date().toISOString(), finished_at: null,
    };
    setPipeline(p);
    setTailLines([]);
    toast.success('Pipeline started — auto-chaining steps');
  };

  const resetPipeline = () => {
    if (!window.confirm('Discard the current pipeline run?')) return;
    setPipeline(null); setTailLines([]);
    savePipeline(null);
  };

  const cancelActive = async () => {
    if (!pipeline) return;
    const active = pipeline.steps.find(s => s.status === 'running' || s.status === 'queued');
    if (!active || !active.job_id) return;
    try {
      await apiFetch(token, `/jobs/${active.job_id}/cancel`, {
        method: 'POST', body: JSON.stringify({ confirm: true }),
      });
      toast.success('Cancel signal sent');
    } catch (e) { toast.error(`Cancel failed: ${e.message}`); }
  };

  const activeStep = pipeline?.steps.find(s => s.status === 'running' || s.status === 'queued');

  return (
    <Section testId="pipeline-section" title="Guided Workflow"
      subtitle="Single-click chains the entire replay → grid sweep → results pipeline."
      accent={ACCENT}
      right={pipeline && (
        <div style={{ display: 'flex', gap: 8 }}>
          {activeStep && <Btn variant="danger" onClick={cancelActive} testId="pipeline-cancel">Cancel</Btn>}
          <Btn variant="ghost" onClick={resetPipeline} testId="pipeline-reset">Reset</Btn>
        </div>
      )}>
      {/* Config row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 10, marginBottom: 14 }}>
        <Field label="League"><Select testId="pipe-league" value={config.league}
          onChange={(e) => setConfig({ ...config, league: e.target.value })} options={['MLB']} /></Field>
        <Field label="Start (YYYY-MM-DD)"><Input testId="pipe-start" value={config.start}
          onChange={(e) => setConfig({ ...config, start: e.target.value })} placeholder="2026-04-01" /></Field>
        <Field label="End (YYYY-MM-DD)"><Input testId="pipe-end" value={config.end}
          onChange={(e) => setConfig({ ...config, end: e.target.value })} placeholder="2026-04-30" /></Field>
        <Field label="Min bets / cell"><Input testId="pipe-minbets" type="number" value={config.minBets}
          onChange={(e) => setConfig({ ...config, minBets: parseInt(e.target.value || '0', 10) })} /></Field>
        <Field label="Exclude families"><Input testId="pipe-excl" value={config.excludeFamilies}
          onChange={(e) => setConfig({ ...config, excludeFamilies: e.target.value })} placeholder="fantasy_score,points" /></Field>
      </div>

      {/* Skip toggles */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', marginBottom: 6 }}>
          Skip prep steps (use if already done):
        </div>
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

      {/* Start buttons */}
      {!pipeline && (
        <div style={{ display: 'flex', gap: 10 }}>
          <Btn variant="primary" testId="pipeline-start-full" onClick={() => startPipeline()}>
            ▶ Run Full Replay Pipeline
          </Btn>
          <Btn variant="ghost" testId="pipeline-start-replayonly" onClick={() => startPipeline('full_only')}>
            ▶ Run Replay + Grid Only (skip prep)
          </Btn>
        </div>
      )}

      {/* Step visualization */}
      {pipeline && (
        <>
          <div data-testid="pipeline-steps" style={{
            display: 'grid', gridTemplateColumns: `repeat(${PIPELINE_STEPS.length}, 1fr)`,
            gap: 6, marginBottom: 14,
          }}>
            {PIPELINE_STEPS.map((s, i) => {
              const st = pipeline.steps[i];
              const color = st.status === 'succeeded' ? ACCENT_2
                          : st.status === 'running' || st.status === 'queued' ? ACCENT
                          : ['failed', 'errored', 'cancelled'].includes(st.status) ? BAD
                          : st.status === 'skipped' ? DIM : MUTED;
              return (
                <div key={s.key} data-testid={`pipe-step-${s.key}`} style={{
                  background: SURFACE_2,
                  border: `1px solid ${color === MUTED ? BORDER : color}`,
                  borderLeft: `3px solid ${color}`,
                  borderRadius: 6, padding: 8,
                }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    {st.status}
                  </div>
                  <div style={{ fontSize: 11, color: TEXT, fontWeight: 600, marginTop: 4 }}>{s.label}</div>
                  <div style={{ fontSize: 9, color: DIM, marginTop: 4, fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {st.job_id ? st.job_id.slice(0, 8) : (st.status === 'skipped' ? '(skipped)' : '')}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Active log tail */}
          {activeStep && (
            <div data-testid="pipeline-active-log" style={{
              background: '#000', border: `1px solid ${BORDER}`, borderRadius: 6,
              padding: 0, maxHeight: 220, overflow: 'hidden',
            }}>
              <div style={{ padding: '6px 10px', borderBottom: `1px solid ${BORDER}`, fontSize: 10, color: MUTED, fontFamily: 'monospace' }}>
                ● live tail · {PIPELINE_STEPS.find(s => s.key === activeStep.key).label} · {activeStep.job_id?.slice(0,8)}
              </div>
              <pre style={{
                margin: 0, padding: 10, fontSize: 10, color: '#A1A1AA',
                fontFamily: 'ui-monospace, monospace', maxHeight: 180,
                overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>{tailLines.length ? tailLines.slice(-50).join('') : '(starting…)'}</pre>
            </div>
          )}

          {pipeline.status === 'completed' && (
            <div data-testid="pipeline-done" style={{
              marginTop: 12, padding: 10, background: `${ACCENT_2}1a`,
              border: `1px solid ${ACCENT_2}`, borderRadius: 6, fontSize: 12, color: ACCENT_2,
            }}>
              ✓ Pipeline complete. Results panel below has been loaded.
            </div>
          )}
          {pipeline.status === 'halted' && (
            <div data-testid="pipeline-halted" style={{
              marginTop: 12, padding: 10, background: `${BAD}1a`,
              border: `1px solid ${BAD}`, borderRadius: 6, fontSize: 12, color: BAD,
            }}>
              ✗ Pipeline halted. Check the failed step's log via Reset → Re-run.
            </div>
          )}
        </>
      )}
    </Section>
  );
}

// ── Results Panel ─────────────────────────────────────────────────────────
function ResultsPanel({ token, refreshKey, onSaveCandidate }) {
  const [runs, setRuns] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const [cells, setCells] = useState([]);
  const [roiByTier, setRoiByTier] = useState([]);
  const [roiByFam, setRoiByFam] = useState([]);
  const [roiByOdds, setRoiByOdds] = useState([]);
  const [roiBySide, setRoiBySide] = useState([]);
  const [reasonCounts, setReasonCounts] = useState([]);
  const [dailyRoi, setDailyRoi] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState({ tier: '', stat_family: '', side: '', slice: 'TIER_FAMILY' });

  // Fetch list of recent runs
  const fetchRuns = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiFetch(token, `/collections/${GRID_RUNS_COLL}/find`, {
        method: 'POST',
        body: JSON.stringify({ filter: {}, sort: { started_at: -1 }, limit: 20 }),
      });
      setRuns(res.docs || []);
      if (!selectedRun && res.docs?.length) setSelectedRun(res.docs[0].run_id);
    } catch (e) {
      toast.error(`Load runs failed: ${e.message}`);
    }
  }, [token, selectedRun]);

  useEffect(() => { fetchRuns(); }, [fetchRuns, refreshKey]);

  // Load cells for selected run
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
        method: 'POST',
        body: JSON.stringify({
          filter: filt,
          sort: { hit_rate: -1 }, limit: 2000,
        }),
      });
      setCells(res.docs || []);
    } catch (e) {
      toast.error(`Load cells failed: ${e.message}`);
    } finally { setLoading(false); }
  }, [token, selectedRun, filter]);

  useEffect(() => { loadCells(); }, [loadCells]);

  // ROI aggregations from the replay collection — uses run window
  const runMeta = useMemo(() => runs.find(r => r.run_id === selectedRun), [runs, selectedRun]);
  const loadRoi = useCallback(async () => {
    if (!token || !runMeta) return;
    const { params } = runMeta;
    if (!params?.start || !params?.end) return;
    const baseMatch = {
      league_id: params.league || 'MLB',
      game_date: { $gte: params.start, $lte: params.end },
      outcome_resolved: true,
      selected_tier: { $ne: null },
    };
    const winCond = { $cond: [{ $eq: ['$outcome_numeric', 1] }, 1, 0] };
    // Bet payout: win → odds-derived multiplier; lose → -1
    const payoutCond = {
      $cond: [
        { $eq: ['$outcome_numeric', 1] },
        {
          $cond: [
            { $gt: ['$odds', 0] }, { $divide: ['$odds', 100] },
            { $divide: [100, { $abs: '$odds' }] },
          ],
        },
        -1,
      ],
    };
    const baseGroup = (groupKey) => ([
      { $match: baseMatch },
      { $group: {
          _id: groupKey,
          n: { $sum: 1 },
          wins: { $sum: winCond },
          roi: { $avg: payoutCond },
          avg_tp: { $avg: '$tp' },
          avg_cv: { $avg: '$cv' },
          avg_edge: { $avg: '$edge' },
          avg_hr20: { $avg: '$hit_rate_l20' },
      } },
      { $sort: { roi: -1 } },
      { $limit: 100 },
    ]);
    try {
      const [tierRes, famRes, oddsRes, sideRes, dailyRes] = await Promise.all([
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, {
          method: 'POST',
          body: JSON.stringify({ pipeline: baseGroup('$selected_tier'), limit: 100 }),
        }),
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, {
          method: 'POST',
          body: JSON.stringify({ pipeline: baseGroup({ tier: '$selected_tier', stat_family: '$stat_family' }), limit: 200 }),
        }),
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, {
          method: 'POST',
          body: JSON.stringify({ pipeline: baseGroup({ tier: '$selected_tier', odds_bucket: '$odds_bucket' }), limit: 200 }),
        }),
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, {
          method: 'POST',
          body: JSON.stringify({ pipeline: baseGroup({ tier: '$selected_tier', side: '$side' }), limit: 100 }),
        }),
        apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, {
          method: 'POST',
          body: JSON.stringify({
            pipeline: [
              { $match: baseMatch },
              { $group: {
                  _id: '$game_date',
                  n: { $sum: 1 }, wins: { $sum: winCond },
                  roi: { $avg: payoutCond },
              } },
              { $sort: { _id: 1 } },
              { $limit: 200 },
            ],
            limit: 200,
          }),
        }),
      ]);
      setRoiByTier(tierRes.docs || []);
      setRoiByFam(famRes.docs || []);
      setRoiByOdds(oddsRes.docs || []);
      setRoiBySide(sideRes.docs || []);
      setDailyRoi(dailyRes.docs || []);
    } catch (e) {
      toast.error(`ROI aggregate failed: ${e.message}`);
    }
  }, [token, runMeta]);

  useEffect(() => { loadRoi(); }, [loadRoi]);

  // Reason code histogram from replay collection
  const loadReasons = useCallback(async () => {
    if (!token || !runMeta?.params) return;
    const { params } = runMeta;
    try {
      const res = await apiFetch(token, `/collections/${REPLAY_COLL}/aggregate`, {
        method: 'POST',
        body: JSON.stringify({
          pipeline: [
            { $match: { league_id: params.league || 'MLB',
                         game_date: { $gte: params.start, $lte: params.end } } },
            { $project: {
                reasons: {
                  $concatArrays: [
                    { $ifNull: ['$safe_haven_failed_reasons', []] },
                    { $ifNull: ['$front_lines_failed_reasons', []] },
                    { $ifNull: ['$war_zone_failed_reasons', []] },
                  ],
                },
            } },
            { $unwind: '$reasons' },
            { $group: { _id: '$reasons', n: { $sum: 1 } } },
            { $sort: { n: -1 } },
            { $limit: 30 },
          ],
          limit: 30,
        }),
      });
      setReasonCounts(res.docs || []);
    } catch (e) {
      console.error('[reasons]', e.message);
    }
  }, [token, runMeta]);

  useEffect(() => { loadReasons(); }, [loadReasons]);

  // Derived: top / worst / best-by-tier / best-by-stat_family / DO-NOT-USE
  const sortedByHr = useMemo(
    () => [...cells].filter(c => (c.n_bets || 0) >= (runMeta?.params?.min_bets || 20))
                     .sort((a, b) => (b.hit_rate || 0) - (a.hit_rate || 0)),
    [cells, runMeta]
  );
  const sortedByCalibration = useMemo(
    () => [...cells].filter(c => (c.n_bets || 0) >= (runMeta?.params?.min_bets || 20) && c.calibration_delta != null)
                     .sort((a, b) => (b.calibration_delta || 0) - (a.calibration_delta || 0)),
    [cells, runMeta]
  );
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

  // Plain-English summary
  const summary = useMemo(() => {
    if (!cells.length) return null;
    const sh = bestByTier['safe_haven'];
    const fl = bestByTier['front_lines'];
    const wz = bestByTier['war_zone'];
    const bestFam = bestByFam[0];
    const worstFam = [...bestByFam].sort((a, b) => (a.hit_rate || 0) - (b.hit_rate || 0))[0];
    const bestOdds = [...roiByOdds].sort((a, b) => (b.roi || 0) - (a.roi || 0))[0];
    return { sh, fl, wz, bestFam, worstFam, bestOdds };
  }, [bestByTier, bestByFam, roiByOdds, cells]);

  // Max drawdown from daily ROI sequence
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
    <Section testId="results-section" title="Results"
      subtitle="Auto-loaded from research_grid_results + sgo_propvision_full_pipeline_replay."
      accent={ACCENT_3}
      right={
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Select testId="results-run-select" value={selectedRun || ''}
            onChange={(e) => setSelectedRun(e.target.value)}
            options={[
              { value: '', label: '— pick run —' },
              ...runs.map(r => ({
                value: r.run_id,
                label: `${r.params?.league || '?'} ${r.params?.start}..${r.params?.end} · ${r.run_id.slice(0,8)} · ${r.status}`,
              })),
            ]} />
          <Btn variant="ghost" onClick={() => { fetchRuns(); loadCells(); loadRoi(); loadReasons(); }} testId="results-refresh">
            Refresh
          </Btn>
        </div>
      }>

      {!runMeta ? (
        <div data-testid="results-empty" style={{ padding: 30, textAlign: 'center', color: DIM, fontSize: 13 }}>
          No grid runs yet — run the pipeline above (step 5 writes here).
        </div>
      ) : (
        <>
          {/* Run header + headline metrics */}
          <div style={{ marginBottom: 14, fontSize: 12, color: MUTED }}>
            <span style={{ color: TEXT, fontFamily: 'monospace' }}>{runMeta.run_id}</span>
            {' · '} <span style={{ color: ACCENT_2 }}>{runMeta.params?.league}</span>
            {' · '} {runMeta.params?.start} → {runMeta.params?.end}
            {' · '} cells={fmtInt(runMeta.n_cells_total)}
            {' · '} qualified={fmtInt(runMeta.n_cells_qualified)}
            {' · '} candidates_saved={fmtInt(runMeta.n_candidates_saved)}
          </div>

          {/* Headline stats from ROI aggregates */}
          <div data-testid="results-headline" style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
            gap: 10, marginBottom: 14,
          }}>
            {roiByTier.map(r => (
              <StatCard key={r._id || 'none'}
                testId={`headline-tier-${r._id}`}
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

          {/* Plain-English summary */}
          {summary && (
            <div data-testid="results-plain-english" style={{
              background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8,
              padding: 14, marginBottom: 14, fontSize: 13, lineHeight: 1.7, color: TEXT,
            }}>
              <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 }}>
                Plain-English Summary
              </div>
              {summary.sh && <div>
                <strong style={{ color: ACCENT_2 }}>Best Safe Haven:</strong> {summary.sh.stat_family} @ HR L20≥{fmtPct(summary.sh.hr_l20_min)}, CV≤{fmtNum(summary.sh.cv_max)}, edge≥{fmtPct(summary.sh.edge_min)} → <strong>{fmtPct(summary.sh.hit_rate)}</strong> on {fmtInt(summary.sh.n_bets)} bets.
              </div>}
              {summary.fl && <div>
                <strong style={{ color: ACCENT_3 }}>Best Front Lines:</strong> {summary.fl.stat_family} @ HR L20≥{fmtPct(summary.fl.hr_l20_min)}, HR L5≥{fmtPct(summary.fl.hr_l5_min)} → <strong>{fmtPct(summary.fl.hit_rate)}</strong> on {fmtInt(summary.fl.n_bets)} bets.
              </div>}
              {summary.wz && <div>
                <strong style={{ color: WARN }}>Best War Zone:</strong> {summary.wz.stat_family} @ HR L20≥{fmtPct(summary.wz.hr_l20_min)}, CV≤{fmtNum(summary.wz.cv_max)} → <strong>{fmtPct(summary.wz.hit_rate)}</strong> on {fmtInt(summary.wz.n_bets)} bets.
              </div>}
              {summary.bestFam && <div>
                <strong>Best stat family overall:</strong> <code style={{ color: ACCENT_2 }}>{summary.bestFam.stat_family}</code> ({fmtPct(summary.bestFam.hit_rate)} @ {summary.bestFam.tier}).
              </div>}
              {summary.worstFam && summary.worstFam.stat_family !== summary.bestFam?.stat_family && <div>
                <strong>Worst stat family:</strong> <code style={{ color: BAD }}>{summary.worstFam.stat_family}</code> ({fmtPct(summary.worstFam.hit_rate)}) — <em>do not use</em>.
              </div>}
              {summary.bestOdds && <div>
                <strong>Best odds bucket:</strong> <code style={{ color: ACCENT_2 }}>{summary.bestOdds._id?.odds_bucket}</code> (tier {summary.bestOdds._id?.tier}) → ROI {fmtPct(summary.bestOdds.roi)} on {fmtInt(summary.bestOdds.n)} bets.
              </div>}
            </div>
          )}

          {/* Filters */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase' }}>Filter</span>
            <Select testId="results-filter-tier" value={filter.tier}
              onChange={(e) => setFilter({ ...filter, tier: e.target.value })}
              options={[{ value: '', label: 'all tiers' }, 'safe_haven', 'front_lines', 'war_zone'].map(x => typeof x === 'string' ? { value: x, label: x } : x)} />
            <Select testId="results-filter-family" value={filter.stat_family}
              onChange={(e) => setFilter({ ...filter, stat_family: e.target.value })}
              options={[{ value: '', label: 'all families' }, ...STAT_FAMILY_PRESETS.map(f => ({ value: f, label: f }))]} />
            <Select testId="results-filter-slice" value={filter.slice}
              onChange={(e) => setFilter({ ...filter, slice: e.target.value })}
              options={[{ value: 'TIER_FAMILY', label: 'tier × family' }, { value: 'TIER_FAMILY_SIDE', label: 'tier × family × side' }, { value: '', label: 'all slices' }]} />
            {loading && <span style={{ color: ACCENT, fontSize: 11 }}>loading…</span>}
          </div>

          {/* Top / Worst tables */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <CellTable title="Top 15 by Hit Rate" cells={top} testId="results-top-table"
              accent={ACCENT_2} onSave={onSaveCandidate} />
            <CellTable title="Bottom 15 (DO NOT USE)" cells={worst} testId="results-worst-table"
              accent={BAD} onSave={onSaveCandidate} />
          </div>

          {/* Best by tier + best by family */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <div data-testid="results-best-by-tier" style={{
              background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12,
            }}>
              <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', marginBottom: 8 }}>Best Config by Tier</div>
              {Object.entries(bestByTier).map(([tier, c]) => (
                <div key={tier} style={{
                  display: 'grid', gridTemplateColumns: '110px 1fr auto',
                  gap: 8, alignItems: 'center', padding: '8px 0',
                  borderTop: `1px solid ${BORDER}`,
                }}>
                  <Badge color={tier === 'safe_haven' ? ACCENT_2 : tier === 'front_lines' ? ACCENT_3 : WARN}>{tier}</Badge>
                  <div style={{ fontSize: 12 }}>
                    <code style={{ color: ACCENT, fontFamily: 'monospace' }}>{c.stat_family}</code> · hr_l20≥{fmtPct(c.hr_l20_min)} · cv≤{fmtNum(c.cv_max)} · edge≥{fmtPct(c.edge_min)} · tp≥{fmtPct(c.tp_min)}
                    <div style={{ fontSize: 10, color: DIM }}>n={fmtInt(c.n_bets)} · HR={fmtPct(c.hit_rate)} · Δcal={fmtPct(c.calibration_delta, 2)}</div>
                  </div>
                  <Btn variant="ghost" onClick={() => onSaveCandidate(c)} testId={`save-bestby-${tier}`}>Save</Btn>
                </div>
              ))}
            </div>
            <div data-testid="results-best-by-family" style={{
              background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12,
              maxHeight: 320, overflowY: 'auto',
            }}>
              <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', marginBottom: 8 }}>Best Config by Stat Family</div>
              {bestByFam.map((c, i) => (
                <div key={i} style={{
                  display: 'grid', gridTemplateColumns: '1fr auto auto',
                  gap: 8, alignItems: 'center', padding: '6px 0',
                  borderTop: `1px solid ${BORDER}`,
                }}>
                  <div style={{ fontSize: 12 }}>
                    <code style={{ color: TEXT, fontFamily: 'monospace' }}>{c.stat_family}</code>
                    <span style={{ color: DIM, marginLeft: 8 }}>({c.tier})</span>
                  </div>
                  <span style={{ fontSize: 12, color: (c.hit_rate || 0) > 0.6 ? ACCENT_2 : MUTED, fontWeight: 600 }}>
                    {fmtPct(c.hit_rate)}
                  </span>
                  <Btn variant="ghost" onClick={() => onSaveCandidate(c)} testId={`save-fam-${c.stat_family}`}>Save</Btn>
                </div>
              ))}
            </div>
          </div>

          {/* Breakdowns: side, odds, family ROI */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginBottom: 14 }}>
            <BreakdownTable title="ROI by Tier · Side" data={roiBySide} testId="results-side-table"
              cols={[
                { key: 'tier', label: 'tier', acc: (r) => r._id?.tier },
                { key: 'side', label: 'side', acc: (r) => r._id?.side },
                { key: 'n', label: 'n', acc: (r) => fmtInt(r.n) },
                { key: 'hr', label: 'HR', acc: (r) => fmtPct((r.wins || 0) / (r.n || 1)) },
                { key: 'roi', label: 'ROI', acc: (r) => fmtPct(r.roi), color: (r) => (r.roi || 0) > 0 ? ACCENT_2 : BAD },
              ]} />
            <BreakdownTable title="ROI by Tier · Odds Bucket" data={roiByOdds} testId="results-odds-table"
              cols={[
                { key: 'tier', label: 'tier', acc: (r) => r._id?.tier },
                { key: 'bucket', label: 'bucket', acc: (r) => r._id?.odds_bucket },
                { key: 'n', label: 'n', acc: (r) => fmtInt(r.n) },
                { key: 'roi', label: 'ROI', acc: (r) => fmtPct(r.roi), color: (r) => (r.roi || 0) > 0 ? ACCENT_2 : BAD },
              ]} />
            <BreakdownTable title="ROI by Tier · Family" data={roiByFam} testId="results-fam-roi-table"
              cols={[
                { key: 'tier', label: 'tier', acc: (r) => r._id?.tier },
                { key: 'fam', label: 'family', acc: (r) => r._id?.stat_family },
                { key: 'n', label: 'n', acc: (r) => fmtInt(r.n) },
                { key: 'roi', label: 'ROI', acc: (r) => fmtPct(r.roi), color: (r) => (r.roi || 0) > 0 ? ACCENT_2 : BAD },
              ]} />
          </div>

          {/* Reason codes */}
          <div data-testid="results-reasons" style={{
            background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8,
            padding: 12, marginBottom: 14,
          }}>
            <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', marginBottom: 8 }}>
              Gate-Fail Reason Codes (all tiers combined)
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 6 }}>
              {reasonCounts.map(r => (
                <div key={r._id} style={{
                  display: 'flex', justifyContent: 'space-between', padding: '5px 8px',
                  background: SURFACE_3, borderRadius: 4, fontSize: 11,
                }}>
                  <code style={{ color: TEXT, fontFamily: 'monospace' }}>{r._id}</code>
                  <span style={{ color: MUTED, fontVariantNumeric: 'tabular-nums' }}>{fmtInt(r.n)}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </Section>
  );
}

function CellTable({ title, cells, testId, accent, onSave }) {
  return (
    <div data-testid={testId} style={{
      background: SURFACE_2, border: `1px solid ${BORDER}`,
      borderLeft: `3px solid ${accent}`, borderRadius: 8, overflow: 'hidden',
    }}>
      <div style={{ fontSize: 11, color: accent, textTransform: 'uppercase', letterSpacing: 0.5, padding: '8px 12px', borderBottom: `1px solid ${BORDER}` }}>
        {title}
      </div>
      <div style={{ maxHeight: 360, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr style={{ background: SURFACE, color: DIM, textTransform: 'uppercase', fontSize: 10 }}>
              <th style={th}>tier</th><th style={th}>family</th><th style={th}>n</th>
              <th style={th}>HR</th><th style={th}>Δcal</th><th style={th}>edge</th>
              <th style={th}>cv</th><th style={th}>tp</th><th style={th}>daily σ</th>
              <th style={th}></th>
            </tr>
          </thead>
          <tbody>
            {cells.length === 0 ? (
              <tr><td colSpan={10} style={{ padding: 18, textAlign: 'center', color: DIM }}>(no cells match)</td></tr>
            ) : cells.map((c, i) => (
              <tr key={i} style={{ borderTop: `1px solid ${BORDER}` }}>
                <td style={td}>{c.tier}</td>
                <td style={{ ...td, fontFamily: 'monospace', color: ACCENT }}>{c.stat_family}</td>
                <td style={td}>{fmtInt(c.n_bets)}</td>
                <td style={{ ...td, color: (c.hit_rate || 0) > 0.6 ? ACCENT_2 : (c.hit_rate || 0) < 0.5 ? BAD : TEXT, fontWeight: 600 }}>{fmtPct(c.hit_rate)}</td>
                <td style={{ ...td, color: (c.calibration_delta || 0) > 0 ? ACCENT_2 : BAD }}>{fmtPct(c.calibration_delta, 2)}</td>
                <td style={td}>{fmtPct(c.avg_edge, 2)}</td>
                <td style={td}>{fmtNum(c.avg_cv)}</td>
                <td style={td}>{fmtPct(c.avg_tp, 1)}</td>
                <td style={td}>{fmtNum(c.daily_consistency)}</td>
                <td style={td}><button onClick={() => onSave(c)} data-testid={`celltable-save-${i}`} style={{ background: 'transparent', border: `1px solid ${BORDER_STRONG}`, color: TEXT, borderRadius: 4, padding: '2px 6px', fontSize: 10, cursor: 'pointer' }}>save</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const th = { padding: '6px 8px', textAlign: 'left', borderBottom: `1px solid ${BORDER}`, fontWeight: 600 };
const td = { padding: '6px 8px', color: TEXT, fontFamily: 'ui-monospace, monospace', fontSize: 11 };

function BreakdownTable({ title, data, cols, testId }) {
  return (
    <div data-testid={testId} style={{
      background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, overflow: 'hidden',
    }}>
      <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', letterSpacing: 0.5, padding: '8px 12px', borderBottom: `1px solid ${BORDER}` }}>
        {title}
      </div>
      <div style={{ maxHeight: 280, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead><tr style={{ background: SURFACE, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
            {cols.map(c => <th key={c.key} style={th}>{c.label}</th>)}
          </tr></thead>
          <tbody>
            {data.length === 0 ? (
              <tr><td colSpan={cols.length} style={{ padding: 12, textAlign: 'center', color: DIM }}>—</td></tr>
            ) : data.map((r, i) => (
              <tr key={i} style={{ borderTop: `1px solid ${BORDER}` }}>
                {cols.map(c => (
                  <td key={c.key} style={{ ...td, color: c.color ? c.color(r) : TEXT, fontWeight: c.color ? 600 : 400 }}>
                    {c.acc(r)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Candidate Manager ─────────────────────────────────────────────────────
function CandidateManager({ token, pendingCandidate, setPendingCandidate }) {
  const [candidates, setCandidates] = useState(() => {
    try { const raw = localStorage.getItem(CANDIDATES_KEY); return raw ? JSON.parse(raw) : []; }
    catch { return []; }
  });
  const [name, setName] = useState('');
  const [tag, setTag] = useState('review');
  const [compare, setCompare] = useState([]);

  useEffect(() => { localStorage.setItem(CANDIDATES_KEY, JSON.stringify(candidates)); }, [candidates]);

  const save = () => {
    if (!pendingCandidate) { toast.error('No pending candidate selected'); return; }
    if (!name.trim()) { toast.error('Name required'); return; }
    const entry = {
      id: `cand_${Date.now()}`, name: name.trim(), tag,
      saved_at: new Date().toISOString(),
      tier: pendingCandidate.tier, stat_family: pendingCandidate.stat_family,
      side: pendingCandidate.side, slice: pendingCandidate.slice,
      thresholds: {
        hr_l20_min: pendingCandidate.hr_l20_min, hr_l5_min: pendingCandidate.hr_l5_min,
        cv_max: pendingCandidate.cv_max, edge_min: pendingCandidate.edge_min,
        tp_min: pendingCandidate.tp_min,
      },
      metrics: {
        n_bets: pendingCandidate.n_bets, hit_rate: pendingCandidate.hit_rate,
        calibration_delta: pendingCandidate.calibration_delta,
        avg_edge: pendingCandidate.avg_edge, avg_cv: pendingCandidate.avg_cv,
        avg_tp: pendingCandidate.avg_tp, daily_consistency: pendingCandidate.daily_consistency,
      },
      run_id: pendingCandidate.run_id,
    };
    setCandidates([entry, ...candidates]);
    setName(''); setPendingCandidate(null);
    toast.success(`Saved "${entry.name}"`);
  };

  const markReady = async (cand) => {
    try {
      await apiFetch(token, '/configs/draft', {
        method: 'POST',
        body: JSON.stringify({
          kind: 'admin_testing_candidate_config',
          scope: `${cand.tier}:${cand.stat_family}`,
          config: {
            name: cand.name, tier: cand.tier, stat_family: cand.stat_family,
            thresholds: cand.thresholds, metrics: cand.metrics,
            source_run_id: cand.run_id,
          },
          note: `Marked ready from /admin/testing UI · tag=${cand.tag}`,
        }),
      });
      setCandidates(candidates.map(c => c.id === cand.id ? { ...c, tag: 'ready', backend_saved_at: new Date().toISOString() } : c));
      toast.success(`Persisted "${cand.name}" → emergent_candidate_configs`);
    } catch (e) { toast.error(`Mark-ready failed: ${e.message}`); }
  };

  const del = (id) => {
    if (!window.confirm('Delete this candidate?')) return;
    setCandidates(candidates.filter(c => c.id !== id));
  };

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(candidates, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `propvision-candidates-${new Date().toISOString().slice(0,10)}.json`;
    a.click(); URL.revokeObjectURL(url);
  };

  const toggleCompare = (id) => {
    setCompare(c => c.includes(id) ? c.filter(x => x !== id) : c.length >= 4 ? c : [...c, id]);
  };

  const compareItems = useMemo(() => candidates.filter(c => compare.includes(c.id)), [candidates, compare]);

  return (
    <Section testId="candidate-section" title="Candidate Manager"
      subtitle="Save winning configs from the Results panel. Mark ready → persisted to emergent_candidate_configs."
      accent={ACCENT_2}
      right={
        <div style={{ display: 'flex', gap: 8 }}>
          <Btn variant="ghost" onClick={exportJson} testId="cand-export" disabled={!candidates.length}>Export JSON</Btn>
        </div>
      }>
      {/* Pending candidate save row */}
      <div style={{
        background: pendingCandidate ? `${ACCENT}10` : SURFACE_2,
        border: `1px solid ${pendingCandidate ? ACCENT : BORDER}`,
        borderRadius: 8, padding: 12, marginBottom: 12,
      }}>
        {pendingCandidate ? (
          <>
            <div style={{ fontSize: 11, color: ACCENT, textTransform: 'uppercase', marginBottom: 8 }}>
              Pending Candidate
            </div>
            <div style={{ fontSize: 12, fontFamily: 'monospace', color: TEXT, marginBottom: 10 }}>
              {pendingCandidate.tier} · {pendingCandidate.stat_family} · hr_l20≥{fmtPct(pendingCandidate.hr_l20_min)} · cv≤{fmtNum(pendingCandidate.cv_max)} · edge≥{fmtPct(pendingCandidate.edge_min)} · n={fmtInt(pendingCandidate.n_bets)} · HR={fmtPct(pendingCandidate.hit_rate)}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Input testId="cand-name" placeholder="Candidate name (e.g. SH-hits-april)"
                value={name} onChange={(e) => setName(e.target.value)} style={{ flex: 1 }} />
              <Select testId="cand-tag" value={tag} onChange={(e) => setTag(e.target.value)}
                options={[{ value: 'review', label: 'tag: review' }, { value: 'ready', label: 'tag: ready' }, { value: 'reject', label: 'tag: do-not-use' }]} />
              <Btn variant="success" onClick={save} testId="cand-save-btn">Save</Btn>
              <Btn variant="ghost" onClick={() => setPendingCandidate(null)} testId="cand-cancel-btn">Cancel</Btn>
            </div>
          </>
        ) : (
          <div style={{ fontSize: 12, color: DIM, textAlign: 'center' }}>
            Click "Save" on any row in the Results panel above to stage a candidate.
          </div>
        )}
      </div>

      {/* Saved candidates list */}
      {candidates.length === 0 ? (
        <div style={{ color: DIM, fontSize: 12, padding: 18, textAlign: 'center' }}>
          No candidates saved yet.
        </div>
      ) : (
        <div data-testid="cand-list" style={{
          background: SURFACE_2, border: `1px solid ${BORDER}`, borderRadius: 8, overflow: 'hidden',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead><tr style={{ background: SURFACE, color: DIM, fontSize: 10, textTransform: 'uppercase' }}>
              <th style={th}>cmp</th><th style={th}>name</th><th style={th}>tier</th>
              <th style={th}>family</th><th style={th}>n</th><th style={th}>HR</th>
              <th style={th}>Δcal</th><th style={th}>thresholds</th>
              <th style={th}>tag</th><th style={th}></th>
            </tr></thead>
            <tbody>
              {candidates.map(c => (
                <tr key={c.id} style={{ borderTop: `1px solid ${BORDER}` }}>
                  <td style={td}>
                    <input type="checkbox" checked={compare.includes(c.id)}
                      onChange={() => toggleCompare(c.id)}
                      data-testid={`cand-cmp-${c.id}`} />
                  </td>
                  <td style={{ ...td, color: TEXT, fontWeight: 600 }}>{c.name}</td>
                  <td style={td}>{c.tier}</td>
                  <td style={{ ...td, color: ACCENT }}>{c.stat_family}</td>
                  <td style={td}>{fmtInt(c.metrics?.n_bets)}</td>
                  <td style={{ ...td, color: (c.metrics?.hit_rate || 0) > 0.6 ? ACCENT_2 : TEXT, fontWeight: 600 }}>{fmtPct(c.metrics?.hit_rate)}</td>
                  <td style={td}>{fmtPct(c.metrics?.calibration_delta, 2)}</td>
                  <td style={{ ...td, fontSize: 10 }}>
                    hr_l20≥{fmtPct(c.thresholds?.hr_l20_min)} · cv≤{fmtNum(c.thresholds?.cv_max)} · edge≥{fmtPct(c.thresholds?.edge_min)}
                  </td>
                  <td style={td}>
                    <Badge color={c.tag === 'ready' ? ACCENT_2 : c.tag === 'reject' ? BAD : WARN}>{c.tag}</Badge>
                  </td>
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

      {/* Compare panel */}
      {compareItems.length >= 2 && (
        <div data-testid="cand-compare" style={{
          marginTop: 14, background: SURFACE_2, border: `1px solid ${ACCENT_3}`, borderRadius: 8, padding: 12,
        }}>
          <div style={{ fontSize: 11, color: ACCENT_3, textTransform: 'uppercase', marginBottom: 8 }}>
            Compare ({compareItems.length}/4)
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${compareItems.length}, 1fr)`, gap: 12 }}>
            {compareItems.map(c => (
              <div key={c.id} style={{ background: SURFACE, padding: 10, borderRadius: 6, border: `1px solid ${BORDER}` }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: TEXT, marginBottom: 6 }}>{c.name}</div>
                <div style={{ fontSize: 10, color: DIM, fontFamily: 'monospace', lineHeight: 1.6 }}>
                  <div>tier: {c.tier}</div>
                  <div>family: <span style={{ color: ACCENT }}>{c.stat_family}</span></div>
                  <div>n: {fmtInt(c.metrics?.n_bets)}</div>
                  <div>HR: <span style={{ color: ACCENT_2 }}>{fmtPct(c.metrics?.hit_rate)}</span></div>
                  <div>Δcal: {fmtPct(c.metrics?.calibration_delta, 2)}</div>
                  <div>edge: {fmtPct(c.metrics?.avg_edge, 2)}</div>
                  <div>cv: {fmtNum(c.metrics?.avg_cv)}</div>
                  <div>tp: {fmtPct(c.metrics?.avg_tp, 1)}</div>
                  <div>daily σ: {fmtNum(c.metrics?.daily_consistency)}</div>
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

// ── Root page ─────────────────────────────────────────────────────────────
export default function AdminTesting() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '');
  const [whoami, setWhoami] = useState(null);
  const [pendingCandidate, setPendingCandidate] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const authed = !!whoami;

  return (
    <div data-testid="admin-testing-page" style={{
      minHeight: '100vh', background: BG, color: TEXT,
      fontFamily: "'Inter', system-ui, sans-serif", padding: 20,
    }}>
      <div style={{ maxWidth: 1500, margin: '0 auto' }}>
        <div style={{ marginBottom: 16 }}>
          <h1 data-testid="admin-testing-title" style={{
            fontSize: 26, margin: 0, fontWeight: 800, letterSpacing: -0.5,
            display: 'flex', alignItems: 'center', gap: 10,
          }}>
            <span style={{ color: ACCENT }}>●</span>
            Universal Historical Testing — Command Center
          </h1>
          <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>
            Self-service. Token-gated. Pipeline-driven. Results auto-loaded. No Mongo Compass required.
          </div>
        </div>

        <TokenGate token={token} setToken={setToken} whoami={whoami} setWhoami={setWhoami} />

        {authed ? (
          <>
            <PipelineRunner token={token}
              onPipelineFinished={() => setRefreshKey(k => k + 1)} />
            <ResultsPanel token={token} refreshKey={refreshKey}
              onSaveCandidate={(c) => {
                setPendingCandidate({ ...c, run_id: c.run_id });
                document.querySelector('[data-testid="candidate-section"]')?.scrollIntoView({ behavior: 'smooth' });
              }} />
            <CandidateManager token={token}
              pendingCandidate={pendingCandidate} setPendingCandidate={setPendingCandidate} />
          </>
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

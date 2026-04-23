import React, { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';

/**
 * Admin PRA Dual-Projection Audit.
 *
 * Evaluates the direct PRA model vs the 3-way component synth
 * (PTS+REB+AST) side-by-side. Read-only view for now — live
 * projection selection is unchanged.
 *
 * Auth: requires `X-Admin-Token` header (env `ADMIN_DEBUG_TOKEN`).
 * Token is kept in local component state only and is never persisted.
 */
const API = process.env.REACT_APP_BACKEND_URL;

function Card({ title, children, testId }) {
  return (
    <div
      data-testid={testId}
      style={{
        background: '#18181B',
        border: '1px solid #27272A',
        borderRadius: 12,
        padding: 20,
        marginBottom: 16,
      }}
    >
      <h3 style={{ margin: '0 0 12px', fontSize: 14, color: '#A1A1AA', textTransform: 'uppercase', letterSpacing: 0.5 }}>
        {title}
      </h3>
      {children}
    </div>
  );
}

function Stat({ label, value, testId, accent }) {
  return (
    <div data-testid={testId} style={{ minWidth: 120 }}>
      <div style={{ fontSize: 11, color: '#71717A', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: accent || '#FAFAFA', marginTop: 4 }}>
        {value ?? '—'}
      </div>
    </div>
  );
}

function Table({ columns, rows, testId }) {
  return (
    <div data-testid={testId} style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                style={{
                  textAlign: c.align || 'left',
                  padding: '8px 10px',
                  borderBottom: '1px solid #27272A',
                  color: '#71717A',
                  fontWeight: 500,
                  fontSize: 11,
                  textTransform: 'uppercase',
                  letterSpacing: 0.3,
                }}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td
                colSpan={columns.length}
                style={{ padding: '16px 10px', color: '#52525B', textAlign: 'center' }}
              >
                No data yet
              </td>
            </tr>
          )}
          {rows.map((r, i) => (
            <tr key={i} style={{ borderBottom: '1px solid #1f1f22' }}>
              {columns.map((c) => (
                <td
                  key={c.key}
                  style={{ padding: '8px 10px', textAlign: c.align || 'left', color: '#E4E4E7' }}
                >
                  {typeof c.render === 'function' ? c.render(r) : r[c.key] ?? '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AdminPRAAudit() {
  const [token, setToken] = useState(() => localStorage.getItem('adminDebugToken') || '');
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [settling, setSettling] = useState(false);
  const [error, setError] = useState(null);

  const fetchReport = useCallback(async () => {
    if (!token) {
      setError('Admin token required');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/api/v3/admin/pra-audit/report`, {
        headers: { 'X-Admin-Token': token },
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 120)}`);
      }
      const data = await res.json();
      setReport(data);
      localStorage.setItem('adminDebugToken', token);
    } catch (e) {
      setError(e.message);
      toast.error(`Report fetch failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [token]);

  const runSettle = useCallback(async () => {
    if (!token) return;
    setSettling(true);
    try {
      const res = await fetch(`${API}/api/v3/admin/pra-audit/settle`, {
        method: 'POST',
        headers: { 'X-Admin-Token': token },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      toast.success(
        `Settle complete — settled=${data.settled_this_run}, pending=${data.total_pending}`
      );
      await fetchReport();
    } catch (e) {
      toast.error(`Settle failed: ${e.message}`);
    } finally {
      setSettling(false);
    }
  }, [token, fetchReport]);

  useEffect(() => {
    if (token) fetchReport();
  }, [token, fetchReport]);

  const counts = report?.counts;
  const div = report?.divergence_audit;
  const acc = report?.accuracy_audit;

  return (
    <div
      data-testid="admin-pra-audit-page"
      style={{
        minHeight: '100vh',
        background: '#09090B',
        color: '#FAFAFA',
        fontFamily: "'Inter', system-ui, sans-serif",
        padding: 24,
      }}
    >
      <div style={{ maxWidth: 1200, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <div>
            <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0, letterSpacing: -0.5 }}>
              PRA Dual-Projection Audit
            </h1>
            <p style={{ fontSize: 13, color: '#71717A', marginTop: 4 }}>
              Direct model vs 3-way component synth (PTS+REB+AST) — evaluation only. Live projection selection unchanged.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              data-testid="pra-audit-refresh-btn"
              onClick={fetchReport}
              disabled={loading}
              style={{
                background: '#27272A',
                color: '#FAFAFA',
                border: '1px solid #3f3f46',
                borderRadius: 6,
                padding: '8px 14px',
                fontSize: 13,
                cursor: 'pointer',
                opacity: loading ? 0.5 : 1,
              }}
            >
              {loading ? 'Loading…' : 'Refresh'}
            </button>
            <button
              data-testid="pra-audit-settle-btn"
              onClick={runSettle}
              disabled={!token || settling}
              style={{
                background: '#18181B',
                color: '#FAFAFA',
                border: '1px solid #52525B',
                borderRadius: 6,
                padding: '8px 14px',
                fontSize: 13,
                cursor: 'pointer',
                opacity: settling ? 0.5 : 1,
              }}
            >
              {settling ? 'Settling…' : 'Run Settle Job'}
            </button>
          </div>
        </div>

        <Card title="Admin Token" testId="pra-audit-token-card">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="password"
              data-testid="pra-audit-token-input"
              placeholder="X-Admin-Token (ADMIN_DEBUG_TOKEN)"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              style={{
                flex: 1,
                background: '#09090B',
                border: '1px solid #27272A',
                color: '#FAFAFA',
                borderRadius: 6,
                padding: '8px 12px',
                fontSize: 13,
                fontFamily: 'monospace',
              }}
            />
            <span data-testid="pra-audit-token-status" style={{ fontSize: 12, color: token ? '#86EFAC' : '#71717A' }}>
              {token ? 'token set' : 'token required'}
            </span>
          </div>
          {error && <div style={{ color: '#FCA5A5', fontSize: 12, marginTop: 8 }}>{error}</div>}
        </Card>

        {report && (
          <>
            <Card title="Audit Counts" testId="pra-audit-counts-card">
              <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
                <Stat label="Total Rows" value={counts?.total_audit_rows} testId="pra-audit-stat-total" />
                <Stat label="Both Available" value={counts?.both_available} testId="pra-audit-stat-both" accent="#86EFAC" />
                <Stat label="Direct Only" value={counts?.direct_only} testId="pra-audit-stat-direct-only" />
                <Stat label="Synth Only" value={counts?.synth_only} testId="pra-audit-stat-synth-only" />
                <Stat label="Settled" value={counts?.settled} testId="pra-audit-stat-settled" accent={counts?.settled > 0 ? '#FBBF24' : '#52525B'} />
                <Stat label="Pending" value={counts?.pending} testId="pra-audit-stat-pending" />
              </div>
            </Card>

            <Card title="Accuracy Audit (Settled Only)" testId="pra-audit-accuracy-card">
              {acc?.settled_samples ? (
                <>
                  <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap', marginBottom: 16 }}>
                    <Stat label="Settled Samples" value={acc.settled_samples} testId="pra-audit-acc-n" />
                    <Stat
                      label="Direct MAE"
                      value={acc.direct_mae}
                      testId="pra-audit-acc-direct-mae"
                      accent={acc.direct_mae <= acc.synth_mae ? '#86EFAC' : '#FCA5A5'}
                    />
                    <Stat
                      label="Synth MAE"
                      value={acc.synth_mae}
                      testId="pra-audit-acc-synth-mae"
                      accent={acc.synth_mae <= acc.direct_mae ? '#86EFAC' : '#FCA5A5'}
                    />
                    <Stat label="Direct Bias" value={acc.direct_bias} testId="pra-audit-acc-direct-bias" />
                    <Stat label="Synth Bias" value={acc.synth_bias} testId="pra-audit-acc-synth-bias" />
                    <Stat
                      label="Direct Side Acc %"
                      value={acc.direct_side_accuracy_pct ? `${acc.direct_side_accuracy_pct}%` : '—'}
                      testId="pra-audit-acc-direct-side"
                      accent={acc.direct_side_accuracy_pct >= (acc.synth_side_accuracy_pct || 0) ? '#86EFAC' : '#FCA5A5'}
                    />
                    <Stat
                      label="Synth Side Acc %"
                      value={acc.synth_side_accuracy_pct ? `${acc.synth_side_accuracy_pct}%` : '—'}
                      testId="pra-audit-acc-synth-side"
                      accent={acc.synth_side_accuracy_pct >= (acc.direct_side_accuracy_pct || 0) ? '#86EFAC' : '#FCA5A5'}
                    />
                  </div>

                  <h4 style={{ fontSize: 12, color: '#A1A1AA', margin: '12px 0 8px' }}>MAE BY ARCHETYPE</h4>
                  <Table
                    testId="pra-audit-acc-by-archetype"
                    columns={[
                      { key: 'arch', label: 'Archetype' },
                      { key: 'n', label: 'n', align: 'right' },
                      { key: 'direct_mae', label: 'Direct MAE', align: 'right' },
                      { key: 'synth_mae', label: 'Synth MAE', align: 'right' },
                      {
                        key: 'winner',
                        label: 'Winner',
                        render: (r) =>
                          r.direct_mae == null || r.synth_mae == null
                            ? '—'
                            : r.direct_mae < r.synth_mae
                            ? <span style={{ color: '#86EFAC' }}>direct</span>
                            : r.synth_mae < r.direct_mae
                            ? <span style={{ color: '#FBBF24' }}>synth</span>
                            : <span style={{ color: '#71717A' }}>tie</span>,
                      },
                    ]}
                    rows={Object.entries(acc.by_archetype || {}).map(([arch, v]) => ({ arch, ...v }))}
                  />

                  <h4 style={{ fontSize: 12, color: '#A1A1AA', margin: '20px 0 8px' }}>MAE BY LINE BUCKET</h4>
                  <Table
                    testId="pra-audit-acc-by-bucket"
                    columns={[
                      { key: 'bucket', label: 'Line Bucket' },
                      { key: 'n', label: 'n', align: 'right' },
                      { key: 'direct_mae', label: 'Direct MAE', align: 'right' },
                      { key: 'synth_mae', label: 'Synth MAE', align: 'right' },
                      {
                        key: 'winner',
                        label: 'Winner',
                        render: (r) =>
                          r.direct_mae == null || r.synth_mae == null
                            ? '—'
                            : r.direct_mae < r.synth_mae
                            ? <span style={{ color: '#86EFAC' }}>direct</span>
                            : r.synth_mae < r.direct_mae
                            ? <span style={{ color: '#FBBF24' }}>synth</span>
                            : <span style={{ color: '#71717A' }}>tie</span>,
                      },
                    ]}
                    rows={Object.entries(acc.by_line_bucket || {}).map(([bucket, v]) => ({ bucket, ...v }))}
                  />

                  <h4 style={{ fontSize: 12, color: '#A1A1AA', margin: '20px 0 8px' }}>SYNTH OUTPERFORMS DIRECT (top 10)</h4>
                  <Table
                    testId="pra-audit-synth-wins"
                    columns={[
                      { key: 'player', label: 'Player' },
                      { key: 'line', label: 'Line', align: 'right' },
                      { key: 'side', label: 'Side' },
                      { key: 'actual', label: 'Actual', align: 'right' },
                      { key: 'direct', label: 'Direct', align: 'right' },
                      { key: 'synth', label: 'Synth', align: 'right' },
                      { key: 'edge', label: 'Edge', align: 'right', render: (r) => <span style={{ color: '#FBBF24' }}>+{r.edge}</span> },
                    ]}
                    rows={acc.synth_outperforms_direct_samples || []}
                  />

                  <h4 style={{ fontSize: 12, color: '#A1A1AA', margin: '20px 0 8px' }}>DIRECT OUTPERFORMS SYNTH (top 10)</h4>
                  <Table
                    testId="pra-audit-direct-wins"
                    columns={[
                      { key: 'player', label: 'Player' },
                      { key: 'line', label: 'Line', align: 'right' },
                      { key: 'side', label: 'Side' },
                      { key: 'actual', label: 'Actual', align: 'right' },
                      { key: 'direct', label: 'Direct', align: 'right' },
                      { key: 'synth', label: 'Synth', align: 'right' },
                      { key: 'edge', label: 'Edge', align: 'right', render: (r) => <span style={{ color: '#86EFAC' }}>{r.edge}</span> },
                    ]}
                    rows={acc.direct_outperforms_synth_samples || []}
                  />
                </>
              ) : (
                <div data-testid="pra-audit-accuracy-empty" style={{ color: '#A1A1AA', fontSize: 13 }}>
                  No settled samples yet. Run the settle job after tonight's games conclude, or wait for the 4:30 AM EST daily cron.
                </div>
              )}
            </Card>

            <Card title="Divergence Audit (Direct vs Synth — live)" testId="pra-audit-divergence-card">
              <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap', marginBottom: 16 }}>
                <Stat label="Samples" value={div?.all?.n} testId="pra-audit-div-n" />
                <Stat label="Avg |Δ| %" value={div?.all?.avg_abs_delta_pct} testId="pra-audit-div-avg" />
                <Stat label="Median |Δ| %" value={div?.all?.median_abs_delta_pct} testId="pra-audit-div-median" />
                <Stat label="Max |Δ| %" value={div?.all?.max_abs_delta_pct} testId="pra-audit-div-max" />
              </div>

              <h4 style={{ fontSize: 12, color: '#A1A1AA', margin: '12px 0 8px' }}>BY ARCHETYPE</h4>
              <Table
                testId="pra-audit-div-by-archetype"
                columns={[
                  { key: 'arch', label: 'Archetype' },
                  { key: 'n', label: 'n', align: 'right' },
                  { key: 'avg_abs_delta_pct', label: 'Avg |Δ| %', align: 'right' },
                  { key: 'median_abs_delta_pct', label: 'Median |Δ| %', align: 'right' },
                  { key: 'max_abs_delta_pct', label: 'Max |Δ| %', align: 'right' },
                ]}
                rows={Object.entries(div?.by_archetype || {}).map(([arch, v]) => ({ arch, ...v }))}
              />

              <h4 style={{ fontSize: 12, color: '#A1A1AA', margin: '20px 0 8px' }}>BY LINE BUCKET</h4>
              <Table
                testId="pra-audit-div-by-bucket"
                columns={[
                  { key: 'bucket', label: 'Line Bucket' },
                  { key: 'n', label: 'n', align: 'right' },
                  { key: 'avg_abs_delta_pct', label: 'Avg |Δ| %', align: 'right' },
                  { key: 'median_abs_delta_pct', label: 'Median |Δ| %', align: 'right' },
                  { key: 'max_abs_delta_pct', label: 'Max |Δ| %', align: 'right' },
                ]}
                rows={['<20','20-30','30-40','40-50','50+']
                  .filter((b) => div?.by_line_bucket?.[b])
                  .map((b) => ({ bucket: b, ...div.by_line_bucket[b] }))}
              />
            </Card>

            <Card title="Notes" testId="pra-audit-notes-card">
              <ul style={{ margin: 0, paddingLeft: 20, color: '#A1A1AA', fontSize: 13, lineHeight: 1.7 }}>
                {(report.notes || []).map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

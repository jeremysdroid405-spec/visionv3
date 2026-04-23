import React, { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';

/**
 * Admin Identity Status — Global Identity Rule observability.
 *
 * Consumes `/api/v3/admin/identity-status` (read-only). One card per
 * sport with a traffic-light resolution badge, scored-doc identity
 * breakdown, HR/CV status counts, and top-20 unresolved player names
 * for hub-coverage triage.
 *
 * Auth: requires `X-Admin-Token` header (env `ADMIN_DEBUG_TOKEN`).
 * Token is kept in component state + localStorage, never sent anywhere
 * besides this endpoint.
 */
const API = process.env.REACT_APP_BACKEND_URL;

const BG = '#09090B';
const SURFACE = '#18181B';
const BORDER = '#27272A';
const BORDER_STRONG = '#3F3F46';
const MUTED = '#71717A';
const DIM = '#52525B';
const TEXT = '#FAFAFA';

const TRAFFIC_LIGHT = {
  green: { color: '#34D399', label: 'Healthy' },
  yellow: { color: '#FBBF24', label: 'Watch' },
  red: { color: '#F87171', label: 'Drift' },
};

function resolutionBadge(pct) {
  if (pct === null || pct === undefined) return 'red';
  if (pct >= 99) return 'green';
  if (pct >= 95) return 'yellow';
  return 'red';
}

function Stat({ label, value, accent, testId }) {
  return (
    <div data-testid={testId} style={{ minWidth: 110 }}>
      <div style={{ fontSize: 10, color: MUTED, textTransform: 'uppercase', letterSpacing: 0.5 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600, color: accent || TEXT, marginTop: 4 }}>
        {value === null || value === undefined ? '—' : value}
      </div>
    </div>
  );
}

function StatusRow({ label, counts, testId }) {
  if (!counts) return null;
  const order = ['computed', 'missing_source_distribution', 'unavailable_stat_family', 'missing_bdl_id'];
  return (
    <div data-testid={testId} style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
      <div style={{ fontSize: 11, color: MUTED, textTransform: 'uppercase', letterSpacing: 0.5, minWidth: 50 }}>
        {label}
      </div>
      {order.map((k) => {
        const v = counts[k] ?? 0;
        const accent = k === 'computed' ? TEXT : v > 0 ? '#FBBF24' : DIM;
        return (
          <div key={k} style={{ minWidth: 90 }}>
            <div style={{ fontSize: 10, color: DIM, textTransform: 'uppercase', letterSpacing: 0.3 }}>
              {k.replace(/_/g, ' ')}
            </div>
            <div style={{ fontSize: 14, fontWeight: 600, color: accent, marginTop: 2 }}>
              {v.toLocaleString()}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SportCard({ sport, data }) {
  const live = data.live_props || {};
  const scored = data.scored_props || {};
  const ident = scored.identity || {};
  const pct = live.resolution_pct;
  const light = resolutionBadge(pct);
  const meta = TRAFFIC_LIGHT[light];
  const top = data.top_unresolved_players || [];

  return (
    <div
      data-testid={`identity-card-${sport}`}
      style={{
        background: SURFACE,
        border: `1px solid ${BORDER}`,
        borderLeft: `4px solid ${meta.color}`,
        borderRadius: 12,
        padding: 22,
        marginBottom: 18,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <h2
            data-testid={`identity-card-${sport}-title`}
            style={{ margin: 0, fontSize: 24, fontWeight: 700, letterSpacing: -0.3 }}
          >
            {sport.toUpperCase()}
          </h2>
          <span
            data-testid={`identity-card-${sport}-badge`}
            style={{
              fontSize: 11,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: 0.6,
              background: `${meta.color}22`,
              color: meta.color,
              padding: '4px 10px',
              borderRadius: 999,
            }}
          >
            {meta.label}
          </span>
        </div>
        <div
          data-testid={`identity-card-${sport}-pct`}
          style={{ fontSize: 36, fontWeight: 700, color: meta.color, letterSpacing: -1 }}
        >
          {pct === null || pct === undefined ? '—' : `${pct.toFixed(2)}%`}
        </div>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 12,
          padding: '14px 0',
          borderTop: `1px solid ${BORDER}`,
          borderBottom: `1px solid ${BORDER}`,
        }}
      >
        <Stat
          label="Live Props"
          value={live.total?.toLocaleString()}
          testId={`identity-${sport}-live-total`}
        />
        <Stat
          label="Live Resolved"
          value={live.resolved?.toLocaleString()}
          accent="#34D399"
          testId={`identity-${sport}-live-resolved`}
        />
        <Stat
          label="Live Missing"
          value={live.missing_bdl_id?.toLocaleString()}
          accent={live.missing_bdl_id > 0 ? '#F87171' : undefined}
          testId={`identity-${sport}-live-missing`}
        />
        <Stat
          label="Scored Total"
          value={scored.total?.toLocaleString()}
          testId={`identity-${sport}-scored-total`}
        />
      </div>

      <div style={{ padding: '14px 0', borderBottom: `1px solid ${BORDER}` }}>
        <div
          style={{
            fontSize: 11,
            color: MUTED,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            marginBottom: 10,
          }}
        >
          Scored Identity · {scored.version_tag}
        </div>
        <div style={{ display: 'flex', gap: 24 }}>
          <Stat
            label="Resolved"
            value={ident.resolved?.toLocaleString()}
            accent="#34D399"
            testId={`identity-${sport}-scored-resolved`}
          />
          <Stat
            label="Missing BDL ID"
            value={ident.missing_bdl_id?.toLocaleString()}
            accent={ident.missing_bdl_id > 0 ? '#F87171' : undefined}
            testId={`identity-${sport}-scored-missing`}
          />
        </div>
      </div>

      <div style={{ padding: '14px 0', borderBottom: `1px solid ${BORDER}`, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <StatusRow label="HR" counts={scored.hit_rate_status} testId={`identity-${sport}-hr-status`} />
        <StatusRow label="CV" counts={scored.cv_status} testId={`identity-${sport}-cv-status`} />
      </div>

      <div style={{ paddingTop: 14 }}>
        <div
          style={{
            fontSize: 11,
            color: MUTED,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            marginBottom: 10,
          }}
        >
          Top Unresolved Players ({top.length})
        </div>
        {top.length === 0 ? (
          <div
            data-testid={`identity-${sport}-unresolved-empty`}
            style={{ color: DIM, fontSize: 13, padding: '6px 0' }}
          >
            None — 100% of live props resolved to a canonical bdl_player_id.
          </div>
        ) : (
          <div
            data-testid={`identity-${sport}-unresolved-list`}
            style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 6 }}
          >
            {top.map((row) => (
              <div
                key={row.player_name}
                style={{
                  background: BG,
                  border: `1px solid ${BORDER}`,
                  borderRadius: 6,
                  padding: '8px 10px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 8,
                }}
              >
                <span style={{ fontSize: 13, color: TEXT, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {row.player_name || '(unknown)'}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    color: MUTED,
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {row.prop_count} props
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function AdminIdentityStatus() {
  const [token, setToken] = useState(() => localStorage.getItem('adminDebugToken') || '');
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [fetchedAt, setFetchedAt] = useState(null);

  const fetchReport = useCallback(async () => {
    if (!token) {
      setError('Admin token required');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/api/v3/admin/identity-status`, {
        headers: { 'X-Admin-Token': token },
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 140)}`);
      }
      const data = await res.json();
      setReport(data);
      setFetchedAt(new Date());
      localStorage.setItem('adminDebugToken', token);
    } catch (e) {
      setError(e.message);
      toast.error(`Identity fetch failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (token) fetchReport();
  }, [token, fetchReport]);

  const sports = report?.sports ? Object.entries(report.sports) : [];

  return (
    <div
      data-testid="admin-identity-status-page"
      style={{
        minHeight: '100vh',
        background: BG,
        color: TEXT,
        fontFamily: "'Inter', system-ui, sans-serif",
        padding: 24,
      }}
    >
      <div style={{ maxWidth: 1200, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <div>
            <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0, letterSpacing: -0.5 }}>
              Identity Status
            </h1>
            <p style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>
              Global Identity Rule · bdl_player_id coverage across every scored sport. Read-only.
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {fetchedAt && (
              <span data-testid="identity-fetched-at" style={{ fontSize: 12, color: DIM }}>
                {fetchedAt.toLocaleTimeString()}
              </span>
            )}
            <button
              data-testid="identity-refresh-btn"
              onClick={fetchReport}
              disabled={loading || !token}
              style={{
                background: BORDER,
                color: TEXT,
                border: `1px solid ${BORDER_STRONG}`,
                borderRadius: 6,
                padding: '8px 14px',
                fontSize: 13,
                cursor: 'pointer',
                opacity: loading || !token ? 0.5 : 1,
              }}
            >
              {loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>
        </div>

        <div
          data-testid="identity-token-card"
          style={{
            background: SURFACE,
            border: `1px solid ${BORDER}`,
            borderRadius: 12,
            padding: 18,
            marginBottom: 18,
          }}
        >
          <div
            style={{
              fontSize: 11,
              color: MUTED,
              textTransform: 'uppercase',
              letterSpacing: 0.5,
              marginBottom: 10,
            }}
          >
            Admin Token
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="password"
              data-testid="identity-token-input"
              placeholder="X-Admin-Token (ADMIN_DEBUG_TOKEN)"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              style={{
                flex: 1,
                background: BG,
                border: `1px solid ${BORDER}`,
                borderRadius: 6,
                padding: '8px 10px',
                color: TEXT,
                fontSize: 13,
                fontFamily: 'monospace',
              }}
            />
            <button
              data-testid="identity-load-btn"
              onClick={fetchReport}
              disabled={!token || loading}
              style={{
                background: '#6366F1',
                color: TEXT,
                border: 'none',
                borderRadius: 6,
                padding: '8px 16px',
                fontSize: 13,
                fontWeight: 600,
                cursor: 'pointer',
                opacity: !token || loading ? 0.5 : 1,
              }}
            >
              Load
            </button>
          </div>
          {error && (
            <div
              data-testid="identity-error"
              style={{ marginTop: 10, fontSize: 12, color: '#F87171' }}
            >
              {error}
            </div>
          )}
        </div>

        {!report && !loading && !error && (
          <div
            data-testid="identity-empty-state"
            style={{ color: DIM, fontSize: 14, padding: 32, textAlign: 'center' }}
          >
            Enter an admin token above and click Load.
          </div>
        )}

        {sports.map(([sport, data]) => (
          <SportCard key={sport} sport={sport} data={data} />
        ))}

        <div
          style={{
            marginTop: 24,
            padding: 14,
            fontSize: 11,
            color: DIM,
            background: SURFACE,
            border: `1px dashed ${BORDER}`,
            borderRadius: 8,
            lineHeight: 1.5,
          }}
        >
          <strong style={{ color: MUTED }}>Traffic light:</strong>{' '}
          <span style={{ color: TRAFFIC_LIGHT.green.color }}>● 99%+</span>{' '}
          <span style={{ color: TRAFFIC_LIGHT.yellow.color }}>● 95–99%</span>{' '}
          <span style={{ color: TRAFFIC_LIGHT.red.color }}>● &lt; 95%</span>
          {'  '}Applied to live-props `bdl_player_id` resolution %. Data source:{' '}
          <code>/api/v3/admin/identity-status</code>.
        </div>
      </div>
    </div>
  );
}

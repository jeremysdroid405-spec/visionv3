import React from 'react';

/**
 * Hard error boundary for the /admin/testing route.
 *
 * Renders a token-login-styled fallback (not a white-screen) so the
 * page remains diagnosable even if a child component crashes from an
 * unexpected API response shape. Children are unmounted on crash; the
 * user can still see the token prompt to re-login and copy the error
 * details for support.
 */
export class AdminTestingErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error, info: null };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('[AdminTestingErrorBoundary]', error, info);
    this.setState({ info });
  }

  reset = () => this.setState({ hasError: false, error: null, info: null });

  render() {
    if (!this.state.hasError) return this.props.children;

    const err = this.state.error;
    const msg = err?.message || String(err || 'Unknown error');
    const stack = err?.stack || '';
    const componentStack = this.state.info?.componentStack || '';

    return (
      <div
        data-testid="admin-testing-error-boundary"
        style={{
          minHeight: '100vh',
          background: '#09090B',
          color: '#FAFAFA',
          fontFamily: "'Inter', system-ui, sans-serif",
          padding: 20,
        }}
      >
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <div
            style={{
              background: '#3F1D1D',
              border: '1px solid #F87171',
              color: '#F87171',
              padding: '10px 14px',
              borderRadius: 8,
              marginBottom: 14,
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: 0.5,
              textTransform: 'uppercase',
            }}
          >
            ⚠ Admin Testing UI crashed — running in safe fallback
          </div>

          <h1
            style={{
              fontSize: 22,
              margin: '0 0 8px',
              fontWeight: 800,
              letterSpacing: -0.3,
            }}
          >
            Universal Historical Testing — Crash Recovery
          </h1>
          <p style={{ fontSize: 13, color: '#71717A', marginTop: 0 }}>
            A component in the admin testing page threw an exception. The
            crash has been logged to the browser console. You can retry, or
            navigate back to the home page below.
          </p>

          <div
            style={{
              background: '#18181B',
              border: '1px solid #27272A',
              borderRadius: 8,
              padding: 14,
              marginTop: 14,
            }}
          >
            <div
              style={{
                fontSize: 11,
                color: '#71717A',
                textTransform: 'uppercase',
                letterSpacing: 0.5,
                marginBottom: 6,
              }}
            >
              Error
            </div>
            <pre
              data-testid="admin-testing-error-message"
              style={{
                fontSize: 12,
                color: '#FBBF24',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                margin: 0,
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              }}
            >
              {msg}
            </pre>
            {(stack || componentStack) && (
              <details style={{ marginTop: 12 }}>
                <summary
                  style={{
                    fontSize: 11,
                    color: '#71717A',
                    cursor: 'pointer',
                  }}
                >
                  Stack trace
                </summary>
                <pre
                  style={{
                    fontSize: 10,
                    color: '#A1A1AA',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    marginTop: 6,
                    fontFamily: 'ui-monospace, monospace',
                  }}
                >
                  {stack}
                  {componentStack ? '\n\nComponent stack:\n' + componentStack : ''}
                </pre>
              </details>
            )}
          </div>

          <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
            <button
              data-testid="admin-testing-error-retry"
              onClick={this.reset}
              style={{
                background: '#A78BFA',
                color: '#09090B',
                border: '1px solid #A78BFA',
                borderRadius: 6,
                padding: '7px 14px',
                fontSize: 12,
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              Retry
            </button>
            <button
              data-testid="admin-testing-error-clear-token"
              onClick={() => {
                try {
                  localStorage.removeItem('emergentAdminToken');
                  localStorage.removeItem('emergentAdminPipeline');
                } catch (_) {
                  /* localStorage may be blocked — ignore */
                }
                this.reset();
              }}
              style={{
                background: 'transparent',
                color: '#FAFAFA',
                border: '1px solid #27272A',
                borderRadius: 6,
                padding: '7px 14px',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Clear cached state & retry
            </button>
            <a
              data-testid="admin-testing-error-home"
              href="/"
              style={{
                background: 'transparent',
                color: '#FAFAFA',
                border: '1px solid #27272A',
                borderRadius: 6,
                padding: '7px 14px',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
                textDecoration: 'none',
              }}
            >
              ← Home
            </a>
          </div>
        </div>
      </div>
    );
  }
}

export default AdminTestingErrorBoundary;

import React, { useEffect, useState } from 'react';
import { getHealth, type HealthResponse } from '@/api/client';

// ────────────────────────────────────────────────────────────────────────────
// Component
// ────────────────────────────────────────────────────────────────────────────

/**
 * Dashboard — shows backend health status and high-level stats.
 *
 * On mount it fetches `/health` and displays the result. The user can
 * manually re-check by clicking the refresh button. This component is
 * intended as a "home view" before any document is uploaded.
 */
const Dashboard: React.FC = () => {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealth = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getHealth();
      setHealth(data);
    } catch (err) {
      setError('Unable to reach the backend. Is the API running?');
      console.error('[Dashboard] Health check failed:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
    // Poll every 30 seconds to keep the dashboard up-to-date.
    const interval = setInterval(fetchHealth, 30_000);
    return () => clearInterval(interval);
  }, []);

  /** Map the health status string to a CSS class for colour-coding. */
  const statusClass =
    health?.status === 'healthy'
      ? 'status-badge--healthy'
      : health?.status === 'degraded'
        ? 'status-badge--degraded'
        : 'status-badge--unhealthy';

  return (
    <div className="dashboard">
      <div className="dashboard__header">
        <h2 className="dashboard__title">System Dashboard</h2>
        <button
          className="btn btn--small btn--secondary"
          onClick={fetchHealth}
          disabled={loading}
        >
          {loading ? 'Refreshing…' : '↻ Refresh'}
        </button>
      </div>

      {/* Health status card. */}
      <div className="dashboard__card">
        <h3 className="dashboard__card-title">API Health</h3>

        {loading && !health ? (
          <p className="dashboard__loading">Checking…</p>
        ) : error ? (
          <p className="dashboard__error">{error}</p>
        ) : health ? (
          <div className="dashboard__health">
            <div className="dashboard__status-row">
              <span className={`status-badge ${statusClass}`}>
                {health.status.charAt(0).toUpperCase() + health.status.slice(1)}
              </span>
              <span className="dashboard__version">v{health.version}</span>
            </div>

            <p className="dashboard__uptime">
              Uptime: {Math.round(health.uptime / 60)}m {Math.round(health.uptime % 60)}s
            </p>

            {/* Individual service statuses. */}
            {health.services && Object.keys(health.services).length > 0 && (
              <div className="dashboard__services">
                <h4>Services</h4>
                <ul className="dashboard__service-list">
                  {Object.entries(health.services).map(([name, status]) => (
                    <li key={name} className="dashboard__service-item">
                      <span
                        className={`status-dot ${
                          status === 'ok' || status === 'healthy'
                            ? 'status-dot--ok'
                            : 'status-dot--error'
                        }`}
                      />
                      <span className="dashboard__service-name">{name}</span>
                      <span className="dashboard__service-status">{status}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
};

export default Dashboard;

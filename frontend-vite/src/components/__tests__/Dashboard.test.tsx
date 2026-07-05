/**
 * Tests for the Dashboard component.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/utils/render';
import { server } from '@/test/mocks/server';
import Dashboard from '@/components/Dashboard';

// ── MSW lifecycle ────────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('Dashboard', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders without crashing', () => {
    render(<Dashboard />);
    expect(screen.getByText('System Dashboard')).toBeInTheDocument();
  });

  it('displays loading state initially', () => {
    render(<Dashboard />);

    // While the health fetch is in flight, show "Checking…"
    expect(screen.getByText('Checking…')).toBeInTheDocument();
  });

  it('shows healthy status after fetch resolves', async () => {
    render(<Dashboard />);

    // Advance timers to let the async effect resolve.
    await vi.advanceTimersByTimeAsync(100);

    // Wait for the healthy status badge to appear.
    await waitFor(() => {
      expect(screen.getByText('Healthy')).toBeInTheDocument();
    });

    expect(screen.getByText('v0.3.0')).toBeInTheDocument();
  });

  it('displays service status indicators', async () => {
    render(<Dashboard />);

    await vi.advanceTimersByTimeAsync(100);

    await waitFor(() => {
      expect(screen.getByText('Services')).toBeInTheDocument();
    });

    // The mock handlers return services: ocr_engine, database, cache.
    expect(screen.getByText('ocr_engine')).toBeInTheDocument();
    expect(screen.getByText('database')).toBeInTheDocument();
    expect(screen.getByText('cache')).toBeInTheDocument();
  });

  it('displays uptime information', async () => {
    render(<Dashboard />);

    await vi.advanceTimersByTimeAsync(100);

    await waitFor(() => {
      // uptime is 3672s = 61m 12s
      expect(screen.getByText(/Uptime:/)).toBeInTheDocument();
    });
  });
});

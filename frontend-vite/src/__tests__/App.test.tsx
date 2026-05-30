/**
 * App integration test.
 *
 * Verifies that the root App component renders correctly with its
 * child components in the default (dashboard) view.
 */

import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/utils/render';
import { server } from '@/test/mocks/server';
import App from '@/App';

// ── MSW lifecycle ────────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('App', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders without crashing', () => {
    render(<App />);
    // If we get here without throwing, the component rendered.
    expect(document.querySelector('.app')).toBeInTheDocument();
  });

  it('shows the Header component', () => {
    render(<App />);

    expect(
      screen.getByRole('heading', { level: 1, name: /Medical Handwriting OCR/ }),
    ).toBeInTheDocument();
  });

  it('shows the Dashboard initially', () => {
    render(<App />);

    expect(screen.getByText('System Dashboard')).toBeInTheDocument();
  });

  it('shows the UploadZone', () => {
    render(<App />);

    // UploadZone is rendered in both dashboard and results views.
    expect(
      screen.getByText(/Drag & drop prescription images or PDFs here/i),
    ).toBeInTheDocument();
  });

  it('shows the footer with copyright', () => {
    render(<App />);

    const currentYear = new Date().getFullYear();
    expect(
      screen.getByText(new RegExp(`Medical Handwriting OCR.*${currentYear}`)),
    ).toBeInTheDocument();
  });

  it('shows Dashboard health loading initially', () => {
    render(<App />);

    expect(screen.getByText('Checking…')).toBeInTheDocument();
  });
});

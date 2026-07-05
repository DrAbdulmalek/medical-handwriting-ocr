/**
 * Global test setup file.
 *
 * - Imports @testing-library/jest-dom to extend Vitest's `expect` with
 *   DOM-specific matchers (toBeInTheDocument, toHaveTextContent, etc.).
 * - Configures global mocks for browser APIs not available in jsdom.
 */

import '@testing-library/jest-dom/vitest';

// ── Global mocks ──────────────────────────────────────────────────────────────

/**
 * Mock window.matchMedia which is not implemented in jsdom.
 * Many UI libraries (e.g., MUI, Tailwind responsive) rely on this.
 */
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},   // deprecated but still referenced
    removeListener: () => {}, // deprecated but still referenced
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

/**
 * Mock HTMLCanvasElement.prototype.getContext for components
 * that may attempt canvas operations in the browser.
 */
HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue(null);

/**
 * Suppress console.error output from React during tests when it's expected
 * (e.g., testing error boundaries, intentional prop validation failures).
 *
 * Uncomment selectively per test file if needed:
 *
 *   const originalError = console.error;
 *   beforeAll(() => { console.error = vi.fn(); });
 *   afterAll(() => { console.error = originalError; });
 */

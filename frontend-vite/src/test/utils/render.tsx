/**
 * Custom render utility for component tests.
 *
 * Wraps components with necessary providers (Toaster from react-hot-toast)
 * and re-exports everything from @testing-library/react so test files can
 * import from this single module.
 *
 * Usage:
 *
 *   import { render, screen } from '@/test/utils/render';
 *   import Header from '@/components/Header';
 *
 *   test('renders header', () => {
 *     render(<Header />);
 *     expect(screen.getByText('Medical Handwriting OCR')).toBeInTheDocument();
 *   });
 */

import React, { ReactElement } from 'react';
import { render, RenderOptions } from '@testing-library/react';
import { Toaster } from 'react-hot-toast';

/**
 * AllProviders — wraps children with all context providers required by the app.
 *
 * Add more providers here as the application grows (e.g., Router, Theme, I18n).
 */
const AllProviders = ({ children }: { children: React.ReactNode }) => {
  return (
    <>
      {children}
      <Toaster position="top-right" />
    </>
  );
};

/**
 * Custom render function that wraps components with all necessary providers.
 */
const customRender = (
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>,
) => render(ui, { wrapper: AllProviders, ...options });

// Re-export everything from @testing-library/react for convenience.
export { customRender as render };

// Re-export commonly used testing library utilities.
export { screen, fireEvent, waitFor, act, cleanup } from '@testing-library/react';
export { default as userEvent } from '@testing-library/user-event';

// Re-export jest-dom matchers types
export type { RenderOptions } from '@testing-library/react';

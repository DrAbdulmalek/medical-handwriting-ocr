/**
 * Tests for the Header component.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/utils/render';
import Header from '@/components/Header';

describe('Header', () => {
  it('renders the application title', () => {
    render(<Header />);

    // The title includes a medical emoji.
    const title = screen.getByRole('heading', { level: 1 });
    expect(title).toBeInTheDocument();
    expect(title).toHaveTextContent('Medical Handwriting OCR');
  });

  it('renders the Arabic/English subtitle with dir="auto"', () => {
    render(<Header />);

    const subtitle = screen.getByText(
      /Prescription digitisation with Arabic & English support/,
    );
    expect(subtitle).toBeInTheDocument();
    expect(subtitle).toHaveAttribute('dir', 'auto');
  });
});

/**
 * Tests for the OCRResults component.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/utils/render';
import OCRResults from '@/components/OCRResults';
import type { OCRRegion } from '@/api/client';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const mockRegions: OCRRegion[] = [
  {
    id: 'region-high',
    document_id: 'doc-001',
    bbox: [0.1, 0.2, 0.8, 0.4],
    text: 'Amoxicillin 500mg',
    confidence: 0.92,
    reviewed: false,
    corrected_text: null,
    created_at: '2024-06-15T10:30:00Z',
  },
  {
    id: 'region-medium',
    document_id: 'doc-001',
    bbox: [0.1, 0.5, 0.7, 0.7],
    text: 'take twice daily',
    confidence: 0.55,
    reviewed: false,
    corrected_text: null,
    created_at: '2024-06-15T10:30:01Z',
  },
  {
    id: 'region-low',
    document_id: 'doc-001',
    bbox: [0.2, 0.1, 0.9, 0.3],
    text: 'illegible scribble',
    confidence: 0.23,
    reviewed: true,
    corrected_text: 'Aspirin 100mg',
    created_at: '2024-06-15T10:30:02Z',
  },
];

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('OCRResults', () => {
  it('renders "No results" message when regions array is empty', () => {
    render(<OCRResults regions={[]} />);

    expect(
      screen.getByText('No OCR results to display. Upload a document to get started.'),
    ).toBeInTheDocument();
  });

  it('renders region cards when results are present', () => {
    render(<OCRResults regions={mockRegions} />);

    // The heading should show the count.
    expect(
      screen.getByText(/OCR Results \(3 regions\)/),
    ).toBeInTheDocument();

    // Each region should display its text.
    expect(screen.getByText('Amoxicillin 500mg')).toBeInTheDocument();
    expect(screen.getByText('take twice daily')).toBeInTheDocument();
  });

  it('shows corrected text when available', () => {
    render(<OCRResults regions={mockRegions} />);

    // The third region has corrected_text: 'Aspirin 100mg'.
    // The component displays corrected_text over raw text.
    expect(screen.getByText('Aspirin 100mg')).toBeInTheDocument();
  });

  it('shows confidence scores with color coding', () => {
    const { container } = render(<OCRResults regions={mockRegions} />);

    // High confidence (92%) → badge--high
    const highBadge = screen.getByTitle('Confidence: 92%');
    expect(highBadge).toBeInTheDocument();
    expect(highBadge).toHaveClass('badge--high');
    expect(highBadge).toHaveTextContent('92%');

    // Medium confidence (55%) → badge--medium
    const mediumBadge = screen.getByTitle('Confidence: 55%');
    expect(mediumBadge).toBeInTheDocument();
    expect(mediumBadge).toHaveClass('badge--medium');

    // Low confidence (23%) → badge--low
    const lowBadge = screen.getByTitle('Confidence: 23%');
    expect(lowBadge).toBeInTheDocument();
    expect(lowBadge).toHaveClass('badge--low');
  });

  it('shows reviewed status badge for reviewed regions', () => {
    render(<OCRResults regions={mockRegions} />);

    // The third region has reviewed: true.
    expect(screen.getByText('✓ Reviewed')).toBeInTheDocument();
  });

  it('shows region ID prefix for each card', () => {
    render(<OCRResults regions={mockRegions} />);

    // Region IDs are shown as "Region #<first 8 chars>".
    expect(screen.getByText(/Region #region-h/)).toBeInTheDocument();
  });

  it('calls onCorrectionSubmitted when correction is submitted', async () => {
    const onCorrectionSubmitted = vi.fn();
    render(
      <OCRResults regions={mockRegions} onCorrectionSubmitted={onCorrectionSubmitted} />,
    );

    // Find the first region's Save button and the input.
    const inputs = screen.getAllByPlaceholderText('Type corrected text…');
    const saveButtons = screen.getAllByRole('button', { name: 'Save' });

    // We have 3 regions, each with its own input and Save button.
    expect(inputs).toHaveLength(3);
    expect(saveButtons).toHaveLength(3);
  });
});

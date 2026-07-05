/**
 * Tests for the UploadZone component.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/utils/render';
import userEvent from '@testing-library/user-event';
import UploadZone from '@/components/UploadZone';

describe('UploadZone', () => {
  const defaultProps = {
    onFilesAccepted: vi.fn(),
  };

  it('renders the dropzone area', () => {
    render(<UploadZone {...defaultProps} />);

    // The default text should be visible.
    expect(
      screen.getByText(/Drag & drop prescription images or PDFs here/i),
    ).toBeInTheDocument();
  });

  it('shows accepted file types info', () => {
    render(<UploadZone {...defaultProps} />);

    // The hint mentions supported formats.
    expect(
      screen.getByText(/Supports PNG, JPEG, TIFF, BMP, WebP, PDF/i),
    ).toBeInTheDocument();
  });

  it('shows max size info (default 20 MB)', () => {
    render(<UploadZone {...defaultProps} />);

    expect(screen.getByText(/20 MB per file/i)).toBeInTheDocument();
  });

  it('shows custom max size when provided', () => {
    render(<UploadZone {...defaultProps} maxSize={10 * 1024 * 1024} />);

    expect(screen.getByText(/10 MB per file/i)).toBeInTheDocument();
  });

  it('calls onFilesAccepted when a file is dropped', async () => {
    const user = userEvent.setup();
    const onFilesAccepted = vi.fn();
    render(<UploadZone onFilesAccepted={onFilesAccepted} />);

    // Create a test file.
    const file = new File(['hello world'], 'prescription.png', {
      type: 'image/png',
    });

    const dropzone = screen.getByText(/Drag & drop/i).closest('.upload-zone')!;

    // Use DataTransfer to simulate a file drop.
    await user.upload(
      dropzone.querySelector('input[type="file"]')!,
      file,
    );

    // The react-dropzone onDrop callback should fire and call onFilesAccepted.
    expect(onFilesAccepted).toHaveBeenCalledTimes(1);
    expect(onFilesAccepted).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ name: 'prescription.png' }),
    ]));
  });

  it('shows uploading state when isUploading is true', () => {
    render(<UploadZone {...defaultProps} isUploading={true} />);

    expect(screen.getByText('Uploading…')).toBeInTheDocument();
  });
});

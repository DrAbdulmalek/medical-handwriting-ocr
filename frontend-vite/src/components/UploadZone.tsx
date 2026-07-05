import React, { useCallback } from 'react';
import { useDropzone, FileRejection } from 'react-dropzone';

// ────────────────────────────────────────────────────────────────────────────
// Props
// ────────────────────────────────────────────────────────────────────────────

interface UploadZoneProps {
  /** Callback invoked with the selected file(s). */
  onFilesAccepted: (files: File[]) => void;
  /** Whether an upload is currently in progress. */
  isUploading?: boolean;
  /** Maximum number of files allowed per drop. */
  maxFiles?: number;
  /** Maximum file size in bytes (default 20 MB). */
  maxSize?: number;
  /** Accepted MIME types. */
  accept?: Record<string, string[]>;
}

// ────────────────────────────────────────────────────────────────────────────
// Defaults
// ────────────────────────────────────────────────────────────────────────────

const DEFAULT_MAX_FILES = 5;
const DEFAULT_MAX_SIZE = 20 * 1024 * 1024; // 20 MB

const DEFAULT_ACCEPT: Record<string, string[]> = {
  'image/*': ['.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.webp'],
  'application/pdf': ['.pdf'],
};

// ────────────────────────────────────────────────────────────────────────────
// Component
// ────────────────────────────────────────────────────────────────────────────

/**
 * Drag-and-drop file upload zone.
 *
 * Wraps `react-dropzone` with sensible defaults for prescription images
 * and PDF documents. Visual feedback changes on drag-hover, accept, and
 * reject states.
 */
const UploadZone: React.FC<UploadZoneProps> = ({
  onFilesAccepted,
  isUploading = false,
  maxFiles = DEFAULT_MAX_FILES,
  maxSize = DEFAULT_MAX_SIZE,
  accept = DEFAULT_ACCEPT,
}) => {
  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      if (acceptedFiles.length > 0) {
        onFilesAccepted(acceptedFiles);
      }
    },
    [onFilesAccepted],
  );

  const onDropRejected = useCallback((rejections: FileRejection[]) => {
    const messages = rejections
      .map((r) => {
        const errors = r.errors.map((e) => e.message).join(', ');
        return `"${r.file.name}": ${errors}`;
      })
      .join('; ');
    console.warn(`[UploadZone] Rejected files: ${messages}`);
  }, []);

  const {
    getRootProps,
    getInputProps,
    isDragActive,
    isDragAccept,
    isDragReject,
  } = useDropzone({
    onDrop,
    onDropRejected,
    accept,
    maxFiles,
    maxSize,
    disabled: isUploading,
  });

  /** Build a CSS class reflecting the current drag state. */
  let zoneClassName = 'upload-zone';
  if (isDragAccept) zoneClassName += ' upload-zone--accept';
  if (isDragReject) zoneClassName += ' upload-zone--reject';
  if (isUploading) zoneClassName += ' upload-zone--uploading';

  return (
    <div {...getRootProps({ className: zoneClassName })}>
      <input {...getInputProps()} />

      {isUploading ? (
        <div className="upload-zone__content">
          <span className="upload-zone__icon">⏳</span>
          <p className="upload-zone__text">Uploading…</p>
        </div>
      ) : isDragActive ? (
        <div className="upload-zone__content">
          <span className="upload-zone__icon">
            {isDragReject ? '❌' : '📥'}
          </span>
          <p className="upload-zone__text">
            {isDragReject
              ? 'This file type is not supported'
              : 'Drop the file(s) here…'}
          </p>
        </div>
      ) : (
        <div className="upload-zone__content">
          <span className="upload-zone__icon">📄</span>
          <p className="upload-zone__text">
            Drag &amp; drop prescription images or PDFs here, or{' '}
            <strong>click to browse</strong>.
          </p>
          <p className="upload-zone__hint" dir="auto">
            Supports PNG, JPEG, TIFF, BMP, WebP, PDF — up to{' '}
            {Math.round(maxSize / 1024 / 1024)} MB per file.
          </p>
        </div>
      )}
    </div>
  );
};

export default UploadZone;

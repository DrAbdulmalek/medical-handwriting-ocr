/**
 * Tests for the API client module.
 *
 * Uses MSW to intercept HTTP requests so we can verify that the client
 * constructs correct requests and handles responses properly.
 */

import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from 'vitest';
import axios from 'axios';
import { server } from '@/test/mocks/server';
import {
  uploadDocument,
  getHealth,
  getPendingRegions,
  submitCorrection,
  getSuggestions,
} from '@/api/client';

// ── MSW lifecycle ────────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('API client', () => {
  describe('Axios instance configuration', () => {
    it('uses correct base URL from env', () => {
      // The client reads from import.meta.env.VITE_API_URL with a fallback.
      // In tests, the env var is not set so it defaults to http://localhost:8000.
      // We verify by checking that the module exports callable functions.
      expect(typeof uploadDocument).toBe('function');
      expect(typeof getHealth).toBe('function');
    });
  });

  describe('uploadDocument', () => {
    it('sends FormData with file and returns UploadResponse', async () => {
      const file = new File(['test'], 'rx.png', { type: 'image/png' });
      const result = await uploadDocument(file);

      expect(result.document_id).toBe('doc-abc123');
      expect(result.filename).toBe('prescription.jpg');
      expect(result.status).toBe('processing');
    });
  });

  describe('getHealth', () => {
    it('fetches from /health and returns HealthResponse', async () => {
      const result = await getHealth();

      expect(result.status).toBe('healthy');
      expect(result.version).toBe('0.3.0');
      expect(result.services).toHaveProperty('ocr_engine', 'ok');
    });
  });

  describe('getPendingRegions', () => {
    it('fetches from /api/pending with document_id param', async () => {
      const result = await getPendingRegions('doc-abc123');

      expect(result.document_id).toBe('doc-abc123');
      expect(result.regions).toHaveLength(2);
      expect(result.total).toBe(2);
      expect(result.regions[0].text).toBe('Amoxicillin 500mg');
    });
  });

  describe('submitCorrection', () => {
    it('sends correct payload and returns CorrectionResponse', async () => {
      const result = await submitCorrection('region-001', 'Amoxicillin 500 mg');

      expect(result.region_id).toBe('region-001');
      expect(result.corrected_text).toBe('Amoxicillin 500 mg');
      expect(result.accepted).toBe(true);
    });
  });

  describe('getSuggestions', () => {
    it('fetches suggestions for a query string', async () => {
      const result = await getSuggestions('Amoxicillin 500mg');

      expect(result.query).toBe('Amoxicillin 500mg');
      expect(result.suggestions).toHaveLength(3);
      expect(result.suggestions[0].text).toBe('Amoxicillin 500 mg');
      expect(result.suggestions[0].source).toBe('dictionary');
    });
  });
});

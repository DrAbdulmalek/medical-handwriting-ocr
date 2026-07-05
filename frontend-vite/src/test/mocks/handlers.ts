/**
 * MSW (Mock Service Worker) request handlers.
 *
 * Provides mock responses for all backend API endpoints used by the frontend,
 * enabling integration tests to run without a live backend.
 *
 * Uses MSW v2 syntax (http.get / http.post with delay helper).
 */

import { http, HttpResponse } from 'msw';

// ── Shared fixtures ───────────────────────────────────────────────────────────

const MOCK_HEALTH = {
  status: 'healthy',
  version: '0.3.0',
  uptime: 3672,
  services: {
    ocr_engine: 'ok',
    database: 'ok',
    cache: 'ok',
  },
};

const MOCK_UPLOAD_RESPONSE = {
  document_id: 'doc-abc123',
  filename: 'prescription.jpg',
  status: 'processing',
  message: 'Document uploaded successfully',
  region_count: 3,
};

const MOCK_PENDING_REGIONS = {
  document_id: 'doc-abc123',
  total: 2,
  regions: [
    {
      id: 'region-001',
      document_id: 'doc-abc123',
      bbox: [0.1, 0.2, 0.8, 0.4] as [number, number, number, number],
      text: 'Amoxicillin 500mg',
      confidence: 0.92,
      reviewed: false,
      corrected_text: null,
      created_at: '2024-06-15T10:30:00Z',
    },
    {
      id: 'region-002',
      document_id: 'doc-abc123',
      bbox: [0.1, 0.5, 0.7, 0.7] as [number, number, number, number],
      text: 'take twice daily',
      confidence: 0.65,
      reviewed: false,
      corrected_text: null,
      created_at: '2024-06-15T10:30:01Z',
    },
  ],
};

const MOCK_CORRECTION_RESPONSE = {
  region_id: 'region-001',
  original_text: 'Amoxicillin 500mg',
  corrected_text: 'Amoxicillin 500 mg',
  accepted: true,
};

const MOCK_SUGGESTIONS_RESPONSE = {
  query: 'Amoxicillin 500mg',
  suggestions: [
    { text: 'Amoxicillin 500 mg', score: 0.95, source: 'dictionary' as const },
    { text: 'Amoxicillin 250 mg', score: 0.82, source: 'umls' as const },
    { text: 'Amoxicillin 1000 mg', score: 0.71, source: 'history' as const },
  ],
};

// ── Handler base URL ──────────────────────────────────────────────────────────

/**
 * The API client (src/api/client.ts) defaults to http://localhost:8000.
 * MSW v2 handlers must match against the *full* request URL, so we prefix
 * every handler path with this origin.
 */
const API_ORIGIN = 'http://localhost:8000';

// ── Handlers ──────────────────────────────────────────────────────────────────

export const handlers = [
  // Health check
  http.get(`${API_ORIGIN}/health`, () => {
    return HttpResponse.json(MOCK_HEALTH);
  }),

  // Upload a document
  http.post(`${API_ORIGIN}/api/upload`, async ({ request }) => {
    // Optionally validate multipart/form-data
    return HttpResponse.json(MOCK_UPLOAD_RESPONSE, { status: 200 });
  }),

  // Get pending OCR regions
  http.get(`${API_ORIGIN}/api/pending`, ({ request }) => {
    const url = new URL(request.url);
    const documentId = url.searchParams.get('document_id');
    return HttpResponse.json({
      ...MOCK_PENDING_REGIONS,
      document_id: documentId ?? MOCK_PENDING_REGIONS.document_id,
    });
  }),

  // Submit a correction
  http.post(`${API_ORIGIN}/api/correct`, async ({ request }) => {
    return HttpResponse.json(MOCK_CORRECTION_RESPONSE, { status: 200 });
  }),

  // Get suggestions
  http.get(`${API_ORIGIN}/api/suggestions`, ({ request }) => {
    const url = new URL(request.url);
    const query = url.searchParams.get('q');
    return HttpResponse.json({
      ...MOCK_SUGGESTIONS_RESPONSE,
      query: query ?? MOCK_SUGGESTIONS_RESPONSE.query,
    });
  }),
];

// Export fixtures for reuse in direct unit tests
export {
  MOCK_HEALTH,
  MOCK_UPLOAD_RESPONSE,
  MOCK_PENDING_REGIONS,
  MOCK_CORRECTION_RESPONSE,
  MOCK_SUGGESTIONS_RESPONSE,
};

import axios, { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios';

// ────────────────────────────────────────────────────────────────────────────
// Types — mirror the FastAPI Pydantic response models
// ────────────────────────────────────────────────────────────────────────────

/** A single OCR region extracted from a document. */
export interface OCRRegion {
  id: string;
  document_id: string;
  /** Bounding box as [x1, y1, x2, y2] normalised to [0, 1]. */
  bbox: [number, number, number, number];
  /** Raw text recognised by the OCR engine. */
  text: string;
  /** Confidence score in the range [0, 1]. */
  confidence: number;
  /** Whether the region has been reviewed / corrected. */
  reviewed: boolean;
  /** Corrected text (null if not yet reviewed). */
  corrected_text: string | null;
  /** ISO-8601 timestamp when the region was created. */
  created_at: string;
}

/** Response from POST /api/upload. */
export interface UploadResponse {
  document_id: string;
  filename: string;
  status: 'processing' | 'completed' | 'failed';
  message: string;
  region_count?: number;
}

/** Response from GET /api/pending. */
export interface PendingRegionsResponse {
  document_id: string;
  regions: OCRRegion[];
  total: number;
}

/** Response from POST /api/correct. */
export interface CorrectionResponse {
  region_id: string;
  original_text: string;
  corrected_text: string;
  accepted: boolean;
}

/** Response from GET /health. */
export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy';
  version: string;
  uptime: number;
  services: Record<string, string>;
}

/** Response from GET /api/suggestions/. */
export interface Suggestion {
  text: string;
  score: number;
  source: 'dictionary' | 'umls' | 'history';
}

export interface SuggestionsResponse {
  query: string;
  suggestions: Suggestion[];
}

// ────────────────────────────────────────────────────────────────────────────
// Axios instance
// ────────────────────────────────────────────────────────────────────────────

/** Base URL read from the VITE_API_URL env var, falling back to localhost. */
const BASE_URL: string =
  import.meta.env.VITE_API_URL || 'http://localhost:8000';

/** Optional API key sent as X-API-Key header. */
const API_KEY: string = import.meta.env.VITE_API_KEY || '';

/**
 * Pre-configured Axios instance used by every API helper below.
 *
 * - Sets the JSON Content-Type header.
 * - Attaches the `X-API-Key` header when an API key is configured.
 */
const apiClient: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
    ...(API_KEY ? { 'X-API-Key': API_KEY } : {}),
  },
  timeout: 30_000,
});

// ── Request interceptor ────────────────────────────────────────────────────

apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // Attach API key on every request (useful when key changes at runtime).
    if (API_KEY) {
      config.headers.set('X-API-Key', API_KEY);
    }
    return config;
  },
  (error: AxiosError) => Promise.reject(error),
);

// ── Response interceptor ──────────────────────────────────────────────────

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    // Normalise error messages for the UI layer.
    if (error.response) {
      // Server responded with a status code outside 2xx.
      const status = error.response.status;
      const data = error.response.data as Record<string, unknown>;
      const detail = (data?.detail as string) || 'Unknown server error';
      console.error(`[API] ${status} – ${detail}`);
    } else if (error.request) {
      // Request was made but no response received (network error / CORS).
      console.error('[API] No response received – possible network/CORS issue');
    } else {
      // Something went wrong setting up the request.
      console.error(`[API] Request error: ${error.message}`);
    }
    return Promise.reject(error);
  },
);

// ────────────────────────────────────────────────────────────────────────────
// Typed API functions
// ────────────────────────────────────────────────────────────────────────────

/**
 * Upload a document (image or PDF) for OCR processing.
 *
 * @param file - The File object selected by the user.
 * @returns Upload metadata including the new document ID.
 */
export async function uploadDocument(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await apiClient.post<UploadResponse>('/api/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}

/**
 * Fetch pending (unreviewed) OCR regions for a given document.
 *
 * @param documentId - The document to fetch regions for.
 * @returns Array of OCR regions awaiting review.
 */
export async function getPendingRegions(documentId: string): Promise<PendingRegionsResponse> {
  const response = await apiClient.get<PendingRegionsResponse>('/api/pending', {
    params: { document_id: documentId },
  });
  return response.data;
}

/**
 * Submit a corrected transcription for a specific OCR region.
 *
 * @param regionId  - The ID of the region being corrected.
 * @param correctedText - The user-provided correct text.
 * @returns Confirmation of the accepted correction.
 */
export async function submitCorrection(
  regionId: string,
  correctedText: string,
): Promise<CorrectionResponse> {
  const response = await apiClient.post<CorrectionResponse>('/api/correct', {
    region_id: regionId,
    corrected_text: correctedText,
  });
  return response.data;
}

/**
 * Check backend health status.
 *
 * Useful as a "connectivity smoke test" when the dashboard loads.
 *
 * @returns Health payload with service statuses.
 */
export async function getHealth(): Promise<HealthResponse> {
  const response = await apiClient.get<HealthResponse>('/health');
  return response.data;
}

/**
 * Retrieve auto-suggestion candidates for a piece of recognised text.
 *
 * @param text - The raw OCR text to find suggestions for.
 * @returns Ranked list of suggestions from multiple sources.
 */
export async function getSuggestions(text: string): Promise<SuggestionsResponse> {
  const response = await apiClient.get<SuggestionsResponse>('/api/suggestions', {
    params: { q: text },
  });
  return response.data;
}

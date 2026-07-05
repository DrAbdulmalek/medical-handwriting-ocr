import React, { useState, useCallback } from 'react';
import { Toaster } from 'react-hot-toast';
import Header from '@/components/Header';
import Dashboard from '@/components/Dashboard';
import UploadZone from '@/components/UploadZone';
import OCRResults from '@/components/OCRResults';
import {
  uploadDocument,
  getPendingRegions,
  type OCRRegion,
  type UploadResponse,
} from '@/api/client';
import './App.css';

// ────────────────────────────────────────────────────────────────────────────
// Types
// ────────────────────────────────────────────────────────────────────────────

/** Simple view state — expand with react-router when needed. */
type View = 'dashboard' | 'results';

// ────────────────────────────────────────────────────────────────────────────
// App
// ────────────────────────────────────────────────────────────────────────────

/**
 * Root application component.
 *
 * Provides:
 * - A global `<Toaster />` for react-hot-toast notifications.
 * - A simple client-side "routing" placeholder (dashboard ↔ results).
 * - State management for the current document and its OCR regions.
 * - Arabic + English bidirectional text support via `dir="auto"`.
 */
const App: React.FC = () => {
  // ── State ──────────────────────────────────────────────────────────────

  const [currentView, setCurrentView] = useState<View>('dashboard');
  const [currentDocumentId, setCurrentDocumentId] = useState<string | null>(null);
  const [regions, setRegions] = useState<OCRRegion[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isLoadingRegions, setIsLoadingRegions] = useState(false);

  // ── Handlers ─────────────────────────────────────────────────────────────

  /**
   * Handle files dropped / selected in the UploadZone.
   *
   * Uploads the first file, then automatically fetches pending regions.
   * In a future iteration this will support batch uploads.
   */
  const handleFilesAccepted = useCallback(async (files: File[]) => {
    const file = files[0];
    if (!file) return;

    setIsUploading(true);
    try {
      // Step 1 — upload the file.
      const uploadResp: UploadResponse = await uploadDocument(file);
      setCurrentDocumentId(uploadResp.document_id);

      // Step 2 — fetch pending OCR regions.
      setIsLoadingRegions(true);
      const pendingResp = await getPendingRegions(uploadResp.document_id);
      setRegions(pendingResp.regions);

      // Switch to the results view.
      setCurrentView('results');
    } catch (err) {
      console.error('[App] Upload flow failed:', err);
    } finally {
      setIsUploading(false);
      setIsLoadingRegions(false);
    }
  }, []);

  /** Navigate back to the dashboard. */
  const handleBackToDashboard = useCallback(() => {
    setCurrentView('dashboard');
    setCurrentDocumentId(null);
    setRegions([]);
  }, []);

  /** Callback fired after a correction is submitted in OCRResults. */
  const handleCorrectionSubmitted = useCallback(
    (regionId: string, correctedText: string) => {
      setRegions((prev) =>
        prev.map((r) =>
          r.id === regionId
            ? { ...r, corrected_text: correctedText, reviewed: true }
            : r,
        ),
      );
    },
    [],
  );

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="app" dir="auto">
      {/* Global toast notifications. */}
      <Toaster position="top-right" />

      <Header />

      <main className="app__main">
        {currentView === 'dashboard' && (
          <Dashboard />
        )}

        {(currentView === 'results' || currentView === 'dashboard') && (
          <section className="app__section">
            <UploadZone
              onFilesAccepted={handleFilesAccepted}
              isUploading={isUploading}
            />
          </section>
        )}

        {isLoadingRegions && (
          <p className="app__loading" dir="ltr">
            Processing document – extracting OCR regions…
          </p>
        )}

        {currentView === 'results' && !isLoadingRegions && (
          <section className="app__section">
            <button
              className="btn btn--secondary app__back-btn"
              onClick={handleBackToDashboard}
            >
              ← Back to Dashboard
            </button>
            <OCRResults
              regions={regions}
              onCorrectionSubmitted={handleCorrectionSubmitted}
            />
          </section>
        )}
      </main>

      <footer className="app__footer" dir="ltr">
        <p>
          Medical Handwriting OCR &copy; {new Date().getFullYear()} &middot;
          Powered by FastAPI + React
        </p>
      </footer>
    </div>
  );
};

export default App;

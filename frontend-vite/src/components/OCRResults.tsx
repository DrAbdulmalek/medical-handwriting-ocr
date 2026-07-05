import React, { useState } from 'react';
import type { OCRRegion, Suggestion } from '@/api/client';
import { submitCorrection, getSuggestions } from '@/api/client';

// ────────────────────────────────────────────────────────────────────────────
// Props
// ────────────────────────────────────────────────────────────────────────────

interface OCRResultsProps {
  /** The list of OCR regions to display. */
  regions: OCRRegion[];
  /** Callback invoked after a successful correction is submitted. */
  onCorrectionSubmitted?: (regionId: string, correctedText: string) => void;
}

// ────────────────────────────────────────────────────────────────────────────
// Sub-components
// ────────────────────────────────────────────────────────────────────────────

/**
 * A confidence badge coloured from red (low) to green (high).
 */
const ConfidenceBadge: React.FC<{ confidence: number }> = ({ confidence }) => {
  /** Clamp to [0, 1] and convert to a percentage. */
  const pct = Math.round(Math.min(1, Math.max(0, confidence)) * 100);

  /** Choose a colour class based on the confidence level. */
  const colourClass =
    pct >= 80 ? 'badge--high' : pct >= 50 ? 'badge--medium' : 'badge--low';

  return (
    <span className={`confidence-badge ${colourClass}`} title={`Confidence: ${pct}%`}>
      {pct}%
    </span>
  );
};

/**
 * A single region card with inline correction support.
 */
const RegionCard: React.FC<{
  region: OCRRegion;
  onCorrect: (regionId: string, text: string) => void;
}> = ({ region, onCorrect }) => {
  const [editValue, setEditValue] = useState(region.corrected_text || region.text);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [saving, setSaving] = useState(false);

  /** Fetch suggestions from the API when the user requests them. */
  const handleFetchSuggestions = async () => {
    try {
      const resp = await getSuggestions(region.text);
      setSuggestions(resp.suggestions);
      setShowSuggestions(true);
    } catch (err) {
      console.error('[RegionCard] Failed to fetch suggestions:', err);
    }
  };

  /** Submit the corrected text to the backend. */
  const handleSubmit = async () => {
    if (!editValue.trim() || editValue === region.corrected_text) return;
    setSaving(true);
    try {
      await submitCorrection(region.id, editValue.trim());
      onCorrect(region.id, editValue.trim());
    } catch (err) {
      console.error('[RegionCard] Correction submit failed:', err);
    } finally {
      setSaving(false);
    }
  };

  /** Apply a suggestion and close the popover. */
  const applySuggestion = (text: string) => {
    setEditValue(text);
    setShowSuggestions(false);
  };

  return (
    <div className="region-card">
      <div className="region-card__header">
        <span className="region-card__id" title={region.id}>
          Region #{region.id.slice(0, 8)}
        </span>
        <ConfidenceBadge confidence={region.confidence} />
        {region.reviewed && <span className="region-card__reviewed">✓ Reviewed</span>}
      </div>

      <div className="region-card__body" dir="auto">
        {/* Display corrected text if available, otherwise the raw OCR text. */}
        <p className="region-card__text">
          {region.corrected_text ?? region.text}
        </p>
      </div>

      {/* Correction controls. */}
      <div className="region-card__actions">
        <input
          type="text"
          className="region-card__input"
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          placeholder="Type corrected text…"
          dir="auto"
        />
        <button
          className="btn btn--small btn--primary"
          disabled={saving || !editValue.trim()}
          onClick={handleSubmit}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          className="btn btn--small btn--secondary"
          onClick={handleFetchSuggestions}
        >
          Suggest
        </button>
      </div>

      {/* Suggestions popover. */}
      {showSuggestions && suggestions.length > 0 && (
        <ul className="region-card__suggestions">
          {suggestions.map((s, i) => (
            <li key={i}>
              <button
                className="suggestion-item"
                onClick={() => applySuggestion(s.text)}
              >
                <span className="suggestion-item__text" dir="auto">{s.text}</span>
                <span className="suggestion-item__meta">
                  {Math.round(s.score * 100)}% · {s.source}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {showSuggestions && suggestions.length === 0 && (
        <p className="region-card__no-suggestions">No suggestions available.</p>
      )}
    </div>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// Main component
// ────────────────────────────────────────────────────────────────────────────

/**
 * Displays OCR results as a grid of region cards.
 *
 * Each card shows the recognised text, a confidence score, and
 * inline correction controls with optional auto-suggestions.
 */
const OCRResults: React.FC<OCRResultsProps> = ({ regions, onCorrectionSubmitted }) => {
  if (regions.length === 0) {
    return (
      <div className="ocr-results ocr-results--empty">
        <p>No OCR results to display. Upload a document to get started.</p>
      </div>
    );
  }

  return (
    <div className="ocr-results">
      <h2 className="ocr-results__heading">
        OCR Results ({regions.length} region{regions.length !== 1 ? 's' : ''})
      </h2>
      <div className="ocr-results__grid">
        {regions.map((region) => (
          <RegionCard
            key={region.id}
            region={region}
            onCorrect={onCorrectionSubmitted ?? (() => {})}
          />
        ))}
      </div>
    </div>
  );
};

export default OCRResults;

#!/usr/bin/env python3
"""
Postprocessor Integration Module
Hooks the medical-ocr-postprocessor into the existing suggestion engine so
that corrections from the postprocessor surface as high-confidence
suggestions alongside the engine's own dictionary / edit-distance /
phonetic / historical suggestions.

Design goals
------------
* **Zero disruption** — if the postprocessor is not installed, the
  suggestion engine behaves exactly as before.
* **Merge strategy** — postprocessor corrections for known medical
  terms receive a higher priority than generic edit-distance matches,
  but do not completely override user-accepted historical corrections
  with very high frequency counts.
"""

import logging
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.suggestion_engine import SuggestionEngine, Suggestion
    from app.postprocessor_bridge import PostprocessorBridge

logger = logging.getLogger(__name__)

# ── Priority weights ─────────────────────────────────────────────
# These determine how postprocessor results interact with existing
# suggestion sources.  Higher values push the postprocessor
# suggestions above dictionary / edit-distance results but below
# high-frequency historical corrections.

POSTPROCESSOR_BASE_SCORE = 0.92
POSTPROCESSOR_MEDICAL_TERM_BONUS = 0.06  # extra for validated medical terms
POSTPROCESSOR_SOURCE_TAG = "postprocessor"


def integrate_with_suggestions(
    suggestion_engine: "SuggestionEngine",
    postprocessor_bridge: "PostprocessorBridge",
) -> None:
    """
    Wire the postprocessor bridge into *suggestion_engine* so that every
    call to ``get_suggestions()`` also consults the postprocessor.

    This mutates ``suggestion_engine`` by attaching a reference to the
    bridge instance.  The engine's ``get_suggestions()`` method checks for
    this reference and includes postprocessor corrections when present.

    Parameters
    ----------
    suggestion_engine : SuggestionEngine
        The running suggestion engine instance.
    postprocessor_bridge : PostprocessorBridge
        A ready-to-use :class:`PostprocessorBridge` instance.
    """
    suggestion_engine._postprocessor_bridge = postprocessor_bridge  # type: ignore[attr-defined]
    logger.info(
        "Postprocessor bridge attached to suggestion engine "
        f"(available={postprocessor_bridge.available})"
    )


def get_postprocessor_suggestions(
    text: str,
    postprocessor_bridge: "PostprocessorBridge",
    is_medical: bool = False,
) -> List["Suggestion"]:
    """
    Query the postprocessor for corrections on *text* and return them
    as :class:`Suggestion` objects suitable for merging.

    Parameters
    ----------
    text : str
        The raw OCR word or phrase to correct.
    postprocessor_bridge : PostprocessorBridge
        An initialised bridge instance.
    is_medical : bool
        When ``True``, validated medical-term corrections get a score
        boost.

    Returns
    -------
    List[Suggestion]
        Zero or more suggestions sourced from the postprocessor.
    """
    from app.suggestion_engine import Suggestion

    suggestions: List[Suggestion] = []

    if not text or not text.strip():
        return suggestions

    try:
        corrected = postprocessor_bridge.correct_text(text)

        if corrected and corrected.strip() and corrected != text:
            score = POSTPROCESSOR_BASE_SCORE

            # Boost the score when the correction is a validated medical term
            if is_medical:
                try:
                    validation = postprocessor_bridge._processor.validate_medical_terms(corrected)
                    if isinstance(validation, dict) and validation.get("is_valid"):
                        score = min(score + POSTPROCESSOR_MEDICAL_TERM_BONUS, 1.0)
                except Exception:
                    pass  # validation API might differ across versions

            confidence = "high" if score >= 0.95 else "medium"
            suggestions.append(
                Suggestion(
                    text=corrected,
                    score=score,
                    source=POSTPROCESSOR_SOURCE_TAG,
                    confidence=confidence,
                    metadata={
                        "original_text": text,
                        "corrected_by": "medical-ocr-postprocessor",
                        "is_medical_term": is_medical,
                    },
                )
            )
    except Exception as exc:
        logger.warning(f"Postprocessor correction failed for '{text}': {exc}")

    return suggestions


def merge_suggestions(
    existing: List["Suggestion"],
    postprocessor_suggestions: List["Suggestion"],
) -> List["Suggestion"]:
    """
    Merge postprocessor suggestions into the existing suggestion list.

    Merge rules
    ------------
    1. **Deduplicate** by lowercased text — keep the entry with the
       higher score.
    2. **Historical override** — if a historical correction has
       ``frequency >= 8``, it always wins over the postprocessor.
    3. **Postprocessor boost** — otherwise, postprocessor corrections
       from ``source == 'postprocessor'`` get a small tie-breaking
       advantage so they surface first when scores are equal.

    Parameters
    ----------
    existing : List[Suggestion]
        Suggestions generated by the standard suggestion engine.
    postprocessor_suggestions : List[Suggestion]
        Suggestions generated by the postprocessor bridge.

    Returns
    -------
    List[Suggestion]
        Merged and de-duplicated list, sorted by score descending.
    """
    merged: dict = {}  # key: lowercased text

    # 1. Insert existing suggestions
    for s in existing:
        key = s.text.lower()
        merged[key] = s

    # 2. Insert / override with postprocessor suggestions
    for ps in postprocessor_suggestions:
        key = ps.text.lower()
        if key in merged:
            existing_entry = merged[key]

            # Rule 2 — high-frequency historical corrections win
            if (
                existing_entry.source == "historical"
                and existing_entry.metadata.get("frequency", 0) >= 8
            ):
                continue

            # Rule 3 — postprocessor wins on ties or lower scores
            if ps.score >= existing_entry.score:
                merged[key] = ps
        else:
            merged[key] = ps

    # 3. Sort descending by score
    result = sorted(merged.values(), key=lambda s: s.score, reverse=True)

    # 4. Final tie-breaking: postprocessor first on equal scores
    result.sort(key=lambda s: (s.score, 1 if s.source == POSTPROCESSOR_SOURCE_TAG else 0), reverse=True)

    return result
